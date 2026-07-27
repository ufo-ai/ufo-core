# Authentication, account connection, credential grants, and workspace authorization  `stage-5`

This stage is the identity gate for parts of the system that need to know who the user is or need permission to use an outside account. It sits behind normal requests and setup flows, checking sessions, guiding account connection, and storing proof of permission without exposing secret access tokens.

Several parts share the job. Bearer tokens are signed “passes” that say which email and workspace a request belongs to. Operator rules protect internal web tools by checking the user’s company email domain and deciding which workspace they may inspect. Grants power the chat-based /connect flow: a user approves an outside account, and the system records that permission for later tool use.

OAuth callback handling finishes the round trip after an outside service redirects the browser back. Pipedream and Composio act as bridges to hosted consent pages that keep the real tokens. The GitHub App connector creates and verifies installation links for workspaces. Slack tools turn Slack setup and channel lookup into guided chat actions. Together, these pieces let users safely connect accounts and let agents use them only where authorized.

## Files in this stage

### Pipedream handoff
Pipedream starts a hosted OAuth account connection and bridges its asynchronous result into UFO's grant flow.

### `extensions/pipedream/ufo_ext_pipedream/provider.py`

`io_transport` · `OAuth connection flow`

OAuth is the web flow where a user grants access to an outside service, such as Gmail or Slack, without giving UFO their password. UFO expects this flow to start with a simple authorize URL and later exchange a returned code for an account. Pipedream works a little differently: before showing its hosted connection page, UFO must first ask Pipedream for a temporary Connect token using an asynchronous API call.

This file solves that mismatch by inserting a small browser bridge route. When UFO asks for an authorization URL, `PipedreamOAuthProvider.authorize_url` does not send the browser straight to Pipedream. It sends the browser to this extension's own `/ext/pipedream/oauth` route. That route then creates the Pipedream Connect token, builds the Pipedream Connect Link for the right app, and redirects the browser there.

When Pipedream sends the browser back, the same route checks whether the connection succeeded. On success, it finds the newest connected Pipedream account for this exact workspace and sealed state, then redirects back to UFO core with that account id as the code. Later, `exchange` verifies that this account really belongs to the expected Pipedream app before returning an `OAuthAccount` to UFO. This matters because overlapping browser callbacks must not accidentally attach the wrong user's or wrong app's account. The user's actual secret token stays in Pipedream; UFO stores only the account id it needs to call through Pipedream later.

#### Function details

##### `PipedreamOAuthProvider.authorize_url`  (lines 50–52)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: This starts the connection flow from UFO's point of view. Instead of sending the user's browser directly to Pipedream, it creates a URL for this extension's bridge route, carrying the provider name, UFO's sealed state value, and the callback URL that UFO core expects later.

**Data flow**: It receives a `state` value and a `redirect_uri` from UFO core. It reads the provider name stored on this provider object, extracts the web origin from the redirect URI, and packs the provider, state, and callback into query-string form. It returns a browser URL pointing at `/ext/pipedream/oauth` on the same origin as the callback.

**Call relations**: UFO core calls this when it needs a user-facing authorization URL. It relies on `_origin` to find the scheme and host, such as `https://example.com`, and on URL encoding so the state and callback survive safely in the browser URL. The returned URL leads into `oauth_route`, which performs the asynchronous Pipedream setup that this synchronous method cannot do.

*Call graph*: calls 1 internal fn (_origin); 1 external calls (urlencode).


##### `PipedreamOAuthProvider.exchange`  (lines 54–63)

```
async def exchange(self, code: str, _redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: This completes UFO's side of the connection after the bridge has returned an account id. It double-checks that the account belongs to the expected Pipedream app before telling UFO core that the account can be bound to the grant.

**Data flow**: It receives a `code`, which in this bridge is actually a Pipedream account id, plus the workspace id and sealed state. It rebuilds the Pipedream external user id from the workspace and state, asks the Pipedream client for that exact connected account, and compares the account's app with this provider's expected app. If the app is wrong, it raises an error; if it is right, it returns an `OAuthAccount` containing the account id.

**Call relations**: UFO core calls this after the browser has been redirected back from `oauth_route` with a code. It hands off to the Pipedream client to retrieve account details and uses `connection_user_id` so the lookup is tied to the same state-scoped user used earlier in the flow. Its app check is the final guard that prevents one connector flow from binding an account created for a different connector.

*Call graph*: 4 external calls (__init__, PipedreamError, connection_user_id, pipedream_client).


##### `oauth_route`  (lines 66–109)

```
async def oauth_route(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: This is the browser-facing bridge between UFO and Pipedream. It handles both the first visit, where it creates a Pipedream Connect Link, and the return visit, where it passes the connected account id back to UFO core.

**Data flow**: It reads query parameters from the incoming request: the sealed `state`, the UFO `callback`, the connector `provider`, and optionally an `outcome` from Pipedream. If state or callback is missing, it returns a bad-request response; if the provider is unknown, it returns a not-found response. If the outcome says the account connected, it looks up the newest matching Pipedream account for this workspace/state/app and redirects to UFO's callback with the state and account id. If the outcome is a failure, it returns a clear error response. If there is no outcome yet, it creates a Pipedream Connect token with success and error redirects back to this same route, builds the hosted Pipedream Connect Link for the selected app, optionally adds a custom OAuth app id from the environment, and redirects the browser to Pipedream.

**Call relations**: The user's browser reaches this route from `PipedreamOAuthProvider.authorize_url` at the start of the flow. The route calls `_origin` to build safe same-origin return URLs, uses `CONNECTORS` to find the requested Pipedream app, and asks the Pipedream client either to mint a Connect token or to find the connected account after success. On a successful return, it redirects to UFO core, which then calls `PipedreamOAuthProvider.exchange` to verify and bind the account.

*Call graph*: calls 1 internal fn (_origin); 5 external calls (Response, get, connection_user_id, pipedream_client, urlencode).


##### `_origin`  (lines 112–116)

```
def _origin(url: str) -> str
```

**Purpose**: This helper extracts just the web origin from a full URL: the scheme and host, such as `https://chat.example.com`. It also rejects callback URLs that are missing the basic pieces needed for a safe browser redirect.

**Data flow**: It receives a URL string, parses it, and checks that it has an `http` or `https` scheme and a network host. If the URL is not suitable, it raises a `ValueError`. If it is valid, it returns only the scheme and host portion.

**Call relations**: Both `PipedreamOAuthProvider.authorize_url` and `oauth_route` call this when they need to build bridge URLs based on an existing callback URL. It keeps those callers from silently producing broken or unsafe redirect targets when the callback does not include a real web origin.

*Call graph*: called by 2 (authorize_url, oauth_route); 1 external calls (urlparse).


### Session authorization
Shared bearer-token and operator-session rules establish who a request belongs to and which workspace it may access.

### `core/src/ufo/bearer.py`

`domain_logic` · `token minting and request handling`

This file is the project’s small authentication token factory and inspector. A bearer token is like a signed visitor badge: whoever holds it can present it, but the system only trusts it if the signature proves it was made with the shared secret key and it has not expired.

The token contains three pieces of information: the workspace ID, the member email, and an expiry time. The contents are turned into compact JSON, encoded with URL-safe Base64 so they can travel safely in headers or links, and then signed with HMAC-SHA256. HMAC is a way to make a tamper-evident stamp using a secret key. If anyone changes the workspace, email, or expiry, the signature no longer matches.

The file also verifies tokens. It reads the signing secret from the `UFO_TOKEN_SECRET` environment variable, recomputes the expected signature, compares it safely, decodes the payload, checks the shape of the data, and rejects expired tokens. Higher-level helpers then either confirm that the token belongs to one expected workspace, or extract the workspace claim for shared services that serve many workspaces. Without this file, different parts of the system could disagree about what a valid member token looks like, or accidentally trust forged or expired credentials.

#### Function details

##### `mint_token`  (lines 28–45)

```
def mint_token(secret: str, workspace_id: str, email: str, ttl: timedelta, now: datetime | None=None) -> str
```

**Purpose**: Creates a signed bearer token for one workspace and one email address. It is used when the system needs to issue a credential that later services can verify without storing server-side session state.

**Data flow**: It receives a secret key, a workspace ID, an email address, a time-to-live, and optionally a fixed current time. It trims and lowercases the email, calculates the expiry timestamp, serializes the workspace, email, and expiry into compact JSON, Base64-encodes that JSON, signs the encoded body with HMAC-SHA256, and returns a single string in the form `body.signature`. If the secret is missing, it stops with an error instead of minting an unsafe token.

**Call relations**: This is the issuing side of the token format. Later, `verified_claims` performs the matching verification steps, so both creation and checking are tied to the same layout and signing method.

*Call graph*: 4 external calls (urlsafe_b64encode, now, new, dumps).


##### `verified_claims`  (lines 48–71)

```
def verified_claims(token: str, now: int | None=None) -> tuple[str, str] | None
```

**Purpose**: Checks whether a bearer token is genuine and still valid, then returns the workspace and email it proves. If anything is wrong, it returns `None` so callers do not accidentally trust bad data.

**Data flow**: It receives a token string and optionally a current timestamp. It reads the shared secret through `_secret`, splits the token into its encoded body and signature, recomputes the expected signature, and compares the signatures in a timing-safe way. If the signature matches, it decodes the body with `_b64url_decode`, parses the JSON, checks that the workspace and email are strings and the expiry is an integer, then rejects the token if it is expired. On success, it returns the workspace string and email string.

**Call relations**: This is the central checking step used by both `verify_token` and `workspace_claim`. Those functions ask it to prove the token first, then apply their own extra question: either “does it match this exact workspace?” or “which workspace does it claim?”

*Call graph*: calls 2 internal fn (_b64url_decode, _secret); called by 2 (verify_token, workspace_claim); 4 external calls (now, compare_digest, new, loads).


##### `verify_token`  (lines 74–85)

```
def verify_token(token: str, workspace_id: UUID, now: int | None=None) -> str | None
```

**Purpose**: Verifies a token for a specific workspace and returns the authenticated member email. It is useful when a service is pinned to one workspace and must reject tokens from any other workspace.

**Data flow**: It receives a token, the expected workspace UUID, and optionally a current timestamp. It asks `verified_claims` to check the token’s signature, expiry, and payload. If verification fails, it returns `None`; if the token’s workspace does not equal the expected workspace, it also returns `None`; otherwise it returns the email in lowercase.

**Call relations**: This function builds on `verified_claims` for the shared security checks, then adds the tenant boundary check. It is the stricter path for code that already knows which workspace it is serving.

*Call graph*: calls 1 internal fn (verified_claims).


##### `workspace_claim`  (lines 88–99)

```
def workspace_claim(token: str, now: int | None=None) -> UUID | None
```

**Purpose**: Verifies a token and extracts the workspace UUID it names. It is useful for shared services that serve many workspaces and need to decide the workspace from each request’s token.

**Data flow**: It receives a token and optionally a current timestamp. It first asks `verified_claims` to prove that the token is signed and unexpired. If that succeeds, it tries to convert the workspace claim from text into a UUID object. It returns that UUID on success, or `None` if verification fails or the workspace text is not a valid UUID.

**Call relations**: Like `verify_token`, this starts with `verified_claims`. Instead of comparing the workspace to a preselected one, it hands the verified workspace identity back to the caller so the rest of request processing can be scoped correctly.

*Call graph*: calls 1 internal fn (verified_claims); 1 external calls (UUID).


##### `_secret`  (lines 102–106)

```
def _secret() -> str
```

**Purpose**: Reads the token signing secret from the process environment. This keeps the secret key out of callers’ hands during verification.

**Data flow**: It looks up `UFO_TOKEN_SECRET` in environment variables. If the value exists, it returns it; if it is missing or empty, it raises an error explaining that the secret is required to verify member bearer tokens.

**Call relations**: `verified_claims` calls this before checking a token signature. That means verification always uses the process’s configured secret, rather than trusting a secret passed in from an extension or request.

*Call graph*: called by 1 (verified_claims).


##### `_b64url_decode`  (lines 109–110)

```
def _b64url_decode(value: str) -> bytes
```

**Purpose**: Decodes the URL-safe Base64 body part of a token back into bytes. It also restores any missing padding characters, because the token format strips them to keep the token shorter.

**Data flow**: It receives the encoded body text from a token. It calculates how many `=` padding characters are needed, appends them, decodes the result with URL-safe Base64 decoding, and returns the original bytes that should contain the JSON payload.

**Call relations**: `verified_claims` calls this after the signature has matched. This order matters: the system only spends effort interpreting the payload after proving that the payload has not been tampered with.

*Call graph*: called by 1 (verified_claims); 1 external calls (urlsafe_b64decode).


### `core/src/ufo/ext/operator.py`

`domain_logic` · `request handling`

This file is the shared front door for internal operator web pages. Its main job is to find an operator’s bearer token, prove it is valid, and turn that into a workspace scope. A bearer token is a secret string that says “who I am”; because it is sensitive, this code deliberately never reads it from a URL query string, where it could end up in browser history or server logs.

The file supports three safe places for the token. First, it accepts an Authorization header, which is the normal machine-to-machine way to send a bearer token. Second, it accepts a shared operator session cookie, so an operator can log in once and move between related tools. Third, for the one request that starts a browser session, it accepts the token from a POSTed form body.

After finding the token, the code asks the bearer-token verifier to check it. Then it applies the important operator gate: the email address inside the token must belong to the configured operator email domain. Only after that check can the request choose a workspace with the `ws` query parameter. Without `ws`, the token’s own workspace is used. With `ws`, operators may target either a workspace UUID directly or a customer domain, which is converted into a stable UUID. Think of it like a guarded control room: first prove you are staff, then choose which customer screen to view.

#### Function details

##### `operator_bearer`  (lines 24–37)

```
async def operator_bearer(request: Request) -> str
```

**Purpose**: This function finds the bearer token for an operator request while avoiding unsafe places like URL query parameters. It gives priority to the Authorization header, then the operator session cookie, and finally a POSTed form field used only when opening a session.

**Data flow**: It receives an HTTP request. It first reads the Authorization header and returns the token if it is a proper `Bearer ...` value. If not, it checks the shared operator cookie. If there is still no token and the request is a POST, it reads the submitted form and looks for the `token` field. The output is the cleaned token string, or an empty string if no token was found.

**Call relations**: This is the token-finding helper used by `resolve_operator_workspace`. When that higher-level resolver needs to decide whether a request is allowed and what workspace it belongs to, it first calls this function to get the credential from the request.

*Call graph*: called by 1 (resolve_operator_workspace); 1 external calls (form).


##### `resolve_operator_workspace`  (lines 40–64)

```
async def resolve_operator_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: This function decides which workspace an operator request is allowed to access. It verifies the token, checks that the token belongs to the operator email domain, and then returns the workspace UUID for the request or `None` to reject it.

**Data flow**: It receives the HTTP request and the surface authentication object. It asks `operator_bearer` for the token, sends that token to the bearer verifier, and reads back the claimed workspace and email address if the token is valid. It extracts the email domain and compares it with the required operator domain. If the request has no `ws` query value, it returns the workspace UUID from the token. If `ws` is present, it tries to treat it as a UUID; if it is not a UUID, it treats it as a domain name and turns it into a stable UUID using DNS-based UUID generation. If any check fails, it returns `None`.

**Call relations**: This is the central authorization step for operator-only surfaces. It calls `operator_bearer` to find the credential, delegates token checking to `verified_claims`, uses `_email_domain` to enforce the operator-domain gate, and uses UUID helpers to turn the chosen workspace target into the final workspace identifier.

*Call graph*: calls 1 internal fn (operator_bearer); 4 external calls (verified_claims, _email_domain, UUID, uuid5).


##### `bind_operator_session`  (lines 67–79)

```
async def bind_operator_session(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function opens a browser session for an operator by saving a posted token into the shared operator cookie and redirecting back to the page. It lets an operator authenticate once and then browse the related operator tools without repeatedly pasting the token.

**Data flow**: It receives the surface context and the HTTP request. It reads the submitted form and looks for the `token` field. If the token is missing or blank, it returns a JSON error with a bad-request status. If the token is present, it creates a redirect response back to the current URL, stores the token in the operator session cookie, and returns that redirect. The cookie is set with `SameSite=Lax`, which allows it to work after common cross-site arrivals such as a login page post or a link from another site.

**Call relations**: This function is used at the moment an operator session is created. It reads the form body through the request object, creates either a `JSONResponse` for a bad form or a `RedirectResponse` for success, and hands the successful response to `set_session_cookie` so later requests can be authenticated by `operator_bearer` through the cookie.

*Call graph*: 4 external calls (JSONResponse, RedirectResponse, form, set_session_cookie).


### Grant lifecycle
Core grant logic records connected external accounts and the CLI callback endpoint completes OAuth returns.

### `core/src/ufo/grants.py`

`domain_logic` · `request handling and grant lookup`

This file is the project’s “permission slip” system for connected accounts. Instead of asking an operator to put every credential into deployment settings, a member can grant an agent access during a conversation. The outside service’s real token stays with a broker service; this code stores only safe facts such as the provider name, the broker’s connected account ID, the allowed host, who granted it, and whether it is shared.

The main flow has two halves, like handing someone a sealed envelope and checking it when they return. `ConnectFlow.authorize` builds an OAuth authorization link. OAuth is a common web sign-in and consent pattern where a user approves access on another service’s website. The link includes encrypted “state” saying which workspace, agent, provider, member, and conversation the request belongs to. `ConnectFlow.complete` later opens that sealed state, exchanges the returned code with the provider, and records the grant.

`GrantStore` is the database-facing part. It records grants, lists active grants for an agent, revokes them, and changes whether they are shared. `ConnectHandoff` ties the flow to a specific chat turn, making sure only the member who received the connect request can open it and that old requests expire. Without this file, agents could not safely gain user-approved external account access, and the system would either lose this feature or risk mixing up accounts, workspaces, or users.

#### Function details

##### `grant_sentinel`  (lines 34–38)

```
def grant_sentinel(account_id: str) -> str
```

**Purpose**: Creates a special placeholder credential string for a connected account. The system can pass this placeholder around instead of a real secret, and both the agent runtime and the outbound proxy can recognize the same account from it.

**Data flow**: It receives a connected account ID as text. It adds a fixed prefix to that ID. It returns the combined string, which acts like a safe ticket rather than an actual password.

**Call relations**: This is a small shared convention used wherever grants need to be represented as command-line or environment credentials. It does not call other project code; it simply makes sure separate parts of the system can agree on the same placeholder format.


##### `OAuthProvider.provider`  (lines 76–76)

```
def provider(self) -> str
```

**Purpose**: Defines that every OAuth provider descriptor must expose its provider name. That name is the stable label used when recording and later finding grants.

**Data flow**: A concrete provider object supplies this value. Code that works with providers reads the value and uses it as the provider identity. Nothing is changed by reading it.

**Call relations**: This is part of the `OAuthProvider` protocol, which means provider extensions must implement it. `ConnectFlow.complete` relies on the provider descriptor’s identity when saving the completed grant.


##### `OAuthProvider.host`  (lines 79–79)

```
def host(self) -> str
```

**Purpose**: Defines that every OAuth provider descriptor must expose the host, or web domain, that the grant allows. The outbound proxy can use this to decide which destinations are allowed for the connected account.

**Data flow**: A concrete provider object supplies the host string. Grant recording reads that host and stores it with the grant. Reading it produces no side effects.

**Call relations**: This belongs to the provider protocol implemented by connector extensions. `ConnectFlow.complete` uses it when it asks `GrantStore.record` to save the grant.


##### `OAuthProvider.authorize_url`  (lines 81–81)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Defines how a provider builds the web link a user opens to approve account access. Each provider knows its own authorization URL shape, while the core flow supplies the sealed state and callback address.

**Data flow**: It receives encrypted state and the redirect URI, which is the callback address the provider will send the browser back to. A concrete provider turns those into a provider-specific URL. The result is a link shown to the user.

**Call relations**: This is implemented outside this file by provider extensions. `ConnectFlow.authorize` calls it after preparing the encrypted state for the specific workspace, agent, and member.


##### `OAuthProvider.exchange`  (lines 83–85)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Defines how a provider turns the callback code into a connected account record. This is where the outside service or broker confirms the approval and returns the account ID that the system may use later.

**Data flow**: It receives the provider’s returned code, the redirect URI, the workspace ID, and the original state. A concrete provider verifies and exchanges that information with the broker or provider. It returns an `OAuthAccount` containing the stable connected account ID.

**Call relations**: This is implemented by connector code, not by the core file. `ConnectFlow.complete` calls it after validating the sealed state, then stores the returned account ID as a grant.


##### `OAuthProviderResolver.claims`  (lines 96–96)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: Checks whether an open-ended provider resolver accepts a provider name. This lets a broker support many provider slugs without registering each one statically.

**Data flow**: It receives a provider name. A concrete resolver checks its catalog or rules and returns true if it can serve that provider, false otherwise. It does not record a grant by itself.

**Call relations**: This is used by `ConnectFlow.validate_provider` when a connect request is first checked. It lets unknown names fail early unless the resolver says they are valid.


##### `OAuthProviderResolver.descriptor`  (lines 98–98)

```
def descriptor(self, provider: str) -> OAuthProvider
```

**Purpose**: Builds an OAuth provider descriptor for a provider name that belongs to an open provider namespace. It gives the rest of the connect flow a normal provider object to work with.

**Data flow**: It receives a provider slug. It returns an object matching the `OAuthProvider` protocol. That object can then build authorization links and exchange callback codes.

**Call relations**: This is called indirectly through `ConnectFlow._provider` when a provider is not in the fixed provider map but a resolver is installed. It allows the rest of `ConnectFlow` to stay provider-agnostic.


##### `GrantStore.record`  (lines 161–214)

```
async def record(self, *, workspace_id: UUID, agent_id: UUID, provider: str, account_id: str, host: str, grantor_member_id: UUID, conversation_id: UUID, shared: bool) -> None
```

**Purpose**: Saves a completed grant in the workspace database. It also updates an existing matching grant instead of creating a duplicate when the same account is connected again.

**Data flow**: It receives the workspace, agent, provider, account ID, host, grantor, conversation, and sharing choice. It first rejects account IDs containing control characters, because those could corrupt later broker requests. Then it opens a workspace database transaction, inserts the row, or updates the existing row with fresh audit and sharing information. It returns nothing, but the database now contains the current grant.

**Call relations**: This is the durable final step after `ConnectFlow.complete` gets an account ID from the provider exchange. It uses `workspace_tx` to write inside the workspace database transaction and `uuid4` to create a new row ID when inserting.

*Call graph*: 2 external calls (workspace_tx, uuid4).


##### `GrantStore.active_grants`  (lines 216–245)

```
async def active_grants(self, workspace_id: UUID, agent_id: UUID) -> tuple[Grant, ...]
```

**Purpose**: Reads all grants currently attached to one agent in one workspace. Other parts of the system use this to decide which connected accounts and hosts the agent may use during a turn.

**Data flow**: It receives a workspace ID and agent ID. It queries the grant table for matching rows and turns each row into a `Grant` object containing provider, account ID, host, grantor, and sharing status. It returns those grants as an immutable tuple.

**Call relations**: The call graph shows this is used by `core/src/ufo/loop/queue._grant_cli_env`, which prepares grant-related environment values for an agent turn. It reads through `workspace_tx` and builds `Grant` objects from the database rows.

*Call graph*: called by 1 (_grant_cli_env); 3 external calls (__init__, select, workspace_tx).


##### `GrantStore.revoke`  (lines 247–265)

```
async def revoke(self, workspace_id: UUID, agent_id: UUID, provider: str, account_id: str) -> bool
```

**Purpose**: Removes one grant binding from one agent. This withdraws that agent’s access to that connected account without affecting the same account if it was granted to another agent.

**Data flow**: It receives the workspace, agent, provider, and account ID. It deletes the matching grant row from the database. It returns true if a row was deleted and false if no matching grant existed.

**Call relations**: This is the delete path for grant administration. It uses `workspace_tx` for the database transaction and SQLAlchemy’s delete builder to remove the row.

*Call graph*: 2 external calls (delete, workspace_tx).


##### `GrantStore.set_shared`  (lines 267–285)

```
async def set_shared(self, workspace_id: UUID, agent_id: UUID, provider: str, account_id: str, shared: bool) -> bool
```

**Purpose**: Changes whether a specific grant is shared with the agent’s audience. This lets the system expose or hide a connected account binding without reconnecting the account.

**Data flow**: It receives the workspace, agent, provider, account ID, and the new shared flag. It updates the matching database row and refreshes its update time. It returns true if a row changed and false if no matching grant was found.

**Call relations**: This is the share or unshare path for grant administration. It writes through `workspace_tx` and uses SQLAlchemy’s update builder.

*Call graph*: 2 external calls (update, workspace_tx).


##### `ConnectFlow.authorize`  (lines 303–323)

```
def authorize(self, *, workspace_id: UUID, agent_id: UUID, provider: str, grantor_member_id: UUID, conversation_id: UUID, shared: bool) -> str
```

**Purpose**: Starts an OAuth connect flow by producing the provider approval link the member should open. It seals all important context into encrypted state so the callback can later prove what the request was for.

**Data flow**: It receives the workspace, agent, provider, grantor member, conversation, and sharing choice. It finds the provider descriptor, builds a `ConnectState` object, encrypts that state with Fernet encryption, and asks the provider descriptor to turn it into an authorization URL. It returns that URL.

**Call relations**: This is called when a member needs a connect link, including through `ConnectHandoff.authorize`. It relies on `ConnectFlow._provider` to find the right provider descriptor and then hands the sealed state to the provider’s `authorize_url` method.

*Call graph*: calls 1 internal fn (_provider); 1 external calls (__init__).


##### `ConnectFlow.validate_provider`  (lines 325–330)

```
async def validate_provider(self, provider: str) -> None
```

**Purpose**: Checks whether a requested provider is available before making a connect request. This prevents the system from creating a consent flow for a typo or unsupported provider.

**Data flow**: It receives a provider name. It first checks the fixed provider map, then asks the optional resolver whether it claims that provider. If neither accepts it, it raises `UnknownProvider`; otherwise it returns with no value.

**Call relations**: This is an early validation step used before a connect request is treated as valid. It may call the resolver’s `claims` method, which can consult a live provider catalog.

*Call graph*: 1 external calls (__init__).


##### `ConnectFlow.knows_provider`  (lines 332–337)

```
def knows_provider(self, provider: str) -> bool
```

**Purpose**: Quickly answers whether the connect system still appears able to serve a provider. Unlike full validation, this avoids doing external catalog checks.

**Data flow**: It receives a provider name. It checks whether that name is in the installed provider map, or whether any resolver is installed that can serve open-ended names. It returns true or false.

**Call relations**: This is used by `ConnectHandoff.authorize` while it is working with a locked turn row. It keeps that locked section cheap and avoids waiting on outside provider catalog I/O.


##### `ConnectFlow.bridge_workspace`  (lines 339–345)

```
def bridge_workspace(self, *, state: str, provider: str, callback: str) -> UUID
```

**Purpose**: Verifies a browser bridge request and extracts the workspace it is allowed to act for. This is a safety check before letting a connector bridge proceed.

**Data flow**: It receives encrypted state, a provider name, and a callback URL. It opens the sealed state, checks that the provider and callback match what was originally sealed, verifies the provider exists, and returns the workspace ID from the state. If anything does not match, it raises `ConnectStateInvalid` or `UnknownProvider`.

**Call relations**: This is called by `connect_bridge_workspace`, which wraps failures as a simple rejection. It uses `ConnectFlow._open` to decrypt and validate the state and `ConnectFlow._provider` to ensure the provider is still known.

*Call graph*: calls 2 internal fn (_open, _provider); 1 external calls (__init__).


##### `ConnectFlow.complete`  (lines 347–364)

```
async def complete(self, *, state: str, code: str) -> GrantRecorded
```

**Purpose**: Finishes the OAuth connect flow after the provider redirects back with a code. It proves the callback belongs to a valid connect request, exchanges the code for an account ID, and records the grant.

**Data flow**: It receives encrypted state and the provider’s returned code. It decrypts the state, finds the provider descriptor, enters the right workspace context, exchanges the code for an `OAuthAccount`, and records the grant with the store. It returns a `GrantRecorded` object naming the provider, account ID, and agent.

**Call relations**: This is the callback-side partner to `ConnectFlow.authorize`. It calls `ConnectFlow._open`, `ConnectFlow._provider`, the provider descriptor’s `exchange`, and then `GrantStore.record`; it uses `ufo.workspace.ws` so the database work happens in the correct workspace context.

*Call graph*: calls 2 internal fn (_open, _provider); 2 external calls (__init__, ws).


##### `ConnectFlow._provider`  (lines 366–372)

```
def _provider(self, name: str) -> OAuthProvider
```

**Purpose**: Finds the provider descriptor for a provider name. It hides the difference between explicitly installed providers and providers served by an open resolver.

**Data flow**: It receives a provider name. It looks in the fixed provider map; if not found, it asks the resolver to create a descriptor if a resolver exists. It returns the descriptor or raises `UnknownProvider`.

**Call relations**: This is a private helper used by `ConnectFlow.authorize`, `ConnectFlow.bridge_workspace`, and `ConnectFlow.complete`. It centralizes provider lookup so all three phases choose providers the same way.

*Call graph*: called by 3 (authorize, bridge_workspace, complete); 1 external calls (__init__).


##### `ConnectFlow._open`  (lines 374–379)

```
def _open(self, state: str) -> ConnectState
```

**Purpose**: Decrypts and validates the sealed OAuth state. It makes sure the callback is based on state this server created recently, not state someone edited or replayed long after it expired.

**Data flow**: It receives the state string from a URL. It asks Fernet to decrypt it with a time limit, then parses the JSON into a `ConnectState` object. If the state is unreadable, tampered with, or expired, it raises `ConnectStateInvalid`.

**Call relations**: This private helper is used by `ConnectFlow.bridge_workspace` and `ConnectFlow.complete`, the two places where browser-returned state must be trusted only after verification.

*Call graph*: called by 2 (bridge_workspace, complete); 1 external calls (__init__).


##### `ConnectHandoff.authorize`  (lines 388–471)

```
async def authorize(self, workspace_id: UUID, turn_id: UUID, member_id: UUID) -> str
```

**Purpose**: Turns a chat turn’s private connect request into an OAuth authorization URL for the correct member. It also remembers the URL so repeated clicks for the same still-valid request get the same link.

**Data flow**: It receives a workspace ID, turn ID, and member ID. It locks and reads the turn row, checks that the row belongs to that member, still contains a connect request, has not expired, and names a provider the flow can serve. If a fresh authorization URL was already saved, it returns it. Otherwise it asks `ConnectFlow.authorize` to create one, saves it on the turn, and returns it; if the request is stale or mismatched, it raises `ConnectRequestInvalid`.

**Call relations**: This is the bridge between the conversation system and the OAuth flow. It reads `TerminalFrame` data from the turn, uses `ConnectFlow.knows_provider` and `ConnectFlow.authorize`, and writes the memoized URL through `workspace_tx` so two near-simultaneous attempts do not create inconsistent handoffs.

*Call graph*: 7 external calls (__init__, model_validate, now, timedelta, select, update, workspace_tx).


##### `install_connect_flow`  (lines 477–485)

```
def install_connect_flow(flow: ConnectFlow | None) -> None
```

**Purpose**: Installs the process-wide connect flow object. This gives tools and callback surfaces one shared place to find the configured providers, encryption key, grant store, and redirect URL.

**Data flow**: It receives either a `ConnectFlow` or `None`. It stores that value in a module-level variable. It returns nothing, but future calls to `installed_connect_flow` will see the new setting.

**Call relations**: This is normally called during server startup, and tests can call it to install a stub flow. If `None` is installed, later users of the connect system fail loudly instead of pretending grants are available.


##### `installed_connect_flow`  (lines 488–491)

```
def installed_connect_flow() -> ConnectFlow
```

**Purpose**: Returns the currently installed connect flow, or raises a clear error if none is configured. This prevents grant features from running without the encryption key and provider setup they need.

**Data flow**: It reads the module-level installed flow. If a flow exists, it returns it. If not, it raises `ConnectUnavailable`.

**Call relations**: The call graph shows `connect_bridge_workspace` calls this before verifying bridge requests. Other surfaces can use it in the same way to depend on the configured singleton flow.

*Call graph*: called by 1 (connect_bridge_workspace); 1 external calls (__init__).


##### `connect_bridge_workspace`  (lines 494–503)

```
def connect_bridge_workspace(request: Request) -> UUID | None
```

**Purpose**: Checks whether an incoming connector browser bridge request is valid and, if so, identifies its workspace. It gives callers a simple `workspace ID or reject` result.

**Data flow**: It receives a Starlette `Request`, which is a web request object. It reads the `state`, `provider`, and `callback` query parameters, gets the installed connect flow, and asks that flow to verify them. It returns the workspace ID on success, or `None` if the state, provider, or configuration is invalid.

**Call relations**: This is a small web-facing wrapper around `installed_connect_flow` and `ConnectFlow.bridge_workspace`. It catches the expected failure cases so the caller can reject the bridge request without exposing internal exception details.

*Call graph*: calls 1 internal fn (installed_connect_flow).


##### `grant_summaries`  (lines 506–549)

```
async def grant_summaries(workspace_id: UUID, agent_id: UUID | None=None) -> tuple[GrantSummary, ...]
```

**Purpose**: Builds a safe audit list of recorded grants. It is meant for operator or member-facing views that need to show which agent has which connected account, without exposing secrets.

**Data flow**: It receives a workspace ID and optionally an agent ID. It queries the grant table, joined with the agent table so it can include the agent name, and optionally narrows the results to one agent. It returns `GrantSummary` objects containing provider, account ID, host, grantor, conversation, timestamps, and sharing status.

**Call relations**: This is a read-only reporting path. It uses `workspace_tx` and SQLAlchemy select queries, then turns rows into `GrantSummary` objects for command-line or connector object views.

*Call graph*: 3 external calls (__init__, select, workspace_tx).


### `core/src/ufo/surfaces/cli.py`

`io_transport` · `request handling`

This file is the small public doorway for an OAuth connect flow. OAuth is the common “sign in or connect with another service” pattern where a user is sent to a provider, approves access, and the provider sends the browser back with proof. Here, that return address is `/v1/connect/callback`.

The important job is to safely finish an account connection that was started elsewhere in chat. The browser does not carry a normal login token for this step. Instead, it carries a sealed `state` value, like a tamper-proof claim ticket, plus a short-lived `code` from the provider. The callback asks the grants system for the installed connect flow, checks that both pieces are present, and then completes the flow. Completing the flow verifies the sealed state, exchanges the provider’s code for the project’s stored account grant, and records which provider account was connected.

If the connect feature is not available, the user gets a service-unavailable error. If the callback is missing required information or the state is invalid, it rejects the request. If the named provider is not installed, it reports that as not found. On success, it returns plain text telling the user the account is connected and to return to chat.

#### Function details

##### `connect_callback`  (lines 19–39)

```
async def connect_callback(state: str='', code: str='') -> PlainTextResponse
```

**Purpose**: This is the HTTP callback endpoint that finishes an OAuth account connection after the provider redirects the user’s browser back. It verifies the returned information, completes the connection, and gives the user a simple success or error message.

**Data flow**: The function receives two query values from the browser: `state`, the sealed claim ticket created when the connection began, and `code`, the provider’s temporary authorization code. It first gets the installed connect flow, rejects the request if connecting is unavailable, then checks that both values are present. It passes the state and code into the flow’s completion step; if that succeeds, it returns plain text naming the provider and account that were connected. If something is wrong, it turns the problem into an HTTP error response with the right status code.

**Call relations**: FastAPI calls this function when a browser visits the registered callback path after an OAuth provider redirect. The function asks `ufo.grants.installed_connect_flow` for the object that knows how to finish the connection. It uses `fastapi.HTTPException` to stop the request with clear web errors when the flow cannot continue, and `fastapi.responses.PlainTextResponse` to send the final human-readable success message.

*Call graph*: 3 external calls (HTTPException, PlainTextResponse, installed_connect_flow).


### Workspace connector setup
Workspace-level integrations guide owners through GitHub App installation, Composio-hosted consent, and Slack setup or lookup tools.

### `extensions/coding/ufo_ext_coding/connect.py`

`orchestration` · `tool invocation and GitHub callback request handling`

This file protects a sensitive step: linking a whole workspace to a GitHub App installation. A GitHub installation id is just a number, so the system must not trust it just because it appears in a browser redirect. Otherwise, someone could try to attach a workspace to an installation that belongs to another organization.

The flow works like a checked coat ticket. First, `connect_github` gives the workspace owner a GitHub install link. That link carries a sealed state value, meaning a tamper-resistant note made by ufo that says which workspace and credential slot this install attempt belongs to. GitHub sends the browser back after installation.

On the return path, `github_installed` checks two things. It reads the sealed state to know which workspace this callback is for, and it exchanges GitHub's temporary authorization code for a user token. That token represents the GitHub member who just authorized the app. Then `GitHubInstallExchange.reaches` asks GitHub which ufo installations that member can see. Only if the claimed installation id appears in GitHub's own answer does ufo bind it into the workspace credentials.

The important behavior is that the query-string installation id is only treated as a claim, not proof. GitHub's API is the proof.

#### Function details

##### `connect_github`  (lines 43–67)

```
async def connect_github(ctx: ToolContext, args: ConnectGitHubInput) -> ToolResult
```

**Purpose**: This is the tool action that starts the GitHub connection process for a workspace. It is limited to the workspace owner because the result affects the whole workspace, not just one user.

**Data flow**: It receives the current tool context and an empty input object. It checks whether the speaker is the workspace owner, checks that this deployment has a GitHub App configured, asks the credential system to create a sealed one-time state value, and returns a message containing the GitHub installation link with that state attached.

**Call relations**: This function begins the flow. It calls the context to verify ownership and to create the sealed credential authorization state, and it calls the manifest helper to confirm a GitHub App id exists. It packages the final link into `TextContent` and `ToolResult` so the conversation can show it to the owner.

*Call graph*: calls 2 internal fn (begin_credential_authorization, speaker_is_owner); 3 external calls (__init__, __init__, github_app_id).


##### `install_workspace`  (lines 70–75)

```
def install_workspace(request: Request) -> UUID | None
```

**Purpose**: This reads the workspace identity from the sealed state GitHub sends back. It exists so the callback can be connected to the right workspace without trusting ordinary browser parameters.

**Data flow**: It receives an HTTP request, looks for the `state` query parameter, and asks the credential system to open and verify that state for the expected GitHub installation slot and purpose. It returns the workspace UUID if the state is valid, or nothing if it is not.

**Call relations**: This is used as the bridge from GitHub's browser redirect back into ufo's workspace world. It delegates the actual seal checking to `authorized_slot_workspace`, which is the shared credential helper that knows how to validate the state.

*Call graph*: 1 external calls (authorized_slot_workspace).


##### `GitHubInstallExchange.reaches`  (lines 94–121)

```
async def reaches(self, code: str, installation_id: str) -> bool
```

**Purpose**: This asks GitHub whether the member who just authorized the app can really access the installation id being claimed. It is the central safety check that prevents binding a workspace to someone else's GitHub installation.

**Data flow**: It receives GitHub's temporary authorization code and the claimed installation id. It sends the code, client id, and client secret to GitHub to get a user access token; if GitHub refuses, it raises `GitHubAuthorizationError`. With that token, it asks GitHub for the installations visible to that user, filters the list to this ufo GitHub App, and returns true only if the claimed installation id is in that list.

**Call relations**: This function is called during the callback flow after GitHub redirects back to ufo. It creates an HTTP client to talk to GitHub's token and installations endpoints. Its yes-or-no answer tells `github_installed` whether to bind the installation or reject the callback.

*Call graph*: 2 external calls (__init__, AsyncClient).


##### `install_exchange`  (lines 124–135)

```
def install_exchange() -> GitHubInstallExchange
```

**Purpose**: This builds the object that knows how to verify a GitHub installation callback for this deployment. It gathers the GitHub App identity and secrets needed to talk to GitHub.

**Data flow**: It reads the configured GitHub App id from the manifest and the client id and client secret from environment variables. If there is no GitHub App configured, it stops with an error. Otherwise, it returns a `GitHubInstallExchange` ready to perform the authorization-code exchange and installation lookup.

**Call relations**: This is called by `github_installed` when a GitHub callback arrives. It keeps deployment configuration lookup separate from the verification logic, and it constructs `GitHubInstallExchange`, which performs the actual GitHub API checks.

*Call graph*: called by 1 (github_installed); 2 external calls (__init__, github_app_id).


##### `github_installed`  (lines 138–167)

```
async def github_installed(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: This is the HTTP callback that GitHub reaches after a user installs and authorizes the ufo GitHub App. It decides whether the installation should be saved into the workspace credentials.

**Data flow**: It receives the extension context and the HTTP request from GitHub. It reads the authorization code and installation id from the query string, rejects the request if either is missing, then asks `install_exchange().reaches(...)` whether the authorizing GitHub member can access that installation. If GitHub rejects the code or the installation is not reachable, it returns an error page. If the check passes, it stores the installation id in the workspace credential slot and returns a success page.

**Call relations**: This function is the return leg of the flow started by `connect_github`. It calls `install_exchange` to get a verifier, relies on `GitHubInstallExchange.reaches` through that verifier to prove the GitHub relationship, and uses `_page` to turn every outcome into a simple browser page.

*Call graph*: calls 2 internal fn (_page, install_exchange).


##### `_page`  (lines 170–178)

```
def _page(message: str, status: int) -> Response
```

**Purpose**: This creates a small HTML response page for the browser. It is used to show success or clear error messages after GitHub redirects the user back.

**Data flow**: It receives a plain message and an HTTP status code. It wraps the message in a minimal HTML document and returns a `Response` with that status code and an HTML media type.

**Call relations**: This helper is called by `github_installed` for every callback outcome: missing information, GitHub authorization failure, forbidden installation, or successful connection. It keeps the callback code focused on decisions while this function formats the browser response.

*Call graph*: called by 1 (github_installed); 1 external calls (Response).


### `extensions/composio/ufo_ext_composio/provider.py`

`io_transport` · `request handling during connector OAuth setup`

ufo expects an OAuth provider to give it a ready-to-open authorization URL and later exchange a returned code for an account. Composio works a little differently: creating the consent link requires an asynchronous API call. This file solves that mismatch by inserting a small web route in the middle.

The flow is like a receptionist forwarding a visitor between two offices. First, `ComposioOAuthProvider.authorize_url` does not contact Composio directly. Instead, it builds a URL to this extension’s own `/ext/composio/oauth` route, carrying the provider name, the sealed connection state, and ufo’s real callback address. When the browser reaches that route for the first time, `oauth_route` asks Composio for a hosted consent link and redirects the browser there.

After the user finishes or cancels consent, Composio sends the browser back to the same route. If Composio provides a `connected_account_id`, the route forwards the browser to ufo’s normal callback and passes that id as the OAuth `code`. Later, `exchange` asks Composio to confirm that this account belongs to the expected workspace user and provider before returning an `OAuthAccount`. The real provider token is never stored here; Composio keeps it and later runs tools server-side.

#### Function details

##### `ComposioOAuthProvider.authorize_url`  (lines 43–45)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: This builds the first URL that the user’s browser should open when starting a Composio-backed connection. Instead of pointing straight to Composio, it points to this extension’s bridge route so the asynchronous Composio link can be created at request time.

**Data flow**: It receives ufo’s protected `state` value and the final callback URL. It extracts the callback’s web origin, adds the provider name, state, and callback as query parameters, and returns a local bridge URL under `/ext/composio/oauth`. It does not change any stored data or contact Composio.

**Call relations**: This is the first step in the connection story. It uses `_origin` to make sure the callback has a valid scheme and host, then hands the browser to `oauth_route`, which does the slower work of asking Composio for the real consent link.

*Call graph*: calls 1 internal fn (_origin); 1 external calls (urlencode).


##### `ComposioOAuthProvider.exchange`  (lines 47–53)

```
async def exchange(self, code: str, _redirect_uri: str, workspace_id: UUID, _state: str) -> OAuthAccount
```

**Purpose**: This confirms and binds the Composio account after the browser callback has returned. It makes sure the returned account id belongs to the expected workspace user and matches the provider being connected.

**Data flow**: It receives the returned `code`, which in this flow is actually Composio’s connected account id, plus the workspace id. It builds the Composio user id for that workspace, asks the Composio client to look up and verify the connected account, and returns an `OAuthAccount` for ufo to bind. It does not receive or store the provider’s secret token.

**Call relations**: This runs after `oauth_route` has redirected back to ufo’s normal callback with the connected account id. It hands the verification work to the Composio client, so a forged or foreign account id should not become a valid grant.

*Call graph*: 1 external calls (composio_client).


##### `oauth_route`  (lines 56–93)

```
async def oauth_route(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: This is the HTTP bridge that handles both trips through the browser: the trip from ufo toward Composio, and the return trip from Composio back to ufo. It keeps ufo’s state and callback intact while translating Composio’s result into ufo’s expected OAuth callback shape.

**Data flow**: It reads query parameters from the incoming request. If required `state` or `callback` values are missing, it returns an error response. If Composio has returned a connected account id, it redirects the browser to the callback with that id as `code`. If Composio returned a failure status without an account id, it returns a clear failure message. Otherwise, it treats the request as the start of consent, asks Composio for a connect link using the provider and workspace user id, and redirects the browser to that link.

**Call relations**: The browser reaches this route because `ComposioOAuthProvider.authorize_url` pointed it here. On the start leg, it calls the Composio client to create the hosted consent URL. On the return leg, it sends the browser onward to ufo’s core callback, where `ComposioOAuthProvider.exchange` can verify and bind the account.

*Call graph*: calls 1 internal fn (_origin); 3 external calls (Response, composio_client, urlencode).


##### `_origin`  (lines 96–100)

```
def _origin(url: str) -> str
```

**Purpose**: This small helper extracts the base web origin from a full URL, such as turning `https://example.com/path` into `https://example.com`. It also rejects callback URLs that are not usable web URLs.

**Data flow**: It takes a URL string, parses it into pieces, checks that it has an `http` or `https` scheme and a host name, and returns only the scheme plus host. If the URL is missing those required parts, it raises an error instead of building an unsafe or broken redirect.

**Call relations**: Both `ComposioOAuthProvider.authorize_url` and `oauth_route` rely on this helper when constructing bridge URLs. It is the safety check that keeps redirects anchored to the same web origin as the callback instead of blindly concatenating unclear URL text.

*Call graph*: called by 2 (authorize_url, oauth_route); 1 external calls (urlparse).


### `extensions/slack/ufo_ext_slack/tools.py`

`orchestration` · `Slack setup and later Slack tool use`

This file is the Slack “front desk” for UFO. It exposes tools the agent can call while talking with a workspace member: one tool walks through connecting Slack, one prints a ready-made Slack app manifest, and one searches Slack conversations after the connection is working.

There are two setup routes. The preferred route is OAuth, which is the familiar “Add to Slack” button. If this UFO deployment has its own Slack app configured, the tool creates a short-lived install link for the workspace owner. The other route is “manifest,” meaning the member creates their own Slack app from a YAML recipe. In that path, sensitive secrets such as the bot token and signing secret are collected through private credential slots, not pasted into chat.

Both routes end in the same place: UFO proves the Slack bot identity, binds that Slack team to the UFO workspace, and reports whether Slack has successfully reached this deployment. It says “pending” once the identity is known, and “connected” only after Slack sends a signature-verified request, which proves the public URL and signing secret work.

The runtime channel search tool uses the saved bot token to page through Slack conversations. It can search by channel name, topic, purpose, or people in a direct message, so the agent can find the right Slack place without already knowing its internal Slack ID.

#### Function details

##### `_events_url`  (lines 127–128)

```
def _events_url(public_base_url: str) -> str
```

**Purpose**: Builds the public web address Slack should call when sending events to this UFO deployment. It keeps the Slack event endpoint consistent wherever the file needs it.

**Data flow**: It receives the deployment’s public base URL, removes any trailing slash, then adds the Slack surface path. The result is a complete URL such as the one used in a Slack app manifest or install status response.

**Call relations**: The Slack connection flow calls this when it needs to tell the user where Slack should reach UFO. The manifest generator also calls it so the pasted Slack app recipe points to the same event endpoint.

*Call graph*: called by 2 (slack_connect_handler, slack_manifest_handler).


##### `_state`  (lines 131–133)

```
def _state(state: str, hint: str, events_url: str | None, **extra: object) -> ToolResult
```

**Purpose**: Packages a Slack setup status into the standard tool response format. This gives callers a predictable JSON message with a state, a human hint, and any extra details.

**Data flow**: It receives a status name, a helpful message, an optional Slack events URL, and extra fields. It turns that information into JSON text and wraps it in a tool result that the agent can read and show to the user.

**Call relations**: The main connection flow and its helper paths use this whenever they need to report progress or a problem, such as “not_configured,” “not_installed,” “pending,” or “connected.” It is the common response shape for Slack setup.

*Call graph*: called by 3 (_derive_manifest_identity, _oauth_link, slack_connect_handler); 3 external calls (__init__, __init__, dumps).


##### `slack_connect_handler`  (lines 136–181)

```
async def slack_connect_handler(ctx: ToolContext, args: SlackConnectInput) -> ToolResult
```

**Purpose**: Runs the main Slack connection check and setup flow. It can be called repeatedly before, during, and after setup, and it reports the current state instead of assuming setup happens all at once.

**Data flow**: It starts with the tool context and the requested install method. It looks for a saved bot token, tries to read the saved Slack identity, and if needed either creates an OAuth install link or derives identity from manifest-provided credentials. It then binds the Slack team to this UFO workspace, checks whether Slack has successfully contacted this deployment, and returns a JSON status response.

**Call relations**: This is the handler behind the `slack_connect` tool. It delegates URL building to `_events_url`, OAuth link creation to `_oauth_link`, manifest identity proving to `_derive_manifest_identity`, status formatting to `_state`, and final reachability checking to `_verified`. It also relies on Slack surface helpers to read identity and form the workspace-specific Slack installation ID.

*Call graph*: calls 5 internal fn (_derive_manifest_identity, _events_url, _oauth_link, _state, _verified); 2 external calls (read_identity, slack_installation_id).


##### `_oauth_link`  (lines 184–214)

```
async def _oauth_link(ctx: ToolContext, events_url: str | None) -> ToolResult
```

**Purpose**: Creates the “Add to Slack” link for the one-click install path. It also explains why that path is unavailable when the deployment has no Slack app configured or when the speaker is not the workspace owner.

**Data flow**: It reads deployment environment settings for the Slack app credentials, checks whether the current speaker is allowed to install, verifies that UFO has a public URL, then starts a protected credential authorization handoff. From that sealed handoff it builds a Slack authorization URL and returns it in a setup state response.

**Call relations**: The main connection handler calls this when there is no existing Slack identity and the requested method is OAuth. It hands off to context authorization code to protect the install flow, then uses Slack URL helpers to create the actual Slack install link.

*Call graph*: calls 3 internal fn (begin_credential_authorization, speaker_is_owner, _state); called by 1 (slack_connect_handler); 3 external calls (slack_authorize_url, slack_client_id, slack_oauth_redirect_uri).


##### `_derive_manifest_identity`  (lines 217–251)

```
async def _derive_manifest_identity(ctx: ToolContext, events_url: str | None) -> SlackIdentity | ToolResult
```

**Purpose**: Completes the bring-your-own-Slack-app setup path. It waits until the needed secrets are present, then proves the bot token by asking Slack who the bot and team are.

**Data flow**: It checks the private credential slots for the bot token and signing secret. If any are missing, it returns a setup message telling the user what still needs to be collected. If both exist, it confirms the speaker is the owner, asks Slack to resolve the bot identity from the token, saves or returns that identity, and turns Slack token errors into plain-language setup guidance.

**Call relations**: The main connection handler calls this when the user chose the manifest setup method and no identity has been saved yet. It uses `_state` to report missing or bad configuration, `_token_diagnosis` to translate Slack authentication failures, and `SlackIdentityResolver` to do the actual identity proof.

*Call graph*: calls 3 internal fn (speaker_is_owner, _state, _token_diagnosis); called by 1 (slack_connect_handler); 1 external calls (__init__).


##### `_verified`  (lines 254–273)

```
async def _verified(ctx: ToolContext) -> bool
```

**Purpose**: Checks whether Slack has really reached this UFO deployment using the current signing secret. This separates “we know the Slack bot identity” from “Slack can successfully call our public endpoint.”

**Data flow**: It looks for a saved verification marker in blob storage, reads it as JSON, then reads the current Slack signing secret from credentials. It fingerprints the current secret and compares it with the fingerprint in the marker. It returns true only if the marker exists, is readable, and matches the current secret.

**Call relations**: The main connection handler calls this after identity and workspace binding are in place. The Slack surface writes the marker after receiving a valid signed request, and this function reads that marker to decide whether setup should be reported as pending or connected.

*Call graph*: called by 1 (slack_connect_handler); 3 external calls (loads, signing_secret_fingerprint, url_verified_blob_key).


##### `slack_manifest_handler`  (lines 276–291)

```
async def slack_manifest_handler(ctx: ToolContext, args: SlackManifestInput) -> ToolResult
```

**Purpose**: Produces a ready-to-paste Slack app manifest for the manual setup path. The manifest is a YAML recipe that tells Slack the bot name, permissions, event subscriptions, and request URLs.

**Data flow**: It receives the desired bot display name, checks that it is short and plain enough for Slack, reads the deployment’s public base URL, builds the Slack event and interactivity URLs, fills them into the manifest template, and returns the manifest text as a tool result.

**Call relations**: This is the handler behind the `slack_app_manifest` tool. It uses `_events_url` so the generated manifest points to the same Slack event endpoint used by the connection flow, and it wraps the finished text in the standard tool response objects.

*Call graph*: calls 1 internal fn (_events_url); 3 external calls (__init__, __init__, match).


##### `slack_channels_handler`  (lines 294–317)

```
async def slack_channels_handler(ctx: ToolContext, args: SlackChannelsInput) -> ToolResult
```

**Purpose**: Searches the connected Slack workspace for conversations the agent may need to use. It helps the agent find a channel, group direct message, or one-to-one direct message by human-friendly clues instead of Slack’s internal IDs.

**Data flow**: It reads the saved Slack bot token, checks that the Slack identity has already been resolved, then runs a Slack conversation search with the query text and bot user ID. It returns JSON containing matching conversations and whether the result was cut short because there were more conversations than one scan covers. The result is marked untrusted because names, topics, and messages come from Slack users.

**Call relations**: This is the handler behind the `slack_channels` tool. It relies on the Slack surface identity reader before searching, then hands the actual Slack API paging and matching work to `SlackConversationSearch`.

*Call graph*: 5 external calls (__init__, __init__, __init__, dumps, read_identity).


##### `_token_diagnosis`  (lines 320–326)

```
def _token_diagnosis(error: str) -> str
```

**Purpose**: Turns Slack authentication error codes into messages a user can act on. This is especially useful when a copied bot token is missing, revoked, inactive, or otherwise rejected.

**Data flow**: It receives a Slack error string. If the error is one of the known token rejection cases, it returns a specific instruction to re-copy the Bot User OAuth Token. Otherwise it returns a more general message naming the Slack `auth.test` failure.

**Call relations**: The manifest identity flow calls this when Slack rejects the token while proving the bot identity. Its output is placed into the setup state response so the user sees a helpful diagnosis rather than a bare error code.

*Call graph*: called by 1 (_derive_manifest_identity).

## 📊 State Registers Touched

- `reg-workspace-tenant-record` — The customer workspace record that all users, conversations, data, tools, and billing are kept under.
- `reg-member-session-auth` — The signed-in person’s identity and session proof used to decide who is making a request.
- `reg-surface-installation-binding` — The stored connection between outside channels like Slack, web chat, or terminal clients and an internal workspace conversation.
- `reg-credential-secret-store` — The encrypted store of workspace secrets and credential kinds used without exposing raw tokens to agents.
- `reg-authorization-grants` — The saved permissions showing which user-approved outside accounts an agent may use.
- `reg-network-egress-policy` — The allow-or-deny rules for outbound network calls, including when approved secrets may be attached.
- `reg-connector-broker-catalog` — The known external service brokers and provider actions that let agents use connected services safely.
- `reg-row-level-security-context` — The database safety context that keeps each workspace’s rows separated even when code uses shared tables.
- `reg-onboarding-verification-invites` — Short-lived hashed email verification proofs and one-time invite codes used to admit new users and create or join workspaces.
- `reg-oauth-handshake-state` — Temporary signed or stored state for account-connection callbacks, hosted consent links, and GitHub App installation flows before a durable grant exists.
- `reg-slack-connect-provisioning-state` — The hosted-control-plane state for creating, retrying, and inspecting customer Slack Connect channels during workspace onboarding.
- `reg-workspace-domain-claim-map` — The hosted onboarding mapping from verified company email domains to existing or newly-created workspaces, reused for domain-based workspace lookup and operator authorization.
- `reg-crypto-signing-encryption-keyring` — Stable secret key material used to sign/verify session, OAuth/state, artifact, and filesystem tokens and to encrypt/decrypt stored credentials.

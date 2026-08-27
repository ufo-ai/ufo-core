# Coding GitHub App Connector Credentials  `stage-13.6`

This stage is behind-the-scenes support for coding work that needs access to GitHub. Its job is to connect a workspace to an approved GitHub App installation, then turn that connection into usable, short-lived credentials. Short-lived means the access token expires soon, which is safer than keeping a long-term password-like secret.

The connect.py file runs the connection process. When a user provides a GitHub App installation ID, it does not simply trust that number. It checks with GitHub that the authorizing user can really see and use that installation. This is like checking someone’s badge with the front desk instead of accepting a handwritten note.

The github_app.py file is used later, when the coding workflow needs to talk to GitHub. It looks at the workspace and, if a GitHub App installation is bound, creates an organization-approved GitHub App token for that installation. If no installation is connected, it falls back to the member’s stored GitHub token. Together, these files make GitHub access both verified and properly scoped.

## Files in this stage

### GitHub App Credential Flow
Connects workspaces to verified GitHub App installations and derives short-lived credentials for coding workflows.

### `extensions/coding/ufo_ext_coding/connect.py`

`orchestration` · `GitHub connection setup and browser callback handling`

This file protects the “connect GitHub” setup path. A workspace admin asks ufo for a GitHub connection link. The file creates a special install URL with sealed state, meaning ufo has signed a small piece of information so it can later tell which workspace and credential slot the browser return belongs to. GitHub then sends the user back with an authorization code and an installation ID.

The important safety point is that the installation ID alone is not trusted. It is like someone writing an address on a form: useful, but not proof they live there. Instead, the return handler exchanges GitHub’s authorization code for a user token, then asks GitHub which ufo App installations that user can see. Only if the returned installation ID appears in GitHub’s own list does ufo bind it to the workspace.

Once verified, the file stores the installation ID in the workspace credential slot. It then shows a friendly browser page saying GitHub is connected. If the deployment has a home page, the page links back there; otherwise it tells the user they can close the tab. Without this file, a workspace could not safely connect GitHub, and a malicious or mistaken return URL could bind the wrong organization’s installation.

#### Function details

##### `connect_github`  (lines 51–75)

```
async def connect_github(ctx: ToolContext, args: ConnectGitHubInput) -> ToolResult
```

**Purpose**: Starts the GitHub connection process for a workspace admin. It gives the admin a GitHub App installation link that includes sealed state, so ufo can recognize the workspace again when GitHub redirects back.

**Data flow**: It receives the current tool context and an empty input object. It checks that the speaker is a workspace admin, checks that this deployment has a GitHub App configured, asks the credential system to create a short-lived sealed authorization value, and returns a text message containing the GitHub install link. If the user is not an admin or the app is not configured, it stops with an error instead of producing a link.

**Call relations**: This is the user-facing start of the flow. It asks the tool context whether the speaker is an admin, reads the GitHub App configuration through `ufo_ext_coding.manifest.github_app_id`, and uses `ToolContext.begin_credential_authorization` to create the sealed state that the later browser return will rely on.

*Call graph*: calls 2 internal fn (begin_credential_authorization, speaker_is_admin); 3 external calls (__init__, __init__, github_app_id).


##### `install_workspace`  (lines 78–83)

```
def install_workspace(request: Request) -> UUID | None
```

**Purpose**: Finds which workspace a GitHub return request belongs to. It does this by opening and checking the sealed `state` value that ufo placed in the install link earlier.

**Data flow**: It receives an HTTP request from the browser return. It reads the `state` query parameter, asks `authorized_slot_workspace` to verify that the state was made for the GitHub installation credential slot and the expected purpose, and returns the workspace ID if the state is valid. If the state is missing, expired, or for the wrong purpose, it returns nothing.

**Call relations**: This function is the bridge between a browser callback and the workspace it should affect. It delegates the actual seal checking to `ufo.sdk.credentials.authorized_slot_workspace`, so the rest of the flow does not have to trust raw query-string data.

*Call graph*: 1 external calls (authorized_slot_workspace).


##### `GitHubInstallExchange.reaches`  (lines 102–129)

```
async def reaches(self, code: str, installation_id: str) -> bool
```

**Purpose**: Asks GitHub whether the user who just authorized can actually access the claimed installation. This is the core safety check that prevents someone from binding an installation ID they do not control.

**Data flow**: It receives GitHub’s temporary authorization code and the installation ID from the callback URL. It sends the code, client ID, and client secret to GitHub to get a user access token. If GitHub does not return a token, it raises `GitHubAuthorizationError`. With the token, it asks GitHub for the list of installations visible to that user, filters the list to this ufo GitHub App, and returns true only if the claimed installation ID is in that list.

**Call relations**: During the callback flow, `github_installed` uses an exchange object to answer the yes-or-no question: does this user reach this installation? The method talks to GitHub through `httpx.AsyncClient`, and reports a declined authorization by raising `GitHubAuthorizationError` so the caller can show the right failure page.

*Call graph*: 2 external calls (__init__, AsyncClient).


##### `install_exchange`  (lines 132–143)

```
def install_exchange() -> GitHubInstallExchange
```

**Purpose**: Builds the object that knows how to verify a GitHub installation against GitHub. It gathers this deployment’s GitHub App identity from configuration and environment variables.

**Data flow**: It reads the configured GitHub App ID and the environment variable names for the OAuth client ID and client secret. If no GitHub App is configured, it raises an error. Otherwise it returns a `GitHubInstallExchange` containing the client ID, client secret, and app ID needed for the verification request.

**Call relations**: `github_installed` calls this when a browser returns from GitHub and a verification is needed. It uses `ufo_ext_coding.manifest.github_app_id` for the app ID and constructs `GitHubInstallExchange`, keeping configuration lookup separate from the callback logic.

*Call graph*: called by 1 (github_installed); 2 external calls (__init__, github_app_id).


##### `github_installed`  (lines 146–181)

```
async def github_installed(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: Finishes the GitHub App installation callback. It checks that GitHub returned the needed values, verifies the claimed installation through GitHub, stores it for the workspace, and shows the user a result page.

**Data flow**: It receives the extension context and the browser request. It reads the `code` and `installation_id` query parameters. If either is missing, it returns an error page. Otherwise it creates an install exchange, asks whether the authorizing user can reach that installation, and handles three outcomes: GitHub rejected the authorization, the user cannot reach the installation, or the installation is valid. On success, it binds the installation ID into the GitHub installation credential slot, then returns a success callback page with either a link back to ufo’s home page or a close-this-tab message.

**Call relations**: This is the return leg of the flow started by `connect_github`. It calls `install_exchange` to get the GitHub verifier, uses `ExtensionContext.home_url` to decide where the browser should go afterward, and builds the visible browser response through `callback_page` and `PageLink`.

*Call graph*: calls 2 internal fn (home_url, install_exchange); 2 external calls (__init__, callback_page).


### `extensions/coding/ufo_ext_coding/github_app.py`

`domain_logic` · `credential lookup during rule derivation and GitHub request preparation`

This file solves a safety problem around GitHub access. A GitHub App can be installed into an organization, and GitHub can issue temporary tokens for that installation. Those tokens are safer than long-lived personal tokens because they expire quickly and only have the permissions the organization granted.

The file reads a sealed installation binding from the credential store. “Sealed” means the installation ID is protected so a user cannot simply type another organization’s installation number and trick the system into minting a token for it. If the seal opens correctly, the code asks GitHub for a fresh installation token. If there is no installation, the system can use the member’s normal stored token instead. But if there is a stored installation value that cannot be opened, the code fails rather than falling back, because using a different identity would be worse than refusing access.

The main class, GitHubAppTokens, creates and caches installation tokens. It signs a short-lived JSON Web Token, or JWT, which is a compact signed message proving this server owns the GitHub App’s private key. It exchanges that JWT with GitHub for an installation token. Tokens are cached until shortly before expiry, and concurrent requests share the same minting task so the system does not ask GitHub for duplicate tokens.

GitHubAPIAuth wraps this for API calls, returning a Bearer authentication header from either the App token or the fallback personal token.

#### Function details

##### `_segment`  (lines 58–59)

```
def _segment(payload: dict[str, object]) -> bytes
```

**Purpose**: This helper turns one part of a JWT into the compact text form GitHub expects. It JSON-encodes the data, base64-url encodes it, and removes padding characters.

**Data flow**: It receives a small dictionary, such as the JWT header or body. It converts that dictionary into tight JSON text, encodes it into URL-safe bytes, trims trailing equals signs, and returns the resulting bytes.

**Call relations**: GitHubAppTokens._jwt uses this when building the signed proof that the server owns the GitHub App. It prepares the header and body pieces before they are signed.

*Call graph*: called by 1 (_jwt); 2 external calls (urlsafe_b64encode, dumps).


##### `GitHubAppTokens.bound`  (lines 83–95)

```
async def bound(self, workspace_id: UUID, store: CredentialStore) -> bool
```

**Purpose**: This checks whether a workspace has a valid GitHub App installation binding. It does not mint a token; it only answers whether the App path should be considered available.

**Data flow**: It receives a workspace ID and a credential store. It tries to read the configured installation slot. If the slot is missing, it returns false. If a value is present, it tries to open the sealed installation value; if that succeeds, it returns true, and if it fails, the error is allowed to stop the flow.

**Call relations**: This is used when the system needs to know whether App-based GitHub access exists for a workspace, such as when deciding what credentials to expose. It deliberately uses the same seal-opening check as GitHubAppTokens.secret so the system does not say “unbound” for a broken or suspicious binding and then fall back to a different identity.

*Call graph*: calls 1 internal fn (get); 1 external calls (open_installation).


##### `GitHubAppTokens.secret`  (lines 97–121)

```
async def secret(self, workspace_id: UUID, store: CredentialStore) -> str | None
```

**Purpose**: This returns the workspace’s GitHub App installation token, or returns nothing when the workspace has no App installation. It is the main doorway for getting a usable GitHub token from the App.

**Data flow**: It receives a workspace ID and credential store. It reads the sealed installation binding. If none exists, it returns None so a fallback token may be used elsewhere. If the binding exists, it opens it, checks whether a still-fresh token is already cached, and returns that token if possible. If not, it starts or joins an in-progress minting task, waits for the token, and returns it.

**Call relations**: GitHubAPIAuth.secret calls into this when API authentication may use the GitHub App. Inside, it hands real minting work to GitHubAppTokens._mint. It also coordinates concurrent callers, so if several parts of the system need a token at once, only one GitHub mint request is made.

*Call graph*: calls 2 internal fn (get, _mint); 4 external calls (create_task, shield, time, open_installation).


##### `GitHubAppTokens._mint`  (lines 123–131)

```
async def _mint(self, key: tuple[UUID, str], installation: str) -> tuple[str, float]
```

**Purpose**: This performs one token minting job and records the result in the cache. It also cleans up the “minting in progress” marker when the job finishes.

**Data flow**: It receives the cache key and the GitHub installation ID. It asks GitHubAppTokens._installation_token for a fresh token and expiry time, stores that pair in the minted-token cache, and returns it. Whether the request succeeds or fails, it removes its own pending-task entry if it is still the active task for that key.

**Call relations**: GitHubAppTokens.secret creates this as an asynchronous task when no usable cached token exists. This function then delegates the actual GitHub HTTP exchange to GitHubAppTokens._installation_token and returns the finished token back to the waiting callers.

*Call graph*: calls 1 internal fn (_installation_token); called by 1 (secret); 1 external calls (current_task).


##### `GitHubAppTokens._installation_token`  (lines 133–188)

```
async def _installation_token(self, installation: str) -> tuple[str, float]
```

**Purpose**: This talks to GitHub to exchange the App’s signed JWT for an installation access token. It also checks the response is usable and, when asking for the full installation grant, warns if important permissions are missing.

**Data flow**: It receives an installation ID. It builds an HTTP POST request to GitHub’s installation-token endpoint, using a freshly created JWT for authorization and including requested permissions when configured. If the network request fails, GitHub refuses it, or the response cannot be understood, it raises a credential minting error. On success, it returns the token string and its expiry time as a timestamp.

**Call relations**: GitHubAppTokens._mint calls this when a fresh token is needed. This function calls GitHubAppTokens._jwt to prove the server owns the App registration, then uses GitHub’s response to feed the token cache used by later GitHubAppTokens.secret calls.

*Call graph*: calls 1 internal fn (_jwt); called by 1 (_mint); 4 external calls (__init__, fromisoformat, AsyncClient, warn).


##### `GitHubAppTokens._jwt`  (lines 190–197)

```
def _jwt(self) -> str
```

**Purpose**: This creates the short-lived signed JWT that GitHub requires before it will issue an installation token. The JWT is like a signed note saying, “this server is the registered GitHub App.”

**Data flow**: It reads the current time, builds a JWT header and body containing the App ID plus issue and expiry times, encodes those pieces, signs them with the App’s RSA private key, and returns the complete JWT as text.

**Call relations**: GitHubAppTokens._installation_token calls this just before contacting GitHub. It uses _segment to prepare the header and body, then signs them so GitHub can verify the request came from the real App owner.

*Call graph*: calls 1 internal fn (_segment); called by 1 (_installation_token); 4 external calls (urlsafe_b64encode, PKCS1v15, SHA256, time).


##### `GitHubAPIAuth.bound`  (lines 207–214)

```
async def bound(self, workspace_id: UUID, store: CredentialStore) -> bool
```

**Purpose**: This checks whether the workspace has any GitHub API authentication available, either through the GitHub App or through a fallback stored token.

**Data flow**: It receives a workspace ID and credential store. If App tokens are configured and the workspace has a valid App binding, it returns true. Otherwise, it tries to read the fallback token slot. If that slot exists, it returns true; if it is missing, it returns false.

**Call relations**: This sits one level above GitHubAppTokens.bound. It first gives the GitHub App path a chance, then checks the personal-token fallback, matching the same priority later used when an actual secret is requested.

*Call graph*: calls 1 internal fn (get).


##### `GitHubAPIAuth.secret`  (lines 216–223)

```
async def secret(self, workspace_id: UUID, store: CredentialStore) -> str | None
```

**Purpose**: This returns the actual HTTP Authorization header value for GitHub API calls. It prefers a GitHub App token, but uses the fallback token if no App installation is bound.

**Data flow**: It receives a workspace ID and credential store. It asks GitHubAppTokens.secret for an App token when App support is configured. If that returns nothing, it reads the fallback slot from the store. If neither exists, it returns None. If it gets a token from either path, it prefixes it with “Bearer ” and returns that header value.

**Call relations**: This is the API-facing wrapper around the lower-level token minting logic. Callers that need to authenticate to GitHub’s API can use this without knowing whether the token came from the App installation or from the fallback credential store.

*Call graph*: calls 1 internal fn (get).


##### `app_tokens`  (lines 226–245)

```
def app_tokens(installation_slot: str, permissions: tuple[tuple[str, str], ...] | None=GIT_INSTALLATION_PERMISSIONS) -> GitHubAppTokens
```

**Purpose**: This builds a GitHubAppTokens object from deployment environment variables. It makes sure the GitHub App ID and private key are present and that the private key is the expected RSA kind.

**Data flow**: It receives the credential-slot name that contains the sealed installation binding, plus an optional permissions request. It reads GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY from the process environment, parses the private key from PEM text, checks its type, and returns a configured GitHubAppTokens instance.

**Call relations**: Startup or configuration code calls this to create the token minter used later by GitHubAPIAuth and other credential flows. After construction, the returned GitHubAppTokens object is responsible for checking bindings, creating JWTs, minting GitHub installation tokens, and caching them.

*Call graph*: 2 external calls (__init__, load_pem_private_key).

# Coding extension and GitHub App repository access  `stage-14.1`

This stage is the bridge between a workspace and GitHub when the system needs to work with code repositories. It is used during setup, when an admin connects the workspace to a GitHub App, and later behind the scenes whenever the system needs temporary access to clone or write to a repository.

The package marker file, __init__.py, is like the label on a toolbox. It tells Python that these coding extension files belong together and can be imported by the rest of the project.

connect.py handles the connection ceremony. It builds the link that sends an admin to GitHub, receives the return request from GitHub, checks that the signed-in GitHub user is allowed to use the selected App installation, and then records that installation for the workspace.

github_app.py uses that saved installation to create a short-lived Git token. This token works like a temporary key, letting the system access repositories without keeping a permanent user password or secret.

## Files in this stage

### Package setup
Defines the coding extension package so its GitHub App access modules can be imported.

### `extensions/coding/ufo_ext_coding/__init__.py`

`other` · `import/package discovery`

This is an empty Python package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as a package, meaning its modules can be imported by name from other parts of the system. Think of it like a label on a folder in a filing cabinet: the label does not contain documents itself, but it lets people reliably refer to the folder. Because this file is empty, it does not run setup code, define helper functions, expose shortcuts, or change how the coding extension works. Its value is structural: without it, depending on the Python version and import style, code that expects `extensions.coding.ufo_ext_coding` to be a normal package might fail to import or behave differently.


### GitHub App repository access
Connects workspaces to GitHub App installations and mints short-lived repository tokens for clone and write operations.

### `extensions/coding/ufo_ext_coding/connect.py`

`domain_logic` · `GitHub connection setup and browser callback`

This file solves a trust problem. A GitHub installation id is just a number, so the system must not accept it just because it appears in a browser redirect. Otherwise, someone could try to attach a workspace to an installation that belongs to another organization. The file avoids that by asking GitHub directly: after the user installs or authorizes the app, it exchanges GitHub’s temporary code for that user’s own GitHub token, then asks GitHub which ufo App installations that user can see. Only if the requested installation appears in GitHub’s answer does the workspace get connected.

The flow has two halves. First, `connect_github` is used inside the conversation. It checks that the speaker is a workspace admin, checks that this deployment has a GitHub App configured, and returns a GitHub install link. That link includes sealed state, which is like a tamper-proof claim saying which workspace and credential slot this return belongs to.

Second, GitHub redirects the browser back to `github_installed`. That route checks for the code and installation id, verifies the user’s reach through `GitHubInstallExchange`, and binds the installation into the workspace credentials. Small helper functions read the workspace from the sealed state, build the GitHub exchange object from environment configuration, and return simple HTML pages for success or failure.

#### Function details

##### `connect_github`  (lines 48–72)

```
async def connect_github(ctx: ToolContext, args: ConnectGitHubInput) -> ToolResult
```

**Purpose**: This starts the GitHub connection process for a workspace. It gives an admin a one-use GitHub App installation link that carries sealed, tamper-resistant state tying the browser return to the correct workspace credential slot.

**Data flow**: It receives the current tool context and a short user-facing description. It reads whether the speaker is an admin and whether a GitHub App is configured. If either check fails, it stops with an error. If the checks pass, it asks the credential system to create sealed authorization state, puts that state into a GitHub install URL, and returns a tool result containing instructions and the link.

**Call relations**: This is the conversation-side starting point. It calls the tool context to confirm admin rights and create the sealed authorization, asks the manifest for the GitHub App id, and wraps the final message in `TextContent` and `ToolResult` so it can be shown back to the user.

*Call graph*: calls 2 internal fn (begin_credential_authorization, speaker_is_admin); 3 external calls (__init__, __init__, github_app_id).


##### `install_workspace`  (lines 75–80)

```
def install_workspace(request: Request) -> UUID | None
```

**Purpose**: This extracts the workspace identity from GitHub’s return request. It only trusts the sealed `state` value that this system created earlier, not ordinary query parameters that a browser could change.

**Data flow**: It receives an HTTP request, reads the `state` query parameter, and passes it to the credential helper along with the expected credential slot and payload name. If the seal is valid, it returns the workspace UUID. If not, it returns nothing.

**Call relations**: This function is the bridge from an incoming browser redirect back to a workspace. It relies on `authorized_slot_workspace` to open and verify the sealed state that `connect_github` created during the first half of the flow.

*Call graph*: 1 external calls (authorized_slot_workspace).


##### `GitHubInstallExchange.reaches`  (lines 99–126)

```
async def reaches(self, code: str, installation_id: str) -> bool
```

**Purpose**: This asks GitHub whether the user who just authorized can actually access the installation id being claimed. It is the main safety check that prevents a workspace from binding to someone else’s GitHub organization.

**Data flow**: It receives GitHub’s temporary authorization code and the installation id from the redirect. It sends the code, client id, and client secret to GitHub to get the user’s access token. If GitHub does not return a token, it raises `GitHubAuthorizationError`. With the token, it asks GitHub for the installations visible to that user, filters them to this app’s installations, and returns `true` only if the requested installation id is in that list.

**Call relations**: This is called by `github_installed` after GitHub redirects back. It uses `httpx.AsyncClient` to talk to GitHub’s token and installation APIs, and it raises `GitHubAuthorizationError` when GitHub rejects the authorization code so the caller can show a clear failure page.

*Call graph*: 2 external calls (__init__, AsyncClient).


##### `install_exchange`  (lines 129–140)

```
def install_exchange() -> GitHubInstallExchange
```

**Purpose**: This builds the object that can perform the GitHub verification step. It gathers this deployment’s GitHub App identity from configuration and environment variables.

**Data flow**: It reads the GitHub App id from the manifest and the GitHub OAuth client id and secret from environment variables. If no app id is configured, it raises an error. Otherwise, it returns a `GitHubInstallExchange` containing those values.

**Call relations**: This is called by `github_installed` right before checking the returned installation. It keeps configuration lookup separate from the callback logic, and it constructs `GitHubInstallExchange`, which then performs the actual GitHub API calls.

*Call graph*: called by 1 (github_installed); 2 external calls (__init__, github_app_id).


##### `github_installed`  (lines 143–172)

```
async def github_installed(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: This is the browser-return endpoint after a user installs or authorizes the GitHub App. It decides whether to save the installation for the workspace and shows the user a success or failure page.

**Data flow**: It receives the extension context and the HTTP request from GitHub. It reads the `code` and `installation_id` query parameters. If either is missing, it returns an error page. Otherwise, it creates a GitHub exchange object and asks whether the authorizing user can reach the claimed installation. If GitHub rejects the code, it returns a GitHub authorization error page. If the installation is not reachable by that user, it returns a forbidden page. If the check succeeds, it binds the installation id into the workspace credential slot and returns a success page.

**Call relations**: This is the second half of the connection flow started by `connect_github`. It calls `install_exchange` to get the verifier, uses that verifier’s `reaches` method to ask GitHub for proof, and uses `_page` for each user-facing HTML response.

*Call graph*: calls 2 internal fn (_page, install_exchange).


##### `_page`  (lines 175–183)

```
def _page(message: str, status: int) -> Response
```

**Purpose**: This creates a small HTML response page with a message and status code. It keeps the callback’s success and error responses simple and consistent.

**Data flow**: It receives a message and an HTTP status code. It places the message inside a minimal HTML document and returns a `Response` with `text/html` as the media type.

**Call relations**: This helper is called by `github_installed` whenever the browser callback needs to answer the user. It hands off to the SDK `Response` type to produce the actual HTTP response.

*Call graph*: called by 1 (github_installed); 1 external calls (Response).


### `extensions/coding/ufo_ext_coding/github_app.py`

`domain_logic` · `credential lookup during rule derivation or Git access setup`

This file solves a security problem around GitHub access. A workspace may have installed this project’s GitHub App, but the workspace should not store the App’s private key or a reusable GitHub token. Instead, it stores a sealed installation reference. A seal is like a tamper-proof envelope: the system can open it only if it was created for the right workspace and slot.

When Git access is needed, the file checks whether the workspace has such a sealed installation. If it does not, the system can fall back to the member’s own stored GitHub token. If it does, this file opens the seal, creates a short-lived GitHub App proof called a JWT, and sends that proof to GitHub to receive an installation access token. That token is valid for about an hour and is scoped to the permissions granted when the organization installed the App.

The file is careful not to trust a plain installation id typed by a user, because an id is just a small number and could point to another organization’s installation. If the seal cannot be opened, it fails instead of falling back to a user token, because using the wrong identity would be less safe than refusing access.

It also caches minted tokens until shortly before expiry, so repeated turns in a conversation do not ask GitHub for a new token every time.

#### Function details

##### `_segment`  (lines 47–48)

```
def _segment(payload: dict[str, object]) -> bytes
```

**Purpose**: This helper prepares one part of a JWT, which is a signed text token used to prove the app’s identity to GitHub. It turns a small JSON object into the URL-safe encoded form that JWTs require.

**Data flow**: It takes a dictionary of values, converts it into compact JSON text, encodes that text as bytes, then converts it to URL-safe base64 and removes the padding characters. The result is a byte string ready to be joined into a JWT.

**Call relations**: It is used by GitHubAppTokens._jwt when building the JWT header and body. It does not contact GitHub or read secrets; it only formats data for the later signing step.

*Call graph*: called by 1 (_jwt); 2 external calls (urlsafe_b64encode, dumps).


##### `GitHubAppTokens.bound`  (lines 71–83)

```
async def bound(self, workspace_id: UUID, store: CredentialStore) -> bool
```

**Purpose**: This answers the yes-or-no question: has this workspace bound a GitHub App installation? It checks for the sealed installation value without minting a token.

**Data flow**: It receives a workspace id and a credential store. It asks the store for the installation slot. If the slot is unset, it returns false. If a value is present, it tries to open the seal for this exact workspace and slot; if that succeeds, it returns true. If the seal is invalid, the error is allowed to surface.

**Call relations**: This is used when the system needs to know whether the GitHub App identity should be considered available, such as when deciding what credentials can be exported or used. It mirrors the stricter behavior of GitHubAppTokens.secret: an unreadable seal means “do not use anything,” not “fall back to another identity.”

*Call graph*: calls 1 internal fn (get); 1 external calls (open_installation).


##### `GitHubAppTokens.secret`  (lines 85–109)

```
async def secret(self, workspace_id: UUID, store: CredentialStore) -> str | None
```

**Purpose**: This returns the actual short-lived GitHub installation token for a workspace, if the workspace has bound an App installation. If there is no installation, it returns None so another stored credential can answer instead.

**Data flow**: It receives a workspace id and credential store. It reads the sealed installation value from the store. If none exists, it returns None. If one exists, it opens the seal, uses the workspace and installation as a cache key, and checks whether a still-fresh token is already cached. If so, it returns that token. If not, it starts or joins an in-progress minting task, waits for it safely, and returns the newly minted token.

**Call relations**: This is the main function callers use when they need a GitHub secret for Git access. It calls GitHubAppTokens._mint when a token is missing or near expiry. It also coordinates concurrent requests so that several callers asking at the same time share one minting operation instead of all contacting GitHub separately.

*Call graph*: calls 2 internal fn (get, _mint); 4 external calls (create_task, shield, time, open_installation).


##### `GitHubAppTokens._mint`  (lines 111–119)

```
async def _mint(self, key: tuple[UUID, str], installation: str) -> tuple[str, float]
```

**Purpose**: This performs one token-minting attempt and records the result in the cache. It also cleans up the “minting in progress” marker when the attempt finishes.

**Data flow**: It receives the cache key and the installation id. It asks GitHubAppTokens._installation_token to fetch a token and expiry time from GitHub. If successful, it stores that pair in the minted-token cache and returns it. Whether it succeeds or fails, it removes its task from the in-progress map if it is still the current task for that key.

**Call relations**: GitHubAppTokens.secret creates this as an asynchronous task when no usable cached token exists. This function hands off the actual GitHub request to GitHubAppTokens._installation_token and then makes the result available to later calls.

*Call graph*: calls 1 internal fn (_installation_token); called by 1 (secret); 1 external calls (current_task).


##### `GitHubAppTokens._installation_token`  (lines 121–154)

```
async def _installation_token(self, installation: str) -> tuple[str, float]
```

**Purpose**: This contacts GitHub and exchanges the App’s signed proof for an installation access token. It is the point where a local App registration becomes a usable Git credential.

**Data flow**: It receives a GitHub installation id. It creates an HTTP client, builds a request to GitHub’s installation access-token endpoint, and includes a freshly signed JWT in the authorization header. If GitHub cannot be reached, returns a non-success status, or sends an unreadable response, it raises a credential-minting error. On success, it reads the token and expiry timestamp from GitHub’s JSON response and returns both.

**Call relations**: GitHubAppTokens._mint calls this when a new token is needed. This function calls GitHubAppTokens._jwt to prove the App’s identity before talking to GitHub. Its failures deliberately stop Git access for that App-bound workspace rather than silently falling back to another credential.

*Call graph*: calls 1 internal fn (_jwt); called by 1 (_mint); 3 external calls (__init__, fromisoformat, AsyncClient).


##### `GitHubAppTokens._jwt`  (lines 156–163)

```
def _jwt(self) -> str
```

**Purpose**: This creates a signed JWT proving that this service owns the GitHub App registration. GitHub requires this proof before it will issue an installation token.

**Data flow**: It reads the current time, builds a JWT header and body containing the App id, issue time, and expiry time, formats those parts with _segment, signs them with the App’s RSA private key, and returns the final JWT string.

**Call relations**: GitHubAppTokens._installation_token calls this right before sending a token request to GitHub. It relies on _segment for JWT formatting and on the private key loaded when GitHubAppTokens was created.

*Call graph*: calls 1 internal fn (_segment); called by 1 (_installation_token); 4 external calls (urlsafe_b64encode, PKCS1v15, SHA256, time).


##### `app_tokens`  (lines 166–177)

```
def app_tokens(installation_slot: str) -> GitHubAppTokens
```

**Purpose**: This builds a GitHubAppTokens object from deployment configuration. It reads the GitHub App id and private key from environment variables so the running service can mint installation tokens.

**Data flow**: It receives the name of the credential slot where installation seals are stored. It reads GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY from the process environment, parses the private key text, checks that it is an RSA private key, and returns a configured GitHubAppTokens instance.

**Call relations**: This is the setup helper used by the surrounding extension code when wiring GitHub App credentials into the system. After it creates GitHubAppTokens, later credential lookups call methods such as bound and secret to decide whether and how to mint tokens.

*Call graph*: 2 external calls (__init__, load_pem_private_key).

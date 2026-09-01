# App-Specific Connect and Credential Setup  `stage-10.2.4`

This stage is the “connect the outside services” part of the system. It usually happens during workspace setup, before the main work can use GitHub, Slack, or iMessage safely. Its job is to prove that the right person is connecting the right account, then store or create the credentials the rest of the system will use.

The GitHub connection file links a workspace to the UFO GitHub App. It does not trust a typed installation number by itself. Instead, it asks GitHub to confirm that the signed-in user really has access to that App installation. The GitHub App helper then turns that approved installation link into a short-lived GitHub access token, which is safer than keeping a permanent key. If no App installation is connected, it can fall back to a member’s saved token.

The iMessage tool connects a member’s phone number by checking the requester, reserving the number, assigning a shared texting line, and giving opt-in steps. The Slack tools guide an administrator through Slack setup and provide Slack search once connected.

## Files in this stage

### GitHub App Installation Credentials
Workspace GitHub App setup binds an approved installation and turns it into short-lived repository credentials.

### `extensions/coding/ufo_ext_coding/connect.py`

`io_transport` · `tool invocation and GitHub callback request handling`

This file is the “connect GitHub” bridge between a ufo workspace, a browser redirect from GitHub, and GitHub’s own API. The main problem it solves is trust. A GitHub App installation is identified by a small number, but that number alone is not proof that the workspace is allowed to use it. Without this check, an administrator could accidentally or deliberately bind a workspace to an installation they do not actually control.

The flow works like a locked return ticket. First, `connect_github` gives a workspace admin a GitHub install link. That link includes sealed state, which is data ufo has signed so it can later recognize the workspace and credential slot. After the admin installs or authorizes the GitHub App, GitHub redirects back to ufo. The callback, `github_installed`, reads the authorization code and claimed installation ID. It then uses `GitHubInstallExchange.reaches` to trade the code for the user’s own GitHub token and asks GitHub which ufo installations that user can see. Only if the claimed installation appears in GitHub’s answer does ufo bind it into the workspace credentials.

The stored value is treated as a sealed credential rather than a public connector grant. That matters because the workspace can later state “GitHub is already connected” even though normal connector listings might not show it.

#### Function details

##### `github_app_installed`  (lines 56–60)

```
async def github_app_installed(ext: ExtensionContext) -> bool
```

**Purpose**: Checks whether this workspace already has a GitHub App installation recorded. It only cares that the sealed credential exists, not what value is inside it, because presence means an admin completed the install flow before.

**Data flow**: It receives an extension context, asks the credential store whether the `github_app_installation` slot has anything stored, and returns true or false. It does not open or inspect the stored seal; it only checks that one is present.

**Call relations**: This is a lightweight status check for the rest of the coding extension. When another part of the extension needs to decide whether to tell the agent that GitHub is already connected, it can call this instead of trying to read or validate the installation itself.


##### `connect_github`  (lines 70–94)

```
async def connect_github(ctx: ToolContext, args: ConnectGitHubInput) -> ToolResult
```

**Purpose**: Creates the GitHub App installation link that a workspace admin should open. It makes sure only an admin can start this workspace-wide connection and that this deployment actually has a GitHub App configured.

**Data flow**: It receives the tool context and an empty input object. First it checks whether the speaker is an admin. Then it reads the configured GitHub App ID. If either check fails, it stops with an error. Otherwise it asks the tool context to begin a credential authorization, which produces sealed state for this workspace and credential slot. It returns a tool result containing a human-readable installation URL with that sealed state attached.

**Call relations**: This is the starting point of the connection flow. It calls `ToolContext.speaker_is_admin` before doing anything workspace-wide, calls `github_app_id` to confirm the deployment is configured, and calls `ToolContext.begin_credential_authorization` to create the sealed state GitHub will later return. It wraps the final message in `TextContent` and `ToolResult` so the user sees the install link in the tool response.

*Call graph*: calls 2 internal fn (begin_credential_authorization, speaker_is_admin); 3 external calls (__init__, __init__, github_app_id).


##### `install_workspace`  (lines 97–102)

```
def install_workspace(request: Request) -> UUID | None
```

**Purpose**: Finds which workspace a GitHub callback belongs to by checking the sealed `state` value returned in the browser redirect. This is needed because the callback arrives as a standalone web request, not as part of the original chat turn.

**Data flow**: It receives an HTTP request, reads the `state` query parameter, and passes it to `authorized_slot_workspace` along with the expected credential slot and payload label. If the seal is valid for this purpose, it returns the workspace UUID. If not, it returns nothing.

**Call relations**: This function is the callback router’s way to turn GitHub’s redirect back into a specific ufo workspace. It delegates the trust check to `authorized_slot_workspace`, which knows how to verify that the state was created by ufo for the GitHub installation slot.

*Call graph*: 1 external calls (authorized_slot_workspace).


##### `GitHubInstallExchange.reaches`  (lines 121–148)

```
async def reaches(self, code: str, installation_id: str) -> bool
```

**Purpose**: Asks GitHub whether the user who just authorized can actually access the installation ID being claimed. This is the key safety check that prevents trusting an installation ID just because it appeared in the callback URL.

**Data flow**: It receives a GitHub authorization code and an installation ID. It opens an HTTP client, posts the code plus this deployment’s GitHub App client ID and secret to GitHub’s access-token endpoint, and reads the returned user token. If GitHub does not return a token, it raises `GitHubAuthorizationError`. With the token, it asks GitHub for the user’s visible installations. It filters that list to installations belonging to this ufo GitHub App and returns true only if the requested installation ID is in that list.

**Call relations**: This method is used during the return leg after GitHub redirects back to ufo. `github_installed` calls it through an object made by `install_exchange`. Inside, it uses `httpx.AsyncClient` to make the two GitHub HTTP requests, and it raises `GitHubAuthorizationError` when GitHub refuses the authorization code so the callback page can explain the failure.

*Call graph*: 2 external calls (__init__, AsyncClient).


##### `install_exchange`  (lines 151–162)

```
def install_exchange() -> GitHubInstallExchange
```

**Purpose**: Builds the helper object that can perform the GitHub authorization-code exchange and installation reachability check. It reads the deployment’s GitHub App identity at the moment it is needed.

**Data flow**: It reads the configured GitHub App ID from the manifest. If no App ID exists, it raises an error because this deployment cannot complete GitHub App installation. Otherwise it reads the GitHub App client ID and secret from environment variables, combines them with the App ID, and returns a `GitHubInstallExchange` object.

**Call relations**: This is called by `github_installed` when the callback needs to verify the installation. It calls `github_app_id` for the App ID and constructs `GitHubInstallExchange`, handing over the credentials that `GitHubInstallExchange.reaches` will use for GitHub’s token and installation API requests.

*Call graph*: called by 1 (github_installed); 2 external calls (__init__, github_app_id).


##### `github_installed`  (lines 165–200)

```
async def github_installed(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: Finishes the GitHub App connection after GitHub redirects the user back to ufo. It verifies the callback, binds the installation into the workspace credentials, and returns a browser page telling the user what happened.

**Data flow**: It receives the extension context and the HTTP request from GitHub. It reads the `code` and `installation_id` query parameters. If either is missing, it returns an error callback page. Otherwise it calls `install_exchange().reaches(...)` to ask GitHub whether this user can reach that installation. If GitHub rejects the code, it returns a failure page. If the installation is not in the user’s reachable list, it returns a forbidden page and does not connect anything. If the check succeeds, it stores the installation ID in the workspace credential slot, asks the context for the deployment’s home URL, and returns a success page with either a return link or instructions to close the tab.

**Call relations**: This is the main browser callback handler for the install flow that began in `connect_github`. It calls `install_exchange` to get the GitHub verifier, uses `callback_page` for every browser response, calls `ExtensionContext.home_url` to decide where the user can go next, and creates a `PageLink` when a return link is available.

*Call graph*: calls 2 internal fn (home_url, install_exchange); 2 external calls (__init__, callback_page).


### `extensions/coding/ufo_ext_coding/github_app.py`

`domain_logic` · `credential lookup and token minting during workspace GitHub access`

This file solves a sensitive identity problem: when a workspace belongs to an organization that installed the project's GitHub App, the system should act as that App installation, not as an individual member. GitHub App tokens are short-lived credentials. They are safer than long-lived personal tokens because they expire quickly and are limited to the permissions the organization approved.

The file first checks whether a workspace has a sealed installation binding. “Sealed” means the stored value is encrypted and tied to the workspace, like a tamper-proof label. The code refuses to trust a plain installation number, because someone could type another organization’s installation id and trick the system into minting tokens for the wrong repositories.

If the binding opens correctly, `GitHubAppTokens` signs a short-lived JSON Web Token, or JWT, which is a signed proof that this service owns the GitHub App. It sends that proof to GitHub and asks for an installation access token. The result is cached until shortly before it expires, so repeated work in the same conversation does not keep asking GitHub for new tokens.

If no installation is bound, `GitHubAPIAuth` can use a fallback personal token from the credential store. But if an installation binding exists and is invalid, the file fails closed instead of silently switching identities.

#### Function details

##### `_segment`  (lines 58–59)

```
def _segment(payload: dict[str, object]) -> bytes
```

**Purpose**: Turns one part of a JWT into the compact text format GitHub expects. A JWT is made of encoded pieces joined with dots, and this helper prepares one of those pieces.

**Data flow**: It receives a small dictionary of values, such as the token header or body. It converts that dictionary to compact JSON, encodes it with URL-safe base64 text, removes padding characters, and returns the resulting bytes.

**Call relations**: When `GitHubAppTokens._jwt` builds the signed proof for GitHub, it asks `_segment` to prepare both the header and the body before signing them.

*Call graph*: called by 1 (_jwt); 2 external calls (urlsafe_b64encode, dumps).


##### `GitHubAppTokens.bound`  (lines 83–95)

```
async def bound(self, workspace_id: UUID, store: CredentialStore) -> bool
```

**Purpose**: Answers whether this workspace has a GitHub App installation bound to it. It does not mint a token; it only checks that the stored binding exists and can be opened safely.

**Data flow**: It receives a workspace id and a credential store. It asks the store for the installation slot; if the slot is empty, it returns `False`. If there is a value, it tries to open the sealed installation binding for that exact workspace and slot. If that succeeds, it returns `True`; if opening fails, the error is allowed to surface.

**Call relations**: This is used by code that needs to know whether the GitHub App identity is available before exposing credentials elsewhere. It relies on the credential store to fetch the saved value and on `open_installation` to prove that the value is a legitimate sealed binding.

*Call graph*: calls 1 internal fn (get); 1 external calls (open_installation).


##### `GitHubAppTokens.secret`  (lines 97–121)

```
async def secret(self, workspace_id: UUID, store: CredentialStore) -> str | None
```

**Purpose**: Returns a usable GitHub installation token for this workspace, or `None` if the workspace has no App installation. It deliberately raises an error for a bad sealed value instead of falling back to another identity.

**Data flow**: It receives a workspace id and credential store. It reads the installation slot. If the slot is missing, it returns `None`. If present, it opens the sealed installation id, checks whether a still-fresh token is already cached, and returns it if possible. If no fresh token exists, it starts or joins an in-progress minting task, waits for it safely, and returns the new token string.

**Call relations**: This is the main entry point for getting an App-backed GitHub secret. When it needs a new token, it creates an asynchronous task for `GitHubAppTokens._mint`; if another request is already minting the same installation token, it waits on that same task instead of duplicating the GitHub request.

*Call graph*: calls 2 internal fn (get, _mint); 4 external calls (create_task, shield, time, open_installation).


##### `GitHubAppTokens._mint`  (lines 123–131)

```
async def _mint(self, key: tuple[UUID, str], installation: str) -> tuple[str, float]
```

**Purpose**: Performs one token minting run and updates the cache. It also cleans up the record of the in-progress mint so future calls know whether minting is still happening.

**Data flow**: It receives the cache key and the installation id. It asks `_installation_token` to get a fresh token and expiry time from GitHub, stores that pair in the cache, and returns it. Whether it succeeds or fails, it removes its own task from the in-progress task table if it is still the current task for that key.

**Call relations**: `GitHubAppTokens.secret` calls this when no fresh cached token exists. `_mint` delegates the actual GitHub API exchange to `GitHubAppTokens._installation_token`, then hands the resulting token and expiry back to the waiting caller.

*Call graph*: calls 1 internal fn (_installation_token); called by 1 (secret); 1 external calls (current_task).


##### `GitHubAppTokens._installation_token`  (lines 133–188)

```
async def _installation_token(self, installation: str) -> tuple[str, float]
```

**Purpose**: Contacts GitHub to exchange the App’s signed proof for an installation access token. This is the point where the system actually talks to GitHub and learns when the token expires.

**Data flow**: It receives a GitHub installation id. It creates a short-lived JWT with `_jwt`, sends an HTTP POST request to GitHub’s installation access-token endpoint, and optionally asks for specific permissions. If GitHub cannot be reached, rejects the request, or returns an unreadable response, it raises `CredentialMintFailed`. On success, it returns the token text and its expiry time. If permissions were not explicitly requested, it also checks GitHub’s returned grant and logs a warning for important missing permissions.

**Call relations**: `GitHubAppTokens._mint` calls this whenever a fresh token is needed. This function calls `_jwt` to prove the service owns the GitHub App, uses `httpx.AsyncClient` for the network request, and uses `warn` only to report permission shortfalls that may matter later.

*Call graph*: calls 1 internal fn (_jwt); called by 1 (_mint); 4 external calls (__init__, fromisoformat, AsyncClient, warn).


##### `GitHubAppTokens._jwt`  (lines 190–197)

```
def _jwt(self) -> str
```

**Purpose**: Builds and signs the short-lived JWT that authenticates this service as the GitHub App. GitHub requires this proof before it will issue an installation token.

**Data flow**: It reads the current time, builds a token header and body containing the App id and expiry time, encodes those pieces, signs them with the App’s RSA private key, and returns the finished dot-separated JWT string.

**Call relations**: `GitHubAppTokens._installation_token` calls this just before making the GitHub API request. `_jwt` uses `_segment` to prepare the unsigned pieces, then adds the cryptographic signature GitHub will verify.

*Call graph*: calls 1 internal fn (_segment); called by 1 (_installation_token); 4 external calls (urlsafe_b64encode, PKCS1v15, SHA256, time).


##### `GitHubAPIAuth.bound`  (lines 207–214)

```
async def bound(self, workspace_id: UUID, store: CredentialStore) -> bool
```

**Purpose**: Answers whether any GitHub API credential is available for a workspace. It checks the App installation first, then checks a fallback stored token.

**Data flow**: It receives a workspace id and credential store. If App tokens are configured and the App installation is bound, it returns `True`. Otherwise it tries to read the fallback slot from the store. If that slot exists, it returns `True`; if it is unset, it returns `False`.

**Call relations**: This wraps `GitHubAppTokens.bound` with a fallback path for personal or stored tokens. It is used when the broader system wants to know whether it can authenticate to the GitHub API at all.

*Call graph*: calls 1 internal fn (get).


##### `GitHubAPIAuth.secret`  (lines 216–223)

```
async def secret(self, workspace_id: UUID, store: CredentialStore) -> str | None
```

**Purpose**: Returns the Authorization header value needed for GitHub API calls. It prefers a GitHub App installation token and uses the fallback stored token only when no App token exists.

**Data flow**: It receives a workspace id and credential store. It first asks `GitHubAppTokens.secret` for an App token if App support is configured. If that returns `None`, it reads the fallback token from the store. If neither exists, it returns `None`. If it finds a token, it prefixes it with `Bearer ` and returns that header-ready string.

**Call relations**: This is the API-facing wrapper around the lower-level token source. It depends on `GitHubAppTokens.secret` for App credentials and on the credential store for the fallback token, then hands callers a value they can place directly in a GitHub API Authorization header.

*Call graph*: calls 1 internal fn (get).


##### `app_tokens`  (lines 226–245)

```
def app_tokens(installation_slot: str, permissions: tuple[tuple[str, str], ...] | None=GIT_INSTALLATION_PERMISSIONS) -> GitHubAppTokens
```

**Purpose**: Builds a `GitHubAppTokens` object from deployment environment variables. It makes startup fail clearly if the GitHub App id or private key is missing or unusable.

**Data flow**: It receives the name of the installation slot and an optional permissions list. It reads `GITHUB_APP_ID` and `GITHUB_APP_PRIVATE_KEY` from the process environment, parses the private key, verifies that it is an RSA key, and returns a configured `GitHubAppTokens` instance.

**Call relations**: This is the setup helper used when wiring GitHub App credentials into the system. It hands the loaded App id, parsed private key, installation slot, and permission request into `GitHubAppTokens` so later credential lookups can mint real installation tokens.

*Call graph*: 2 external calls (__init__, load_pem_private_key).


### iMessage Number Connection
The iMessage tooling verifies the requesting member, reserves a phone number, assigns a texting line, and returns opt-in instructions.

### `extensions/imessage/ufo_ext_imessage/tools.py`

`domain_logic` · `request handling`

This file is the “connect my phone” flow for the iMessage extension. Its job is to make sure a phone number is connected safely: only a signed-in member can claim a number, an admin may need to connect the workspace to the iMessage provider first, and a number already owned by another member is rejected.

The flow works like a careful front desk. First it cleans and validates the phone number so the rest of the system sees one standard form, such as +14155550123. Then, when the tool runs, it checks that an iMessage provider is configured and bound to the workspace. If not, it explains what is missing instead of failing silently.

Next it reserves the requested phone number for the member for a short time, currently 30 minutes. If the number is already linked, it assigns a line and reports that the connection is complete. If this is a new claim, it asks the provider for a shared line, creates a random opt-in code, stores that pending claim, and returns instructions telling the member to text that code from their phone. It also creates a QR code so someone reading on a desktop can scan with their phone instead of typing. Results are returned as structured JSON text so the caller can display the state and instructions consistently.

#### Function details

##### `opt_in_link`  (lines 37–41)

```
def opt_in_link(assigned_phone_number: str, opt_in_code: str) -> str
```

**Purpose**: Builds a clickable phone-message link that opens the Messages app with the opt-in text already filled in. This helps the member send the required first message without manually typing the code.

**Data flow**: It takes the shared line phone number and the opt-in code. It combines them with the fixed opt-in message, safely encodes the message text for use inside a link, and returns an sms: link ready to show to the user.

**Call relations**: This is used when the tool is preparing the pending-connection response. The higher-level result builder asks it for a link, then includes that link alongside the written instructions.

*Call graph*: called by 1 (_opt_in_result); 1 external calls (quote).


##### `opt_in_qr`  (lines 44–52)

```
def opt_in_qr(assigned_phone_number: str, opt_in_code: str) -> bytes
```

**Purpose**: Creates a QR code image for the same opt-in message. This is useful when the user is on a computer and wants to scan the code with their phone camera.

**Data flow**: It receives the shared line phone number and opt-in code. It writes a QR code into an in-memory byte buffer as a PNG image, then returns the raw image bytes so they can be shared as an artifact.

**Call relations**: The main connection flow calls this after it has a pending claim. It then hands the generated image to the tool context so the caller can display or attach the QR code for the member.

*Call graph*: called by 1 (run); 2 external calls (BytesIO, make).


##### `_display_phone`  (lines 58–62)

```
def _display_phone(phone_number: str) -> str
```

**Purpose**: Turns a US phone number in strict international form into a friendlier display form, like “(415) 555-0123”. If the number is not a matching US number, it leaves it unchanged.

**Data flow**: It takes a phone number string. It checks whether it matches the expected US +1 format; if so, it rearranges the digits into a readable format, otherwise it returns the original string.

**Call relations**: The connection flow and the opt-in result builder use this when writing user-facing instructions. It keeps the stored phone format strict while making messages easier for humans to read.

*Call graph*: called by 2 (run, _opt_in_result).


##### `ImessageConnectInput._e164`  (lines 73–85)

```
def _e164(cls, value: str) -> str
```

**Purpose**: Validates and normalizes the phone number supplied to the iMessage connection tool. It accepts common US number formatting, rejects letters and invalid numbers, and returns one clean standard form.

**Data flow**: It receives the user-entered phone number. It trims spaces, removes punctuation such as parentheses or dashes, checks that the result is a valid 10-digit US number, and returns it as a +1 international-format string. If the input is not acceptable, it raises a clear validation error.

**Call relations**: This runs as part of building the tool input model before the main connection flow receives the arguments. By the time the main tool runs, it can rely on the phone number being in one predictable format.


##### `_result`  (lines 88–94)

```
def _result(state: str, instruction: str, **extra: object) -> ToolResult
```

**Purpose**: Builds a standard tool response with a connection state and a human-readable instruction. It keeps all responses shaped the same way so callers can interpret them reliably.

**Data flow**: It receives a state, an instruction sentence, and any extra details. It packs these into a JSON object, wraps that JSON as text content, marks the result as untrusted user-facing content, and returns a ToolResult.

**Call relations**: The main connection flow uses this for all outcomes, including errors, successful connections, and pending steps. The opt-in result helper also uses it after adding opt-in-specific details.

*Call graph*: called by 2 (run, _opt_in_result); 3 external calls (__init__, __init__, dumps).


##### `_opt_in_result`  (lines 97–106)

```
def _opt_in_result(assigned_phone_number: str, opt_in_code: str) -> ToolResult
```

**Purpose**: Builds the response for a phone number that is reserved but still needs the member to send the opt-in text. It tells the member exactly what to text, where to send it, and includes a clickable message link.

**Data flow**: It receives the assigned shared line number and opt-in code. It creates the exact text message the member must send, formats the destination phone number for readability, builds an opt-in link, and returns a pending-state tool result with those details.

**Call relations**: The main connection flow calls this after it has stored or found a pending phone claim and shared the QR code. This helper gathers the final instructions into the common result format.

*Call graph*: calls 3 internal fn (_display_phone, _result, opt_in_link); called by 1 (run).


##### `ImessageConnect.run`  (lines 113–168)

```
async def run(self, ctx: ToolContext, args: ImessageConnectInput) -> ToolResult
```

**Purpose**: Runs the full iMessage phone connection workflow for one member. It checks permissions and provider setup, reserves the phone number, assigns a shared line, and either completes the connection or starts the opt-in step.

**Data flow**: It receives the tool context, which contains workspace and speaker information, and the validated phone number input. It checks that a signed-in member is asking, obtains the iMessage provider, binds the provider to the workspace if an admin is allowed to do so, reserves the requested phone number, and decides what state applies. If the number is already linked, it returns a connected response. If the number needs confirmation, it creates or reuses a pending claim, stores the shared line and random opt-in code, shares a QR image artifact, and returns pending opt-in instructions.

**Call relations**: This is the central story of the file. It calls the smaller helpers to format phone numbers, create standard responses, make QR codes, and build opt-in instructions. It also relies on the surrounding tool context, installation store, provider, and claim storage to coordinate the workspace-level connection safely.

*Call graph*: calls 6 internal fn (share_artifact, speaker_is_admin, _display_phone, _opt_in_result, _result, opt_in_qr); 6 external calls (__init__, now, choice, claim_key, read_claim, uuid4).


### Slack Workspace Setup
The Slack tools guide administrators through connecting Slack, generating setup configuration, and searching workspace conversations.

### `extensions/slack/ufo_ext_slack/tools.py`

`domain_logic` · `Slack setup and Slack tool use`

Slack setup has several moving parts: tokens, signing secrets, Slack app settings, a public web address, and proof that Slack can actually reach this UFO deployment. This file turns that setup into a few guided actions the agent can run in conversation, instead of making a user piece everything together by hand.

The main action is `slack_connect_handler`. It checks whether Slack is already partly or fully connected, then reports a clear state such as not configured, not installed, pending, or connected. There are two setup paths. The OAuth path is the easier “Add to Slack” button flow, used when this UFO deployment already has its own Slack app credentials. The manifest path is the “bring your own Slack app” flow: UFO prints a ready-made Slack app manifest, the user creates the app in Slack, and the secret values are collected privately rather than pasted into chat.

The file also verifies whether Slack has sent a real, signature-checked request to this deployment. That is like confirming not only that someone has a key, but that they successfully opened the right door. Finally, `slack_channels_handler` uses the stored bot token to list and search Slack conversations, so the agent can find a channel or direct message by name or people rather than needing a raw Slack ID.

#### Function details

##### `_events_url`  (lines 145–146)

```
def _events_url(public_base_url: str) -> str
```

**Purpose**: Builds the public web address where Slack should send events for this deployment. This keeps Slack pointed at the same standard `/surface/slack` endpoint every time.

**Data flow**: It receives the deployment’s public base URL, removes any trailing slash, adds `/surface/slack`, and returns the finished event URL as text.

**Call relations**: When Slack connection setup or manifest generation needs to tell Slack where to call back, `slack_connect_handler` and `slack_manifest_handler` call this helper first.

*Call graph*: called by 2 (slack_connect_handler, slack_manifest_handler).


##### `_state`  (lines 149–151)

```
def _state(state: str, hint: str, events_url: str | None, **extra: object) -> ToolResult
```

**Purpose**: Creates a standard tool response describing the current Slack setup state. It gives the caller a machine-readable JSON message with a state, a human hint, the Slack events URL, and any extra details.

**Data flow**: It receives a state name, a helpful hint, an optional events URL, and extra fields. It packs them into a dictionary, turns that into JSON text, wraps it in tool result objects, and returns that result.

**Call relations**: The Slack setup flow uses this whenever it needs to report progress or a problem. `_oauth_link`, `_derive_manifest_identity`, and `slack_connect_handler` all hand their user-facing status messages through this function.

*Call graph*: called by 3 (_derive_manifest_identity, _oauth_link, slack_connect_handler); 3 external calls (__init__, __init__, dumps).


##### `slack_connect_handler`  (lines 154–195)

```
async def slack_connect_handler(ctx: ToolContext, args: SlackConnectInput) -> ToolResult
```

**Purpose**: Runs the main Slack connection check and setup flow. A user or agent can call it repeatedly, and it will say what still needs to happen or confirm that Slack is ready.

**Data flow**: It reads the tool context, the chosen install method, stored Slack credentials, saved Slack identity information, and whether Slack has already reached this deployment. If no identity exists, it either creates an OAuth install link or tries the manifest-token path. Once identity is known, it binds the Slack team to this UFO workspace, checks verification, and returns a JSON status result.

**Call relations**: This is the central Slack setup tool. It calls `_events_url` to build callback addresses, `_oauth_link` for one-click install, `_derive_manifest_identity` for bring-your-own-app setup, `_verified` to confirm Slack has contacted UFO, and `_state` to explain the result.

*Call graph*: calls 5 internal fn (_derive_manifest_identity, _events_url, _oauth_link, _state, _verified); 2 external calls (read_identity, slack_installation_id).


##### `_oauth_link`  (lines 198–228)

```
async def _oauth_link(ctx: ToolContext, events_url: str | None) -> ToolResult
```

**Purpose**: Creates an “Add to Slack” link for the one-click installation path. It is used when the UFO deployment already has Slack app credentials configured.

**Data flow**: It checks environment settings for the Slack app client ID and secret, checks that the speaker is an admin, checks that the deployment has a public URL, then creates a short-lived sealed authorization handoff. From that, it builds a Slack authorization URL and returns it inside a setup-state response.

**Call relations**: `slack_connect_handler` calls this when no Slack identity is saved yet and the user asked for the OAuth setup method. It relies on the tool context to start private credential authorization, then uses Slack URL helpers to produce the link.

*Call graph*: calls 3 internal fn (begin_credential_authorization, speaker_is_admin, _state); called by 1 (slack_connect_handler); 3 external calls (slack_authorize_url, slack_client_id, slack_oauth_redirect_uri).


##### `_derive_manifest_identity`  (lines 231–265)

```
async def _derive_manifest_identity(ctx: ToolContext, events_url: str | None) -> SlackIdentity | ToolResult
```

**Purpose**: Completes the bring-your-own-Slack-app setup path after the needed secrets have been collected privately. It proves the bot token is valid and saves or returns the Slack workspace identity.

**Data flow**: It checks whether both required secret slots are filled: the bot token and signing secret. If any are missing, it returns instructions saying what to collect. If both exist, it reads the bot token, requires an admin speaker, asks Slack to identify the token through `auth.test`, and returns either the Slack identity or a clear not-configured error.

**Call relations**: `slack_connect_handler` calls this for the manifest method. It uses `_state` to explain missing or bad credentials and `_token_diagnosis` to turn Slack token errors into understandable instructions.

*Call graph*: calls 3 internal fn (speaker_is_admin, _state, _token_diagnosis); called by 1 (slack_connect_handler); 1 external calls (__init__).


##### `_verified`  (lines 268–292)

```
async def _verified(ctx: ToolContext) -> bool
```

**Purpose**: Checks whether Slack has successfully reached this UFO deployment using the currently trusted signing secret. This is the final proof that the connection works, not just that credentials were entered.

**Data flow**: It calculates the current signing-secret fingerprint, looks for a saved verification marker, reads and parses that marker, and compares the marker fingerprint with the current one. If they match, it mirrors the verification into the workspace store and returns true; otherwise it returns false.

**Call relations**: `slack_connect_handler` calls this after Slack identity is known. It uses Slack surface helpers to compute the trusted fingerprint and to mirror the verified status so other parts of the system can see the same connection proof.

*Call graph*: called by 1 (slack_connect_handler); 3 external calls (loads, mirror_url_verified, verifying_fingerprint).


##### `slack_manifest_handler`  (lines 295–310)

```
async def slack_manifest_handler(ctx: ToolContext, args: SlackManifestInput) -> ToolResult
```

**Purpose**: Generates a ready-to-paste Slack app manifest for the manual setup path. The manifest tells Slack which permissions, event subscriptions, and request URLs the UFO bot needs.

**Data flow**: It receives a desired bot display name, checks that the name is simple and within Slack-friendly limits, reads the public base URL from the context, builds the Slack event and interactivity URLs, fills the manifest template, and returns the manifest as text.

**Call relations**: This is called as its own tool when a user chooses the manifest setup path. It uses `_events_url` so the generated Slack app sends events to the same endpoint used by the rest of the Slack surface.

*Call graph*: calls 1 internal fn (_events_url); 3 external calls (__init__, __init__, match).


##### `slack_channels_handler`  (lines 313–336)

```
async def slack_channels_handler(ctx: ToolContext, args: SlackChannelsInput) -> ToolResult
```

**Purpose**: Searches the connected Slack workspace for conversations such as channels, group direct messages, and one-to-one direct messages. It lets the agent find a place to read or post by name, topic, purpose, or people involved.

**Data flow**: It reads the stored Slack bot token, checks that Slack identity has already been resolved, then runs a Slack conversation search using the bot token and bot user ID. It returns JSON containing matching conversations and a flag saying whether the result was cut short.

**Call relations**: This is the runtime discovery tool, used after Slack is connected. It calls `read_identity` to know which bot is installed and creates a `SlackConversationSearch` to page through Slack’s conversation list.

*Call graph*: 5 external calls (__init__, __init__, __init__, dumps, read_identity).


##### `_token_diagnosis`  (lines 339–345)

```
def _token_diagnosis(error: str) -> str
```

**Purpose**: Turns a Slack token error code into a plain-language explanation. This helps users understand whether to re-copy the bot token or whether Slack returned some other failure.

**Data flow**: It receives an error string from Slack. If the error means the token is missing, revoked, inactive, or invalid, it returns a specific instruction to re-copy the Bot User OAuth Token; otherwise it returns a generic Slack `auth.test` failure message.

**Call relations**: _derive_manifest_identity calls this when Slack rejects the token during the manifest setup path, so the final setup response can be useful instead of exposing only a raw Slack error code.

*Call graph*: called by 1 (_derive_manifest_identity).

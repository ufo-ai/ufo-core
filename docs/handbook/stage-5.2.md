# OAuth and connection callbacks  `stage-5.2`

This stage is the doorway back into UFO after a user has visited an outside service to approve access. It is not the main work loop. It is a setup and support step used when someone links an account, installs an app, or checks a connection. The outside service sends the browser back to a special web address, called a callback URL, and these files turn that return visit into a trusted connection record.

The core CLI surface exposes UFO’s general OAuth callback address and finishes the connection when the browser returns. The Composio and Pipedream providers do the same job for their hosted approval pages: they send the user out to approve access, then translate the result back into the form UFO expects, making sure the account just approved is the one UFO binds. The coding connection file handles GitHub App setup for a workspace. It builds the install link, checks GitHub’s return message, and saves only installations the signed-in member is allowed to use.

## Files in this stage

### Core OAuth Callback Surface
Defines the primary browser redirect endpoint that completes an approved OAuth connection inside UFO.

### `core/src/ufo/surfaces/cli.py`

`io_transport` · `request handling`

When a user connects an outside account, such as a service provider account, the provider sends the user’s browser back to a callback URL with two important pieces of information: a state value and a code. The state is like a tamper-resistant claim ticket that proves this callback belongs to the right user, agent, and conversation. The code is the short-lived token that can be exchanged for the actual connected account grant.

This file defines that callback route using FastAPI, a Python web framework. It does not require normal bearer authentication, because the browser may not have an API token at this point. Instead, safety comes from checking the sealed state value that was created when the connection flow began.

The route first asks the grants system for the installed connection flow. If the system is not configured to support connecting accounts, it returns a service-unavailable error. It then checks that both required callback fields are present. Finally, it asks the connection flow to complete the handoff: verify the state, exchange the provider’s code, and record the resulting account grant. On success, it returns a plain text message telling the user the account is connected and they can go back to chat.

#### Function details

##### `connect_callback`  (lines 19–39)

```
async def connect_callback(state: str='', code: str='') -> PlainTextResponse
```

**Purpose**: This is the HTTP endpoint the OAuth provider sends the browser to after the user approves a connection. It validates the callback, completes the account-linking flow, and gives the user a simple success or error message.

**Data flow**: It receives two query values from the browser: state and code. It gets the configured connect flow, rejects the request if connection support is unavailable or either value is missing, then passes the state and code into the flow so it can verify and finish the connection. If that succeeds, it returns plain text naming the connected provider and account; if something is wrong, it raises an HTTP error with the right status code.

**Call relations**: FastAPI calls this function when a GET request arrives at the connect callback path. The function asks ufo.grants.installed_connect_flow for the object that knows how to finish the OAuth handoff. It uses FastAPI HTTPException to turn expected failure cases into HTTP error responses, and PlainTextResponse to send the final human-readable success message back to the browser.

*Call graph*: 3 external calls (HTTPException, PlainTextResponse, installed_connect_flow).


### Hosted OAuth Bridges
Connects UFO account-linking flows to hosted third-party OAuth consent screens and maps their callback results back into UFO.

### `extensions/composio/ufo_ext_composio/provider.py`

`io_transport` · `request handling during connector account connection`

OAuth is the common “approve this app to access my account” flow. UFO expects that flow to start with a simple authorization URL, but Composio requires an asynchronous API call to create the real consent link. This file solves that mismatch.

Instead of sending the browser directly to Composio, `ComposioOAuthProvider.authorize_url` sends it to this extension's own `/ext/composio/oauth` route. That route can do asynchronous work. On the first visit, `oauth_route` asks Composio for a connect link for the requested provider, tied to the current workspace, then redirects the browser to Composio's hosted consent page.

After the user approves or cancels, Composio redirects back to the same route. If approval succeeded, Composio includes a `connected_account_id`. The route forwards the browser to UFO's normal callback and passes that account id as the OAuth `code`. Later, `ComposioOAuthProvider.exchange` checks with Composio that this account really belongs to this workspace's Composio user and matches the expected provider before binding it.

A key point is that UFO never receives or stores the provider's secret token. The token stays with Composio, and Composio runs the connector tools server-side.

#### Function details

##### `ComposioOAuthProvider.authorize_url`  (lines 43–45)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds the URL that UFO should send the user's browser to when starting a Composio-backed connection. It does not contact Composio directly; it points to this extension's bridge route so the real async setup can happen there.

**Data flow**: It receives UFO's sealed `state` value and the final UFO callback URL. It packages the provider name, state, and callback into query parameters, extracts the scheme and host from the callback URL, and returns a bridge URL under `/ext/composio/oauth`. Nothing is stored or changed.

**Call relations**: This is the first step of the flow. UFO's connect system calls it when it needs an authorization URL. It uses `_origin` to make sure the callback URL has a usable web origin, then hands the browser off to `oauth_route`, which performs the Composio-specific work.

*Call graph*: calls 1 internal fn (_origin); 1 external calls (urlencode).


##### `ComposioOAuthProvider.exchange`  (lines 47–53)

```
async def exchange(self, code: str, _redirect_uri: str, workspace_id: UUID, _state: str) -> OAuthAccount
```

**Purpose**: Turns the returned Composio connected-account id into an `OAuthAccount` that UFO can bind to the workspace. It also verifies that the account id belongs to the expected workspace user and provider, which prevents someone from injecting a foreign account id.

**Data flow**: It receives the `code` from UFO's callback, which in this flow is actually Composio's connected-account id. It combines the workspace id with Composio's external-user prefix to form the expected Composio user id. It then asks the Composio client to fetch and confirm the connected account for that user and provider, and returns the resulting account object.

**Call relations**: This runs after `oauth_route` has forwarded the successful consent result back to UFO's normal callback. It calls `composio_client().connected_account(...)` to validate and retrieve the account details before UFO records the connection.

*Call graph*: 1 external calls (composio_client).


##### `oauth_route`  (lines 56–93)

```
async def oauth_route(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: Acts as the HTTP bridge between UFO's connect callback and Composio's hosted consent page. It supports both halves of the browser trip: starting consent and receiving the result.

**Data flow**: It reads query parameters from the incoming request. If `state` or `callback` is missing, it returns a 400 error because the flow cannot be safely continued. If Composio returned a `connected_account_id`, it redirects to UFO's callback with that id as `code`. If Composio returned a failure `status` without an account id, it returns a clear error instead of silently restarting. Otherwise, it treats the request as the start of consent: it checks the provider, builds a return URL back to itself, asks Composio for a connect link for the current workspace user, and redirects the browser there.

**Call relations**: The browser arrives here first because `ComposioOAuthProvider.authorize_url` pointed it here. On the start leg, this function calls the Composio client to create the real consent link. On the return leg, it redirects back to UFO's core callback so `ComposioOAuthProvider.exchange` can finish binding the connected account.

*Call graph*: calls 1 internal fn (_origin); 3 external calls (Response, composio_client, urlencode).


##### `_origin`  (lines 96–100)

```
def _origin(url: str) -> str
```

**Purpose**: Extracts the base web origin from a URL, meaning just the scheme and host such as `https://example.com`. It also rejects callback URLs that are not valid HTTP or HTTPS URLs.

**Data flow**: It receives a URL string, parses it, and checks that it has an `http` or `https` scheme and a host name. If the URL is valid, it returns `scheme://host`. If not, it raises an error explaining that the OAuth bridge needs a proper scheme and host.

**Call relations**: Both `ComposioOAuthProvider.authorize_url` and `oauth_route` use this helper when building bridge URLs. It keeps those URLs anchored to the same origin as the callback, like copying only the street address from a full set of driving directions.

*Call graph*: called by 2 (authorize_url, oauth_route); 1 external calls (urlparse).


### `extensions/pipedream/ufo_ext_pipedream/provider.py`

`io_transport` · `request handling during connector OAuth connection`

This file solves a timing mismatch. ufo expects an OAuth provider to give it a web address right away, but Pipedream first needs an asynchronous API call to create a temporary Connect token. To bridge that gap, this file sends the user’s browser to ufo’s own extension route first, then that route asks Pipedream for the token and redirects the browser onward to Pipedream’s consent page.

Think of it like a front desk. ufo sends the visitor to the front desk, the front desk prepares the correct visitor pass, then points the visitor to the right room. When the visitor returns, the front desk checks which account was just connected and sends that account id back to ufo.

The main class, PipedreamOAuthProvider, is the provider object registered with ufo’s connect system. Its authorize_url method builds the first bridge URL, and its exchange method later confirms that the returned account belongs to the expected Pipedream app before creating ufo’s OAuthAccount record. The oauth_route function is the actual browser-facing bridge. It starts the Pipedream consent process, receives success or failure redirects, and refuses to silently restart on failure. A key safety detail is that every connection attempt gets a state-specific Pipedream external user id, so two overlapping connection attempts cannot accidentally claim each other’s accounts.

#### Function details

##### `PipedreamOAuthProvider.authorize_url`  (lines 50–52)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: This creates the first URL that ufo should send the user to when they need to connect an account. Instead of pointing directly at Pipedream, it points at this extension’s own OAuth bridge route, because the bridge must first ask Pipedream for a temporary Connect token.

**Data flow**: It receives ufo’s sealed state value and the final callback URL. It packages the provider name, state, and callback into query parameters, extracts the origin from the callback URL, and returns a bridge URL under /ext/pipedream/oauth. Nothing is stored or changed; the result is just the next browser destination.

**Call relations**: This is the first step in the connect story. ufo calls it when it needs an authorization URL. It uses _origin to keep the bridge on the same scheme and host as the callback, then hands the browser to oauth_route for the asynchronous Pipedream work.

*Call graph*: calls 1 internal fn (_origin); 1 external calls (urlencode).


##### `PipedreamOAuthProvider.exchange`  (lines 54–63)

```
async def exchange(self, code: str, _redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: This turns the account id returned from the browser bridge into the OAuthAccount object that ufo can attach to a grant. It also checks that the account really belongs to the expected Pipedream app before accepting it.

**Data flow**: It receives the returned code, which in this flow is a Pipedream connected account id, plus the workspace id and state. From the workspace and state it rebuilds the Pipedream external user id, fetches that exact connected account from Pipedream, checks that its app matches this provider, and returns an OAuthAccount containing the account id. If the app is wrong, it raises an error instead of binding the wrong account.

**Call relations**: This runs after oauth_route redirects back to ufo’s core callback with a code. It asks the Pipedream client for the exact account, relies on the same state-derived external user identity used earlier in the route, and hands ufo a clean OAuthAccount only after the ownership and app checks pass.

*Call graph*: 4 external calls (__init__, PipedreamError, connection_user_id, pipedream_client).


##### `oauth_route`  (lines 66–109)

```
async def oauth_route(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: This is the browser bridge for the whole Pipedream consent flow. It both starts consent by sending the user to Pipedream, and receives the browser back afterward to tell ufo which account was connected.

**Data flow**: It reads query parameters from the incoming HTTP request: provider, state, callback, and sometimes an outcome marker. If state or callback is missing, it returns a bad request response. If the provider is unknown, it returns not found. On a successful return from Pipedream, it finds the newest connected account for this state-specific external user and redirects to ufo’s callback with the state and account id. On a failed return, it returns a clear error message. On the starting leg, it creates a Pipedream Connect token with success and error redirects pointing back to this same route, builds a Pipedream Connect Link for the requested app, optionally adds a custom OAuth app id from the environment, and redirects the browser there.

**Call relations**: This route is reached first through PipedreamOAuthProvider.authorize_url. During the start leg it calls into the Pipedream client to create a Connect token, then sends the browser to Pipedream. During the return leg it calls the Pipedream client again to resolve the connected account, then redirects back to ufo core so PipedreamOAuthProvider.exchange can verify and bind that account.

*Call graph*: calls 1 internal fn (_origin); 5 external calls (Response, get, connection_user_id, pipedream_client, urlencode).


##### `_origin`  (lines 112–116)

```
def _origin(url: str) -> str
```

**Purpose**: This small helper extracts just the scheme and host from a URL, such as turning https://example.com/path into https://example.com. It is used so bridge URLs stay on the same web origin as the callback.

**Data flow**: It receives a URL string, parses it, and checks that it has an http or https scheme and a host name. If the URL is valid, it returns only scheme plus host. If not, it raises an error because the OAuth bridge cannot safely build redirects without a real origin.

**Call relations**: PipedreamOAuthProvider.authorize_url uses it when building the first bridge URL. oauth_route uses it when building the return URLs that Pipedream should call after consent succeeds or fails. In both cases it is the guardrail that keeps redirect construction based on a valid web address.

*Call graph*: called by 2 (authorize_url, oauth_route); 1 external calls (urlparse).


### GitHub App Installation Callback
Handles workspace GitHub App installation by generating the install link, validating GitHub’s callback, and storing the authorized installation.

### `extensions/coding/ufo_ext_coding/connect.py`

`domain_logic` · `GitHub connection setup and callback request handling`

This file solves a trust problem. A GitHub App installation is identified by a small number, but ufo cannot safely accept that number just because it appears in a browser redirect or was typed into a credential field. That would be like letting someone claim a house key fits their house without ever testing it in the lock.

The flow starts when an admin asks to connect GitHub. `connect_github` checks that the speaker is a workspace admin and that this deployment has a GitHub App configured. It then creates a short-lived sealed state value. A sealed value is a protected token that ufo can later open and verify; here it ties the browser return to the correct workspace and credential slot. The admin receives a GitHub install link containing that state.

After GitHub finishes the install, it redirects the browser back to ufo with an authorization code and an installation id. `github_installed` does not trust the installation id by itself. It exchanges the code for the member’s own GitHub access token, asks GitHub which ufo installations that member can actually reach, and only binds the id if GitHub confirms it is in that list. The final stored credential is therefore tied to both the workspace and a verified GitHub installation.

#### Function details

##### `connect_github`  (lines 48–72)

```
async def connect_github(ctx: ToolContext, args: ConnectGitHubInput) -> ToolResult
```

**Purpose**: This starts the GitHub connection process for a workspace. It gives an admin a GitHub App installation link that is tied to the current workspace by a protected state value.

**Data flow**: It receives the tool context and a small input message for the activity timeline. It checks whether the current speaker is an admin, checks whether this deployment has a GitHub App id, asks the credential system to create a sealed authorization state for the GitHub installation slot, and returns a tool result containing the install URL. Nothing is connected yet; the output is the next link the admin must visit.

**Call relations**: This is called when the agent needs to help an admin connect GitHub. It relies on the tool context to confirm admin status and create the sealed credential authorization, and it asks the manifest for the GitHub App id before producing the message shown back to the user.

*Call graph*: calls 2 internal fn (begin_credential_authorization, speaker_is_admin); 3 external calls (__init__, __init__, github_app_id).


##### `install_workspace`  (lines 75–80)

```
def install_workspace(request: Request) -> UUID | None
```

**Purpose**: This extracts the workspace named by GitHub’s return request, but only if the request contains a valid sealed state created for this exact credential slot and purpose.

**Data flow**: It receives an HTTP request and reads the `state` query parameter. It passes that state, the GitHub installation slot name, and the expected install purpose to the credential helper. The result is either the workspace identifier hidden inside the valid state, or nothing if the state is missing, expired, or not meant for this use.

**Call relations**: This is used by the surrounding web routing flow to decide which workspace GitHub’s browser redirect belongs to. It delegates the actual seal-checking to `authorized_slot_workspace`, so this file does not have to parse or trust the state value itself.

*Call graph*: 1 external calls (authorized_slot_workspace).


##### `GitHubInstallExchange.reaches`  (lines 99–126)

```
async def reaches(self, code: str, installation_id: str) -> bool
```

**Purpose**: This asks GitHub whether the person who just authorized can actually access the installation id being claimed. It is the key safety check that stops someone from binding another organization’s installation by guessing or copying an id.

**Data flow**: It receives a GitHub authorization code and an installation id. First it sends the code, client id, and client secret to GitHub to get the member’s access token. If GitHub does not return a token, it raises a GitHub authorization error. Then it uses that token to ask GitHub for the installations visible to that member. It returns true only if the requested installation id appears in that list and belongs to this ufo GitHub App.

**Call relations**: This method is called during the GitHub callback flow after `github_installed` has received a code and installation id. It talks directly to GitHub through `httpx.AsyncClient`, and hands back a simple yes-or-no answer that `github_installed` uses to decide whether to bind the credential.

*Call graph*: 2 external calls (__init__, AsyncClient).


##### `install_exchange`  (lines 129–140)

```
def install_exchange() -> GitHubInstallExchange
```

**Purpose**: This builds the object that can perform the GitHub verification exchange using this deployment’s GitHub App credentials.

**Data flow**: It reads the GitHub App id from the manifest and the GitHub OAuth client id and secret from environment variables. If the app id is missing, it stops with a runtime error. Otherwise it returns a `GitHubInstallExchange` containing the information needed to ask GitHub about the authorization code and installation.

**Call relations**: This is called by `github_installed` just before checking the callback with GitHub. It keeps credential lookup in one place and creates the `GitHubInstallExchange` that performs the actual external GitHub calls.

*Call graph*: called by 1 (github_installed); 2 external calls (__init__, github_app_id).


##### `github_installed`  (lines 143–172)

```
async def github_installed(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: This finishes the GitHub App installation callback. It verifies that the browser return includes a real GitHub authorization and that the claimed installation belongs to the authorizing member before saving it for the workspace.

**Data flow**: It receives the extension context and the HTTP request from GitHub. It reads the `code` and `installation_id` query parameters. If either is missing, it returns an error page. Otherwise it creates an install exchange, asks GitHub whether the authorizing member reaches that installation, and handles three outcomes: GitHub rejects the code, the member cannot reach that installation, or the check succeeds. On success, it binds the installation id into the GitHub credential slot and returns a success page.

**Call relations**: This is the main return point after the admin visits the install link created by `connect_github`. It calls `install_exchange` to get a verifier, uses that verifier’s `reaches` method, and uses `_page` to turn each result into a small browser page for the admin.

*Call graph*: calls 2 internal fn (_page, install_exchange).


##### `_page`  (lines 175–183)

```
def _page(message: str, status: int) -> Response
```

**Purpose**: This creates the simple HTML page shown in the browser after the GitHub callback finishes or fails.

**Data flow**: It receives a message and an HTTP status code. It wraps the message in a small HTML document and returns a response with that status and an HTML media type. It does not change stored data.

**Call relations**: This helper is called by `github_installed` for every browser-facing outcome: missing authorization details, GitHub rejection, failed ownership check, or successful connection. It keeps the callback function focused on decisions while this helper formats the response.

*Call graph*: called by 1 (github_installed); 1 external calls (Response).

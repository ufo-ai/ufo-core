# Authentication, sessions, member identity, and credential grants  `stage-6`

This stage is the system’s identity checkpoint. It runs when people sign in, when browsers return from “connect account” pages, when agents need stored credentials, and when protected sandbox links are opened. The shared signing code makes tamper-proof tokens; bearer tokens prove a user’s workspace and email, surface tokens carry trusted route context, and ingress tokens guard access to sandbox app ports. Session and identity helpers define whether work is acting as a workspace or a specific member. Seat and audience rules decide which members may talk to which agents, while operator login code protects admin-only tools.

The credential side stores secrets safely and grants agents limited use of outside accounts. Core grant code tracks connected OAuth-style accounts and refreshable OpenAI or Anthropic logins. Web flows connect OpenAI by device code and Anthropic by OAuth or pasted key. The CLI surface route receives OAuth return trips. Composio and Pipedream providers bridge UFO to hosted consent screens, with Pipedream token code turning connections into usable sandbox credentials. iMessage tools verify phone ownership by opt-in text. Package marker files simply make these authentication folders importable.

## Files in this stage

### Hosted connector bridges
These files adapt Composio and Pipedream hosted connection flows into UFO's account-connection and sandbox credential model.

### `extensions/composio/ufo_ext_composio/provider.py`

`io_transport` · `account connection request handling`

This file solves a mismatch between two systems. ufo expects an OAuth provider to immediately give it an authorization URL, like handing a browser a fixed address to visit. Composio, however, must be asked asynchronously to create a fresh connect link. So this file adds a small middle stop: instead of sending the browser directly to Composio, `authorize_url` sends it to this extension's own `/ext/composio/oauth` route.

That route works like a ticket desk. On the first visit, it checks the requested provider, asks Composio to create a consent link for the current workspace, and redirects the browser there. After the user finishes or cancels consent, Composio sends the browser back to the same route. If Composio includes a `connected_account_id`, the route forwards the browser to ufo's normal callback and passes that account id as the OAuth `code`.

Later, `exchange` receives that `code` and verifies with Composio that the connected account really belongs to this workspace's Composio user and matches the expected provider. The actual secret token is never copied into ufo; it stays with Composio, and tools run through Composio server-side. This protects against accidentally binding someone else's account id and avoids storing provider tokens locally.

#### Function details

##### `ComposioOAuthProvider.authorize_url`  (lines 43–45)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds the first URL that the user's browser should visit when starting a Composio-backed connection. Instead of pointing directly at Composio, it points at this extension's bridge route so the async Composio link can be created later.

**Data flow**: It receives ufo's sealed `state` value and the core `redirect_uri` callback. It packages the provider name, state, and callback into query parameters, extracts the web origin from the callback URL, and returns a bridge URL under `/ext/composio/oauth`. It does not contact Composio or change stored data.

**Call relations**: This is the first step in the connection story. ufo's connect registry calls it when it needs a browser URL. It uses `_origin` to make sure the callback has a valid scheme and host, then hands the browser off to `oauth_route`, which does the real Composio redirect.

*Call graph*: calls 1 internal fn (_origin); 1 external calls (urlencode).


##### `ComposioOAuthProvider.exchange`  (lines 47–57)

```
async def exchange(self, code: str, _redirect_uri: str, workspace_id: UUID, _state: str) -> OAuthAccount
```

**Purpose**: Turns the returned Composio connected-account id into ufo's account record after checking that the account belongs to the right workspace and provider. This is the safety check that prevents a random or foreign account id from being accepted.

**Data flow**: It receives the `code`, which in this flow is really a Composio connected account id, plus the workspace id. It builds the expected Composio user id for that workspace, asks the Composio client to fetch and validate the connected account, then tries to fetch a friendly account label. It returns an `OAuthAccount` containing the account id and optional label; it does not return or store the provider's secret token.

**Call relations**: This runs after `oauth_route` has sent the browser back to ufo's normal callback with the connected account id as the code. It calls the Composio client to confirm ownership and provider match, then hands ufo a small account descriptor it can bind to the connector grant.

*Call graph*: 2 external calls (__init__, composio_client).


##### `oauth_route`  (lines 60–97)

```
async def oauth_route(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: Acts as the browser bridge for both halves of the Composio consent flow. It either starts consent by creating a Composio link, or finishes consent by forwarding Composio's connected account id back to ufo's core callback.

**Data flow**: It reads query parameters from the incoming request, especially `state`, `callback`, `provider`, `connected_account_id`, and `status`. If `state` or `callback` is missing, it returns a 400 error. If a connected account id is present, it redirects to the original callback with `state` and `code`. If Composio reports a status without an account id, it returns a clear failure response instead of restarting consent. Otherwise, it asks Composio for a connect link for the requested provider and current workspace, then redirects the browser to that link.

**Call relations**: Browsers arrive here first from `ComposioOAuthProvider.authorize_url`. On that start leg, it calls the Composio client to mint a hosted consent link. Composio later sends the browser back here; on that return leg, this route redirects onward to ufo's core callback so `ComposioOAuthProvider.exchange` can validate and bind the account.

*Call graph*: calls 1 internal fn (_origin); 3 external calls (Response, composio_client, urlencode).


##### `_origin`  (lines 100–104)

```
def _origin(url: str) -> str
```

**Purpose**: Extracts just the scheme and host from a full URL, such as turning `https://example.com/path` into `https://example.com`. It also rejects URLs that are not usable as browser callback origins.

**Data flow**: It receives a URL string, parses it, and checks that it has an `http` or `https` scheme and a host name. If the URL is valid, it returns the origin string. If not, it raises an error explaining that the connect callback needs a scheme and host.

**Call relations**: Both `ComposioOAuthProvider.authorize_url` and `oauth_route` use this helper when building bridge URLs. It keeps those URLs anchored to the same web origin as the trusted callback, rather than guessing or accepting malformed callback addresses.

*Call graph*: called by 2 (authorize_url, oauth_route); 1 external calls (urlparse).


### `extensions/pipedream/ufo_ext_pipedream/provider.py`

`io_transport` · `request handling during connector account connection`

OAuth is the web sign-in-and-consent process where a user says, “yes, this app may access my account.” ufo expects that process to begin with a normal web address and end with a code it can exchange. Pipedream works a little differently: before showing its hosted Connect page, ufo must first make an asynchronous API call to mint a short-lived Connect token. This file fills that gap.

The main idea is a browser detour. Instead of sending the user's browser straight to Pipedream, `authorize_url` sends it to this extension's own `/ext/pipedream/oauth` route. That route can do the async work: create the Pipedream Connect token, build a Pipedream consent link for the right connector app, and redirect the browser there.

When Pipedream sends the browser back, the same route checks whether consent succeeded. If it did, it finds the newest account connected for this exact sealed state and sends that account id back to ufo core as the OAuth “code.” Later, `exchange` verifies that the account really belongs to the expected Pipedream app and to the state-scoped external user before creating an `OAuthAccount`. This prevents two overlapping connection attempts from accidentally grabbing each other's accounts, like using numbered claim tickets at a coat check.

#### Function details

##### `PipedreamOAuthProvider.authorize_url`  (lines 62–64)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: This builds the first URL that ufo gives to the user's browser when account connection starts. Instead of pointing directly at Pipedream, it points at this extension's OAuth bridge route so the async Pipedream setup can happen first.

**Data flow**: It receives a sealed `state` value and the final `redirect_uri` that ufo core expects. It extracts the web origin from the redirect URI, packages the provider name, state, and callback into query parameters, and returns a bridge URL under `/ext/pipedream/oauth`.

**Call relations**: This is the opening move of the connect flow. It uses `_origin` to keep the bridge on the same scheme and host as the callback, then the browser later lands in `oauth_route`, which performs the Pipedream-specific consent setup.

*Call graph*: calls 1 internal fn (_origin); 1 external calls (urlencode).


##### `PipedreamOAuthProvider.exchange`  (lines 66–84)

```
async def exchange(self, code: str, _redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: This turns the account id returned from the bridge into the `OAuthAccount` that ufo stores as the connected account. It also checks that the account belongs to the expected Pipedream app before allowing the grant to bind.

**Data flow**: It receives the returned `code`, which is really a Pipedream account id, plus the workspace id and sealed state. It recreates the Pipedream external user id for that workspace and state, asks Pipedream for that connected account, rejects it if it is for the wrong app, optionally reads a friendly account label, and may compute a commit identity for connectors that need one. It returns an `OAuthAccount` containing the account id, optional label, and optional commit identity.

**Call relations**: This runs after `oauth_route` has redirected back to ufo core with an account id. It calls Pipedream client helpers to fetch and verify the account, reads connector settings from `CONNECTORS`, and calls `commit_identity` when the connector needs a stable identity for later sandbox commits.

*Call graph*: 6 external calls (__init__, get, PipedreamError, connection_user_id, pipedream_client, commit_identity).


##### `oauth_route`  (lines 87–140)

```
async def oauth_route(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: This is the browser-facing bridge for both halves of the Pipedream consent trip. It starts consent by minting a Pipedream Connect token, and it finishes consent by sending the connected account id back to ufo core.

**Data flow**: It reads query parameters from the incoming request: provider, state, callback, and optionally an outcome from Pipedream. If required values are missing, it returns an error response. If the provider is unknown, it returns a not-found response. If the outcome says consent succeeded, it finds the newest matching Pipedream account for this workspace and state, then redirects to the callback with the state and account id. If the outcome says failure, it returns a clear error page. If there is no outcome yet, it creates a Pipedream Connect token with success and error redirects back to this same route, builds the hosted Pipedream Connect Link, optionally attaches a custom OAuth app id from the environment, and redirects the browser to Pipedream.

**Call relations**: The browser reaches this route because `PipedreamOAuthProvider.authorize_url` pointed it here. On the start leg, it calls Pipedream to mint the token and hands the browser off to Pipedream. On the return leg, it calls Pipedream again to resolve the connected account, then hands control back to ufo core, which will later call `PipedreamOAuthProvider.exchange`.

*Call graph*: calls 1 internal fn (_origin); 5 external calls (Response, get, connection_user_id, pipedream_client, urlencode).


##### `_origin`  (lines 143–147)

```
def _origin(url: str) -> str
```

**Purpose**: This extracts the scheme and host from a URL, such as `https://example.com`, so redirect links can be built on the same web origin. It also protects the OAuth bridge from being built with an invalid callback URL.

**Data flow**: It receives a URL string, parses it, and checks that it has an `http` or `https` scheme and a host name. If the URL is valid, it returns only the scheme and host. If not, it raises an error explaining that the callback needs a scheme and host.

**Call relations**: `PipedreamOAuthProvider.authorize_url` uses this when building the initial bridge URL, and `oauth_route` uses it when building the return URLs that Pipedream will call after consent. This keeps all legs of the browser trip anchored to a valid web origin.

*Call graph*: called by 2 (authorize_url, oauth_route); 1 external calls (urlparse).


### `extensions/pipedream/ufo_ext_pipedream/token.py`

`domain_logic` · `connector consent and sandbox credential setup`

Pipedream can hold OAuth tokens for connected services, but most sandbox tools cannot use a remote token broker directly. For GitHub in particular, command-line tools expect ordinary credentials: the GitHub CLI reads a `GH_TOKEN` environment variable, and `git clone` over HTTPS expects a basic username and password. This file bridges that gap. It describes when a connector should expose a command-line credential, how to fetch the real token from Pipedream, and how Git should use it.

The file also solves a separate but related problem: if the sandbox creates a Git commit, GitHub needs an author name and email that match the connected account. The `commit_identity` function asks GitHub who the token belongs to, then creates GitHub’s standard no-reply email address for that account. That avoids exposing a real email address and still lets GitHub attribute the commit correctly.

A small cache keeps recently fetched Pipedream tokens for a few minutes, like keeping a ticket at the front desk instead of asking the ticket office again for every doorway. The cache is short-lived, so credentials are not held longer than needed. If GitHub refuses or returns incomplete account information, this file deliberately raises an error rather than guessing, because a bad guess could make commits appear under the wrong identity.

#### Function details

##### `PipedreamGrantSecret.secret`  (lines 45–52)

```
async def secret(self, workspace_id: UUID, account_id: str) -> str
```

**Purpose**: Fetches the real provider token for a connected Pipedream account, with a short cache so repeated lookups in the same turn do not keep asking Pipedream. It is used when the sandbox needs the token behind a declared command-line credential.

**Data flow**: It receives a workspace ID and an account ID. It first checks its in-memory `held` dictionary to see whether that account already has a still-valid token, using the current monotonic clock time to avoid problems if the system clock changes. If the cached token is still fresh, it returns it. Otherwise it asks the current Pipedream client for the account token, stores that token with a new expiry time, and returns the token string.

**Call relations**: When a `CliCredential` needs its secret, this object supplies it. Inside the lookup, it calls `time.monotonic` to judge cache freshness and obtains a Pipedream client through `ufo_ext_pipedream.client.pipedream_client` so token reads go through the configured client, including any test transport overrides.

*Call graph*: 2 external calls (monotonic, pipedream_client).


##### `cli_credential`  (lines 55–64)

```
def cli_credential(spec: pipedream.ConnectorSpec) -> CliCredential | None
```

**Purpose**: Builds the command-line credential description for a connector, or returns nothing if that connector should keep its token only inside Pipedream. This is what tells the sandbox which environment variable and Git helper setup to use.

**Data flow**: It receives a connector specification. If the specification does not name a command-line environment variable, the function returns `None`. If it does, the function creates a `CliCredential` with that environment variable, the authorization header name, a new `PipedreamGrantSecret` for retrieving the token when needed, and the GitHub Git wiring information that lets Git authenticate through the GitHub CLI credential helper.

**Call relations**: This function is used when connector metadata is being turned into sandbox-facing credentials. It hands off secret retrieval to `PipedreamGrantSecret` and packages the result through `CliCredential.__init__`, so later code can treat the credential as a clear recipe rather than knowing all the GitHub-specific details.

*Call graph*: 2 external calls (__init__, __init__).


##### `commit_identity`  (lines 67–124)

```
async def commit_identity(spec: pipedream.ConnectorSpec, account_id: str, workspace_id: UUID) -> CommitIdentity | None
```

**Purpose**: Finds the Git author name and no-reply email address for a connected GitHub account. This lets commits made by the sandbox be attributed to the same GitHub user whose token is used to push them.

**Data flow**: It receives a connector specification, an account ID, and a workspace ID. If the connector is not GitHub, it returns `None` because other Pipedream-held tokens are not used for sandbox Git commits here. For GitHub, it asks Pipedream for the account token, then uses an HTTP client to call GitHub’s `/user` endpoint with that token. If GitHub rejects the request or returns data without a numeric user ID and a login, the function raises a `PipedreamError` instead of inventing an identity. If the response is valid, it chooses the user’s display name only when it contains at least one letter or number; otherwise it falls back to the login. It returns a `CommitIdentity` containing that name and GitHub’s standard `<id>+<login>@users.noreply.github.com` email address.

**Call relations**: This function runs when consent for a GitHub connection is completed, so the system can store the commit identity once rather than asking GitHub every time a sandbox opens. It uses `ufo_ext_pipedream.client.pipedream_client` to get the token, `httpx.AsyncClient` to read GitHub’s user record, `ufo_ext_pipedream.client.PipedreamError` to report unsafe or failed identity reads, and `CommitIdentity.__init__` to package the final author information for later Git operations.

*Call graph*: 4 external calls (__init__, AsyncClient, PipedreamError, pipedream_client).


### Web agent audience policy
This file defines which workspace members can see, chat with, and administratively manage access to web agents.

### `extensions/web/ufo_ext_web/audience.py`

`domain_logic` · `request handling`

The web portal needs a clear answer to a sensitive question: when a person signs in, which agents and conversations are they allowed to reach? This file is the web surface’s rulebook for that. It treats a member’s email address as the web identity, then stores explicit access grants as small records keyed by agent and email. Think of those records like named badges at a front desk: if the badge exists, that person can enter that agent’s room.

The main flow builds a WebAudience for one signed-in member. It checks whether the email belongs to a seated workspace member, gathers all agents, reads any explicit grants, and adds agents that are visible to the whole workspace or owned by that member. Workspace admins get a wider view: they can reach every agent.

The file also exposes tools that can be called from the system: grant web access, revoke web access, and acknowledge opening a private transcript. These are deliberately restricted. Only a speaking workspace admin can grant or revoke access, and transcript access is recorded so there is an audit trail. Without this file, the portal would have no central, consistent authority for web access, which could either hide agents people should see or expose private agents and transcripts to the wrong people.

#### Function details

##### `web_extension`  (lines 38–45)

```
def web_extension() -> ExtensionContext
```

**Purpose**: Creates the web extension’s own access handle so code running from the web surface can read and write the web audience store. This matters because audience grants live in the web extension’s private storage area, not in a global shared place.

**Data flow**: It takes no input. It builds a scoped store for the web extension, attaches an empty credential-access declaration, and returns an ExtensionContext that other code can use for transactions and stored rows.

**Call relations**: Surface code uses this when it needs to cross from a web request into the extension’s own stored audience records. It hands back the context that later functions use to list, add, or delete grants.

*Call graph*: 3 external calls (__init__, __init__, __init__).


##### `_grant_key`  (lines 48–49)

```
def _grant_key(agent_id: UUID, email: str) -> str
```

**Purpose**: Builds the storage key for one web access grant. The key combines the agent id and the member email so the store has one predictable row for each agent-person pair.

**Data flow**: It receives an agent UUID and an email address. It trims and lowercases the email, joins it with the audience prefix and agent id, and returns the resulting string key.

**Call relations**: The grant and revoke actions both use this helper so they write and delete the exact same storage location. That shared key format is what keeps granting and revoking in sync.

*Call graph*: called by 2 (_grant, _revoke).


##### `granted_emails`  (lines 52–59)

```
async def granted_emails(store: ScopedStore) -> dict[UUID, tuple[str, ...]]
```

**Purpose**: Reads all saved web access grants and presents them grouped by agent. This is useful for an administration view that wants to show who has been explicitly granted access to each agent.

**Data flow**: It receives a scoped store, lists all stored rows under the audience prefix, splits each key into an agent id and an email, then returns a dictionary from agent UUID to a sorted tuple of emails.

**Call relations**: This function reads the same rows that the grant and revoke tools write. It does not change access; it gives the admin side a clean summary of the current grant list.

*Call graph*: calls 1 internal fn (list); 1 external calls (UUID).


##### `_granted_agent_ids`  (lines 62–69)

```
async def _granted_agent_ids(store: ScopedStore, email: str) -> frozenset[UUID]
```

**Purpose**: Finds every agent that a particular email address has been explicitly granted in the web portal. It answers the member-facing question: “Which private agents has this person been given access to?”

**Data flow**: It receives a scoped store and an email address. It normalizes the email, scans the stored audience rows, keeps the agent ids whose stored email matches, and returns those ids as a frozen set.

**Call relations**: web_audience calls this while building a signed-in member’s portal view. The result is combined with workspace-visible agents and owned agents to decide the member’s allowed agent list.

*Call graph*: calls 1 internal fn (list); called by 1 (web_audience); 1 external calls (UUID).


##### `WebAudience.allows`  (lines 86–87)

```
def allows(self, agent_id: UUID) -> bool
```

**Purpose**: Checks whether this web audience includes a specific agent for normal access. It is a quick yes-or-no test used before the portal lets someone open or use an agent.

**Data flow**: It receives an agent UUID. It looks through the WebAudience object's allowed agents and returns true if any agent has that id, otherwise false.

**Call relations**: Web surface routing uses this when resolving existing chats, new chats, and chat targets. In that larger flow, WebAudience is the permission snapshot, and allows is the gate at the door.

*Call graph*: called by 3 (_existing_chat_target, _new_chat_target, _resolve_chat).


##### `WebAudience.allows_chat`  (lines 89–90)

```
def allows_chat(self, agent_id: UUID) -> bool
```

**Purpose**: Checks whether a member may chat with a specific agent, including special cases where a private extension conversation opens chat access. This is broader than plain agent access.

**Data flow**: It receives an agent UUID. It checks the combined chat_agents list and returns true when the id appears there, otherwise false.

**Call relations**: It relies on the chat_agents property to include both ordinary allowed agents and conversation-based agents. This lets chat permission follow the same WebAudience object without duplicating the combining logic.


##### `WebAudience.chat_agents`  (lines 93–94)

```
def chat_agents(self) -> tuple[AgentSummary, ...]
```

**Purpose**: Returns the full set of agents this member can chat with. It adds ordinary allowed agents together with agents reached through private extension conversations.

**Data flow**: It reads the WebAudience object's agents and conversation_agents fields. It returns a new tuple containing both groups in order.

**Call relations**: allows_chat uses this property as its source of truth. The property keeps the special chat-only expansion in one place instead of spreading that rule through the web surface.


##### `web_audience`  (lines 97–127)

```
async def web_audience(surface: SurfaceContext, extension: ExtensionContext, email: str) -> WebAudience
```

**Purpose**: Builds the complete web access view for one email address in one workspace. This is the main function that decides what the signed-in portal user can see.

**Data flow**: It receives a surface context, an extension context, and an email. It normalizes the email, reads the workspace seat list, finds the matching seated member, lists agents, reads explicit grants, checks extension conversation agent ids, and returns a WebAudience. If the email is not a seated member, it returns an empty audience.

**Call relations**: This is the central reader for portal permissions. It calls _granted_agent_ids to include explicit grants, asks the surface for agents and member conversation links, and packages the result into WebAudience so the web routes can later ask simple questions like “is this agent allowed?”

*Call graph*: calls 4 internal fn (transaction, list_agents, member_extension_agent_ids, _granted_agent_ids); 2 external calls (__init__, __init__).


##### `_refusal`  (lines 137–138)

```
def _refusal(text: str) -> ToolResult
```

**Purpose**: Creates a standard error result for a tool action that is not allowed. It keeps refusals consistent and user-readable.

**Data flow**: It receives a text message. It wraps that message in TextContent, marks the ToolResult as an error, and returns it.

**Call relations**: _gate and _read_private_transcript use this whenever a member is missing, is not an admin, or is asking for something unsafe. It is the common way permission checks turn into a clear response.

*Call graph*: called by 2 (_gate, _read_private_transcript); 2 external calls (__init__, __init__).


##### `_target_agent`  (lines 141–148)

```
def _target_agent(ctx: ToolContext) -> tuple[UUID, str]
```

**Purpose**: Figures out which agent a web access action applies to. If the action explicitly named an agent, it uses that; otherwise it falls back to the agent currently running the turn.

**Data flow**: It receives the tool context. It checks the target information, returns the chosen agent UUID and a human-friendly label, and raises an error if the action was dispatched without the expected target structure.

**Call relations**: Grant, revoke, and private transcript actions all call this after their basic checks. It gives them one shared interpretation of “the target agent,” so the access row and the user-facing message refer to the same agent.

*Call graph*: called by 3 (_grant, _read_private_transcript, _revoke).


##### `_gate`  (lines 151–166)

```
async def _gate(ctx: ToolContext, extension: ExtensionContext) -> ToolResult | SeatEntry
```

**Purpose**: Performs the shared security checks for changing web access. It makes sure the caller is a speaking workspace member, is an admin, and is acting on a real workspace member.

**Data flow**: It receives a tool context and extension context. It checks who is speaking, asks whether they are an admin, extracts the target member id, reads the workspace seat snapshot, and returns either the matching SeatEntry or an error ToolResult explaining why the action is refused.

**Call relations**: _grant and _revoke both call this before touching stored grants. This makes the admin-only rule live in one place, so the two actions cannot drift apart in their security behavior.

*Call graph*: calls 3 internal fn (transaction, speaker_is_admin, _refusal); called by 2 (_grant, _revoke); 2 external calls (__init__, UUID).


##### `_grant`  (lines 169–186)

```
async def _grant(ctx: ToolContext, args: WebAccessInput) -> ToolResult
```

**Purpose**: Implements the admin tool that gives a workspace member web access to an agent. It writes the grant row that later lets web_audience include that agent for the member’s email.

**Data flow**: It receives the tool context and an empty input model. It checks that an extension context exists, runs _gate, chooses the target agent, skips unnecessary grants for the main agent when appropriate, normalizes the target member’s email, writes the grant record, and returns a confirmation message.

**Call relations**: This is the handler behind the grant_web_access tool definition. It depends on _gate for permission and member lookup, _target_agent for deciding the agent, and _grant_key for writing the same key format that audience reads later.

*Call graph*: calls 4 internal fn (agent_is_main, _gate, _grant_key, _target_agent); 2 external calls (__init__, __init__).


##### `_revoke`  (lines 189–209)

```
async def _revoke(ctx: ToolContext, args: WebAccessInput) -> ToolResult
```

**Purpose**: Implements the admin tool that removes a member’s explicit web access grant for an agent. It deletes the grant row, while also explaining cases where access remains for other reasons, such as the main agent being visible to every member.

**Data flow**: It receives the tool context and an empty input model. It checks for an extension context, runs _gate, chooses the target agent, normalizes the member email, deletes the matching grant key, and returns a message about the result.

**Call relations**: This is the handler behind the revoke_web_access tool definition. It mirrors _grant: both pass through _gate, both use _target_agent, and both use _grant_key so deletion targets the exact row that granting created.

*Call graph*: calls 4 internal fn (agent_is_main, _gate, _grant_key, _target_agent); 2 external calls (__init__, __init__).


##### `_read_private_transcript`  (lines 220–256)

```
async def _read_private_transcript(ctx: ToolContext, args: PrivateTranscriptInput) -> ToolResult
```

**Purpose**: Records an admin’s acknowledgement before opening another member’s private conversation transcript in the web portal. This creates an audit record, so private transcript access is visible and accountable.

**Data flow**: It receives the tool context and an empty input model. It verifies there is a speaking member, checks that the speaker is an admin, refuses requests from a foreign shared audience, extracts the target conversation id and agent id, asks the surface layer to record transcript access, and returns either a refusal or a confirmation naming whose transcript was opened.

**Call relations**: This is the handler behind the read_private_transcript tool definition. It uses _refusal for all blocked cases, _target_agent to bind the acknowledgement to the right agent, and record_transcript_access to create the actual audit entry that the portal’s content gate can rely on.

*Call graph*: calls 3 internal fn (speaker_is_admin, _refusal, _target_agent); 4 external calls (__init__, __init__, record_transcript_access, UUID).


### Signed authentication tokens
These files provide signed-token primitives and specialized tokens for browser login, public surfaces, provider account grants, and sandbox ingress.

### `core/src/ufo/harness/auth/__init__.py`

`other` · `import/package discovery`

This is an empty Python package file. In Python projects, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. Here, it makes the `auth` folder part of the `ufo.harness` package tree, which likely contains code related to authentication: proving who a user or process is. Think of it like a label on a filing cabinet drawer: the label does not contain the documents, but it lets the rest of the system find that drawer by name. Because this file is empty, it does not run setup code, expose shortcut imports, or store shared authentication settings. Its main value is structural. Without it, depending on the Python version and packaging setup, imports that expect `ufo.harness.auth` to be a normal package could fail or behave differently.


### `core/src/ufo/harness/auth/bearer.py`

`domain_logic` · `login, request authentication, and cross-cutting session checks`

This file is the shared rulebook for UFO member bearer tokens. A bearer token is like a signed wristband: whoever presents it can be treated as the person named on it, but only if the signature proves UFO made it and the expiry time has not passed.

The token contains three pieces of information: the workspace id, the member email address, and an expiry time. The contents are turned into compact JSON, encoded with URL-safe base64, then signed with HMAC-SHA256. HMAC is a way to make a tamper-evident signature using a shared secret. If anyone changes even one character in the token body, the signature check fails.

The signing secret comes from the `UFO_TOKEN_SECRET` environment variable. That matters because extensions and browser-facing surfaces can submit tokens for checking without ever receiving the secret key themselves.

The file also defines fixed web paths and cookie names used around login and logout, such as the session cookie name and login/logout routes. Without this file, different token issuers and checkers could drift apart in format, expiry rules, or workspace checks, causing valid users to be rejected or forged tokens to be accepted.

#### Function details

##### `mint_token`  (lines 37–54)

```
def mint_token(secret: str, workspace_id: str, email: str, ttl: timedelta, now: datetime | None=None) -> str
```

**Purpose**: Creates a signed bearer token for one workspace and one email address. It is used when the system needs to issue proof that a member is allowed in for a limited time.

**Data flow**: It receives a secret key, a workspace id, an email address, a time-to-live, and optionally a fixed current time. It trims and lowercases the email, calculates an expiry timestamp, writes the claim as compact JSON, base64-encodes that JSON, signs the encoded body with HMAC-SHA256, and returns one token string made from the body plus the signature. If the secret is missing, it raises an error instead of creating an unsafe token.

**Call relations**: This is the issuing side of the same format that `verified_claims` later checks. It relies on standard JSON, base64, time, and HMAC tools to produce a token that other UFO surfaces can verify without needing a database lookup.

*Call graph*: 4 external calls (urlsafe_b64encode, now, new, dumps).


##### `verified_claims`  (lines 57–80)

```
def verified_claims(token: str, now: int | None=None) -> tuple[str, str] | None
```

**Purpose**: Checks whether a bearer token is genuine and still valid, then returns the workspace and email it proves. If anything looks wrong, it returns nothing instead of trusting partial information.

**Data flow**: It takes a token string and optionally a current timestamp. It reads the signing secret from the environment, splits the token into body and signature, recomputes the expected signature, compares signatures in a timing-safe way, decodes the body, parses the JSON, checks that the needed fields have the right types, and rejects expired tokens. On success it returns the workspace id and email as a pair; on failure it returns `None`.

**Call relations**: `verify_token` and `workspace_claim` both call this first because no workspace or email should be trusted until the signature and expiry are proven. It hands base64 decoding to `_b64url_decode` and secret lookup to `_secret`, keeping the main verification flow focused on deciding whether the token is safe to use.

*Call graph*: calls 2 internal fn (_b64url_decode, _secret); called by 2 (verify_token, workspace_claim); 4 external calls (now, compare_digest, new, loads).


##### `verify_token`  (lines 83–94)

```
def verify_token(token: str, workspace_id: UUID, now: int | None=None) -> str | None
```

**Purpose**: Authenticates a token for one specific workspace and returns the member email if it matches. This prevents a token for one workspace from being reused in another workspace.

**Data flow**: It receives a token, the workspace id this process expects, and optionally a current timestamp. It asks `verified_claims` to prove the token first. If verification fails, or if the token’s workspace claim does not equal the expected workspace id, it returns `None`. If everything matches, it returns the email address in lowercase.

**Call relations**: This function sits one step above general token verification. After `verified_claims` proves the token is real, `verify_token` adds the tenant check: it makes sure the token belongs to this particular workspace before giving the caller an authenticated email.

*Call graph*: calls 1 internal fn (verified_claims).


##### `workspace_claim`  (lines 97–108)

```
def workspace_claim(token: str, now: int | None=None) -> UUID | None
```

**Purpose**: Extracts the workspace id from a valid token when the process is serving many workspaces and cannot compare against one fixed workspace. It still refuses forged, expired, or malformed tokens.

**Data flow**: It receives a token and optionally a current timestamp. It first asks `verified_claims` to prove the signature and expiry. If that succeeds, it tries to turn the workspace string into a real UUID object. It returns that UUID on success, or `None` if the token is invalid or the workspace value is not a valid UUID.

**Call relations**: This is the shared-fleet version of workspace selection. It depends on `verified_claims` for trust, then converts the trusted workspace text into a UUID that the rest of the system can use as a clean workspace identifier.

*Call graph*: calls 1 internal fn (verified_claims); 1 external calls (UUID).


##### `_secret`  (lines 111–115)

```
def _secret() -> str
```

**Purpose**: Fetches the token signing secret from the environment. It makes verification fail loudly if the server was started without the secret needed to check bearer tokens.

**Data flow**: It reads the `UFO_TOKEN_SECRET` environment variable. If a value is present, it returns that string. If it is missing or empty, it raises a runtime error explaining that the secret must be set.

**Call relations**: `verified_claims` calls this before checking a token signature. This keeps the secret lookup in one place, so token verification always uses the same configured key.

*Call graph*: called by 1 (verified_claims).


##### `_b64url_decode`  (lines 118–119)

```
def _b64url_decode(value: str) -> bytes
```

**Purpose**: Decodes the URL-safe base64 body used inside the token. It restores any omitted padding so standard base64 decoding can read it.

**Data flow**: It receives the encoded token body as text. It adds the right number of `=` padding characters, decodes the URL-safe base64 text into bytes, and returns those bytes for JSON parsing.

**Call relations**: `verified_claims` calls this after the signature check has passed, so it can read the token payload. This helper hides the small base64 padding detail from the main verification logic.

*Call graph*: called by 1 (verified_claims); 1 external calls (urlsafe_b64decode).


### `core/src/ufo/harness/auth/surface_token.py`

`domain_logic` · `link generation and request handling`

Some routes are reached through a plain link, before the system has a user session cookie or any other usual way to know what workspace the request belongs to. This file solves that problem by putting the needed route claims into a signed token that can travel in a URL. Think of it like a tamper-evident label on a package: anyone can carry it, but the system can tell if someone changed the label.

The token is signed with a shared secret from the environment. The signing uses HMAC, which means a short proof made with a secret key; someone without that key cannot make a valid altered token. The file never decides whether a request is allowed to do something. It only proves that the claims inside the token were minted by this deployment and were meant for this exact surface.

A special claim named "surface" is added when the token is made. Later, verification requires that claim to match the surface currently being visited. This prevents a token created for one public route from being reused at another route. All other claim keys and values must be strings, because the token is meant to cross URLs cleanly. If the token is malformed, forged, signed with the wrong secret, for the wrong surface, or contains non-string claims, verification simply returns no claims.

#### Function details

##### `mint_surface_token`  (lines 24–35)

```
def mint_surface_token(surface: str, payload: Mapping[str, str]) -> str
```

**Purpose**: Creates a signed surface token from a surface name and a set of string claims. It is used when the system needs to build a link that can later identify the right surface context without relying on cookies.

**Data flow**: It receives a surface name and a mapping of claim names to claim values. It first rejects an empty surface name and rejects any caller-supplied claim named "surface", because that name is reserved for the file's own safety marker. It then adds the real surface name, turns the claims into compact JSON text, reads the signing secret, and passes the bytes to the shared token-signing helper. The result is an opaque signed string suitable for placing in a URL.

**Call relations**: When a surface link is being minted, this function is the front door. It asks _secret for the deployment's signing secret, uses JSON encoding to make the claims stable text, and hands that text to sign_token so the token can later be checked for tampering.

*Call graph*: calls 1 internal fn (_secret); 2 external calls (dumps, sign_token).


##### `verify_surface_token`  (lines 38–51)

```
def verify_surface_token(surface: str, token: str) -> dict[str, str] | None
```

**Purpose**: Checks whether a token is valid for a particular surface and, if so, returns the trusted claims inside it. It is used when a request arrives through a token-bearing link and the route needs safe information before anything else is loaded.

**Data flow**: It receives the expected surface name and the token string from the request. It reads the shared secret, asks the token verification helper to prove the signature, and parses the verified bytes as JSON. If the signature is bad, the JSON is invalid, the payload is not a dictionary, or the embedded surface name does not match the expected one, it returns None. It removes the reserved "surface" field, checks that every remaining key and value is a string, and returns those claims as a dictionary.

**Call relations**: During request handling, this function is the matching half of mint_surface_token. It calls _secret to use the same deployment secret, hands the token to verify_token to reject forged or changed tokens, and then uses JSON parsing to recover the original claims only after the signature has been trusted.

*Call graph*: calls 1 internal fn (_secret); 2 external calls (loads, verify_token).


##### `_secret`  (lines 54–58)

```
def _secret() -> str
```

**Purpose**: Fetches the shared token secret from the process environment. This keeps surface tokens tied to the deployment's configured secret instead of hard-coding a key in the source code.

**Data flow**: It reads the environment variable named by UFO_TOKEN_SECRET_ENV. If the variable exists and is not empty, it returns that string. If it is missing or empty, it raises a runtime error so the system fails loudly instead of creating or checking tokens with no real secret.

**Call relations**: Both mint_surface_token and verify_surface_token call this helper before signing or checking a token. It is the common doorway to the secret, which keeps token creation and token verification using the same configuration.

*Call graph*: called by 2 (mint_surface_token, verify_surface_token).


### `core/src/ufo/harness/auth/token_signing.py`

`domain_logic` · `cross-cutting auth during token creation and request handling`

This file is a compact toolkit for making “tamper-evident” tokens. A token here is made from two parts: a payload turned into URL-safe text, and a signature that proves the payload has not been altered. The signature uses HMAC, which is a standard way to combine a secret key with a message to produce a fingerprint. Think of it like sealing an envelope with a special wax stamp: anyone can see the envelope, but only someone with the right stamp can make a matching seal.

The payload is encoded with base64url, a text format that is safe to place in URLs because it avoids characters that commonly cause trouble there. The token format is simple: encoded payload, a dot, then the signature. When reading a token back, the file first checks that the token has both pieces. It then recomputes the expected signature using the same secret and compares it safely. If anything is missing, mismatched, or unreadable, it raises SignedTokenError, a clear signal that the token should not be trusted.

This matters anywhere the system passes compact authentication or authorization data around without storing it server-side. Without this file, callers would either need to trust user-supplied token text blindly, which is unsafe, or reimplement signing and checking in scattered places.

#### Function details

##### `sign_detached`  (lines 12–14)

```
def sign_detached(secret: bytes, message: bytes) -> str
```

**Purpose**: Creates a signature for a message using a shared secret. The signature is “detached” because it is returned by itself, not bundled together with the message.

**Data flow**: It receives a secret as bytes and a message as bytes. It uses HMAC with SHA-256, a common secure fingerprinting method, to make a binary digest, then turns that digest into URL-safe text and removes padding characters. It returns that signature string and does not change any outside state.

**Call relations**: When a full token is being built, sign_token calls this to stamp the encoded payload. When a signature is being checked, verify_detached calls this again to recreate the expected stamp from the same message and secret.

*Call graph*: called by 2 (sign_token, verify_detached); 2 external calls (urlsafe_b64encode, new).


##### `verify_detached`  (lines 17–18)

```
def verify_detached(secret: bytes, message: bytes, signature: str) -> bool
```

**Purpose**: Checks whether a provided signature really matches a message and secret. This is the low-level yes-or-no test used before trusting token contents.

**Data flow**: It receives the secret, the original message bytes, and a signature string to test. It recreates the correct signature with sign_detached, then compares the two signatures using a safe comparison designed for secrets. It returns true if they match and false if they do not.

**Call relations**: verify_token uses this after splitting a token into its body and signature. This function delegates the signature-making part to sign_detached, then gives verify_token a simple pass-or-fail answer.

*Call graph*: calls 1 internal fn (sign_detached); called by 1 (verify_token); 1 external calls (compare_digest).


##### `sign_token`  (lines 21–23)

```
def sign_token(secret: bytes, payload: bytes) -> str
```

**Purpose**: Builds a complete signed token from raw payload bytes. Someone would use it when they want to hand out token text that can later be checked for tampering.

**Data flow**: It receives a secret and a payload. First it converts the payload into URL-safe base64 text, then it asks sign_detached to sign that text. It returns one string in the form payload-text.signature.

**Call relations**: This is the outward-facing creation step in this file. It prepares the token body itself, then relies on sign_detached for the cryptographic stamp that verify_token will later expect.

*Call graph*: calls 1 internal fn (sign_detached); 1 external calls (urlsafe_b64encode).


##### `verify_token`  (lines 26–35)

```
def verify_token(token: str, secret: bytes) -> bytes
```

**Purpose**: Checks a full signed token and returns the original payload if the token is valid. If the token is malformed, altered, or not readable, it raises SignedTokenError instead of returning untrusted data.

**Data flow**: It receives token text and the shared secret. It splits the token at the dot into an encoded body and a signature, rejects the token if either piece is missing, and asks verify_detached whether the signature matches the body. If the signature is valid, it decodes the body back into the original payload bytes and returns them. If decoding fails, it reports that the payload is unreadable.

**Call relations**: This is the main reading and trust-checking step. Callers hand it token text from outside the trusted boundary, such as a request or stored value; it uses verify_detached to decide whether the token can be trusted before handing the payload back.

*Call graph*: calls 1 internal fn (verify_detached); 2 external calls (__init__, b64decode).


### `core/src/ufo/harness/models/grant.py`

`domain_logic` · `credential use and refresh during request handling`

A connected provider account is more like a rechargeable pass than a permanent key. The access token is what API calls spend right now, but it expires. The refresh token is what buys the next access token. This file keeps those pieces together so the system does not think an account is still connected after its usable token has died.

The main model is Grant. It stores the current access token, the refresh token, the expiry time, and a short “refresh claim” window. That claim matters because many parts of the system might notice an old token at the same time. Since refresh tokens may be single-use, only one caller should spend it.

The file also defines which OAuth client identity and token endpoint to use for OpenAI and Anthropic. OAuth is the standard web flow where an app receives tokens from a provider after a user connects an account. A deploy can provide its own client id through environment variables, or fall back to public defaults.

When a token endpoint replies, granted checks that the response includes every needed piece. read_grant separates stored JSON grants from plain API keys. refreshed performs the actual network call to trade a refresh token for a new grant, and raises GrantRefusedRefresh when the provider says no or cannot be reached.

#### Function details

##### `openai_client_id`  (lines 36–39)

```
def openai_client_id() -> str
```

**Purpose**: This chooses the OpenAI OAuth client id that this deployment should present when asking OpenAI for tokens. It lets a deployment use its own configured client id, while still having a public default when none is set.

**Data flow**: It reads the UFO_OPENAI_OAUTH_CLIENT_ID environment variable. If that value exists, it returns it; otherwise it returns the built-in public OpenAI client id.

**Call relations**: When refreshed needs to renew an OpenAI grant, it gets this function through the grant-client lookup table and calls it so the refresh request names the same kind of client used during sign-in.


##### `anthropic_client_id`  (lines 42–45)

```
def anthropic_client_id() -> str
```

**Purpose**: This chooses the Anthropic OAuth client id that this deployment should use when asking Anthropic for tokens. It supports both custom deployments and a built-in public default.

**Data flow**: It reads the UFO_ANTHROPIC_OAUTH_CLIENT_ID environment variable. If a value is present, that value comes out; if not, the built-in Anthropic public client id comes out.

**Call relations**: When refreshed renews an Anthropic grant, it reaches this function through the grant-client lookup table and uses its return value in the token refresh request.


##### `GrantRefusedRefresh.__init__`  (lines 71–73)

```
def __init__(self, slot: str) -> None
```

**Purpose**: This builds the error used when a connected account cannot be refreshed. It records which provider slot failed so higher-level code can tell the user which account needs reconnecting.

**Data flow**: It receives a slot name, such as the OpenAI or Anthropic credential slot. It creates a clear error message and stores the slot on the exception object for later use.

**Call relations**: refreshed raises this error when the provider cannot be reached, rejects the refresh token, or returns an unusable response. Workspace credential-refresh code can also create the same error when it decides a refresh cannot continue.

*Call graph*: called by 2 (refreshed, _refreshed_credential).


##### `Grant.spent`  (lines 88–89)

```
def spent(self) -> bool
```

**Purpose**: This tells whether a grant should be treated as used up soon enough that it needs refreshing now. It does not wait until the exact expiry moment, because a long provider call might otherwise start successfully and fail halfway through.

**Data flow**: It reads the grant’s expiry time and the current clock time. If the current time is within the safety margin before expiry, it returns true; otherwise it returns false.

**Call relations**: Code that is about to use a credential can check this property before spending the access token. It relies on the system clock to make that decision.

*Call graph*: 1 external calls (time).


##### `Grant.claimed`  (lines 92–93)

```
def claimed(self) -> bool
```

**Purpose**: This tells whether another caller has already claimed the right to refresh this grant. It helps stop two tasks from spending the same refresh token at once.

**Data flow**: It reads the grant’s refreshing_until timestamp and compares it with the current clock time. If the current time is still before that timestamp, it returns true; otherwise it returns false.

**Call relations**: Credential-refresh code can use this property before starting a refresh. The idea is like putting a sticky note on a shared coupon saying, “I’m using this right now,” so nobody else tries to redeem it at the same time.

*Call graph*: 1 external calls (time).


##### `Grant.stored`  (lines 95–96)

```
def stored(self) -> str
```

**Purpose**: This turns a Grant into the string form that can be saved in a credential slot. It preserves the whole renewable grant, not just the access token.

**Data flow**: It reads the Grant object’s fields and serializes them into JSON text. The returned string is ready to be stored wherever credentials are kept.

**Call relations**: After a grant is created or refreshed, other parts of the system can call this before writing it back to storage. It uses the model’s built-in JSON serialization rather than doing custom formatting here.


##### `granted`  (lines 99–112)

```
def granted(payload: dict[str, object]) -> Grant | None
```

**Purpose**: This checks a token endpoint response and turns it into a usable Grant. It refuses partial replies, because an access token without a refresh token would work briefly and then leave the account unable to renew.

**Data flow**: It receives a dictionary from a provider response. It looks for a non-empty access_token, a non-empty refresh_token, and a numeric expires_in value. If all are valid, it creates a Grant whose expiry time is the current time plus expires_in; otherwise it returns None.

**Call relations**: refreshed calls this after receiving JSON from OpenAI or Anthropic. This function is the gatekeeper that decides whether the provider’s reply is complete enough to store and use.

*Call graph*: called by 1 (refreshed); 2 external calls (__init__, time).


##### `read_grant`  (lines 115–127)

```
def read_grant(stored: str) -> Grant | None
```

**Purpose**: This tries to read a stored credential as a renewable Grant. If the stored value is just a plain API key or invalid JSON, it quietly returns None instead of treating it as a broken grant.

**Data flow**: It receives a stored string. It tries to parse the string as JSON, checks that the result is an object, and asks the Grant model to validate the fields. A valid grant object comes out; invalid or non-grant input becomes None.

**Call relations**: Credential-loading code can use this to decide whether a slot contains a renewable connected account or a plain key. It relies on JSON parsing and model validation to avoid guessing from raw text.

*Call graph*: 1 external calls (loads).


##### `refreshed`  (lines 130–155)

```
async def refreshed(grant: Grant, slot: str) -> Grant
```

**Purpose**: This renews an expired or nearly expired grant by trading its refresh token for a fresh grant from the provider. If anything about that exchange fails, it raises GrantRefusedRefresh so the system knows the member must reconnect the account.

**Data flow**: It receives the old Grant and the provider slot name. It looks up the correct token endpoint and client id function for that slot, sends an asynchronous HTTP POST request with the refresh token, then checks the provider response. A valid response becomes a new Grant; network errors, non-success status codes, unreadable JSON, or missing token fields become GrantRefusedRefresh.

**Call relations**: This is the main refresh step used when stored credentials are no longer safe to spend. It calls the appropriate client id helper through the grant-client table, uses httpx.AsyncClient to talk to the provider over HTTP, passes the response to granted for validation, and raises GrantRefusedRefresh when renewal cannot be completed.

*Call graph*: calls 2 internal fn (__init__, granted); 1 external calls (AsyncClient).


### `core/src/ufo/harness/sandbox/ingress_token.py`

`domain_logic` · `request handling`

A sandbox app may be running on an internal port, but the system cannot let anyone reach that port just because they know the address. This file acts like a stamped, time-limited visitor pass. The pass says which workspace, conversation, and port it is for, when it expires, and sometimes extra context such as a shipped app bundle or an enclosing frame site.

There are different token “kinds” for different moments in the visit. A view token is used when a browser first opens the special ingress path. A session token is later kept as that origin’s cookie. A report token is for reporting that a site could not be reached. The kind is included inside the signed data, so a token made for one job cannot be reused for another. That matters because, for example, a cookie should not be able to pretend it is a fresh view link.

The file signs tokens with one deployment-wide secret read from the environment. Verification reverses the process: check the signature, parse the JSON body, confirm the token kind, rebuild the claims, reject impossible port numbers, and reject tokens whose expiry time has passed. Without this file, sandbox ingress would either have no trustworthy way to grant temporary access, or each caller would have to duplicate fragile security checks.

#### Function details

##### `mint_ingress_token`  (lines 81–98)

```
def mint_ingress_token(claims: IngressClaims, kind: IngressTokenKind) -> str
```

**Purpose**: This function turns trusted ingress claims into a signed token string that can be put in a link, cookie, or report. The token includes its exact purpose, so it can only be accepted at the matching step later.

**Data flow**: It receives an IngressClaims object and a token kind. It copies the workspace ID, conversation ID, port, expiry time, and optional shipped or framer details into a small JSON-shaped body, reads the shared ingress secret, and signs that body. The result is an opaque string that callers can safely send to a browser or another service because later tampering will be detected.

**Call relations**: When some other part of the sandbox needs to grant temporary ingress access, it calls this function. This function asks ingress_secret for the deployment secret, uses JSON conversion to make a stable body, then hands the bytes to the shared token-signing helper so verification can later prove the token came from this system.

*Call graph*: calls 1 internal fn (ingress_secret); 2 external calls (dumps, sign_token).


##### `verify_ingress_token`  (lines 101–140)

```
def verify_ingress_token(token: str, now: datetime, kind: IngressTokenKind) -> IngressClaims
```

**Purpose**: This function checks whether a presented token is genuine, unexpired, well formed, and meant for the exact ingress step being served. If everything checks out, it returns the trusted claims inside the token.

**Data flow**: It receives a token string, the current time, and the expected token kind. It reads the shared secret, verifies the signature, parses the JSON payload, checks that the kind matches, converts IDs and ports into typed values, and rebuilds the claims object. It rejects malformed data, bad signatures, wrong token kinds, invalid port numbers, and expired tokens by raising IngressTokenError; otherwise it outputs an IngressClaims object that the caller can trust.

**Call relations**: Ingress code calls this when a browser opens a tokenized view path, presents a session cookie, or sends a site report. The function relies on ingress_secret for the same secret used when minting, delegates cryptographic checking to the shared verify_token helper, and then constructs ShippedClaim, FramerClaim, and IngressClaims objects so the rest of ingress can decide what workspace and port may be served.

*Call graph*: calls 1 internal fn (ingress_secret); 8 external calls (__init__, __init__, __init__, __init__, timestamp, loads, verify_token, UUID).


##### `ingress_secret`  (lines 143–150)

```
def ingress_secret() -> str
```

**Purpose**: This function retrieves the deployment-wide secret used to sign and verify ingress tokens. It fails loudly if the secret is missing, because missing token security is a deployment error, not something to ignore.

**Data flow**: It reads the environment variable named by UFO_TOKEN_SECRET_ENV. If the value exists, it returns that secret string. If it is absent or empty, it raises a RuntimeError explaining that ingress tokens cannot be minted or verified without it.

**Call relations**: Both mint_ingress_token and verify_ingress_token call this before doing their security work. Keeping the lookup here makes both sides use the same configured secret, like requiring the same official stamp to issue and inspect a visitor pass.

*Call graph*: called by 2 (mint_ingress_token, verify_ingress_token).


### Credential and grant storage
These files store protected user secrets and connected-account grants while controlling when agents may use them.

### `core/src/ufo/runtime/access/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python projects, an `__init__.py` file tells Python that a folder should be treated as an importable package, like putting a label on a drawer so the rest of the program can find what is inside it. Here, the drawer is `ufo.runtime.access`. Even though this file contains no code, it still matters because it helps organize the project and makes imports from this part of the runtime possible or explicit. Any real access-related behavior lives in other files inside this package, not here. If this file were removed, package discovery or import behavior could change depending on the Python version and project setup.


### `core/src/ufo/runtime/access/credentials.py`

`domain_logic` · `cross-cutting credential request, storage, and proxy injection`

This file is the project’s safe deposit box for “bring your own key” credentials. A workspace may need a secret, such as an API token, but the system must not show that secret to the chat transcript, logs, or sandboxed code. Instead, the secret is encrypted before it is saved, and decrypted only inside the trusted process that injects it into outbound proxy traffic.

The file has three main jobs. First, it defines names for credential slots, which are the labeled places where secrets live. It also turns those names into safe object names used elsewhere in the system. Second, it creates and checks sealed credential requests. A sealed request is like a tamper-proof envelope: it says which workspace, member, and slot a prompt belongs to, and it expires quickly. This stops someone from reusing or editing an old prompt to write a different secret. Third, it reads, writes, updates, clears, and rotates encrypted credential values in the database.

There is also support for providers whose API host can vary by account. Instead of letting a user type any hostname, the code only accepts a choice from a declared list. That matters because the proxy may trust this host when deciding where secrets are allowed to go.

#### Function details

##### `deploy_env`  (lines 33–39)

```
def deploy_env(name: str) -> str | None
```

**Purpose**: Looks up a deployment-level secret from environment variables. It first checks a UFO-specific name, then falls back to the ordinary name, so the project can keep its own secret separate from other tools that might read the same environment.

**Data flow**: It receives a variable name. It checks the process environment for `UFO_<name>`, then for `<name>`, treating an empty value as missing. It returns the first usable string it finds, or `None` if neither exists.

**Call relations**: This is a small lookup helper used when the runtime needs secrets supplied by the deployment itself rather than by a workspace member. It does not call other project code; it reads directly from the process environment.


##### `credential_object_name`  (lines 42–45)

```
def credential_object_name(slot: str) -> str
```

**Purpose**: Turns a credential slot name into a safe, simple object name. This gives the rest of the system a predictable way to refer to a credential without using raw, possibly messy slot text.

**Data flow**: It receives a slot name, lowercases it, replaces runs of non-letter-or-number characters with hyphens, and trims extra hyphens from the ends. It returns the cleaned name.

**Call relations**: When `named_slots` needs public-facing names for declared slots, it calls this function first. This function delegates the text replacement to Python’s regular expression tool.

*Call graph*: called by 1 (named_slots); 1 external calls (sub).


##### `member_slot`  (lines 51–55)

```
def member_slot(slot: str, member_id: UUID) -> str
```

**Purpose**: Builds the storage key for a credential value that belongs to one specific member rather than the whole workspace. This lets the same base slot have separate per-person values.

**Data flow**: It receives a base slot name and a member ID. It combines them with a special marker string and returns the resulting slot key.

**Call relations**: This helper is available to callers that need to store or retrieve a member-specific credential in the same encrypted credential table as workspace-wide credentials.


##### `named_slots`  (lines 58–73)

```
def named_slots(slots: 'tuple[DeclaredSlot, ...]') -> 'dict[str, DeclaredSlot]'
```

**Purpose**: Assigns stable object names to all declared credential slots. If two slots would produce the same simple name, it adds a short digest so both can still be addressed safely.

**Data flow**: It receives declared slot objects. It groups them by their cleaned credential object name. Single slots keep the plain name; colliding slots get the plain name plus a short hash based on the extension and slot name. It returns a dictionary from final object name to the declared slot.

**Call relations**: This function calls `credential_object_name` to get the human-friendly base name. When there is a collision, it uses SHA-256 hashing to make a stable short qualifier, so other parts of the system can refer to the same credential consistently.

*Call graph*: calls 1 internal fn (credential_object_name); 1 external calls (sha256).


##### `seal_credential_request`  (lines 102–103)

```
def seal_credential_request(fernet: Fernet, state: CredentialRequestState) -> str
```

**Purpose**: Turns a credential request state into an encrypted, tamper-proof string. Callers use this when they need to hand a private credential prompt to a user-facing surface without letting anyone edit its claims.

**Data flow**: It receives a Fernet encryption object and a `CredentialRequestState`. It converts the state to JSON bytes, encrypts those bytes, and returns the encrypted token as text.

**Call relations**: `CredentialRequests.seal` and `CredentialRequests.authorize` call this after they have built the state they want to protect. It relies on the state model’s JSON conversion and Fernet encryption.

*Call graph*: called by 2 (authorize, seal); 2 external calls (model_dump_json, encrypt).


##### `open_credential_request`  (lines 106–120)

```
def open_credential_request(fernet: Fernet, sealed: str, *, ttl: int=CREDENTIAL_REQUEST_TTL_SECONDS) -> CredentialRequestState
```

**Purpose**: Opens and checks a sealed credential request. It turns bad, expired, or malformed tokens into one clear project-specific error so callers do not have to handle low-level encryption or validation failures themselves.

**Data flow**: It receives a Fernet encryption object, a sealed token string, and an optional lifetime limit. It decrypts the token, checks that it has not expired, parses the JSON into a request state, and returns that state. If anything is wrong, it raises `CredentialRequestInvalid`.

**Call relations**: `CredentialRequests.open_authorization` calls this when it needs to verify a sealed authorization state. This function sits at the boundary between encrypted text from outside and trusted structured data inside the process.

*Call graph*: called by 1 (open_authorization); 2 external calls (__init__, decrypt).


##### `CredentialRequests.seal`  (lines 132–145)

```
def seal(self, workspace_id: UUID, member_id: UUID, slots: tuple[str, ...]) -> str
```

**Purpose**: Creates a short-lived sealed prompt for one member to fill one or more declared credential slots. It refuses slots that no installed extension declared, which prevents prompts for unknown secrets.

**Data flow**: It receives a workspace ID, member ID, and slot names. It checks the slots against the declared set, adds a fresh request ID and current timestamp, encrypts the state, and returns the sealed token string.

**Call relations**: A credential request flow calls this before showing a private prompt to a member. It hands the actual encryption work to `seal_credential_request`, after creating the `CredentialRequestState` with a new UUID and timestamp.

*Call graph*: calls 1 internal fn (seal_credential_request); 3 external calls (__init__, time, uuid4).


##### `CredentialRequests.authorize`  (lines 147–160)

```
def authorize(self, workspace_id: UUID, member_id: UUID, slot: str, payload: str) -> str
```

**Purpose**: Creates a sealed token for an external provider authorization step, such as an OAuth-style flow. The token carries provider state privately and ties it to exactly one workspace, member, and credential slot.

**Data flow**: It receives the workspace ID, member ID, slot name, and provider payload. It checks that the slot is declared and the payload is not empty, then seals those details into encrypted text and returns it.

**Call relations**: This is used when credential fulfillment needs an intermediate provider authorization state instead of just a direct secret entry. It builds a `CredentialRequestState` and passes it to `seal_credential_request`.

*Call graph*: calls 1 internal fn (seal_credential_request); 1 external calls (__init__).


##### `CredentialRequests.open_authorization`  (lines 162–176)

```
def open_authorization(self, sealed: str, workspace_id: UUID, member_id: UUID, slot: str) -> str
```

**Purpose**: Verifies a sealed provider authorization token and extracts its private payload. It makes sure the token belongs to the exact workspace, member, and slot the caller expects.

**Data flow**: It receives sealed text plus the expected workspace ID, member ID, and slot. It opens the sealed request, compares every important claim, checks that the slot is still declared, and returns the payload. If the token is wrong, missing its payload, or meant for something else, it raises an error.

**Call relations**: This function is called after a provider authorization step returns and the system needs to continue safely. It depends on `open_credential_request` to decrypt and parse the token, then performs the project-specific claim checks.

*Call graph*: calls 1 internal fn (open_credential_request); 1 external calls (__init__).


##### `CredentialStore.put`  (lines 183–205)

```
async def put(self, workspace_id: UUID, slot: str, plaintext: str) -> None
```

**Purpose**: Stores a plaintext credential value in encrypted form for a workspace and slot. It is the direct “save this secret” operation for trusted callers.

**Data flow**: It receives a workspace ID, slot name, and plaintext secret. It rejects an empty secret, encrypts the text, opens a database transaction, and either updates the existing credential row or inserts a new one. It returns nothing, but the database now contains the encrypted value.

**Call relations**: This method is part of `CredentialStore`, the main database-backed safe storage object. It uses `workspace_tx` to write inside a transaction and SQLAlchemy statements to update or insert the credential row.

*Call graph*: 3 external calls (insert, update, workspace_tx).


##### `CredentialStore.fulfill`  (lines 207–294)

```
async def fulfill(self, workspace_id: UUID, slot: str, submitted: str, request_id: UUID | None, member_id: UUID, merge: Callable[[str | None, str], str] | None) -> None
```

**Purpose**: Writes a credential submitted through a sealed member prompt, while checking that the member is allowed to do it and that the same request is not fulfilled twice. This is the guarded path for secrets coming from a private prompt.

**Data flow**: It receives the workspace, slot, submitted value, optional request ID, member ID, and an optional merge function. It rejects empty submissions, locks the workspace row, confirms the member is a seated admin, optionally claims the request ID so it cannot be reused, decrypts the current value if one exists, merges or replaces it, encrypts the result, and inserts or updates the database row.

**Call relations**: This is used after a user-facing surface has collected a secret outside the transcript. It coordinates database locking, authority checks, one-time request claiming, optional merge logic, and encrypted storage. It raises `CredentialRequestInvalid` when the request is not allowed or was already used.

*Call graph*: 5 external calls (__init__, insert, select, update, workspace_tx).


##### `CredentialStore.clear`  (lines 296–305)

```
async def clear(self, workspace_id: UUID, slot: str) -> None
```

**Purpose**: Deletes one stored credential slot for a workspace. Calling it is safe even when the slot is already empty, which makes disconnect or cleanup actions simple.

**Data flow**: It receives a workspace ID and slot name. It opens a transaction and deletes any matching credential row. It returns nothing, and after it runs there is no stored value for that slot.

**Call relations**: This method is called by flows that need to remove a credential, such as disconnecting an account. It uses `workspace_tx` for the database transaction and a SQL delete statement for the actual removal.

*Call graph*: 2 external calls (delete, workspace_tx).


##### `CredentialStore.update`  (lines 307–354)

```
async def update(self, workspace_id: UUID, slot: str, submitted: str, merge: Callable[[str | None, str], str]) -> None
```

**Purpose**: Merges a private submitted value into an existing encrypted credential slot. This is useful when a credential is structured and one submission should update part of it rather than replacing the whole thing blindly.

**Data flow**: It receives a workspace ID, slot, submitted text, and merge function. It rejects empty submitted text, locks the workspace row, reads and decrypts the current credential if present, asks the merge function to produce the new plaintext, rejects an empty result, encrypts the new plaintext, and inserts or updates the credential row.

**Call relations**: This method is a safer update path for credentials that need custom combining logic. Like `fulfill`, it uses `workspace_tx` and SQLAlchemy, but it does not perform the sealed-request admin and one-time-claim checks.

*Call graph*: 4 external calls (insert, select, update, workspace_tx).


##### `CredentialStore.get`  (lines 356–368)

```
async def get(self, workspace_id: UUID, slot: str) -> str
```

**Purpose**: Reads and decrypts a stored credential value. Callers use this only in trusted parts of the system that need the real secret, such as preparing proxy injection or building a sandbox environment with sentinel values.

**Data flow**: It receives a workspace ID and slot name. It looks up the encrypted database row inside a transaction. If no row exists, it raises `CredentialSlotUnset`; otherwise it decrypts the ciphertext and returns the plaintext string.

**Call relations**: This is called by sandbox environment setup, egress rule derivation, and `credential_host`. It is the main read path from encrypted storage back into trusted runtime code.

*Call graph*: called by 3 (_keyed_provider_env, credential_host, derive_credential_rules); 3 external calls (__init__, select, workspace_tx).


##### `CredentialStore.rotate`  (lines 370–401)

```
async def rotate(self, workspace_id: UUID, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Replaces a stored credential only if it still has the expected current value. This prevents two concurrent refreshes, such as OAuth token refreshes, from overwriting each other in the wrong order.

**Data flow**: It receives a workspace ID, slot, expected old plaintext, and new plaintext. It rejects an empty new value, reads and decrypts the current stored value, and returns `False` if the slot is missing or no longer matches the expected value. If it matches, it writes the encrypted new value, but only while the stored ciphertext is still the same row it read. It returns `True` only when exactly one row was updated.

**Call relations**: OAuth-style clients use this after refreshing an existing credential. The first credential still enters through the member fulfillment path; this method is for later safe replacement without clobbering a newer token.

*Call graph*: 3 external calls (select, update, workspace_tx).


##### `HostChoice.__post_init__`  (lines 425–430)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that a host choice declaration is internally consistent. In particular, the default host must be one of the allowed hosts.

**Data flow**: After a `HostChoice` object is created, it reads its default value and allowed host list. If the default is not in that list, it raises `ValueError`; otherwise the object remains valid.

**Call relations**: This runs automatically when a `HostChoice` is constructed. It protects later code, such as `credential_host`, from relying on a declaration whose fallback host is not actually allowed.


##### `HostChoice.resolve`  (lines 432–437)

```
def resolve(self, selected: str) -> str | None
```

**Purpose**: Converts a stored host selection into the exact declared host string, or rejects it by returning `None`. This keeps user input from becoming an arbitrary network destination.

**Data flow**: It receives the selected text, trims spaces, lowercases it for comparison, and searches the declared host list case-insensitively. If it finds a match, it returns the canonical declared spelling; otherwise it returns `None`.

**Call relations**: `credential_host` uses this when a workspace has stored a host choice. The important handoff is that this function returns only a host literal already written in code, not a free-form hostname supplied by a member.


##### `credential_host`  (lines 440–457)

```
async def credential_host(store: CredentialStore, workspace_id: UUID, host: str | HostChoice) -> str | None
```

**Purpose**: Resolves the API host that a credential is allowed to use for a workspace. It supports both fixed hosts and account-specific host choices with a safe default.

**Data flow**: It receives a credential store, workspace ID, and either a plain host string or a `HostChoice`. If the host is already a string, it returns it. If it is a `HostChoice`, it tries to read the workspace’s stored selection; if none exists, it returns the declared default. If a stored selection exists but is not one of the declared choices, it returns `None`.

**Call relations**: The egress proxy and sandbox environment setup can both rely on this single answer when deciding where a credential may go or what host value to expose. It calls `CredentialStore.get` to read any stored selection, and uses `HostChoice.resolve` indirectly through the host choice object.

*Call graph*: calls 1 internal fn (get).


### `core/src/ufo/runtime/access/grants.py`

`domain_logic` · `connect request handling, OAuth callback, grant administration`

This file is the project’s “connection desk” for OAuth accounts. OAuth is the common web flow where a user leaves the app, approves access on another service, and returns with a short-lived code. The problem this file solves is making that flow safe and durable: the agent needs access, but the user’s private token must not be handed into the sandbox or stored in a loose place.

The main flow has two halves. First, ConnectFlow.authorize creates a provider approval link. It seals important facts into an encrypted state value: workspace, member, agent, provider, conversation, and whether the connection is shared. Later, ConnectFlow.complete opens that sealed state, exchanges the provider’s returned code for a broker-side account, records or reuses the connection, grants the target agent, fires hooks so related features can prepare derived data, and optionally resumes the conversation that asked for the connection.

GrantStore is the database-facing part. It creates connection rows, grant edges, sharing changes, revocation, disconnect cleanup, and summary views. Think of a connection as a locked cabinet owned by one member, and a grant as a keycard issued to one agent. The file also contains helper naming and sentinel functions so sandboxes and proxies can agree which connected account is being requested without exposing the real secret.

#### Function details

##### `grant_sentinel`  (lines 49–53)

```
def grant_sentinel(account_id: str) -> str
```

**Purpose**: Builds a placeholder credential value for a connected account. The sandbox can carry this placeholder, and the egress proxy can later swap it for the real server-held token.

**Data flow**: It takes an account id string → adds a fixed sentinel prefix → returns a deterministic marker string that identifies that account without containing a secret.

**Call relations**: This is used wherever the engine and proxy need to agree on an account by name. It stands apart from the OAuth flow: ConnectFlow records the account, while this helper creates the safe marker other runtime pieces can pass around.


##### `usable_cli_accounts`  (lines 56–76)

```
def usable_cli_accounts(grants: 'tuple[Grant, ...]', provider: str, member_id: UUID | None) -> tuple[str, ...]
```

**Purpose**: Chooses which connected accounts a command-line tool may use for one provider. It prefers the member’s own private grants, and falls back to workspace-shared grants if no private ones are available.

**Data flow**: It receives a tuple of Grant objects, a provider name, and an optional member id → filters grants into private matches and shared matches → returns the sorted account ids from the best available group.

**Call relations**: This supports later sandbox environment setup. The active grants come from GrantStore.active_grants, and this function decides which accounts are safe and unambiguous to expose to command-line tooling.


##### `UnknownProvider.__init__`  (lines 84–85)

```
def __init__(self, provider: str) -> None
```

**Purpose**: Creates a friendly error when someone asks to connect a provider that this deployment does not know about. The message is written for a member, not just for a programmer.

**Data flow**: It receives a provider slug → formats a sentence saying no connector is available → stores that sentence as the exception text.

**Call relations**: ConnectFlow._provider raises this when it cannot find a provider descriptor, and ConnectFlow.validate_provider raises it when validation fails. That makes unknown connectors fail early and clearly.

*Call graph*: called by 2 (_provider, validate_provider).


##### `OAuthProvider.provider`  (lines 146–146)

```
def provider(self) -> str
```

**Purpose**: Names the provider this OAuth descriptor represents. The system uses this as the stable internal provider key when recording connections and grants.

**Data flow**: An implementation supplies no input here → returns its provider name string → callers store or compare that name.

**Call relations**: Connector extensions implement this protocol property. ConnectFlow uses the provider descriptor during completion so GrantStore.record writes the canonical provider name, not an unchecked request string.


##### `OAuthProvider.host`  (lines 149–149)

```
def host(self) -> str
```

**Purpose**: Names the provider host that a grant should allow through outbound access rules. In plain terms, it says which outside service this connection is for.

**Data flow**: An implementation supplies no input here → returns a host string → the grant record keeps that host for later access decisions.

**Call relations**: Connector extensions provide this value. During ConnectFlow.complete, the descriptor’s host is handed to GrantStore.record so later runtime egress can know what the grant permits.


##### `OAuthProvider.authorize_url`  (lines 151–151)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds the web link that sends a member to the provider’s consent page. This is the start of the OAuth handoff.

**Data flow**: It receives encrypted state and a redirect URI → embeds them in a provider-specific authorization URL → returns the URL the member should open.

**Call relations**: ConnectFlow.authorize calls the provider descriptor’s implementation after creating sealed state. ConnectHandoff.authorize may trigger that flow when a conversation needs a fresh connect link.


##### `OAuthProvider.exchange`  (lines 153–155)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Turns the code returned by the provider into a connected account known to the broker. This is where the provider confirms the user approved access.

**Data flow**: It receives the returned code, redirect URI, workspace id, and state → talks to the provider or broker implementation → returns an OAuthAccount containing the stable account id and optional display or commit identity.

**Call relations**: ConnectFlow.complete relies on this provider method after validating the sealed state. Its result is immediately passed into GrantStore.record so the connection becomes durable.


##### `OAuthProviderResolver.claims`  (lines 166–166)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: Checks whether an open connector namespace can serve a provider name that was not explicitly registered. This lets a broker support many provider slugs without listing each one in advance.

**Data flow**: It receives a provider string → consults the resolver’s catalog or rules → returns true if that resolver accepts the provider, false otherwise.

**Call relations**: ConnectFlow.validate_provider uses this check when the provider is not in the fixed provider map. If it returns false, validation raises UnknownProvider.


##### `OAuthProviderResolver.descriptor`  (lines 168–168)

```
def descriptor(self, provider: str) -> OAuthProvider
```

**Purpose**: Creates an OAuthProvider descriptor for a provider accepted by an open resolver. It is the factory for provider-specific OAuth behavior in that namespace.

**Data flow**: It receives a provider name → builds or returns a descriptor for it → callers use that descriptor to create authorization links and exchange codes.

**Call relations**: ConnectFlow._provider calls this when no explicit provider descriptor exists but a resolver is installed. That lets ConnectFlow.authorize, bridge_workspace, and complete continue through the same provider interface.


##### `ConnectionHooks.fire`  (lines 279–279)

```
async def fire(self, connection: ConnectionRecorded) -> None
```

**Purpose**: Publishes a newly recorded connection to extension code that needs to create follow-up state, such as feed source rows. It lets other parts react as soon as the connection lands.

**Data flow**: It receives a ConnectionRecorded payload → extension hook handlers inspect it and do their own work → the method returns when the hook dispatch is done.

**Call relations**: ConnectFlow.complete calls this after GrantStore.record commits the connection and grant. The hook sits between recording the connection and resuming the conversation, so resumed agents see the connection as ready.


##### `ConnectResumption.resume`  (lines 292–299)

```
async def resume(self, conversation_id: UUID, message: str, *, speaker_member_id: UUID, idempotency_key: str) -> bool
```

**Purpose**: Tells the conversation that originally requested the connection that the connection succeeded. This prevents the member from returning to a silent thread after approving access in the browser.

**Data flow**: It receives the conversation id, message text, speaker member id, and an idempotency key → queues or posts a resume message once → returns whether the resume was accepted.

**Call relations**: ConnectFlow.complete calls this last, after the grant is recorded and hooks have run. The idempotency key comes from _resume_key so browser refreshes do not create duplicate messages.


##### `_resume_key`  (lines 302–317)

```
def _resume_key(state: str) -> str
```

**Purpose**: Creates a stable duplicate-prevention key for one OAuth connect action. It is based on the sealed state rather than the connection id, because several separate connect actions may reuse the same connection row.

**Data flow**: It receives the sealed state string → hashes it with SHA-256 → returns a short key with the connect prefix and part of the digest.

**Call relations**: ConnectFlow.complete calls this when asking ConnectResumption.resume to notify the conversation. The key makes a callback refresh repeat the same resume instead of posting a new one.

*Call graph*: called by 1 (complete); 1 external calls (sha256).


##### `GrantStore.workspace_id`  (lines 344–345)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace currently active in the runtime context. GrantStore uses this so every database operation is automatically scoped to the right workspace.

**Data flow**: It reads the current workspace context → extracts the workspace id → returns that UUID.

**Call relations**: GrantStore methods use this property throughout their queries and writes. It depends on ws_current, which is set by surrounding code such as ConnectFlow.complete.

*Call graph*: 1 external calls (ws_current).


##### `GrantStore.agent_id`  (lines 348–349)

```
def agent_id(self) -> UUID
```

**Purpose**: Returns the agent currently targeted by object dispatch. This tells GrantStore which agent a grant operation applies to.

**Data flow**: It reads the current object-agent context → extracts the agent id → returns that UUID.

**Call relations**: GrantStore.record, active_grants, attach, revoke, and set_shared use this agent id to bind or inspect grant edges. ConnectFlow.complete sets the agent context before recording.

*Call graph*: 1 external calls (object_agent_id).


##### `GrantStore.record`  (lines 351–553)

```
async def record(self, *, provider: str, account_id: str, host: str, grantor_member_id: UUID, conversation_id: UUID, shared: bool, account_label: str | None=None, commit: CommitIdentity | None=None, l
```

**Purpose**: Records a completed OAuth connection and grants the current agent access to it. It also protects ownership: one provider account in a workspace cannot silently move from one member to another.

**Data flow**: It receives provider/account details, the granting member, conversation, sharing choice, optional display label, optional commit identity, and optional turn id → checks that the grantor still has workspace access → creates or reuses the connection row → updates account details and sharing without narrowing existing sharing → creates or updates the agent grant edge → stamps the answered turn if present → wakes parked feed sources that may now work again → returns the connection id.

**Call relations**: ConnectFlow.complete calls this after the provider exchange succeeds. Other GrantStore methods later read, attach, revoke, share, or disconnect the records this method creates.

*Call graph*: 10 external calls (__init__, __init__, now, and_, literal, or_, select, update, workspace_tx, uuid4).


##### `GrantStore.active_grants`  (lines 555–609)

```
async def active_grants(self) -> tuple[Grant, ...]
```

**Purpose**: Lists all connection grants usable by the currently bound agent. This is the agent’s view of which outside accounts it may act through.

**Data flow**: It reads the current workspace and agent id → joins grant rows to connection and member rows → converts each row into a Grant object with owner email, account label, and optional commit identity → returns a tuple of grants.

**Call relations**: The sandbox environment builder calls this when preparing command-line credentials. Its results can feed helpers like usable_cli_accounts and grant_sentinel.

*Call graph*: called by 1 (_grant_cli_env); 5 external calls (__init__, __init__, and_, select, workspace_tx).


##### `GrantStore.revoke`  (lines 611–623)

```
async def revoke(self, grant_id: UUID, *, actor_member_id: UUID) -> bool
```

**Purpose**: Removes one grant edge from the current agent, if the acting member is allowed to do so. The underlying connection may remain for other agents.

**Data flow**: It receives a grant id and actor member id → verifies the actor can touch that grant through _grant_for_actor → deletes the connector_grant row if allowed → returns true if a row was deleted, false if no matching grant was available.

**Call relations**: This is an administrative or user action after grants already exist. It delegates permission checking to _grant_for_actor, which in turn checks the owning connection.

*Call graph*: calls 1 internal fn (_grant_for_actor); 2 external calls (delete, workspace_tx).


##### `GrantStore.attach`  (lines 625–682)

```
async def attach(self, *, provider: str, account_id: str, conversation_id: UUID, actor_member_id: UUID, shared: bool) -> bool
```

**Purpose**: Grants the current agent access to an already existing connection. It is used when a member wants another agent to use an account that is already connected.

**Data flow**: It receives provider, account id, conversation, actor member, and requested sharing flag → finds and locks the matching connection → allows the attach only if the actor owns it or it is already shared → refuses attempts to widen sharing through attach → inserts the grant edge if it does not already exist → returns whether the connection existed.

**Call relations**: This complements GrantStore.record. Record creates or reuses a connection after OAuth; attach connects an agent to a connection that is already present.

*Call graph*: 4 external calls (__init__, select, workspace_tx, uuid4).


##### `GrantStore.set_shared`  (lines 684–712)

```
async def set_shared(self, grant_id: UUID, shared: bool, *, actor_member_id: UUID) -> bool
```

**Purpose**: Changes whether the connection behind a grant is shared across the workspace. It enforces who is allowed to widen or narrow that sharing.

**Data flow**: It receives a grant id, the desired shared value, and actor member id → checks permission through _grant_for_actor, with stricter admin rules when sharing is being turned on → updates the connection’s shared flag → returns whether a row changed.

**Call relations**: This is called by grant administration flows. It relies on _grant_for_actor to find the grant and confirm that the actor may change the underlying connection.

*Call graph*: calls 1 internal fn (_grant_for_actor); 3 external calls (select, update, workspace_tx).


##### `GrantStore.disconnect`  (lines 714–770)

```
async def disconnect(self, connection_id: UUID, *, actor_member_id: UUID) -> bool
```

**Purpose**: Fully removes a connection, not just one agent’s grant. It also retires feed sources and tombstones pages tied to that connection so stale synced content is no longer treated as live.

**Data flow**: It receives a connection id and actor member id → checks permission through _connection_for_actor → finds sources using the connection → deletes source grants, marks sources removed, clears active claims, tombstones pages, then deletes the connection row → returns true if the operation was allowed and completed, false if the connection was not found.

**Call relations**: This is the cleanup path for a member or authorized admin. Because grant edges cascade from the deleted connection, it removes all agent access connected to that account.

*Call graph*: calls 1 internal fn (_connection_for_actor); 5 external calls (now, delete, select, update, workspace_tx).


##### `GrantStore._connection_for_actor`  (lines 772–800)

```
async def _connection_for_actor(self, connection: AsyncConnection, connection_id: UUID, actor_member_id: UUID, *, admin_allowed: bool=True) -> UUID | None
```

**Purpose**: Checks whether a member may mutate a connection and returns the locked connection id if so. It is a permission gate shared by higher-level operations.

**Data flow**: It receives a database connection, connection id, actor member id, and whether admins are allowed → looks up and locks the connection → returns none if missing → returns the id if the actor owns it → otherwise checks admin status and either returns the id or raises a permission error.

**Call relations**: GrantStore.disconnect calls this directly. GrantStore._grant_for_actor also uses it when a grant operation must first verify the underlying connection.

*Call graph*: calls 1 internal fn (_is_admin); called by 2 (_grant_for_actor, disconnect); 3 external calls (__init__, execute, select).


##### `GrantStore._is_admin`  (lines 802–812)

```
async def _is_admin(self, connection: AsyncConnection, actor_member_id: UUID) -> bool
```

**Purpose**: Checks whether a member is an admin in the current workspace. It is a small helper for permission decisions.

**Data flow**: It receives a database connection and actor member id → reads the member row in the current workspace → returns true if the stored admin flag is true, otherwise false.

**Call relations**: GrantStore._connection_for_actor calls this when the actor is not the connection owner and admin access might be allowed.

*Call graph*: called by 1 (_connection_for_actor); 2 external calls (execute, select).


##### `GrantStore._grant_for_actor`  (lines 814–852)

```
async def _grant_for_actor(self, connection: AsyncConnection, grant_id: UUID, actor_member_id: UUID, *, admin_allowed: bool=True) -> UUID | None
```

**Purpose**: Checks whether a member may mutate a specific grant edge for the current agent. It verifies both the grant and the connection behind it.

**Data flow**: It receives a database connection, grant id, actor member id, and admin policy → finds the grant’s connection id for the current workspace and agent → asks _connection_for_actor to confirm permission → locks and returns the grant id if it still matches → returns none if it disappeared.

**Call relations**: GrantStore.revoke and GrantStore.set_shared call this before changing anything. It centralizes the “is this actor allowed?” logic for grant-level actions.

*Call graph*: calls 1 internal fn (_connection_for_actor); called by 2 (revoke, set_shared); 2 external calls (execute, select).


##### `ConnectFlow.authorize`  (lines 877–899)

```
def authorize(self, *, workspace_id: UUID, agent_id: UUID, provider: str, grantor_member_id: UUID, conversation_id: UUID, shared: bool, turn_id: UUID | None=None) -> str
```

**Purpose**: Creates the provider approval URL for a new connect attempt. It seals all facts needed later so the callback can be trusted without storing a separate pending request row.

**Data flow**: It receives workspace, agent, provider, member, conversation, sharing choice, and optional turn id → resolves the provider descriptor → builds a ConnectState object → encrypts it with Fernet, a symmetric encryption tool → asks the provider for an authorization URL → returns that URL.

**Call relations**: ConnectHandoff.authorize calls this when a turn needs a fresh link. Later, ConnectFlow.complete opens the same sealed state from the callback to finish the handoff.

*Call graph*: calls 1 internal fn (_provider); 1 external calls (__init__).


##### `ConnectFlow.validate_provider`  (lines 901–906)

```
async def validate_provider(self, provider: str) -> None
```

**Purpose**: Checks whether a provider name can be connected before a request is accepted. It catches typos or unavailable connectors early.

**Data flow**: It receives a provider string → accepts it if it is explicitly installed → otherwise asks the open resolver, if present, whether it claims the provider → raises UnknownProvider if nobody accepts it.

**Call relations**: This is the heavier validation step used when a connect request is made. ConnectHandoff.authorize later uses the cheaper knows_provider check while holding a turn row.

*Call graph*: calls 1 internal fn (__init__).


##### `ConnectFlow.knows_provider`  (lines 908–913)

```
def knows_provider(self, provider: str) -> bool
```

**Purpose**: Quickly answers whether the connect system still has some way to serve a provider. It avoids slow external catalog checks while a stored connect request is being opened.

**Data flow**: It receives a provider string → checks the local provider map or whether any resolver exists → returns true or false.

**Call relations**: ConnectHandoff.authorize calls this before minting a URL from an existing terminal connect request. It protects users from pressing a link for a provider that is no longer installed.


##### `ConnectFlow.bridge_workspace`  (lines 915–921)

```
def bridge_workspace(self, *, state: str, provider: str, callback: str) -> UUID
```

**Purpose**: Verifies a browser bridge request and tells which workspace it may run as. This prevents a bridge request from claiming a workspace different from the encrypted OAuth state.

**Data flow**: It receives state, provider, and callback URL → opens and validates the sealed state → checks the provider and callback match → confirms the provider is known → returns the workspace id from the state.

**Call relations**: connect_bridge_workspace calls this through the installed ConnectFlow. If checks fail, the outer helper rejects the bridge request by returning none.

*Call graph*: calls 2 internal fn (_open, _provider); 1 external calls (__init__).


##### `ConnectFlow.complete`  (lines 923–974)

```
async def complete(self, *, state: str, code: str) -> GrantRecorded
```

**Purpose**: Finishes the OAuth callback. It verifies the sealed state, exchanges the provider code, records the connection and grant, notifies extension hooks, and resumes the conversation if possible.

**Data flow**: It receives encrypted state and provider code → decrypts the state → resolves the provider → enters the saved workspace and agent context → exchanges the code for an OAuthAccount → records the grant through GrantStore.record → fires ConnectionHooks if installed → builds a member-facing connected message → resumes the conversation if installed → returns a GrantRecorded summary.

**Call relations**: This is the landing point for the OAuth callback. It calls _open, _provider, GrantStore.record, ConnectionHooks.fire, label_for, _resume_key, and ConnectResumption.resume in that order so the conversation wakes only after the connection is usable.

*Call graph*: calls 4 internal fn (_open, _provider, label_for, _resume_key); 4 external calls (__init__, __init__, agent, ws).


##### `ConnectFlow.label_for`  (lines 976–981)

```
def label_for(self, provider: str) -> str
```

**Purpose**: Returns the friendly display name for a provider. If no label was declared, it turns the provider slug into title-cased words.

**Data flow**: It receives a provider string → looks it up in the labels map → if absent, replaces underscores with spaces and title-cases it → returns the display label.

**Call relations**: ConnectFlow.complete calls this when building the callback result and conversation resume message. It keeps member-facing text from exposing raw wiring names when a better label exists.

*Call graph*: called by 1 (complete).


##### `ConnectFlow._provider`  (lines 983–989)

```
def _provider(self, name: str) -> OAuthProvider
```

**Purpose**: Finds the OAuth descriptor for a provider name. This is the central lookup used by the flow whenever it needs provider-specific behavior.

**Data flow**: It receives a provider name → returns an explicit descriptor if installed → otherwise asks the resolver for one if a resolver exists → raises UnknownProvider if neither path works.

**Call relations**: ConnectFlow.authorize uses it to build a consent URL, bridge_workspace uses it to verify bridge requests, and complete uses it before exchanging the callback code.

*Call graph*: calls 1 internal fn (__init__); called by 3 (authorize, bridge_workspace, complete).


##### `ConnectFlow._open`  (lines 991–996)

```
def _open(self, state: str) -> ConnectState
```

**Purpose**: Decrypts and validates the OAuth state returned from the browser. It rejects tampered or expired state values.

**Data flow**: It receives the state string → asks Fernet to decrypt it with a time limit → turns the JSON into a ConnectState object → returns the trusted claims, or raises ConnectStateInvalid if decryption fails.

**Call relations**: ConnectFlow.bridge_workspace and ConnectFlow.complete both call this before trusting any callback or bridge parameters. It is the main guard against forged OAuth returns.

*Call graph*: called by 2 (bridge_workspace, complete); 1 external calls (__init__).


##### `ConnectHandoff.authorize`  (lines 1024–1109)

```
async def authorize(self, workspace_id: UUID, turn_id: UUID, member_id: UUID) -> str
```

**Purpose**: Returns a private OAuth URL for a connect request stored on a conversation turn. It reuses a recent URL when safe, and mints a fresh one when the old sealed state is too close to expiry.

**Data flow**: It receives workspace id, turn id, and member id → locks and reads the turn → checks that the terminal connect request still exists, belongs to that member, names an available provider, and targets an existing agent → returns a memoized URL if it is still fresh → otherwise calls ConnectFlow.authorize to mint one → stores it on the turn with a compare-and-update race check → returns either the new URL or the winner from a competing update.

**Call relations**: This is the surface-facing handoff for a user pressing “connect.” It uses _held for freshness checks and ConnectFlow.authorize for new links.

*Call graph*: calls 1 internal fn (_held); 5 external calls (__init__, model_validate, select, update, workspace_tx).


##### `ConnectHandoff._held`  (lines 1111–1122)

```
def _held(self, url: str | None, authorized_at: datetime | None) -> str | None
```

**Purpose**: Decides whether a stored authorization URL is still fresh enough to hand back to the member. It avoids giving out a link whose encrypted state may expire during the provider consent flow.

**Data flow**: It receives a URL and timestamp → returns none if either is missing → normalizes the timestamp to UTC → compares its age to the short memo window → returns the URL if still fresh, otherwise none.

**Call relations**: ConnectHandoff.authorize calls this before minting a URL and again after losing a race to another updater. It keeps repeated presses stable without serving stale links.

*Call graph*: called by 1 (authorize); 3 external calls (now, replace, timedelta).


##### `install_connect_flow`  (lines 1128–1136)

```
def install_connect_flow(flow: ConnectFlow | None) -> None
```

**Purpose**: Installs the process-wide ConnectFlow singleton. This lets tools, surfaces, and callbacks all use the same configured credential key, provider map, and redirect URL.

**Data flow**: It receives a ConnectFlow or none → writes it into the module-level _installed_flow variable → returns nothing.

**Call relations**: Startup code or tests call this before connect operations run. installed_connect_flow later reads the value and fails loudly if no flow was installed.


##### `installed_connect_flow`  (lines 1139–1142)

```
def installed_connect_flow() -> ConnectFlow
```

**Purpose**: Returns the installed ConnectFlow or raises a clear error if connection support is unavailable. It prevents callers from silently proceeding without the credential setup needed to seal grants.

**Data flow**: It reads the module-level _installed_flow → if present, returns it → if absent, raises ConnectUnavailable.

**Call relations**: connect_bridge_workspace calls this before verifying bridge requests. Other runtime paths can also use it as the single access point for the configured connect flow.

*Call graph*: called by 1 (connect_bridge_workspace); 1 external calls (__init__).


##### `connect_bridge_workspace`  (lines 1145–1154)

```
def connect_bridge_workspace(request: Request) -> UUID | None
```

**Purpose**: Checks whether an incoming browser bridge request is valid and, if so, identifies its workspace. It returns none instead of leaking detailed errors to the bridge layer.

**Data flow**: It receives a Starlette Request → reads state, provider, and callback query parameters → asks the installed ConnectFlow to verify them → returns the workspace UUID if valid, or none if the flow is unavailable, the state is invalid, or the provider is unknown.

**Call relations**: This is a small web-facing wrapper around installed_connect_flow and ConnectFlow.bridge_workspace. It turns exceptions from the connect machinery into a simple accept-or-reject answer.

*Call graph*: calls 1 internal fn (installed_connect_flow).


##### `account_object_name`  (lines 1161–1170)

```
def account_object_name(provider: str, account_id: str) -> str
```

**Purpose**: Creates a stable, readable object name for a provider account. The name includes a short hash so two similar-looking accounts do not collide.

**Data flow**: It receives provider and account id → hashes their exact combined identity → slugifies both into lowercase dash-separated text → truncates the readable head to fit the object-name limit → appends the hash qualifier → returns the final name.

**Call relations**: It calls _slug for the readable pieces and SHA-256 for the collision-resistant suffix. Portal and object surfaces can use the result to name the same connection consistently.

*Call graph*: calls 1 internal fn (_slug); 1 external calls (sha256).


##### `_slug`  (lines 1173–1174)

```
def _slug(raw: str) -> str
```

**Purpose**: Turns arbitrary text into a simple lowercase dash-separated slug. This is useful for names that must be safe and readable in object identifiers.

**Data flow**: It receives raw text → lowercases it → replaces runs of non-letter-or-number characters with dashes → trims leading and trailing dashes → returns the slug.

**Call relations**: account_object_name calls this for both provider and account id before adding a hash suffix.

*Call graph*: called by 1 (account_object_name); 1 external calls (sub).


##### `grant_summaries`  (lines 1177–1185)

```
async def grant_summaries() -> tuple[GrantSummary, ...]
```

**Purpose**: Returns audit-style summaries of connector grants for the currently targeted agent. This is the view a normal object-scoped action needs.

**Data flow**: It reads the current workspace and object agent id → builds a database scope for that agent’s grants → delegates the actual query and row conversion to _grant_summaries → returns GrantSummary objects.

**Call relations**: This is a public wrapper around _grant_summaries. It supplies the agent-specific filter so the shared helper can do the common join and formatting work.

*Call graph*: calls 1 internal fn (_grant_summaries); 3 external calls (and_, object_agent_id, ws_current).


##### `workspace_grant_summaries`  (lines 1188–1191)

```
async def workspace_grant_summaries(workspace_id: UUID) -> tuple[GrantSummary, ...]
```

**Purpose**: Returns audit-style grant summaries for an entire workspace. This is useful for operator or admin surfaces that need the full picture.

**Data flow**: It receives a workspace id → enters that workspace context → asks _grant_summaries for all grant rows in that workspace → returns the resulting GrantSummary tuple.

**Call relations**: Like grant_summaries, this delegates the heavy query to _grant_summaries, but it uses a workspace-wide filter instead of an agent filter.

*Call graph*: calls 1 internal fn (_grant_summaries); 1 external calls (ws).


##### `_grant_summaries`  (lines 1194–1245)

```
async def _grant_summaries(scope: sa.ColumnElement[bool]) -> tuple[GrantSummary, ...]
```

**Purpose**: Runs the shared database query that turns grant rows into audit summaries. It joins grants to connections, agents, and member emails so a human can see who granted what to whom.

**Data flow**: It receives a SQL scope condition → queries connector grants joined with connection, agent, and member tables → orders by provider and agent name → converts each row into a GrantSummary → returns them as a tuple.

**Call relations**: grant_summaries and workspace_grant_summaries both call this with different scopes. Keeping the join here makes both views consistent.

*Call graph*: called by 2 (grant_summaries, workspace_grant_summaries); 4 external calls (__init__, and_, select, workspace_tx).


##### `connection_summaries`  (lines 1248–1325)

```
async def connection_summaries() -> tuple[ConnectionSummary, ...]
```

**Purpose**: Lists all member-owned connections in the current workspace, independent of which agent is currently bound. Each summary also includes the names of agents granted that connection.

**Data flow**: It reads the current workspace → queries connections joined to owners and optionally grants and agents → groups rows by provider and account id → gathers agent names per connection → returns ConnectionSummary objects with sorted agent lists.

**Call relations**: This supports workspace connection overview surfaces. It reads the same tables written by GrantStore.record, attach, revoke, set_shared, and disconnect.

*Call graph*: 5 external calls (__init__, and_, select, workspace_tx, ws_current).


##### `main_agent_connections`  (lines 1328–1363)

```
async def main_agent_connections() -> tuple[MainAgentConnection, ...]
```

**Purpose**: Lists connections granted to the workspace’s main agent. Feed registration can use this to know which connected accounts are available by default.

**Data flow**: It reads the current workspace → queries connections joined to grant edges and agents → keeps only grants where the agent is marked as the main agent → orders by provider and account id → returns MainAgentConnection objects.

**Call relations**: This is a focused read path over the grant records created by GrantStore.record or attach. It intentionally excludes connections granted only to non-main shipped agents.

*Call graph*: 4 external calls (__init__, select, workspace_tx, ws_current).


### Authority and seating sessions
These files model member authority, operator-only sessions, and workspace seat rules for who may act or speak in a workspace.

### `core/src/ufo/runtime/authority.py`

`data_model` · `cross-cutting`

This file answers a basic security question: “Whose authority is this execution using?” Some actions run with a member’s private credentials, like a person using their own badge. Other actions run without any individual member attached, using only workspace-level authority. The file makes that difference explicit with two small immutable data shapes: `MemberAuthority`, which carries a member UUID, and `WorkspaceAuthority`, which carries no member id. “Immutable” means once created, the value cannot be changed, which helps prevent accidental privilege changes later in a run.

The project still stores this authority in some places as a nullable member id: a real UUID means “member authority,” and `None` means “workspace authority.” This file is the translator between that storage shape and the clearer runtime shape.

It also protects a turn of execution from claiming two identities at once. If both a speaker member and a delegated “on behalf of” member are present, that is treated as an error, because the system would not know which authority should be used. Without this file, code elsewhere would have to repeat these rules, making it easier to accidentally mix up workspace actions and member-private actions.

#### Function details

##### `authority_from_member_id`  (lines 28–30)

```
def authority_from_member_id(member_id: UUID | None) -> ExecutionAuthority
```

**Purpose**: Turns the older stored form of authority into the clearer runtime form. A missing member id means workspace authority; a real member id means the execution is acting as that member.

**Data flow**: It receives either a UUID or `None`. If the input is `None`, it returns the shared workspace authority value. If the input is a UUID, it creates and returns a `MemberAuthority` containing that UUID. It does not change anything outside itself.

**Call relations**: This is the shared decoding step used when another part of the file needs to turn nullable member information into an execution authority. `turn_authority` calls it after deciding which possible member id should count for the current turn.

*Call graph*: called by 1 (turn_authority); 1 external calls (__init__).


##### `authority_member_id`  (lines 33–41)

```
def authority_member_id(authority: ExecutionAuthority) -> UUID | None
```

**Purpose**: Turns the runtime authority value back into the older nullable member id form used by existing storage or token formats. This keeps the rest of the system from guessing how to encode authority.

**Data flow**: It receives an execution authority object. If it is `MemberAuthority`, it extracts and returns that member’s UUID. If it is `WorkspaceAuthority`, it returns `None`. If it receives something that is not one of the expected authority types, it raises a `TypeError` instead of silently producing a bad value.

**Call relations**: No direct caller is shown in the provided call facts, but its role is the opposite of `authority_from_member_id`: code that needs to write authority back into the existing nullable-member shape can use this function as the single safe encoder.


##### `turn_authority`  (lines 44–52)

```
def turn_authority(speaker_member_id: UUID | None, on_behalf_of_member_id: UUID | None) -> ExecutionAuthority
```

**Purpose**: Chooses the single authority for one turn of execution. It prevents a turn from carrying both a direct speaker identity and a delegated identity at the same time.

**Data flow**: It receives two optional member ids: one for the speaker and one for a member being acted on behalf of. If both are present, it raises a `ValueError` because that would be ambiguous. Otherwise, it picks the present member id, or `None` if neither is present, and passes that value to `authority_from_member_id`. The result is one clear execution authority.

**Call relations**: This function sits at the point where turn-level identity information is normalized. After checking that the inputs do not conflict, it hands the chosen nullable member id to `authority_from_member_id`, which performs the actual conversion into `MemberAuthority` or `WorkspaceAuthority`.

*Call graph*: calls 1 internal fn (authority_from_member_id).


### `core/src/ufo/runtime/ext/operator.py`

`domain_logic` · `request handling and operator dashboard reads`

Operator tools need stronger, cleaner access rules than ordinary pages. This file makes sure an operator proves who they are with a bearer token, which is a signed credential, without ever putting that long-lived secret in a URL where it could leak through browser history or server logs. It accepts the token from the Authorization header, an HTTP-only cookie, or the one form POST used to open the session. Once verified, it checks that the email belongs to the configured operator domain before allowing the request to view or switch between workspaces.

It also knows how to start the shared operator session. A successful POST stores the token in one cookie, so an operator can sign in once and move between all operator surfaces.

The other half of the file is the fleet directory. Think of it like the lobby board in a building: it shows every workspace, how busy it is, and the most recently active conversations across the whole deployment. To preserve workspace boundaries, it first reads only IDs and timestamps from the owner-level database view, then re-enters each workspace separately to read human-facing details such as domains, titles, and queue keys. It deliberately ignores subagent turns so background fan-out work does not drown out real member-facing conversations.

#### Function details

##### `operator_claims`  (lines 45–63)

```
async def operator_claims(request: Request) -> tuple[str, str] | None
```

**Purpose**: This function tries to prove who an operator request belongs to. It looks for a valid token in safe places only: first the Authorization header, then the operator session cookie, and finally the submitted form body for the one POST that opens a session.

**Data flow**: It receives a web request. It reads the Authorization header, then the cookie named for the operator session, and if the request is a POST it reads the form field named token. Each possible token is trimmed and verified. If one works, it returns the workspace claim and email address from the token; if none work, it returns nothing.

**Call relations**: resolve_operator_workspace calls this at the start of operator access checks. operator_claims delegates the actual token checking to _candidate_claims, and it reads the request form only when a POST may be trying to create a session.

*Call graph*: calls 1 internal fn (_candidate_claims); called by 1 (resolve_operator_workspace); 1 external calls (form).


##### `_candidate_claims`  (lines 66–68)

```
def _candidate_claims(candidate: str) -> tuple[str, str] | None
```

**Purpose**: This small helper turns a possible token string into verified identity claims. It exists so the same trimming and verification rule is used for headers, cookies, and form posts.

**Data flow**: It receives a raw candidate string. It removes surrounding spaces; if anything remains, it asks the bearer-token verifier to validate it and decode its claims. It returns the verified workspace and email pair when valid, or nothing when the string is empty or invalid.

**Call relations**: operator_claims calls this for every possible token source. The helper hands the real security decision to verified_claims, which knows how to validate the signed bearer token.

*Call graph*: called by 1 (operator_claims); 1 external calls (verified_claims).


##### `resolve_operator_workspace`  (lines 71–111)

```
async def resolve_operator_workspace(request: Request, _auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: This function decides which workspace an operator request is allowed to use. It also redirects unauthenticated page visits to the shared login page instead of simply failing.

**Data flow**: It receives the current web request and the surface authentication object. It asks operator_claims for a verified workspace and email. If there is no valid credential, a plain page GET is redirected to the operator login flow; other requests are rejected by returning nothing. If the email is not from the operator domain, it rejects the request. If there is no ws query parameter, it uses the workspace ID from the token. If ws is present, it accepts either a raw workspace UUID or a workspace domain; for a domain, it looks up the seated workspace and falls back to the deterministic UUID that would be used for that domain.

**Call relations**: This is the main gate used by operator surfaces before serving a page or API route. It calls operator_claims for identity, checks the email domain, may call workspace_by_domain inside an owner-level transaction, and may return a RedirectResponse when a browser should be sent to sign in.

*Call graph*: calls 1 internal fn (operator_claims); 6 external calls (owner_tx, email_domain, workspace_by_domain, RedirectResponse, UUID, uuid5).


##### `bind_operator_session`  (lines 114–132)

```
async def bind_operator_session(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function opens the shared operator browser session after login. It takes the posted bearer token and stores it in a secure cookie, then sends the browser back to the requested operator page.

**Data flow**: It receives the surface context and web request. It reads the form body and looks for the token field. If the field is missing or blank, it returns a JSON error with status 400. Otherwise it creates a redirect response to the same URL, sets the operator session cookie on that response, and returns it.

**Call relations**: This is used after the access resolver has already verified the posted token. It reads the request form, builds either a JSONResponse for bad input or a RedirectResponse for success, and uses set_session_cookie so later operator requests can be authenticated from the cookie.

*Call graph*: 4 external calls (JSONResponse, RedirectResponse, form, set_session_cookie).


##### `FleetWorkspace._aware_utc`  (lines 150–151)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: This validator makes sure a workspace activity timestamp includes timezone information. That prevents later code from accidentally mixing timezone-aware and timezone-unaware dates.

**Data flow**: It receives the last activity time for a workspace, or nothing. If there is no time, it leaves it alone. If the time already has timezone information, it leaves it alone. If the time lacks a timezone, it marks it as UTC and returns that adjusted value.

**Call relations**: Pydantic, the data validation library used for these models, calls this when creating a FleetWorkspace. It uses datetime.replace only when it needs to attach UTC to a timestamp.

*Call graph*: 1 external calls (replace).


##### `FleetThread._aware_utc`  (lines 169–170)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: This validator makes sure a recent-thread timestamp is timezone-aware. It keeps the fleet directory’s dates consistent when they are displayed or compared.

**Data flow**: It receives a thread’s last activity time. If the time already names a timezone, it returns it unchanged. If it does not, it labels the time as UTC and returns the corrected timestamp.

**Call relations**: Pydantic calls this when creating a FleetThread. FleetDirectory.read creates those FleetThread objects after collecting recent conversation information.

*Call graph*: 1 external calls (replace).


##### `FleetDirectory.read`  (lines 202–233)

```
async def read(self) -> FleetListing
```

**Purpose**: This function builds the complete operator fleet listing: all workspaces plus the most recently active conversations across them. It is the public read method for the operator’s directory page.

**Data flow**: It starts by asking _enumerate for owner-level rows: workspace IDs, last activity times, and recent conversation IDs. It groups the recent conversation IDs by workspace. Then, for each workspace, it temporarily scopes execution to that workspace and calls _scoped to read displayable details such as domain, member count, conversation count, and conversation titles. Finally it combines those pieces into a FleetListing containing FleetWorkspace and FleetThread records.

**Call relations**: This is the top-level directory builder. It calls _enumerate first for the cross-workspace skeleton, then enters each workspace with ws and calls _scoped for the human-readable details. It constructs FleetThread records only for recent conversations that were successfully opened in the scoped pass.

*Call graph*: calls 2 internal fn (_enumerate, _scoped); 3 external calls (__init__, __init__, ws).


##### `FleetDirectory._enumerate`  (lines 235–273)

```
async def _enumerate(self) -> tuple[Sequence[sa.Row[Any]], Sequence[sa.Row[Any]]]
```

**Purpose**: This function performs the safe cross-workspace pass for the fleet directory. It gathers only identifiers and activity timestamps, not user-facing text.

**Data flow**: It builds two database queries. One lists every workspace with its most recent root turn time, keeping even workspaces with no turns yet. The other lists the most recently active root conversations across all workspaces, including their turn counts and last activity times, capped by the directory’s thread limit. It runs both queries in an owner-level database transaction and returns the resulting rows.

**Call relations**: FleetDirectory.read calls this before entering individual workspaces. It uses owner_tx because it needs to look across the whole deployment, and it uses SQLAlchemy query building to ask the database for the ordered workspace and conversation skeleton.

*Call graph*: called by 1 (read); 2 external calls (select, owner_tx).


##### `FleetDirectory._scoped`  (lines 275–323)

```
async def _scoped(self, workspace_id: UUID, last_turn_at: datetime | None, conversation_ids: Sequence[UUID]) -> tuple[FleetWorkspace, dict[UUID, sa.Row[Any]]]
```

**Purpose**: This function reads the human-readable details for one workspace in the fleet directory. It works inside a workspace-scoped database context so the same row-level protections apply as normal workspace reads.

**Data flow**: It receives a workspace ID, that workspace’s last activity time, and the recent conversation IDs that should be opened there. It queries the workspace domain, counts members, counts non-subagent conversations, and fetches surface, queue key, and title for the requested conversations. It returns a FleetWorkspace summary and a dictionary of opened conversation rows keyed by conversation ID.

**Call relations**: FleetDirectory.read calls this once per workspace after rebinding scope with ws. _scoped uses workspace_tx for the workspace-level database transaction, calls workspace_domain for the display domain, and creates the FleetWorkspace object that later appears in the final FleetListing.

*Call graph*: called by 1 (read); 4 external calls (__init__, select, workspace_tx, workspace_domain).


### `core/src/ufo/runtime/seats.py`

`domain_logic` · `cross-cutting: onboarding, access checks, admin seat changes, and background seat-reporting jobs`

A “seat” here means active permission for a workspace member to use the agent. The project treats membership and access as different things: a person can still be remembered as a member after an admin removes their seat, but the agent will refuse to answer them until the seat is restored. This is like keeping someone in an address book while taking away their door key.

The file provides the rules for checking seats, changing seats, creating members, and finding workspaces from email information. The central class is Seats, which is tied to one workspace and always works through a database connection. That matters because the same rules run in every place that needs them: when someone sends a message, while a turn is running, and when an admin tool grants or revokes access.

Several safeguards are built in. Revoking a seat is harmless if the person is already unseated, but it refuses to unseat the last seated admin, because then nobody could restore access through chat. Member creation always checks that email addresses have a normal one-local-part-at-one-domain shape, lowercases them, and uses database uniqueness so two simultaneous attempts to add the same person collapse into one member row.

The file also knows how hosted workspaces are named from signup emails or domains, while avoiding unsafe matches for personal email providers.

#### Function details

##### `SeatSnapshot.seated`  (lines 58–59)

```
def seated(self) -> int
```

**Purpose**: Counts how many members in a snapshot currently have a seat. It gives callers a simple number for reporting or display without making them inspect every member themselves.

**Data flow**: It starts with the snapshot's stored member entries. It looks at each entry's seated flag, counts the ones that are true, and returns that count. It does not change the snapshot.

**Call relations**: This is used after a SeatSnapshot has been built, especially by code that wants a summary of the current seat state. It relies on the snapshot data that Seats.snapshot creates.


##### `Seats.admits`  (lines 70–87)

```
async def admits(self, connection: AsyncConnection, authority: ExecutionAuthority) -> bool
```

**Purpose**: Answers the basic access question: is this authority allowed to act in this workspace right now? Workspace-level authority is always allowed, while member-level authority must still have an active seat.

**Data flow**: It receives a database connection and an authority object. If the authority represents the whole workspace, it returns true. If it represents a member, it looks up that member in this workspace and checks whether their seated_at value is present. It returns true only when the member exists here and is still seated.

**Call relations**: This is the front-door check for live access. Other parts of the system call it when they need to know whether a saved authority can still be used, and it gets its answer directly from the member table through the provided connection.

*Call graph*: 2 external calls (execute, select).


##### `Seats.all_seated`  (lines 89–109)

```
async def all_seated(self, connection: AsyncConnection, member_ids: Collection[UUID]) -> bool
```

**Purpose**: Checks whether a whole group of members still has seats. This is useful when one running turn depends on several speakers and the system must know whether all of them are still allowed.

**Data flow**: It receives a set or other collection of member IDs. If the set is empty, it returns true. Otherwise it asks the database how many of those IDs belong to this workspace and have a non-empty seated_at value, then compares that count with the number requested. The result is true only if every requested member is currently seated.

**Call relations**: This supports repeated liveness checks during work such as per-round enforcement, parked-turn checks, and dispatch sweeps. Instead of checking members one by one, those flows can ask this function once for the whole group.

*Call graph*: 2 external calls (execute, select).


##### `Seats.snapshot`  (lines 111–134)

```
async def snapshot(self, connection: AsyncConnection) -> SeatSnapshot
```

**Purpose**: Builds a read-only picture of all members in a workspace and whether each one is seated and/or an admin. This is meant for reporting the current seat state to users or tools.

**Data flow**: It receives a database connection, reads all member rows for this workspace in creation order, and turns each row into a SeatEntry with an ID, email, seated flag, and admin flag. It wraps those entries in a SeatSnapshot and returns it. It does not modify the database.

**Call relations**: This is the read side of seat administration. Code that wants to show or inspect the current membership state calls it, and the returned SeatSnapshot can then provide details such as the seated count.

*Call graph*: 4 external calls (__init__, __init__, execute, select).


##### `Seats.grant`  (lines 136–146)

```
async def grant(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Restores access for an existing workspace member by giving them a seat. If the member is already seated, it does nothing, so callers can safely repeat the request.

**Data flow**: It receives a connection and an email address. It first uses Seats._member_by_email to find the matching member in this workspace. If that member already has a seated_at timestamp, it returns without changes. Otherwise it updates the member row with the current time in seated_at and updated_at.

**Call relations**: Admin-facing flows call this when a seat should be restored. It delegates the email lookup to Seats._member_by_email so grant and revoke use the same member-finding rule, then writes the actual change with a database update.

*Call graph*: calls 1 internal fn (_member_by_email); 2 external calls (execute, update).


##### `Seats.revoke`  (lines 148–171)

```
async def revoke(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Removes access from an existing workspace member by clearing their seat. It also protects the workspace from being stranded by refusing to unseat the last seated admin.

**Data flow**: It receives a connection and an email address. It first locks the workspace row so competing revokes cannot both make decisions from stale counts. It finds the member by email. If the member is already unseated, it returns. If the member is an admin and they are the only seated admin left, it raises LastAdminSeatRevocation. Otherwise it clears seated_at and updates updated_at.

**Call relations**: Admin tools call this when someone should lose access. It relies on Seats._member_by_email to identify the member and Seats._seated_admin_count to enforce the last-admin safety rule before it writes the revoke.

*Call graph*: calls 2 internal fn (_member_by_email, _seated_admin_count); 4 external calls (__init__, execute, select, update).


##### `Seats._member_by_email`  (lines 173–190)

```
async def _member_by_email(self, connection: AsyncConnection, email: str) -> tuple[UUID, datetime | None, bool]
```

**Purpose**: Finds a member in this workspace by email and returns the details needed for seat changes. It raises a clear error when the email does not belong to any member here.

**Data flow**: It receives a connection and an email address. It trims and lowercases the email for comparison, searches only within this workspace, and reads the member ID, seated_at value, and admin flag. If no row is found, it raises UnknownMember. Otherwise it returns those three pieces of information.

**Call relations**: Seats.grant and Seats.revoke both call this so they share one exact lookup rule. It is deliberately private to the Seats class because it supports the grant/revoke workflow rather than being a general public lookup.

*Call graph*: called by 2 (grant, revoke); 3 external calls (__init__, execute, select).


##### `Seats._seated_admin_count`  (lines 192–201)

```
async def _seated_admin_count(self, connection: AsyncConnection) -> int
```

**Purpose**: Counts how many admins in this workspace currently still have seats. It exists mainly to enforce the rule that the last seated admin cannot be removed.

**Data flow**: It receives a database connection, counts member rows in this workspace where seated_at is present and is_admin is true, and returns that number. It does not change anything.

**Call relations**: Seats.revoke calls this only when the target member is an admin. The count tells revoke whether clearing that seat would leave the workspace with no seated admin.

*Call graph*: called by 1 (revoke); 2 external calls (execute, select).


##### `email_domain`  (lines 204–217)

```
def email_domain(email: str) -> str
```

**Purpose**: Extracts a safe, lowercase domain from an email address. If the input is not exactly one normal local@domain address with no whitespace, it returns an empty string so bad data cannot accidentally match or create a member.

**Data flow**: It receives a string, trims and lowercases it, then checks that there is one @ sign with text on both sides and no whitespace. If the shape is valid, it returns the domain part after the @. Otherwise it returns an empty string.

**Call relations**: Several workspace and member flows rely on this as the shared email-shape gate. create_member uses it before inserting a member, while workspace_subject, workspace_domain, and workspace_by_domain use it when deriving or matching workspace identity from email domains.

*Call graph*: called by 4 (create_member, workspace_by_domain, workspace_domain, workspace_subject).


##### `signup_workspace_id`  (lines 220–222)

```
def signup_workspace_id(subject: str) -> UUID
```

**Purpose**: Turns a signup subject into the stable ID of the hosted workspace it names. A signup subject can be an exact email address or a domain, depending on how the workspace was created.

**Data flow**: It receives a subject string, lowercases it, and feeds it into UUID version 5 generation. That produces the same UUID every time for the same subject. It returns that UUID.

**Call relations**: Workspace identity helpers call this when they need to compare an email or domain-derived subject with an actual workspace ID. workspace_subject, workspace_domain, and workspace_by_domain all use it to avoid inconsistent workspace naming rules.

*Call graph*: called by 3 (workspace_by_domain, workspace_domain, workspace_subject); 1 external calls (uuid5).


##### `workspace_subject`  (lines 225–234)

```
def workspace_subject(first_email: str, workspace_id: UUID) -> str
```

**Purpose**: Returns the signup subject that represents a seated workspace. For personal-email workspaces it is the founder's exact email; for domain-based workspaces it is the founder's email domain.

**Data flow**: It receives the first member's email and the workspace ID. It checks whether the workspace ID is the one produced from that exact email. If so, it returns the email. Otherwise it extracts and returns the email's domain.

**Call relations**: This is the shared derivation used by consumers that need to label, invite into, or check a workspace subject. It calls signup_workspace_id and email_domain so all those consumers follow the same rule.

*Call graph*: calls 2 internal fn (email_domain, signup_workspace_id).


##### `workspace_domain`  (lines 237–255)

```
async def workspace_domain(connection: AsyncConnection, workspace_id: UUID) -> str | None
```

**Purpose**: Finds the domain that belongs to a workspace when the workspace was created as a domain workspace. It deliberately returns nothing for personal-email workspaces so a shared provider domain does not grant access to unrelated users.

**Data flow**: It receives a connection and workspace ID. It reads the earliest member's email for that workspace, extracts its domain, and checks whether the workspace ID came from the exact email instead of the domain. If there is no first member, no valid domain, or the workspace is personal-email based, it returns None. Otherwise it returns the domain.

**Call relations**: Code that needs to know whether a workspace can be addressed or joined by domain calls this helper. It uses email_domain for safe parsing and signup_workspace_id to distinguish domain workspaces from personal-email workspaces.

*Call graph*: calls 2 internal fn (email_domain, signup_workspace_id); 2 external calls (execute, select).


##### `workspace_by_domain`  (lines 258–291)

```
async def workspace_by_domain(connection: AsyncConnection, domain: str) -> UUID | None
```

**Purpose**: Looks up the workspace whose first member gives it a particular email domain. It skips personal-email workspaces, so domains like common mail providers do not accidentally resolve to someone else's workspace.

**Data flow**: It receives a connection and a domain string. It first validates the domain by pretending it is part of an address. Then it searches for workspaces whose earliest member email ends with that domain, ordered in a stable way. From those candidates, it returns the first workspace whose ID was not derived from the founder's exact email. If no valid match exists, it returns None.

**Call relations**: Domain-based onboarding or join flows can call this to find where a verified domain should lead. It uses email_domain to clean the input and signup_workspace_id to filter out personal-email workspaces.

*Call graph*: calls 2 internal fn (email_domain, signup_workspace_id); 2 external calls (execute, select).


##### `member_by_email`  (lines 294–310)

```
async def member_by_email(connection: AsyncConnection, workspace_id: UUID, email: str) -> UUID | None
```

**Purpose**: Finds a member ID in one specific workspace from an email address. It is a lookup only; it never creates a member or grants a seat.

**Data flow**: It receives a connection, a workspace ID, and an email address. It trims and lowercases the email for comparison, searches within the given workspace, and returns the member ID if found. If no member in that workspace has that address, it returns None.

**Call relations**: Routes or admission code can call this after an email has been verified elsewhere. The function keeps the workspace condition inside the database query, which avoids accidentally reading a same-email member from another workspace along the way.

*Call graph*: 2 external calls (execute, select).


##### `member_is_admin`  (lines 313–324)

```
async def member_is_admin(connection: AsyncConnection, workspace_id: UUID, member_id: UUID) -> bool
```

**Purpose**: Checks whether a given seated member is an admin in a workspace. Unseated admins do not count for this check, because they currently cannot exercise admin access.

**Data flow**: It receives a connection, workspace ID, and member ID. It queries for the admin flag only if that member belongs to the workspace and has a current seat. It returns true when such a row says the member is an admin, otherwise false.

**Call relations**: Permission-checking code can call this before allowing admin-only actions. It reads directly from the member table and folds membership, seating, and admin status into one answer.

*Call graph*: 2 external calls (execute, select).


##### `create_member`  (lines 327–398)

```
async def create_member(connection: AsyncConnection, workspace_id: UUID, email: str, *, is_admin: bool=False, invited_by: UUID | None=None) -> UUID
```

**Purpose**: Creates a member row in a workspace through one shared, safe path. New members are seated by default, and repeated racing attempts to create the same email return the already-created member instead of making duplicates.

**Data flow**: It receives a connection, workspace ID, email, optional admin flag, and optional inviter ID. It first validates the email shape with email_domain and lowercases it. It locks the workspace row so simultaneous creations follow the same order. Then it inserts a new member with a fresh UUID, invitation details if present, and timestamps. If another transaction already created the same workspace/email row, the insert does nothing and it reads back the existing member ID. It returns the created or existing member ID.

**Call relations**: Every surface that mints members is meant to use this function, such as onboarding, verified teammate joins, and admin invitations. It calls email_domain for the shared address rule and uses database conflict handling so callers do not each need to solve duplicate-creation races.

*Call graph*: calls 1 internal fn (email_domain); 3 external calls (execute, select, uuid4).


##### `member_workspaces`  (lines 401–409)

```
def member_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a candidate source for jobs that need to visit workspaces with members. It gives other code a safe way to ask, “Which workspaces have any member rows?” without reaching into the member table itself.

**Data flow**: It creates an inner query function that selects distinct workspace IDs from the member table, then wraps that function with owner_candidates. The result is a WorkspaceCandidates object that a job can use later. It does not run the query immediately.

**Call relations**: Background or extension jobs can declare this as their workspace candidate list. The nested member_workspaces.with_a_member function supplies the actual query, and owner_candidates packages it in the form the job framework expects.

*Call graph*: 1 external calls (owner_candidates).


##### `member_workspaces.with_a_member`  (lines 406–407)

```
def with_a_member() -> sa.Select[tuple[UUID]]
```

**Purpose**: Defines the database query for workspaces that have at least one member. It is intentionally broad: any member row makes the workspace a candidate.

**Data flow**: It takes no direct inputs. When called, it returns a SQL select statement that asks for distinct workspace IDs from the member table. The statement itself is handed back for later execution; this function does not contact the database.

**Call relations**: member_workspaces passes this query builder into owner_candidates. Later, the candidate system can call it when it needs to find workspaces for a seat-reporting or member-related job.

*Call graph*: 1 external calls (select).


### Connection callbacks and login flows
These files finish browser-return connection flows and implement member-owned phone, Anthropic, and OpenAI credential setup.

### `core/src/ufo/runtime/surfaces/cli.py`

`io_transport` · `request handling`

This file is the small web-facing doorway for finishing an account connection. In an OAuth flow, the user leaves UFO to approve access with another service, and that service sends the browser back with a short-lived code and a sealed state value. The sealed state is like a tamper-proof claim ticket: it proves which member, agent, and conversation started the connection without needing the browser to be logged in here.

The main route checks that the connection service is available, requires both the state and the provider code, and then asks the installed connect flow to finish the handoff. That step verifies the state, exchanges the provider code, and records the granted account. If anything is missing, invalid, unavailable, or refers to an uninstalled provider, the route returns a clear HTTP error.

After a successful connection, the file builds a plain callback page. If this deployment has a browser portal, the page links or forwards the user to the connectors screen and includes the newly connected account name in the URL. If there is no portal, it shows a completion message and asks the user to close the tab or return to the conversation. The file also serves the UFO logo used on that callback page, because this page may be reached without a normal frontend session or bundled web app.

#### Function details

##### `portal_url`  (lines 52–62)

```
def portal_url(public_base_url: str | None, home_surface: str | None) -> str | None
```

**Purpose**: Builds the public URL for the deployment’s browser portal, if one exists. It is used when the system needs somewhere in the web app to send a user after a connector is successfully added.

**Data flow**: It receives a public base URL and the name of the browser surface. If either is missing, it returns None, meaning there is no browser destination available. If both are present, it trims any trailing slash from the base URL and appends `/surface/<home_surface>`, producing the portal address.

**Call relations**: This helper is meant to be used when the connect flow is prepared, so the finished callback later knows where to send the browser. The callback route itself reads the already-prepared portal URL from the installed connect flow rather than rebuilding it during the request.


##### `connect_callback`  (lines 69–105)

```
async def connect_callback(state: str='', code: str='') -> HTMLResponse
```

**Purpose**: Finishes the provider return step after a user approves an account connection. It confirms the sealed state, completes the account grant, and returns a friendly HTML page saying what was connected and where the user should go next.

**Data flow**: It receives `state` and `code` from the browser’s query string. It gets the installed connect flow, rejects the request if the flow is unavailable or if either value is missing, then asks the flow to complete the connection. On success, it builds a human-readable account name from the recorded provider label and optional account label. If a portal URL exists, it creates a connectors-screen link and a forwarding URL with the connected account name encoded into the query string. If no portal exists, it returns a page that can close itself or tell the user the conversation continues. Invalid state, unavailable service, or unknown provider become HTTP error responses.

**Call relations**: FastAPI calls this function when a browser visits `/v1/connect/callback`. Inside that request, it asks `installed_connect_flow` for the active connection machinery, relies on that flow to complete the OAuth handoff, uses `urlencode` to safely place the account name in a URL, creates a `PageLink` for the connectors screen when possible, and hands everything to `callback_page` to produce the final HTML response.

*Call graph*: 5 external calls (__init__, HTTPException, installed_connect_flow, callback_page, urlencode).


##### `connect_logo`  (lines 109–117)

```
async def connect_logo() -> Response
```

**Purpose**: Serves the UFO logo used by the callback page. This matters because the callback page may be loaded without a normal frontend app or session, so it needs a stable logo URL from the core service itself.

**Data flow**: It reads the SVG logo file from disk, wraps those bytes in an HTTP response, marks the media type as `image/svg+xml`, and adds a long cache header so browsers can keep the file for a long time. The output is the raw logo image response.

**Call relations**: FastAPI calls this function when the browser requests `/v1/connect/logo.svg`. The function does not call into the connect flow; it simply reads the local asset and returns it through FastAPI’s `Response` object so the callback page can display the mark reliably.

*Call graph*: 1 external calls (Response).


### `extensions/imessage/ufo_ext_imessage/tools.py`

`domain_logic` · `request handling`

This file is the “connect my phone” doorway for the iMessage integration. A shared iMessage line is not allowed to text a person until that person has first sent a message to it, so the tool creates a safe opt-in flow: reserve the member’s phone number, assign a shared line, generate a short code, and tell the member exactly what to text.

The flow starts by accepting a phone number and normalizing it into a strict US E.164 format, which is the international-style form like +14155550123. Then the main tool checks whether this deployment has iMessage provider credentials. If the workspace has not yet been bound to that provider, only an admin is allowed to connect it.

Next, the tool reserves the phone number for the current speaker. If the number already belongs to someone else, it refuses. If the phone is already linked, it simply confirms the connection and says which assigned line to text. Otherwise it stores a pending claim with an assigned line and a random opt-in code. It also creates a QR code so someone using this from a desktop can scan it with their phone instead of typing. The result returned to the caller is marked untrusted because it is user-facing instruction data, not internal authority.

#### Function details

##### `opt_in_link`  (lines 37–41)

```
def opt_in_link(assigned_phone_number: str, opt_in_code: str) -> str
```

**Purpose**: Builds a phone-friendly link that opens the Messages app with the opt-in text already filled in. This helps the member send the required first message without copying the code by hand.

**Data flow**: It receives the assigned shared phone number and the opt-in code. It combines them with the standard opt-in phrase, safely encodes the message text for use inside a link, and returns an sms: link that can open a prefilled text message.

**Call relations**: This is used when the pending opt-in response is built. The result-producing helper includes the link alongside the written instructions so the user has an easier way to start the required text.

*Call graph*: called by 1 (_opt_in_result); 1 external calls (quote).


##### `opt_in_qr`  (lines 44–52)

```
def opt_in_qr(assigned_phone_number: str, opt_in_code: str) -> bytes
```

**Purpose**: Creates a QR code image for the same opt-in message. This is useful when the user is reading instructions on a computer but needs to send the message from their phone.

**Data flow**: It receives the assigned shared phone number and opt-in code. It builds a QR payload that phone cameras understand as a text-message shortcut, draws it as a PNG image in memory, and returns the image bytes.

**Call relations**: The main connection tool calls this after it has a pending claim. It then shares the image as an artifact so the member can scan it during the connection process.

*Call graph*: called by 1 (run); 2 external calls (BytesIO, make).


##### `_display_phone`  (lines 59–63)

```
def _display_phone(phone_number: str) -> str
```

**Purpose**: Turns a US phone number into a friendlier format for instructions, such as changing +14155550123 into (415) 555-0123. If the number is not in the expected US shape, it leaves it unchanged rather than guessing.

**Data flow**: It receives a phone number string. It checks whether it matches the expected +1 US phone pattern, and if so rearranges the digits into a readable display form; otherwise it returns the original string.

**Call relations**: Both the main tool and the opt-in response helper use this when writing messages for humans. It keeps user-facing instructions readable without changing the actual stored or provider-facing phone number.

*Call graph*: called by 2 (run, _opt_in_result).


##### `ImessageConnectInput._e164`  (lines 74–86)

```
def _e164(cls, value: str) -> str
```

**Purpose**: Checks and cleans the phone number supplied to the tool. It accepts common formatting like spaces or dashes, but only allows valid 10-digit US phone numbers.

**Data flow**: It receives the raw phone number text from the tool input. It trims whitespace, rejects letters, removes punctuation and other non-digits, handles an optional leading US country code, verifies the result is a valid US number, and returns the normalized +1 form.

**Call relations**: This runs as part of input validation before the connection tool uses the phone number. That means the rest of the file can work with one consistent phone format instead of defending against many user-entered formats.


##### `_result`  (lines 89–95)

```
def _result(state: str, instruction: str, **extra: object) -> ToolResult
```

**Purpose**: Packages a tool response in the standard shape expected by the UFO tool system. It returns a small JSON message with a state, an instruction, and any extra details the caller needs.

**Data flow**: It receives a state label, a human instruction, and optional extra fields. It turns that information into JSON text, wraps it as text content, marks it as untrusted user-facing output, and returns a ToolResult.

**Call relations**: The main connection flow uses this for every outcome: missing provider setup, admin-required setup, phone already taken, successful connection, and changed pending state. The opt-in response helper also uses it so pending responses have the same format as all other responses.

*Call graph*: called by 2 (run, _opt_in_result); 3 external calls (__init__, __init__, dumps).


##### `_opt_in_result`  (lines 98–107)

```
def _opt_in_result(assigned_phone_number: str, opt_in_code: str) -> ToolResult
```

**Purpose**: Builds the response shown when the phone connection is waiting for the member to send the opt-in text. It tells the member what to send, where to send it, and includes a shortcut link.

**Data flow**: It receives the assigned shared phone number and opt-in code. It creates the exact message text, formats the assigned phone number for readability, builds the sms link, and returns a standardized pending ToolResult with those details included.

**Call relations**: The main connection tool calls this after it has stored or reused a pending phone claim and shared the QR code. This helper gathers the final user instructions into the common result format.

*Call graph*: calls 3 internal fn (_display_phone, _result, opt_in_link); called by 1 (run).


##### `ImessageConnect.run`  (lines 114–166)

```
async def run(self, ctx: ToolContext, args: ImessageConnectInput) -> ToolResult
```

**Purpose**: Runs the full “connect this phone to iMessage” workflow for one member. It checks provider setup, permissions, phone ownership, pending claims, line assignment, QR-code sharing, and the final instruction returned to the user.

**Data flow**: It receives the tool context and the validated input phone number. It identifies the speaker, creates or checks the iMessage provider connection, binds the workspace if an admin is setting it up, reserves the requested phone number, assigns a shared line when needed, stores a pending claim with a random code, shares a QR code artifact, and returns a clear state such as not_connected, pending, or connected.

**Call relations**: This is the central flow that calls the smaller helpers when it needs them: it uses result helpers for user-facing responses, phone display formatting for readable messages, and QR creation for the scan-to-text shortcut. It also calls into the surrounding tool context, installation store, claim store, and provider so the in-memory request becomes a real workspace/provider connection.

*Call graph*: calls 7 internal fn (require_speaker, share_artifact, speaker_is_admin, _display_phone, _opt_in_result, _result, opt_in_qr); 6 external calls (__init__, now, choice, claim_key, read_claim, uuid4).


### `extensions/web/ufo_ext_web/anthropic_login.py`

`domain_logic` · `request handling`

This file is the bridge between UFO and Anthropic login. Its job is to make sure that when a member adds an Anthropic credential, the value is real and belongs to a flow Anthropic accepts. Without this file, UFO could not guide users through Anthropic authorization, exchange the code for a token, or reject broken keys before they enter the credential store.

There are two main paths. If UFO has an Anthropic OAuth client ID, `AnthropicCodeLogin` creates a special Anthropic authorization link. The user opens that link, approves access, and Anthropic shows them a code. Unlike many web logins, Anthropic does not redirect back to this server with the code. The user is the messenger: they paste the code back into UFO. The file then checks that the pasted code matches the browser’s saved verifier and asks Anthropic to exchange it for an access token.

The helper `_split_pasted` is forgiving about what the user pastes. It accepts just the code, a `code#state` pair, or a full callback URL. This matters because users often copy different parts of the page.

Finally, `verified_key` tests any resulting token or manually entered API key by asking Anthropic for the model list. If Anthropic says no, UFO refuses to store it.

#### Function details

##### `AnthropicCodeLogin.authorize`  (lines 73–96)

```
def authorize(self) -> PendingAuthorization
```

**Purpose**: This starts an Anthropic OAuth authorization attempt. It creates a secure one-time verifier, builds the Anthropic approval URL, and returns both the URL the user should open and the cookie value UFO must remember.

**Data flow**: It starts with the configured Anthropic client ID and URLs. It creates random bytes, turns them into a safe text verifier, hashes that verifier into a challenge, and puts the challenge plus OAuth details into a web link. The result is a `PendingAuthorization` containing the Anthropic URL for the browser and the verifier to keep in the user’s cookie.

**Call relations**: This is used at the beginning of the sign-in story, when UFO needs to send the member to Anthropic. It relies on standard encoding, hashing, randomness, and URL-building tools, then hands back a `PendingAuthorization` that later makes `AnthropicCodeLogin.claim` able to prove the pasted code belongs to the same browser session.

*Call graph*: 5 external calls (__init__, urlsafe_b64encode, sha256, token_bytes, urlencode).


##### `AnthropicCodeLogin.claim`  (lines 98–132)

```
async def claim(self, pasted: str, verifier: str) -> Grant | None
```

**Purpose**: This redeems the code the user pasted after approving Anthropic access. If the code and verifier check out, it asks Anthropic for an access token and returns that token packaged as a grant.

**Data flow**: It receives the pasted text from the user and the verifier saved in the browser cookie. It first rejects missing verifier data, then uses `_split_pasted` to pull out the authorization code and any returned state value. If a returned state is present, it compares it safely with the saved verifier. Then it sends the code, verifier, client ID, and redirect URI to Anthropic’s token endpoint. If Anthropic replies successfully and the response has the expected shape, the function returns a `Grant`; otherwise it returns `None`.

**Call relations**: This is the second half of the OAuth sign-in flow started by `AnthropicCodeLogin.authorize`. It calls `_split_pasted` because users may paste the code in several formats. It then talks to Anthropic over HTTP and passes the successful JSON response to `granted`, which turns Anthropic’s answer into UFO’s internal representation of an issued credential.

*Call graph*: calls 1 internal fn (_split_pasted); 4 external calls (AsyncClient, dumps, compare_digest, granted).


##### `_split_pasted`  (lines 135–147)

```
def _split_pasted(pasted: str) -> tuple[str, str | None]
```

**Purpose**: This reads the authorization code out of whatever the user copied from Anthropic. It makes the login form tolerant of real user behavior instead of requiring one exact paste format.

**Data flow**: It receives a pasted string and trims extra spaces. If the string looks like a full URL, it reads both the URL query part and the part after `#`, then extracts `code` and `state` values. If the string contains `#` but is not a URL, it treats it as `code#state`. Otherwise, it treats the whole trimmed string as the code and says there was no state value. It returns the code plus either the state or `None`.

**Call relations**: This helper is called by `AnthropicCodeLogin.claim` before any token exchange happens. Its output tells `claim` what code to send to Anthropic and whether there is a returned state value that must be checked against the browser’s saved verifier.

*Call graph*: called by 1 (claim); 2 external calls (parse_qs, urlsplit).


##### `verified_key`  (lines 150–164)

```
async def verified_key(credential: str) -> bool
```

**Purpose**: This checks whether an Anthropic credential actually works before UFO stores it. It prevents dead, mistyped, or unauthorized tokens from becoming saved user credentials.

**Data flow**: It receives a credential string. If the credential looks like an Anthropic OAuth access token, it sends it as a bearer token and includes Anthropic’s OAuth beta header. Otherwise, it sends the value as a normal Anthropic API key. It asks Anthropic’s models endpoint for a model list. If Anthropic returns success, the function returns `true`; if the request fails or Anthropic rejects it, it returns `false`.

**Call relations**: This function is used after a user supplies or obtains an Anthropic credential, before that credential is trusted. It does not call the OAuth flow; instead, it performs a practical final test against Anthropic itself, acting like a door checker before the value reaches the credential store.

*Call graph*: 1 external calls (AsyncClient).


### `extensions/web/ufo_ext_web/openai_login.py`

`io_transport` · `sign-in request handling`

This file solves a practical sign-in problem: the portal needs each member to bring their own OpenAI access, but the app cannot simply ask them to paste a password. Instead, it uses a device sign-in flow, similar to how a TV asks you to visit a website and type a short code. First, the code asks OpenAI for a temporary user code. The portal can show that code and a verification link to the member. Then the app keeps checking OpenAI to see whether the member has approved the login. While the member is still deciding, the file reports “pending” rather than treating it as a failure. Once OpenAI says the login was approved, this file exchanges the approval for a token and checks that the token really belongs to a ChatGPT account. If anything is missing, malformed, or rejected, it returns a clear refused state instead of pretending the sign-in worked. The main class, OpenAiDeviceLogin, is the worker for this whole flow. Small result objects, DeviceAuthorization and DeviceClaim, carry the important facts between the web page and the polling step: the code to show, the polling delay, whether the login is still pending, and the final key if one was granted.

#### Function details

##### `OpenAiDeviceLogin.request_code`  (lines 85–103)

```
async def request_code(self) -> DeviceAuthorization | None
```

**Purpose**: This starts the OpenAI device sign-in by asking OpenAI for a one-time code the member can type into OpenAI’s verification page. It returns the code and related details if OpenAI starts the flow successfully, or nothing if the request fails.

**Data flow**: It starts with the configured OpenAI client ID and user-code endpoint. It sends those to OpenAI over HTTP, reads the JSON reply, checks that the required device ID and user code are present, turns the polling interval into a usable number, and returns a DeviceAuthorization object. If the network request fails, OpenAI returns a non-success status, or the response is missing the expected fields, it returns None.

**Call relations**: This is the first step in the login story, called when the portal needs to draw the sign-in instructions. It uses _interval to safely interpret OpenAI’s suggested wait time, then hands the resulting DeviceAuthorization back to the web layer so the member can see the code.

*Call graph*: calls 1 internal fn (_interval); 2 external calls (__init__, AsyncClient).


##### `OpenAiDeviceLogin.claim`  (lines 105–126)

```
async def claim(self, device_auth_id: str, user_code: str) -> DeviceClaim
```

**Purpose**: This checks whether the member has approved the device sign-in and, if so, continues toward getting the final stored key. It separates “not approved yet” from a real refusal, so the portal can keep polling without alarming the user.

**Data flow**: It receives the device authorization ID and user code from the earlier step. It sends them to OpenAI’s device token endpoint. If OpenAI says the approval is still pending, it returns a pending DeviceClaim. If OpenAI returns an authorization code and verifier, it passes those to _redeem to exchange them for the real token. If the response is missing those required values, it returns a refused DeviceClaim with an explanatory message.

**Call relations**: This is the polling step after request_code. When OpenAI has not approved the login yet, it asks _unapproved to translate the response into pending or refused. When approval data is present, it hands off to _redeem, which performs the final exchange.

*Call graph*: calls 2 internal fn (_redeem, _unapproved); 2 external calls (__init__, AsyncClient).


##### `OpenAiDeviceLogin._redeem`  (lines 128–153)

```
async def _redeem(self, client: httpx.AsyncClient, code: str, verifier: str) -> DeviceClaim
```

**Purpose**: This performs the final exchange after the member approves the login: it trades OpenAI’s temporary authorization code for a usable ChatGPT account token. It also verifies that the returned token is one this system can actually use.

**Data flow**: It receives an HTTP client, an authorization code, and a code verifier. It posts a form to OpenAI’s token endpoint with the client ID, redirect URI, and grant details. It then parses the reply into the project’s token model, checks that the token contains a ChatGPT account ID, and returns a granted DeviceClaim with the stored key string. If the HTTP request fails, the response is not successful, the token cannot be parsed, or the token lacks an account ID, it returns a refused DeviceClaim.

**Call relations**: This is only reached from claim, after OpenAI has confirmed that the member approved the sign-in. It relies on the shared SDK helpers granted and chatgpt_account_id to validate the token before giving the key back to the rest of the login flow.

*Call graph*: called by 1 (claim); 4 external calls (__init__, post, chatgpt_account_id, granted).


##### `_interval`  (lines 156–162)

```
def _interval(raw: object) -> int
```

**Purpose**: This turns OpenAI’s suggested polling interval into a safe integer number of seconds. It prevents the app from polling too aggressively if OpenAI sends a missing or unreadable value.

**Data flow**: It receives any raw value from OpenAI’s response. It tries to convert that value into an integer after trimming it as text. If conversion works, that number comes out; if not, the default polling interval is returned.

**Call relations**: request_code calls this while building the DeviceAuthorization object. Its result tells the web side how long to wait between checks during the device sign-in flow.

*Call graph*: called by 1 (request_code).


##### `_unapproved`  (lines 165–177)

```
def _unapproved(polled: httpx.Response) -> DeviceClaim
```

**Purpose**: This interprets OpenAI poll responses that are not ordinary success responses. It decides whether the member is simply still approving the login, or whether the sign-in has truly failed.

**Data flow**: It receives an HTTP response from a poll attempt. It first treats known waiting statuses as pending. If needed, it reads the response JSON and looks for known OpenAI pending error codes. If those are found, it returns a pending DeviceClaim. Otherwise, it returns a refused DeviceClaim saying the device sign-in could not be started or continued.

**Call relations**: claim calls this whenever OpenAI’s polling endpoint does not return a 200 success response. It shields the rest of the login flow from OpenAI’s different “still waiting” shapes and gives claim a simple pending-or-refused answer.

*Call graph*: called by 1 (claim); 2 external calls (__init__, json).

## 📊 State Registers Touched

- `reg-persistence-handles` — The shared database and blob-storage connections used to read and save durable system data.
- `reg-tool-catalog` — The shared list of tools and actions agents may ask to run, including extension tools.
- `reg-workspace-directory` — The shared record of workspaces, members, seats, admins, invitations, and onboarding status.
- `reg-member-session-auth` — The signed tokens and browser/session identity state that prove who is making a request.
- `reg-runtime-authority` — The current workspace, agent, and member identity under which work is allowed to act.
- `reg-credential-connections` — The encrypted outside-account credentials, reusable connections, and grants that let agents use them.
- `reg-agent-registry` — The saved agents, their owners, visibility, model choices, tool policies, and sandbox settings.
- `reg-surface-routing-state` — The saved routing state for web, Slack, iMessage, terminal, and other public conversation surfaces.
- `reg-credential-request-state` — Pending and fulfilled credential-connection requests, OAuth/device-code callback context, and idempotency markers for credential fulfillment.
- `reg-transcript-access-audit` — Durable audit records of privileged/admin reads of private member transcripts for compliance and safety review.

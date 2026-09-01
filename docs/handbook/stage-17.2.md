# Network, Secret, and Spend Enforcement  `stage-17.2` (cross-cutting infrastructure)

This stage is the system’s safety and money gatekeeper during normal work. Before an agent calls an outside service or spends paid model tokens, it checks which workspace and agent are active, so secrets and charges stay with the right customer. Workspace and agent scope provide that identity boundary. Grants and connector access let members connect accounts like OpenAI, Anthropic, Gmail, GitHub, Composio, or Pipedream, refresh expiring tokens, and allow only chosen agents to use them. Credential handling encrypts secrets and chooses the right provider host or key without exposing it.

For network access, the egress rule and resolver code turns a run’s signed token, model choice, extensions, stored credentials, and grants into concrete “allowed hosts and secrets” rules. The egress control API is the private desk the Rust proxy calls to ask what traffic is allowed, fetch safe credential injections, and report usage.

Billing files price token use, track prepaid micro-dollar balances, enforce limits, debit accounts, roll up reports, and export billing data. The Metronome extension connects those records to Metronome, Stripe, admin chat tools, and billing pages. Package files only make these modules importable.

## Files in this stage

### Workspace and Agent Identity
Establishes the workspace and agent boundaries that keep secrets, permissions, and charges attached to the correct customer context.

### `core/src/ufo/runtime/workspace.py`

`domain_logic` · `cross-cutting during request handling and background jobs`

A workspace is the project or customer area that a request or background job is running for. This file makes the workspace an “ambient” setting: code enters `with ws(workspace_id):`, and anything inside can call `ws_current()` instead of passing the workspace ID through every function. That is like putting a colored wristband on everyone entering an event, so each purchase and permission check knows which event they belong to.

The file uses that workspace identity for two important jobs. First, it finds secrets such as model provider API keys. It checks for a stored key owned by the workspace, or in some cases a connected member’s own key, and only falls back to platform environment variables if no stored key exists. This prevents one workspace from silently using another workspace’s credentials.

Second, it records model usage for billing. Code opens a `billable_event()` block, reports usage as provider calls finish, and the charges are written when the block exits. Even if later processing fails, already-reported provider usage is still recorded.

The file also supports short-lived connected-account grants. If a member’s provider token is spent, the code refreshes it carefully so that only one caller refreshes it while others wait, avoiding accidental token reuse.

#### Function details

##### `speaker`  (lines 52–63)

```
def speaker(member_id: UUID | None, models: frozenset[str]=frozenset()) -> Iterator[None]
```

**Purpose**: Temporarily says which member is currently “speaking” for certain model calls. This matters when a member connected their own provider account, because only the model calls covered by that binding should use the member’s personal key.

**Data flow**: It receives an optional member ID and a set of model names. Before the block runs, it stores that pairing in task-local context; after the block finishes, it restores the previous value. Nothing is returned, but credential lookups inside the block can see the temporary speaker setting.

**Call relations**: This is set around code that wants member-routed model calls. Later, workspace credential lookup uses the stored speaker information through `_slot_order` to decide whether to try a member-specific credential before the workspace credential.


##### `init_workspace_credentials`  (lines 69–73)

```
def init_workspace_credentials(store: CredentialStore | None) -> None
```

**Purpose**: Installs the credential store that workspace code will use to read and write stored secrets. If no store is installed, the system can still use platform environment variables, but it cannot read workspace-owned stored credentials.

**Data flow**: It receives either a credential store object or `None`. It saves that value in this module’s global `_store`. Future credential operations read that saved store to decide whether stored workspace credentials are available.

**Call relations**: This is meant to be called during startup. After that, methods such as `WorkspaceScope.credential`, `WorkspaceScope.model_credential`, `WorkspaceScope.put_credential`, and related checks rely on the installed store.


##### `BillableEvent.usage`  (lines 89–99)

```
def usage(self, model: str, usage: Usage, pricing: Pricing=CORE_PRICING, byok: bool=False) -> None
```

**Purpose**: Adds one provider usage report to a billable event. Code uses it when a model provider has reported metered usage, such as tokens consumed, and that usage must later be written to the workspace ledger.

**Data flow**: It receives the model name, the usage numbers, a pricing table, and a flag saying whether the call used a bring-your-own-key credential. It appends that bundle to the event’s internal list. It returns nothing; the stored list is later consumed when the billing block exits.

**Call relations**: This is used inside a `WorkspaceScope.billable_event` block. The billing context creates the `BillableEvent`, callers add usage to it, and the context writes each collected item through the billing recorder at the end.


##### `WorkspaceScope.credential`  (lines 115–137)

```
async def credential(self, slot: str, env: str | None=None, model: str | None=None) -> str
```

**Purpose**: Fetches a secret for the current workspace, such as an API key. It always looks in the context of a specific workspace, so code cannot fetch a credential without saying which workspace owns the work.

**Data flow**: It receives a credential slot name, an optional environment variable name, and optionally a model name. It first asks `_slot_order` which stored credential names to try, which may include a member-specific slot. If a configured store has a value, it returns that. If not, it reads a platform environment variable through `deploy_env`. If no value exists anywhere, it raises `CredentialSlotUnset` instead of returning an empty or wrong key.

**Call relations**: Callers use this when they need a normal secret. It delegates slot choice to `_slot_order`, uses the credential store if one was installed, and falls back to deployment environment variables. Errors are raised loudly so higher-level model or service calls do not proceed without a real credential.

*Call graph*: calls 1 internal fn (_slot_order); 2 external calls (__init__, deploy_env).


##### `WorkspaceScope.model_credential`  (lines 139–159)

```
async def model_credential(self, slot: str, env: str | None, model: str) -> str
```

**Purpose**: Fetches the credential that should pay for a model call. Unlike `credential`, it understands connected-account grants, which can expire or be spent and may need refreshing before use.

**Data flow**: It receives a slot, an optional environment variable name, and the model being called. It tries the relevant stored slots from `_slot_order`. If the stored value is a plain key, it returns it. If the value is a grant, it returns the grant’s access value when still usable, or calls `_refreshed_access` when the grant has been spent. If no stored value exists, it falls back to the platform environment; if that is missing, it raises `CredentialSlotUnset`.

**Call relations**: Model-calling code uses this right before contacting a provider. It relies on `read_grant` to tell plain keys from refreshable grants, and hands spent grants to `_refreshed_access` so provider calls get a usable token without several callers refreshing the same grant at once.

*Call graph*: calls 2 internal fn (_refreshed_access, _slot_order); 3 external calls (__init__, read_grant, deploy_env).


##### `WorkspaceScope._refreshed_access`  (lines 161–190)

```
async def _refreshed_access(self, store: 'CredentialStore', candidate: str, slot: str, stored: str, grant: Grant) -> str
```

**Purpose**: Refreshes a spent connected-account grant safely. Its main job is to ensure that, even if several model calls notice the spent grant at the same time, only one of them performs the refresh while the others wait for the new credential.

**Data flow**: It receives the credential store, the stored slot name, the original slot, the stored grant text, and the parsed grant. It tries to claim a short refresh lease by rotating the stored value to a “refreshing” version. If it wins, it asks the provider for a refreshed grant and stores the new pair. If it does not win, it sleeps briefly, rereads the stored value, and returns the newly available access token when another caller has finished. If no refresh succeeds in time, it raises `GrantRefusedRefresh`.

**Call relations**: Only `WorkspaceScope.model_credential` calls this, and only for a spent grant. It uses grant parsing, time-based leases, store rotation, and the provider refresh helper to turn an unusable grant into a usable access token without holding a database transaction open during the provider call.

*Call graph*: calls 1 internal fn (__init__); called by 1 (model_credential); 5 external calls (sleep, model_copy, time, read_grant, refreshed).


##### `WorkspaceScope.member_routed_call`  (lines 192–195)

```
def member_routed_call(self, slot: str, model: str) -> bool
```

**Purpose**: Answers whether a particular model call will use the speaking member’s own credential before the workspace credential. This helps the caller know whether the call is routed to a member-owned account.

**Data flow**: It receives a credential slot and a model name. It asks `_slot_order` what credentials would be tried. If that list contains more than the normal workspace slot, the call is member-routed; otherwise it is not.

**Call relations**: This is a small question built on the same routing rule used by actual credential lookup. By calling `_slot_order`, it stays consistent with `credential`, `model_credential`, and `credential_is_stored`.

*Call graph*: calls 1 internal fn (_slot_order).


##### `WorkspaceScope._slot_order`  (lines 197–204)

```
def _slot_order(self, slot: str, model: str | None) -> list[str]
```

**Purpose**: Decides which stored credential names should be tried, and in what order. It is the rule that prevents a member’s personal account from being used except for the exact model calls they are meant to cover.

**Data flow**: It receives a base slot name and optionally a model name. It reads the current speaker binding. If the slot is not one of the member-routable provider slots, or there is no speaker, or the model is not covered, it returns just the normal workspace slot. If the member should pay for that model, it returns the member-specific slot first and the workspace slot second.

**Call relations**: Credential-reading functions call this before touching stored secrets. `credential`, `model_credential`, `credential_is_stored`, and `member_routed_call` all depend on it so that checks and real credential reads follow the same routing rule.

*Call graph*: called by 4 (credential, credential_is_stored, member_routed_call, model_credential); 1 external calls (member_slot).


##### `WorkspaceScope.member_holds_own_model_key`  (lines 206–209)

```
async def member_holds_own_model_key(self, member_id: UUID | None) -> bool
```

**Purpose**: Checks whether a member has connected any supported model provider account of their own. This is useful when deciding whether work can run on that member’s personal provider connection.

**Data flow**: It receives an optional member ID. It asks `member_model_provider` which provider, if any, that member has connected. It returns `true` if a provider is found and `false` otherwise.

**Call relations**: This is a simpler yes-or-no wrapper around `member_model_provider`. Code that does not care which provider was connected can call this instead of inspecting the provider name.

*Call graph*: calls 1 internal fn (member_model_provider).


##### `WorkspaceScope.member_model_provider`  (lines 211–224)

```
async def member_model_provider(self, member_id: UUID | None) -> str | None
```

**Purpose**: Finds which supported model provider account a member has connected for this workspace. It deliberately checks the member’s own stored credentials only, not workspace keys or platform defaults.

**Data flow**: It receives an optional member ID. If there is no credential store or no member ID, it returns `None`. Otherwise it tries each member-specific provider slot in declaration order. The first stored credential found determines the provider name returned. If none are found, it returns `None`.

**Call relations**: This supports `member_holds_own_model_key` and any logic that needs to know the member’s connected provider. It uses `member_slot` to form member-specific credential names and the credential store to test whether those values exist.

*Call graph*: called by 1 (member_holds_own_model_key); 1 external calls (member_slot).


##### `WorkspaceScope.credential_is_stored`  (lines 226–240)

```
async def credential_is_stored(self, slot: str, model: str | None=None) -> bool
```

**Purpose**: Checks whether a credential is stored for this workspace or routed member, rather than coming from the platform’s environment variables. This matters for billing, because usage paid with a workspace-owned or member-owned key may not be charged the same way as platform-paid usage.

**Data flow**: It receives a slot and optionally a model name. If no credential store is installed, it returns `false`. Otherwise it asks `_slot_order` which stored slots apply, tries to read each one, and returns `true` as soon as a stored value is found. If none are found, it returns `false`.

**Call relations**: This uses the same routing rule as actual credential fetching, so its answer matches what `credential` or `model_credential` would have used. Billing-related code can use it to decide whether the provider call used a stored customer or member credential.

*Call graph*: calls 1 internal fn (_slot_order).


##### `WorkspaceScope.rotate_credential`  (lines 242–247)

```
async def rotate_credential(self, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Replaces an existing stored workspace credential only if it still has the expected old value. This protects against overwriting someone else’s update when two operations happen close together.

**Data flow**: It receives the slot to update, the expected current plaintext value, and the new plaintext value. If there is no credential store, it returns `false`. Otherwise it asks the store to perform the conditional rotation for this workspace and returns whether that rotation succeeded.

**Call relations**: This is used when stored credentials need to be updated safely. It relies on the configured credential store; platform environment defaults are not changed by this function.


##### `WorkspaceScope.put_credential`  (lines 249–253)

```
async def put_credential(self, slot: str, plaintext: str) -> None
```

**Purpose**: Stores a new credential for the current workspace. It is used when an authorized owner or setup flow is adding the initial secret value.

**Data flow**: It receives a slot name and the plaintext secret to store. If no credential store is configured, it raises an error because there is nowhere safe to save the secret. Otherwise it writes the secret under this workspace’s ID.

**Call relations**: Setup or credential-management flows call this after a workspace has been bound. It hands the actual storage work to the installed credential store while ensuring the write is tied to the current workspace.


##### `WorkspaceScope.billable_event`  (lines 256–268)

```
async def billable_event(self) -> AsyncIterator[BillableEvent]
```

**Purpose**: Creates a block where provider usage can be collected and then written to the workspace’s billing ledger when the block exits. This makes sure usage is recorded even if later code cannot use the model output.

**Data flow**: It creates a fresh `BillableEvent` and yields it to the caller. The caller adds usage entries during the block. When the block finishes, it opens a workspace database transaction and writes each collected usage item with the workspace ID, model, usage details, pricing, and bring-your-own-key flag. It returns no normal value beyond the temporary event object.

**Call relations**: Model-calling code wraps provider work in this context and calls `BillableEvent.usage` as usage arrives. On exit, this function hands each item to `record_workspace_usage` inside `workspace_tx`, connecting provider usage to the same workspace scope.

*Call graph*: 3 external calls (__init__, workspace_tx, record_workspace_usage).


##### `ws`  (lines 272–280)

```
def ws(workspace_id: UUID) -> Iterator[WorkspaceScope]
```

**Purpose**: Temporarily binds a workspace ID for a block of work. This is the boundary that makes later calls to `ws_current`, credential lookup, billing, and workspace-scoped database access point at the right workspace.

**Data flow**: It receives a workspace UUID. Before the block starts, it stores that UUID in the current workspace context and yields a `WorkspaceScope` for it. After the block finishes, it resets the context to its previous value.

**Call relations**: Request handlers or background job runners use this at the start of workspace-specific work. Inside the block, `ws_current` can recover the scope, and database helpers that read the same context can pin their operations to the correct workspace.

*Call graph*: 3 external calls (__init__, reset, set).


##### `ws_current`  (lines 283–289)

```
def ws_current() -> WorkspaceScope
```

**Purpose**: Returns the currently bound workspace scope. If no workspace has been bound, it raises an error instead of guessing, which prevents accidental cross-workspace credential reads or unbilled provider calls.

**Data flow**: It reads the current workspace ID from context. If a UUID is present, it wraps it in a `WorkspaceScope` and returns it. If the value is missing, it raises `WorkspaceUnbound` with a message telling the caller to use `with ws(workspace_id):`.

**Call relations**: Code that needs credentials, billing, or workspace identity calls this after a boundary has used `ws`. It is the safe access point that turns the ambient workspace setting into the `WorkspaceScope` methods used throughout the runtime.

*Call graph*: 3 external calls (__init__, __init__, get).


### `core/src/ufo/runtime/agent_scope.py`

`domain_logic` · `cross-cutting during agent-owned work`

Some parts of the system need to know, “Which agent is doing this?” This file provides that answer without passing the agent ID through every function call. It uses a context variable, which is like a thread- and task-safe sticky note attached to the current flow of work: code running inside that flow can read it, but unrelated work does not share it.

The main idea is an agent scope. An AgentScope records two things together: the workspace ID and the agent ID. Tying them together matters because an agent identity is only valid inside the workspace where it was bound. The agent(...) context manager creates this scope from the current workspace and a given agent ID, then makes it available until the with-block ends. It also refuses to switch to a different agent while one is already bound, which prevents confusing identity changes halfway through an operation.

Code that needs the current agent calls agent_current(). If no agent has been bound, it raises AgentUnbound with a clear message. If the workspace has changed underneath the bound agent, it raises an error too. Without this file, agent-owned capabilities could be used with no clear owner, or worse, with an owner from the wrong workspace.

#### Function details

##### `agent`  (lines 28–38)

```
def agent(agent_id: UUID) -> Iterator[AgentScope]
```

**Purpose**: This function creates a temporary boundary where one specific agent is considered the current agent. Use it around code that must run with a known agent identity.

**Data flow**: It takes an agent ID as input and reads the current workspace from ws_current(). It combines those into an AgentScope, checks whether another different agent is already bound, and if not, stores the new scope in the current context. It yields that scope to the caller, then restores the previous context when the with-block finishes.

**Call relations**: This is the entry door for agent-scoped work. When someone writes code inside `with agent(agent_id):`, this function first asks the workspace layer for the active workspace using ws_current(), then builds an AgentScope for that workspace and agent. Later calls to agent_current() rely on the scope this function placed in the context.

*Call graph*: 2 external calls (__init__, ws_current).


##### `agent_current`  (lines 41–48)

```
def agent_current() -> AgentScope
```

**Purpose**: This function returns the agent scope currently bound to the running work. It is used by code that needs to know which agent owns an operation, and it fails loudly if there is no safe answer.

**Data flow**: It reads the current stored AgentScope from the context. If there is none, it raises AgentUnbound to tell the caller that the code must be wrapped in `with agent(agent_id):`. If a scope exists, it checks the current workspace with ws_current(); if the workspace does not match the one stored with the agent, it raises an error. Otherwise, it returns the AgentScope.

**Call relations**: This function is the lookup side of the flow started by agent(). Code running inside an agent boundary calls it to recover the bound identity. To make sure that identity is still valid, it asks the workspace layer for the current workspace before handing the scope back.

*Call graph*: 2 external calls (__init__, ws_current).


### Connector Grants and Secrets
Manages connected outside accounts, connector authorization, and encrypted workspace credentials without exposing sensitive tokens to agents or sandboxes.

### `core/src/ufo/harness/models/grant.py`

`domain_logic` · `cross-cutting during account connection, request handling, and token refresh`

When a member connects a provider account, the system receives an access token, a refresh token, and an expiry time. The access token is what API calls use right now. The refresh token is like a renewal ticket: it can be exchanged later for a fresh access token. Without this file, a connected account could look valid in storage while silently becoming unusable after the access token expires.

The main model is Grant. It stores the current access token, the refresh token that can buy the next one, and the time when the access token stops working. It also records whether a refresh is already being attempted, so two callers do not both spend the same one-time refresh token at once.

The file knows the token exchange addresses for OpenAI and Anthropic, and it chooses the right public client ID for the deployment. It can read a stored grant from JSON, serialize a grant back to JSON, decide when a grant is close enough to expiry to count as “spent,” and call the provider’s token endpoint to refresh it. If the provider refuses, it raises GrantRefusedRefresh, meaning the member must reconnect the account.

#### Function details

##### `openai_client_id`  (lines 36–39)

```
def openai_client_id() -> str
```

**Purpose**: This chooses the client ID the deployment should present when refreshing or redeeming an OpenAI connected account. It lets a deployment use its own configured ID, while falling back to a known public OpenAI Codex client ID if none is set.

**Data flow**: It reads the UFO_OPENAI_OAUTH_CLIENT_ID environment variable. If that value exists, it returns it; otherwise it returns the built-in public OpenAI client ID. Nothing else is changed.

**Call relations**: The grant refresh setup keeps this function beside OpenAI’s token endpoint so refreshes use the same client identity that was used during sign-in. When a refresh is prepared for an OpenAI slot, this function supplies the client_id value sent to the provider.


##### `anthropic_client_id`  (lines 42–45)

```
def anthropic_client_id() -> str
```

**Purpose**: This chooses the client ID the deployment should present when refreshing or redeeming an Anthropic connected account. It supports a deployment-specific ID, with a built-in Claude public client ID as the fallback.

**Data flow**: It reads the UFO_ANTHROPIC_OAUTH_CLIENT_ID environment variable. If that value is present, it returns it; otherwise it returns the built-in Anthropic client ID. It does not modify any stored state.

**Call relations**: The grant refresh setup keeps this function paired with Anthropic’s token endpoint. When an Anthropic grant is refreshed, this function supplies the client_id value that is sent along with the refresh token.


##### `GrantRefusedRefresh.__init__`  (lines 71–73)

```
def __init__(self, slot: str) -> None
```

**Purpose**: This creates a clear error for the case where a provider will not exchange a refresh token for a new grant. It marks the connected account as something the system cannot fix automatically; the member needs to reconnect.

**Data flow**: It receives the slot name, such as the place where an OpenAI or Anthropic credential is stored. It builds a human-readable error message and stores the slot on the exception, so later code can know which connected account failed.

**Call relations**: refreshed raises this when the provider cannot be reached, returns a bad status, sends unreadable data, or omits required token fields. WorkspaceScope._refreshed_access also uses this error when a connected account cannot be refreshed while trying to provide a usable access token.

*Call graph*: called by 2 (refreshed, _refreshed_access).


##### `Grant.spent`  (lines 88–89)

```
def spent(self) -> bool
```

**Purpose**: This answers the practical question, “Is this access token too close to expiry to safely use?” It treats a token as spent several minutes before its exact expiry time so a long-running operation does not fail halfway through.

**Data flow**: It reads the grant’s expires_at time and compares it with the current clock time. If the current time is at or beyond the expiry time minus the safety margin, it returns true; otherwise it returns false. It does not change the grant.

**Call relations**: Other code can use this property before making provider calls to decide whether the grant should be refreshed first. Internally it relies only on the current time and the stored expiry timestamp.

*Call graph*: 1 external calls (time).


##### `Grant.claimed`  (lines 92–93)

```
def claimed(self) -> bool
```

**Purpose**: This answers whether another caller is already in the middle of refreshing this grant. It helps prevent two tasks from spending the same refresh token at the same time.

**Data flow**: It reads refreshing_until and compares it with the current clock time. If the current time is still before that deadline, it returns true; otherwise it returns false. The grant itself is not changed.

**Call relations**: Refresh coordination code can check this before attempting a refresh. The idea is like putting a temporary “occupied” sign on the account while one caller is renewing it.

*Call graph*: 1 external calls (time).


##### `Grant.stored`  (lines 95–96)

```
def stored(self) -> str
```

**Purpose**: This turns a Grant into the JSON text that can be saved in a credential slot. It preserves the full renewable account, not just the short-lived access token.

**Data flow**: It reads the Grant fields: access token, refresh token, expiry time, and refresh claim deadline. It serializes them into a JSON string and returns that string. It does not write to storage by itself.

**Call relations**: Code that updates a stored connected account can call this after a grant is first created or refreshed. It is the counterpart to read_grant, which turns stored JSON text back into a Grant.


##### `granted`  (lines 99–112)

```
def granted(payload: dict[str, object]) -> Grant | None
```

**Purpose**: This checks a provider’s token response and turns it into a Grant only if all required pieces are present. It refuses incomplete responses because an access token without a refresh token would work briefly and then leave the member disconnected.

**Data flow**: It receives a dictionary decoded from a token endpoint response. It looks for access_token, refresh_token, and expires_in, checks that they have usable types and values, then builds a Grant whose expiry time is the current time plus expires_in seconds. If anything important is missing or malformed, it returns null.

**Call relations**: refreshed calls this after receiving JSON from the provider. If granted returns a Grant, the refresh succeeded; if it returns null, refreshed turns that into GrantRefusedRefresh because the response cannot safely be stored as a renewable account.

*Call graph*: called by 1 (refreshed); 2 external calls (__init__, time).


##### `read_grant`  (lines 115–127)

```
def read_grant(stored: str) -> Grant | None
```

**Purpose**: This tries to interpret a stored credential string as a renewable Grant. If the string is just a plain API key or invalid JSON, it returns null instead of treating it as an expiring connected account.

**Data flow**: It receives a stored string. It first tries to parse it as JSON, then checks that the parsed value is an object, then asks the Grant model to validate the fields. A valid grant comes out as a Grant object; anything unreadable, non-object, or invalid returns null.

**Call relations**: This is used at the boundary between storage and runtime behavior. It lets the rest of the system tell the difference between old-style plain keys, which do not refresh, and connected-account grants, which can expire and be renewed.

*Call graph*: 1 external calls (loads).


##### `refreshed`  (lines 130–155)

```
async def refreshed(grant: Grant, slot: str) -> Grant
```

**Purpose**: This exchanges an existing grant’s refresh token for a brand-new grant from the correct provider. It is the function that keeps a member’s connected account alive after the original access token becomes stale.

**Data flow**: It receives the current Grant and the slot name that says which provider account this is. It looks up the provider’s token endpoint and client ID, sends an HTTP POST request containing the refresh token, then checks the response. A successful, complete response becomes a new Grant; network failures, non-200 responses, unreadable JSON, or missing token fields become GrantRefusedRefresh.

**Call relations**: When a grant needs renewal, this function performs the actual provider call. It asks the slot’s client-ID function for the right client identity, uses httpx.AsyncClient to send the request, hands the response body to granted for validation, and raises GrantRefusedRefresh whenever the refresh cannot produce a safe replacement grant.

*Call graph*: calls 2 internal fn (__init__, granted); 1 external calls (AsyncClient).


### `core/src/ufo/runtime/access/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells the language that the surrounding folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may contain useful tools, but the label itself does not do the work. Here, the drawer is `core/src/ufo/runtime/access`, which is likely meant to hold code related to runtime access behavior elsewhere in the project.

Because this file is empty, it does not define functions, classes, settings, or side effects. Nothing is run when it is imported except Python’s normal package-loading step. Its importance is structural: without it, some Python versions, tools, or packaging setups might not recognize this directory as a regular package, and imports using `ufo.runtime.access` could fail or behave differently.


### `core/src/ufo/runtime/access/connectors.py`

`domain_logic` · `cross-cutting: connector discovery, tool execution, feed sync authentication, and proxied provider requests`

This file is the project’s “customs desk” for external services. A connector may need to call a provider API, but the system must be very careful about who can see the secret that proves access. The file defines a small set of objects and interfaces for that job.

For feed syncing, a source asks an AuthProxy for a Credential. That credential has exactly one authentication route: a broker-owned transport that rewrites requests while keeping the token elsewhere, a bearer token read on the host, or provider-specific headers. Its printed form deliberately hides secrets, like a receipt that says “paid by card” but not the card number.

For dynamic connector tools, a ConnectorBroker describes available tools, gives their input shape, runs them, and deals with file handoff through short-lived URLs instead of moving bytes through the server. A ConnectorRegistry is the routing table. It knows which provider belongs to which broker, can ask an open resolver about providers discovered at runtime, and can fall back to direct credentials when a workspace supplied its own key.

The last part protects feed sources tied to a member-owned connection. Before returning or using brokered credentials, it checks the database to confirm the connection still belongs to the same workspace, member, provider, and account. If not, the source is stopped instead of silently using stale access.

#### Function details

##### `Credential.__repr__`  (lines 57–66)

```
def __repr__(self) -> str
```

**Purpose**: Returns a safe text version of a Credential without revealing any token, header value, or broker transport detail. This matters because objects sometimes appear in logs or error reports by accident.

**Data flow**: It reads which authentication path is present on the Credential: transport, bearer token, custom headers, or nothing. It turns that into a short redacted string such as “Credential(<bearer: redacted>)”. No stored data changes, and no secret is returned.

**Call relations**: This is used whenever Python needs a printable form of a Credential. It supports the larger connector flow by making accidental debugging output much less dangerous.


##### `AuthProxy.credential`  (lines 85–85)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Defines the common promise for anything that can turn a workspace, provider, and account handle into a usable Credential. Implementations may fetch direct keys or ask a broker to prepare a safe proxy route.

**Data flow**: The caller supplies a workspace ID, provider name, and account handle. The implementation looks up or prepares the right authentication route and returns a Credential. Because this is a protocol method, the file states the shape of the operation rather than doing the lookup itself.

**Call relations**: Feed sync code and SourceCredentialResolver use this interface so they do not need to know whether credentials come from a broker or from the workspace’s own credential store.


##### `GrantUnusable.__init__`  (lines 113–115)

```
def __init__(self, reason: str, *, awaits_grant: bool=False) -> None
```

**Purpose**: Creates an error for a connected account that cannot currently be used and may require the member to reconnect it. The optional flag records whether the system should wait for a reconnect event instead of repeatedly retrying.

**Data flow**: It receives a human-readable reason and an awaits_grant flag. It stores the reason as the exception message and saves the flag on the exception object. The output is a raised or raisable error carrying both the message and repair hint.

**Call relations**: Broker implementations in Composio and Pipedream raise this when their account checks show a grant is revoked, expired, unhealthy, or otherwise unusable. Higher-level sync code can then skip or park the affected stream instead of treating it like a temporary network fault.

*Call graph*: called by 4 (credential, _account, credential, _account).


##### `stale_grant_guidance`  (lines 118–125)

```
def stale_grant_guidance(provider: str) -> str
```

**Purpose**: Builds a clear error-message suffix for the case where a broker does not recognize an account grant. It tells the operator-facing reader that retrying will not help and the member should reconnect.

**Data flow**: It receives a provider name. It inserts that name into a guidance sentence and returns the sentence as text. It does not read or change any external state.

**Call relations**: Broker code can use this helper when reporting stale or unknown grants, keeping the repair instruction consistent across connector backends.


##### `ConnectorBroker.tools`  (lines 196–198)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Defines how a broker lists tools available for a provider, optionally filtered by a search query. These are the actions an agent-facing connector tool may later describe or execute.

**Data flow**: The caller provides workspace ID, provider name, and query text. The broker implementation searches its catalog and returns BrokerTool records. This protocol method specifies the contract; each broker supplies the actual catalog lookup.

**Call relations**: Dynamic connector discovery calls this kind of broker method when it needs to show which provider actions exist before one is selected.


##### `ConnectorBroker.schema`  (lines 200–200)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Defines how a broker returns the detailed input shape for one provider tool. The input shape tells the agent what arguments the tool expects.

**Data flow**: The caller gives workspace ID, provider name, and a tool slug, which is the broker’s short tool identifier. The implementation returns a BrokerTool including its input schema, or raises UnknownBrokerTool when the slug is not known.

**Call relations**: Tool-description flows use this after a candidate tool is chosen. It lets the agent build valid arguments without the core server needing provider-specific knowledge.


##### `ConnectorBroker.execute`  (lines 202–210)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Defines how a broker runs one provider tool under a connected account while keeping the provider token inside the broker. This is the core execution seam for brokered connector tools.

**Data flow**: The caller supplies workspace ID, provider, tool slug, argument values, account ID, and an optional idempotency key, which helps prevent duplicate side effects on retries. The broker runs the provider action and returns a dictionary response. Secrets do not come back through this result.

**Call relations**: Dynamic connector tools dispatch through this method after choosing a broker from the registry. The broker owns the actual provider call and token injection.


##### `ConnectorBroker.file_outputs`  (lines 212–212)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Defines how a broker extracts produced files from a tool response. It turns broker-specific response data into standard BrokerFile references.

**Data flow**: The method receives the raw dictionary response from a broker execution. The implementation inspects it and returns file names plus short-lived download URLs. It returns references, not file bytes.

**Call relations**: After a connector tool runs, the surrounding tool flow can call this so the sandbox can fetch output files directly through the allowed network path.


##### `ConnectorBroker.stage_upload`  (lines 214–222)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Defines how a broker prepares a workspace file for use as an input to a provider tool. Instead of routing file bytes through the server, it gives the sandbox a place to upload them.

**Data flow**: The caller provides workspace ID, provider, tool slug, filename, MIME type, and MD5 checksum. The broker returns a StagedUpload containing a PUT URL when upload is needed, the required content type, and the argument value to pass into the tool call. Some brokers may raise an error if they do not support this file-upload style.

**Call relations**: Connector tool execution uses this before calling execute when an argument points at a workspace file. The sandbox performs the actual upload, while the broker later reads the staged object.


##### `ConnectorBroker.search`  (lines 224–224)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Defines semantic search over a broker’s tools. Semantic search means searching by intent or meaning, not just exact names.

**Data flow**: The caller gives workspace ID, provider, and query text. The implementation returns matching tools and any plan, guidance, or warnings the broker can provide. The protocol only defines the expected exchange.

**Call relations**: Agent-facing discovery can use this when a user asks for a capability in natural language and the system needs to find the best matching connector tools.


##### `ConnectorBroker.credential`  (lines 226–226)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Defines how a broker produces a Credential for feed syncing under one connected account. Usually this is a broker transport that injects authentication outside the syncing process.

**Data flow**: The caller supplies workspace ID, provider, and account handle. The broker checks that the account is valid for that workspace and returns a Credential. If the grant is invalid, broker implementations may raise errors such as GrantUnusable.

**Call relations**: _credential calls this when a feed source uses a brokered account rather than direct workspace credentials. It keeps feed sync code independent of each broker’s account system.


##### `RequestForwarder.forward`  (lines 245–247)

```
async def forward(self, account_id: str, method: str, url: str, headers: Mapping[str, str], body: bytes) -> ForwardedResponse
```

**Purpose**: Defines how a captured provider HTTP request is sent through a broker under a connected account. The broker adds the real authentication, so the sandbox never sees the token.

**Data flow**: The caller provides account ID, HTTP method, URL, headers, and request body bytes. The implementation sends the request through the broker and returns status, headers, and body in a ForwardedResponse. The provider credential remains hidden inside the broker path.

**Call relations**: The egress proxy uses this style of object when it sees a request carrying a grant sentinel instead of a real secret. CliCredential points to a RequestForwarder for command-line tools that authenticate through normal-looking headers.


##### `ConnectorResolver.transfer_hosts`  (lines 302–302)

```
def transfer_hosts(self) -> tuple[str, ...]
```

**Purpose**: Defines the extra file-transfer hosts allowed for every grant in an open connector namespace. These are broker file-store hosts used for uploads and downloads.

**Data flow**: The property takes no call inputs beyond the resolver object. It returns a tuple of host names. It does not change state.

**Call relations**: The egress-proxy permission flow can use this information so sandbox file transfers to broker storage are allowed when a connector grant needs them.


##### `ConnectorResolver.claims`  (lines 304–304)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: Defines how an open resolver answers whether its broker serves a provider slug. This prevents the system from assuming every unknown provider belongs to the open namespace.

**Data flow**: The caller gives a provider name. The implementation may perform I/O, such as checking a live catalog, and returns true or false. No routing entry is created unless the provider is claimed.

**Call relations**: Connector selection code can ask this before choosing between a brokered provider and a workspace-stored direct credential.


##### `ConnectorResolver.entry`  (lines 306–306)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: Defines how an open resolver builds a registry entry for a provider it serves. The entry connects that provider slug to the shared broker.

**Data flow**: The caller supplies a provider name. The implementation returns a ConnectorEntry with provider, label, and broker. This method is expected to be a pure construction step, not a live lookup.

**Call relations**: ConnectorRegistry.entry and _broker call into this path when a provider is not explicitly registered but an open resolver is installed.


##### `ConnectorResolver.catalog`  (lines 308–308)

```
async def catalog(self, query: str, limit: int, after: str | None) -> CatalogPage
```

**Purpose**: Defines how an open resolver pages through connectable services from a broker’s live catalog. Paging means returning a limited slice plus a cursor for the next slice.

**Data flow**: The caller provides query text, a maximum number of entries, and an optional cursor named after. The implementation returns a CatalogPage containing matching provider entries and the next cursor, if more results exist.

**Call relations**: ConnectorRegistry.search_catalog and ConnectorRegistry.catalog use this to include providers that were not individually registered at startup.


##### `ConnectorRegistry.entry`  (lines 325–331)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: Finds the broker registry entry for a provider. It first checks explicitly installed connectors, then asks the open resolver if one exists.

**Data flow**: It receives a provider name and reads the registry’s entries and optional resolver. If it finds a direct entry, it returns it. Otherwise it builds one through the resolver, or raises KeyError if no connector can serve that provider.

**Call relations**: Dynamic connector tool flows use this when they need to route a provider action to the correct broker. It is the main “which broker owns this provider?” lookup.


##### `ConnectorRegistry.search_catalog`  (lines 333–338)

```
async def search_catalog(self, query: str, limit: int) -> tuple[CatalogEntry, ...]
```

**Purpose**: Returns searchable catalog entries from the open connector namespace only. If no open resolver is installed, it returns an empty result.

**Data flow**: It receives query text and a limit. It asks the resolver for the first catalog page when available, then returns just that page’s entries as a tuple. It does not include explicitly registered connectors here.

**Call relations**: Discovery tools can append this result to their known registered connectors so users can find services provided by a broad broker catalog.


##### `ConnectorRegistry.catalog`  (lines 340–360)

```
async def catalog(self, query: str, limit: int, after: str | None) -> CatalogPage
```

**Purpose**: Builds one combined catalog page from explicitly registered connectors and the open broker namespace. It also removes duplicates so the same provider does not appear twice.

**Data flow**: The caller gives query text, a limit, and an optional after cursor. On the first page, it filters explicit entries by matching the query against provider and label. It also asks the resolver for its page, combines both lists, keeps the first entry for each provider, and returns a CatalogPage with the resolver’s next cursor.

**Call relations**: Connector discovery calls this when it needs a user-facing list of connectable services. It creates CatalogEntry and CatalogPage values to present that list in a uniform shape.

*Call graph*: 2 external calls (__init__, __init__).


##### `_broker`  (lines 363–369)

```
def _broker(registry: ConnectorRegistry, provider: str) -> ConnectorBroker | None
```

**Purpose**: Finds the broker object that should serve a provider, or returns nothing if no broker is known. It is a small internal shortcut used by credential routing.

**Data flow**: It receives a ConnectorRegistry and provider name. It checks explicit entries first, then the resolver if present, and extracts the broker from the resulting entry. If neither path applies, it returns None.

**Call relations**: _credential calls this when a source is using a brokered account. This keeps the credential-routing function focused on the direct-versus-broker decision.

*Call graph*: called by 1 (_credential).


##### `_credential`  (lines 372–385)

```
async def _credential(registry: ConnectorRegistry, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Chooses the correct way to obtain credentials for a feed-sync source. Brokered accounts go through their connector broker, while direct accounts go through the fallback authentication backend.

**Data flow**: It receives the registry, workspace ID, provider, and account handle. If the account is not the special direct-account handle, it looks up a broker and asks that broker for credentials. If the account is direct, it asks the fallback AuthProxy. If the needed route is missing, it raises a RuntimeError explaining what cannot be resolved.

**Call relations**: _BoundSourceCredentials.credential calls this after it has checked whether the source is allowed to use the requested account. _credential itself uses _broker to find broker-backed providers.

*Call graph*: calls 1 internal fn (_broker); called by 1 (credential).


##### `_require_source_connection`  (lines 388–412)

```
async def _require_source_connection(workspace_id: UUID, connection_id: UUID, owner_member_id: UUID, provider: str, account: str) -> None
```

**Purpose**: Verifies that a feed source’s stored connection is still active and still belongs to the expected workspace, member, provider, and account. This stops old or mismatched source registrations from using access they no longer own.

**Data flow**: It receives workspace ID, connection ID, owner member ID, provider, and account. Inside the workspace context, it opens a database transaction and selects a matching connection row. If a row exists, it returns successfully; if not, it raises ValueError.

**Call relations**: _BoundSourceCredentials.credential calls this before issuing brokered credentials, and _ConnectionTransport.handle_async_request calls it before every proxied request. It relies on the workspace context and database transaction helpers to read the connection table safely.

*Call graph*: called by 2 (credential, handle_async_request); 3 external calls (select, workspace_tx, ws).


##### `_ConnectionTransport.handle_async_request`  (lines 424–432)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: Checks that a source connection is still valid immediately before sending a proxied HTTP request. This is a last safety gate around brokered feed-sync traffic.

**Data flow**: It receives an HTTP request object. It first calls _require_source_connection using the connection details stored on the transport. If the check passes, it forwards the request to the inner HTTP transport and returns that transport’s response.

**Call relations**: _BoundSourceCredentials.credential wraps broker-provided transports in this transport. Every later HTTP request through that credential passes through this method before reaching the broker path.

*Call graph*: calls 1 internal fn (_require_source_connection).


##### `_ConnectionTransport.aclose`  (lines 434–435)

```
async def aclose(self) -> None
```

**Purpose**: Closes the wrapped HTTP transport when the client is done. This releases any network resources held by the underlying transport.

**Data flow**: It takes no extra inputs beyond the transport object. It calls close on the inner transport and returns when that cleanup is complete. The wrapper itself does not add additional cleanup work.

**Call relations**: HTTP client shutdown uses this method as part of normal async transport cleanup. It preserves the behavior of the broker-provided transport while adding the connection-checking wrapper elsewhere.


##### `_BoundSourceCredentials.credential`  (lines 444–474)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns credentials for one feed source after enforcing whether that source is allowed to use direct credentials or a specific member-owned connection. It binds authentication to the source’s original registration decision.

**Data flow**: It receives workspace ID, provider, and account. For the direct-account handle, it rejects the request if this source was bound to a connection, then delegates to _credential. For brokered accounts, it requires stored connection and owner IDs, verifies the connection in the database, obtains broker credentials, requires those credentials to include a proxy transport, and returns a new Credential whose transport rechecks the connection on every request.

**Call relations**: SourceCredentialResolver.bind creates this object for a source run. It calls _require_source_connection for authorization, _credential for actual credential resolution, and wraps broker transports with _ConnectionTransport before handing them to the sync connector.

*Call graph*: calls 2 internal fn (_credential, _require_source_connection); 2 external calls (__init__, __init__).


##### `SourceCredentialResolver.bind`  (lines 481–486)

```
def bind(self, connection_id: UUID | None, owner_member_id: UUID | None) -> AuthProxy
```

**Purpose**: Creates an AuthProxy tied to one feed source’s connection information. This lets the sync runner ask for credentials later without forgetting which member-owned connection the source was registered with.

**Data flow**: It receives an optional connection ID and optional owner member ID. It returns a _BoundSourceCredentials object carrying those IDs plus the registry. It does not contact brokers or the database yet.

**Call relations**: The sync runner calls this before running a source. The returned object is then used whenever that source needs credentials, and the real checks happen in _BoundSourceCredentials.credential.

*Call graph*: 1 external calls (__init__).


### `core/src/ufo/runtime/access/credentials.py`

`domain_logic` · `cross-cutting`

This file is the credential safety layer for UFO. A workspace may need private values, like an API token or an account-specific provider setting, but those values must not appear in chat, logs, or the sandbox where untrusted work may run. The file treats each secret as a named “slot,” like a locked drawer labeled for one kind of key. Values in those drawers are encrypted with Fernet, which is a standard symmetric encryption scheme where the same configured key locks and unlocks data.

It also supports a safer way for a member to provide a secret. Instead of asking someone to paste a key into a public transcript, the system creates a sealed credential request. That sealed request says which workspace, member, and slots are allowed. A trusted surface can ask privately for the secret, then the store verifies the seal before writing anything.

The file also covers credentials that are not typed by members. Some slots are filled by provider authorization flows, such as binding an external installation. Those bindings are sealed too, so a guessed installation ID cannot be used by the wrong workspace.

Finally, it gives one shared answer for “is this credential available?”, “what secret should be injected?”, and “which host is this secret allowed to reach?” That consistency matters because the sandbox, proxy, and extension code must agree; otherwise the system could expose a secret in one place that another place refuses or misroutes.

#### Function details

##### `deploy_env`  (lines 34–40)

```
def deploy_env(name: str) -> str | None
```

**Purpose**: Reads a deployment-level secret from environment variables. It first looks for a UFO-specific name, then falls back to the plain name, so this project can keep its secrets separate from other tools while still supporting common setups.

**Data flow**: It receives a variable name → checks the process environment for `UFO_<name>` and then `<name>` → returns the first non-empty value it finds, or `None` if neither is set.

**Call relations**: This is a small lookup helper used when the wider application needs configuration secrets from the deployment environment. It does not call into the credential store; it is for platform-provided secrets rather than workspace-stored ones.


##### `credential_object_name`  (lines 43–46)

```
def credential_object_name(slot: str) -> str
```

**Purpose**: Turns a credential slot name into a safe object-style name. It makes names lowercase and replaces punctuation or spaces with hyphens so different parts of the system can refer to a slot consistently.

**Data flow**: It receives a slot name → lowercases it, swaps non-letter-and-number runs for hyphens, and trims extra hyphens at the ends → returns the cleaned name.

**Call relations**: When `named_slots` needs public names for declared credential slots, it asks this function to create the plain version first. That keeps naming rules in one place.

*Call graph*: called by 1 (named_slots); 1 external calls (sub).


##### `member_slot`  (lines 52–56)

```
def member_slot(slot: str, member_id: UUID) -> str
```

**Purpose**: Builds the special slot name used to store a credential value that belongs to one workspace member rather than the whole workspace. This lets the same encrypted table hold both shared and per-member secrets without mixing them up.

**Data flow**: It receives a base slot name and a member ID → joins them with a fixed marker that means “member-specific” → returns the combined slot key.

**Call relations**: Other credential code can use this naming convention when it needs a private value tied to one person. The function is intentionally simple so all callers format member slots the same way.


##### `named_slots`  (lines 59–74)

```
def named_slots(slots: 'tuple[DeclaredSlot, ...]') -> 'dict[str, DeclaredSlot]'
```

**Purpose**: Creates the stable public names for a set of declared credential slots. If two slots would end up with the same cleaned name, it adds a short digest, meaning a compact fingerprint, so they remain distinct.

**Data flow**: It receives declared slot objects → groups them by their cleaned object name → keeps unique names as-is, and gives colliding names a short hash based on extension and slot name → returns a dictionary from public name to slot declaration.

**Call relations**: It builds on `credential_object_name` for the first pass at naming. When names collide, it uses a hash so later features that address credential objects can still point to exactly one slot.

*Call graph*: calls 1 internal fn (credential_object_name); 1 external calls (sha256).


##### `seal_credential_request`  (lines 120–121)

```
def seal_credential_request(fernet: Fernet, state: CredentialRequestState) -> str
```

**Purpose**: Encrypts a credential request state into an opaque string that can be handed around safely. The sealed string is like a tamper-proof ticket: callers can carry it, but cannot change what workspace, member, or slot it names.

**Data flow**: It receives a Fernet encryptor and a structured request state → turns the state into JSON text → encrypts that text → returns the encrypted text as a string.

**Call relations**: `CredentialRequests.seal`, `CredentialRequests.authorize`, and `seal_installation` all use this helper when they need to create a sealed credential-related token. Centralizing this keeps all sealed states in the same format.

*Call graph*: called by 3 (authorize, seal, seal_installation); 2 external calls (model_dump_json, encrypt).


##### `open_credential_request`  (lines 124–150)

```
def open_credential_request(fernet: Fernet, sealed: str, *, purpose: str, ttl: int | None=CREDENTIAL_REQUEST_TTL_SECONDS) -> CredentialRequestState
```

**Purpose**: Decrypts and checks a sealed credential request or binding. It rejects anything expired, tampered with, malformed, or sealed for the wrong purpose.

**Data flow**: It receives a Fernet encryptor, a sealed string, the expected purpose, and an optional time limit → tries to decrypt and parse the state → checks the purpose field → returns the trusted state, or raises `CredentialRequestInvalid` if any check fails.

**Call relations**: Credential authorization, installation opening, and callback workspace lookup all pass their sealed strings through this function. That means each caller gets a clean trusted state or a single clear failure, rather than duplicating security checks.

*Call graph*: called by 3 (open_authorization, authorized_slot_workspace, open_installation); 2 external calls (__init__, decrypt).


##### `CredentialRequests.seal`  (lines 169–188)

```
def seal(self, workspace_id: UUID, member_id: UUID, slots: tuple[str, ...]) -> str
```

**Purpose**: Creates a sealed request for a member to privately fill one or more credential slots. It refuses slots that are not declared or that members are not allowed to type into.

**Data flow**: It receives a workspace ID, member ID, and slot names → checks that each slot exists and is member-fillable → adds a fresh request ID and issue time → returns an encrypted sealed request string.

**Call relations**: This is used before a private prompt is shown to a member. It hands off to `seal_credential_request` after doing the policy checks that decide whether the request is allowed at all.

*Call graph*: calls 1 internal fn (seal_credential_request); 3 external calls (__init__, time, uuid4).


##### `CredentialRequests.authorize`  (lines 190–203)

```
def authorize(self, workspace_id: UUID, member_id: UUID, slot: str, payload: str) -> str
```

**Purpose**: Creates a sealed authorization state for a provider-driven credential flow. This is for cases where a provider callback, not a typed member secret, will eventually fill the slot.

**Data flow**: It receives a workspace ID, member ID, slot name, and provider payload → checks that the slot is declared and the payload is not empty → seals that information → returns the encrypted authorization string.

**Call relations**: A provider authorization flow calls this when it needs to send state through a browser redirect or similar path. It uses `seal_credential_request` so the callback can later prove the state came from this deployment.

*Call graph*: calls 1 internal fn (seal_credential_request); 1 external calls (__init__).


##### `CredentialRequests.open_authorization`  (lines 205–219)

```
def open_authorization(self, sealed: str, workspace_id: UUID, member_id: UUID, slot: str) -> str
```

**Purpose**: Opens a sealed provider authorization and confirms it belongs to the exact workspace, member, and slot expected. It returns the provider payload only if all those checks pass.

**Data flow**: It receives a sealed string plus the expected workspace, member, and slot → decrypts the seal → compares the stored claims with the expected ones → returns the payload, or raises an error if it does not match.

**Call relations**: Provider callback handling uses this after receiving a sealed authorization. It relies on `open_credential_request` for decryption and then adds the context-specific checks.

*Call graph*: calls 1 internal fn (open_credential_request); 1 external calls (__init__).


##### `seal_installation`  (lines 222–236)

```
def seal_installation(fernet: Fernet, workspace_id: UUID, slot: str, installation_id: str) -> str
```

**Purpose**: Seals an external provider installation ID so it is bound to one workspace and one slot. This prevents someone from typing or guessing a raw installation ID and making the system use another organization’s installation.

**Data flow**: It receives a Fernet encryptor, workspace ID, slot, and installation ID → wraps them in a state marked specifically as an installation binding → encrypts it → returns the sealed string.

**Call relations**: After a provider installation has been authorized, this function creates the safe value that can be stored in the credential slot. It uses the same sealing helper as requests, but with a different purpose.

*Call graph*: calls 1 internal fn (seal_credential_request); 1 external calls (__init__).


##### `open_installation`  (lines 239–250)

```
def open_installation(fernet: Fernet, workspace_id: UUID, slot: str, sealed: str) -> str
```

**Purpose**: Opens a sealed installation binding and confirms it belongs to the expected workspace and slot. It returns the real installation ID only if the binding is valid.

**Data flow**: It receives a Fernet encryptor, workspace ID, slot, and sealed value → decrypts it without an expiry time because installations are long-lived → checks workspace, slot, and payload → returns the installation ID or raises `CredentialRequestInvalid`.

**Call relations**: Provider code uses this when it needs to mint or use credentials from a stored installation binding. It delegates seal decoding to `open_credential_request` and then applies installation-specific checks.

*Call graph*: calls 1 internal fn (open_credential_request); 1 external calls (__init__).


##### `install_credential_requests`  (lines 256–262)

```
def install_credential_requests(requests: CredentialRequests | None) -> None
```

**Purpose**: Stores the process-wide credential request authority for later use. This is needed because some provider callbacks arrive outside the normal chat or request context.

**Data flow**: It receives a `CredentialRequests` object or `None` → writes it into a module-level variable → returns nothing, but changes what later global credential lookup functions can use.

**Call relations**: Startup code installs the configured credential request authority here. Later, callback routes and helpers can retrieve or use it even when they do not have the usual per-turn context.


##### `installed_credential_requests`  (lines 265–268)

```
def installed_credential_requests() -> CredentialRequests
```

**Purpose**: Returns the credential request authority that was installed for this process. If no credential key was configured, it fails loudly instead of letting code pretend authorization is available.

**Data flow**: It reads the module-level installed authority → returns it if present → otherwise raises a runtime error explaining that credential authorization is unavailable.

**Call relations**: Code that needs the global credential sealing authority calls this after startup. It depends on `install_credential_requests` having already placed the authority in the module.


##### `authorized_slot_workspace`  (lines 271–287)

```
def authorized_slot_workspace(sealed: str, slot: str, payload: str) -> UUID | None
```

**Purpose**: Figures out which workspace a provider authorization seal belongs to, but only if the seal is valid for the exact slot and provider payload. It returns `None` instead of throwing for invalid callback input.

**Data flow**: It receives a sealed state, slot, and payload → uses the installed Fernet key to open the seal if available → checks that the sealed slot and payload match → returns the workspace ID, or `None` if anything is missing or invalid.

**Call relations**: A provider redirect route can call this when it has no active session but needs to know which workspace the callback belongs to. It uses `open_credential_request` and quietly rejects seals that do not match.

*Call graph*: calls 1 internal fn (open_credential_request).


##### `CredentialStore.put`  (lines 294–316)

```
async def put(self, workspace_id: UUID, slot: str, plaintext: str) -> None
```

**Purpose**: Writes a credential value directly into a workspace slot after encrypting it. It creates the row if needed or replaces the existing encrypted value.

**Data flow**: It receives a workspace ID, slot, and plaintext secret → rejects an empty value → encrypts the secret → opens a workspace database transaction → updates the existing row or inserts a new one → returns nothing.

**Call relations**: This is the basic store-write path for trusted code that already has permission to write a secret. It talks to the database through `workspace_tx` and does not do member prompt verification; that stricter flow lives in `CredentialStore.fulfill`.

*Call graph*: 3 external calls (insert, update, workspace_tx).


##### `CredentialStore.fulfill`  (lines 318–405)

```
async def fulfill(self, workspace_id: UUID, slot: str, submitted: str, request_id: UUID | None, member_id: UUID, merge: Callable[[str | None, str], str] | None) -> None
```

**Purpose**: Stores a secret submitted through a sealed member credential request. It verifies that the member is a seated workspace admin and, when a request ID is present, makes sure the same prompt cannot be fulfilled twice.

**Data flow**: It receives the workspace, slot, submitted secret, optional request ID, member ID, and optional merge function → rejects empty input → locks the workspace row → checks admin authority → records the fulfillment if needed → decrypts any current value → either replaces or merges the value → encrypts and inserts or updates the stored credential.

**Call relations**: This is the write path used after a private credential prompt is completed. It combines database locking, admin verification, one-time request claiming, optional merging, and encrypted storage in one transaction so the handoff cannot be replayed or raced.

*Call graph*: 5 external calls (__init__, insert, select, update, workspace_tx).


##### `CredentialStore.clear`  (lines 407–416)

```
async def clear(self, workspace_id: UUID, slot: str) -> None
```

**Purpose**: Deletes a stored credential slot for a workspace. If the slot is already empty, it still succeeds, which makes repeated disconnect or cleanup actions safe.

**Data flow**: It receives a workspace ID and slot → opens a workspace database transaction → deletes matching credential rows → returns nothing whether or not a row existed.

**Call relations**: Credential disconnect and cleanup flows use this to remove a saved secret. It only touches the credential table and does not need to decrypt anything.

*Call graph*: 2 external calls (delete, workspace_tx).


##### `CredentialStore.update`  (lines 418–465)

```
async def update(self, workspace_id: UUID, slot: str, submitted: str, merge: Callable[[str | None, str], str]) -> None
```

**Purpose**: Merges a private submitted value into an existing credential slot while holding a workspace write lock. This is useful when a credential is structured and one submission updates only part of it.

**Data flow**: It receives a workspace ID, slot, submitted value, and merge function → rejects empty input → locks the workspace row → reads and decrypts the current credential if present → calls the merge function → rejects an empty merged result → encrypts and inserts or updates the stored value.

**Call relations**: This is a controlled update path for structured secrets. It resembles `CredentialStore.fulfill` but does not do the sealed-request and seated-admin checks itself, so callers use it when those checks are handled elsewhere or not needed.

*Call graph*: 4 external calls (insert, select, update, workspace_tx).


##### `CredentialStore.get`  (lines 467–479)

```
async def get(self, workspace_id: UUID, slot: str) -> str
```

**Purpose**: Reads and decrypts a credential value for one workspace slot. If no value is stored, it raises a specific “slot unset” error so callers can distinguish missing credentials from other failures.

**Data flow**: It receives a workspace ID and slot → queries the credential table → if no row exists, raises `CredentialSlotUnset` → otherwise decrypts the stored ciphertext and returns the plaintext secret.

**Call relations**: This is the main read path for stored credentials. Higher-level helpers such as `slot_secret`, `slot_is_set`, and `credential_host`, plus GitHub extension authentication code, call it when they need the saved value.

*Call graph*: called by 7 (credential_host, slot_is_set, slot_secret, bound, secret, bound, secret); 3 external calls (__init__, select, workspace_tx).


##### `CredentialStore.rotate`  (lines 481–512)

```
async def rotate(self, workspace_id: UUID, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Replaces a credential only if the stored value still matches an expected old value. This protects token refreshes from overwriting a newer token created by another concurrent refresh.

**Data flow**: It receives a workspace ID, slot, expected current plaintext, and new plaintext → rejects an empty new value → reads and decrypts the current stored value → returns `False` if missing or different → updates the row only if the ciphertext is still the same → returns whether exactly one row was updated.

**Call relations**: OAuth-style provider clients use this after refreshing a token. It is separate from the initial member fulfillment path because it is meant for safe replacement of an already-stored credential.

*Call graph*: 3 external calls (select, update, workspace_tx).


##### `HostChoice.__post_init__`  (lines 536–541)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that a host choice declaration is internally consistent. The default host must be one of the allowed hosts, otherwise an unconfigured workspace could fall back to something not actually permitted.

**Data flow**: It reads the newly created `HostChoice` object’s default and allowed host list → raises a value error if the default is not in the list → otherwise leaves the object unchanged.

**Call relations**: This runs automatically when a `HostChoice` is created. It catches bad extension declarations early, before proxy or sandbox logic relies on them.


##### `HostChoice.resolve`  (lines 543–548)

```
def resolve(self, selected: str) -> str | None
```

**Purpose**: Turns a stored host selection into the exact declared host string. It matches case-insensitively, because domain names are case-insensitive, but returns only the canonical host from the declaration.

**Data flow**: It receives a selected value → trims spaces and lowercases it for comparison → looks for a matching allowed host → returns the declared host string, or `None` if the selection is not allowed.

**Call relations**: `credential_host` uses this when a workspace has stored a choice for an account-specific provider host. Returning `None` for unknown values prevents a member-supplied string from deciding an arbitrary network destination.


##### `CredentialSource.secret`  (lines 556–556)

```
async def secret(self, workspace_id: UUID, store: 'CredentialStore') -> str | None
```

**Purpose**: Defines the interface for a credential source that can mint a secret on demand. A source is used when the real secret should be generated from another binding instead of stored directly as a member-entered value.

**Data flow**: An implementation receives a workspace ID and credential store → may read stored bindings or contact a provider → returns a freshly minted secret string, or `None` if there is nothing to mint.

**Call relations**: `slot_secret` calls this when a declared slot has a source. This method is a protocol method, meaning the file states what implementing classes must provide rather than giving the behavior here.

*Call graph*: called by 1 (slot_secret).


##### `CredentialSource.bound`  (lines 558–566)

```
async def bound(self, workspace_id: UUID, store: 'CredentialStore') -> bool
```

**Purpose**: Defines the interface for asking whether a credential source has enough binding information to mint a secret, without actually minting one. This avoids expensive or risky provider calls when the system only needs to know whether a slot is available.

**Data flow**: An implementation receives a workspace ID and credential store → checks local binding state or other lightweight evidence → returns `True` or `False`, or raises if a binding exists but cannot be used safely.

**Call relations**: `slot_is_set` calls this during availability checks, such as deciding whether to configure a sandbox client. The matching source implementation also provides `secret`, so availability and actual secret resolution stay aligned.

*Call graph*: called by 1 (slot_is_set).


##### `slot_secret`  (lines 569–584)

```
async def slot_secret(name: str, source: CredentialSource | None, workspace_id: UUID, store: CredentialStore) -> str | None
```

**Purpose**: Answers the central question: what secret should this slot provide for this workspace? It prefers a minted provider secret when a source exists, then falls back to the stored member value.

**Data flow**: It receives a slot name, optional credential source, workspace ID, and credential store → asks the source for a minted secret if present → returns that if available → otherwise tries to read the stored slot → returns the stored secret or `None` if unset.

**Call relations**: Proxy rules, sandbox exports, and other credential consumers use this shared path so they all inject the same secret. It calls `CredentialSource.secret` for minted credentials and `CredentialStore.get` for stored ones.

*Call graph*: calls 2 internal fn (secret, get).


##### `slot_is_set`  (lines 587–605)

```
async def slot_is_set(name: str, source: CredentialSource | None, workspace_id: UUID, store: CredentialStore) -> bool
```

**Purpose**: Checks whether a slot would provide a usable secret without actually producing the secret. This matters because minting a provider token may require a network request, while many turns only need a yes-or-no answer.

**Data flow**: It receives a slot name, optional source, workspace ID, and store → if a source exists, asks whether it is bound → returns `True` if so → otherwise checks whether a stored credential exists → returns `True` or `False` based on that result.

**Call relations**: Sandbox setup and similar availability checks call this before deciding whether to configure credential-dependent behavior. It uses `CredentialSource.bound` for provider-backed slots and `CredentialStore.get` for stored slots.

*Call graph*: calls 2 internal fn (bound, get).


##### `credential_host`  (lines 608–625)

```
async def credential_host(store: CredentialStore, workspace_id: UUID, host: str | HostChoice) -> str | None
```

**Purpose**: Determines which provider host a credential is allowed to reach for a workspace. It returns either a fixed declared host, a workspace-selected allowed host, or no host if the stored choice is not permitted.

**Data flow**: It receives a credential store, workspace ID, and either a plain host string or a `HostChoice` → returns plain strings directly → for a choice, reads the workspace’s stored selection → uses the default if none is stored → resolves the stored value against the allowed list → returns the approved host or `None`.

**Call relations**: Both the egress proxy and sandbox/export logic use this same function so they agree on the network destination tied to a credential. It reads stored selections through `CredentialStore.get` and uses `HostChoice.resolve` to keep member input inside a closed allowed set.

*Call graph*: calls 1 internal fn (get).


### `core/src/ufo/runtime/access/grants.py`

`domain_logic` · `request handling, OAuth callback, grant administration, and connection listing`

This file is the project’s “permission desk” for connected accounts. A member may leave the app, approve access on an outside provider’s website, and return through a callback. This code keeps that round trip safe by sealing the important details into an encrypted state value: workspace, member, agent, provider, conversation, and sharing choice. Without this, the system could not reliably know who approved what, or it might grant the wrong agent access to the wrong account.

There are two main parts. ConnectFlow runs the OAuth journey. It creates the provider authorization link, later opens and checks the sealed state, asks the provider to exchange the returned code for a stable account id, records the grant, fires extension hooks, and optionally resumes the conversation that asked for the connection.

GrantStore is the database-facing part. It creates or reuses a member-owned connection row, adds or removes agent grant edges, checks owner/admin permissions, changes sharing, disconnects sources, and produces audit-style summaries. Think of a connection as a locked toolbox owned by a member, and a grant as a key handed to one agent. The toolbox stays owned by the member; the key can be added, removed, or shared according to rules.

#### Function details

##### `grant_sentinel`  (lines 49–53)

```
def grant_sentinel(account_id: str) -> str
```

**Purpose**: Builds a special placeholder credential string for a connected account. The sandbox and egress proxy can both recognize this same marker without separately registering it.

**Data flow**: It receives an account id, prefixes it with a fixed sentinel string, and returns the combined value. It does not read or change stored data.

**Call relations**: Other parts of the system can use this marker when exporting credentials into an agent environment, while the proxy can match the same marker back to the brokered account.


##### `UnknownProvider.__init__`  (lines 61–62)

```
def __init__(self, provider: str) -> None
```

**Purpose**: Creates a clear, member-facing error when someone asks for a connector provider that is not installed or claimed. It avoids exposing only a raw internal slug.

**Data flow**: It receives the requested provider name and turns it into a readable error message. The resulting exception can be shown to the member or calling flow.

**Call relations**: ConnectFlow.validate_provider and ConnectFlow._provider raise this when no direct provider and no resolver can satisfy the requested provider.

*Call graph*: called by 2 (_provider, validate_provider).


##### `OAuthProvider.provider`  (lines 105–105)

```
def provider(self) -> str
```

**Purpose**: Names the provider represented by an OAuth connector descriptor. Implementations use it as the stable internal provider id to record in grants and connections.

**Data flow**: An implementation returns a provider name from its own stored descriptor data. Nothing is changed.

**Call relations**: ConnectFlow.complete relies on provider descriptors to supply the provider identity that is recorded in GrantStore and returned in GrantRecorded.


##### `OAuthProvider.host`  (lines 108–108)

```
def host(self) -> str
```

**Purpose**: Gives the provider host that a grant allows the proxy to reach. This is the network destination tied to the connected account when the provider needs one.

**Data flow**: An implementation returns its host string, possibly empty for brokered providers that do not expose a direct host. The value is later stored with the connection.

**Call relations**: ConnectFlow.complete reads this through the provider descriptor and hands it to GrantStore.record so the usable connection knows what host it covers.


##### `OAuthProvider.authorize_url`  (lines 110–110)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds the web link a member opens to approve a connection with the outside provider. The link carries sealed state so the callback can later prove what was requested.

**Data flow**: It receives an encrypted state string and callback address, combines them with provider-specific OAuth details, and returns a URL. It does not itself record the grant.

**Call relations**: ConnectFlow.authorize calls the provider descriptor’s authorize_url after creating the sealed state for this connection attempt.


##### `OAuthProvider.exchange`  (lines 112–114)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Turns the short-lived code returned by the provider into the project’s stable connected account identity. This is where the provider confirms which account was approved.

**Data flow**: It receives the returned code, callback address, workspace id, and original state, then contacts or validates through the provider implementation and returns an OAuthAccount. The account token stays with the broker, not in the grant.

**Call relations**: ConnectFlow.complete calls exchange after checking the sealed state, then passes the returned account id and label into GrantStore.record.


##### `OAuthProviderResolver.claims`  (lines 125–125)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: Checks whether an open provider namespace can serve a requested provider name. This lets a broker extension support many provider slugs without registering each one ahead of time.

**Data flow**: It receives a provider name, usually checks an external or live catalog, and returns true or false. It does not create a connection.

**Call relations**: ConnectFlow.validate_provider uses this when the provider is not in the fixed provider map, so bad provider names fail before a dead authorization link is minted.


##### `OAuthProviderResolver.descriptor`  (lines 127–127)

```
def descriptor(self, provider: str) -> OAuthProvider
```

**Purpose**: Builds an OAuthProvider descriptor for a provider served through an open namespace. It gives ConnectFlow the same shape of object as a directly registered provider.

**Data flow**: It receives a provider name and returns an OAuthProvider implementation for that name. It does not contact the user or write to storage.

**Call relations**: ConnectFlow._provider calls this when no direct provider is installed but a resolver is available.


##### `ConnectionHooks.fire`  (lines 234–234)

```
async def fire(self, connection: ConnectionRecorded) -> None
```

**Purpose**: Notifies installed extensions after a connection has been recorded. Extensions can use this moment to create related state, such as feed source rows.

**Data flow**: It receives a ConnectionRecorded payload describing the landed connection and performs extension-defined work. The connection already exists in the database.

**Call relations**: ConnectFlow.complete calls fire after GrantStore.record succeeds, so extensions see a durable connection rather than a speculative one.


##### `ConnectResumption.resume`  (lines 247–254)

```
async def resume(self, conversation_id: UUID, message: str, *, speaker_member_id: UUID, idempotency_key: str) -> bool
```

**Purpose**: Posts a follow-up message into the conversation that originally asked the member to connect an account. This lets the agent continue after the member returns from the browser flow.

**Data flow**: It receives a conversation id, message text, speaker member id, and idempotency key, then tries to enqueue or admit that message. It returns whether the resume message was accepted.

**Call relations**: ConnectFlow.complete calls resume last, after the grant and any connection hooks have completed, so the conversation wakes up with the connection already usable.


##### `_resume_key`  (lines 257–272)

```
def _resume_key(state: str) -> str
```

**Purpose**: Creates a stable idempotency key for one specific connect attempt. An idempotency key is a duplicate-prevention label, so refreshing the callback does not post the same success message twice.

**Data flow**: It receives the sealed state string, hashes it with SHA-256, keeps a short digest, adds a prefix, and returns the key. It does not expose the full sealed state.

**Call relations**: ConnectFlow.complete uses this key when asking ConnectResumption.resume to notify the conversation.

*Call graph*: called by 1 (complete); 1 external calls (sha256).


##### `GrantStore.workspace_id`  (lines 299–300)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace currently in scope. GrantStore uses this so every database action is limited to the active workspace.

**Data flow**: It reads the current workspace context and returns its workspace id. It does not query or modify tables itself.

**Call relations**: GrantStore methods use this property throughout their database queries to avoid crossing workspace boundaries.

*Call graph*: 1 external calls (ws_current).


##### `GrantStore.agent_id`  (lines 303–304)

```
def agent_id(self) -> UUID
```

**Purpose**: Returns the agent currently targeted for object actions. This is the agent that receives or loses connector grants.

**Data flow**: It reads the current object-agent context and returns the agent id. It does not change storage.

**Call relations**: GrantStore.record, active_grants, attach, revoke, and set_shared use this to bind grant edges to the correct agent.

*Call graph*: 1 external calls (object_agent_id).


##### `GrantStore.record`  (lines 306–497)

```
async def record(self, *, provider: str, account_id: str, host: str, grantor_member_id: UUID, conversation_id: UUID, shared: bool, account_label: str | None=None, landed_turn_id: UUID | None=None) ->
```

**Purpose**: Creates or reuses a member-owned connection and grants the current agent access to it. It is the durable landing step after an OAuth approval succeeds.

**Data flow**: It receives provider, account, owner, conversation, sharing, label, and optional turn information. It checks the member still has access, inserts or finds the connection, refuses ownership conflicts, upserts the agent grant, marks the requesting turn as landed if needed, and wakes parked sources so they can retry. It returns the connection id.

**Call relations**: ConnectFlow.complete calls this after the provider exchange returns an account. It is the central database write that later summaries, active grants, hooks, and conversation resumption depend on.

*Call graph*: 10 external calls (__init__, __init__, now, and_, literal, or_, select, update, workspace_tx, uuid4).


##### `GrantStore.active_grants`  (lines 499–544)

```
async def active_grants(self) -> tuple[Grant, ...]
```

**Purpose**: Lists the connection grants currently usable by the bound agent. This is used when building the agent’s runtime environment.

**Data flow**: It reads connector grant rows joined with their connection and owner member rows for the current workspace and agent. It returns Grant objects with provider, account, host, owner, and sharing details.

**Call relations**: The sandbox environment builder calls this to decide which connector credentials should be visible to an executing agent.

*Call graph*: called by 1 (_grant_cli_env); 4 external calls (__init__, and_, select, workspace_tx).


##### `GrantStore.revoke`  (lines 546–558)

```
async def revoke(self, grant_id: UUID, *, actor_member_id: UUID) -> bool
```

**Purpose**: Removes one grant edge from the bound agent, if the actor is allowed to do so. The underlying connection can remain for other grants or sources.

**Data flow**: It receives a grant id and actor member id, checks permission through _grant_for_actor, deletes the matching connector grant row, and returns whether a row was removed.

**Call relations**: It delegates the ownership/admin decision to _grant_for_actor before performing the deletion.

*Call graph*: calls 1 internal fn (_grant_for_actor); 2 external calls (delete, workspace_tx).


##### `GrantStore.attach`  (lines 560–617)

```
async def attach(self, *, provider: str, account_id: str, conversation_id: UUID, actor_member_id: UUID, shared: bool) -> bool
```

**Purpose**: Gives the bound agent access to an existing connection. This is for reusing a connection that already belongs to the actor or is workspace-shared.

**Data flow**: It receives provider, account id, conversation id, actor id, and requested sharing flag. It locks and checks the existing connection, rejects private access by non-owners and attempts to widen sharing here, inserts the grant if absent, and returns true if the connection existed.

**Call relations**: This is a direct grant-administration path, separate from OAuth completion. It uses the same connector grant table that GrantStore.record writes after a fresh connect.

*Call graph*: 4 external calls (__init__, select, workspace_tx, uuid4).


##### `GrantStore.set_shared`  (lines 619–647)

```
async def set_shared(self, grant_id: UUID, shared: bool, *, actor_member_id: UUID) -> bool
```

**Purpose**: Changes whether a connection is shared across the workspace. Owners can change sharing, and admins are only allowed where the method permits them.

**Data flow**: It receives a grant id, desired shared value, and actor id. It checks that the actor may act on the connection behind that grant, updates the connection’s shared flag, and returns whether the update happened.

**Call relations**: It asks _grant_for_actor to verify the grant and permission, then updates the connection row rather than the grant row because sharing belongs to the connection itself.

*Call graph*: calls 1 internal fn (_grant_for_actor); 3 external calls (select, update, workspace_tx).


##### `GrantStore.disconnect`  (lines 649–705)

```
async def disconnect(self, connection_id: UUID, *, actor_member_id: UUID) -> bool
```

**Purpose**: Fully removes a connection and cleans up the sources that depended on it. This is stronger than revoking one agent grant.

**Data flow**: It receives a connection id and actor member id, checks permission, finds bound sources, removes their source grants, marks sources removed, tombstones their pages, deletes the connection, and returns whether it completed.

**Call relations**: It uses _connection_for_actor for permission and then performs the teardown that cascades connector grant removal through the database relationship.

*Call graph*: calls 1 internal fn (_connection_for_actor); 5 external calls (now, delete, select, update, workspace_tx).


##### `GrantStore._connection_for_actor`  (lines 707–735)

```
async def _connection_for_actor(self, connection: AsyncConnection, connection_id: UUID, actor_member_id: UUID, *, admin_allowed: bool=True) -> UUID | None
```

**Purpose**: Checks whether a member may mutate a connection. It allows the owner, or an admin when that action permits admin authority.

**Data flow**: It receives a database connection, connection id, actor id, and admin rule. It locks the connection row, returns none if missing, returns the id for the owner, or checks admin status and either returns the id or raises a permission error.

**Call relations**: GrantStore.disconnect calls it directly. _grant_for_actor also calls it when an operation starts from a grant id and needs to verify the underlying connection.

*Call graph*: calls 1 internal fn (_is_admin); called by 2 (_grant_for_actor, disconnect); 3 external calls (__init__, execute, select).


##### `GrantStore._is_admin`  (lines 737–747)

```
async def _is_admin(self, connection: AsyncConnection, actor_member_id: UUID) -> bool
```

**Purpose**: Answers whether a member is an admin in the current workspace. It is a small helper for permission checks.

**Data flow**: It receives a database connection and member id, reads the member’s is_admin flag, and returns true or false. Missing members count as false.

**Call relations**: _connection_for_actor calls this only when the actor is not the connection owner and admin access might be allowed.

*Call graph*: called by 1 (_connection_for_actor); 2 external calls (execute, select).


##### `GrantStore._grant_for_actor`  (lines 749–787)

```
async def _grant_for_actor(self, connection: AsyncConnection, grant_id: UUID, actor_member_id: UUID, *, admin_allowed: bool=True) -> UUID | None
```

**Purpose**: Checks whether a grant belongs to the current agent and whether the actor may change the connection behind it. It protects revoke and sharing changes.

**Data flow**: It receives a database connection, grant id, actor id, and admin rule. It finds the grant’s connection, checks connection permission through _connection_for_actor, locks the matching grant row, and returns the grant id or none.

**Call relations**: GrantStore.revoke and GrantStore.set_shared call this before changing grant or connection data.

*Call graph*: calls 1 internal fn (_connection_for_actor); called by 2 (revoke, set_shared); 2 external calls (execute, select).


##### `ConnectFlow.authorize`  (lines 808–830)

```
def authorize(self, *, workspace_id: UUID, agent_id: UUID, provider: str, grantor_member_id: UUID, conversation_id: UUID, shared: bool, turn_id: UUID | None=None) -> str
```

**Purpose**: Creates the provider approval link for a new connect attempt. It packages the needed facts into encrypted state so the callback can trust them later.

**Data flow**: It receives workspace, agent, provider, member, conversation, sharing, and optional turn ids. It finds the provider descriptor, builds a ConnectState, encrypts it, and returns the provider authorization URL.

**Call relations**: ConnectHandoff.authorize uses this when a turn needs a fresh private OAuth URL for the member to open.

*Call graph*: calls 1 internal fn (_provider); 1 external calls (__init__).


##### `ConnectFlow.validate_provider`  (lines 832–837)

```
async def validate_provider(self, provider: str) -> None
```

**Purpose**: Checks that a requested provider can actually be connected before the request proceeds. This prevents creating connect prompts for unknown providers.

**Data flow**: It receives a provider name, accepts it if directly installed or claimed by the resolver, and otherwise raises UnknownProvider.

**Call relations**: This is the stricter validation step for connect requests; it may ask a resolver to verify a provider name against its catalog.

*Call graph*: calls 1 internal fn (__init__).


##### `ConnectFlow.knows_provider`  (lines 839–844)

```
def knows_provider(self, provider: str) -> bool
```

**Purpose**: Quickly checks whether this process still has machinery for a provider. It is intentionally cheaper than full validation.

**Data flow**: It receives a provider name and returns true if the provider is directly installed or any resolver is present. It does not perform external catalog checks.

**Call relations**: ConnectHandoff.authorize uses this while serving an existing turn’s connect button, where the earlier request already performed the expensive validation.


##### `ConnectFlow.bridge_workspace`  (lines 846–852)

```
def bridge_workspace(self, *, state: str, provider: str, callback: str) -> UUID
```

**Purpose**: Verifies a browser bridge request and extracts the workspace it is allowed to use. This prevents a bridge request from claiming a different provider or callback than the sealed state says.

**Data flow**: It receives state, provider, and callback values from a request. It opens the sealed state, compares the provider and callback, confirms the provider descriptor exists, and returns the workspace id or raises an invalid-state error.

**Call relations**: connect_bridge_workspace calls this through the installed flow when a connector browser bridge needs to decide whether a request may proceed.

*Call graph*: calls 2 internal fn (_open, _provider); 1 external calls (__init__).


##### `ConnectFlow.complete`  (lines 854–904)

```
async def complete(self, *, state: str, code: str) -> GrantRecorded
```

**Purpose**: Finishes the OAuth handoff after the provider redirects back with a code. It records the connection grant and tells the rest of the system that the account is ready.

**Data flow**: It receives sealed state and provider code. It opens and validates the state, finds the provider, exchanges the code for an account, records the connection and grant in the proper workspace and agent context, fires connection hooks, optionally resumes the conversation, and returns a GrantRecorded summary.

**Call relations**: This is the callback-side counterpart to ConnectFlow.authorize. It calls _open, _provider, GrantStore.record, ConnectionHooks.fire, label_for, _resume_key, and ConnectResumption.resume in that order of responsibility.

*Call graph*: calls 4 internal fn (_open, _provider, label_for, _resume_key); 4 external calls (__init__, __init__, agent, ws).


##### `ConnectFlow.label_for`  (lines 906–911)

```
def label_for(self, provider: str) -> str
```

**Purpose**: Returns the human-friendly name for a provider. If no label was declared, it turns the provider slug into title-cased words.

**Data flow**: It receives a provider name, looks it up in the labels map, and otherwise replaces underscores with spaces and title-cases the result. It does not touch storage.

**Call relations**: ConnectFlow.complete uses this label in the success record and in the message sent back to the conversation.

*Call graph*: called by 1 (complete).


##### `ConnectFlow._provider`  (lines 913–919)

```
def _provider(self, name: str) -> OAuthProvider
```

**Purpose**: Finds the OAuth descriptor for a provider name. It hides the difference between fixed providers and providers served by an open resolver.

**Data flow**: It receives a provider name, returns a descriptor from the installed provider map if present, asks the resolver otherwise, or raises UnknownProvider if neither can serve it.

**Call relations**: ConnectFlow.authorize, bridge_workspace, and complete all call this before relying on provider-specific OAuth behavior.

*Call graph*: calls 1 internal fn (__init__); called by 3 (authorize, bridge_workspace, complete).


##### `ConnectFlow._open`  (lines 921–926)

```
def _open(self, state: str) -> ConnectState
```

**Purpose**: Decrypts and validates the sealed OAuth state. It rejects state that was tampered with, unreadable, or too old.

**Data flow**: It receives the encrypted state string, asks Fernet to decrypt it within the configured time limit, parses the JSON into ConnectState, and returns that object. Invalid tokens become ConnectStateInvalid errors.

**Call relations**: ConnectFlow.bridge_workspace and ConnectFlow.complete both call this before trusting anything from the browser redirect.

*Call graph*: called by 2 (bridge_workspace, complete); 1 external calls (__init__).


##### `ConnectHandoff.authorize`  (lines 954–1039)

```
async def authorize(self, workspace_id: UUID, turn_id: UUID, member_id: UUID) -> str
```

**Purpose**: Hands a member the OAuth URL for a connect request stored on a conversation turn. It reuses a recent URL when safe, or mints a fresh one when the old one may expire too soon.

**Data flow**: It receives workspace, turn, and member ids. It loads and locks the turn, verifies the terminal connect request still exists, confirms the requesting member and grantee agent are valid, checks for a still-fresh memoized URL, otherwise calls ConnectFlow.authorize, stores the new URL on the turn, and returns the usable URL.

**Call relations**: This is the surface-facing entry for connect buttons. It calls _held to judge cached URLs and calls ConnectFlow.authorize when a new sealed OAuth link is needed.

*Call graph*: calls 1 internal fn (_held); 5 external calls (__init__, model_validate, select, update, workspace_tx).


##### `ConnectHandoff._held`  (lines 1041–1052)

```
def _held(self, url: str | None, authorized_at: datetime | None) -> str | None
```

**Purpose**: Decides whether a stored authorization URL is still fresh enough to give back to the member. This avoids handing out a link whose sealed state may expire during the provider approval flow.

**Data flow**: It receives a URL and timestamp. If either is missing or the timestamp is older than the memo window, it returns none; otherwise it returns the URL.

**Call relations**: ConnectHandoff.authorize calls this before minting a new URL and again after a race where another caller may have stored a fresh URL first.

*Call graph*: called by 1 (authorize); 3 external calls (now, replace, timedelta).


##### `install_connect_flow`  (lines 1058–1066)

```
def install_connect_flow(flow: ConnectFlow | None) -> None
```

**Purpose**: Installs the process-wide ConnectFlow singleton. This lets tools, surfaces, and callbacks find the same configured OAuth machinery without passing it everywhere.

**Data flow**: It receives a ConnectFlow or none and stores it in a module-level variable. It returns nothing.

**Call relations**: Startup or tests call this to configure the installed flow; installed_connect_flow later reads the value.


##### `installed_connect_flow`  (lines 1069–1072)

```
def installed_connect_flow() -> ConnectFlow
```

**Purpose**: Returns the configured ConnectFlow, or fails loudly if connect support is unavailable. This makes missing credential configuration obvious.

**Data flow**: It reads the module-level installed flow. If present it returns it; if absent it raises ConnectUnavailable.

**Call relations**: connect_bridge_workspace calls this before validating bridge requests. Other application paths can use the same accessor to require connect support.

*Call graph*: called by 1 (connect_bridge_workspace); 1 external calls (__init__).


##### `connect_bridge_workspace`  (lines 1075–1084)

```
def connect_bridge_workspace(request: Request) -> UUID | None
```

**Purpose**: Extracts and verifies the workspace for a connector browser bridge request. It returns none instead of raising when the request should be rejected.

**Data flow**: It reads state, provider, and callback query parameters from the incoming request, asks the installed ConnectFlow to verify them, and returns the workspace id. Invalid state, missing configuration, or unknown provider becomes none.

**Call relations**: This is a safe wrapper around installed_connect_flow and ConnectFlow.bridge_workspace for request-routing code.

*Call graph*: calls 1 internal fn (installed_connect_flow).


##### `account_object_name`  (lines 1091–1100)

```
def account_object_name(provider: str, account_id: str) -> str
```

**Purpose**: Creates a stable, readable object name for a provider account. The name includes a short hash so two similar-looking provider/account slugs do not collide.

**Data flow**: It receives provider and account id strings, slugifies both, truncates the readable part to fit the object-name limit, hashes the full identity, and returns a combined name.

**Call relations**: It calls _slug for the readable pieces and uses SHA-256 for the collision-resistant suffix. Other surfaces can use this to name the same connection consistently.

*Call graph*: calls 1 internal fn (_slug); 1 external calls (sha256).


##### `_slug`  (lines 1103–1104)

```
def _slug(raw: str) -> str
```

**Purpose**: Turns arbitrary text into a lowercase dash-separated slug. A slug is a simple safe name made from letters, numbers, and dashes.

**Data flow**: It receives raw text, lowercases it, replaces runs of non-letter-or-number characters with dashes, trims edge dashes, and returns the cleaned string.

**Call relations**: account_object_name calls this for provider and account id before adding the digest suffix.

*Call graph*: called by 1 (account_object_name); 1 external calls (sub).


##### `grant_summaries`  (lines 1107–1115)

```
async def grant_summaries() -> tuple[GrantSummary, ...]
```

**Purpose**: Lists connector grants for the currently targeted agent in the current workspace. It is an audit-style view for one agent’s access.

**Data flow**: It builds a scope from the current workspace and object-agent id, delegates the database query to _grant_summaries, and returns GrantSummary objects.

**Call relations**: This is a narrow wrapper over _grant_summaries for agent-specific surfaces.

*Call graph*: calls 1 internal fn (_grant_summaries); 3 external calls (and_, object_agent_id, ws_current).


##### `workspace_grant_summaries`  (lines 1118–1121)

```
async def workspace_grant_summaries(workspace_id: UUID) -> tuple[GrantSummary, ...]
```

**Purpose**: Lists all connector grants in a workspace for an operator or admin-style surface. It is broader than the current-agent view.

**Data flow**: It receives a workspace id, enters that workspace context, asks _grant_summaries for all grants in that workspace, and returns the summaries.

**Call relations**: This shares the same query builder as grant_summaries but supplies a workspace-wide scope.

*Call graph*: calls 1 internal fn (_grant_summaries); 1 external calls (ws).


##### `_grant_summaries`  (lines 1124–1175)

```
async def _grant_summaries(scope: sa.ColumnElement[bool]) -> tuple[GrantSummary, ...]
```

**Purpose**: Runs the shared database query behind grant summary views. It joins grants to connections, agents, and owner members so the result is useful to humans.

**Data flow**: It receives a SQL scope condition, queries matching grant rows with provider, account, host, owner email, agent name, conversation, timestamps, and sharing flag, then returns GrantSummary objects ordered by provider and agent.

**Call relations**: grant_summaries and workspace_grant_summaries both call this to avoid duplicating the same join and formatting logic.

*Call graph*: called by 2 (grant_summaries, workspace_grant_summaries); 4 external calls (__init__, and_, select, workspace_tx).


##### `connection_summaries`  (lines 1178–1255)

```
async def connection_summaries() -> tuple[ConnectionSummary, ...]
```

**Purpose**: Lists the workspace’s member-owned connections and the agents currently granted each one. This gives a connection-centered view rather than a grant-centered view.

**Data flow**: It queries connections joined to owners and optionally grants/agents, groups rows by provider and account id, collects agent names, and returns ConnectionSummary objects with sorted agent lists.

**Call relations**: Surfaces that show connected accounts use this to explain who owns each connection and which agents can use it.

*Call graph*: 5 external calls (__init__, and_, select, workspace_tx, ws_current).


##### `main_agent_connections`  (lines 1258–1293)

```
async def main_agent_connections() -> tuple[MainAgentConnection, ...]
```

**Purpose**: Lists connections granted to the workspace’s main agent. Feed registration can use this to know which connected accounts are available without being explicitly told.

**Data flow**: It queries connections joined through connector grants to agents marked as main, orders them by provider and account, and returns MainAgentConnection records.

**Call relations**: Feed-related code can call this to sync accounts available to the main agent while ignoring connections granted only to other shipped agents.

*Call graph*: 4 external calls (__init__, select, workspace_tx, ws_current).


### Egress Policy Enforcement
Turns runtime identity, model choices, manifests, credentials, and grants into network rules and proxy control responses.

### `core/src/ufo/runtime/access/egress_resolver.py`

`domain_logic` · `request handling`

This file is the control-room for outbound network permissions. When the data-plane proxy sees a connection request, it does not decide policy by itself and it does not touch stored secrets directly. Instead, it asks this resolver: “Given this token, which doors may this agent open, and which keys may be added?”

The main class, PerAgentRules, builds the answer fresh each time. That matters because permissions can change while a run is active: a turn may finish, a member may lose their seat, a credential slot may be filled, or an OAuth grant may be revoked or added. Fresh lookup prevents old tokens from continuing to draw private keys.

The resolver starts with a base set of rules. If the token is missing, that base is all it returns. If the token names a live run or an unexpired probe, it looks up the matching workspace, agent, and acting member in the database. It then adds only the extras that are allowed for that situation: internet access, the internal tool bridge for normal runs, cache hosts, preview-site authentication, workspace credential injections, OAuth grant forwarding, and command-line credential forwarding.

A probe is treated almost like a run, but with one important safety difference: it is not allowed to receive the deployment’s own model API key injection. That check is enforced here, close to the network boundary, rather than trusting the sandbox environment to omit it.

#### Function details

##### `_seat_scope`  (lines 43–54)

```
def _seat_scope(workspace_id: UUID, member_id: UUID | None) -> tuple[sa.ColumnElement[bool], ...]
```

**Purpose**: This helper adds a database condition that says, “if a member is involved, they must still belong to this workspace and still have an active seat.” It is used to stop revoked or unseated members from continuing to use private access through old tokens.

**Data flow**: It receives a workspace ID and maybe a member ID. If there is no member ID, it returns no extra condition. If there is a member ID, it builds a database existence check that only passes when that member exists in the workspace and has a non-empty seated timestamp.

**Call relations**: The run, probe, and liveness checks call this before trusting a token tied to a member. It hands those database queries an extra safety filter, so the rest of the resolver only sees authorities that are still allowed to act.

*Call graph*: called by 4 (_conversation_of, _turn_of, probe_live, turn_live); 2 external calls (exists, select).


##### `PerAgentRules.resolve`  (lines 97–151)

```
async def resolve(self, principal: EgressPrincipal | None) -> tuple[Rule, ...]
```

**Purpose**: This is the main rule builder. Given a run token, a probe token, or no token, it returns the exact network rules the proxy should enforce for that request.

**Data flow**: It starts with the principal presented by the proxy. With no principal, it returns only the base rules. With a run or probe token, it enters that token’s workspace, checks whether the referenced run or conversation is still valid, then assembles rules from the base policy, allowed internet policy, internal service hosts, preview authentication, stored credential slots, OAuth grants, and CLI forwarding. For probes, it removes the model-key injection before returning the final tuple of rules.

**Call relations**: The proxy-facing flow depends on this method whenever it needs the full rule set for a connection. It calls _turn_of for run tokens and _conversation_of for probe tokens to learn whose authority the token carries. It then delegates credential, grant, and CLI rule creation to the egress rule helpers, and finally calls _without_the_model_key for probe-specific safety.

*Call graph*: calls 3 internal fn (_conversation_of, _turn_of, _without_the_model_key); 7 external calls (__init__, __init__, derive_cli_rules, derive_credential_rules, derive_grant_rules, agent, ws).


##### `PerAgentRules._turn_of`  (lines 153–188)

```
async def _turn_of(self, run: RunToken) -> _Authority | None
```

**Purpose**: This method verifies that a run token points to a real, currently running turn and finds the agent and internet policy for that turn. It also carries forward the acting member, if there is one.

**Data flow**: It receives a run token. Inside the token’s workspace database scope, it looks up the turn joined to its agent, requires the turn to be running, requires both records to belong to the same workspace, and applies the member seat check. If no matching row exists, it returns nothing. If the row exists, it reads any turn runtime configuration and returns an authority object containing the agent ID, whether internet access is effectively allowed, and the acting member ID.

**Call relations**: PerAgentRules.resolve calls this when it is resolving a normal run token. The authority it returns becomes the basis for all later additions: agent-scoped grants, internet rules, CLI forwarding, and service access.

*Call graph*: calls 1 internal fn (_seat_scope); called by 1 (resolve); 4 external calls (__init__, model_validate, select, workspace_tx).


##### `PerAgentRules._conversation_of`  (lines 190–222)

```
async def _conversation_of(self, probe: ProbeToken) -> _Authority | None
```

**Purpose**: This method verifies that a probe token is still usable and finds the agent and internet policy for the conversation being probed. A probe names a conversation rather than a specific running turn, so this follows a different database path than _turn_of.

**Data flow**: It receives a probe token. First it checks the token expiration time against the current time. If the token is expired, it returns nothing. Otherwise it looks up the conversation joined to its agent within the token’s workspace, applies the member seat check, and returns an authority object with the agent ID, the agent’s internet access flag, and the token’s acting member ID. If the conversation or valid seat is not found, it returns nothing.

**Call relations**: PerAgentRules.resolve calls this for probe tokens. The returned authority lets probes use the same agent-level credentials and grants as a normal turn, while resolve later applies the probe-only rule that withholds the model key.

*Call graph*: calls 1 internal fn (_seat_scope); called by 1 (resolve); 4 external calls (__init__, now, select, workspace_tx).


##### `PerAgentRules._without_the_model_key`  (lines 224–235)

```
def _without_the_model_key(self, rules: tuple[Rule, ...]) -> tuple[Rule, ...]
```

**Purpose**: This method removes any rule that would inject the deployment’s own model key. It exists so probe executions cannot accidentally inherit the system’s model authentication secret.

**Data flow**: It receives a tuple of already-built rules. It scans them and keeps every rule except an injection rule whose sentinel marker refers to the model key. The returned tuple is the same policy minus that sensitive injection.

**Call relations**: PerAgentRules.resolve calls this only at the end of probe-token resolution. That means probes still get other allowed access, such as workspace credentials or grants, but they do not receive the deployment model key.

*Call graph*: called by 1 (resolve).


##### `PerAgentRules.turn_live`  (lines 237–269)

```
async def turn_live(self, run: RunToken) -> int | None
```

**Purpose**: This is a fast liveness gate for run tokens. It answers whether the turn is still running and, if so, returns the current egress-rule generation number for cache coordination.

**Data flow**: It receives a run token, enters that token’s workspace, and queries the database for the turn status plus the workspace’s egress rules generation. The query also checks that the acting member, if present, still has a seat. If the turn is missing or no longer running, it returns nothing. If the turn is live, it returns the generation number.

**Call relations**: The proxy can use this before allowing a CONNECT request or before trusting cached rules. It shares the same seat-check helper as the full resolver, so a finished turn or revoked member is cut off before secrets are injected onto the wire.

*Call graph*: calls 1 internal fn (_seat_scope); 3 external calls (select, workspace_tx, ws).


##### `PerAgentRules.probe_live`  (lines 271–293)

```
async def probe_live(self, probe: ProbeToken) -> int | None
```

**Purpose**: This is the liveness gate for probe tokens. It answers whether the probe is unexpired, still tied to an existing conversation, and allowed under the current workspace rules generation.

**Data flow**: It receives a probe token. It first rejects expired tokens by comparing their expiration timestamp to the current time. If still valid, it enters the token’s workspace and queries for the workspace egress-rule generation through the referenced conversation, again applying the member seat check. It returns the generation number if everything is valid, or nothing if not.

**Call relations**: The proxy can use this to decide whether a probe connection may proceed or whether cached rules are still tied to the current workspace policy. It mirrors turn_live, but follows the conversation path because probes do not name a turn.

*Call graph*: calls 1 internal fn (_seat_scope); 4 external calls (now, select, workspace_tx, ws).


### `core/src/ufo/runtime/access/egress_control.py`

`io_transport` · `request handling`

The Rust egress proxy sits on the network path, but it deliberately does not know customer secrets, workspace database details, or the full policy rules. This file gives that proxy a small private API under `/internal/egress/`. Think of it like a security guard at a locked supply room: the proxy can ask, “Is this pass valid?”, “What rules apply?”, “Please forward this approved request,” or “Here is the usage I observed,” but it cannot make the rules itself.

Every request to the main egress routes must carry a shared control token in the HTTP `Authorization` header. Inside the request body, the proxy also sends a run or probe token. A run token represents a live sandbox run; a probe token represents a limited check. `EgressControl` verifies those tokens, checks whether the run or probe is still live, asks `PerAgentRules` for the current rules, and turns those rules into the exact JSON shape the Rust proxy expects.

The file also receives metering records and writes them to the billing ledger, including special handling for model token usage. It can forward approved requests through connector-specific forwarders, dispatch tool bridge calls for live runs, and provide Git credentials to a cache daemon through a separate route protected by a separate token. That separation matters: the cache daemon can ask only for Git credentials, not for the broader egress control surface.

#### Function details

##### `rule_json`  (lines 51–80)

```
def rule_json(rule: Rule) -> dict[str, object]
```

**Purpose**: This turns one internal egress rule into the simple JSON form that the Rust proxy understands. It is the translation layer between Python policy objects and the proxy’s wire format.

**Data flow**: It receives a rule object, checks which kind of rule it is, and builds a dictionary with a `kind` label plus the fields needed for that rule. The result is plain data that can be serialized as JSON and sent to the proxy; for forward rules, it leaves out the actual Python forwarding function because the proxy only needs to recognize the match.

**Call relations**: When `EgressControl._resolve` has collected the allowed rules for a run or probe, it calls `rule_json` for each one. The translated list is then returned to the Rust proxy so the proxy can enforce those rules on network traffic.

*Call graph*: called by 1 (_resolve).


##### `EgressControl.router`  (lines 173–180)

```
def router(self) -> APIRouter
```

**Purpose**: This builds the private FastAPI router for the main egress control endpoints. FastAPI is the web framework used here to expose HTTP routes.

**Data flow**: It starts with the `EgressControl` object’s methods and creates a router under `/internal/egress`. It attaches a shared-token guard to every route, then registers endpoints for authorization, rule resolution, metering, forwarding, and tool bridge requests. The output is a router that the main server can mount.

**Call relations**: This is used during server setup to make the egress control API available. The guard it attaches means calls must pass through `EgressControl._guard` before reaching the route methods.

*Call graph*: 2 external calls (APIRouter, Depends).


##### `EgressControl.git_credential_router`  (lines 182–187)

```
def git_credential_router(self) -> APIRouter
```

**Purpose**: This builds a separate private router for the Git credential callback used by the cache daemon. It exists so the cache daemon receives only the narrow access it needs.

**Data flow**: It creates a router under `/internal`, protects it with the cache-specific token guard, and registers the `/git-credential` endpoint. The result is a router that can be mounted separately from the broader egress control API.

**Call relations**: This is part of server setup, like `EgressControl.router`, but it uses `EgressControl._cache_guard` instead of the main egress guard. That keeps the cache daemon’s credential from being accepted on the secrets-and-metering routes.

*Call graph*: 2 external calls (APIRouter, Depends).


##### `EgressControl._guard`  (lines 189–191)

```
async def _guard(self, authorization: Annotated[str, Header()]='') -> None
```

**Purpose**: This checks whether a request to the main egress control API has the correct bearer token. A bearer token is a shared secret sent in an HTTP header to prove the caller is allowed in.

**Data flow**: It reads the `Authorization` header. If the header exactly matches `Bearer <control_token>`, it allows the request to continue by returning nothing. If it does not match, it raises an HTTP 401 error, which stops the request as unauthorized.

**Call relations**: The router made by `EgressControl.router` installs this guard on all main `/internal/egress` routes. It runs before authorization, resolve, meter, forward, or tool bridge logic does any sensitive work.

*Call graph*: 1 external calls (HTTPException).


##### `EgressControl._cache_guard`  (lines 193–195)

```
async def _cache_guard(self, authorization: Annotated[str, Header()]='') -> None
```

**Purpose**: This checks whether a request to the Git credential endpoint has the separate cache bearer token. It prevents the cache daemon’s limited credential from being mixed with the broader egress control credential.

**Data flow**: It reads the `Authorization` header and compares it to `Bearer <cache_control_token>`. A match lets the request proceed. A mismatch raises an HTTP 401 error and stops the request.

**Call relations**: The router made by `EgressControl.git_credential_router` installs this guard on `/internal/git-credential`. It is intentionally separate from `EgressControl._guard` so each internal caller gets only the access it needs.

*Call graph*: 1 external calls (HTTPException).


##### `EgressControl._authorize`  (lines 197–200)

```
async def _authorize(self, body: AuthorizeRequest) -> AuthorizeResponse
```

**Purpose**: This tells the proxy whether a run or probe token is valid and currently live. The proxy uses this as a quick gate before allowing a connection.

**Data flow**: It receives a request body containing the raw proxy authorization value. It asks `EgressControl._principal` to decode that value into a run or probe identity, then asks `EgressControl._live_generation` whether that identity is still active. It returns an `AuthorizeResponse` saying whether access is authorized and, if so, which rule generation applies.

**Call relations**: This is one of the routes registered by `EgressControl.router`. It relies on `_principal` for token verification and `_live_generation` for the liveness check, then hands the proxy a compact yes-or-no answer plus a generation number for caching.

*Call graph*: calls 2 internal fn (_live_generation, _principal); 1 external calls (__init__).


##### `EgressControl._live_generation`  (lines 202–207)

```
async def _live_generation(self, principal: EgressPrincipal) -> int | None
```

**Purpose**: This checks whether a decoded run or probe is still allowed to use egress, and returns the current rule generation if it is. A generation is a version number that helps the proxy know whether cached rules are still current.

**Data flow**: It receives either a run token or a probe token. For a run token, it asks the rule resolver whether the turn is live; for a probe token, it asks whether the probe is live. It returns an integer generation when live, or `None` when not live.

**Call relations**: `EgressControl._authorize` uses this to answer the proxy’s liveness question. `EgressControl._forward` uses it again before forwarding traffic, so a forwarded request cannot slip through after a run or probe has stopped.

*Call graph*: called by 2 (_authorize, _forward).


##### `EgressControl._resolve`  (lines 209–211)

```
async def _resolve(self, body: ResolveRequest) -> dict[str, object]
```

**Purpose**: This returns the concrete egress rules that apply to a valid run or probe token. The proxy uses these rules to decide what network traffic to allow, meter, rewrite, inject, or forward.

**Data flow**: It receives a body with the raw proxy authorization value, decodes it with `EgressControl._principal`, and asks the resolver for the matching rule list. It converts every rule with `rule_json` and returns a JSON object containing the list.

**Call relations**: This route is registered by `EgressControl.router` and is called by the Rust proxy when it needs the rule set. It bridges Python’s policy objects to the proxy’s JSON format.

*Call graph*: calls 2 internal fn (_principal, rule_json).


##### `EgressControl._meter`  (lines 213–268)

```
async def _meter(self, body: MeterRequest) -> dict[str, object]
```

**Purpose**: This receives usage reports from the proxy and records them for metrics and billing. It groups many small observations into fewer ledger writes so accounting stays efficient and accurate.

**Data flow**: It receives a list of records. Metric records are counted by host and dimension and emitted as monitoring metrics. Egress records are counted by workspace and turn, and token records are summed by workspace, turn, and model. It then enters each workspace context, opens a workspace database transaction, writes probe egress, run egress, and model token usage as appropriate, and returns an empty response.

**Call relations**: This route is registered by `EgressControl.router` and is called after the proxy observes billable activity. When recording model tokens, it calls `EgressControl._priced_cache_write` so token usage is shaped the same way the billing system expects.

*Call graph*: calls 1 internal fn (_priced_cache_write); 7 external calls (__init__, workspace_tx, emit_metric, record_egress_request, record_probe_egress_request, record_sandbox_tokens, ws).


##### `EgressControl._priced_cache_write`  (lines 270–283)

```
def _priced_cache_write(self, model: str, usage: Usage) -> Usage
```

**Purpose**: This adjusts model token usage when a model does not have a separate price for 30-minute cache writes. In that case, those tokens must be billed like normal input tokens instead.

**Data flow**: It receives a model name and a `Usage` object containing token counts. It looks up the model’s pricing. If 30-minute cache writes are priced, or there are none, it returns the usage unchanged. Otherwise, it creates a copy where the 30-minute cache-write count is moved into input tokens and set to zero.

**Call relations**: `EgressControl._meter` calls this just before writing sandbox token usage to accounting. This keeps proxy-reported token details aligned with how the rest of the host-side billing adapters charge the same work.

*Call graph*: called by 1 (_meter); 1 external calls (model_copy).


##### `EgressControl._forward`  (lines 285–318)

```
async def _forward(self, body: ForwardRequest) -> ForwardResponse
```

**Purpose**: This performs an approved forwarded HTTP request on behalf of the proxy. It is used when a rule says traffic to a certain host must go through a connector-specific broker rather than directly over the wire.

**Data flow**: It receives the proxy token, target account, HTTP method, URL, headers, and a base64-encoded body. It decodes and validates the token, checks that the URL is HTTPS with a hostname, confirms the run or probe is still live, and resolves the current rules again. If it finds a matching forward rule for the host and account, it decodes the body, calls the rule’s forwarder, base64-encodes the response body, and returns status, headers, and body. If any check fails, it raises HTTP 403.

**Call relations**: This route is registered by `EgressControl.router` and is called by the Rust proxy only for forward-rule traffic. It reuses `_principal` and `_live_generation` so forwarding is tied to the same token and liveness checks as ordinary egress authorization.

*Call graph*: calls 2 internal fn (_live_generation, _principal); 5 external calls (__init__, b64decode, b64encode, HTTPException, urlsplit).


##### `EgressControl._tool_bridge`  (lines 320–325)

```
async def _tool_bridge(self, body: ToolBridgeControlRequest) -> ToolBridgeResponse
```

**Purpose**: This lets the proxy send a bounded tool bridge request into the host side for a live run. It is restricted to run tokens, not probe tokens.

**Data flow**: It receives a proxy authorization value and a tool bridge request. It decodes the principal with `EgressControl._principal`; if the result is not a run token, or no bridge is configured, it raises HTTP 403. Otherwise, it enters the run’s workspace context and sends the request to the configured bridge, returning the bridge response.

**Call relations**: This route is registered by `EgressControl.router`. It uses the same run-token authority as the egress control flow, then hands the actual tool dispatch to the configured `ToolBridgeRequester`.

*Call graph*: calls 1 internal fn (_principal); 2 external calls (HTTPException, ws).


##### `EgressControl._git_credential`  (lines 327–341)

```
async def _git_credential(self, body: GitCredentialRequest) -> dict[str, object]
```

**Purpose**: This answers the cache daemon’s request for a Git credential for one workspace and host. If no matching credential is available, it tells the daemon to fetch publicly instead of leaking some other credential.

**Data flow**: It receives an optional workspace ID and host. If either is missing, or credential support is not configured, it returns a public identity. Otherwise, it enters that workspace context and asks `EgressControl._git_credential_for` to find the matching username and secret. If found, it returns them with a workspace-specific principal label; if not, it returns a public result with no credential.

**Call relations**: This endpoint is mounted by `EgressControl.git_credential_router`, not the main egress router. It calls `_git_credential_for` for the actual lookup while the separate cache guard keeps access narrow.

*Call graph*: calls 1 internal fn (_git_credential_for); 1 external calls (ws).


##### `EgressControl._git_credential_for`  (lines 343–369)

```
async def _git_credential_for(self, workspace_id: UUID, host: str) -> tuple[str, str] | None
```

**Purpose**: This searches the configured credential slots for the Git username and token that match a specific workspace and host. It mirrors the same matching idea used by proxy-side credential injection.

**Data flow**: It receives a workspace ID and host. It walks through the resolver’s credential slots, skipping slots that are not Git basic-auth slots. For each possible slot, it checks whether the slot is set for the workspace, whether the stored credential host equals the requested host, and then reads the secret. If a usable secret is found, it returns the configured username and secret. If a slot fails while being checked, it logs a warning and keeps looking; if none match, it returns `None`.

**Call relations**: `EgressControl._git_credential` calls this after it has entered the right workspace context. This function does the careful per-slot lookup and deliberately does not fall through to unrelated identities when one slot cannot be resolved.

*Call graph*: called by 1 (_git_credential); 4 external calls (warn, credential_host, slot_is_set, slot_secret).


##### `EgressControl._principal`  (lines 371–381)

```
def _principal(self, proxy_auth: str) -> EgressPrincipal | None
```

**Purpose**: This verifies and decodes the raw proxy authorization value into the identity it represents. That identity is either a run token, a probe token, or no valid principal at all.

**Data flow**: It receives the raw authorization string from the request body. If it is empty, it returns `None`. Otherwise, it first tries to decode it as a run token using the configured run token codec. If that fails, it tries to decode it as a probe token using the same secret. If both attempts fail, it returns `None`.

**Call relations**: `EgressControl._authorize`, `_resolve`, `_forward`, and `_tool_bridge` all call this before trusting a request body’s run or probe identity. It is the common doorway from raw proxy-supplied text into a verified principal object.

*Call graph*: called by 4 (_authorize, _forward, _resolve, _tool_bridge); 1 external calls (__init__).


### `core/src/ufo/runtime/access/egress_rules.py`

`domain_logic` · `per run / request setup before sandbox network access`

A sandboxed agent should not be able to freely call any website or see raw secrets. This file builds the rulebook for the egress proxy, which is the gatekeeper for outbound network traffic. Think of it like a security desk: it checks which doors are open, which badge to substitute at the door, and which visits must be counted for billing or limits.

The file defines several small rule types. A ScopeRule says exactly which hosts are allowed. An InternetRule allows broader public internet access for live turns that need it. An InjectionRule lets the sandbox use a harmless placeholder value while the proxy swaps in the real secret only when sending the request. A MeterRule says requests to a host should be counted. A ForwardRule sends certain credentialed requests through a broker instead of exposing a token locally. A ServiceRule describes special local service hosts.

The derivation functions then build these rules from different sources. The chosen AI model opens only its provider host and injects the model key. Extension manifests may allow internet. S3 artifact storage opens its storage host so files can be shared. Credential slots add per-workspace secrets, but failures are logged and skipped so one broken credential does not block all egress. Grants allow connector hosts and file-transfer hosts, while CLI credentials may be forwarded through the broker.

#### Function details

##### `provider_host`  (lines 112–116)

```
def provider_host(model: str) -> str
```

*Call graph*: called by 1 (derive_model_rules).


##### `derive_model_rules`  (lines 119–134)

```
def derive_model_rules(model: str, real_key: str) -> tuple[Rule, ...]
```

*Call graph*: calls 1 internal fn (provider_host); 3 external calls (__init__, __init__, __init__).


##### `derive_manifest_rules`  (lines 137–139)

```
def derive_manifest_rules(manifests: tuple[Manifest, ...]) -> tuple[InternetRule, ...]
```

*Call graph*: 1 external calls (__init__).


##### `derive_artifact_store_rules`  (lines 142–158)

```
async def derive_artifact_store_rules(blob: FilesystemBlobStore | S3BlobStore) -> tuple[Rule, ...]
```

*Call graph*: 3 external calls (__init__, __init__, put_host).


##### `derive_credential_rules`  (lines 161–224)

```
async def derive_credential_rules(slots: tuple[CredentialSlot, ...], workspace_id: UUID, store: CredentialStore) -> tuple[Rule, ...]
```

*Call graph*: 7 external calls (__init__, __init__, __init__, b64encode, warn, credential_host, slot_secret).


##### `derive_grant_rules`  (lines 227–244)

```
def derive_grant_rules(grants: tuple[Grant, ...], transfer_hosts: 'ConnectorTransferHosts | None'=None) -> tuple[Rule, ...]
```

*Call graph*: 2 external calls (__init__, __init__).


##### `derive_cli_rules`  (lines 247–267)

```
def derive_cli_rules(grants: tuple[Grant, ...], acting_member_id: UUID | None, clis: Mapping[str, CliCredential]) -> tuple[Rule, ...]
```

*Call graph*: 2 external calls (__init__, grant_sentinel).


##### `ConnectorTransferHosts.of`  (lines 281–282)

```
def of(self, provider: str) -> tuple[str, ...]
```


##### `connector_transfer_hosts`  (lines 285–296)

```
def connector_transfer_hosts(manifests: tuple[Manifest, ...]) -> ConnectorTransferHosts
```

*Call graph*: 2 external calls (__init__, open_connector_namespace).


### Billing Ledgers and Limits
Defines pricing, prepaid balances, accounting records, spend limits, and billing package boundaries for workspace usage.

### `core/src/ufo/runtime/billing/accounting.py`

`domain_logic` · `cross-cutting: turn admission, usage recording, billing export, and spend reporting`

This file keeps the project’s money records honest. Each model call, sandbox network request, generated image, or generated video becomes a row in a ledger, which is like a checkbook for workspace usage. Token usage is priced in tiny units called micro-USD, meaning millionths of a US dollar, so costs can be tracked precisely.

The file is careful about retries. A turn may be replayed after a crash, so `record_turn_usage` stores cumulative usage for one attempt and only charges the new difference. That prevents both double-charging and losing real provider cost. Other usage, such as sandbox tokens or media generation, is accumulated safely so two writes at the same time do not overwrite each other.

It also decides whether work may start or continue. `BalanceGate` checks prepaid balance, while `SpendEvaluator` checks configurable caps for a whole workspace, a member, or an agent. If limits are reached, a turn may be rejected or parked, meaning held until conditions improve.

Finally, `SpendRollup` reads the ledger back into human-facing reports, and the export functions freeze usage deltas for external billing consumers so retries send the exact same billable facts.

#### Function details

##### `applicable_caps_absent`  (lines 58–64)

```
def applicable_caps_absent(workspace_id: UUID, member_id: UUID | None, agent_id: UUID) -> bool
```

**Purpose**: This is a quick memory check that says whether the system recently found no spend caps for a specific workspace, member, and agent combination. It exists to avoid unnecessary database reads in the common case where no caps are configured.

**Data flow**: It receives the workspace id, optional member id, and agent id. It looks up that exact triple in a small in-memory cache and compares the stored expiry time with the current monotonic clock. It returns true only if the cache entry still exists and has not expired.

**Call relations**: This is the fast path that other admission code can consult before doing a full cap check. It does not call back into the database; it only uses the clock and the cache that `SpendEvaluator.decide` updates through `_note_absent_caps`.

*Call graph*: 1 external calls (monotonic).


##### `_note_absent_caps`  (lines 67–76)

```
def _note_absent_caps(key: tuple[UUID, UUID | None, UUID]) -> None
```

**Purpose**: This remembers, briefly, that no spend caps applied to one workspace/member/agent triple. The point is speed, not correctness: the cache expires quickly so new caps take effect soon.

**Data flow**: It receives a cache key made from workspace id, member id, and agent id. If the cache is at its soft size limit, it removes entries whose expiry time has passed, then stores a new expiry time a few seconds in the future.

**Call relations**: `SpendEvaluator.decide` calls this only after it has checked the database and found no applicable caps. Later, `applicable_caps_absent` can use the saved result to skip a cap-checking database round trip.

*Call graph*: called by 1 (decide); 1 external calls (monotonic).


##### `_total_tokens`  (lines 79–87)

```
def _total_tokens(usage: Usage) -> int
```

**Purpose**: This adds all token categories in a usage record into one total. It gives the billing ledger a single token count while still allowing detailed token classes to be stored separately.

**Data flow**: It receives a `Usage` object containing input, output, cache-read, and cache-write token counts. It adds those fields together. It returns the total token count as an integer.

**Call relations**: `record_turn_usage`, `record_workspace_usage`, and `record_sandbox_tokens` call this before deciding whether there is anything to bill and before writing the ledger row.

*Call graph*: called by 3 (record_sandbox_tokens, record_turn_usage, record_workspace_usage).


##### `_prompt_tokens`  (lines 90–99)

```
def _prompt_tokens(usage: Usage) -> int
```

**Purpose**: This counts the tokens the model read as the prompt, including cached prompt tokens. It is used to later show what share of a prompt came from cache.

**Data flow**: It receives a `Usage` object. It adds input tokens, cache-read tokens, and cache-write tokens, but not output tokens. It returns that prompt-side total.

**Call relations**: The token-recording functions call this when they write ledger rows. `read_turn_cost` later uses the stored prompt and cache-read counts to compute a cache percentage.

*Call graph*: called by 3 (record_sandbox_tokens, record_turn_usage, record_workspace_usage).


##### `workspace_owns_the_key`  (lines 102–125)

```
async def workspace_owns_the_key(connection: AsyncConnection, workspace_id: UUID, key_slot: str | None) -> bool
```

**Purpose**: This answers whether a workspace has stored its own provider key for a given key slot. That matters because work paid directly by the workspace’s own provider account should not also be charged against UFO balance.

**Data flow**: It receives a database connection, workspace id, and optional key slot. If no slot is given, it returns false. Otherwise it asks the credential table whether that workspace has a credential row for the slot, and returns true or false.

**Call relations**: `BalanceGate._workspace_serves_itself` calls this while deciding whether low-balance work can still start because the workspace, not the platform, will pay the model provider.

*Call graph*: called by 1 (_workspace_serves_itself); 3 external calls (exists, scalar, select).


##### `record_turn_usage`  (lines 128–263)

```
async def record_turn_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, usage: Usage, attempt: str='', pricing: Pricing=CORE_PRICING, byok: bool=False) -> None
```

**Purpose**: This records and bills token usage for one turn attempt. It is designed to be safe when workflow steps replay after a crash, so the same work is not charged twice.

**Data flow**: It receives a connection, workspace id, turn id, model name, usage counters, attempt id, pricing table, and whether the workspace brought its own key. It totals and prices the usage, finds the deterministic ledger row for that attempt, and either inserts it or advances it to a larger cumulative snapshot. It debits only the new billable difference unless `byok` says the workspace paid the provider directly.

**Call relations**: Turn-running code calls this after model usage is known. It uses `_total_tokens`, `_prompt_tokens`, pricing, ledger ids, and balance `debit`; if a replay or conflicting snapshot would make the ledger unsafe, it raises `TurnUsageConflict` instead of guessing.

*Call graph*: calls 3 internal fn (micro_usd, _prompt_tokens, _total_tokens); 7 external calls (__init__, execute, insert, select, update, debit, ledger_id_for).


##### `read_turn_cost`  (lines 277–307)

```
async def read_turn_cost(connection: AsyncConnection, turn_id: UUID, dimension: str) -> TurnCost | None
```

**Purpose**: This reads what a turn spent for one kind of usage, such as host-side tokens or sandbox tokens. It gives terminal UI or reporting code a clean summary instead of raw ledger rows.

**Data flow**: It receives a connection, turn id, and ledger dimension name. It sums matching ledger rows across all attempts, calculates cache percentage from stored prompt and cache-read tokens, and returns a `TurnCost`. If no matching usage exists, it returns null.

**Call relations**: This is a read-side partner to the recording functions. Rows written by `record_turn_usage` or `record_sandbox_tokens` become the facts that this function summarizes.

*Call graph*: 3 external calls (__init__, execute, select).


##### `record_workspace_usage`  (lines 310–359)

```
async def record_workspace_usage(connection: AsyncConnection, workspace_id: UUID, model: str, usage: Usage, pricing: Pricing=CORE_PRICING, byok: bool=False) -> None
```

**Purpose**: This bills model usage from a background workspace job that is not attached to a turn. It lets workspace-level spend and caps include automated work as well as user-facing turns.

**Data flow**: It receives workspace id, model, usage, pricing, and a bring-your-own-key flag. It totals and prices the usage, debits the workspace unless the workspace paid directly, and inserts a fresh ledger row with no turn id.

**Call relations**: Background job code calls this when a real provider call completes outside a turn. It shares token-totaling, prompt-totaling, pricing, and balance debit behavior with `record_turn_usage`, but does not use replay replacement because each job completion is a separate event.

*Call graph*: calls 3 internal fn (micro_usd, _prompt_tokens, _total_tokens); 4 external calls (execute, insert, debit, uuid4).


##### `record_egress_request`  (lines 362–391)

```
async def record_egress_request(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, amount: int=1) -> None
```

**Purpose**: This counts sandbox network egress requests made during a turn. These requests are metered as counts, not charged as dollars.

**Data flow**: It receives workspace id, turn id, and a request count. It derives the per-turn egress ledger id and inserts a row or atomically adds to the existing count. The cost fields stay zero.

**Call relations**: The sandbox egress proxy calls this when it flushes turn-related network activity. It writes a separate `egress` dimension so it never overlaps with token billing from `record_turn_usage`.

*Call graph*: 2 external calls (execute, ledger_id_for).


##### `record_probe_egress_request`  (lines 394–420)

```
async def record_probe_egress_request(connection: AsyncConnection, workspace_id: UUID, amount: int=1) -> None
```

**Purpose**: This counts sandbox network egress requests made by an off-turn probe. A probe is not tied to a user turn, but its network activity still belongs to the workspace.

**Data flow**: It receives workspace id and a request count. It inserts a new ledger row with no turn id, dimension `egress`, amount equal to the count, and zero price.

**Call relations**: Probe-related proxy code calls this instead of `record_egress_request` because there is no turn id to accumulate under. Workspace-level reports can still see the activity, while member and agent attribution ignore it.

*Call graph*: 3 external calls (execute, insert, uuid4).


##### `record_sandbox_tokens`  (lines 423–499)

```
async def record_sandbox_tokens(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, usage: Usage, pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: This records model tokens used from inside the sandbox through the egress proxy. These are separate from the host turn loop’s own model calls, so they are added as a different ledger dimension.

**Data flow**: It receives workspace id, turn id, model, usage, and pricing. It totals and prices the tokens, debits the workspace, then inserts or atomically increments a per-turn `sandbox_tokens` ledger row with detailed token classes.

**Call relations**: The egress proxy or sandbox model-call path calls this when in-sandbox model usage is observed. It uses `_total_tokens`, `_prompt_tokens`, pricing, balance `debit`, and deterministic ledger ids, and its rows can later be read by `read_turn_cost` and reports.

*Call graph*: calls 3 internal fn (micro_usd, _prompt_tokens, _total_tokens); 3 external calls (execute, debit, ledger_id_for).


##### `record_image_usage`  (lines 502–521)

```
async def record_image_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, images: int, micro_usd: int) -> None
```

**Purpose**: This records generated image usage for a turn. The caller supplies the price because image providers may charge by units that are not normal text tokens.

**Data flow**: It receives workspace id, turn id, model, image count, and cost. It passes those facts to the shared media-recording helper using the `images` dimension.

**Call relations**: Provider extension code calls this after image generation. It delegates the actual ledger write and balance debit to `_record_media_usage`.

*Call graph*: calls 1 internal fn (_record_media_usage).


##### `record_video_usage`  (lines 524–538)

```
async def record_video_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, videos: int, micro_usd: int) -> None
```

**Purpose**: This records generated video usage for a turn. Like images, video cost is supplied by the caller because video pricing may be based on provider-specific units.

**Data flow**: It receives workspace id, turn id, model, video count, and cost. It passes those values to the shared media-recording helper using the `videos` dimension.

**Call relations**: Provider extension code calls this after video generation. It shares the same write path as image usage by handing off to `_record_media_usage`.

*Call graph*: calls 1 internal fn (_record_media_usage).


##### `_record_media_usage`  (lines 541–582)

```
async def _record_media_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, dimension: str, model: str, amount: int, micro_usd: int) -> None
```

**Purpose**: This is the shared ledger writer for generated images and videos. It makes sure media usage both appears in reports and subtracts from prepaid balance.

**Data flow**: It receives the media dimension, workspace id, turn id, model, amount, and cost. It derives a per-turn ledger id, debits the cost, and inserts or atomically increments the ledger row’s amount and cost.

**Call relations**: `record_image_usage` and `record_video_usage` call this so they do not duplicate the same insert-or-add logic. Unlike egress recording, this helper calls balance `debit` because media generation costs real provider money.

*Call graph*: called by 2 (record_image_usage, record_video_usage); 3 external calls (execute, debit, ledger_id_for).


##### `mint_usage_exports`  (lines 607–722)

```
async def mint_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, floor: datetime, key_slot_for: Callable[[str], str | None]) -> None
```

**Purpose**: This freezes unexported ledger growth into export-intent rows for an external billing consumer. Freezing means retries send the same facts instead of recalculating changing totals.

**Data flow**: It receives workspace id, consumer name, a backfill floor time, and a function that maps model names to key slots. It finds ledger rows that have grown beyond what this consumer has already exported, applies settlement rules, determines whether usage was bring-your-own-key, and inserts one intent row per new delta without duplicating existing intents.

**Call relations**: A background export job calls this before reading pending exports. It reads credentials, ledger, turns, and prior ledger exports, then writes `ledger_export` rows that `read_pending_usage_exports` will deliver.

*Call graph*: 5 external calls (now, timedelta, execute, or_, select).


##### `read_pending_usage_exports`  (lines 725–770)

```
async def read_pending_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, limit: int) -> tuple[UsageExport, ...]
```

**Purpose**: This reads frozen usage-export intents that have not yet been acknowledged. It gives an external billing sender a stable batch to deliver.

**Data flow**: It receives workspace id, consumer name, and a limit. It joins export-intent rows to their ledger rows, calculates the delta amount and delta cost, and returns a tuple of `UsageExport` records in creation order.

**Call relations**: Export delivery code calls this after `mint_usage_exports`. If delivery fails before acknowledgement, the same rows remain pending and will be read again unchanged.

*Call graph*: 3 external calls (__init__, execute, select).


##### `ack_usage_exports`  (lines 773–797)

```
async def ack_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: This marks exported usage intents as acknowledged after the external consumer accepts them. Once acknowledged, they stop appearing in pending reads.

**Data flow**: It receives workspace id, consumer name, and the exact `UsageExport` records that were delivered. It builds a match on ledger id and starting amount, then updates matching export rows with acknowledgement and update timestamps.

**Call relations**: The export sender calls this only after a successful external API response. It completes the flow started by `mint_usage_exports` and read by `read_pending_usage_exports`.

*Call graph*: 3 external calls (execute, or_, update).


##### `metered_workspaces`  (lines 800–803)

```
def metered_workspaces() -> WorkspaceCandidates
```

**Purpose**: This finds candidate workspaces for a usage-export job. It intentionally uses a broad rule: any workspace that has ever had ledger usage may need export attention.

**Data flow**: It builds a query for distinct workspace ids from the ledger table and wraps it in the project’s workspace-candidate helper. The result is a `WorkspaceCandidates` object the job runner can iterate.

**Call relations**: Background jobs use this to decide which workspaces to check. The later per-workspace export read can cheaply do nothing if there is no pending work.

*Call graph*: 1 external calls (owner_candidates).


##### `SpendEvaluator.decide`  (lines 839–854)

```
async def decide(self, connection: AsyncConnection, pending_micro_usd: int) -> SpendDecision
```

**Purpose**: This decides whether a turn is allowed, parked, or rejected under spend caps. A spend cap is a configured limit over a rolling time window, like “this agent may spend $10 per day.”

**Data flow**: It receives a database connection and a pending cost that has not yet been written. It loads applicable caps, sums recent ledger spend for each one, adds the pending cost, and compares that with each cap’s limit. It returns a `SpendDecision` with an outcome and, when blocked, a readable message.

**Call relations**: Admission or mid-turn checks call this when spend caps may apply. It calls `_applicable_caps`, `_used_micro_usd`, `_message`, and uses `_note_absent_caps` to speed up later no-cap checks.

*Call graph*: calls 4 internal fn (_applicable_caps, _message, _used_micro_usd, _note_absent_caps); 1 external calls (__init__).


##### `SpendEvaluator._applicable_caps`  (lines 856–884)

```
async def _applicable_caps(self, connection: AsyncConnection) -> tuple[SpendCap, ...]
```

**Purpose**: This loads the spend caps that apply to this exact turn context: the workspace, the member if any, and the agent. It filters out unrelated caps so they cannot block the wrong work.

**Data flow**: It reads the spend cap table for rows in the same workspace whose scope is workspace-wide, the current member, or the current agent. It converts each row into a `SpendCap` value and returns them as a tuple.

**Call relations**: `SpendEvaluator.decide` calls this as its first real step. The returned caps are then checked one by one with `_used_micro_usd`.

*Call graph*: called by 1 (decide); 4 external calls (__init__, execute, or_, select).


##### `SpendEvaluator._used_micro_usd`  (lines 886–912)

```
async def _used_micro_usd(self, connection: AsyncConnection, cap: SpendCap) -> int
```

**Purpose**: This calculates how much money has already been spent inside one cap’s rolling window. It answers the practical question: “how close are we to this limit right now?”

**Data flow**: It receives a cap, computes the cutoff time from the cap’s window length, and sums ledger `priced_micro_usd` rows since that cutoff. For workspace caps it sums the workspace directly; for member and agent caps it joins through turns and conversations to attribute spend.

**Call relations**: `SpendEvaluator.decide` calls this for each applicable cap. Its result is combined with pending unbilled cost before comparing against the cap limit.

*Call graph*: called by 1 (decide); 4 external calls (now, timedelta, execute, select).


##### `SpendEvaluator._message`  (lines 914–925)

```
def _message(self, outcome: SpendOutcome, breaches: list[SpendCap]) -> str
```

**Purpose**: This turns a cap breach into a message a user can understand. It names whether the turn was parked or declined and shows the tightest breached cap in dollars.

**Data flow**: It receives the chosen outcome and the list of breached caps. It picks the cap with the smallest limit, converts micro-USD to dollars, and returns a sentence explaining the block.

**Call relations**: `SpendEvaluator.decide` calls this only when at least one cap is breached. The returned text becomes the `SpendDecision.message` shown to callers.

*Call graph*: called by 1 (decide).


##### `_token_sum`  (lines 1042–1050)

```
def _token_sum() -> sa.ColumnElement[int]
```

**Purpose**: This builds a database expression that sums only token-like ledger dimensions. It keeps token totals from accidentally including images, videos, or egress counts.

**Data flow**: It creates a SQL expression: for each ledger row, count `amount` only if the dimension is `tokens` or `sandbox_tokens`, otherwise count zero, then sum and default null to zero.

**Call relations**: `_usage_details`, `SpendRollup.read`, and `SpendRollup._by_origin` use this helper when building report queries. It is a query-building helper rather than a function that runs a query itself.

*Call graph*: called by 3 (_by_origin, read, _usage_details); 1 external calls (case).


##### `_token_cost_sum`  (lines 1053–1065)

```
def _token_cost_sum() -> sa.ColumnElement[int]
```

**Purpose**: This builds a database expression that sums the cost of only token-like ledger dimensions. It separates model-token cost from other paid items such as media.

**Data flow**: It creates a SQL expression that includes `priced_micro_usd` only for `tokens` and `sandbox_tokens` rows, treats all other dimensions as zero, and coalesces an empty sum to zero.

**Call relations**: `_usage_details`, `SpendRollup.read`, and `SpendRollup._by_origin` use this when they need token-cost figures beside total spend figures.

*Call graph*: called by 3 (_by_origin, read, _usage_details); 1 external calls (case).


##### `_usage_details`  (lines 1068–1192)

```
async def _usage_details(connection: AsyncConnection, source: sa.FromClause, scope: sa.ColumnElement[bool], cutoff: datetime | None, now: datetime) -> UsageDetails
```

**Purpose**: This builds the reusable usage section of spend reports: selected-period totals, all-time totals, daily history, execution breakdowns, model breakdowns, and comparison with the previous period.

**Data flow**: It receives a database connection, a source table/join, a scope condition, an optional cutoff time, and the current time. It runs several aggregate queries, fills missing days with zeroes, computes previous-period token totals when a window is selected, normalizes the first-used timestamp, and returns a `UsageDetails` object.

**Call relations**: `SpendRollup.read`, `SpendRollup.read_agent`, and `SpendRollup.read_member` all call this so workspace, agent, and member reports share the same usage-summary rules.

*Call graph*: calls 2 internal fn (_token_cost_sum, _token_sum); called by 3 (read, read_agent, read_member); 9 external calls (__init__, __init__, __init__, __init__, fromisoformat, date, timedelta, execute, select).


##### `SpendRollup.read`  (lines 1203–1307)

```
async def read(self, connection: AsyncConnection, window_seconds: int | None) -> SpendReport
```

**Purpose**: This reads a full workspace spend report for a selected time window or for all time. It turns raw ledger rows into totals by dimension, member, agent, origin, price table, and usage detail.

**Data flow**: It receives a connection and optional window length. It computes the cutoff, sums total cost, groups ledger rows in several useful ways, asks `_by_origin` for root-conversation attribution, asks `_usage_details` for time-series and model detail, and returns a `SpendReport`.

**Call relations**: Dashboard or API code calls this to show workspace billing. It uses `_token_sum`, `_token_cost_sum`, `_by_origin`, and `_usage_details` to keep report calculations consistent.

*Call graph*: calls 4 internal fn (_by_origin, _token_cost_sum, _token_sum, _usage_details); 8 external calls (__init__, __init__, __init__, __init__, now, timedelta, execute, select).


##### `SpendRollup._by_origin`  (lines 1309–1374)

```
async def _by_origin(self, connection: AsyncConnection, window: sa.ColumnElement[bool]) -> tuple[OriginTotal, ...]
```

**Purpose**: This attributes token spend to the place where the user-facing conversation began. That matters because subagent work may happen in private child turns, but people want to see the original surface, such as a Slack channel, that caused the spend.

**Data flow**: It receives a connection and the already-built window condition. It uses a recursive database query, meaning a query that repeatedly follows parent links, to climb from spending turns to their root turns. It then groups token amounts and token costs by the root conversation’s surface label, using “Workspace jobs” when there is no conversation.

**Call relations**: `SpendRollup.read` calls this while building the workspace report. It uses `_token_sum` and `_token_cost_sum` so origin totals match the rest of the report’s token rules.

*Call graph*: calls 2 internal fn (_token_cost_sum, _token_sum); called by 1 (read); 4 external calls (__init__, desc, execute, select).


##### `SpendRollup.read_agent`  (lines 1376–1432)

```
async def read_agent(self, connection: AsyncConnection, agent_id: UUID, window_seconds: int | None) -> AgentSpendReport
```

**Purpose**: This reads spend and usage for one agent, including its configured agent-level caps. It leaves turn-less workspace jobs out because they do not belong to an agent turn.

**Data flow**: It receives a connection, agent id, and optional window length. It joins ledger rows to turns for that agent, groups spend by dimension, reads matching cap lines, asks `_usage_details` for usage summaries, and returns an `AgentSpendReport`.

**Call relations**: Agent detail pages or APIs call this when showing billing for one agent. It shares the common usage-summary helper with workspace and member reports.

*Call graph*: calls 1 internal fn (_usage_details); 7 external calls (__init__, __init__, __init__, now, timedelta, execute, select).


##### `SpendRollup.read_member`  (lines 1434–1492)

```
async def read_member(self, connection: AsyncConnection, member_id: UUID, window_seconds: int | None) -> MemberSpendReport
```

**Purpose**: This reads spend and usage for one member, plus that member’s configured spend caps. It attributes ledger rows to a member through the conversation that owned each turn.

**Data flow**: It receives a connection, member id, and optional window length. It joins ledger rows through turns to conversations, filters to the member, groups spend by dimension, reads member cap lines, calls `_usage_details`, and returns a `MemberSpendReport`.

**Call relations**: Member detail pages or APIs call this for per-person billing views. Its attribution path mirrors how member spend caps are enforced in `SpendEvaluator._used_micro_usd`.

*Call graph*: calls 1 internal fn (_usage_details); 7 external calls (__init__, __init__, __init__, now, timedelta, execute, select).


##### `BalanceGate.admits`  (lines 1524–1562)

```
async def admits(self, connection: AsyncConnection, agent_id: UUID | None=None, key_slot_for: Callable[[str], str | None] | None=None, turn_id: UUID | None=None, model: str | None=None) -> SpendDecisi
```

**Purpose**: This decides whether a workspace balance allows a turn to start, be folded into a live turn, or resume after being parked. It uses a stricter start threshold than the continue threshold so low balances do not cause start-stop loops.

**Data flow**: It receives a connection and optional context about the agent, model, key-slot resolver, and turn id. It reads balance headroom; if no balance row exists, it allows. Otherwise it compares balance with reserve and grace, checks whether the turn has already debited, and may allow low-balance work if the workspace’s own provider key will serve it. It returns an allow or reject `SpendDecision` with a billing message when rejected.

**Call relations**: Turn admission and resume paths call this before starting work. It calls `_workspace_serves_itself`, `_turn_has_debited`, balance `read_headroom`, and balance refusal-message helpers.

*Call graph*: calls 2 internal fn (_turn_has_debited, _workspace_serves_itself); 4 external calls (__init__, _forget_absent_balance, balance_refusal_message, read_headroom).


##### `BalanceGate._workspace_serves_itself`  (lines 1564–1586)

```
async def _workspace_serves_itself(self, connection: AsyncConnection, agent_id: UUID | None, key_slot_for: Callable[[str], str | None] | None, model: str | None=None) -> bool
```

**Purpose**: This checks whether the model for a turn will be served by a provider key owned by the workspace. That can allow some low-balance starts because the platform is not paying for those model tokens.

**Data flow**: It receives optional agent id, key-slot resolver, and model name. If the model is not supplied, it reads the agent’s model from the database. It maps the model to a key slot and asks `workspace_owns_the_key` whether the workspace has that credential.

**Call relations**: `BalanceGate.admits` calls this when balance is below the normal reserve but still above zero. It hands off the final credential check to `workspace_owns_the_key`.

*Call graph*: calls 1 internal fn (workspace_owns_the_key); called by 1 (admits); 2 external calls (execute, select).


##### `BalanceGate.sustains`  (lines 1588–1609)

```
async def sustains(self, connection: AsyncConnection, pending_micro_usd: int, turn_id: UUID | None=None) -> SpendDecision
```

**Purpose**: This decides whether a running turn may continue into another round. Unlike admission, it stops at zero balance, not at the reserve, so the reserve remains a starting cushion.

**Data flow**: It receives a connection, pending unbilled cost, and optional turn id. It reads balance headroom; if there is no balance row, it allows. Otherwise it subtracts pending cost, accounts for grace, and rejects only if the next charge or prior debits would push the workspace past the allowed floor.

**Call relations**: Mid-turn execution calls this before another round of work. It uses `_turn_has_debited` to avoid letting a turn that has already charged keep running below zero, and uses balance helpers for headroom and refusal text.

*Call graph*: calls 1 internal fn (_turn_has_debited); 4 external calls (__init__, _forget_absent_balance, balance_refusal_message, read_headroom).


##### `BalanceGate._turn_has_debited`  (lines 1611–1634)

```
async def _turn_has_debited(self, connection: AsyncConnection, turn_id: UUID | None) -> bool
```

**Purpose**: This answers whether a turn has already taken any money from the workspace balance. It looks at actual debits, not priced cost, because bring-your-own-key token rows can have a price but debit nothing.

**Data flow**: It receives a connection and optional turn id. If there is no turn id, it returns false. Otherwise it searches the ledger for any row on that turn whose debited amount is greater than zero, and returns true if one exists.

**Call relations**: `BalanceGate.admits` and `BalanceGate.sustains` call this when deciding whether a low-balance or overdrawn turn should be blocked. Its answer helps distinguish free-to-balance work from work that is actually spending platform-funded balance.

*Call graph*: called by 2 (admits, sustains); 2 external calls (execute, select).


### `core/src/ufo/harness/models/pricing.py`

`domain_logic` · `cross-cutting billing and usage recording`

This file is the project’s small pricing calculator for language model usage. Model providers charge different rates for different kinds of tokens: input tokens, output tokens, and cached tokens. This file keeps those rates in one structured form and uses them to compute a cost in micro-dollars, meaning millionths of a US dollar. That lets the rest of the system store precise costs without floating-point rounding surprises.

The central idea is a price table: each model name points to a ModelPrice, which says how much one million tokens costs for each token category. When the system records usage, it asks this file to multiply each token count by the matching rate, add the pieces together, and divide by one million tokens to get the final micro-dollar cost.

The file also makes a digest, which is a cryptographic fingerprint of the whole price table. A digest works like a tamper-evident seal: if any model rate changes, the fingerprint changes too. Billing records can then say not only “this cost was calculated,” but “this cost was calculated using this exact version of the prices.”

One important behavior is that unknown models do not crash billing. Instead, the file logs a warning and returns zero cost. That is safer for reading old historical records whose model names may no longer be in the current price table.

#### Function details

##### `price_digest`  (lines 27–44)

```
def price_digest(prices: Mapping[str, ModelPrice]) -> str
```

**Purpose**: Creates a stable fingerprint for a whole model price table. Someone uses this when they need a short, reliable version stamp showing exactly which prices were used for billing.

**Data flow**: It takes a mapping from model names to their price entries. It sorts the models, converts each price into a compact JSON text, then runs that text through SHA-256, a standard fingerprinting algorithm. It returns a string starting with "sha256:" followed by the fingerprint.

**Call relations**: When a new Pricing object is built, pricing_from calls this function first so the finished price table carries its own version stamp. Inside, it relies on JSON formatting and SHA-256 hashing to make the same input prices always produce the same digest.

*Call graph*: called by 1 (pricing_from); 2 external calls (sha256, dumps).


##### `usage_priced_micro_usd`  (lines 47–61)

```
def usage_priced_micro_usd(model: str, usage: Usage, prices: Mapping[str, ModelPrice]) -> int
```

**Purpose**: Calculates the cost of one model usage record in micro-US dollars. It is the main arithmetic step that turns counted tokens into a billable amount.

**Data flow**: It receives a model name, a Usage record containing token counts, and a price table. It looks up the model’s rates, multiplies each token category by its matching rate, adds those partial costs, then divides by one million because the rates are stored per million tokens. It returns an integer number of micro-dollars; if the model is missing, it logs that fact and returns zero.

**Call relations**: Pricing.micro_usd calls this function whenever the rest of the billing system asks for a cost. If the model is unknown, this function hands a warning to the observability logger instead of stopping the billing flow.

*Call graph*: called by 1 (micro_usd); 1 external calls (log).


##### `Pricing.micro_usd`  (lines 71–72)

```
def micro_usd(self, model: str, usage: Usage) -> int
```

**Purpose**: Provides the simple public method for asking a Pricing table, “What did this usage cost?” It hides the details of looking up rates and doing token math.

**Data flow**: It receives a model name and a Usage record. It uses the Pricing object’s stored price table, passes that along with the inputs to usage_priced_micro_usd, and returns the resulting micro-dollar amount without changing the Pricing object.

**Call relations**: Billing code calls this method when recording sandbox, turn, or workspace usage. This method is the doorway from the broader accounting system into the detailed pricing calculation.

*Call graph*: calls 1 internal fn (usage_priced_micro_usd); called by 3 (record_sandbox_tokens, record_turn_usage, record_workspace_usage).


##### `pricing_from`  (lines 75–78)

```
def pricing_from(prices: Mapping[str, ModelPrice]) -> Pricing
```

**Purpose**: Builds a complete Pricing object from a raw model price table. It makes sure the table and its digest are created together, so they cannot accidentally get out of sync.

**Data flow**: It receives a mapping of model names to ModelPrice entries. It copies that mapping into a plain dictionary, computes the digest for that copied table, and returns a frozen Pricing object containing both the copied prices and the digest.

**Call relations**: Setup code can call this when it has loaded or defined model prices and needs a ready-to-use Pricing object. It calls price_digest to create the version stamp, then constructs Pricing with the table and that stamp.

*Call graph*: calls 1 internal fn (price_digest); 1 external calls (__init__).


### `core/src/ufo/runtime/billing/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells the language that the surrounding folder should be treated as an importable package. Here, that folder is `ufo.runtime.billing`, which likely contains code related to billing or usage accounting elsewhere in the project.

Because the file is empty, it does not run setup code, expose shortcut imports, or define any functions or classes. Its value is structural: it helps Python and project tools recognize the billing folder as a meaningful part of the code layout. A simple analogy is a label on a filing cabinet drawer. The label does not contain the documents, but it tells people and tools where that category begins.

Without this file, depending on the Python version and packaging setup, imports that expect `ufo.runtime.billing` to be a regular package might fail or behave differently. So even though there is no executable logic here, it helps keep the package organization clear and reliable.


### `core/src/ufo/runtime/billing/balance.py`

`domain_logic` · `cross-cutting: billing setup, refill jobs, admin reads, and per-round admission checks`

This file exists because the system needs a fast, trustworthy answer to a simple question: “Can this workspace afford to keep working?” Instead of recalculating the balance from every purchase every time, it stores the current balance in one database row. That is like keeping a wallet total handy, while still saving every receipt so the total can be audited later.

The file covers the full life of prepaid billing. It can read the current balance, read just the smaller “headroom” needed before a model round starts, add credit from purchases or grants, subtract credit when work costs money, and show recent purchases for an admin screen. It also supports automatic refills: a workspace can set an amount and a threshold, and refill jobs can find workspaces that have crossed that threshold.

A small in-memory cache remembers, briefly, when a workspace has no balance row. This avoids a database read before every round in deployments that do not use billing. The cache is only a speed shortcut: adding credit clears it, and a stale entry merely causes one skipped shortcut later.

One important behavior is that debits are allowed to make the balance negative. The system prefers an honest record of already-spent money over rejecting the ledger entry and losing track of the cost.

#### Function details

##### `balance_absent`  (lines 40–46)

```
def balance_absent(workspace_id: UUID) -> bool
```

**Purpose**: This is a quick, database-free check for whether a recent lookup found that a workspace has no balance row. It helps avoid repeated billing reads for workspaces that are not using prepaid billing.

**Data flow**: It receives a workspace ID, looks in a small in-memory map for an expiry time, compares that expiry with the current monotonic clock, and returns true only if the “no balance” note is still fresh. It does not change the database or the balance.

**Call relations**: This function is a fast path for callers that want to skip a balance lookup when the system recently learned there was nothing to read. The only outside call it makes is to the clock, so it can decide whether the cached note has expired.

*Call graph*: 1 external calls (monotonic).


##### `_note_absent_balance`  (lines 49–56)

```
def _note_absent_balance(workspace_id: UUID) -> None
```

**Purpose**: This records, for a few seconds, that a workspace was checked and no balance row existed. It keeps billing-free workspaces from paying the cost of the same database lookup over and over.

**Data flow**: It receives a workspace ID, gets the current monotonic time, removes expired cache entries if the cache is full, and stores a new expiry time for that workspace. The result is an updated in-memory cache; nothing is returned.

**Call relations**: read_balance and read_headroom call this when their database query finds no balance row. After that, callers can use balance_absent to take the connectionless shortcut until the short time-to-live runs out.

*Call graph*: called by 2 (read_balance, read_headroom); 1 external calls (monotonic).


##### `_forget_absent_balance`  (lines 59–62)

```
def _forget_absent_balance(workspace_id: UUID) -> None
```

**Purpose**: This clears the short-lived “no balance exists” note for a workspace. It matters because once credit is added, the system must not keep pretending there is no balance row.

**Data flow**: It receives a workspace ID and removes that ID from the in-memory absence cache if it is present. It returns nothing and does not touch the database.

**Call relations**: credit calls this after it successfully creates or updates a workspace balance. That makes the fast path safe: a new credit immediately cancels any earlier cached absence.

*Call graph*: called by 1 (credit).


##### `billing_screen_url`  (lines 79–88)

```
def billing_screen_url(public_base_url: str | None, home_surface: str | None) -> str | None
```

**Purpose**: This builds the web address for the workspace billing screen, if this deployment has one. It gives refusal messages and other callers a ready-made place to send admins.

**Data flow**: It receives a public base URL and the name of the browser surface. If either is missing, it returns None. Otherwise it trims any trailing slash from the base URL and joins it with the surface path and billing screen fragment.

**Call relations**: This helper is meant to be prepared during setup and passed down to code that may need to point someone at billing. It does not call other project code; it simply composes a string from its inputs.


##### `balance_refusal_message`  (lines 91–100)

```
def balance_refusal_message(billing_url: str | None) -> str
```

**Purpose**: This creates the user-facing sentence shown when a workspace is out of credit. It explains the problem and, when possible, tells an admin exactly where to fix it.

**Data flow**: It receives either a billing URL or None. With a URL, it returns a message containing that URL; without one, it returns a shorter message that still says an admin can set up automatic refills.

**Call relations**: This function is used by code that refuses work because billing credit is exhausted. It depends on billing_screen_url’s result in the larger flow, but it does not call it directly.


##### `read_auto_topup`  (lines 112–134)

```
async def read_auto_topup(connection: AsyncConnection, workspace_id: UUID) -> AutoTopup | None
```

**Purpose**: This answers the refill job’s question: should this workspace be topped up now? It returns refill settings only when automatic top-up is configured and the balance has reached or dropped below its threshold.

**Data flow**: It receives a database connection and workspace ID, reads that workspace’s balance, top-up amount, and threshold from the database, and returns None if there is no row, no top-up setting, or the balance is still above the threshold. If the workspace is due for refill, it returns an AutoTopup object with the amount and threshold.

**Call relations**: Refill code can call this after selecting a workspace candidate to confirm whether payment should happen. Inside, it asks the database for the relevant row and builds the small AutoTopup data object when the rule applies.

*Call graph*: 3 external calls (__init__, execute, select).


##### `topping_up_workspaces`  (lines 137–151)

```
def topping_up_workspaces() -> WorkspaceCandidates
```

**Purpose**: This builds the candidate list for the periodic refill job. It narrows the job to workspaces that have automatic top-up configured and are already at or below their chosen line.

**Data flow**: It creates a database-selection rule through its nested helper and passes that rule to owner_candidates, which wraps it in the project’s workspace-candidate mechanism. The output is a WorkspaceCandidates object that the refill job can iterate or schedule from.

**Call relations**: This is the broad fleet-level entry into refill selection. It delegates the exact SQL condition to topping_up_workspaces.short_of_its_line, then hands that selector to owner_candidates so the rest of the system can work in terms of eligible workspace owners.

*Call graph*: 1 external calls (owner_candidates).


##### `topping_up_workspaces.short_of_its_line`  (lines 144–149)

```
def short_of_its_line() -> sa.Select[tuple[UUID]]
```

**Purpose**: This nested helper describes the database query for workspaces that are ready for automatic refill. It keeps the refill condition in one place: top-up must be configured, and the balance must be at or below the threshold.

**Data flow**: It takes no direct input, builds a SQL select for workspace IDs from the balance table, filters to rows with a top-up amount and a low enough balance, and returns that select statement for another component to run.

**Call relations**: topping_up_workspaces passes this helper to owner_candidates. The helper supplies the “which workspaces are short?” part, while owner_candidates supplies the broader candidate machinery around it.

*Call graph*: 1 external calls (select).


##### `set_auto_topup`  (lines 154–174)

```
async def set_auto_topup(connection: AsyncConnection, workspace_id: UUID, amount_micro_usd: int | None, threshold_micro_usd: int | None) -> bool
```

**Purpose**: This turns automatic refilling on or off for a workspace that already has a balance row. It prevents half-configured refills by requiring both the refill amount and threshold together, or neither.

**Data flow**: It receives a database connection, workspace ID, optional amount, and optional threshold. If only one of amount or threshold is provided, it raises a ValueError. Otherwise it updates the workspace balance row with the new settings and timestamp, then returns true if exactly one row was changed.

**Call relations**: Admin or billing settings code calls this when someone changes auto-top-up settings. It writes directly to the balance table through the database connection and does not create a balance row for workspaces that have never been credited.

*Call graph*: 2 external calls (execute, update).


##### `mark_topup_verified`  (lines 177–191)

```
async def mark_topup_verified(connection: AsyncConnection, workspace_id: UUID) -> None
```

**Purpose**: This records that a workspace has successfully paid for at least one top-up. That matters because a verified payer is allowed a fixed grace amount below zero while refilling catches up.

**Data flow**: It receives a database connection and workspace ID, then updates the balance row only if the verification timestamp is still empty. The database row gains a first-time top-up verification time and a fresh update time; nothing is returned.

**Call relations**: Payment or refill fulfillment code calls this after a card charge has settled. Later, read_headroom uses the presence of this timestamp to decide whether the workspace gets the top-up grace allowance.

*Call graph*: 2 external calls (execute, update).


##### `configured_auto_topup`  (lines 203–224)

```
async def configured_auto_topup(connection: AsyncConnection, workspace_id: UUID) -> AutoTopup | None
```

**Purpose**: This reads the automatic refill settings exactly as configured, even if the workspace has not yet reached the refill threshold. It is for showing or reporting the setting, not for deciding whether to charge right now.

**Data flow**: It receives a database connection and workspace ID, reads the top-up amount and threshold, and returns None if there is no balance row or no configured amount. Otherwise it returns an AutoTopup object containing the saved settings.

**Call relations**: Admin-facing code can call this to display the current refill rule. Unlike read_auto_topup, it does not check whether the balance is low enough to trigger a refill; it simply reports the configured values.

*Call graph*: 3 external calls (__init__, execute, select).


##### `read_headroom`  (lines 227–246)

```
async def read_headroom(connection: AsyncConnection, workspace_id: UUID) -> Headroom | None
```

**Purpose**: This reads the small set of numbers needed before starting paid work: current balance, required reserve, and any earned grace. It is designed for frequent checks before model rounds, so it avoids heavier lifetime totals.

**Data flow**: It receives a database connection and workspace ID, reads the balance row’s balance, reserve, and top-up verification timestamp, and returns None if no row exists. If there is no row, it also notes that absence in the short-lived cache. If a row exists, it returns a Headroom object, giving a fixed grace amount only when top-up has been verified.

**Call relations**: Admission or gate code uses this before allowing model work to begin. When no database row exists, it calls _note_absent_balance so future checks can use the fast absence shortcut.

*Call graph*: calls 1 internal fn (_note_absent_balance); 3 external calls (__init__, execute, select).


##### `recent_purchases`  (lines 260–293)

```
async def recent_purchases(connection: AsyncConnection, workspace_id: UUID, limit: int) -> tuple[Purchase, ...]
```

**Purpose**: This returns the newest balance credits for a workspace, for example to show an admin where the balance came from. It limits the result so a long-running workspace with many refills does not load an endless history.

**Data flow**: It receives a database connection, workspace ID, and maximum number of rows. It queries purchase rows for that workspace, newest first, using the purchase ID to make ties stable when timestamps match. It returns a tuple of Purchase objects with granted amount, charged amount, and creation time.

**Call relations**: Billing screens and reporting code use this when they need a visible purchase history. It reads the same purchase table that read_balance later totals, but it returns individual recent entries instead of lifetime sums.

*Call graph*: 3 external calls (__init__, execute, select).


##### `read_balance`  (lines 296–326)

```
async def read_balance(connection: AsyncConnection, workspace_id: UUID) -> Balance | None
```

**Purpose**: This gives the full balance picture for a workspace: what is left, what reserve is required, how much was ever granted, how much was ever charged, and when the last purchase happened. It is heavier than read_headroom because it calculates lifetime totals.

**Data flow**: It receives a database connection and workspace ID, first reads the current balance row, and returns None if no row exists while also caching that absence briefly. If a row exists, it sums all purchase grants and charges for that workspace, finds the latest purchase time, and returns a Balance object combining the current row with those totals.

**Call relations**: Admin, operator, or billing overview code calls this when it needs the whole audited picture. It calls _note_absent_balance on missing rows, while gates that only need a quick admission decision should use read_headroom instead.

*Call graph*: calls 1 internal fn (_note_absent_balance); 3 external calls (__init__, execute, select).


##### `credit`  (lines 329–387)

```
async def credit(connection: AsyncConnection, workspace_id: UUID, granted_micro_usd: int, charged_micro_usd: int, reference: str) -> bool
```

**Purpose**: This adds credit to a workspace balance exactly once for a given reference, such as a payment ID. It protects against duplicate payment deliveries by refusing to add the same referenced purchase twice.

**Data flow**: It receives a database connection, workspace ID, granted amount, charged amount, and reference string. It inserts a purchase row with a new UUID, but does nothing if that workspace/reference pair already exists. When the insert succeeds, it creates or updates the workspace balance by adding the granted amount, clears any cached “no balance” note, and returns true. If the reference was already used, it returns false.

**Call relations**: Payment fulfillment, grants, refunds, or operator credit tools call this inside their own database transaction. After a successful transaction commits, the caller can use count_charge if credit returned true and the charged amount should be counted as a metric.

*Call graph*: calls 1 internal fn (_forget_absent_balance); 2 external calls (execute, uuid4).


##### `count_charge`  (lines 390–407)

```
def count_charge(charged_micro_usd: int) -> None
```

**Purpose**: This records a positive charged amount as an observability metric, which is a counter used for monitoring. It is deliberately separate from credit so failed or rolled-back database transactions do not produce permanent metric counts.

**Data flow**: It receives a charged amount. If the amount is zero or negative, it returns without doing anything. If the amount is positive, it emits the balance-charged metric with that amount; it returns nothing.

**Call relations**: Callers use this after credit has returned true and their surrounding transaction has committed. It hands the count to emit_metric, avoiding double-counting retries or counting purchases that were later rolled back.

*Call graph*: 1 external calls (emit_metric).


##### `debit`  (lines 410–430)

```
async def debit(connection: AsyncConnection, workspace_id: UUID, micro_usd: int) -> int
```

**Purpose**: This subtracts spent money from a workspace balance when work has burned credit. It returns the amount actually taken so the caller can record the same number in its ledger.

**Data flow**: It receives a database connection, workspace ID, and amount to subtract. If the amount is zero, it immediately returns zero. Otherwise it updates the balance row by subtracting the amount and refreshing the timestamp. It returns the requested amount if a balance row existed, or zero if there was no row to debit.

**Call relations**: Usage-recording code calls this in the same transaction that records the cost. It uses the database update directly, and it allows the balance to go negative so the system does not lose the record of money already spent.

*Call graph*: 2 external calls (execute, update).


##### `set_reserve`  (lines 433–444)

```
async def set_reserve(connection: AsyncConnection, workspace_id: UUID, reserve_micro_usd: int) -> bool
```

**Purpose**: This sets the reserve amount a workspace must keep before new work may start. The reserve is a safety buffer so a nearly empty workspace does not begin work that is likely to stall immediately.

**Data flow**: It receives a database connection, workspace ID, and reserve amount, then updates that workspace’s balance row with the new reserve and timestamp. It returns true if exactly one row was updated, or false if the workspace had no balance row.

**Call relations**: Billing settings or operator code calls this when changing the admission buffer for a workspace. Later, read_headroom includes this reserve value when gate code decides whether work may begin.

*Call graph*: 2 external calls (execute, update).


### External Billing Exports
Connects internal usage and balance state to Metronome reporting, Stripe prepaid billing, and admin-facing billing tools.

### `extensions/metronome/ufo_ext_metronome.py`

`orchestration` · `scheduled jobs, chat tool calls, and billing page requests`

This extension is the bridge between UFO's internal accounting and two outside services: Metronome, which records and prices usage, and Stripe, which stores cards and takes top-up payments. Without it, settled usage would stay inside UFO and never appear in Metronome, and admins would have no built-in way to add a payment method or arrange automatic refills.

The file has three main jobs. First, a scheduled usage shipper reads frozen usage export records from core, turns each one into a Metronome ingest event, sends a batch, and only then marks those records as sent. It uses stable transaction IDs, like writing the same receipt number on a retry, so a crash can safely resend without double-counting.

Second, it defines billing actions for chat. An admin can ask for status, get a Stripe Customer Portal link, or set autopay. These actions read UFO's own prepaid balance but ask Stripe whether a card really exists right now.

Third, another scheduled job watches workspaces with autopay enabled. When a balance is low, it charges the saved card off-session and credits the workspace once the payment succeeds. It deliberately slows down after missing cards or declined cards, to avoid hammering Stripe or card networks.

#### Function details

##### `StripeError.__init__`  (lines 187–189)

```
def __init__(self, message: str, status: int=0) -> None
```

**Purpose**: Creates an error object for a failed Stripe call, while keeping the HTTP status code available. This matters because later code treats different Stripe failures differently, such as a declined payment versus a still-running request.

**Data flow**: It receives an error message and an optional status code. It stores the message in the normal exception machinery and saves the status on the object. The result is an exception that can be raised and later inspected.

**Call relations**: The Stripe HTTP helper creates this error when Stripe returns a non-success response. Payment top-up code then uses the saved status to decide whether to retry, pause, or report a declined card.

*Call graph*: called by 1 (_stripe).


##### `UsageShipper.run`  (lines 219–245)

```
async def run(self) -> None
```

**Purpose**: Sends one workspace's pending settled usage to Metronome safely. It is designed so that if the process crashes halfway through, the next run can resend the same events without double-billing.

**Data flow**: It reads the Metronome token from the environment, finds the workspace's fixed backfill floor, then repeatedly reads a batch of pending usage exports. For each batch it warns if the usage is getting too old, confirms the Metronome customer alias exists, sends the events, logs success, and only then acknowledges the exports as shipped. It changes the export state only after Metronome accepts the batch.

**Call relations**: The scheduled job wrapper calls this for each metered workspace. During its run it relies on helper methods to build events and track the shipping floor, and it hands the actual network work to the Metronome customer and ingest helpers.

*Call graph*: calls 6 internal fn (_events, _floor, _note_usage_aging_out, _ensure_metronome_customer, _ingest, _require_env); 1 external calls (log).


##### `UsageShipper._floor`  (lines 247–257)

```
async def _floor(self) -> datetime
```

**Purpose**: Finds or creates the oldest point in time from which this workspace's usage should be shipped. This prevents a first run from sending unlimited historical usage while still allowing old pending records to ship later.

**Data flow**: It reads a stored timestamp from the extension store. If none exists, it creates one set to seven days before the current time, stores it, and returns it. If one exists, it parses and returns that saved timestamp.

**Call relations**: The usage shipper asks this before reading pending exports. The returned time is passed into core's usage export seam so only usage after that fixed floor is considered.

*Call graph*: called by 1 (run); 3 external calls (fromisoformat, now, timedelta).


##### `UsageShipper._note_usage_aging_out`  (lines 259–280)

```
def _note_usage_aging_out(self, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: Warns operators when pending usage is older than Metronome's backdating window. It cannot fix old usage, but it makes a quiet billing loss visible before or when it happens.

**Data flow**: It receives a batch of usage exports, finds the oldest event time, compares it with the allowed backfill window, and logs a warning if the batch is too old. It returns nothing and does not change the exports.

**Call relations**: The usage shipper calls this before sending each batch. It uses the timestamp formatting helper for readable warning output.

*Call graph*: calls 1 internal fn (_rfc3339); called by 1 (run); 3 external calls (now, timedelta, warn).


##### `UsageShipper._events`  (lines 282–301)

```
def _events(self, exports: tuple[UsageExport, ...]) -> list[dict[str, object]]
```

**Purpose**: Turns UFO usage export records into the JSON-like event objects Metronome expects. Each event includes a stable transaction ID so repeats are treated as the same event by Metronome.

**Data flow**: It takes a tuple of usage exports and reads the workspace ID from the context. It builds a list of event dictionaries with customer ID, timestamp, model, amount, price information, turn ID, and whether the workspace used its own provider key. The output is ready to send to Metronome ingest.

**Call relations**: The usage shipper calls this right before ingestion. It uses the timestamp helper so event times are formatted consistently.

*Call graph*: calls 1 internal fn (_rfc3339); called by 1 (run).


##### `_ship`  (lines 304–305)

```
async def _ship(ctx: ExtensionContext) -> None
```

**Purpose**: Acts as the scheduled job entry for usage shipping. It creates a UsageShipper for the workspace and starts it.

**Data flow**: It receives an extension context for one workspace. It passes that context and the configured test or production transport into UsageShipper, then waits for the shipper to finish. It returns nothing.

**Call relations**: The extension manifest registers this as the usage shipping job handler. The real work is delegated to UsageShipper.run.

*Call graph*: 1 external calls (__init__).


##### `BillingConfig.from_env`  (lines 323–340)

```
def from_env(cls) -> 'BillingConfig'
```

**Purpose**: Loads the required billing settings from environment variables and checks that none are missing. It fails early so a half-configured deployment does not create partial Stripe or Metronome state.

**Data flow**: It reads the Stripe secret key, Stripe portal configuration ID, and Metronome bearer token from the process environment. If any are absent, it raises an error naming all missing settings. Otherwise it returns a frozen BillingConfig object.

**Call relations**: Billing tools, billing page reads, and top-up jobs call this before talking to Stripe or billing-related Metronome APIs. Usage shipping reads only the Metronome token separately because it must keep working even if Stripe billing is not configured.


##### `_billing_record`  (lines 353–355)

```
async def _billing_record(ctx: ExtensionContext) -> BillingRecord | None
```

**Purpose**: Reads the stored Stripe billing record for a workspace, if one exists. This record is mainly the Stripe Customer ID that future billing calls should reuse.

**Data flow**: It asks the extension store for the billing key. If nothing is stored, it returns None. If data is present, it validates it as a BillingRecord and returns that object.

**Call relations**: Billing status, portal creation, autopay setup, the top-up job, and the billing page all use this to find the workspace's Stripe Customer before asking Stripe about cards or payments.

*Call graph*: called by 5 (run, _billing_autopay, _billing_portal, _billing_projection, _billing_status).


##### `manage_billing`  (lines 378–387)

```
async def manage_billing(ctx: ToolContext, args: ManageBillingInput) -> ToolResult
```

**Purpose**: Implements the chat action admins use to manage billing. It routes the requested operation to status, portal, or autopay behavior.

**Data flow**: It receives the tool context and structured arguments from the agent. It first proves the speaker is an admin, loads billing configuration, then dispatches based on the requested operation. It returns a ToolResult containing JSON text for the agent to show or use.

**Call relations**: The tool definition exposes this function to the workspace object. It delegates permission checking to _admin_billing and the actual work to the three billing operation helpers.

*Call graph*: calls 4 internal fn (_admin_billing, _billing_autopay, _billing_portal, _billing_status).


##### `_billing_autopay`  (lines 390–421)

```
async def _billing_autopay(ext: ExtensionContext, config: BillingConfig, args: ManageBillingInput) -> ToolResult
```

**Purpose**: Sets or stops automatic balance refills for a workspace. It makes sure a card is already saved before promising future unattended charges.

**Data flow**: It receives the workspace context, billing config, and requested autopay numbers. If both dollar values are omitted, it disables autopay. If values are provided, it checks for an existing Stripe customer and default payment method, converts dollars to micro-dollars, stores the rule in core balance state, clears refusal markers, bumps an attempt counter, logs the change, and returns the saved settings.

**Call relations**: manage_billing calls this when the admin chooses the autopay operation. It reads Stripe through _default_payment_method, writes the rule through core balance functions, and formats the tool reply through _text_result.

*Call graph*: calls 4 internal fn (transaction, _billing_record, _default_payment_method, _text_result); called by 1 (manage_billing); 2 external calls (set_auto_topup, log).


##### `_admin_billing`  (lines 424–430)

```
async def _admin_billing(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Checks that the chat billing action is being run by a real workspace admin. This protects billing information and payment setup from non-admin users or anonymous tool calls.

**Data flow**: It reads the speaker member ID and admin status from the tool context. If there is no speaker, it asks for one; if the speaker is not an admin, it raises an error. If allowed, it returns the extension context for the workspace.

**Call relations**: manage_billing calls this before any billing operation. The returned extension context is then used by status, portal, or autopay code.

*Call graph*: calls 1 internal fn (speaker_is_admin); called by 1 (manage_billing); 1 external calls (__init__).


##### `_billing_status`  (lines 433–457)

```
async def _billing_status(ext: ExtensionContext, config: BillingConfig) -> ToolResult
```

**Purpose**: Reports the workspace's prepaid balance and whether Stripe currently has a card on file. It gives admins facts rather than guessing from local records.

**Data flow**: It reads the balance from UFO's database, reads the saved billing record from the extension store, and if possible asks Stripe for the customer's default payment method. It returns JSON text with card presence and balance fields, using nulls when no balance exists.

**Call relations**: manage_billing calls this for the status operation. It combines core balance data with Stripe's current answer and formats the result through _text_result.

*Call graph*: calls 4 internal fn (transaction, _billing_record, _default_payment_method, _text_result); called by 1 (manage_billing); 1 external calls (read_balance).


##### `_billing_portal`  (lines 460–482)

```
async def _billing_portal(ext: ExtensionContext, config: BillingConfig) -> ToolResult
```

**Purpose**: Creates a short-lived Stripe Customer Portal link for an admin. The portal lets them save or change a payment method and view billing details without UFO storing card data.

**Data flow**: It reads the workspace ID and existing billing record. If no Stripe Customer is recorded, it creates one and stores the returned ID. It then creates a portal session with a return URL back to UFO's billing screen, logs the action, and returns the URL and customer ID as JSON text.

**Call relations**: manage_billing calls this for the portal operation. It uses _stripe_customer when first provisioning the customer, _portal_session for the Stripe link, and _text_result for the chat result.

*Call graph*: calls 5 internal fn (home_url, _billing_record, _portal_session, _stripe_customer, _text_result); called by 1 (manage_billing); 2 external calls (__init__, log).


##### `_text_result`  (lines 485–486)

```
def _text_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: Wraps a small payload as text for a tool response. The agent receives a JSON string it can report back to the user.

**Data flow**: It takes a dictionary, serializes it to JSON text, puts that text in a TextContent object, and returns a ToolResult containing it. It does not write any state.

**Call relations**: The billing operation helpers use this as their final packaging step before returning to manage_billing and the chat tool system.

*Call graph*: called by 3 (_billing_autopay, _billing_portal, _billing_status); 3 external calls (__init__, __init__, dumps).


##### `_require_env`  (lines 500–504)

```
def _require_env(name: str) -> str
```

**Purpose**: Reads one required environment variable and fails clearly if it is missing. It is a small guardrail for settings that must exist before work can safely begin.

**Data flow**: It receives an environment variable name, looks it up, and returns the value if present. If the value is empty or missing, it raises a RuntimeError naming the missing setting.

**Call relations**: UsageShipper.run uses this before touching pending usage exports, so an unconfigured deployment does not create a growing backlog of newly minted export intents.

*Call graph*: called by 1 (run).


##### `_stripe_customer`  (lines 507–524)

```
async def _stripe_customer(config: BillingConfig, workspace_id: UUID, transport: httpx.AsyncBaseTransport | None) -> str
```

**Purpose**: Creates or reuses the one Stripe Customer for a workspace. It uses a stable idempotency key, meaning repeated create attempts settle on the same customer instead of making duplicates.

**Data flow**: It receives billing config, a workspace ID, and an optional HTTP transport. It sends a customer creation request to Stripe with workspace metadata and a deterministic idempotency key. It extracts and returns the customer ID from Stripe's response.

**Call relations**: _billing_portal calls this when a workspace has no stored billing record yet. It relies on _stripe for the HTTP request and _as_str to verify the returned ID is usable.

*Call graph*: calls 2 internal fn (_as_str, _stripe); called by 1 (_billing_portal).


##### `_portal_session`  (lines 527–552)

```
async def _portal_session(config: BillingConfig, customer_id: str, flow: str | None, transport: httpx.AsyncBaseTransport | None, return_url: str | None=None) -> str
```

**Purpose**: Creates a Stripe Customer Portal session URL. This is the link an admin opens to manage payment methods, invoices, and billing details.

**Data flow**: It receives billing config, a Stripe customer ID, an optional portal flow, an HTTP transport, and an optional return URL. It builds the form data Stripe expects, sends the request, and returns the session URL from the response.

**Call relations**: _billing_portal calls this after it has a Stripe Customer ID. It uses _stripe for the provider call and _as_str to make sure a real URL came back.

*Call graph*: calls 2 internal fn (_as_str, _stripe); called by 1 (_billing_portal).


##### `_default_payment_method`  (lines 555–567)

```
async def _default_payment_method(config: BillingConfig, customer_id: str, transport: httpx.AsyncBaseTransport | None) -> str | None
```

**Purpose**: Asks Stripe which payment method is the customer's default, if any. This is the source of truth for whether an unattended top-up can charge a card.

**Data flow**: It receives billing config, a customer ID, and an optional transport. It fetches the Stripe customer, looks inside invoice settings for a default payment method string, and returns that string or None.

**Call relations**: Autopay setup, billing status, top-up charging, and card display all call this before assuming a card exists. It delegates the HTTP request to _stripe.

*Call graph*: calls 1 internal fn (_stripe); called by 4 (run, _billing_autopay, _billing_status, _card_on_file).


##### `_card_on_file`  (lines 582–597)

```
async def _card_on_file(config: BillingConfig, customer_id: str, transport: httpx.AsyncBaseTransport | None) -> CardOnFile | None
```

**Purpose**: Gets the human-readable card description for the default card: brand and last four digits. This helps an admin recognize which card will be charged.

**Data flow**: It first asks for the default payment method. If none exists, it returns None. If there is one, it fetches that payment method from Stripe and returns a CardOnFile object when the method is a card with brand and last-four data.

**Call relations**: The billing page calls this when building its projection. It builds on _default_payment_method and _stripe, keeping card display separate from the charging path.

*Call graph*: calls 2 internal fn (_default_payment_method, _stripe); called by 1 (_billing_projection); 1 external calls (__init__).


##### `_stripe`  (lines 600–621)

```
async def _stripe(config: BillingConfig, method: str, path: str, transport: httpx.AsyncBaseTransport | None, data: dict[str, str] | None=None, idempotency_key: str | None=None) -> dict[str, object]
```

**Purpose**: Provides the shared low-level Stripe HTTP call behavior. It adds authentication, pins the Stripe API version, applies optional idempotency, and turns failed responses into StripeError.

**Data flow**: It receives config, HTTP method, Stripe path, optional form data, optional idempotency key, and optional transport. It sends the request to Stripe, raises StripeError if the response is not successful, and otherwise returns the parsed JSON body.

**Call relations**: All Stripe-facing helpers use this rather than building requests themselves. BalanceTopup._charge also depends on its StripeError status codes to understand payment conflicts and declines.

*Call graph*: calls 1 internal fn (__init__); called by 5 (_charge, _card_on_file, _default_payment_method, _portal_session, _stripe_customer); 1 external calls (AsyncClient).


##### `_as_str`  (lines 624–628)

```
def _as_str(value: object, field: str) -> str
```

**Purpose**: Checks that a provider response field is a non-empty string. It prevents later code from silently using a missing customer ID or portal URL.

**Data flow**: It receives a value and a field name for error messages. If the value is a non-empty string, it returns it. Otherwise it raises a ValueError naming the missing field.

**Call relations**: _stripe_customer and _portal_session call this after Stripe responds. It is a small validation step between provider JSON and the rest of the billing flow.

*Call graph*: called by 2 (_portal_session, _stripe_customer).


##### `_ensure_metronome_customer`  (lines 631–682)

```
async def _ensure_metronome_customer(ctx: ExtensionContext, token: str, transport: httpx.AsyncBaseTransport | None) -> None
```

**Purpose**: Makes sure Metronome has a live customer whose ingest alias is this workspace's UUID. Without that alias, Metronome may accept usage events but fail to attach them to a real customer, causing silent loss.

**Data flow**: It receives the workspace context, Metronome token, and optional transport. It looks up a customer by the workspace alias; if found, it returns. If not found, it tries to create one, handles alias conflicts by re-reading, and raises clear errors when the token cannot confirm or create the alias. On success it logs that the customer was created.

**Call relations**: UsageShipper.run calls this once per shipping pass before sending any usage. It uses _customer_by_alias for reads and stops the shipper from acknowledging exports when attribution is unsafe.

*Call graph*: calls 1 internal fn (_customer_by_alias); called by 1 (run); 4 external calls (__init__, __init__, AsyncClient, log).


##### `_customer_by_alias`  (lines 692–709)

```
async def _customer_by_alias(http: httpx.AsyncClient, headers: dict[str, str], alias: str) -> str | None
```

**Purpose**: Looks up the live Metronome customer that owns a given ingest alias. It returns None when no visible live customer is found.

**Data flow**: It receives an HTTP client, headers, and an alias string. It sends a Metronome customer search request, raises specific errors for permission problems or failed responses, and returns the first customer ID from the response data if present.

**Call relations**: _ensure_metronome_customer uses this before creating a customer and again after a conflict. That second read distinguishes a harmless race from an alias held by something this token cannot see.

*Call graph*: called by 1 (_ensure_metronome_customer); 3 external calls (__init__, __init__, get).


##### `BalanceTopup.run`  (lines 728–811)

```
async def run(self) -> None
```

**Purpose**: Runs the automatic refill process for one workspace. If the workspace is short, has autopay configured, and has a saved card, it charges Stripe and credits the prepaid balance.

**Data flow**: It reads the configured top-up rule from core balance state. It skips work when no rule exists, when a recent no-card check is still cooling down, or when a recent refusal should not be retried yet. It loads billing config, finds the Stripe customer and payment method, reads balance totals, builds an attempt key, tries to charge, records refusal markers on decline, and credits the workspace after a successful payment. It logs and counts successful top-ups.

**Call relations**: The scheduled top-up wrapper calls this for workspaces that core says may need refilling. It uses _billing_record and _default_payment_method to find the payment source, BalanceTopup._charge to move money through Stripe, and core balance functions to record the resulting credit.

*Call graph*: calls 3 internal fn (_charge, _billing_record, _default_payment_method); 9 external calls (fromisoformat, now, count_charge, credit, mark_topup_verified, read_auto_topup, read_balance, log, warn).


##### `BalanceTopup._charge`  (lines 813–870)

```
async def _charge(self, config: BillingConfig, customer_id: str, payment_method: str, wanted: AutoTopup, workspace_id: UUID, attempt: str) -> str | None
```

**Purpose**: Creates and confirms a Stripe PaymentIntent for an automatic top-up. It returns the payment intent ID only when Stripe says the money actually moved.

**Data flow**: It receives billing config, customer ID, payment method ID, the desired top-up amount, workspace ID, and an attempt marker. It converts micro-dollars to cents, sends an off-session Stripe payment request with an idempotency key, treats an in-flight duplicate as a special case, treats card declines as None, and returns the intent ID when status is succeeded.

**Call relations**: BalanceTopup.run calls this after deciding a refill should happen. It uses _stripe for the provider call and signals special retry behavior with _ChargeInFlight or a None result.

*Call graph*: calls 1 internal fn (_stripe); called by 1 (run); 2 external calls (__init__, warn).


##### `_top_up`  (lines 873–874)

```
async def _top_up(ctx: ExtensionContext) -> None
```

**Purpose**: Acts as the scheduled job entry for automatic balance refills. It creates a BalanceTopup worker for the workspace and runs it.

**Data flow**: It receives an extension context, passes it and the billing transport into BalanceTopup, and waits for the run to finish. It returns nothing.

**Call relations**: The extension manifest registers this as the top-up job handler. BalanceTopup.run contains the actual refill decision and payment logic.

*Call graph*: 1 external calls (__init__).


##### `_ingest`  (lines 877–885)

```
async def _ingest(token: str, events: list[dict[str, object]], transport: httpx.AsyncBaseTransport | None) -> None
```

**Purpose**: Sends a prepared batch of usage events to Metronome's ingest endpoint. It raises loudly if Metronome does not accept the batch.

**Data flow**: It receives a bearer token, a list of event dictionaries, and an optional transport. It POSTs the events with authorization to Metronome. If the response is unsuccessful, it raises MetronomeError; otherwise it returns nothing.

**Call relations**: UsageShipper.run calls this after confirming the customer alias and building events. Only after this helper succeeds does the shipper acknowledge exports in core.

*Call graph*: called by 1 (run); 2 external calls (__init__, AsyncClient).


##### `_rfc3339`  (lines 888–890)

```
def _rfc3339(moment: datetime) -> str
```

**Purpose**: Formats a datetime for provider-facing JSON. If the time has no timezone, it treats it as UTC so the event still has an explicit point in time.

**Data flow**: It receives a datetime. If it lacks timezone information, it attaches UTC; then it returns the ISO-format string. It does not change stored data.

**Call relations**: UsageShipper._events uses this for Metronome event timestamps, and UsageShipper._note_usage_aging_out uses it in warning logs.

*Call graph*: called by 2 (_events, _note_usage_aging_out); 1 external calls (replace).


##### `_billing_request_workspace`  (lines 899–904)

```
def _billing_request_workspace(request: Request) -> UUID | None
```

**Purpose**: Identifies which workspace a billing page request belongs to by reading the session cookie. If it cannot find a workspace claim, the route should not be served.

**Data flow**: It receives an HTTP request, reads the session cookie, and asks the bearer-token helper for the workspace claim. It returns a workspace UUID or None.

**Call relations**: The manifest attaches this as the billing route's identify function. Core uses its answer to bind the request to a workspace before _billing_projection runs.

*Call graph*: 1 external calls (workspace_claim).


##### `_billing_projection`  (lines 907–974)

```
async def _billing_projection(ext: ExtensionContext, request: Request) -> Response
```

**Purpose**: Builds the data shown on the standalone billing screen. This screen is important because a workspace with no credit may be unable to ask the chat agent why it stopped.

**Data flow**: It verifies the session cookie for this workspace, checks that the signed-in email belongs to an admin, then reads headroom, balance, autopay settings, and recent purchases from core. If the workspace is not balance-limited, it returns that. Otherwise it loads billing config, tries to read the saved card from Stripe, tolerates Stripe read failures by marking the card as unread, and returns a JSON response with balance, limits, card display, autopay, and purchase history.

**Call relations**: The billing HTTP route calls this after workspace identification. It combines authentication helpers, member checks, core balance reads, and _card_on_file into one response for the browser.

*Call graph*: calls 3 internal fn (transaction, _billing_record, _card_on_file); 9 external calls (configured_auto_topup, read_balance, read_headroom, recent_purchases, verify_token, JSONResponse, warn, member_by_email, member_is_admin).


##### `manifest`  (lines 977–1015)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the UFO host: its tools, scheduled jobs, HTTP route, prompt guidance, and credential slot. This is how the rest of the system discovers and runs the Metronome billing extension.

**Data flow**: It constructs and returns a Manifest object. The manifest includes the manage_billing tool, the usage shipping job, the balance top-up job, the billing page route, the billing prompt text, and the Anthropic BYOK credential slot.

**Call relations**: The extension loader calls this when registering the extension. The returned manifest connects _ship to metered workspaces, _top_up to topping-up workspaces, _billing_projection to the billing route, and manage_billing to chat actions.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, metered_workspaces, topping_up_workspaces).

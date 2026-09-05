# Connector action execution and proxy access  `stage-12.2`

This stage is the system’s gateway to the outside world during the agent’s main work. It lets an agent find tools, call them, move files, search the web, and reach approved services without handing it raw secrets.

The access files act like a guard booth. Egress rules turn user grants, connector settings, storage settings, and credentials into exact allow-or-deny rules. The egress resolver decides which outside destinations and injected secrets are safe. Egress control is the private phone line used by the Rust network proxy to ask for permission, fetch credentials, and report usage for billing.

The connector files are the adapters. Composio and Pipedream brokers discover tool catalogs, describe actions, run them, and prepare uploads or downloads. Their proxy files rewrite normal HTTP requests so the real service token stays hidden. Composio’s MCP session supports catalog search. The general connector tools expose safe find, inspect, run, and file-transfer actions to the agent.

Other extensions add tool sources: evaluation tools with seeded fake workplace data, workspace MCP servers, Perplexity-backed search and fetch, and research tools that route searches to the configured provider.

## Files in this stage

### Egress authorization
Defines the policy, private control surface, and enforceable rules that decide which external network access and credentials a sandboxed agent may use.

### `core/src/ufo/runtime/access/egress_resolver.py`

`domain_logic` · `request handling`

When an agent tries to connect to something outside its sandbox, the proxy needs a fresh answer: is this connection allowed, and should any credential be added? This file builds that answer from the agent’s token, the workspace it belongs to, the current database state, and any grants or stored credentials available to that agent. Think of it like a security desk that checks the badge every time, rather than trusting an old guest list.

The central class, PerAgentRules, starts with a basic set of rules that always apply. If the caller presents no valid principal, it returns only that base set. If the caller has a run token or probe token, it looks up the matching turn or conversation in the database, checks that it is still live, checks that any member authority still has a seat, and then builds the final rule list.

Those rules can include internet access, internal service access, preview access, package-cache access, workspace credentials, OAuth-style grants, and command-line tool credentials. Probe tokens are treated almost like normal runs, but with one important difference: they do not receive the deployment’s own model API key. The file also provides liveness checks used by the proxy before allowing a connection, so a stopped turn or revoked member cannot keep using old access.

#### Function details

##### `_seat_scope`  (lines 50–67)

```
def _seat_scope(workspace_id: UUID, authority: ExecutionAuthority) -> tuple[sa.ColumnElement[bool], ...]
```

**Purpose**: This helper adds the database condition that proves a member still has an active seat in the workspace. Workspace-wide authority needs no extra check, but member-specific authority must still be valid.

**Data flow**: It receives a workspace ID and an execution authority. If the authority is workspace-wide, it returns no extra database filters. If it belongs to a specific member, it returns a database condition that only passes when that member exists in the workspace and has a non-empty seated time. If it receives an authority type it does not understand, it raises an error instead of guessing.

**Call relations**: The database lookup functions use this whenever they need to prove a run, probe, or liveness check is still allowed for a member. It builds the small piece of SQL that PerAgentRules._turn_of, PerAgentRules._conversation_of, PerAgentRules.turn_live, and PerAgentRules.probe_live fold into their larger queries.

*Call graph*: called by 4 (_conversation_of, _turn_of, probe_live, turn_live); 2 external calls (exists, select).


##### `PerAgentRules.resolve`  (lines 110–166)

```
async def resolve(self, principal: EgressPrincipal | None) -> tuple[Rule, ...]
```

**Purpose**: This is the main rule builder. Given a run token, probe token, or no token, it returns the exact network rules the proxy should enforce for that caller right now.

**Data flow**: It takes a principal presented by the proxy. With no principal, it returns only the base rules. With a run token, it looks up the running turn; with a probe token, it looks up the related conversation. If that lookup fails, it returns no rules. If it succeeds, it combines the base rules with allowed internet rules, internal service rules, cache and preview rules, stored credential rules, grant-based rules, and command-line credential rules. For probe tokens, it removes the model-key injection before returning the final tuple of rules.

**Call relations**: This is the function the egress-control path depends on when the proxy needs a policy answer. It asks PerAgentRules._turn_of or PerAgentRules._conversation_of to identify the live authority, calls the external rule-derivation helpers to translate credentials and grants into concrete rules, and uses PerAgentRules._without_the_model_key at the end for probes so they cannot inherit the deployment model key.

*Call graph*: calls 3 internal fn (_conversation_of, _turn_of, _without_the_model_key); 7 external calls (__init__, __init__, derive_cli_rules, derive_credential_rules, derive_grant_rules, agent, ws).


##### `PerAgentRules.git_credential`  (lines 168–215)

```
async def git_credential(self, principal: EgressPrincipal, host: str) -> tuple[GitWire, str, str] | None
```

**Purpose**: This chooses the Git credential that a cache daemon should use when fetching from a Git host on behalf of a run or probe. If no single safe credential is available, it deliberately returns nothing so the fetch can proceed anonymously.

**Data flow**: It receives a principal and a Git host name. It first checks which live turn or conversation the principal belongs to. If the principal is no longer valid, or there is no grant store, it returns None. Otherwise it reads active grants for that agent, searches the configured command-line connectors for one whose Git settings match the host, and asks which account is usable for the current authority. If exactly one account fits, it tries to retrieve that account’s token. On success it returns the Git wire settings, the secret token, and the account ID. If token retrieval fails, it logs a warning and keeps looking; if nothing works, it returns None.

**Call relations**: The cache daemon calls this kind of logic when it needs to mirror or fetch Git data as the same identity the sandbox would use. It shares the same authority lookup path as PerAgentRules.resolve through PerAgentRules._turn_of and PerAgentRules._conversation_of, then hands the grant list to usable_cli_accounts so it does not accidentally choose a sibling or broader account.

*Call graph*: calls 2 internal fn (_conversation_of, _turn_of); 5 external calls (warn, usable_cli_accounts, agent, authority_member_id, ws).


##### `PerAgentRules._turn_of`  (lines 217–252)

```
async def _turn_of(self, run: RunToken) -> _Authority | None
```

**Purpose**: This looks up the agent and effective internet policy for a run token’s turn. It is the database-backed proof that the token still names a running turn in the right workspace.

**Data flow**: It receives a run token containing a workspace ID, turn ID, and authority. It opens a workspace-scoped database transaction and joins the turn to its agent. The query only succeeds if the turn is in the same workspace, the turn is marked running, the agent is in the same workspace, and any member-seat condition is satisfied. If no row matches, it returns None. If a row matches, it validates any stored runtime configuration, calculates whether internet access is truly allowed for this turn, and returns an internal _Authority object with the agent ID, internet flag, and original execution authority.

**Call relations**: PerAgentRules.resolve uses this before building rules for a normal run, and PerAgentRules.git_credential uses it before choosing a Git account. It relies on _seat_scope to add the member-seat guard and on the turn runtime configuration parser to respect per-turn internet restrictions.

*Call graph*: calls 1 internal fn (_seat_scope); called by 2 (git_credential, resolve); 4 external calls (__init__, model_validate, select, workspace_tx).


##### `PerAgentRules._conversation_of`  (lines 254–286)

```
async def _conversation_of(self, probe: ProbeToken) -> _Authority | None
```

**Purpose**: This looks up the agent and internet policy for a probe token, which names a conversation rather than a specific turn. It also rejects expired probes.

**Data flow**: It receives a probe token with a workspace ID, conversation ID, expiry time, and authority. If the expiry time has passed, it returns None immediately. Otherwise it opens a workspace-scoped database transaction and joins the conversation to its agent. The lookup only succeeds if both belong to the workspace and any member-seat condition still passes. If nothing matches, it returns None. If it finds the conversation, it returns an _Authority object containing the agent ID, the agent’s internet-access setting, and the token’s execution authority.

**Call relations**: PerAgentRules.resolve calls this when building rules for a probe, and PerAgentRules.git_credential calls it when a probe needs a Git credential. Like the turn lookup, it uses _seat_scope so member-specific probes stop working when that member no longer has a valid seat.

*Call graph*: calls 1 internal fn (_seat_scope); called by 2 (git_credential, resolve); 4 external calls (__init__, now, select, workspace_tx).


##### `PerAgentRules._without_the_model_key`  (lines 288–299)

```
def _without_the_model_key(self, rules: tuple[Rule, ...]) -> tuple[Rule, ...]
```

**Purpose**: This removes the deployment’s own model API key injection from a rule set. It exists because probes may reach model hosts, but they should not receive the platform’s model credential.

**Data flow**: It receives a tuple of already-built rules. It scans through them and keeps every rule except injection rules whose sentinel marker includes the model-key sentinel. The output is a new tuple with the same allowed hosts and credentials except for that model-key injection.

**Call relations**: PerAgentRules.resolve calls this only for probe tokens, after building the rules in the same general way it would for a run. This final filtering step preserves workspace credentials and grant-based access while withholding the special model key.

*Call graph*: called by 1 (resolve).


##### `PerAgentRules.turn_live`  (lines 301–333)

```
async def turn_live(self, run: RunToken) -> int | None
```

**Purpose**: This is a fast authorization gate for a run token. It tells the proxy whether the turn is still running and, if so, which egress-rules generation is current.

**Data flow**: It receives a run token. Inside the token’s workspace scope, it queries the database for the turn status and the workspace’s egress-rules generation, again applying any member-seat requirement. If the turn is missing or is not running, it returns None. If the turn is live, it returns the generation number, which represents the current version of the workspace’s egress policy.

**Call relations**: The proxy can use this before allowing a connection or reusing cached rules. It shares the same member-seat guard built by _seat_scope, so a token stops authorizing network access when the turn ends or the member loses their seat.

*Call graph*: calls 1 internal fn (_seat_scope); 3 external calls (select, workspace_tx, ws).


##### `PerAgentRules.probe_live`  (lines 335–357)

```
async def probe_live(self, probe: ProbeToken) -> int | None
```

**Purpose**: This is the liveness check for a probe token. It confirms that the probe has not expired, its conversation still exists, and the workspace’s current egress-rules generation can be read.

**Data flow**: It receives a probe token. If the probe expiry time has passed, it returns None. Otherwise it enters the token’s workspace scope and queries the database through the conversation to the workspace, applying any member-seat condition. If the conversation is valid, it returns the workspace’s current egress-rules generation. If not, it returns None.

**Call relations**: This plays the same role for probes that PerAgentRules.turn_live plays for normal runs. It uses _seat_scope for the member-seat part of the check and gives the proxy a fresh generation value so old cached policy can be invalidated when rules change.

*Call graph*: calls 1 internal fn (_seat_scope); 4 external calls (now, select, workspace_tx, ws).


### `core/src/ufo/runtime/access/egress_control.py`

`io_transport` · `request handling`

The Rust egress proxy sits close to network traffic, but it deliberately does not know customer secrets, workspace ownership, or policy rules. This file is the “control desk” it phones home to. The proxy brings a shared control token to prove it is allowed to use these internal routes, and it also sends a run or probe token that identifies the specific sandbox activity asking for access.

`EgressControl` builds FastAPI routers, which are web route collections. One router serves `/internal/egress/*` for authorization, rule lookup, metering, and tool bridge requests. A separate router serves `/internal/git-credential`, protected by a different token, so the cache daemon can only ask for Git credentials and cannot enter the broader secrets-and-metering area.

The file has three main jobs. First, it verifies who is asking by decoding run and probe tokens. Second, it asks `PerAgentRules` for live status, allowed egress rules, and Git credentials scoped to the correct workspace. Third, it aggregates usage reports from the proxy and writes them to the billing ledger. This aggregation matters because the proxy may send many small records; the core service combines them before writing, like totaling receipts before entering them into an accounting book.

A small but important detail is `rule_json`: it serializes policy rules in exactly the shape the Rust proxy expects. If those field names changed casually, the proxy would stop understanding the rules.

#### Function details

##### `rule_json`  (lines 47–67)

```
def rule_json(rule: Rule) -> dict[str, object]
```

**Purpose**: This turns one internal egress policy rule into the exact JSON-like dictionary the Rust proxy understands. It is used so Python policy objects can cross the service boundary safely and consistently.

**Data flow**: It receives one rule object, checks which kind of rule it is, and copies the important fields into a plain dictionary with a `kind` label. The output is ready to be serialized into an HTTP response for the proxy.

**Call relations**: When the proxy asks to resolve egress rules, `EgressControl._resolve` gets the internal rule objects and uses this function on each one before returning them. This is the translation step between Python’s policy model and the Rust proxy’s wire format.

*Call graph*: called by 1 (_resolve).


##### `EgressControl.router`  (lines 144–150)

```
def router(self) -> APIRouter
```

**Purpose**: This builds the private egress-control web routes used by the Rust proxy. It groups the authorize, resolve, meter, and tool-bridge endpoints under `/internal/egress` and protects them with the main control token check.

**Data flow**: It starts with the `EgressControl` object’s methods and shared secret guard, creates a FastAPI router, attaches four POST routes to it, and returns that router to be mounted by the service.

**Call relations**: During service setup, the application calls this to expose the proxy-facing control API. Every request reaching these routes must first pass through `EgressControl._guard`, so the handler methods only run after the proxy’s bearer token is accepted.

*Call graph*: 2 external calls (APIRouter, Depends).


##### `EgressControl.git_credential_router`  (lines 152–157)

```
def git_credential_router(self) -> APIRouter
```

**Purpose**: This builds a separate private web route for Git credential lookup by the cache daemon. It is intentionally separated from the main egress-control routes so a cache-only token cannot access metering or secrets APIs.

**Data flow**: It creates a FastAPI router under `/internal`, attaches the `/git-credential` POST route, protects it with the cache-specific guard, and returns the router for the service to mount.

**Call relations**: During startup, the service can mount this alongside the main egress router. Requests to this route go through `EgressControl._cache_guard` and then into `EgressControl._git_credential`, keeping cache credential access on its own narrow path.

*Call graph*: 2 external calls (APIRouter, Depends).


##### `EgressControl._guard`  (lines 159–161)

```
async def _guard(self, authorization: Annotated[str, Header()]='') -> None
```

**Purpose**: This checks whether a request to the main internal egress API has the correct bearer token. It stops unauthorized callers before any policy, secret, or billing work begins.

**Data flow**: It reads the HTTP `Authorization` header, compares it with `Bearer <control_token>`, and returns silently if it matches. If it does not match, it raises an HTTP 401 error, which means “unauthorized.”

**Call relations**: The router created by `EgressControl.router` installs this guard as a dependency, so FastAPI runs it before authorize, resolve, meter, or tool-bridge requests. It acts like the locked front door for the proxy-only API.

*Call graph*: 1 external calls (HTTPException).


##### `EgressControl._cache_guard`  (lines 163–165)

```
async def _cache_guard(self, authorization: Annotated[str, Header()]='') -> None
```

**Purpose**: This checks whether a request to the Git credential callback has the cache daemon’s special bearer token. It prevents that route from being called by anyone without the cache-only shared secret.

**Data flow**: It reads the HTTP `Authorization` header and compares it with `Bearer <cache_control_token>`. A match lets the request continue; a mismatch raises an HTTP 401 error.

**Call relations**: The router from `EgressControl.git_credential_router` uses this guard before calling `EgressControl._git_credential`. This keeps the cache daemon’s route separate from the broader egress-control surface.

*Call graph*: 1 external calls (HTTPException).


##### `EgressControl._authorize`  (lines 167–170)

```
async def _authorize(self, body: AuthorizeRequest) -> AuthorizeResponse
```

**Purpose**: This answers the proxy’s quick question: “Is this run or probe still allowed to make network connections?” It also returns a generation number that helps the proxy know whether its cached rules are still current.

**Data flow**: It receives a request containing the raw proxy authorization value, decodes it into a run or probe identity using `EgressControl._principal`, and asks `EgressControl._live_generation` whether that identity is still live. It returns an authorization response with `authorized` set to true only when a live generation was found.

**Call relations**: The proxy calls this before allowing traffic. This method relies on `_principal` to understand the token and `_live_generation` to ask the policy resolver whether the corresponding run or probe is still valid.

*Call graph*: calls 2 internal fn (_live_generation, _principal); 1 external calls (__init__).


##### `EgressControl._live_generation`  (lines 172–177)

```
async def _live_generation(self, principal: EgressPrincipal) -> int | None
```

**Purpose**: This asks the rule resolver whether a specific run or probe is still active, and gets the current policy generation for it. The generation is like a version number for the allowed network rules.

**Data flow**: It receives a decoded principal, either a run token or a probe token. For a run, it asks the resolver about the live turn; for a probe, it asks about the live probe. It returns an integer generation if live, or `None` if not live.

**Call relations**: `EgressControl._authorize` calls this after decoding the proxy’s token. This function is the small branching point that sends run checks and probe checks to the correct resolver method.

*Call graph*: called by 1 (_authorize).


##### `EgressControl._resolve`  (lines 179–181)

```
async def _resolve(self, body: ResolveRequest) -> dict[str, object]
```

**Purpose**: This returns the actual network access rules the proxy should enforce for a run or probe. It is how the proxy learns which hosts are allowed, which secrets to inject, what to meter, and what service routes exist.

**Data flow**: It receives a request with proxy authorization, decodes that into a principal, asks the resolver for the matching internal rules, converts each rule through `rule_json`, and returns them in a plain response dictionary.

**Call relations**: The Rust proxy calls this when it needs the current rule set. This method sits between the resolver, which owns the policy, and `rule_json`, which turns each rule into the exact format the proxy can deserialize.

*Call graph*: calls 2 internal fn (_principal, rule_json).


##### `EgressControl._meter`  (lines 183–238)

```
async def _meter(self, body: MeterRequest) -> dict[str, object]
```

**Purpose**: This receives usage reports from the proxy and records them for metrics and billing. It combines repeated records first, so the system writes cleaner totals instead of one database entry per tiny event.

**Data flow**: It receives a list of metering records. Metric records are counted by host and dimension, egress records are counted by workspace and turn, and token records are added together by workspace, turn, and model. It emits monitoring counters, opens the correct workspace context, writes egress counts and token usage into the accounting system, and returns an empty response.

**Call relations**: The proxy calls this after observing network requests or model token usage on the wire. While writing token usage, this method calls `EgressControl._priced_cache_write` to adjust cache-write tokens according to pricing rules, then hands the final amounts to the billing accounting functions.

*Call graph*: calls 1 internal fn (_priced_cache_write); 7 external calls (__init__, workspace_tx, emit_metric, record_egress_request, record_probe_egress_request, record_sandbox_tokens, ws).


##### `EgressControl._priced_cache_write`  (lines 240–253)

```
def _priced_cache_write(self, model: str, usage: Usage) -> Usage
```

**Purpose**: This fixes token usage before billing when a model does not have a special price for 30-minute cache writes. In that case, those cache-write tokens are treated as normal input tokens so billing matches the rest of the system.

**Data flow**: It receives a model name and a usage object. It looks up the model’s price information; if the model supports 30-minute cache-write pricing, or there are no such tokens, it returns the usage unchanged. Otherwise, it returns a copied usage object where those cache-write tokens have been moved into normal input tokens.

**Call relations**: `EgressControl._meter` calls this right before recording sandbox token usage. This keeps the proxy simple: the proxy reports what it saw, and the core service applies the pricing knowledge.

*Call graph*: called by 1 (_meter); 1 external calls (model_copy).


##### `EgressControl._tool_bridge`  (lines 255–260)

```
async def _tool_bridge(self, body: ToolBridgeControlRequest) -> ToolBridgeResponse
```

**Purpose**: This lets the proxy ask the host-side tool bridge to perform a bounded tool request for a live run. It only accepts run tokens, not probe tokens, and refuses the request if no bridge is configured.

**Data flow**: It receives proxy authorization and a tool bridge request. It decodes the authorization, checks that it is a run token and that a bridge exists, enters the run’s workspace context, sends the request to the bridge, and returns the bridge’s response. If the checks fail, it raises a 403 error, which means “forbidden.”

**Call relations**: The proxy calls this when sandbox traffic needs to cross into the controlled tool bridge. This method uses `_principal` for identity, then hands the validated request to the configured `bridge` object inside the correct workspace.

*Call graph*: calls 1 internal fn (_principal); 2 external calls (HTTPException, ws).


##### `EgressControl._git_credential`  (lines 262–279)

```
async def _git_credential(self, body: GitCredentialRequest) -> dict[str, object]
```

**Purpose**: This gives the cache daemon the Git credential that the proxy would have injected for a specific run or probe and host. If no valid credential is available, it tells the daemon to fetch publicly instead of leaking another account’s identity.

**Data flow**: It receives an optional proxy authorization value and an optional host. Without both a valid principal and a host, it returns `principal: public`. With them, it asks the resolver for a matching Git credential. If none exists, it returns an anonymous/public result; if one exists, it returns the username, token, and a principal label tied to the workspace and account.

**Call relations**: Requests reach this method through the separate Git credential router and cache guard. It uses `_principal` to decode the run or probe token, then asks the resolver for a credential scoped to that exact principal and host.

*Call graph*: calls 1 internal fn (_principal).


##### `EgressControl._principal`  (lines 281–291)

```
def _principal(self, proxy_auth: str) -> EgressPrincipal | None
```

**Purpose**: This decodes the proxy authorization string into the run or probe identity it represents. If the string is empty or invalid, it returns `None` instead of trusting it.

**Data flow**: It receives the raw proxy authorization value. It first tries to decode it as a run token using the configured run token codec. If that fails, it tries to decode it as a probe token using the same secret. The result is a run token, a probe token, or `None`.

**Call relations**: Authorization, rule resolution, tool bridge requests, and Git credential lookup all call this before doing principal-specific work. It is the common identity checkpoint for the internal egress control flow.

*Call graph*: called by 4 (_authorize, _git_credential, _resolve, _tool_bridge); 1 external calls (__init__).


### `core/src/ufo/runtime/access/egress_rules.py`

`domain_logic` · `turn setup / egress rule derivation`

A sandbox should not be able to call any internet address or see raw secret keys. This file builds the rulebook for the egress proxy, which is the gatekeeper for outgoing network traffic. Think of it like a security desk: it checks which doors are open, which badge should be exchanged for a real key, and which visits must be counted for billing or auditing.

The rules are small data objects. A ScopeRule says “these exact hosts are allowed.” An InternetRule says the sandbox may use public internet during a live turn. An InjectionRule says “if the sandbox sends this harmless placeholder value, replace it on the wire with the real secret.” A MeterRule says requests to a host should be counted under a named usage bucket. A ServiceRule describes special local service hosts, such as preview or cache daemons.

The file derives rules from several sources. The chosen model opens only its provider host and injects the model API key. Extension manifests may allow public internet. An S3 artifact store allows only its own storage host. Workspace credentials can allow and inject per-provider secrets. Grants allow connector hosts and file-transfer hosts. CLI credentials get special treatment because their tokens may be used both against an API host and a Git host. Importantly, many failures are isolated: if one credential cannot be read, that one slot is skipped and a warning is logged, instead of breaking the whole turn’s network access.

#### Function details

##### `provider_host`  (lines 95–99)

```
def provider_host(model: str) -> str
```

**Purpose**: This function identifies which company API host should be used for a model name. For example, model names that look like OpenAI models map to OpenAI’s API host, while Claude models map to Anthropic’s host.

**Data flow**: It receives a model name as text. It checks the model name against known prefixes, such as "claude-" or "gpt-". If it finds a match, it returns the matching provider host; if not, it raises an error because the system would not know where that model should be contacted.

**Call relations**: derive_model_rules calls this first, because model network rules cannot be built until the provider host is known. It does not call other project functions; it is the simple lookup step at the start of model rule creation.

*Call graph*: called by 1 (derive_model_rules).


##### `derive_model_rules`  (lines 102–117)

```
def derive_model_rules(model: str, real_key: str) -> tuple[Rule, ...]
```

**Purpose**: This function builds the network rules needed for the sandbox to call the selected AI model provider. It allows the provider host, swaps the sandbox’s placeholder model key for the real key, and marks model traffic for token metering.

**Data flow**: It receives the model name and the real API key. It finds the provider host, chooses the correct authentication header shape for that provider, and creates three rules: allow that host, inject the real key where the sandbox sent the sentinel placeholder, and meter usage as tokens. It returns those rules as a tuple.

**Call relations**: It relies on provider_host to translate the model name into a host. After that it creates ScopeRule, InjectionRule, and MeterRule objects, which the egress proxy will later read when deciding whether and how to pass model-provider traffic.

*Call graph*: calls 1 internal fn (provider_host); 3 external calls (__init__, __init__, __init__).


##### `derive_manifest_rules`  (lines 120–122)

```
def derive_manifest_rules(manifests: tuple[Manifest, ...]) -> tuple[InternetRule, ...]
```

**Purpose**: This function checks whether any extension manifest says the sandbox needs public internet access. If so, it adds the rule that permits public internet during the live turn.

**Data flow**: It receives the deploy’s manifests. It looks for any manifest with sandbox internet enabled. If at least one asks for it, it returns an InternetRule; otherwise it returns an empty tuple, meaning no broad internet permission comes from manifests.

**Call relations**: This is one of the rule sources combined with model, credential, grant, and storage rules. It creates an InternetRule only when manifests request that wider access.

*Call graph*: 1 external calls (__init__).


##### `derive_artifact_store_rules`  (lines 125–141)

```
async def derive_artifact_store_rules(blob: FilesystemBlobStore | S3BlobStore) -> tuple[Rule, ...]
```

**Purpose**: This function gives the sandbox just enough network access to upload produced files when the artifact store is S3. Without this, sharing a file through a presigned S3 URL could be blocked by the egress proxy.

**Data flow**: It receives the configured blob store. If the store is S3, it asks the store for the host used for uploads, then returns rules allowing that exact host and metering requests to it. If the store is local filesystem storage, there is no network host to allow, so it returns no rules.

**Call relations**: When artifact sharing needs network access, this function supplies narrowly scoped rules instead of broad public internet. It calls the blob store’s put_host method for S3 and then creates ScopeRule and MeterRule entries for the proxy.

*Call graph*: 3 external calls (__init__, __init__, put_host).


##### `derive_credential_rules`  (lines 144–199)

```
async def derive_credential_rules(slots: tuple[CredentialSlot, ...], workspace_id: UUID, store: CredentialStore) -> tuple[Rule, ...]
```

**Purpose**: This function turns workspace credential slots into safe egress rules. If a connector declares that a request should carry a secret, this function allows the right host and arranges for the proxy to replace a sandbox-visible placeholder with the real secret.

**Data flow**: It receives credential slot declarations, a workspace ID, and the credential store. For each slot that has an injection target, it tries to read the real secret and resolve the host. If the slot is unset, unreadable, or has no available host, that slot contributes nothing. Successful slots are grouped by host, then the function returns allow rules, injection rules, and optional metering rules for each host.

**Call relations**: This function is designed to fail softly per credential slot. It calls CredentialStore.get and credential_host to resolve secrets and hosts, logs warnings through warn when something unexpected goes wrong, and creates ScopeRule, InjectionRule, and MeterRule objects for the proxy. Other rule derivation can safely continue even if one credential is bad.

*Call graph*: calls 1 internal fn (get); 5 external calls (__init__, __init__, __init__, warn, credential_host).


##### `derive_grant_rules`  (lines 202–220)

```
def derive_grant_rules(grants: tuple[Grant, ...], transfer_hosts: 'ConnectorTransferHosts | None'=None) -> tuple[Rule, ...]
```

**Purpose**: This function gives network access for active connector grants. A grant means the sandbox may reach the connector provider’s host, plus any file-transfer hosts the connector uses, and those requests should be counted.

**Data flow**: It receives active grants and, optionally, a ConnectorTransferHosts lookup. For each grant, it collects the grant’s provider host and any extra transfer hosts, removes duplicates and empty values, then creates one allow rule covering those hosts and request-metering rules for each. It returns all of those rules as a tuple.

**Call relations**: This is used when grants are converted into proxy permissions. If a ConnectorTransferHosts object is provided, derive_grant_rules asks it which extra hosts belong to the grant’s provider. It only creates ScopeRule and MeterRule entries; token injection for CLI-style grants is handled separately by derive_cli_rules.

*Call graph*: 2 external calls (__init__, __init__).


##### `derive_cli_rules`  (lines 223–270)

```
async def derive_cli_rules(grants: tuple[Grant, ...], authority: ExecutionAuthority, clis: Mapping[str, CliCredential], workspace_id: UUID) -> tuple[Rule, ...]
```

**Purpose**: This function builds secret-injection rules for connector grants that expose a command-line credential. It lets the proxy swap a grant placeholder for the real account token, and it also opens any related Git host needed by the connector.

**Data flow**: It receives active grants, the current execution authority, available CLI credential definitions, and the workspace ID. It works out which member, if any, the authority represents. For each grant with a matching CLI credential and usable ownership or sharing permission, it asks the broker-side secret object for the real token. If that succeeds, it creates an injection rule for the provider host; if the credential also has a Git host, it groups Git injection rules by host and later adds allow and metering rules for those Git hosts. If token lookup fails for one grant, it logs a warning and skips only that grant.

**Call relations**: This complements derive_grant_rules. Grant rules allow and meter the main provider host, while derive_cli_rules supplies the actual token swap and separately scopes Git hosts. It calls authority_member_id to understand who may use which grant, grant_sentinel to know the placeholder value, warn for recoverable failures, and creates InjectionRule, ScopeRule, and MeterRule objects.

*Call graph*: 6 external calls (__init__, __init__, __init__, warn, grant_sentinel, authority_member_id).


##### `ConnectorTransferHosts.of`  (lines 284–285)

```
def of(self, provider: str) -> tuple[str, ...]
```

**Purpose**: This method answers the question: “Which file-transfer hosts should this connector provider be allowed to use?” It respects explicit connector declarations before falling back to a default namespace.

**Data flow**: It receives a provider name. It first checks the explicit mapping of provider names to host tuples. If the provider is present there, it returns that exact tuple, even if it is empty; otherwise it returns the default transfer hosts.

**Call relations**: derive_grant_rules calls this when it needs extra hosts for a grant. The method is intentionally small: connector_transfer_hosts prepares the mapping, and this method performs the lookup during grant rule derivation.


##### `connector_transfer_hosts`  (lines 288–299)

```
def connector_transfer_hosts(manifests: tuple[Manifest, ...]) -> ConnectorTransferHosts
```

**Purpose**: This function builds the lookup table that says which connector file-transfer hosts belong to which provider. It also records default hosts from the open connector namespace for providers that were not explicitly registered.

**Data flow**: It receives the deploy’s manifests. It walks through all declared connectors and records each connector provider’s transfer hosts. Then it asks for the open connector namespace; if one exists, its transfer hosts become the default. The result is a ConnectorTransferHosts object containing both the explicit provider map and the default tuple.

**Call relations**: This prepares data for derive_grant_rules. It calls open_connector_namespace to find any namespace-level default and then constructs ConnectorTransferHosts, whose of method is later used when grant rules are being built.

*Call graph*: 2 external calls (__init__, open_connector_namespace).


### Composio integration
Discovers Composio toolkits, searches their catalog, executes actions, and rewrites provider traffic through Composio proxy access.

### `extensions/composio/ufo_ext_composio/resolver.py`

`orchestration` · `connector discovery and connect flow`

Composio offers many third-party toolkits, and this project does not want to hard-code each one as a separate connector. This file provides a small resolver that treats Composio like an open catalog: if a member asks for a provider slug, such as a service name, the resolver decides whether Composio can connect to it.

The main class, ComposioResolver, is deliberately lightweight. It stores only a shared ConnectorBroker, which is the object that actually runs brokered connector work. For everything else, it asks the Composio client at the moment it is needed. That matters because tests or runtime setup can swap the client transport or credentials without stale connections being kept around.

The flow is simple. First, claims rejects locally banned provider names. If the name is not banned, it asks Composio’s live catalog whether that toolkit is connectable. If it is valid, descriptor creates an OAuthProvider description. OAuth is the common web sign-in permission flow, but here the host is blank because Composio keeps the account token and runs tools server-side. entry then builds the connector entry that points the provider name to the shared broker. catalog supports discovery by searching Composio’s toolkit catalog and returning only services that can actually be connected. transfer_hosts lists Composio file-transfer hosts so sandboxed tool inputs and outputs can safely pass through the approved path.

#### Function details

##### `ComposioResolver.transfer_hosts`  (lines 32–33)

```
def transfer_hosts(self) -> tuple[str, ...]
```

**Purpose**: This property tells the rest of the system which Composio file-store hosts are allowed for transfers. It is used so files moving in and out of Composio-run tools can pass through the sandbox safely.

**Data flow**: It takes no caller-supplied data. It reads the predefined COMPOSIO_TRANSFER_HOSTS list from the Composio client module and returns it unchanged as the approved set of host names.

**Call relations**: When the connector system needs to know what outside file-transfer locations a Composio-backed grant may use, it reads this property. The value is not computed here; this resolver simply exposes the shared Composio transfer-host list to the broader connector flow.


##### `ComposioResolver.claims`  (lines 35–38)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: This function answers the question, “Does Composio claim this provider name?” It prevents banned names from being accepted locally, then checks Composio’s live catalog to see whether the requested toolkit can actually be connected.

**Data flow**: It receives a provider slug as text. First it lowercases the slug and compares it with the local banned list; banned names immediately produce false. Otherwise it creates or retrieves the current Composio client, asks whether the toolkit is connectable, and returns true only if Composio reports a matching toolkit.

**Call relations**: The connector registry uses this during provider resolution, after explicitly registered connectors have had the chance to claim the name. If no built-in connector claims the provider, this method asks Composio whether the open namespace should take it, using ufo_ext_composio.client.composio_client to reach the live Composio catalog.

*Call graph*: 1 external calls (composio_client).


##### `ComposioResolver.descriptor`  (lines 40–41)

```
def descriptor(self, provider: str) -> OAuthProvider
```

**Purpose**: This function builds the connection description for a Composio-backed provider. It returns an OAuthProvider object, which describes an OAuth-style permission connection, while leaving the host blank because Composio keeps and uses the account token on its own side.

**Data flow**: It receives a provider slug. It places that slug into a new ComposioOAuthProvider and sets the host field to an empty string, then returns that descriptor to the caller.

**Call relations**: After a provider has been accepted as a Composio toolkit, the connect flow asks this resolver for the provider descriptor. This method hands off to ComposioOAuthProvider.__init__ to create the descriptor object that the rest of the connector system can understand.

*Call graph*: 1 external calls (__init__).


##### `ComposioResolver.entry`  (lines 43–46)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: This function creates the connector registry entry for a Composio toolkit. The entry gives the provider a human-readable label and points it to the shared Composio broker that will run its tools.

**Data flow**: It receives a provider slug. It turns underscores into spaces and title-cases the result for a display label, combines that with the original provider name and the resolver’s broker, and returns a new ConnectorEntry.

**Call relations**: Once the system has decided that a provider belongs to Composio, it needs an entry that says where requests for that provider should go. This method creates that entry through ConnectorEntry.__init__, routing many possible Composio toolkit names to the same shared broker.

*Call graph*: 1 external calls (__init__).


##### `ComposioResolver.catalog`  (lines 48–51)

```
async def catalog(self, query: str, limit: int=TOOLKIT_SEARCH_LIMIT, after: str | None=None) -> CatalogPage
```

**Purpose**: This function searches Composio’s toolkit catalog for connectable services. It supports discovery tools, so users can find services they are actually able to connect rather than seeing unusable names.

**Data flow**: It receives a search query, a maximum number of results, and an optional cursor named after for pagination, meaning continuing from a previous page. It gets the current Composio client, asks it to list matching toolkits, and returns the resulting CatalogPage.

**Call relations**: Discovery code calls this when it wants to show possible Composio-backed connectors. The resolver delegates the real search to the current Composio client through ufo_ext_composio.client.composio_client, then passes the catalog page back to the caller.

*Call graph*: 1 external calls (composio_client).


### `extensions/composio/ufo_ext_composio/broker.py`

`io_transport` · `connector discovery, request handling, tool execution, and file transfer`

ComposioBroker is the shared doorway used whenever UFO wants to work with a Composio-backed connector, such as an app or service provider. Without this file, the system would not know how to translate UFO’s connector requests into Composio API calls, or how to turn Composio’s answers back into the common shapes UFO expects.

The broker is intentionally stateless. Each method asks for the current Composio client when it runs, instead of keeping one forever. That matters for tests and for configuration changes, because each call uses the latest transport and API key setup.

The file covers the whole connector flow. It can list available tools, fetch one tool’s input schema, run a tool for a workspace user, stage a file upload, find files returned by a tool, search through tools, and create a safe HTTP credential proxy. The proxy is important: a caller can make provider API requests through Composio without ever receiving the provider’s real secret token.

It also improves failure messages. If a tool slug is wrong, it tries to include real available tool slugs so the next attempt can be better. If an account grant is stale or no longer belongs to this workspace’s broker user, it tells the agent to ask the member to reconnect instead of pretending the tool itself was wrong.

#### Function details

##### `ComposioBroker.tools`  (lines 49–50)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Finds Composio tools for a given provider and search text, then presents them in UFO’s standard tool format. Someone uses this when they need to show or choose possible actions for a connector.

**Data flow**: It receives a workspace ID, a provider name, and a query. It asks the current Composio client for matching tools, then passes those raw rows through a converter that keeps the slug, short description, input shape, and read-only hint. It returns a tuple of BrokerTool objects.

**Call relations**: This is the discovery entry point for Composio tools. It gets the current client from the Composio client factory, then hands the raw catalog response to _discovered_tools so the rest of UFO sees a consistent tool list rather than Composio’s native response shape.

*Call graph*: calls 1 internal fn (_discovered_tools); 1 external calls (composio_client).


##### `ComposioBroker.schema`  (lines 52–66)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Fetches the detailed description and input requirements for one Composio tool. This is used before running a tool so the caller knows what arguments the tool expects.

**Data flow**: It receives a workspace ID, provider name, and tool slug. It asks Composio for that tool’s schema, rewrites any file-upload inputs into UFO’s workspace-file language, checks whether the tool is marked read-only, and returns a BrokerTool. If Composio says the slug does not exist, it raises UnknownBrokerTool instead of returning a misleading empty result.

**Call relations**: When a caller needs one specific tool’s contract, this method talks to the current Composio client. It uses workspace_file_schema to translate file inputs and _read_only to read Composio’s tag hint, then packages everything into BrokerTool for the wider connector system.

*Call graph*: calls 1 internal fn (_read_only); 4 external calls (__init__, __init__, composio_client, workspace_file_schema).


##### `ComposioBroker.execute`  (lines 68–91)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs a named Composio tool for this workspace and account. This is the main path that turns an agent’s chosen action and arguments into a real server-side Composio execution.

**Data flow**: It receives the workspace, provider, tool slug, argument values, connected account ID, and an optional idempotency key, which is a repeat-safe key used to avoid accidental duplicate work. It builds the Composio external user ID from the workspace ID, sends the execution request, and returns Composio’s response as a dictionary. If the account is stale, it raises a reconnect-focused error; if the slug is missing, it tries to return a more helpful unknown-tool error with available slugs.

**Call relations**: This method is called when UFO is ready to perform an actual connector action. It first delegates to the Composio client. On errors, it uses _stale_account to decide whether the member must reconnect, _reconnect_error to make that message clear, and _slug_miss to improve bad-tool-slug feedback.

*Call graph*: calls 3 internal fn (_slug_miss, _reconnect_error, _stale_account); 1 external calls (composio_client).


##### `ComposioBroker.file_outputs`  (lines 93–98)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Finds files that a Composio tool produced inside a nested execution response. This lets UFO notice downloadable results even if they are buried deep in the returned data.

**Data flow**: It receives the full response dictionary from a tool execution. It walks through that dictionary and any nested lists or dictionaries, collecting objects that look like Composio file records with a name and presigned file URL. It returns those files as BrokerFile objects.

**Call relations**: After execute returns a response, this helper-facing method can be used to extract file outputs. It relies on _collect_files to do the recursive searching and then returns the gathered results in the connector system’s standard file format.

*Call graph*: calls 1 internal fn (_collect_files).


##### `ComposioBroker.stage_upload`  (lines 100–116)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Creates a temporary upload slot for a file that will be passed into a Composio tool. This is used when a tool needs a file input, but the file must first be placed where Composio can read it.

**Data flow**: It receives the workspace, provider, tool slug, filename, MIME type, and MD5 checksum. It asks Composio to create an upload target, then returns a StagedUpload containing the URL to upload bytes to, the content type to send, and the argument object that should later be passed to the tool.

**Call relations**: This method sits before tool execution in file-input workflows. It talks to the current Composio client to reserve storage, then hands the caller both the upload instructions and the tool argument that points Composio at the uploaded file.

*Call graph*: 2 external calls (__init__, composio_client).


##### `ComposioBroker.search`  (lines 118–121)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Searches Composio’s tool catalog using Composio’s tool-router search feature. This is useful when a caller has a natural-language need and wants matching connector tools.

**Data flow**: It receives the workspace ID, provider, and query text. It gets the current Composio client and passes the search request to the shared Composio search helper. It returns a BrokerSearch result.

**Call relations**: This is a higher-level discovery path than tools. Rather than directly listing tools itself, it delegates to search_connector_tools, which knows how to use Composio’s tool-router search and return the result in UFO’s expected search shape.

*Call graph*: 2 external calls (composio_client, search_connector_tools).


##### `ComposioBroker.credential`  (lines 123–139)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Creates a safe credential object for provider HTTP access through Composio, without giving the caller a real provider token. It first confirms that the requested connected account belongs to this workspace’s broker user.

**Data flow**: It receives the workspace ID, provider, and connected account ID. It builds the broker user ID, asks Composio whether that account is connected for this workspace and provider, and fails with reconnect guidance if Composio cannot find it. If the account is valid, it returns a Credential whose transport sends requests through Composio’s proxy execution layer.

**Call relations**: This is used when code needs to make provider API calls while keeping secrets out of UFO. It verifies ownership through the Composio client, then builds a ComposioProxyTransport wrapped in a Credential. If the grant is stale, it raises GrantUnusable with guidance from stale_grant_guidance.

*Call graph*: calls 1 internal fn (__init__); 5 external calls (__init__, __init__, AsyncHTTPTransport, stale_grant_guidance, composio_client).


##### `ComposioBroker._slug_miss`  (lines 141–162)

```
async def _slug_miss(self, client: composio.ComposioClient, provider: str, slug: str, error: composio.ComposioError) -> composio.ComposioError
```

**Purpose**: Improves a “tool not found” error by trying to include real tool slugs available for the provider. This helps the next model or agent attempt choose a valid tool name.

**Data flow**: It receives the Composio client, provider, missing slug, and original error. It turns the missing slug into search words, asks Composio for nearby tools, and if needed falls back to listing tools without a query. If it finds tools, it returns a new ComposioError with the original message plus available slugs; otherwise it returns the original error.

**Call relations**: ComposioBroker.execute calls this only after Composio reports a missing tool slug. It uses _discovered_tools to convert any candidate tool rows before building the clearer error message, and it deliberately treats this enhancement as best-effort so a failed lookup does not hide the original problem.

*Call graph*: calls 2 internal fn (_discovered_tools, list_tools); called by 1 (execute); 2 external calls (sub, ComposioError).


##### `_collect_files`  (lines 165–174)

```
def _collect_files(value: object, found: list[BrokerFile]) -> None
```

**Purpose**: Recursively searches a value for Composio file objects and adds them to a result list. It exists because file outputs may appear anywhere inside a tool’s nested response.

**Data flow**: It receives any value and a list being filled with BrokerFile objects. If the value looks like a Composio file record, it adds a BrokerFile with the file name and URL. If the value is a dictionary or list, it searches each contained value; otherwise it leaves it alone.

**Call relations**: ComposioBroker.file_outputs starts the search with the full execution response and an empty list. _collect_files then walks the response tree like checking every drawer in a filing cabinet, adding each matching file record it finds.

*Call graph*: called by 1 (file_outputs); 1 external calls (__init__).


##### `_stale_account`  (lines 177–188)

```
def _stale_account(error: composio.ComposioError, account_id: str) -> bool
```

**Purpose**: Decides whether a Composio execution error means the connected account is missing or stale. This protects users from receiving the wrong advice, such as being shown tool slugs when the real fix is reconnecting their account.

**Data flow**: It receives a Composio error and the connected account ID that was used. It lowercases the error body and looks for narrow signs that Composio could not find the connected account, either by phrase or by the specific account ID. It returns true if the error matches that stale-account pattern, otherwise false.

**Call relations**: ComposioBroker.execute calls this when a tool execution fails. If it returns true, execute switches to _reconnect_error so the failure tells the member to reconnect rather than treating the problem as a missing tool.

*Call graph*: called by 1 (execute).


##### `_reconnect_error`  (lines 191–192)

```
def _reconnect_error(error: composio.ComposioError, provider: str) -> composio.ComposioError
```

**Purpose**: Builds an error message that explains a stale or missing connected account and adds guidance to reconnect. It preserves the original Composio status while making the next action clearer.

**Data flow**: It receives the original Composio error and provider name. It appends provider-specific reconnect guidance to the original error body and returns a new ComposioError with the same status code.

**Call relations**: ComposioBroker.execute uses this after _stale_account identifies the account problem. It relies on stale_grant_guidance to produce the human-facing instruction for the affected provider.

*Call graph*: called by 1 (execute); 2 external calls (stale_grant_guidance, ComposioError).


##### `_discovered_tools`  (lines 195–216)

```
def _discovered_tools(rows: tuple[dict[str, object], ...]) -> tuple[BrokerTool, ...]
```

**Purpose**: Converts raw Composio tool-list rows into UFO’s BrokerTool objects. This gives the rest of the system clean, consistent tool records without needing to understand Composio’s exact catalog format.

**Data flow**: It receives a tuple of dictionaries from Composio. For each row, it chooses a usable slug, trims long descriptions, translates input parameters into UFO’s workspace-file-aware schema, checks whether the tool is read-only, and skips rows without a valid slug. It returns a tuple of BrokerTool objects.

**Call relations**: ComposioBroker.tools uses this for normal discovery, and ComposioBroker._slug_miss uses it while preparing better error messages. It calls _read_only for tag interpretation and workspace_file_schema for file-input translation.

*Call graph*: calls 1 internal fn (_read_only); called by 2 (_slug_miss, tools); 2 external calls (__init__, workspace_file_schema).


##### `_read_only`  (lines 219–221)

```
def _read_only(payload: dict[str, object]) -> bool
```

**Purpose**: Checks whether Composio marked a tool as read-only, meaning it should not change outside data. This hint helps callers reason about tool safety.

**Data flow**: It receives one tool payload dictionary. It looks at the payload’s tags and returns true only if the tags are a list containing the read-only marker. Otherwise it returns false.

**Call relations**: ComposioBroker.schema uses this when building the detailed BrokerTool for one slug, and _discovered_tools uses it while converting listed tools. It is the small shared rule for interpreting Composio’s read-only tag.

*Call graph*: called by 2 (schema, _discovered_tools).


### `extensions/composio/ufo_ext_composio/mcp_session.py`

`io_transport` · `request handling`

This file is a small bridge between this project and Composio's Tool Router. The Tool Router speaks MCP, which means “Model Context Protocol”, a standard way for software to expose tools to AI agents. In everyday terms, this file opens a short-lived conversation with a remote tool-search service, asks one question, then closes the conversation.

The main job is to call a named MCP tool, such as Composio's semantic tool search tool, over streamable HTTP. “Streamable HTTP” means the response can arrive over an HTTP connection in a way that supports MCP's message format, rather than as a simple one-shot web response.

After the call returns, the file carefully normalizes the result. If the MCP library already parsed the response into structured data, it returns that. If not, it looks for a text block and tries to read it as JSON, which is a common format for structured data. If even that is not possible, it still returns something useful, such as the raw text or a generic result wrapper.

This matters because the rest of the system wants a simple dictionary, not several possible MCP response shapes. Without this file, every caller would need to know the details of MCP response formats and network session setup.

#### Function details

##### `mcp_call_tool`  (lines 18–42)

```
async def mcp_call_tool(endpoint: str, tool: str, arguments: dict[str, Any], headers: dict[str, str], timeout_seconds: float) -> dict[str, object]
```

**Purpose**: Opens a temporary MCP-over-HTTP session, calls one remote tool, and returns the tool's answer as a plain dictionary. It gives the rest of the project a simple, predictable result even when the MCP response comes back in different shapes.

**Data flow**: It receives an endpoint URL, the tool name, input arguments, HTTP headers, and a timeout. It builds an HTTP MCP transport, opens a client session, sends the tool call, and waits for the result. Then it checks the returned data in order: already-parsed dictionary data, structured content, JSON stored inside a text block, plain text, or finally a generic result value. The output is always a dictionary, and the network session is closed after the call.

**Call relations**: When another part of the Composio extension needs to ask the Tool Router for information, it calls this function instead of dealing with MCP session details itself. Inside the function, it hands the endpoint and headers to FastMCP's streamable HTTP transport, uses a FastMCP client to make the remote tool call, and uses JSON parsing only if the response arrives as text that may contain structured data.

*Call graph*: 3 external calls (Client, StreamableHttpTransport, loads).


### `extensions/composio/ufo_ext_composio/proxy.py`

`io_transport` · `request handling and teardown`

Some connected services, such as Google or other providers, require private credentials. In this setup, Composio keeps those credentials and does not hand them to this project. This file is the bridge that makes that safe arrangement still feel normal to the rest of the code.

The main piece is `ComposioProxyTransport`, an `httpx` transport. A transport is the low-level part of an HTTP client that actually sends requests. Instead of sending a request straight to the provider, this transport reads the original request, packages its URL, method, safe headers, body, and connected-account ID into a JSON payload, and sends that to Composio's proxy endpoint. Composio then adds the real credential on its own server and calls the provider.

When Composio replies, the file rebuilds an ordinary HTTP response with the provider's status code, headers, and body. That matters because other code can still use normal HTTP behavior, such as pagination information stored in response headers. If the provider returned binary data, Composio stores it elsewhere and returns a temporary download URL; this transport represents that as a redirect, like a signpost telling the caller where to fetch the large file.

#### Function details

##### `ComposioProxyTransport.handle_async_request`  (lines 61–97)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: This is the main request path. It receives a normal provider request and sends an equivalent request to Composio's proxy endpoint so Composio can add the hidden credential server-side.

**Data flow**: It starts with an incoming `httpx.Request` meant for the provider. It reads the request body, copies the method and full URL, keeps only headers that are safe and useful to forward, adds the connected account ID, and sends all of that as JSON to Composio. If Composio itself reports an error, it returns that error as the response. Otherwise, it passes Composio's payload to `_provider_response` and returns the rebuilt provider-style response.

**Call relations**: This function is called by `httpx` whenever the bound client needs to send a request. It creates a new `httpx.Request` aimed at Composio, uses the inner transport to send it, and then hands successful proxy results to `ComposioProxyTransport._provider_response` so the rest of the application can keep behaving as if it talked directly to the provider.

*Call graph*: calls 1 internal fn (_provider_response); 4 external calls (Request, aread, Response, loads).


##### `ComposioProxyTransport._provider_response`  (lines 99–142)

```
def _provider_response(self, payload: dict[str, Any], request: httpx.Request) -> httpx.Response
```

**Purpose**: This turns Composio's proxy result back into a normal-looking provider response. It hides Composio's wrapper format from the caller and preserves the provider details the caller needs, such as status, headers, and body.

**Data flow**: It takes the decoded JSON payload from Composio and the original provider request. It unwraps nested `data` envelopes until it reaches the actual provider result, extracts the status code, filters out body-related headers that would no longer be reliable, and builds the response body. If Composio says the provider result was binary data, it returns a redirect response pointing to the temporary file URL instead of trying to load those bytes into memory.

**Call relations**: It is used by `ComposioProxyTransport.handle_async_request` after the proxy call succeeds. It may create a normal `httpx.Response`, serialize JSON body data when needed, or raise `ComposioError` if Composio reports binary data without the URL needed to fetch it.

*Call graph*: called by 1 (handle_async_request); 4 external calls (Response, dumps, cast, ComposioError).


##### `ComposioProxyTransport.aclose`  (lines 144–145)

```
async def aclose(self) -> None
```

**Purpose**: This closes the underlying transport when the client is done. It is the cleanup step that releases any network resources held underneath.

**Data flow**: It receives no new data. It simply forwards the close request to the inner transport, which performs the actual shutdown. Nothing is returned except completion of the asynchronous cleanup.

**Call relations**: This is called during HTTP client teardown. It does not participate in rewriting requests; it makes sure the wrapped transport is also closed instead of being left open.


### Pipedream integration
Bridges UFO connector actions to Pipedream discovery, authentication, execution, and token-safe proxying.

### `extensions/pipedream/ufo_ext_pipedream/broker.py`

`domain_logic` · `request handling`

Pipedream offers many ready-made actions, such as sending an email or creating a record in another service. This file turns those Pipedream actions into a form the rest of UFO can safely use. Think of it as a concierge: the agent asks what tools are available, what information a tool needs, or asks to run one, and the broker translates that request into Pipedream’s language.

The broker is deliberately stateless. It asks for a fresh Pipedream client each time, so tests can swap in a fake transport and long-running connections do not leak. It can list actions for a provider, build a plain input schema from Pipedream’s configurable fields, and hide internal fields that the agent should not fill in itself. One important hidden field is the connected-account slot: when an action runs, the broker fills that slot with the chosen account’s authorization ID.

It also protects users from confusing failures. If an action key is unknown, it suggests nearby real action keys. If Pipedream says the connected account is stale or missing, it turns that into guidance to reconnect. For files, it reads Pipedream’s File Stash output URLs so the sandbox can download produced files. It refuses staged uploads because Pipedream actions expect file inputs as URLs instead.

#### Function details

##### `PipedreamBroker.tools`  (lines 62–64)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Finds Pipedream actions for one connector provider and presents them as UFO broker tools. Someone uses this when the agent needs to discover what actions are available for an app.

**Data flow**: It receives a workspace ID, a provider name, and a search query. It looks up the provider’s Pipedream app slug, asks the Pipedream client for matching actions, converts those raw action rows into broker-friendly tool records, and returns them as a tuple.

**Call relations**: This is the main discovery path for Pipedream tools. The search method calls it when a broader broker search is requested, and it hands the raw Pipedream catalog data to _listed_tools so the rest of UFO sees a consistent tool shape.

*Call graph*: calls 2 internal fn (_listed_tools, _spec); called by 1 (search); 1 external calls (pipedream_client).


##### `PipedreamBroker.schema`  (lines 66–73)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Returns the detailed shape of the inputs needed by one Pipedream action. This helps the agent know what arguments it can provide before trying to run the action.

**Data flow**: It receives a workspace ID, provider, and action slug. It fetches the action definition, extracts its configurable fields, turns them into a simple JSON schema, reads its description and read-only hint, and returns a BrokerTool describing that action.

**Call relations**: This is used when the system needs a more exact description than a catalog listing gives. It relies on _definition to fetch the action, then uses helper functions to clean and translate Pipedream’s fields into UFO’s broker-tool format.

*Call graph*: calls 5 internal fn (_definition, _input_schema, _props, _read_only, _str); 1 external calls (__init__).


##### `PipedreamBroker.execute`  (lines 75–110)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs a Pipedream action using a specific connected account. It is the core path that turns an agent’s chosen tool call into a real server-side Pipedream run.

**Data flow**: It receives the workspace, provider, action slug, user-supplied arguments, account ID, and optional idempotency key. It fetches the action definition, adds the account binding into the action’s hidden app slot, checks that the account belongs to this workspace and app, asks Pipedream to run the action, and returns the response. If the action is missing, the account is stale, or Pipedream reports an action error, it raises a clearer error instead of returning a misleading result.

**Call relations**: This sits at the center of action execution. It calls _definition to learn how the action is built, _app_slot to know where to attach the account, _spec to verify the provider, _key_miss when a slug is unknown, and the stale-account helpers when an error means the user must reconnect.

*Call graph*: calls 7 internal fn (_definition, _key_miss, _app_slot, _reconnect_error, _spec, _stale_account, __init__); 2 external calls (dumps, pipedream_client).


##### `PipedreamBroker.file_outputs`  (lines 112–130)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Extracts files produced by a Pipedream action and turns them into downloadable broker file records. This lets the sandbox fetch files that the action saved during its run.

**Data flow**: It receives the action response dictionary. It looks inside the response exports for File Stash upload entries, keeps entries that contain a usable download URL, derives a filename from the local path when possible, and returns BrokerFile objects.

**Call relations**: This runs after an action response is available. It does not call back into Pipedream; it simply interprets the response format and hands the rest of UFO clean file records with names and URLs.

*Call graph*: 2 external calls (__init__, PurePosixPath).


##### `PipedreamBroker.stage_upload`  (lines 132–144)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Rejects staged file uploads for Pipedream actions. It exists to make the expected file flow explicit: Pipedream wants file inputs as URLs, not as pre-uploaded blobs.

**Data flow**: It receives workspace, provider, action slug, filename, MIME type, and checksum information. Instead of creating an upload destination, it raises an error explaining that the caller should share the workspace file and pass the resulting download URL.

**Call relations**: This is the negative side of the file-input story. Callers that use the generic connector interface may ask to stage a file, and this method stops that path early with instructions suited to Pipedream.


##### `PipedreamBroker.search`  (lines 146–147)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Wraps tool discovery in the broker’s search result format. Pipedream does not provide a separate planning router here, so the result is simply the matching tools.

**Data flow**: It receives workspace, provider, and query. It asks tools for the matching action list, places that list into a BrokerSearch object, and returns it.

**Call relations**: This is a thin wrapper around PipedreamBroker.tools. It is used when the generic connector layer expects a search-style response rather than just a tuple of tools.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `PipedreamBroker.credential`  (lines 149–170)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Builds a credential object that can send proxied requests through Pipedream for a connected account. It also verifies that the account really belongs to the requested workspace and provider app.

**Data flow**: It receives a workspace ID, provider, and account ID. It looks up the provider spec, fetches the connected account from Pipedream, turns a missing stale grant into reconnect guidance, checks that the account authenticates the expected app, and returns a Credential containing a Pipedream proxy transport.

**Call relations**: This is used when code needs an authenticated transport rather than a one-shot action run. It depends on _spec for provider validation and constructs PipedreamProxyTransport so later HTTP requests can travel through the correct Pipedream-connected account.

*Call graph*: calls 3 internal fn (__init__, _spec, __init__); 5 external calls (__init__, __init__, AsyncHTTPTransport, stale_grant_guidance, pipedream_client).


##### `PipedreamBroker._definition`  (lines 172–180)

```
async def _definition(self, slug: str) -> dict[str, object]
```

**Purpose**: Fetches the full Pipedream definition for one action slug. It translates Pipedream’s not-found response into UFO’s standard “unknown tool” signal.

**Data flow**: It receives an action slug. It asks the Pipedream client for that action’s definition, raises UnknownBrokerTool if Pipedream says it does not exist, and otherwise returns the definition dictionary, unwrapping a top-level data field when present.

**Call relations**: Both schema and execute call this before they can describe or run an action. It isolates the details of Pipedream’s definition response so those higher-level methods can work with a normal dictionary.

*Call graph*: called by 2 (execute, schema); 2 external calls (__init__, pipedream_client).


##### `PipedreamBroker._key_miss`  (lines 182–201)

```
async def _key_miss(self, client: pipedream.PipedreamClient, provider: str, slug: str) -> PipedreamError
```

**Purpose**: Creates a helpful error when an action slug is not known. Instead of just saying “not found,” it tries to suggest nearby real action keys for the same app.

**Data flow**: It receives a Pipedream client, provider, and missing slug. It looks up the provider’s app, fetches that app’s action list if possible, compares the missing slug to real slugs, and returns a PipedreamError containing either close matches or advice to search the catalog.

**Call relations**: PipedreamBroker.execute calls this when _definition says the requested action is unknown. It uses _listed_tools to normalize catalog rows before comparing names, so its suggestions match the same slugs the agent would see during discovery.

*Call graph*: calls 4 internal fn (_listed_tools, _spec, list_actions, __init__); called by 1 (execute); 1 external calls (get_close_matches).


##### `_stale_account`  (lines 204–211)

```
def _stale_account(error: PipedreamError, account_id: str) -> bool
```

**Purpose**: Decides whether a Pipedream error likely means the connected account is no longer usable. This prevents old or deleted grants from looking like ordinary action failures.

**Data flow**: It receives a Pipedream error and the account ID that was being used. It lowercases the error body and checks for narrow phrases such as “external user not found” or the account ID appearing with “not found,” then returns true or false.

**Call relations**: PipedreamBroker.execute calls this after run-related errors. If it returns true, execute passes the error to _reconnect_error so the user gets reconnect guidance instead of a vague Pipedream failure.

*Call graph*: called by 1 (execute).


##### `_reconnect_error`  (lines 214–215)

```
def _reconnect_error(error: PipedreamError, provider: str) -> PipedreamError
```

**Purpose**: Adds reconnect instructions to a Pipedream error. It keeps the original status and message, but makes the next action clear for the user or agent.

**Data flow**: It receives a Pipedream error and provider name. It appends standard stale-grant guidance for that provider to the error body and returns a new PipedreamError with the same status.

**Call relations**: PipedreamBroker.execute uses this when _stale_account identifies a missing or outdated connected account. It centralizes the wording so stale-grant failures are explained consistently.

*Call graph*: calls 1 internal fn (__init__); called by 1 (execute); 1 external calls (stale_grant_guidance).


##### `_spec`  (lines 218–222)

```
def _spec(provider: str) -> ConnectorSpec
```

**Purpose**: Looks up the registered Pipedream connector specification for a provider name. This connects UFO’s provider name to Pipedream’s app slug.

**Data flow**: It receives a provider string. It searches the Pipedream connector registry, returns the matching ConnectorSpec if found, and raises a KeyError if this provider was not registered for Pipedream.

**Call relations**: Discovery, execution, credential creation, and unknown-key help all call this before speaking to Pipedream for a provider. It is the small gatekeeper that prevents using an unregistered provider name.

*Call graph*: called by 4 (_key_miss, credential, execute, tools).


##### `_listed_tools`  (lines 225–242)

```
def _listed_tools(rows: tuple[dict[str, object], ...]) -> tuple[BrokerTool, ...]
```

**Purpose**: Converts Pipedream catalog rows into UFO BrokerTool records. This gives the rest of the system clean tool names, descriptions, input schemas, and read-only hints.

**Data flow**: It receives raw action rows from Pipedream. It skips rows without a usable action key, extracts descriptions and configurable fields, builds an input schema, notes whether the action is read-only, and returns a tuple of BrokerTool objects.

**Call relations**: PipedreamBroker.tools uses this for normal catalog discovery, and _key_miss uses it while building suggestions. It delegates the smaller translation steps to _props, _input_schema, _read_only, and _str.

*Call graph*: calls 4 internal fn (_input_schema, _props, _read_only, _str); called by 2 (_key_miss, tools); 1 external calls (__init__).


##### `_read_only`  (lines 245–247)

```
def _read_only(definition: dict[str, object]) -> bool
```

**Purpose**: Checks whether a Pipedream action is marked as read-only. A read-only action is one that should not change outside data, such as a lookup rather than a write.

**Data flow**: It receives an action definition dictionary. It looks for an annotations dictionary and returns true only when the readOnlyHint flag is exactly true.

**Call relations**: PipedreamBroker.schema and _listed_tools call this while building BrokerTool objects. Its result helps the agent understand whether a tool is likely safe to use for information gathering.

*Call graph*: called by 2 (schema, _listed_tools).


##### `_props`  (lines 250–252)

```
def _props(definition: dict[str, object]) -> list[dict[str, object]]
```

**Purpose**: Safely extracts the configurable property list from a Pipedream action definition. These properties describe the inputs an action can accept.

**Data flow**: It receives an action definition dictionary. If configurable_props is a list, it keeps only entries that are dictionaries; otherwise it returns an empty list.

**Call relations**: Schema building, catalog conversion, and account-slot lookup all call this. It shields those callers from malformed or unexpected Pipedream data by giving them a clean list to inspect.

*Call graph*: called by 3 (schema, _app_slot, _listed_tools).


##### `_app_slot`  (lines 255–262)

```
def _app_slot(definition: dict[str, object], slug: str) -> str
```

**Purpose**: Finds the hidden action field where the connected account must be inserted. Without this slot, the broker cannot run the action as the user’s granted account.

**Data flow**: It receives an action definition and slug. It scans the configurable properties for the special Pipedream app property, returns that property’s name, and raises a PipedreamError if no usable app slot exists.

**Call relations**: PipedreamBroker.execute calls this just before running an action. It tells execute exactly where to place the account authorization object inside the arguments sent to Pipedream.

*Call graph*: calls 2 internal fn (_props, __init__); called by 1 (execute).


##### `_input_schema`  (lines 265–288)

```
def _input_schema(props: list[dict[str, object]]) -> dict[str, object]
```

**Purpose**: Builds a simple JSON schema for the action inputs the agent is allowed to provide. JSON schema is a standard way to describe the names and types of fields in an object.

**Data flow**: It receives a list of Pipedream configurable properties. It skips internal fields such as the connected-account slot and service-only paths, converts known Pipedream field types into JSON types, copies helpful descriptions, records required fields, and returns an object schema.

**Call relations**: PipedreamBroker.schema and _listed_tools call this when presenting actions to the rest of UFO. It uses _str to safely read property types and keeps broker-owned fields away from the agent.

*Call graph*: calls 1 internal fn (_str); called by 2 (schema, _listed_tools).


##### `_str`  (lines 291–292)

```
def _str(value: object) -> str
```

**Purpose**: Returns a value only if it is already a string, otherwise returns an empty string. This is a small safety helper for messy external data.

**Data flow**: It receives any value. If the value is a string, it returns that string; if not, it returns an empty string.

**Call relations**: Schema building, tool listing, and detailed schema creation use this when reading descriptions and field types from Pipedream responses. It keeps unexpected non-string values from leaking into broker tool descriptions or schema types.

*Call graph*: called by 3 (schema, _input_schema, _listed_tools).


### `extensions/pipedream/ufo_ext_pipedream/proxy.py`

`io_transport` · `request handling and transport teardown`

Many integrations need to call outside services, such as Gmail or another API, using a user's account credential. In this setup, Pipedream owns that credential and does not reveal it to this project. This file solves that problem by acting like a postal forwarding service: code can address a request to the real provider, but this transport repackages it and sends it to Pipedream, which adds the private credential on the server side and forwards the request onward.

The main class, PipedreamProxyTransport, plugs into httpx, a Python HTTP client library. When a request is about to be sent, it reads the original request body, asks the Pipedream client for an access token, and builds a new request to Pipedream's proxy endpoint. The original provider URL is encoded into the proxy URL, and the Pipedream account and external user are added as query values so Pipedream knows which connected account to use.

Headers need special care. Pipedream only forwards headers that start with x-pd-proxy-, so this file prefixes useful provider headers before sending them. It also drops transport-level headers such as host, content-length, and authorization, because those either belong to the proxy request itself or could be unsafe to forward. The response is not interpreted or softened; status code, body, and headers come back as the provider sent them, so higher-level connector code can still rely on provider-specific meanings like “not found” or “unauthorized.”

#### Function details

##### `PipedreamProxyTransport.handle_async_request`  (lines 55–74)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: This function is called whenever httpx wants to send a request through this custom transport. It rewrites an ordinary provider request into a Pipedream Connect Proxy request so Pipedream can add the user's hidden credential before forwarding it.

**Data flow**: It starts with an httpx request aimed at the real provider. It reads the request body, gets a Pipedream access token, copies only safe and useful headers with the x-pd-proxy- prefix Pipedream expects, encodes the original URL into the proxy path, and adds the external user ID and account ID to the query string. It then creates a new request aimed at Pipedream and sends it through the inner transport. The returned response is the proxy's response, which represents the provider's response and is passed back unchanged.

**Call relations**: httpx calls this method as the transport hook when a connector sends an HTTP request. Inside, it relies on the Pipedream client for an access token, uses base64.urlsafe_b64encode to safely place the original URL inside the proxy path, builds the proxy URL and request with httpx.URL and httpx.Request, then hands the finished proxy request to the wrapped inner transport to actually send it.

*Call graph*: 4 external calls (urlsafe_b64encode, Request, aread, URL).


##### `PipedreamProxyTransport.aclose`  (lines 76–77)

```
async def aclose(self) -> None
```

**Purpose**: This function shuts down the wrapped HTTP transport when the proxy transport is no longer needed. It makes sure any network resources owned by the inner transport are cleaned up properly.

**Data flow**: It receives no new data. It simply asks the inner transport to close itself, which may release open connections or other network resources. Nothing is returned.

**Call relations**: httpx or surrounding cleanup code calls this when the client is being closed. Rather than doing its own cleanup, this proxy transport passes the close request down to the inner transport that performed the actual network work.


### Connector tool surfaces
Exposes live, deterministic, and workspace-configured connector tool packs to agents through safe discovery, inspection, execution, and file-transfer paths.

### `extensions/connectors/ufo_ext_connectors/tools.py`

`io_transport` · `request handling`

A single connector broker can represent many outside services and thousands of possible actions. Instead of hard-coding one tool per service, this file exposes a small set of general tools: list available connectors, discover a connector's real tools, search for tools by goal, and call one selected tool. Think of it like a hotel concierge desk: the agent does not memorize every restaurant and taxi company; it asks the concierge what exists, gets the right form, then sends the request through the concierge.

When a tool is called, this file is careful about boundaries. If the agent passes a workspace file, the file is uploaded from inside the sandbox to the broker's storage, and the broker receives a safe reference instead of raw bytes. If the external tool returns files, they are downloaded into a dedicated workspace folder. If a provider returns base64 text, which is an encoded form often used for file contents, the code decodes small readable text inline and stores large or binary data as workspace files.

It also adds a visible “Sent using ufo” footer to Slack messages sent through connectors, so text written by this system is clearly marked. Finally, it shrinks repetitive JSON results by replacing repeated objects with pointers to the first copy, keeping useful results in the agent's immediate context instead of forcing them into a separate file.

#### Function details

##### `list_external_tools`  (lines 246–282)

```
async def list_external_tools(ctx: ToolContext, args: ListExternalToolsInput) -> ToolResult
```

**Purpose**: Finds external connector services that are available for this turn, such as Slack or GitHub. It returns matching connectors and shows which connected accounts the agent can already use.

**Data flow**: It receives the current tool context and search queries. It reads the connector registry, reads active connection grants, matches query words against local connector names and broker catalog results, then returns a JSON tool result containing connector IDs, labels, and connected accounts.

**Call relations**: This is one of the public tool handlers registered at the bottom of the file. It asks _registry for the live connector registry, asks _connected_accounts for usable accounts, uses the registry's catalog search in parallel, and wraps the final answer through _json_result.

*Call graph*: calls 3 internal fn (_connected_accounts, _json_result, _registry); 1 external calls (gather).


##### `_connected_accounts`  (lines 285–299)

```
async def _connected_accounts(ctx: ToolContext) -> dict[str, list[JsonValue]]
```

**Purpose**: Builds a simple map of which accounts are already connected for each provider. This lets discovery answers say not only that Slack exists, but also whose Slack account can be used.

**Data flow**: It receives the tool context. If there are no grants, it returns an empty map; otherwise it reads active grants and groups account ID, owner email, and sharing status by provider.

**Call relations**: list_external_tools calls this while preparing connector search results. Its output is folded into each connector row so the caller can choose an account later if needed.

*Call graph*: called by 1 (list_external_tools).


##### `describe_external_tools`  (lines 302–324)

```
async def describe_external_tools(ctx: ToolContext, args: DescribeExternalToolsInput) -> ToolResult
```

**Purpose**: Describes real tools inside one connector and fetches their input schemas, which are the forms the agent must fill out before calling them. It also helps recover from guessed or wrong tool names by showing nearby real tools.

**Data flow**: It receives a connector source ID, optional exact tool names, and an optional discovery query. It looks up the connector, asks the broker for schemas for exact names, collects unresolved names, optionally searches or lists available tools, and returns all of that as JSON.

**Call relations**: This is a public discovery tool. It uses _registry to find the connector, _tool_json to simplify broker tool objects, _discovery_query to turn missed names into search words, _discovered_rows to produce a bounded tool list, and _json_result to return the answer.

*Call graph*: calls 5 internal fn (_discovered_rows, _discovery_query, _json_result, _registry, _tool_json).


##### `attribution_stripped`  (lines 327–331)

```
def attribution_stripped(text: str) -> str
```

**Purpose**: Removes ufo attribution text from a Slack message before deciding what a human actually wrote. This prevents the system's own footer from being mistaken for user intent, such as a mention.

**Data flow**: It receives message text. It removes any matching attribution phrase wherever it appears and returns the cleaned text.

**Call relations**: This helper is available for inbound Slack-style reads. It is separate from sending logic: outbound code adds attribution, while this function cleans it back out when reading messages.


##### `attributed_arguments`  (lines 334–361)

```
def attributed_arguments(arguments: dict[str, JsonValue], subject: str) -> dict[str, JsonValue]
```

**Purpose**: Adds a “Sent using ufo” footer to Slack send arguments when there is an actual message body. It avoids adding a second footer if one is already present.

**Data flow**: It receives the outgoing argument dictionary and the footer subject text. It checks for an existing attribution, builds a Slack context block, appends it to existing blocks when possible, or converts text or markdown arguments into blocks with the footer after them; if no message body can be found, it returns the original arguments.

**Call relations**: slack_attributed calls this only for Slack message-send style tools. It relies on _carries_attribution to avoid duplicates, _appended_blocks to add to existing block payloads, and _body_blocks to turn plain text or markdown into Slack blocks.

*Call graph*: calls 3 internal fn (_appended_blocks, _body_blocks, _carries_attribution); called by 1 (slack_attributed).


##### `_body_blocks`  (lines 364–392)

```
def _body_blocks(arguments: dict[str, JsonValue]) -> list[JsonValue] | None
```

**Purpose**: Turns a Slack message body written as plain text or markdown into Slack block objects so a footer block can be added after it. It preserves the intended markup style instead of mixing Slack's different text formats.

**Data flow**: It receives the outgoing argument dictionary. If there is non-empty markdown_text, it makes one markdown block; if there is non-empty text, it splits it into section blocks that fit Slack's per-section length limit; otherwise it returns nothing.

**Call relations**: attributed_arguments calls this when the send does not already provide blocks. The returned blocks become the body that the attribution footer follows.

*Call graph*: called by 1 (attributed_arguments).


##### `_appended_blocks`  (lines 395–416)

```
def _appended_blocks(value: JsonValue, footer: dict[str, JsonValue]) -> JsonValue | None
```

**Purpose**: Adds the footer block to an existing Slack blocks argument when that argument is usable. It supports both real lists and JSON strings because connector schemas may accept either form.

**Data flow**: It receives a blocks value and a footer block. If the value is a non-empty list, it returns a new list with the footer appended; if it is a JSON string, it parses it, checks it, appends the footer, and serializes it back in the same style; otherwise it returns nothing to signal that it should be left alone.

**Call relations**: attributed_arguments calls this before trying to build blocks from text. It uses _carries_attribution to avoid stacking footers and JSON/URL encoding helpers to preserve the input shape.

*Call graph*: calls 1 internal fn (_carries_attribution); called by 1 (attributed_arguments); 4 external calls (dumps, loads, quote, unquote).


##### `_carries_attribution`  (lines 419–428)

```
def _carries_attribution(value: JsonValue) -> bool
```

**Purpose**: Checks whether a value already contains a ufo attribution line. This is the guard that prevents repeated sends or edits from accumulating multiple footers.

**Data flow**: It receives any JSON-like value. It searches strings directly, recursively scans lists and dictionaries, and returns true if any nested value contains the attribution.

**Call relations**: attributed_arguments uses it before adding any footer, and _appended_blocks uses it after parsing serialized blocks. It is the shared duplicate-detection step for Slack attribution.

*Call graph*: called by 2 (_appended_blocks, attributed_arguments); 1 external calls (values).


##### `slack_attributed`  (lines 431–445)

```
def slack_attributed(provider: str, slug: str, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Decides whether an external tool call is a Slack message send and, if so, adds the ufo attribution footer. Non-Slack calls, reads, edits, listings, and unrelated Slack tools are left untouched.

**Data flow**: It receives a provider name, a tool slug, and argument data. It checks that the provider is Slack and that the tool name looks like a message send/post/reply/schedule action, then either returns the original arguments or the attributed version.

**Call relations**: call_external_tool calls this just before executing a connector tool. If attribution is needed, it hands off to attributed_arguments.

*Call graph*: calls 1 internal fn (attributed_arguments); called by 1 (call_external_tool).


##### `call_external_tool`  (lines 448–465)

```
async def call_external_tool(ctx: ToolContext, args: CallExternalToolInput) -> ToolResult
```

**Purpose**: Runs one selected external connector tool through the broker. It also enforces read-only mode, chooses the connected account, applies Slack attribution, and starts the safe file/result processing flow.

**Data flow**: It receives the tool context plus source ID, tool name, account ID, and tool arguments. It looks up the connector, optionally refuses write tools in read-only mode, gets the account connection, adjusts Slack sends, creates a _ConnectorCall, and returns the execution result as tool text.

**Call relations**: This is the public execution tool. It uses _registry for connector lookup, ToolContext.connector_connection for account selection, slack_attributed for Slack sends, and _ConnectorCall.run for the full broker execution and result cleanup.

*Call graph*: calls 3 internal fn (connector_connection, _registry, slack_attributed); 4 external calls (__init__, __init__, __init__, __init__).


##### `_ConnectorCall.run`  (lines 493–507)

```
async def run(self, arguments: dict[str, JsonValue], connection: ConnectorConnection) -> str
```

**Purpose**: Performs a connector tool execution from start to finish. It stages input files, runs the broker call, fetches output files, decodes embedded data, and shrinks repeated result objects.

**Data flow**: It receives already prepared arguments and a connector connection. It replaces workspace file references with broker upload references, verifies the connection, calls the broker's execute API, downloads returned files, translates base64-like result data, adds workspace file listings, deduplicates repeated objects, and returns a JSON string.

**Call relations**: call_external_tool creates a _ConnectorCall and calls this method. It delegates file input work to _staged_value, file output work to _fetched_files, result cleanup to _translated_node, and runs _deduped in a worker thread so the main event loop is not blocked.

*Call graph*: calls 3 internal fn (_fetched_files, _staged_value, _translated_node); 1 external calls (to_thread).


##### `_ConnectorCall._staged_value`  (lines 509–524)

```
async def _staged_value(self, value: object) -> object
```

**Purpose**: Walks through tool arguments and replaces any workspace file marker with a broker-ready file reference. This lets tools accept files without sending raw file bytes through the main server process.

**Data flow**: It receives any argument value. If it finds exactly {"workspace_file": path}, it validates the path string and stages that file; if it sees a dictionary or list, it recurses into it; all other values pass through unchanged.

**Call relations**: _ConnectorCall.run calls this for every top-level argument before the broker execute request. When it finds a file marker, it hands off to _stage_file.

*Call graph*: calls 1 internal fn (_stage_file); called by 1 (run).


##### `_ConnectorCall._stage_file`  (lines 526–562)

```
async def _stage_file(self, path: str) -> dict[str, object]
```

**Purpose**: Uploads one workspace file to the broker's file store in a controlled way. It checks size, guesses a content type, asks the broker for an upload URL, and performs the upload from inside the sandbox.

**Data flow**: It receives a workspace path. It converts it to a scoped workspace path, runs a sandboxed preflight script to hash and measure the file, rejects unreadable or too-large files, asks the broker to stage the upload, optionally uses curl in the sandbox to PUT the bytes, and returns the broker's argument value for that uploaded file.

**Call relations**: _staged_value calls this whenever it sees a workspace_file argument. It relies on sandbox execution, broker stage_upload, and shell quoting helpers to keep path and URL handling safe.

*Call graph*: called by 1 (_staged_value); 4 external calls (guess_type, PurePosixPath, quote, workspace_path).


##### `_ConnectorCall._fetched_files`  (lines 564–597)

```
async def _fetched_files(self, files: tuple[BrokerFile, ...]) -> list[dict[str, str]]
```

**Purpose**: Downloads files produced by a connector tool into the workspace. It gives each returned file a safe name and a fresh folder so one download cannot overwrite another.

**Data flow**: It receives broker file records with names and presigned URLs. For each one, it reduces the provider's filename to a safe leaf name, claims a target path inside /workspace/connector_files, downloads the file there from inside the sandbox with a size limit, and returns a list of saved file names and workspace paths.

**Call relations**: _ConnectorCall.run calls this after broker execution, using the broker's file_outputs result. It uses sandbox scripts and curl so file bytes travel between sandbox and broker storage rather than through the serve process.

*Call graph*: called by 1 (run); 3 external calls (quote, contained_leaf, uuid4).


##### `_ConnectorCall._translated_node`  (lines 599–659)

```
async def _translated_node(self, node: Mapping[str, object], depth: int=0) -> dict[str, object]
```

**Purpose**: Scans one result object for provider-marked base64 fields and turns them into readable text or workspace file references. This prevents giant encoded file contents from filling the agent's context.

**Data flow**: It receives a mapping from the tool result. It first recursively translates child values, then looks for fields such as encoding: base64 together with content/data/body strings, decodes valid base64 fields, chooses a filename and MIME type, replaces decoded fields with text or file references, and updates the encoding marker when every marked field was translated.

**Call relations**: _ConnectorCall.run starts result translation here, and _translated calls it for nested objects. It uses _decoded_base64 for safe decoding, _translated_bytes to decide inline versus file, and _translated for recursive walking.

*Call graph*: calls 3 internal fn (_translated, _translated_bytes, _decoded_base64); called by 2 (_translated, run); 1 external calls (guess_type).


##### `_ConnectorCall._translated`  (lines 661–680)

```
async def _translated(self, value: object, depth: int) -> object
```

**Purpose**: Recursively translates nested result values. It handles dictionaries, lists, and data URLs while leaving unsupported or overly deep structures unchanged.

**Data flow**: It receives any result value and the current nesting depth. It stops translating when the depth limit is reached; otherwise it sends dictionaries to _translated_node, lists through itself item by item, data:...base64 strings to _translated_data_url, and returns all other values unchanged.

**Call relations**: _translated_node calls this for child values, forming the recursive walk over broker output. It hands object work back to _translated_node and special string work to _translated_data_url.

*Call graph*: calls 2 internal fn (_translated_data_url, _translated_node); called by 1 (_translated_node).


##### `_ConnectorCall._translated_data_url`  (lines 682–694)

```
async def _translated_data_url(self, value: str) -> object
```

**Purpose**: Translates a data URL that embeds base64 bytes directly in a string. Data URLs are strings like data:image/png;base64,... that carry both a content type and encoded bytes.

**Data flow**: It receives a string. If the string matches the expected data URL shape and contains valid base64, it decodes the bytes, chooses a fallback filename based on the MIME type, and sends the bytes to _translated_bytes; otherwise it returns the original string.

**Call relations**: _translated calls this when it sees a data: string within the allowed size. It uses _decoded_base64 for validation and decoding, then _translated_bytes for the inline-or-file decision.

*Call graph*: calls 2 internal fn (_translated_bytes, _decoded_base64); called by 1 (_translated); 1 external calls (guess_extension).


##### `_ConnectorCall._translated_bytes`  (lines 696–705)

```
async def _translated_bytes(self, decoded: bytes, text: str | None, name: str, mimetype: str) -> object
```

**Purpose**: Decides how decoded bytes should appear in the final result. Small UTF-8 text stays readable inline; large text or binary data becomes a workspace file reference.

**Data flow**: It receives decoded bytes, optional decoded text, a filename, and a MIME type. If the text exists and is short enough, it returns the text; otherwise it writes the bytes to the workspace through _offloaded and returns that reference.

**Call relations**: _translated_node and _translated_data_url call this after decoding base64. It is the fork between keeping useful text in context and moving heavy or binary content into a file.

*Call graph*: calls 1 internal fn (_offloaded); called by 2 (_translated_data_url, _translated_node).


##### `_ConnectorCall._offloaded`  (lines 707–749)

```
async def _offloaded(self, name: str, mimetype: str, data: bytes) -> dict[str, object]
```

**Purpose**: Writes decoded result bytes into the workspace and returns a small reference object. It uses a content-based folder name so the same bytes do not create endless duplicate files.

**Data flow**: It receives a suggested name, MIME type, and byte content. It sanitizes the name, hashes the bytes with SHA-256, writes them to a temporary path in the sandbox, atomically places them at the final connector_files path, and returns name, workspace path, MIME type, and byte count.

**Call relations**: _translated_bytes calls this whenever decoded content should not be inlined. It works through the sandbox write path and a guarded placement script to avoid unsafe symlinks or partial writes.

*Call graph*: called by 1 (_translated_bytes); 3 external calls (sha256, contained_leaf, uuid4).


##### `_ConnectorCall._deduped`  (lines 751–809)

```
def _deduped(self, payload: dict[str, object]) -> str
```

**Purpose**: Serializes the final result and, when safe and worthwhile, replaces repeated large objects with same_as pointers. This keeps repetitive connector responses smaller without deleting information.

**Data flow**: It receives the cleaned result payload. It serializes it once, skips deduplication if the result is too large, too structurally dense, or already contains same_as, otherwise walks the payload with _condensed and returns a JSON string of either the original or condensed form.

**Call relations**: _ConnectorCall.run runs this in a worker thread after file and base64 cleanup. It uses _condensed to find repeated structures and _escaped to build JSON Pointer paths.

*Call graph*: calls 2 internal fn (_condensed, _escaped); 1 external calls (dumps).


##### `_ConnectorCall._condensed`  (lines 811–887)

```
def _condensed(self, value: object, pointer: str, depth: int, first: dict[bytes, str]) -> tuple[object, bytes, int]
```

**Purpose**: Walks one part of a JSON-like result and detects repeated dictionary objects by structural identity. Later copies of the same sufficiently large object become a pointer to the first copy.

**Data flow**: It receives a value, its JSON Pointer path, current depth, and a table of first-seen object hashes. It recursively processes dictionaries and lists, hashes each node based on its shape and child hashes, records large first-seen dictionaries, replaces later matching dictionaries with {"same_as": pointer}, and returns the rewritten value plus its hash and original size.

**Call relations**: _deduped calls this for each top-level payload field. It calls itself recursively, uses _escaped for pointer path tokens, and uses SHA-256 hashing to compare structures without repeatedly serializing whole subtrees.

*Call graph*: calls 1 internal fn (_escaped); called by 1 (_deduped); 1 external calls (sha256).


##### `_escaped`  (lines 890–893)

```
def _escaped(token: str) -> str
```

**Purpose**: Escapes one path segment for a JSON Pointer. JSON Pointer is a standard way to point to a place inside a JSON document, and keys containing / or ~ need special spelling.

**Data flow**: It receives one dictionary key string. It replaces ~ with ~0 and / with ~1, then returns the escaped token.

**Call relations**: _deduped and _condensed use this while building same_as pointer paths. It makes sure a pointer still identifies the exact original object even when provider keys contain special characters.

*Call graph*: called by 2 (_condensed, _deduped).


##### `_decoded_base64`  (lines 896–920)

```
def _decoded_base64(value: object) -> tuple[bytes, str | None] | None
```

**Purpose**: Safely decodes a string that a provider claims is base64. It refuses oversized or invalid data instead of guessing and corrupting ordinary text.

**Data flow**: It receives any value. If the value is a string within the decode limit, it removes whitespace, strictly base64-decodes it, then tries to decode the bytes as UTF-8 text; it returns bytes plus text when possible, bytes plus no text for binary data, or nothing when decoding is invalid.

**Call relations**: _translated_node and _translated_data_url call this before converting embedded encoded content. Its result feeds into _translated_bytes, which decides whether content stays inline or becomes a workspace file.

*Call graph*: called by 2 (_translated_data_url, _translated_node); 1 external calls (b64decode).


##### `search_connector_tools`  (lines 923–937)

```
async def search_connector_tools(ctx: ToolContext, args: SearchConnectorToolsInput) -> ToolResult
```

**Purpose**: Searches within one connector for tools that match a natural-language goal. It can return matching tool schemas plus broker-provided advice, such as recommended steps or pitfalls.

**Data flow**: It receives a source ID and query. It looks up the connector, asks the broker to search its catalog, formats the found tools into a bounded list, adds plan, guidance, and pitfalls from the broker, and returns JSON.

**Call relations**: This is a public discovery tool, richer than describe_external_tools for goal-based searches. It uses _registry to find the connector, _discovered_rows to apply fallback and size rules, and _json_result to format the response.

*Call graph*: calls 3 internal fn (_discovered_rows, _json_result, _registry).


##### `_registry`  (lines 940–943)

```
def _registry(ctx: ToolContext) -> ConnectorRegistry
```

**Purpose**: Returns the live connector registry for the current turn. It fails loudly if connector tools were invoked without registry data, because discovery and execution cannot work without it.

**Data flow**: It receives the tool context. If ctx.connectors exists, it returns it; otherwise it raises a runtime error.

**Call relations**: All public connector handlers call this first or near first: list_external_tools, describe_external_tools, search_connector_tools, and call_external_tool. It is the common gate to the turn's connector state.

*Call graph*: called by 4 (call_external_tool, describe_external_tools, list_external_tools, search_connector_tools).


##### `_tool_json`  (lines 946–947)

```
def _tool_json(tool: BrokerTool) -> dict[str, object]
```

**Purpose**: Converts a broker tool object into the compact JSON shape returned to the agent. It keeps the fields the agent needs: slug, description, and input schema.

**Data flow**: It receives a BrokerTool. It extracts its slug, description, and input_schema and returns them in a plain dictionary.

**Call relations**: describe_external_tools uses this for exact schema results, and _available_tools uses it when building discovery rows. It standardizes how tool descriptions appear across different discovery paths.

*Call graph*: called by 2 (_available_tools, describe_external_tools).


##### `_discovered_rows`  (lines 950–967)

```
async def _discovered_rows(entry: ConnectorEntry, workspace_id: UUID, query: str, found: tuple[BrokerTool, ...]) -> tuple[list[dict[str, object]], str]
```

**Purpose**: Builds the list of tool rows for discovery and adds a note when a search had to fall back. The fallback matters because an empty search result should not falsely tell the agent the connector has no useful tools.

**Data flow**: It receives a connector entry, workspace ID, query, and found tools. If a non-empty query found nothing, it asks the broker for the connector's unqueried top tools instead; it then trims rows to the inline budget and returns both rows and any explanatory note.

**Call relations**: describe_external_tools and search_connector_tools both call this, so they share the same “never a dead end” behavior. It delegates final row trimming to _available_tools.

*Call graph*: calls 1 internal fn (_available_tools); called by 2 (describe_external_tools, search_connector_tools).


##### `_available_tools`  (lines 970–983)

```
def _available_tools(listed: tuple[BrokerTool, ...]) -> list[dict[str, object]]
```

**Purpose**: Returns as many discovered tool rows as can fit in the inline response budget. This avoids producing a huge discovery answer that the engine would offload to a file.

**Data flow**: It receives a tuple of broker tools. It converts tools one by one with _tool_json, counts the JSON size spent so far, stops once adding more would exceed the budget after at least one row, and returns the selected rows.

**Call relations**: _discovered_rows calls this for both normal and fallback discovery results. It uses JSON serialization only to estimate how large each row will be in the final response.

*Call graph*: calls 1 internal fn (_tool_json); called by 1 (_discovered_rows); 1 external calls (dumps).


##### `_discovery_query`  (lines 986–993)

```
def _discovery_query(explicit: str, unresolved: list[str]) -> str
```

**Purpose**: Creates a useful catalog search query when exact tool names were requested but some were wrong. It turns missed slugs into deduplicated plain words that can find nearby real tools.

**Data flow**: It receives an explicit query and a list of unresolved tool names. If the explicit query is non-empty, it returns that; otherwise it lowercases unresolved names, replaces punctuation with spaces, removes duplicate words while preserving order, and returns the joined search string.

**Call relations**: describe_external_tools calls this when it needs to search after unresolved tool names or when no exact names were provided. Its output is sent to the broker's tool listing.

*Call graph*: called by 1 (describe_external_tools); 1 external calls (sub).


##### `_json_result`  (lines 996–997)

```
def _json_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: Wraps a dictionary payload as a ToolResult containing JSON text. It gives the public handlers a single consistent way to return structured data.

**Data flow**: It receives a dictionary. It serializes it with json.dumps, places that text in a TextContent object, and returns a ToolResult containing it.

**Call relations**: list_external_tools, describe_external_tools, and search_connector_tools use this for their final answers. Execution results from call_external_tool are already serialized by _ConnectorCall.run, so they are wrapped directly there instead.

*Call graph*: called by 3 (describe_external_tools, list_external_tools, search_connector_tools); 3 external calls (__init__, __init__, dumps).


### `extensions/eval_env/ufo_ext_eval_env/manifest.py`

`domain_logic` · `evaluation connector setup and tool calls`

This file gives the system a controlled “practice office” for evaluations. An agent can send email, list calendar events, search code, or read business records through the same connector route it would use in production, but the data comes from fixed tables and seeded storage. That matters because tests can check the exact final state after an agent acts, without depending on real Gmail, GitHub, or other services.

The main piece is `EvalEnvBroker`, which is the fake service counter. It advertises available tools, validates tool inputs, reads seeded fixtures for read-only services, and writes durable changes for email and calendar actions. If a test forgot to seed data, it fails loudly instead of quietly returning an empty answer.

The file also defines an object store for fixed application actions. These are small, allowed mutations over seeded GitHub-like fixtures, such as assigning an issue or enabling a PR babysitter. Think of it like a staged board game: only certain legal moves exist, and every move changes the shared board so the grader can inspect it later.

Finally, the file registers the extension manifest: connector providers, a private repair agent, and a hook that tightly limits what that repair agent may read or edit.

#### Function details

##### `_transaction`  (lines 371–375)

```
def _transaction()
```

**Purpose**: Opens a database transaction for this evaluation extension. It gives email and calendar operations a safe way to read or write the extension’s own workspace-scoped tables.

**Data flow**: It takes no direct input. It builds an `ExtensionContext` with this extension’s scoped storage and no declared credentials, then returns that context’s transaction object so callers can run database statements inside it.

**Call relations**: Email and calendar helper methods call this whenever they need durable state. It is the shared doorway through which sending email, replying, listing mail, creating events, listing events, and changing events reach the database.

*Call graph*: called by 6 (_change_event, _create_event, _list_emails, _list_events, _reply_all_email, _send_email); 3 external calls (__init__, __init__, __init__).


##### `_moment`  (lines 378–382)

```
def _moment(value: str) -> datetime
```

**Purpose**: Turns a text timestamp into a timezone-aware `datetime` value. This keeps calendar times consistent even when the caller leaves out a timezone.

**Data flow**: It receives an ISO 8601 time string, parses it, and if the parsed time has no timezone, treats it as UTC. It returns the normalized datetime object.

**Call relations**: Calendar creation and updates call this before storing event start or end times. It keeps those paths from each inventing their own time parsing rules.

*Call graph*: called by 2 (_create_event, _update_event); 1 external calls (fromisoformat).


##### `EvalEnvBroker.tools`  (lines 390–398)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Returns the tools available for one provider, optionally filtered by a search query. This is what lets the connector layer show an agent the relevant actions it can take.

**Data flow**: It receives a workspace id, provider name, and query text. It looks up that provider’s fixed catalog, filters by tool name or description when there is a query, and returns matching tools; if nothing matches, it returns the full catalog.

**Call relations**: The broker search method calls this when the connector layer asks what tools are available. It reads from the fixed in-file catalog rather than from an external service.

*Call graph*: called by 1 (search).


##### `EvalEnvBroker.schema`  (lines 400–404)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Finds the definition for one specific tool. The schema tells the system what arguments the tool accepts.

**Data flow**: It receives a provider and tool slug, scans that provider’s catalog, and returns the matching `BrokerTool`. If no such tool exists, it raises an `UnknownBrokerTool` error.

**Call relations**: The main execute path uses this to confirm fixture-backed read-only tools really exist before returning seeded data. It is also the broker’s guardrail against misspelled or unsupported tool names.

*Call graph*: called by 1 (execute); 1 external calls (__init__).


##### `EvalEnvBroker.execute`  (lines 406–467)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs a requested connector tool against the deterministic evaluation data. This is the central dispatcher for all eval environment tool calls.

**Data flow**: It receives the workspace, provider, tool name, raw arguments, account id, and optional idempotency key. It validates arguments with the right input model, sends the request to the matching helper, reads seeded fixtures for read-only app providers, or raises an error if the tool is unknown or unseeded.

**Call relations**: The connector runtime calls this when an agent invokes a tool. It then hands off to specialized helpers for email, calendar, GitHub commit status, code search, or fixture-backed providers.

*Call graph*: calls 10 internal fn (_cancel_event, _create_commit_status, _create_event, _list_emails, _list_events, _reply_all_email, _search_code, _send_email, _update_event, schema); 2 external calls (__init__, __init__).


##### `EvalEnvBroker._create_commit_status`  (lines 469–476)

```
async def _create_commit_status(self, args: CreateCommitStatusArgs) -> dict[str, object]
```

**Purpose**: Accepts and echoes a GitHub-like commit status. It lets evaluations verify that an agent published a review verdict even though no real GitHub repository is involved.

**Data flow**: It receives validated commit status fields such as SHA, state, context, description, and target URL. It returns those same fields as a provider-style response without writing to an outside service.

**Call relations**: The main execute method calls this for the GitHub `create_commit_status` tool. It is deliberately small because its job is to validate the shape of the publication, not to contact GitHub.

*Call graph*: called by 1 (execute).


##### `EvalEnvBroker._search_code`  (lines 478–486)

```
async def _search_code(self, args: SearchCodeArgs) -> dict[str, object]
```

**Purpose**: Returns a seeded code-search response for an exact query. This makes code search predictable during evaluations.

**Data flow**: It receives a validated search query, builds the storage key for that exact query, and reads the seeded response from scoped storage. If the stored value is not a dictionary, it raises an error; otherwise it returns a copy.

**Call relations**: The main execute method calls this for code search. It depends on prior test setup having seeded the exact query response, so missing fixtures fail immediately.

*Call graph*: called by 1 (execute); 1 external calls (__init__).


##### `EvalEnvBroker._send_email`  (lines 488–503)

```
async def _send_email(self, workspace_id: UUID, args: SendEmailArgs) -> dict[str, object]
```

**Purpose**: Creates a sent email in the evaluation mailbox. It simulates sending mail by writing a row that graders can later inspect.

**Data flow**: It receives the workspace id and validated email fields. It creates a new id, inserts a sent-message row with sender, recipients, subject, body, and current time, then returns the new id, sent status, and recipients.

**Call relations**: The execute dispatcher calls this for `send_email`. It uses `_transaction` so the inserted email becomes durable shared state for the evaluation.

*Call graph*: calls 1 internal fn (_transaction); called by 1 (execute); 3 external calls (now, insert, uuid4).


##### `EvalEnvBroker._reply_all_email`  (lines 505–546)

```
async def _reply_all_email(self, workspace_id: UUID, args: ReplyAllEmailArgs) -> dict[str, object]
```

**Purpose**: Replies to an existing email and includes everyone except the eval mailbox itself. It mirrors ordinary “reply all” behavior in a controlled mailbox.

**Data flow**: It receives the workspace id and the original message id plus reply body. It looks up the original email, builds a de-duplicated recipient list from the sender and recipients, adds a `Re:` subject when needed, inserts the sent reply, and returns the new message id and recipients.

**Call relations**: The execute dispatcher calls this for `reply_all_email`. It relies on `_transaction` to read the original and write the reply in one database interaction, and it fails if the original message or recipients are missing.

*Call graph*: calls 1 internal fn (_transaction); called by 1 (execute); 4 external calls (now, insert, select, uuid4).


##### `EvalEnvBroker._list_emails`  (lines 548–583)

```
async def _list_emails(self, workspace_id: UUID, args: ListEmailsArgs) -> dict[str, object]
```

**Purpose**: Lists emails from the eval mailbox. It supports folder selection, simple text filtering, and a maximum result count.

**Data flow**: It receives the workspace id and list options. It builds database conditions for workspace and folder, optionally adds a case-insensitive search over sender, subject, and body, reads newest messages first, and returns them as plain JSON-style dictionaries.

**Call relations**: The execute dispatcher calls this for `list_emails`. It uses `_transaction` for the read and returns data in the shape an agent-facing connector expects.

*Call graph*: calls 1 internal fn (_transaction); called by 1 (execute); 2 external calls (or_, select).


##### `EvalEnvBroker._create_event`  (lines 585–599)

```
async def _create_event(self, workspace_id: UUID, args: CreateEventArgs) -> dict[str, object]
```

**Purpose**: Creates a confirmed calendar event in the evaluation calendar. This gives tests a durable record of scheduling actions.

**Data flow**: It receives the workspace id and validated event details. It creates a new id, parses start and end times with `_moment`, inserts the event as confirmed, and returns the id and status.

**Call relations**: The execute dispatcher calls this for `create_event`. It uses `_transaction` for the database write and `_moment` to keep stored times consistent.

*Call graph*: calls 2 internal fn (_moment, _transaction); called by 1 (execute); 2 external calls (insert, uuid4).


##### `EvalEnvBroker._list_events`  (lines 601–614)

```
async def _list_events(self, workspace_id: UUID, args: ListEventsArgs) -> dict[str, object]
```

**Purpose**: Lists calendar events for a workspace, optionally filtered by title. It returns events in start-time order.

**Data flow**: It receives the workspace id and list options. It builds a query, reads matching rows up to the requested limit, converts each row with `_event_json`, and returns them under an `events` key.

**Call relations**: The execute dispatcher calls this for `list_events`. It shares formatting with event update and cancel responses through `_event_json`.

*Call graph*: calls 2 internal fn (_event_json, _transaction); called by 1 (execute); 1 external calls (select).


##### `EvalEnvBroker._update_event`  (lines 616–628)

```
async def _update_event(self, workspace_id: UUID, args: UpdateEventArgs) -> dict[str, object]
```

**Purpose**: Updates selected fields on an existing calendar event. It only changes fields the caller actually supplied.

**Data flow**: It receives the workspace id and update arguments. It builds a change set from non-empty title, time, and attendee fields, parses any new times, rejects an empty update, and passes the changes to `_change_event`.

**Call relations**: The execute dispatcher calls this for `update_event`. It prepares and validates the requested edits, while `_change_event` performs the shared database update.

*Call graph*: calls 2 internal fn (_change_event, _moment); called by 1 (execute).


##### `EvalEnvBroker._cancel_event`  (lines 630–631)

```
async def _cancel_event(self, workspace_id: UUID, args: CancelEventArgs) -> dict[str, object]
```

**Purpose**: Marks an event as cancelled without removing it. This matches the eval rule that cancelled events stay visible with a cancelled status.

**Data flow**: It receives the workspace id and event id. It asks `_change_event` to set that event’s status to `cancelled` and returns the updated event data.

**Call relations**: The execute dispatcher calls this for `cancel_event`. It reuses the same update machinery as normal event edits.

*Call graph*: calls 1 internal fn (_change_event); called by 1 (execute).


##### `EvalEnvBroker._change_event`  (lines 633–652)

```
async def _change_event(self, workspace_id: UUID, event_id: str, changes: dict[str, object]) -> dict[str, object]
```

**Purpose**: Applies a prepared set of changes to one calendar event and returns the updated event. It is the shared write path for updating and cancelling events.

**Data flow**: It receives a workspace id, event id text, and a dictionary of changed fields. It converts the id to a UUID, updates the matching row for that workspace, errors if exactly one row was not changed, reads the row back, and formats it with `_event_json`.

**Call relations**: `_update_event` and `_cancel_event` both call this after deciding what should change. It centralizes the database update and missing-event check.

*Call graph*: calls 2 internal fn (_event_json, _transaction); called by 2 (_cancel_event, _update_event); 3 external calls (select, update, UUID).


##### `EvalEnvBroker._event_json`  (lines 654–662)

```
def _event_json(self, row: sa.Row) -> dict[str, object]
```

**Purpose**: Converts a calendar database row into the response shape returned to tools. It hides database column names behind simpler API field names.

**Data flow**: It receives a row with event fields. It turns ids and times into strings and returns a dictionary containing id, title, start, end, attendees, and status.

**Call relations**: Event listing and event changes both call this so their responses look the same. It is the final formatting step after calendar reads.

*Call graph*: called by 2 (_change_event, _list_events).


##### `EvalEnvBroker.file_outputs`  (lines 664–665)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Declares that these eval tools do not produce downloadable files. It satisfies the broker interface with an empty answer.

**Data flow**: It receives a tool response but does not inspect it. It always returns an empty tuple of files.

**Call relations**: The broader connector system can ask any broker for file outputs after a tool call. For this broker, there is nothing to hand off.


##### `EvalEnvBroker.stage_upload`  (lines 667–676)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Rejects file uploads for eval environment providers. These deterministic tools are text-and-data only.

**Data flow**: It receives upload details such as provider, tool, filename, MIME type, and checksum. It does not store anything and raises a runtime error explaining that uploads are unsupported.

**Call relations**: The connector upload flow would call this if someone tried to upload a file through an eval provider. The method protects the eval environment from unsupported side paths.


##### `EvalEnvBroker.search`  (lines 678–679)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Wraps the provider’s matching tools in a broker search result. This is the connector-friendly version of tool lookup.

**Data flow**: It receives a workspace id, provider, and query. It calls `tools` to get the matching catalog entries, then returns them inside a `BrokerSearch` object.

**Call relations**: The connector layer uses this when listing or searching external tools. It delegates the actual matching to `EvalEnvBroker.tools`.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `EvalEnvBroker.credential`  (lines 681–682)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a fake bearer credential for an eval account. This lets the connector path behave as if an account is connected without using real secrets.

**Data flow**: It receives workspace, provider, and account id. It returns a `Credential` whose bearer token is a deterministic string based on the account.

**Call relations**: The connector framework can request credentials before calling provider tools. In this eval environment, the credential is only a placeholder because all work stays local.

*Call graph*: 1 external calls (__init__).


##### `_EvalEnvOAuth.authorize_url`  (lines 693–694)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds a plausible authorization URL for an eval provider. It exists because connector providers need an OAuth-style descriptor, even though evals seed grants directly.

**Data flow**: It receives state and redirect URI strings. It combines them with the provider’s fake host into an HTTPS authorization URL.

**Call relations**: The manifest registers `_EvalEnvOAuth` objects for each provider. If a connect flow ever asks for an authorization URL, this method supplies one without contacting a real service.


##### `_EvalEnvOAuth.exchange`  (lines 696–699)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Completes a fake OAuth exchange by returning the fixed eval account id. OAuth is the common web flow where an app trades a code for account access.

**Data flow**: It receives a code, redirect URI, workspace id, and state. It ignores the external details and returns an `OAuthAccount` using the fixed eval account id.

**Call relations**: The connector registry expects an exchange method on OAuth providers. This keeps the eval provider structurally honest while avoiding real third-party authorization.

*Call graph*: 1 external calls (__init__).


##### `AppActionStore.list`  (lines 706–724)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists previously applied eval application actions as objects. This lets the system show what fixed app-bench actions have already been taken.

**Data flow**: It receives a tool context and object list query. It reads stored action records with the action key prefix, validates them, turns each into an `ObjectRow`, and returns a paged result.

**Call relations**: The object framework calls this when someone lists objects of the eval app action kind. It uses `_ext` to reach the extension context and `object_page` to shape the final list.

*Call graph*: calls 1 internal fn (_ext); 2 external calls (__init__, object_page).


##### `AppActionStore.get`  (lines 726–734)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[AppActionSpec] | None
```

**Purpose**: Fetches the full specification for one stored app action. It lets callers inspect the exact action request that was applied.

**Data flow**: It receives a context and object name. It loads the stored action with `_stored`; if none exists it returns `None`, otherwise it returns an `ObjectDetail` with the spec and timestamps.

**Call relations**: The object framework calls this for object detail views. It relies on `_stored` for the storage lookup and validation.

*Call graph*: calls 1 internal fn (_stored); 1 external calls (__init__).


##### `AppActionStore.status`  (lines 736–746)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports whether a stored app action has been applied and what result text it produced. It gives callers a simple state check.

**Data flow**: It receives a context, object name, and optional expected generation. It loads the stored record; if missing it returns `None`, otherwise it returns `state: applied` plus the stored result.

**Call relations**: The object framework calls this when checking an action object’s state. It shares the lookup path with `get` and `apply` through `_stored`.

*Call graph*: calls 1 internal fn (_stored).


##### `AppActionStore.apply`  (lines 748–772)

```
async def apply(self, ctx: ToolContext, name: str, spec: AppActionSpec, old: AppActionSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Applies one allowed application action to the seeded fixture and records it permanently. It prevents changing an already recorded action to a different request.

**Data flow**: It receives a context, action name, desired spec, optional old spec, and expected generation. It checks whether the action already exists; if the same action exists it does nothing, if a different one exists it errors, otherwise it mutates the seeded fixture through `_apply_fixture` and stores a `StoredAppAction` with timestamps.

**Call relations**: The object framework calls this when an app action object is applied. It uses `_stored` for idempotency, `_apply_fixture` for the actual fixture mutation, and `_ext` to write the resulting record.

*Call graph*: calls 3 internal fn (_apply_fixture, _ext, _stored); 2 external calls (__init__, now).


##### `AppActionStore.delete`  (lines 774–781)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects deletion of eval application actions. Once applied, these actions are intentionally immutable so graders can trust the final record.

**Data flow**: It receives a context, action name, and optional expected generation. It does not read or change storage; it raises `VerbNotSupported`.

**Call relations**: The object framework would call this for delete requests. This store answers that deletion is not part of the eval action contract.

*Call graph*: 1 external calls (__init__).


##### `AppActionStore._apply_fixture`  (lines 783–847)

```
async def _apply_fixture(self, ctx: ToolContext, name: str, spec: AppActionSpec) -> str
```

**Purpose**: Performs the actual allowed mutation on a seeded GitHub-like fixture. It is where the few legal app-bench actions are encoded.

**Data flow**: It receives a context, action name, and action spec. It finds the fixture key for the case, deep-copies the seeded response through JSON, locates the relevant list of issues or pull requests, applies the one matching allowed change, writes the changed fixture under an action fixture key, and returns human-readable result text.

**Call relations**: `AppActionStore.apply` calls this only for new actions. It is intentionally strict: if the fixture is missing, malformed, or the action does not match the fixed contract, it raises an error.

*Call graph*: calls 1 internal fn (_ext); called by 1 (apply); 2 external calls (dumps, loads).


##### `AppActionStore._stored`  (lines 849–851)

```
async def _stored(self, ctx: ToolContext, name: str) -> StoredAppAction | None
```

**Purpose**: Loads and validates one stored app action record. It keeps the storage-reading code in one place.

**Data flow**: It receives a context and action name. It reads the value under the action key prefix, returns `None` if missing, or validates and returns it as a `StoredAppAction`.

**Call relations**: `get`, `status`, and `apply` all call this before deciding what to show or whether a new action may be written. It uses `_ext` to reach the extension store.

*Call graph*: calls 1 internal fn (_ext); called by 3 (apply, get, status).


##### `AppActionStore._ext`  (lines 853–856)

```
def _ext(self, ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Retrieves the extension context from a tool context. This is needed because the store lives on the extension context.

**Data flow**: It receives a `ToolContext`. If the context has an extension context, it returns it; otherwise it raises a runtime error because app actions cannot work without scoped storage.

**Call relations**: Most `AppActionStore` storage methods call this before reading or writing. It is the safety check that the object store is being used in the proper extension environment.

*Call graph*: called by 4 (_apply_fixture, _stored, apply, list).


##### `bound_app_qa_repair_tools`  (lines 869–924)

```
async def bound_app_qa_repair_tools(ctx: HookContext)
```

**Purpose**: Enforces strict tool limits for the private app QA repair agent. It allows that agent to read or make small edits only to the fixed app source file.

**Data flow**: It receives hook context before a tool runs. If the current agent is not the repair agent, it does nothing. For the repair agent, it allows reading the exact source path, checks edits for the same path, blocks replace-all edits, tracks edit call and byte budgets in extension storage, and returns a `Deny` object when a rule is broken.

**Call relations**: The manifest registers this as a `pre_tool_use` hook for read and edit tools. It acts like a gatekeeper before the repair agent can touch the workspace.

*Call graph*: 2 external calls (__init__, __init__).


##### `manifest`  (lines 944–1000)

```
def manifest() -> Manifest
```

**Purpose**: Builds the extension manifest that tells the host system what this eval extension provides. The manifest is the registration card for connectors, objects, agents, and hooks.

**Data flow**: It creates one `EvalEnvBroker`, wraps each fake provider with OAuth metadata and labels, includes the app action object kind, registers the repair agent, attaches the pre-tool-use hook, and returns a `Manifest` object.

**Call relations**: The extension loader calls this to discover the extension. Everything else in the file becomes reachable through the providers, object store, agent provision, and hook listed here.

*Call graph*: 5 external calls (__init__, __init__, __init__, __init__, __init__).


### `extensions/mcp/ufo_ext_mcp.py`

`domain_logic` · `extension registration, credential update, and MCP tool request handling`

MCP, or Model Context Protocol, is a standard way for outside services to offer tools to an AI agent. This file is the bridge between the agent and those outside MCP servers. Without it, the agent would not know which MCP servers a workspace has configured, could not list the tools those servers provide, and could not safely call them.

The file exposes two main tools to the agent. `list_mcp_tools` asks a named server what tools it has. To avoid flooding the agent with huge tool schemas, it first returns a short catalog, like a restaurant menu with names and summaries. If the agent wants to use a specific tool, it asks again for that tool's full input schema, meaning the exact shape of the data the tool expects. `call_mcp_tool` then sends arguments to one chosen MCP tool and returns the result.

The file also protects boundaries. Server URLs must be HTTP or HTTPS. Request and response sizes are capped at one mebibyte, so a bad or broken server cannot dump unlimited data into the system. Results from MCP servers are marked as untrusted because they come from outside services and could contain misleading or hostile text. Credentials stay in the core process and are sent directly as bearer tokens when needed.

#### Function details

##### `McpServer._http_url`  (lines 81–84)

```
def _http_url(cls, value: str) -> str
```

**Purpose**: This validator checks that an MCP server URL starts with `http://` or `https://`. It prevents the workspace configuration from pointing this extension at unsupported or surprising URL types.

**Data flow**: It receives a URL string during `McpServer` validation. It compares the string with the allowed HTTP/HTTPS pattern. If the URL is valid, the same string continues into the model; if not, validation fails with a clear error.

**Call relations**: This runs automatically when an `McpServer` is built, including during credential merging and credential loading. It is an early gate before later functions, such as `mcp_client`, ever try to contact a server.


##### `McpServerUpdate._name`  (lines 99–103)

```
def _name(cls, value: str) -> str
```

**Purpose**: This validator cleans and checks the server name when a user adds or updates one MCP server. It makes sure a name is not just empty space.

**Data flow**: It receives the submitted name, trims whitespace from both ends, and checks whether anything remains. The trimmed name is stored if valid; otherwise validation stops with an error.

**Call relations**: It runs inside `merge_mcp_server` when a submitted credential value is treated as a single-server add or update. The cleaned name becomes the key used to store that server.


##### `McpServerRemoval._name`  (lines 112–116)

```
def _name(cls, value: str) -> str
```

**Purpose**: This validator cleans and checks the server name when a user asks to remove an MCP server. It prevents removal requests with blank names.

**Data flow**: It receives the submitted name, trims it, and verifies that the result is not empty. A valid trimmed name is used for lookup; an empty one causes validation to fail.

**Call relations**: It runs inside `merge_mcp_server` when the submitted credential value contains `remove: true`. The checked name is then used to find and delete an existing server entry.


##### `merge_mcp_server`  (lines 119–157)

```
def merge_mcp_server(current: str | None, submitted: str) -> str
```

**Purpose**: This function updates the stored MCP server credential value. It supports three user actions: replace the whole server map, add or update one named server, or remove one named server.

**Data flow**: It receives the current stored JSON string, if any, and a newly submitted JSON string. It parses and validates the submitted data, combines it with the existing server map when needed, preserves an old auth token during an update if no new token was supplied, and returns a cleaned JSON string to store. If the input is malformed or names an impossible removal, it raises `CredentialValueInvalid` so the bad credential is rejected.

**Call relations**: This function is attached to the `mcp_servers` credential slot by `manifest`. It is called by the credential system when someone submits MCP server settings, and it builds validated `McpServer` and `McpServersConfig` objects that later request-time code reads through `_server`.

*Call graph*: 4 external calls (__init__, __init__, __init__, loads).


##### `mcp_client`  (lines 177–183)

```
def mcp_client(server: McpServer) -> Client
```

**Purpose**: This function creates a client connection object for one configured MCP server. It packages the server URL, optional bearer token, and timeout into the transport used to speak MCP over HTTP.

**Data flow**: It receives a validated `McpServer`. If the server has an auth token, it turns it into an `authorization: Bearer ...` header. It then creates a Streamable HTTP transport and wraps it in a FastMCP client, which is returned to the caller.

**Call relations**: `_list_mcp_tools` and `_call_mcp_tool` call this after `_server` has resolved a configured server name. The returned client performs the actual MCP protocol work, such as listing tools and calling tools.

*Call graph*: called by 2 (_call_mcp_tool, _list_mcp_tools); 2 external calls (Client, StreamableHttpTransport).


##### `_server`  (lines 186–198)

```
async def _server(ctx: ToolContext, name: str) -> McpServer
```

**Purpose**: This helper finds one named MCP server in the workspace's stored credentials. It makes sure tool calls do not silently go to nowhere or to an unconfigured server.

**Data flow**: It receives the current tool context and a server name. It reads the `mcp_servers` credential slot from the extension context, parses it into a validated config object, and looks up the requested name. It returns the matching `McpServer`, or raises an error if the extension context is missing or the name is unknown.

**Call relations**: `_list_mcp_tools` and `_call_mcp_tool` both use this as their first step. It hands them the validated connection information they need before `mcp_client` can open a client connection.

*Call graph*: called by 2 (_call_mcp_tool, _list_mcp_tools).


##### `_list_mcp_tools`  (lines 201–222)

```
async def _list_mcp_tools(ctx: ToolContext, args: ListMcpToolsInput) -> ToolResult
```

**Purpose**: This is the handler behind the agent-facing `list_mcp_tools` tool. It lets the agent browse an MCP server's tools first, then ask for full schemas only for the tools it plans to call.

**Data flow**: It receives the tool context and parsed input containing a server name and optional tool names. It resolves the server, connects to it, asks for its tool list, and either returns a compact catalog or returns full schema entries for requested tools. If a requested tool name is not exposed by the server, it raises an error that tells the agent to list the catalog first.

**Call relations**: The `manifest` registers this as the handler for `list_mcp_tools`. In its flow it relies on `_server` for configuration, `mcp_client` for communication, `_catalog_entry` for compact entries, `_schema_entry` for full entries, `_bounded_schemas` for size-aware schema responses, and `_json_result` for the final tool result.

*Call graph*: calls 6 internal fn (_bounded_schemas, _catalog_entry, _json_result, _schema_entry, _server, mcp_client).


##### `_idempotent`  (lines 225–227)

```
def _idempotent(tool: McpTool) -> bool
```

**Purpose**: This helper reads whether an MCP tool says it is idempotent, meaning repeated calls should have the same effect as one call. That hint helps the agent understand how risky it may be to retry or repeat a tool.

**Data flow**: It receives an MCP tool object. It checks the tool's annotations for an `idempotentHint` and converts that to a plain true-or-false value. If the tool has no annotations, it returns false.

**Call relations**: `_catalog_entry` and `_schema_entry` call this so both short catalog listings and full schema responses include the same safety hint.

*Call graph*: called by 2 (_catalog_entry, _schema_entry).


##### `_catalog_entry`  (lines 230–246)

```
def _catalog_entry(tool: McpTool) -> JsonValue
```

**Purpose**: This function turns a full MCP tool description into a small catalog entry. It gives the agent enough information to choose a tool without sending the whole schema.

**Data flow**: It receives an MCP tool object. It extracts the tool name, a short summary, parameter names from the input schema, required parameter names, and the idempotent hint. It returns a plain JSON-like dictionary suitable for a compact listing.

**Call relations**: `_list_mcp_tools` calls this for every tool when the agent asks to browse a server without requesting full schemas. It uses `_summary` to shorten the description and `_idempotent` to include the retry-safety hint.

*Call graph*: calls 2 internal fn (_idempotent, _summary); called by 1 (_list_mcp_tools).


##### `_summary`  (lines 249–255)

```
def _summary(description: str) -> str
```

**Purpose**: This helper makes a short summary from a longer tool description. It is meant to keep catalog listings readable and small.

**Data flow**: It receives a description string, trims it, keeps only the first line, takes the first sentence-like part, and cuts it to the maximum summary length. The result is a short text summary.

**Call relations**: `_catalog_entry` calls this while building the compact tool catalog. It keeps long docstrings or argument sections from spilling into the browsing view.

*Call graph*: called by 1 (_catalog_entry).


##### `_schema_entry`  (lines 258–264)

```
def _schema_entry(tool: McpTool) -> JsonValue
```

**Purpose**: This function prepares the full details for one MCP tool when the agent is ready to inspect it closely. It includes the exact input schema the agent needs before calling the tool.

**Data flow**: It receives an MCP tool object and returns a JSON-like dictionary containing the tool name, full description, input schema, and idempotent hint.

**Call relations**: `_list_mcp_tools` calls this only for tool names the agent specifically requested. It uses `_idempotent` so the detailed view carries the same repeat-safety information as the catalog view.

*Call graph*: calls 1 internal fn (_idempotent); called by 1 (_list_mcp_tools).


##### `_call_mcp_tool`  (lines 267–278)

```
async def _call_mcp_tool(ctx: ToolContext, args: CallMcpToolInput) -> ToolResult
```

**Purpose**: This is the handler behind the agent-facing `call_mcp_tool` tool. It sends the agent's arguments to a named tool on a named MCP server and returns the server's answer.

**Data flow**: It receives the tool context and parsed input containing the server name, tool name, and JSON arguments. It resolves the server, checks that the serialized arguments are not over the request size limit, calls the MCP tool, and then turns the response into a `ToolResult`. If the server reports an error, it returns a structured tool failure marked as untrusted; if the server returns structured JSON, it returns that; otherwise it joins any text blocks into a JSON result.

**Call relations**: The `manifest` registers this as the handler for `call_mcp_tool`. Its path runs through `_server` and `mcp_client`, delegates server-side failures to `_call_failed`, uses `_joined_text` for text-only responses, and uses `_json_result` to apply the response size check before returning.

*Call graph*: calls 5 internal fn (_call_failed, _joined_text, _json_result, _server, mcp_client); 2 external calls (__init__, dumps).


##### `_call_failed`  (lines 281–296)

```
def _call_failed(args: CallMcpToolInput, result: CallToolResult) -> ToolFailure
```

**Purpose**: This function turns an MCP server's own tool error into the system's standard tool-failure shape. It preserves useful diagnostics from the server instead of replacing them with a vague local message.

**Data flow**: It receives the original call input and the failed MCP result. It joins any text content into a summary, falls back to a default message if the server wrote no text, and serializes structured error content if present. It returns a `ToolFailure` object describing the refused operation.

**Call relations**: `_call_mcp_tool` calls this when the MCP result says `is_error`. The returned failure is then converted into an untrusted tool result so the agent can see what the outside server reported without treating it as trusted system text.

*Call graph*: calls 1 internal fn (_joined_text); called by 1 (_call_mcp_tool); 2 external calls (__init__, dumps).


##### `_joined_text`  (lines 299–300)

```
def _joined_text(content: Sequence[object]) -> str
```

**Purpose**: This helper gathers text blocks from an MCP response into one string. It ignores non-text blocks because this extension only exposes plain text or structured JSON in its returned result.

**Data flow**: It receives a sequence of content blocks from the MCP client. It keeps only blocks that are MCP text content, takes their text fields, joins them with newlines, and returns the combined string.

**Call relations**: `_call_mcp_tool` uses this for successful responses that do not provide structured JSON. `_call_failed` uses it to extract the server's human-readable error message.

*Call graph*: called by 2 (_call_failed, _call_mcp_tool).


##### `_bounded`  (lines 303–306)

```
def _bounded(text: str) -> str
```

**Purpose**: This helper enforces the maximum allowed response size. It fails loudly rather than silently cutting off data, because a clipped tool result could mislead the agent.

**Data flow**: It receives a text string, measures its encoded byte length, and compares it with the response limit. If it fits, the same text is returned; if it is too large, it raises `McpError`.

**Call relations**: `_json_result` calls this just before wrapping JSON text into a tool result. That means successful listings, schemas, and tool-call outputs all pass through the same response-size gate.

*Call graph*: called by 1 (_json_result); 1 external calls (__init__).


##### `_bounded_schemas`  (lines 309–326)

```
def _bounded_schemas(payload: dict[str, JsonValue], tools: int) -> ToolResult
```

**Purpose**: This function keeps multi-tool schema requests from becoming too large to use. It nudges the agent to ask for fewer schemas when a batch would overflow the normal tool result space.

**Data flow**: It receives a JSON-like payload and the number of schemas inside it. If there is more than one schema and the rendered payload is too large for the listing limit, it raises an error telling the agent to request fewer tools. Otherwise it passes the payload to `_json_result` and returns the resulting tool result.

**Call relations**: `_list_mcp_tools` calls this when the agent asks for full schemas. It then hands off to `_json_result`, which performs the final response byte check used across this file.

*Call graph*: calls 1 internal fn (_json_result); called by 1 (_list_mcp_tools); 1 external calls (dumps).


##### `_json_result`  (lines 329–330)

```
def _json_result(payload: dict[str, JsonValue]) -> ToolResult
```

**Purpose**: This helper converts a JSON-like dictionary into the standard text-based tool result used by the agent. It also applies the response size limit before returning anything.

**Data flow**: It receives a payload dictionary, serializes it to JSON text, passes that text through `_bounded`, wraps the safe text in a `TextContent` object, and returns a `ToolResult` containing it.

**Call relations**: `_list_mcp_tools`, `_bounded_schemas`, and `_call_mcp_tool` use this as the common final packaging step for successful responses. Because it calls `_bounded`, all those paths share the same oversized-response behavior.

*Call graph*: calls 1 internal fn (_bounded); called by 3 (_bounded_schemas, _call_mcp_tool, _list_mcp_tools); 3 external calls (__init__, __init__, dumps).


##### `manifest`  (lines 333–364)

```
def manifest() -> Manifest
```

**Purpose**: This function declares the extension to the host system. It tells the system the extension's name and version, which tools it offers, and which credential slot it needs.

**Data flow**: It creates a `Manifest` containing two tool definitions: `list_mcp_tools` and `call_mcp_tool`, each with its input model, description, handler, and untrusted-content flag. It also creates the `mcp_servers` credential slot and attaches `merge_mcp_server` as the merge function for submitted server settings. The finished manifest is returned.

**Call relations**: The extension loader calls this when registering the file's tool pack. Its returned manifest is what makes `_list_mcp_tools`, `_call_mcp_tool`, and `merge_mcp_server` visible to the rest of the system.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Research access
Connects agent research tools to configured web search and page-fetch providers such as Perplexity.

### `extensions/perplexity/ufo_ext_perplexity.py`

`io_transport` · `request handling`

This file is an adapter between UFO’s search interface and Perplexity’s hosted search API. Without it, the rest of the system could ask for “search results” or “fetch this page,” but it would not know how to speak Perplexity’s specific language: what URL to call, what JSON shape to send, where to put the API key, or how to check the answer.

The main class, PerplexitySearchProvider, works like a travel interpreter. UFO gives it a SearchQuery or FetchRequest in the project’s own format. The provider checks that the request is reasonable, builds the right Perplexity request body, sends it over HTTPS with the Perplexity API key, validates the response, and returns SearchResults or FetchedPage objects that the rest of UFO already understands.

The file also includes small safety limits, such as maximum query length, URL length, number of results, and extracted text size. These protect both the caller and the remote service from overly large requests. For fetching a single page, it asks Perplexity to search within the requested page’s domain, then confirms that the returned result really matches the requested URL after normalizing small differences like trailing slashes or “.html”. Finally, the manifest function tells UFO how to register this extension and what credential it needs.

#### Function details

##### `PerplexitySearchProvider.search`  (lines 72–85)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: Runs a web search through Perplexity and returns the results in UFO’s standard search-result format. A caller uses this when it wants search hits without caring which outside search service is being used.

**Data flow**: It receives a SearchQuery containing the user’s search text, desired result count, optional vertical such as people or academic, domain limits, and date limits. It passes that query to _search_body to make a Perplexity-ready JSON request, sends it with _post, checks the returned data with _response, then converts each Perplexity result into a SearchHit. The final output is a SearchResults object containing those hits.

**Call relations**: This is the public search path for the provider. It relies on _search_body to translate UFO’s request into Perplexity’s request format, _post to do the network call, and _response to make sure the returned data has the expected shape before handing clean SearchResults back to the rest of the system.

*Call graph*: calls 3 internal fn (_post, _response, _search_body); 2 external calls (__init__, __init__).


##### `PerplexitySearchProvider.fetch`  (lines 87–131)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: Extracts text for one specific web page using Perplexity. It is used when UFO already has a URL and wants readable page content, optionally guided by a short extraction prompt.

**Data flow**: It receives a FetchRequest with a URL, optional prompt, and optional character limit. It first rejects unsafe or unsupported inputs, such as very long URLs, non-HTTP URLs, negative limits, or too-long prompts. It then builds a Perplexity search request focused on the URL’s domain, sends it with _post, validates the reply with _response, and looks for a returned result that matches the requested page after URL cleanup by _canonical_page. If it finds one, it trims the snippet to the requested size and returns a FetchedPage; if not, it raises a PerplexityError.

**Call relations**: This is the provider’s page-fetch path. It uses urlsplit to understand the requested URL, _post for the Perplexity call, _response to verify the answer, and _canonical_page to avoid being fooled by minor URL spelling differences. It hands back a FetchedPage to callers that need page text.

*Call graph*: calls 3 internal fn (_post, _response, _canonical_page); 3 external calls (__init__, __init__, urlsplit).


##### `PerplexitySearchProvider._search_body`  (lines 134–159)

```
def _search_body(query: SearchQuery) -> dict[str, Json]
```

**Purpose**: Builds the JSON request body that Perplexity expects for a search. It keeps the rest of the search code from needing to know Perplexity’s field names and limits.

**Data flow**: It receives a SearchQuery. It checks that at least one result was requested, caps the requested result count, adds a plain-language qualifier for special search verticals like academic or image, checks the final query length, and adds optional domain and date filters. It returns a dictionary ready to be sent as JSON to Perplexity.

**Call relations**: PerplexitySearchProvider.search calls this before making the network request. When date filters are present, this function hands each date to _api_date so Perplexity receives dates in the format it expects. If the query is invalid, it stops the flow early with a PerplexityError.

*Call graph*: calls 1 internal fn (_api_date); called by 1 (search); 1 external calls (__init__).


##### `PerplexitySearchProvider._response`  (lines 162–166)

```
def _response(payload: object) -> _PerplexitySearchResponse
```

**Purpose**: Checks that Perplexity’s response has the result structure this provider expects. This prevents confusing or malformed remote data from leaking into the rest of UFO.

**Data flow**: It receives raw data decoded from Perplexity’s JSON response. It asks the local response model to validate that the data contains a results list with items that have fields such as URL, title, and snippet. If validation succeeds, it returns a typed _PerplexitySearchResponse; if validation fails, it raises a PerplexityError.

**Call relations**: Both search and fetch call this after _post returns data. It acts as the quality-control step between the outside API and UFO’s internal result objects.

*Call graph*: called by 2 (fetch, search); 1 external calls (__init__).


##### `PerplexitySearchProvider._post`  (lines 168–188)

```
async def _post(self, body: dict[str, Json]) -> object
```

**Purpose**: Sends one authenticated request to Perplexity’s /search endpoint and returns the decoded JSON answer. It centralizes the network details so search and fetch do not duplicate them.

**Data flow**: It receives a JSON-ready request body. It asks the credential store for the Perplexity API key, opens an asynchronous HTTP client pointed at Perplexity, sends the body to /search with a Bearer authorization header, and waits for the response. If Perplexity returns an error status or invalid JSON, it raises a PerplexityError; otherwise it returns the decoded JSON data.

**Call relations**: PerplexitySearchProvider.search and PerplexitySearchProvider.fetch both depend on this function for the actual internet call. It uses httpx.AsyncClient as the HTTP transport, and it is the place where credentials, timeout, endpoint path, and error reporting come together.

*Call graph*: called by 2 (fetch, search); 2 external calls (__init__, AsyncClient).


##### `_api_date`  (lines 191–192)

```
def _api_date(value: date) -> str
```

**Purpose**: Formats a Python date into the date text format Perplexity expects. It exists so date filters are sent consistently.

**Data flow**: It receives a date value. It formats that date as month/day/year text, such as 03/15/2024. The output is a string that can be placed directly into the Perplexity request body.

**Call relations**: PerplexitySearchProvider._search_body calls this when the search query includes a start or end publication date. It is a small translation step inside the larger request-building process.

*Call graph*: called by 1 (_search_body); 1 external calls (strftime).


##### `_canonical_page`  (lines 195–202)

```
def _canonical_page(value: str) -> tuple[str | None, str, str]
```

**Purpose**: Normalizes a URL enough to compare whether two URLs point to the same page. This helps fetch confirm that Perplexity returned the exact page that was requested, despite small URL style differences.

**Data flow**: It receives a URL string. It splits the URL into parts, removes a trailing slash from the path, and strips common page suffixes like .html, .htm, or .txt. It returns a tuple containing the hostname, cleaned path, and query string, which can be compared with another normalized URL.

**Call relations**: PerplexitySearchProvider.fetch uses this on both the requested URL and each returned result URL. That lets fetch decide whether Perplexity’s result is really the requested page before returning a FetchedPage.

*Call graph*: called by 1 (fetch); 1 external calls (urlsplit).


##### `manifest`  (lines 205–225)

```
def manifest() -> Manifest
```

**Purpose**: Tells UFO how to discover and load this Perplexity extension. It declares the needed API-key credential and registers Perplexity as a search backend.

**Data flow**: It takes no input from the caller. It creates a Manifest containing the extension name and version, a credential slot named for the Perplexity API key, and a search provider specification that knows how to build a PerplexitySearchProvider when credentials are available. It returns that Manifest to the host system.

**Call relations**: This is called by UFO’s extension-loading machinery, not by normal search requests. It wires the Perplexity provider into the larger system so later searches and fetches can construct PerplexitySearchProvider with the right credential access.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/research/ufo_ext_research/tools.py`

`orchestration` · `tool request handling`

This file gives the system three research abilities: broad web search, fetching one URL, and searching a specific kind of content such as videos, products, or academic papers. Without it, an agent could not ask the project’s search backend for outside information in a safe, consistent way.

The file first describes what inputs each tool accepts. These input models act like a form with rules: web search can take several natural-language queries, optional date filters, and allowed domains; URL fetching takes a public HTTP or HTTPS address; vertical search requires a known category. These rules help keep tool use predictable and discourage fragile tricks like putting site filters inside the query text.

The actual work is done by the turn’s selected SearchProvider, meaning the search service is chosen outside this file. That matters because API keys and crawler identity stay on the host side, not inside the agent’s sandbox. Search results and fetched pages are also recorded as observations when an extension context is available, so the system can keep track of what outside information was shown.

One important warning is built into fetched pages: page content comes from the provider’s crawler session, not the user’s workspace. Like borrowing someone else’s library card, any logged-in view belongs to the crawler, not to the agent or user.

#### Function details

##### `_provider`  (lines 133–136)

```
def _provider(ctx: ToolContext) -> SearchProvider
```

**Purpose**: This function finds the search provider chosen for the current turn. It fails loudly if no provider was configured, because the research tools cannot work without a backend service to answer them.

**Data flow**: It receives the current tool context, looks inside it for a search provider, and either returns that provider or raises an error saying none is configured. It does not change any data; it simply checks that the needed service exists before the rest of the tool continues.

**Call relations**: The three tool handlers call this first when they need outside information. If it returns a provider, they move on to searching or fetching; if it raises an error, the tool call stops instead of quietly producing misleading empty results.

*Call graph*: called by 3 (_fetch_url, _search_vertical, _search_web).


##### `_results_json`  (lines 139–153)

```
def _results_json(hits: list[SearchHit], answer: str | None) -> str
```

**Purpose**: This function turns search hits into a JSON text response that the agent can read. It keeps the output shape consistent for both normal web search and vertical search.

**Data flow**: It receives a list of search hits and an optional direct answer. For each hit, it copies the useful public fields such as URL, title, snippet text, published date, and highlights into plain dictionaries. It then packages those into a JSON string, adding the answer only when one was provided.

**Call relations**: The web search and vertical search handlers use this after the provider returns results. It is the final packaging step before those handlers place the JSON text into a ToolResult for the agent.

*Call graph*: called by 2 (_search_vertical, _search_web); 1 external calls (dumps).


##### `_search_web`  (lines 156–174)

```
async def _search_web(ctx: ToolContext, args: SearchWebInput) -> ToolResult
```

**Purpose**: This is the handler for the general web search tool. It lets the agent run one or more distinct web searches, combines the results, records what was found when possible, and returns the combined result as text.

**Data flow**: It receives the tool context and validated search arguments. It gets the configured provider, then sends each query to that provider with the requested result count, publication dates, and allowed domains. It gathers all returned hits into one list, keeps the first direct answer if the provider gives one, optionally records the hits for later observation, and returns a tool result containing JSON text.

**Call relations**: The tool system calls this when the agent invokes search_web. Inside the flow, it depends on _provider to get the backend, builds SearchQuery objects to describe each search request, hands those to the provider, then uses _results_json to turn the collected results into the response.

*Call graph*: calls 2 internal fn (_provider, _results_json); 4 external calls (__init__, __init__, __init__, record_search_hits).


##### `_fetch_url`  (lines 177–198)

```
async def _fetch_url(ctx: ToolContext, args: FetchUrlInput) -> ToolResult
```

**Purpose**: This is the handler for the URL fetch tool. It reads a public web page through the search provider’s crawler, optionally asks for extraction or summarization, and returns the page content with a clear warning about crawler provenance.

**Data flow**: It receives the tool context and validated fetch arguments. It gets the provider and first checks whether that provider can fetch URLs. If not, it returns an error message telling the agent to use another route. If fetching is supported, it sends a FetchRequest with the URL, optional prompt, length limit, and cache-bypass choice. It optionally records the fetched page, then returns JSON containing the final URL, page text, provenance warning, and summary if one exists.

**Call relations**: The tool system calls this when the agent invokes fetch_url. It uses _provider to find the backend, hands the fetch request to that backend, and records the fetched page through the observations layer when available. Unlike the search handlers, it builds its response directly because fetched pages have a different shape from search results.

*Call graph*: calls 1 internal fn (_provider); 5 external calls (__init__, __init__, __init__, dumps, record_fetched_page).


##### `_search_vertical`  (lines 201–210)

```
async def _search_vertical(ctx: ToolContext, args: SearchVerticalInput) -> ToolResult
```

**Purpose**: This is the handler for searching a specialized content category, such as images, people, academic papers, videos, or shopping listings. It gives the provider both the query and the requested category so the backend can search the right kind of index.

**Data flow**: It receives the tool context and validated vertical-search arguments. It gets the configured provider, creates a search query with the requested vertical and the default number of results, and sends it to the provider. It optionally records the returned hits, then returns the results as JSON text.

**Call relations**: The tool system calls this when the agent invokes search_vertical. It follows the same broad path as _search_web: get the provider, ask it for results, record observations when possible, and use _results_json to format the answer. The difference is that it folds the chosen content type into the provider request.

*Call graph*: calls 2 internal fn (_provider, _results_json); 4 external calls (__init__, __init__, __init__, record_search_hits).

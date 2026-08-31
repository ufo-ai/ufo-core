# Cross-cutting security, authorization, credentials, and network policy  `stage-20` (cross-cutting infrastructure)

This stage is the system’s safety layer. It is not one step in the main work loop; it is checked whenever the system starts work, serves a page, runs an agent, opens a file, calls the network, or cleans up. token_signing.py is the tamper-evident seal maker for small signed messages. auth/bearer.py checks member login tokens, surface_token.py signs link-based route claims, and sdk/bearer.py lets extensions verify tokens without learning signing secrets. workspace.py and agent_scope.py keep the current workspace and acting agent tied to each decision. seats.py, web/audience.py, turns/audience.py, and turns/subjects.py decide who may talk, see agents, or read conversation items; turns/untrusted.py labels outside text as evidence, not instructions. credentials.py stores secrets encrypted and governs who may fill or use them; github_app.py and sources/direct.py turn approved grants or API keys into provider access. egress_resolver.py, egress_rules.py, and egress_control.py translate those permissions into proxy rules for outbound network calls and secret injection. artifact_url.py signs temporary downloads, image_previews.py rejects unsafe preview files, containment.py keeps file paths inside the sandbox, and ext/operator.py protects trusted operator tools.

## Files in this stage

### Outbound egress policy
These files derive and expose the per-run network, secret-injection, and billing rules used by the sandbox egress proxy.

### `core/src/ufo/runtime/access/egress_resolver.py`

`domain_logic` · `request handling`

This file is the policy brain behind egress control, meaning control over what an agent may connect to outside its sandbox. The actual proxy that sees network connections asks this code for rules; the proxy does not know how to read workspace secrets or decide which agent owns which permissions.

The central class, PerAgentRules, starts with a safe base set of rules. When given a signed principal token, it looks up which agent that token belongs to inside the token's workspace. A run token points to a turn. A probe token points to a conversation. If the lookup fails, the code falls back to the base rules rather than guessing.

Once it knows the agent, it adds permissions in layers: general internet rules only if that agent is allowed internet access, internal service routes, package-cache routes, preview access, workspace credential injections, OAuth grant forwarding, and command-line connector credentials. Think of it like building a visitor badge: the default badge opens only basic doors, then extra stickers are added only after checking who the visitor is and what room they are serving.

There are two important safety details. Probe tokens deliberately lose the deployment's own model-key injection, so probes cannot silently borrow the platform's model credential. Also, turn_live checks the database fresh before allowing a live connection, so a finished or missing turn cannot keep using secrets through an old token.

#### Function details

##### `PerAgentRules.resolve`  (lines 81–135)

```
async def resolve(self, principal: EgressPrincipal | None) -> tuple[Rule, ...]
```

**Purpose**: Builds the complete network rule list for one run token, probe token, or no token. It is used when the proxy needs to know exactly what a sandboxed agent may connect to and what credentials may be injected.

**Data flow**: It receives an optional principal token. With no token, it returns only the base rules. With a run or probe token, it reads the token's workspace and asks the database which agent and internet policy apply, then adds allowed rule layers such as service access, preview access, workspace credentials, OAuth grants, and command-line connector forwarding. For probe tokens, it removes the model-key injection before returning the final tuple of rules.

**Call relations**: This is the main entry point in the file. It calls _turn_of for run tokens and _conversation_of for probe tokens to learn who the token represents. It then calls helper rule builders such as derive_credential_rules, derive_grant_rules, and derive_cli_rules to turn stored credentials and grants into enforceable rules, and calls _without_the_model_key at the end for probes.

*Call graph*: calls 3 internal fn (_conversation_of, _turn_of, _without_the_model_key); 7 external calls (__init__, __init__, derive_cli_rules, derive_credential_rules, derive_grant_rules, agent, ws).


##### `PerAgentRules._turn_of`  (lines 137–161)

```
async def _turn_of(self, run: RunToken) -> _Authority | None
```

**Purpose**: Finds the authority carried by a run token: which agent owns the turn, whether that agent was allowed internet access, and which member's private grants may be used. This prevents one run from borrowing another agent's permissions.

**Data flow**: It receives a RunToken containing a workspace id, turn id, and acting member id. Inside that workspace, it queries the turn and agent tables together. If the matching turn and agent exist, it returns an _Authority record with the agent id, internet-access flag, and acting member id; otherwise it returns None.

**Call relations**: PerAgentRules.resolve calls this when the proxy presents a run token. The result tells resolve which agent scope to use and whether internet-related rules may be added.

*Call graph*: called by 1 (resolve); 3 external calls (__init__, select, workspace_tx).


##### `PerAgentRules._conversation_of`  (lines 163–192)

```
async def _conversation_of(self, probe: ProbeToken) -> _Authority | None
```

**Purpose**: Finds the authority carried by a probe token, using the conversation because a probe is not tied to a specific turn. This lets probes inherit the right agent context without giving them unrelated permissions.

**Data flow**: It receives a ProbeToken containing a workspace id, conversation id, and acting member id. It queries the conversation and agent tables in that workspace. If the conversation exists and belongs to an agent in the same workspace, it returns an _Authority record; if not, it returns None.

**Call relations**: PerAgentRules.resolve calls this when the proxy presents a probe token. The returned authority is then used the same way as a run's authority, except that probe rules are later filtered to remove the deployment model key.

*Call graph*: called by 1 (resolve); 3 external calls (__init__, select, workspace_tx).


##### `PerAgentRules._without_the_model_key`  (lines 194–205)

```
def _without_the_model_key(self, rules: tuple[Rule, ...]) -> tuple[Rule, ...]
```

**Purpose**: Removes any rule that would inject the deployment's own model key. It is used so probe executions can reach model hosts only without silently receiving the platform's private model credential.

**Data flow**: It receives a tuple of already-built rules. It scans them and keeps every rule except an InjectionRule whose sentinel refers to the model key. It returns a new tuple with that sensitive injection removed and leaves the original rules unchanged.

**Call relations**: PerAgentRules.resolve calls this only for ProbeToken resolution, after all normal rules have been assembled. This makes probe behavior mostly match turn behavior while enforcing the special model-key restriction at the last step.

*Call graph*: called by 1 (resolve).


##### `PerAgentRules.turn_live`  (lines 207–238)

```
async def turn_live(self, run: RunToken) -> int | None
```

**Purpose**: Checks whether a run token still names a turn that is currently running. It is the live safety gate used before a connection may receive sensitive injected credentials.

**Data flow**: It receives a RunToken. In that token's workspace, it reads the turn's status and the workspace's egress-rules generation counter from the database. If the turn does not exist or is not marked running, it returns None. If the turn is running, it returns the current generation number, which tells callers which version of the rules they are authorizing against.

**Call relations**: This method is separate from resolve because connection authorization must be checked fresh at connect time, not only when rules were first built. It uses workspace_tx and the workspace scope directly to make that decision from current database state.

*Call graph*: 3 external calls (select, workspace_tx, ws).


##### `PerAgentRules.rules_generation`  (lines 240–251)

```
async def rules_generation(self, workspace_id: UUID) -> int
```

**Purpose**: Reads the current egress-rules generation counter for a workspace. The counter is a simple version number that changes when rule inputs change, so callers can tell whether cached rules are still current.

**Data flow**: It receives a workspace id. It enters that workspace's database scope, queries the workspace row, and returns the current egress_rules_generation integer.

**Call relations**: This is the probe-side companion to turn_live: run-token checks can get the generation while proving the turn is live, but probe paths need a direct way to read the workspace's current rule version.

*Call graph*: 3 external calls (select, workspace_tx, ws).


### `core/src/ufo/runtime/access/egress_control.py`

`io_transport` · `request handling`

The egress proxy is the part that sits on the network path, but it is deliberately kept simple and untrusted with secrets. This file is the proxy’s back office. When the proxy sees a sandbox trying to connect outward, it calls these internal routes to check whether the run or probe token is valid, fetch the current egress rules, forward approved connector requests, and report usage for billing. Without this file, the proxy would either need to know customer keys and policy rules itself, or it could not safely decide what traffic to allow.

The main class, `EgressControl`, builds FastAPI routers. FastAPI is the web framework that turns Python functions into HTTP endpoints. One router is for `/internal/egress/*` and is protected by a shared bearer token, like a staff-only badge at a service desk. A second, separate router is only for git credentials used by a cache daemon, protected by its own token so that credential cannot be used to reach the broader secrets-and-metering API.

Requests carry a `proxy_auth` value, which is the sandbox run or probe token. `_principal` verifies that token and turns it into a trusted identity. From there, the file asks the rules resolver for allowed hosts and secret-injection rules, records egress and token usage, checks connector ownership before forwarding, and looks up git credentials only for the exact workspace and host requested.

#### Function details

##### `rule_json`  (lines 55–84)

```
def rule_json(rule: Rule) -> dict[str, object]
```

**Purpose**: This converts one internal egress rule into the exact JSON shape that the Rust proxy understands. It is used so Python and Rust agree on what each rule means, such as allowing a host, injecting a header, metering a host, or forwarding through a connector.

**Data flow**: A rule object goes in. The function looks at which kind of rule it is and builds a plain dictionary with a `kind` label and the fields the proxy needs. The output is JSON-ready data; for forwarding rules, it leaves out Python-only behavior and sends only the identifying information the proxy can enforce.

**Call relations**: `EgressControl._resolve` calls this after it has obtained the policy rules for a run or probe. The converted rules are then returned to the Rust proxy, which enforces them on live network traffic.

*Call graph*: called by 1 (_resolve).


##### `EgressControl.router`  (lines 178–185)

```
def router(self) -> APIRouter
```

**Purpose**: This builds the private `/internal/egress` API used by the Rust proxy. It attaches the shared-token guard to every route so unauthorized callers are rejected before reaching sensitive logic.

**Data flow**: The `EgressControl` object already contains the resolver, billing helpers, credentials, and tokens. This method creates a FastAPI router, registers the authorize, resolve, meter, forward, and tool-bridge endpoints, and returns that router for the main server to mount.

**Call relations**: The application setup code calls this when assembling the server. After that, incoming proxy requests flow through `_guard` first, then into the specific endpoint method for the requested path.

*Call graph*: 2 external calls (APIRouter, Depends).


##### `EgressControl.git_credential_router`  (lines 187–192)

```
def git_credential_router(self) -> APIRouter
```

**Purpose**: This builds a separate private API for the cache daemon to request git credentials. It intentionally uses a different guard token so the cache daemon can ask only for git credentials and cannot use the wider egress control API.

**Data flow**: The method creates a FastAPI router under `/internal`, protects it with `_cache_guard`, registers the `/git-credential` route, and returns the router to be mounted by the server.

**Call relations**: Server setup calls this alongside the main egress router. Later, cache-daemon requests pass through `_cache_guard` and then into `_git_credential`.

*Call graph*: 2 external calls (APIRouter, Depends).


##### `EgressControl._guard`  (lines 194–196)

```
async def _guard(self, authorization: Annotated[str, Header()]='') -> None
```

**Purpose**: This checks that a request to the main egress control API carries the expected bearer token. It is the first gate that keeps outsiders from asking for rules, secrets, forwarding, or billing writes.

**Data flow**: The HTTP `Authorization` header goes in. The function compares it with `Bearer <control_token>`. If it matches, nothing is returned and the request may continue; if it does not match, it raises a 401 unauthorized HTTP error.

**Call relations**: FastAPI runs this automatically for routes created by `EgressControl.router`. If it passes, the request continues to endpoints such as `_authorize`, `_resolve`, `_meter`, `_forward`, or `_tool_bridge`.

*Call graph*: 1 external calls (HTTPException).


##### `EgressControl._cache_guard`  (lines 198–200)

```
async def _cache_guard(self, authorization: Annotated[str, Header()]='') -> None
```

**Purpose**: This checks that a request to the git credential callback carries the cache daemon’s separate bearer token. It prevents that narrower credential from being confused with the more powerful egress-control token.

**Data flow**: The HTTP `Authorization` header goes in. The function compares it with `Bearer <cache_control_token>`. A match allows the request to continue; a mismatch becomes a 401 unauthorized HTTP error.

**Call relations**: FastAPI runs this automatically for the route created by `EgressControl.git_credential_router`. Only after it passes can `_git_credential` run.

*Call graph*: 1 external calls (HTTPException).


##### `EgressControl._authorize`  (lines 202–213)

```
async def _authorize(self, body: AuthorizeRequest) -> AuthorizeResponse
```

**Purpose**: This answers the proxy’s basic question: “Is this sandbox token allowed to make egress decisions right now?” It also returns a rule generation number so the proxy can know whether its cached rules are current.

**Data flow**: An authorization request with `proxy_auth` goes in. The function verifies the token with `_principal`. For a run token, it asks the resolver whether the turn is still live. For a probe token, it checks the expiry time and then reads the workspace’s rules generation. The output says whether the request is authorized and, when available, includes the current generation number.

**Call relations**: The Rust proxy calls this before allowing or continuing network activity. `_authorize` depends on `_principal` to turn the raw token into a trusted run or probe identity, then asks the resolver for liveness or generation information.

*Call graph*: calls 1 internal fn (_principal); 2 external calls (__init__, now).


##### `EgressControl._resolve`  (lines 215–217)

```
async def _resolve(self, body: ResolveRequest) -> dict[str, object]
```

**Purpose**: This gives the proxy the concrete egress rules for a verified run or probe. These are the rules the proxy will enforce on network connections.

**Data flow**: A resolve request with `proxy_auth` goes in. The function verifies the token through `_principal`, asks the resolver for the rules that apply to that identity, converts each rule with `rule_json`, and returns a dictionary containing the rule list.

**Call relations**: The proxy calls this when it needs the policy rules, often after authorization or when its cached generation is stale. `_resolve` hands internal rule objects to `rule_json` so the response is in the Rust proxy’s expected format.

*Call graph*: calls 2 internal fn (_principal, rule_json).


##### `EgressControl._meter`  (lines 219–274)

```
async def _meter(self, body: MeterRequest) -> dict[str, object]
```

**Purpose**: This receives usage reports from the proxy and records them for billing and observability. It combines repeated records before writing, so one request can efficiently account for many events.

**Data flow**: A batch of metering records goes in. The function groups egress counts by workspace and turn, groups token usage by workspace, turn, and model, and separately counts host-and-dimension metrics. It emits metric counters, opens the correct workspace context, writes probe egress, run egress, and token usage to the accounting system, then returns an empty response.

**Call relations**: The Rust proxy calls this after observing traffic or model-token usage. When token usage includes cache-write tokens, `_meter` calls `_priced_cache_write` before passing the result to `record_sandbox_tokens`, so billing matches the pricing data core knows about.

*Call graph*: calls 1 internal fn (_priced_cache_write); 7 external calls (__init__, record_egress_request, record_probe_egress_request, record_sandbox_tokens, workspace_tx, emit_metric, ws).


##### `EgressControl._priced_cache_write`  (lines 276–289)

```
def _priced_cache_write(self, model: str, usage: Usage) -> Usage
```

**Purpose**: This adjusts token usage for models that do not have a priced 30-minute cache-write tier. In that case, those cache-write tokens are billed like normal input tokens instead.

**Data flow**: A model name and a `Usage` object go in. The function looks up the model’s price information. If the model prices 30-minute cache writes, or there are no such tokens, the usage is returned unchanged. Otherwise, those tokens are moved into regular input tokens and the 30-minute cache-write count is set to zero.

**Call relations**: `EgressControl._meter` calls this just before recording sandbox token usage. It is the bridge between what the proxy observed on the wire and how core’s pricing table says the usage should be billed.

*Call graph*: called by 1 (_meter); 1 external calls (model_copy).


##### `EgressControl._forward`  (lines 291–325)

```
async def _forward(self, body: ForwardRequest) -> ForwardResponse
```

**Purpose**: This lets the proxy forward an approved request through a connector account without giving the proxy the connector’s private credentials. It checks that the run or probe is allowed to use the requested account before forwarding.

**Data flow**: A forward request goes in with `proxy_auth`, account id, HTTP method, URL, headers, and a base64-encoded body. The function verifies the token, looks up the account inside the workspace database, checks that the account is shared or owned by the acting member, finds the matching connector client, decodes the body, sends the request through the connector forwarder, then returns status, headers, and a base64-encoded response body. If any permission or connector check fails, it returns a forbidden HTTP error.

**Call relations**: The Rust proxy calls this when an egress rule says a request should be forwarded through a brokered connector. `_forward` uses `_principal` for identity, the database for account ownership, and the configured CLI connector map to perform the actual forwarded request.

*Call graph*: calls 1 internal fn (_principal); 7 external calls (__init__, b64decode, b64encode, HTTPException, select, workspace_tx, ws).


##### `EgressControl._tool_bridge`  (lines 327–332)

```
async def _tool_bridge(self, body: ToolBridgeControlRequest) -> ToolBridgeResponse
```

**Purpose**: This lets a live run use the host-side tool bridge through a bounded JSON request. It rejects probe tokens and requests when no bridge is configured.

**Data flow**: A tool-bridge control request goes in with `proxy_auth` and a tool request body. The function verifies the token, checks that the result is specifically a run token and that a bridge exists, enters the run’s workspace context, and sends the request to the bridge. The bridge response comes back as the HTTP response; invalid cases become forbidden errors.

**Call relations**: The proxy calls this for tool-bridge traffic. `_tool_bridge` relies on `_principal` to prove the caller is a real run, then hands the work to the configured `ToolBridgeRequester`.

*Call graph*: calls 1 internal fn (_principal); 2 external calls (HTTPException, ws).


##### `EgressControl._git_credential`  (lines 334–348)

```
async def _git_credential(self, body: GitCredentialRequest) -> dict[str, object]
```

**Purpose**: This is the cache daemon’s narrow callback for getting a git credential for one workspace and one host. If there is no matching credential, it tells the daemon to proceed as a public, anonymous fetch rather than leaking another credential.

**Data flow**: A request may provide a workspace id and host. If either is missing, or credential resolution is unavailable, the function returns a public principal. Otherwise it enters that workspace and asks `_git_credential_for` for the exact host credential. If none is found, it returns no credential and a public principal; if one is found, it returns the username, token, and a workspace-specific principal label.

**Call relations**: Requests reach this function only through the separate router protected by `_cache_guard`. It delegates the careful slot-by-slot lookup to `_git_credential_for` and formats the answer for the cache daemon.

*Call graph*: calls 1 internal fn (_git_credential_for); 1 external calls (ws).


##### `EgressControl._git_credential_for`  (lines 350–376)

```
async def _git_credential_for(self, workspace_id: UUID, host: str) -> tuple[str, str] | None
```

**Purpose**: This searches the workspace’s configured credential slots for the git basic-auth secret that matches the requested host. It is careful to skip broken or non-matching slots instead of falling through to the wrong identity.

**Data flow**: A workspace id and host go in. The function walks the resolver’s credential slots, ignores slots that are not git-basic-user injections, checks whether each slot is set, checks whether its stored host equals the requested host, and then reads the secret. If a slot lookup fails, it logs a warning and continues. The output is either `(username, secret)` for the matching slot or `None`.

**Call relations**: `EgressControl._git_credential` calls this after entering the requested workspace context. This helper uses credential-reading utilities and warning logging, then hands the resolved credential back to the public callback response.

*Call graph*: called by 1 (_git_credential); 4 external calls (credential_host, slot_is_set, slot_secret, warn).


##### `EgressControl._principal`  (lines 378–388)

```
def _principal(self, proxy_auth: str) -> EgressPrincipal | None
```

**Purpose**: This verifies the raw proxy authorization value and turns it into a trusted identity object. It accepts either a run token or a probe token, and returns nothing if neither verification succeeds.

**Data flow**: A raw `proxy_auth` string goes in. If it is empty, the function returns `None`. Otherwise it first tries to parse it as a run token using the configured run-token codec. If that fails, it tries to parse it as a probe token using the same secret. The output is a run token, a probe token, or `None`.

**Call relations**: `_authorize`, `_resolve`, `_forward`, and `_tool_bridge` call this before making decisions based on the caller’s workspace or member identity. It is the shared doorway from an untrusted string in a request body to a trusted principal used by the rest of this file.

*Call graph*: called by 4 (_authorize, _forward, _resolve, _tool_bridge); 1 external calls (__init__).


### `core/src/ufo/runtime/access/egress_rules.py`

`domain_logic` · `turn setup / request handling`

A sandbox is meant to be restricted: it should not freely call any website, and it should not be handed raw secrets unless absolutely necessary. This file builds the rulebook used by the egress proxy, the component that sits between the sandbox and the outside network. Think of it like a security desk that checks both the destination and the paperwork before letting a request leave the building.

The rules are derived from existing facts rather than registered as a separate API. If a model is OpenAI or Anthropic, the proxy allows that provider host, swaps a harmless placeholder key for the real API key, and meters usage as tokens. If an extension asks for public internet, the file adds a broader internet rule. If the artifact store is S3, it allows exactly the S3 host needed to upload shared files. If a workspace has credential slots, it looks up each secret and host, creates safe header-injection rules, and meters the allowed host. If a connector grant is active, it allows the provider and file-transfer hosts but does not inject credentials, because the broker performs those actions server-side. CLI-style connector credentials are forwarded through the broker in the same spirit.

The important safety idea is that missing or broken optional credentials do not crash the whole rule derivation. A bad credential slot is skipped and logged, so one unreadable secret does not accidentally block all other egress decisions.

#### Function details

##### `provider_host`  (lines 112–116)

```
def provider_host(model: str) -> str
```

**Purpose**: This function identifies which network host belongs to a model name. For example, model names beginning with OpenAI-style prefixes map to OpenAI's API host, while Claude-style names map to Anthropic.

**Data flow**: It receives a model name as text. It checks the known model-name prefixes one by one. If a prefix matches, it returns the matching provider host; if none match, it raises an error because the system does not know where that model should send requests.

**Call relations**: It is used by derive_model_rules when building the network rules for the selected model. That caller needs the host first so it can allow the right destination, inject the right kind of authorization header, and meter usage.

*Call graph*: called by 1 (derive_model_rules).


##### `derive_model_rules`  (lines 119–134)

```
def derive_model_rules(model: str, real_key: str) -> tuple[Rule, ...]
```

**Purpose**: This function creates the egress rules needed for the sandbox to call the deploy's AI model provider. It allows the provider host, arranges for the real model API key to replace the sandbox's placeholder key, and marks the traffic for token metering.

**Data flow**: It receives a model name and the real API key. It asks provider_host which host owns that model, chooses the correct authorization header format for that host, then returns a small bundle of rules: one allowlist rule, one secret-injection rule, and one metering rule.

**Call relations**: This is part of the rule-building phase before model traffic is allowed. It delegates host selection to provider_host, then constructs the specific rules the proxy will later read while processing outbound model requests.

*Call graph*: calls 1 internal fn (provider_host); 3 external calls (__init__, __init__, __init__).


##### `derive_manifest_rules`  (lines 137–139)

```
def derive_manifest_rules(manifests: tuple[Manifest, ...]) -> tuple[InternetRule, ...]
```

**Purpose**: This function checks whether any installed extension has declared that the sandbox needs public internet access. If so, it adds the rule that permits live turns to reach the public internet.

**Data flow**: It receives the extension manifests. It scans them for a sandbox_internet flag. If at least one manifest asks for internet, it returns an InternetRule; otherwise it returns no rules.

**Call relations**: This fits into the wider egress rule derivation for a turn or deploy. Other functions create exact host permissions, while this one is the place where a broader internet permission is added only when the manifests explicitly require it.

*Call graph*: 1 external calls (__init__).


##### `derive_artifact_store_rules`  (lines 142–158)

```
async def derive_artifact_store_rules(blob: FilesystemBlobStore | S3BlobStore) -> tuple[Rule, ...]
```

**Purpose**: This function allows the sandbox to upload shared artifacts when the artifact store is S3. Without it, a file produced inside the sandbox could be given a presigned S3 URL but still be blocked from connecting to the S3 host.

**Data flow**: It receives the blob store configuration. If the store is S3, it asks the store for the host used for uploads and returns rules allowing that exact host and metering requests to it. If the store is local filesystem storage, there is no network host to allow, so it returns no rules.

**Call relations**: It is called during egress rule construction for file-sharing support. It relies on the blob store to reveal the needed upload host, then hands the proxy exact allow and metering rules rather than granting broad internet access.

*Call graph*: 3 external calls (__init__, __init__, put_host).


##### `derive_credential_rules`  (lines 161–224)

```
async def derive_credential_rules(slots: tuple[CredentialSlot, ...], workspace_id: UUID, store: CredentialStore) -> tuple[Rule, ...]
```

**Purpose**: This function turns workspace credential slots into safe network permissions. For each usable credential, it allows the right host, prepares a rule to replace a placeholder value with the real secret on the wire, and optionally meters that host.

**Data flow**: It receives credential slot declarations, the workspace ID, and the credential store. For each slot that actually injects a credential, it tries to read the secret and resolve the selected host for this workspace. If anything fails, it logs a warning and skips that slot. If a slot is valid, it creates an injection rule; for Git basic authentication it first combines the username and secret into the encoded Basic header value. Finally, it groups rules by host so each host is allowed and metered once, then returns the complete rule tuple.

**Call relations**: This is one of the main rule derivation steps for workspace-specific access. It calls the credential helpers to fetch secrets and hosts, logs through warn when a slot cannot be used, and produces ScopeRule, InjectionRule, and MeterRule objects for the proxy to enforce later.

*Call graph*: 7 external calls (__init__, __init__, __init__, b64encode, credential_host, slot_secret, warn).


##### `derive_grant_rules`  (lines 227–244)

```
def derive_grant_rules(grants: tuple[Grant, ...], transfer_hosts: 'ConnectorTransferHosts | None'=None) -> tuple[Rule, ...]
```

**Purpose**: This function creates network permissions from active connector grants. A grant can allow the sandbox to reach the connector provider's host and any broker file-transfer hosts, while metering each request, but it does not expose the account token to the sandbox.

**Data flow**: It receives the active grants and, optionally, a ConnectorTransferHosts lookup. For each grant, it combines the provider host with any extra transfer hosts, removes blanks and duplicates, then returns an allowlist rule plus request-metering rules for those hosts.

**Call relations**: This is used when connector access is being folded into the sandbox's egress rulebook. If transfer host information is available, it asks ConnectorTransferHosts.of for provider-specific extra hosts before creating the rules the proxy will read.

*Call graph*: 2 external calls (__init__, __init__).


##### `derive_cli_rules`  (lines 247–267)

```
def derive_cli_rules(grants: tuple[Grant, ...], acting_member_id: UUID | None, clis: Mapping[str, CliCredential]) -> tuple[Rule, ...]
```

**Purpose**: This function builds forwarding rules for connector CLI credentials. These rules let requests carrying a grant placeholder be executed through the broker, so the real credential stays server-side instead of entering the deploy or sandbox.

**Data flow**: It receives grants, the acting member ID if there is one, and a map of CLI credential declarations by provider. It keeps only grants whose provider has a CLI credential and whose account the acting member is allowed to use: either a shared connection or that member's own grant. For each allowed grant, it creates a forwarding rule with the host, header, placeholder value, account ID, and broker forwarding object.

**Call relations**: This complements derive_grant_rules. Grant rules allow and meter hosts; this function adds the special path for authenticated CLI-style requests by using grant_sentinel to recognize the placeholder and ForwardRule to tell the proxy to send the request through the broker.

*Call graph*: 2 external calls (__init__, grant_sentinel).


##### `ConnectorTransferHosts.of`  (lines 281–282)

```
def of(self, provider: str) -> tuple[str, ...]
```

**Purpose**: This method looks up which broker file-transfer hosts belong to a connector provider. It gives registered connectors their explicit host list, and falls back to a default only for providers that were not explicitly registered.

**Data flow**: It receives a provider name. It checks the explicit provider-to-host mapping first. If the provider is present there, it returns that tuple, even if it is empty; otherwise it returns the default tuple.

**Call relations**: derive_grant_rules calls this when it needs to add file-transfer hosts for a grant. The method keeps the fallback behavior in one place so grant rule derivation does not accidentally widen access for a connector that deliberately declared no transfer hosts.


##### `connector_transfer_hosts`  (lines 285–296)

```
def connector_transfer_hosts(manifests: tuple[Manifest, ...]) -> ConnectorTransferHosts
```

**Purpose**: This function builds the lookup table of broker file-transfer hosts from the deploy's extension manifests. It records each registered connector's declared transfer hosts and also captures the open connector namespace's hosts as a fallback for unregistered providers.

**Data flow**: It receives the manifests. It walks through their connectors, mapping each connector provider to its declared transfer hosts. It then asks open_connector_namespace whether there is a shared namespace, takes its transfer hosts as the default if present, and returns a ConnectorTransferHosts object containing both pieces.

**Call relations**: This prepares data used later by derive_grant_rules. It reads manifest-level connector declarations, delegates namespace discovery to open_connector_namespace, and packages the result so grant rule derivation can ask a simple provider-to-host question.

*Call graph*: 2 external calls (__init__, open_connector_namespace).


### Artifact and file safety
These files protect stored artifacts, uploaded previews, and sandbox file paths from unauthorized access or unsafe content.

### `core/src/ufo/runtime/media/artifact_url.py`

`domain_logic` · `artifact link creation and download request handling`

Artifacts are files stored under a workspace, such as shared documents, patches, or generated images. This file is the gatekeeper for turning those stored files into safe download URLs. A signed URL is like a temporary guest pass: it contains the file address, an expiry time, the workspace it belongs to, and a cryptographic signature (a tamper-proof stamp made with a secret key). If anyone changes the important parts, verification fails and no bytes are served.

The file also supports image previews. A preview URL can say, “this exact image type and size may be shown inline in the browser,” but only for small raster images such as PNG or JPEG. The route that serves the file can later check the real bytes against that claim.

One important detail is expiry bucketing. Instead of making every newly minted URL unique down to the second, expiry times are rounded up to fixed one-hour boundaries. That means repeated links for the same artifact stay identical for a while, so browsers and edge caches can reuse them instead of refetching constantly.

Older URLs without a workspace claim are treated specially. They can still prove they were signed, but they are never served directly. Instead, they must be refreshed by a signed-in workspace member so the system can attach the correct workspace scope.

#### Function details

##### `artifact_url_expiry`  (lines 59–66)

```
def artifact_url_expiry(now: datetime) -> int
```

**Purpose**: Chooses the expiry timestamp to put into a newly minted artifact URL. It rounds the expiry up to a shared time bucket so repeated links for the same file can be identical and cache-friendly.

**Data flow**: It takes the current time as input, reads its Unix timestamp, adds the configured lifetime, rounds up to the next bucket boundary, and returns that future timestamp as an integer.

**Call relations**: When an image preview link is being made, mint_image_preview_url asks this function for the expiry time. The returned timestamp is then passed into mint_artifact_url so the signed link and its cache behavior are consistent.

*Call graph*: called by 1 (mint_image_preview_url); 1 external calls (timestamp).


##### `ArtifactUrlExpired.__init__`  (lines 88–90)

```
def __init__(self, claims: ArtifactClaims) -> None
```

**Purpose**: Creates the special error used when a URL is authentic but no longer directly usable. It keeps the decoded claims attached so another trusted path, such as a signed-in member refresh, can decide whether to renew access.

**Data flow**: It receives already-verified artifact claims, stores them on the exception, and produces an error object whose message says the artifact URL is expired.

**Call relations**: verify_artifact_url calls this when a signature checks out but the URL is expired, or when an older URL has no workspace claim. That lets callers distinguish “bad or tampered link” from “real link that needs refresh.”

*Call graph*: called by 1 (verify_artifact_url).


##### `artifact_media_type`  (lines 93–105)

```
def artifact_media_type(filename: str) -> str
```

**Purpose**: Decides what media type, also called a MIME type, should be used when serving an artifact file. This tells browsers whether bytes are a patch, spreadsheet, image, generic download, and so on.

**Data flow**: It takes a filename, first checks the project’s own known suffix list for important artifact types, then asks Python’s mimetype guesser. If the type is unknown or the name implies compression that is not separately handled, it returns the safe generic fallback.

**Call relations**: This is a standalone helper for serving artifact bytes with a sensible content type. It relies on explicit project mappings before using the host system’s mimetype knowledge, because that host knowledge can differ between a developer laptop and production.

*Call graph*: 2 external calls (guess_type, PurePosixPath).


##### `mint_artifact_url`  (lines 108–131)

```
def mint_artifact_url(secret: str, blob_key: str, expires_at: int, *, workspace_id: UUID, preview: ImagePreviewGrant | None=None) -> str
```

**Purpose**: Builds the relative signed download URL for one artifact blob inside one workspace. It is used when the system wants to hand someone a temporary link that can later be verified without requiring normal authentication on the fetch itself.

**Data flow**: It receives a secret key, a blob key, an expiry timestamp, a workspace id, and optionally an image preview claim. It checks that the blob key is a valid artifact address, formats the exact message to sign, signs it, URL-escapes the filename and preview value where needed, and returns a path plus query string containing the expiry, workspace, signature, and optional preview claim. If the secret or preview claim is invalid, it raises an artifact URL error instead.

**Call relations**: mint_image_preview_url calls this after deciding an image is eligible for inline preview. Internally, it uses _split_key to safely extract the artifact id and filename, _parsed_preview to check preview claim formatting, _signed_message to build the bytes that must be signed, and sign_detached to create the tamper-proof stamp.

*Call graph*: calls 3 internal fn (_parsed_preview, _signed_message, _split_key); called by 1 (mint_image_preview_url); 3 external calls (__init__, sign_detached, quote).


##### `mint_image_preview_url`  (lines 134–162)

```
def mint_image_preview_url(secret: str, public_base_url: str | None, blob_key: str, size_bytes: int | None, *, workspace_id: UUID) -> str | None
```

**Purpose**: Creates a full public URL for showing a stored raster image inline, or returns nothing when the file is not safe or eligible for preview. It protects the preview path by signing the exact image type and byte size the serving route must later validate.

**Data flow**: It receives the signing secret, the public base URL, the blob key, the file size, and workspace id. It refuses to continue if signing or public delivery is not configured, if the file suffix is not a supported raster image type, if the size is missing, or if the image is too large. Otherwise it chooses a bucketed expiry, creates an image preview grant, mints the signed artifact path, joins it to the public base URL, and returns the absolute URL.

**Call relations**: This is the higher-level convenience function for preview links. It asks raster_image_media_type what image type the blob key implies, asks artifact_url_expiry for a cache-friendly expiry, and then hands all of that to mint_artifact_url to produce the signed path.

*Call graph*: calls 2 internal fn (artifact_url_expiry, mint_artifact_url); 3 external calls (__init__, now, raster_image_media_type).


##### `verify_artifact_url`  (lines 165–206)

```
def verify_artifact_url(secret: str, artifact_id: str, filename: str, expires_at: str, signature: str, preview: str, workspace: str, now: datetime) -> ArtifactClaims
```

**Purpose**: Checks whether an incoming artifact download URL is well-formed, untampered, scoped to a workspace, and still within its valid time window. If it passes, it returns the claims that say exactly which blob may be served.

**Data flow**: It receives the secret, URL path pieces, query string values, and the current time. It validates the artifact id, filename, expiry, workspace id, and optional preview claim; rebuilds the same signed message used at mint time; verifies the signature; and turns the result into ArtifactClaims containing workspace id, blob key, filename, expiry, and preview details. If anything is malformed or tampered, it raises an artifact URL error. If the URL is expired or lacks a workspace claim, it raises ArtifactUrlExpired while carrying the verified claims.

**Call relations**: This is the counterpart to mint_artifact_url. Download routes call it when a request arrives. It uses _is_canonical_uuid and _is_filename to reject unsafe path pieces, _parsed_preview to understand preview grants, _signed_message to recreate the signed bytes, and ArtifactUrlExpired when the link is real but must be refreshed instead of served directly.

*Call graph*: calls 5 internal fn (__init__, _is_canonical_uuid, _is_filename, _parsed_preview, _signed_message); 5 external calls (__init__, __init__, timestamp, verify_detached, UUID).


##### `_signed_message`  (lines 209–211)

```
def _signed_message(workspace: str, artifact_id: str, expires_at: str, preview_value: str) -> bytes
```

**Purpose**: Creates the exact byte string that is signed when a URL is minted and recreated when it is verified. This shared formatting is what makes signing and checking agree.

**Data flow**: It receives the workspace value, artifact id, expiry text, and preview text. It joins them in a fixed colon-separated order, including the workspace prefix only when present, encodes the result as bytes, and returns those bytes.

**Call relations**: mint_artifact_url uses this before creating a signature, and verify_artifact_url uses it before checking that signature. If this format changed on one side but not the other, valid links would stop verifying.

*Call graph*: called by 2 (mint_artifact_url, verify_artifact_url).


##### `_parsed_preview`  (lines 214–225)

```
def _parsed_preview(value: str) -> ImagePreviewGrant | None
```

**Purpose**: Reads and validates the optional preview claim from a signed URL. A preview claim says which raster image media type may be shown inline and exactly how many bytes it should have.

**Data flow**: It receives a preview string shaped like media-type:size. It splits off the size, checks that the media type is one of the supported raster image types, checks that the size is a number, and rejects sizes over the configured preview limit. On success it returns an ImagePreviewGrant; otherwise it returns nothing.

**Call relations**: mint_artifact_url calls this as a safety check before signing a preview claim, and verify_artifact_url calls it when reading a request. This keeps the producer and consumer using the same rules for what counts as a valid preview grant.

*Call graph*: called by 2 (mint_artifact_url, verify_artifact_url); 3 external calls (__init__, cast, values).


##### `_split_key`  (lines 228–237)

```
def _split_key(blob_key: str) -> tuple[str, str]
```

**Purpose**: Checks that a blob key really points to one artifact file and extracts its two meaningful parts: the artifact id and filename. This prevents signed URLs from being minted for paths outside the artifact area.

**Data flow**: It receives a blob key string. It requires the key to start with the artifact prefix, contain an artifact id followed by one filename, use a canonical UUID for the id, and use a simple filename without slashes or dot-directory names. If all checks pass, it returns the artifact id and filename; otherwise it raises an artifact URL error.

**Call relations**: mint_artifact_url calls this before signing anything. It delegates the detailed UUID and filename checks to _is_canonical_uuid and _is_filename, so only safe artifact addresses can become signed links.

*Call graph*: calls 2 internal fn (_is_canonical_uuid, _is_filename); called by 1 (mint_artifact_url); 1 external calls (__init__).


##### `_is_canonical_uuid`  (lines 240–244)

```
def _is_canonical_uuid(value: str) -> bool
```

**Purpose**: Checks whether a string is a normal, canonical UUID. A UUID is a standard unique identifier, and using one here prevents loose or oddly formatted artifact and workspace ids from slipping through.

**Data flow**: It receives a string, tries to parse it as a UUID, then compares the normalized UUID text back to the original string. It returns true only when parsing succeeds and the text is already in canonical form; otherwise it returns false.

**Call relations**: _split_key uses this to validate artifact ids before minting URLs, and verify_artifact_url uses it to validate artifact and workspace ids before trusting incoming URL data.

*Call graph*: called by 2 (_split_key, verify_artifact_url); 1 external calls (UUID).


##### `_is_filename`  (lines 247–248)

```
def _is_filename(value: str) -> bool
```

**Purpose**: Checks whether a URL filename is a plain single filename rather than a path. This stops callers from smuggling directory traversal pieces like slashes, dot, or double-dot into artifact addresses.

**Data flow**: It receives a string and returns true only if it is non-empty, contains no slash, and is not '.' or '..'. It does not change anything else.

**Call relations**: _split_key uses this before minting a URL from a stored blob key, and verify_artifact_url uses it before rebuilding a blob key from an incoming request. Together they keep filenames confined to one artifact’s folder.

*Call graph*: called by 2 (_split_key, verify_artifact_url).


### `core/src/ufo/runtime/media/image_previews.py`

`domain_logic` · `request handling`

Image previews are often shown early and handled automatically, so they need stricter checks than an ordinary file attachment. This file acts like a gatekeeper at the door: it lets through only common raster image formats, meaning pixel-based images such as JPEG, PNG, GIF, and WebP, and rejects data that does not match its signed claim.

The process starts with simple type recognition. `raster_image_media_type` looks at a path ending, such as `.jpg` or `.png`, and turns it into a media type like `image/jpeg`.

The main entry point is `validated_image_preview`. It receives image bytes from an asynchronous stream, meaning chunks may arrive over time instead of all at once. It compares the actual number of bytes received with the promised `ImagePreviewGrant`. If the stream is too long, too short, or above the hard limit, it refuses the preview.

Once the bytes are collected, the heavier image checks run in a worker thread so the main async event loop is not blocked. `_ImagePreviewValidator` uses Pillow, the Python image library, to verify that the image can really be opened and decoded. It also checks container endings, image dimensions, total decoded pixels, and animated frame count. This protects against malformed files and “decompression bombs,” where a tiny file expands into huge memory use.

#### Function details

##### `raster_image_media_type`  (lines 43–44)

```
def raster_image_media_type(path: str) -> RasterImageMediaType | None
```

**Purpose**: This function guesses the expected image media type from a file path. It is useful before validating a preview, because the system needs to know whether `.jpg`, `.png`, `.gif`, or `.webp` corresponds to an allowed image type.

**Data flow**: It takes a path string as input. It reads only the final suffix of that path, lowercases it, and looks it up in the table of accepted raster image endings. It returns a media type such as `image/png`, or `None` if the suffix is not one of the accepted image endings.

**Call relations**: This is a small helper used before deeper validation. It relies on `PurePosixPath` to read the suffix in a consistent path-like way, then hands back the media type that other preview-checking code can compare against the actual image bytes.

*Call graph*: 1 external calls (PurePosixPath).


##### `validated_image_preview`  (lines 47–61)

```
async def validated_image_preview(stream: AsyncIterator[bytes], grant: ImagePreviewGrant) -> bytes
```

**Purpose**: This function reads an incoming image preview and accepts it only if its size and contents match the signed promise in its grant. Someone would use it at the point where preview bytes arrive from outside the system and must be checked before storage or display.

**Data flow**: It receives an asynchronous stream of byte chunks and an `ImagePreviewGrant` containing the claimed media type and claimed byte count. It counts chunks as they arrive, rejects the data if it is negative, too large, larger than claimed, or shorter than claimed, then joins the chunks into one byte string. After that it sends the bytes and claimed media type to the image validator; if all checks pass, it returns the original bytes unchanged.

**Call relations**: This is the public validation path for image preview data. When the byte-count checks pass, it uses `asyncio.to_thread` to run `_ImagePreviewValidator.validate` outside the async loop, because opening and decoding images can be CPU-heavy. If any check fails, it raises `InvalidImagePreview` so the caller can reject the preview.

*Call graph*: 2 external calls (__init__, to_thread).


##### `_ImagePreviewValidator.validate`  (lines 66–109)

```
def validate(data: bytes, media_type: RasterImageMediaType) -> None
```

**Purpose**: This function performs the deep safety check on the image bytes. It proves that the data is a real image of the claimed type and that decoding it will not exceed the project’s limits for size, dimensions, frames, or total pixels.

**Data flow**: It takes the full image bytes and the media type the preview claims to be. First it checks basic container markers, then opens the bytes with Pillow and verifies the file structure. It opens the image again to walk through frames, checking each frame’s width and height, counting total decoded pixels, and forcing the frame to load. It returns nothing when the image is acceptable; otherwise it raises `InvalidImagePreview`.

**Call relations**: This is called after `validated_image_preview` has already confirmed the byte count. It calls `_validate_container` first for quick format-specific completeness checks, then hands the bytes to Pillow through `BytesIO`, which treats the bytes like a file in memory. It converts Pillow errors, warning-based decompression-bomb alerts, and common parsing failures into one clear project error: `InvalidImagePreview`.

*Call graph*: 5 external calls (__init__, open, BytesIO, catch_warnings, simplefilter).


##### `_ImagePreviewValidator._validate_container`  (lines 112–125)

```
def _validate_container(data: bytes, media_type: RasterImageMediaType) -> None
```

**Purpose**: This function does fast, format-specific checks that the image container looks complete. It catches obvious truncation before the more expensive image decoding step.

**Data flow**: It receives the raw image bytes and the claimed media type. For JPEG, GIF, PNG, and WebP it checks the expected ending or header-and-length markers for that format. If the bytes do not match what a complete file of that type should look like, it raises `InvalidImagePreview`; otherwise it quietly returns.

**Call relations**: This helper is used inside `_ImagePreviewValidator.validate` before Pillow opens the image. It acts as an early checkpoint: if the claimed format is clearly incomplete, validation stops immediately instead of passing suspicious bytes further into the image decoder.

*Call graph*: 1 external calls (__init__).


### `core/src/ufo/harness/sandbox/containment.py`

`domain_logic` · `cross-cutting file access`

A sandbox only works if untrusted code cannot trick the host into opening files outside the sandbox. This file solves that problem for paths. It does not just check whether a filename looks safe. That would miss a common trick: placing a symlink, which is a filesystem shortcut, inside the sandbox that points to a host file outside it.

The file uses a layered guard. First it rejects unusable relative names like empty paths, “.”, or “..”. Then it resolves the real parent directory and checks that it is still inside the root. Next it walks down each directory one piece at a time using file descriptors, which are operating-system handles to already-open directories. This “pins” the parent directory, like keeping your finger on the exact folder in a filing cabinet so nobody can swap the label after you checked it. Finally, it checks the target file itself without following a final symlink.

The central object is ContainedFile. It represents a file whose parent directory has already been safely pinned. Reads, writes, chmods, renames, and deletes happen relative to that pinned parent. Writes are staged under a temporary sibling name and then atomically renamed into place, so readers see either the old file or the complete new file, not a half-written one.

#### Function details

##### `contained_root`  (lines 88–101)

```
def contained_root(root: str | os.PathLike[str]) -> Path
```

**Purpose**: Checks that a sandbox root exists and is a real directory, not a symlink. This is used when the root may be reachable by untrusted code, because a symlinked root could redirect every later path check.

**Data flow**: It receives a root path → looks at that exact filesystem entry without following a symlink → rejects it if it is missing or not a directory → returns the root’s real, resolved Path when it is safe.

**Call relations**: The main file, directory, and removal guards call this first. It gives them a trustworthy starting point before they check any path underneath the sandbox.

*Call graph*: called by 3 (contained_dir, contained_file, contained_remove); 4 external calls (__init__, __init__, Path, S_ISDIR).


##### `configured_root`  (lines 104–121)

```
def configured_root(root: str | os.PathLike[str], setting: str) -> Path
```

**Purpose**: Checks a root directory that came from operator configuration, where a symlink is allowed on purpose. This supports normal deployment layouts, such as a configured data directory pointing to a mounted disk.

**Data flow**: It receives a root path and the setting name that supplied it → follows the path normally → rejects it if it does not exist or is not a directory → returns the resolved real directory so later checks use the actual location.

**Call relations**: This is a public helper for configuration code outside this file. Unlike contained_root, it is not used by the path guards listed here because it applies to trusted deployment choices, not agent-controlled roots.

*Call graph*: 4 external calls (__init__, __init__, Path, S_ISDIR).


##### `ContainedFile.lstat`  (lines 139–150)

```
def lstat(self) -> os.stat_result | None
```

**Purpose**: Looks at the target file itself without following a final symlink. It tells callers whether the name is empty, a regular file, or something unsafe like a directory or special file.

**Data flow**: It reads the ContainedFile’s pinned parent descriptor and target name → asks the operating system about that exact name without following symlinks → returns the stat information for a regular file, returns None if it is absent, or raises an error if it is not a regular file.

**Call relations**: contained_regular uses this after contained_file has pinned the parent. Other callers can use it before reading or deciding whether an existing target is acceptable.

*Call graph*: 4 external calls (__init__, stat, S_ISDIR, S_ISREG).


##### `ContainedFile.mode`  (lines 152–167)

```
def mode(self, default: int) -> int
```

**Purpose**: Finds what permission bits a replacement file should keep. If there is no current regular file, or the name is held by a symlink, it falls back to a caller-provided default.

**Data flow**: It receives a default mode → checks the target name relative to the pinned parent without following symlinks → returns the existing file’s permission bits for a regular file, returns the default when missing or not a regular file, and refuses directories.

**Call relations**: This is meant to be used by write paths after contained_file has created a safe ContainedFile. It supports safe replacement without letting a planted symlink block delivery forever.

*Call graph*: 4 external calls (__init__, stat, S_ISDIR, S_ISREG).


##### `ContainedFile.open_bytes`  (lines 169–179)

```
def open_bytes(self) -> BufferedReader
```

**Purpose**: Opens the contained target for streaming binary reads. This is useful for large files because the caller can read gradually instead of loading the whole file at once.

**Data flow**: It uses the ContainedFile’s pinned parent and name → asks _open_regular to open only a real file and not a symlink → wraps the low-level file descriptor as a Python binary reader → returns that reader, closing the descriptor if wrapping fails.

**Call relations**: read_bytes calls this when it wants a simpler read-all-up-to-a-limit operation. It hands the risky part, opening the file safely, to _open_regular.

*Call graph*: calls 1 internal fn (_open_regular); called by 1 (read_bytes); 2 external calls (close, fdopen).


##### `ContainedFile.read_bytes`  (lines 181–184)

```
def read_bytes(self, limit: int) -> bytes
```

**Purpose**: Reads up to a given number of bytes from a safely contained regular file. It is a convenience method for callers that want bytes and do not need to stream manually.

**Data flow**: It receives a byte limit → opens the file safely through open_bytes → reads at most that many bytes → closes the file automatically and returns the bytes.

**Call relations**: read_text builds on this to get text. It relies on open_bytes to do the secure opening work.

*Call graph*: calls 1 internal fn (open_bytes); called by 1 (read_text).


##### `ContainedFile.read_text`  (lines 186–187)

```
def read_text(self, limit: int) -> str
```

**Purpose**: Reads a contained file as UTF-8 text, replacing invalid byte sequences instead of failing. This is useful for displaying or processing text-like files safely.

**Data flow**: It receives a byte limit → asks read_bytes for that many bytes from the contained file → decodes the bytes as UTF-8 with replacement for bad characters → returns a string.

**Call relations**: This is the text-friendly wrapper over read_bytes. It does not open files itself; it lets the lower layers keep the safety checks in one place.

*Call graph*: calls 1 internal fn (read_bytes).


##### `ContainedFile.chmod`  (lines 189–190)

```
def chmod(self, mode: int) -> None
```

**Purpose**: Changes the permission bits of the contained target. It applies only normal permission bits and addresses the file through the pinned parent directory.

**Data flow**: It receives a requested mode → masks it down to file permission bits → asks the operating system to change the target name relative to the pinned parent without following symlinks → changes the file’s permissions or lets the operating system raise an error.

**Call relations**: This is an operation available after contained_file has produced a ContainedFile. The safety comes from the already-pinned parent descriptor.

*Call graph*: 1 external calls (chmod).


##### `ContainedFile.unlink`  (lines 192–196)

```
def unlink(self) -> None
```

**Purpose**: Removes the contained target if it exists. Missing files are treated as already gone, which makes cleanup safe to repeat.

**Data flow**: It uses the pinned parent and target name → asks the operating system to unlink that name → leaves the directory changed if the file existed, or does nothing if it was already missing.

**Call relations**: This is a small file operation on a ContainedFile created by contained_file. It avoids re-resolving the path from scratch.

*Call graph*: 1 external calls (unlink).


##### `ContainedFile.replace_with`  (lines 198–200)

```
def replace_with(self, source: ContainedFile) -> None
```

**Purpose**: Renames one already-contained file onto another already-contained target. This is a safe move because both names are interpreted relative to pinned parent directories.

**Data flow**: It receives another ContainedFile as the source → tells the operating system to replace this object’s target name with the source name using both pinned parent descriptors → the source name disappears and the destination name now refers to that file.

**Call relations**: This operation assumes both ContainedFile objects were created by the guard. It delegates the actual atomic replacement to the operating system.

*Call graph*: 1 external calls (replace).


##### `ContainedFile.replace_text`  (lines 202–203)

```
def replace_text(self, text: str, mode: int) -> None
```

**Purpose**: Writes text to the contained target by using the same safe replacement path as binary writes. It exists so callers can provide a string without doing their own encoding.

**Data flow**: It receives text and a permission mode → encodes the text to bytes → passes those bytes and the mode to replace_bytes → the target is replaced with the encoded content.

**Call relations**: This is a convenience wrapper around replace_bytes. The real staging and rename behavior happens there.

*Call graph*: calls 1 internal fn (replace_bytes).


##### `ContainedFile.replace_bytes`  (lines 205–229)

```
def replace_bytes(self, data: bytes, mode: int) -> None
```

**Purpose**: Safely replaces the target file with new bytes. It writes to a unique temporary sibling first, then renames it into place so the final file appears all at once.

**Data flow**: It receives bytes and a permission mode → creates a random staged filename in the pinned parent without following symlinks and without overwriting an existing name → writes the bytes and sets permissions → renames the staged file over the target → cleans up the staged name if anything goes wrong.

**Call relations**: replace_text calls this after encoding text. This function is the main safe-write mechanism for a ContainedFile produced by contained_file.

*Call graph*: called by 1 (replace_text); 7 external calls (close, fchmod, fdopen, open, replace, unlink, uuid4).


##### `ContainedFile._open_regular`  (lines 231–247)

```
def _open_regular(self) -> int
```

**Purpose**: Performs the low-level safe open for reading. It refuses missing files, symlinks, directories, and other non-regular files.

**Data flow**: It uses the pinned parent descriptor and target name → opens the target without following symlinks → checks the opened file descriptor to confirm it is a regular file → returns the descriptor, or closes it and raises an error if the file is not acceptable.

**Call relations**: open_bytes calls this before turning the descriptor into a Python file object. Keeping this check here prevents higher-level readers from accidentally opening unsafe targets.

*Call graph*: called by 1 (open_bytes); 6 external calls (__init__, __init__, close, fstat, open, S_ISREG).


##### `contained_file`  (lines 251–285)

```
def contained_file(path: str | os.PathLike[str], root: str | os.PathLike[str], *, create_parent: bool=False) -> Iterator[ContainedFile]
```

**Purpose**: This is the main safe entry point for reading or writing one file under a sandbox root. It runs the full containment check and yields a ContainedFile whose parent directory is pinned.

**Data flow**: It receives a path, a root, and an option to create missing parent directories → validates the root → anchors relative paths under that root → rejects unusable target names → resolves and checks the parent location → opens the root and descends through each parent directory without following symlinks, optionally creating missing directories → yields a ContainedFile → closes the pinned directory descriptor when the caller is done.

**Call relations**: contained_regular uses this when it needs to prove an existing file path is safe. Internally it relies on rooted, _inside, _open_root, and _descend to turn a text path into a pinned filesystem location.

*Call graph*: calls 5 internal fn (_descend, _inside, _open_root, contained_root, rooted); called by 1 (contained_regular); 5 external calls (__init__, __init__, __init__, close, mkdir).


##### `contained_dir`  (lines 288–314)

```
def contained_dir(path: str | os.PathLike[str], root: str | os.PathLike[str], *, create: bool=False) -> Path
```

**Purpose**: Checks that a directory path stays inside the sandbox and is reached without following symlinks. It is used when the caller wants a directory to list or walk, not a file to open.

**Data flow**: It receives a directory path, a root, and an option to create missing directories → validates the root → anchors and resolves the target → rejects locations outside the root → opens the root and walks each directory component safely, optionally creating components → closes descriptors → returns the resolved directory path.

**Call relations**: contained_glob calls this to choose the directory where a pattern search should begin. It shares the same root opening and per-component descent helpers as contained_file and contained_remove.

*Call graph*: calls 5 internal fn (_descend, _inside, _open_root, contained_root, rooted); called by 1 (contained_glob); 3 external calls (__init__, close, mkdir).


##### `contained_remove`  (lines 317–344)

```
def contained_remove(path: str | os.PathLike[str], root: str | os.PathLike[str]) -> None
```

**Purpose**: Safely removes one contained file or directory tree without following symlinks on the way there. It is the delete-side counterpart to the read and write guards.

**Data flow**: It receives a path and root → validates the root and target name → resolves and checks the parent is inside the root → safely descends to the parent → if the target is missing, it does nothing → if the target is a directory, it removes the tree only when the platform’s removal routine is symlink-safe → otherwise it unlinks the file.

**Call relations**: This function uses rooted, _inside, _open_root, and _descend in the same pattern as contained_file. It does not call contained_file because removal needs to handle both files and directories.

*Call graph*: calls 5 internal fn (_descend, _inside, _open_root, contained_root, rooted); 8 external calls (__init__, __init__, __init__, close, stat, unlink, rmtree, S_ISDIR).


##### `contained_regular`  (lines 347–356)

```
def contained_regular(path: str | os.PathLike[str], root: str | os.PathLike[str]) -> Path
```

**Purpose**: Returns the canonical path of an existing regular file inside the sandbox. It is for callers that must pass a filename to another tool instead of reading through an open file descriptor.

**Data flow**: It receives a path and root → enters contained_file to run the full guard and pin the parent → checks the target itself with lstat → raises if the file is missing → returns the safe canonical path.

**Call relations**: It is a small wrapper around contained_file. The wrapper adds the rule that the target must already exist as a regular file.

*Call graph*: calls 1 internal fn (contained_file); 1 external calls (__init__).


##### `contained_pattern`  (lines 359–376)

```
def contained_pattern(pattern: str, root: Path) -> str
```

**Purpose**: Checks and rewrites a glob pattern, which is a filename search pattern, so it cannot search outside the sandbox root. It also prevents “..” path parts from moving upward.

**Data flow**: It receives a pattern and root → treats the pattern as a POSIX-style path pattern → rejects any pattern containing “..” → returns relative patterns unchanged → for absolute patterns, confirms they point inside the root and rewrites them to be root-relative.

**Call relations**: contained_glob calls this after deciding where the search should start. This keeps pattern safety rules in one place instead of making each listing caller interpret patterns differently.

*Call graph*: called by 1 (contained_glob); 3 external calls (__init__, __init__, PurePosixPath).


##### `contained_glob`  (lines 379–391)

```
def contained_glob(pattern: str, path: str | os.PathLike[str] | None, root: Path) -> tuple[Path, str]
```

**Purpose**: Prepares a safe directory and pattern pair for file enumeration. It decides where a search should start and makes sure both the start point and the pattern are confined to the root.

**Data flow**: It receives a pattern, an optional starting path, and a root → checks whether the pattern is absolute → chooses the root as the start for absolute patterns or missing paths, otherwise uses the provided start path → validates that directory with contained_dir → validates and rewrites the pattern with contained_pattern → returns both values.

**Call relations**: This function coordinates contained_dir and contained_pattern for callers that list files. It prevents an absolute pattern from silently causing a search from the whole filesystem.

*Call graph*: calls 2 internal fn (contained_dir, contained_pattern); 1 external calls (PurePosixPath).


##### `contained_relative`  (lines 394–419)

```
def contained_relative(path: str, root: str) -> str
```

**Purpose**: Performs a purely text-based containment check for a path under a root. It is used when this process cannot inspect the actual filesystem yet, such as a path that will later be written inside a container.

**Data flow**: It receives a path string and a root string → combines relative paths with the root → processes “.” and “..” pieces as text → rejects paths that climb out of the root or name the root itself → returns the cleaned absolute-looking path string.

**Call relations**: This helper is independent of the filesystem-walking guards. Later, when an actual write happens on a reachable filesystem, the stronger guard still needs to run.

*Call graph*: 3 external calls (__init__, __init__, PurePosixPath).


##### `contained_leaf`  (lines 422–430)

```
def contained_leaf(raw: str, fallback: str) -> str
```

**Purpose**: Turns an outside-provided filename into one safe filename component. It drops any directory parts, including Windows-style backslashes, and falls back to a safe name if nothing usable remains.

**Data flow**: It receives a raw name and a fallback → converts backslashes to slashes → keeps only the final filename piece → returns the fallback when that piece is empty, “.”, or “..”, otherwise returns the leaf name.

**Call relations**: This is a helper for callers that receive names from providers, browsers, or attachments. It does not prove full containment by itself; callers still join the leaf under a root and use the main guard for writes.

*Call graph*: 1 external calls (PurePosixPath).


##### `is_contained_regular`  (lines 433–444)

```
def is_contained_regular(path: Path, root: Path) -> bool
```

**Purpose**: Checks whether an already-enumerated path appears to be a regular file inside the root without crossing symlinks. It is mainly a fast filter for listing results.

**Data flow**: It receives a Path and root → rejects it if its own entry is not a regular file → resolves it strictly to its real location → returns false on filesystem errors → returns true only when the resolved path is exactly the same path and lies inside the root.

**Call relations**: This helper uses _inside for the final containment test. It is for deciding what to list; actual reading should still go through contained_file.

*Call graph*: calls 1 internal fn (_inside); 3 external calls (lstat, resolve, S_ISREG).


##### `rooted`  (lines 447–453)

```
def rooted(path: str | os.PathLike[str], root: Path) -> Path
```

**Purpose**: Anchors a caller-provided path under a root when the path is relative. This avoids accidentally interpreting untrusted relative paths against the process’s current working directory.

**Data flow**: It receives a path and a root → converts the path to a Path object → returns it unchanged if it is absolute → otherwise returns root joined with that relative path.

**Call relations**: contained_file, contained_dir, and contained_remove call this near the start. It makes sure those guards all ask questions about the same root-based path.

*Call graph*: called by 3 (contained_dir, contained_file, contained_remove); 1 external calls (Path).


##### `_inside`  (lines 456–457)

```
def _inside(path: Path, root: Path) -> bool
```

**Purpose**: Answers the simple question: is one path equal to the root or somewhere below it? This is the shared containment test after paths have been resolved.

**Data flow**: It receives a candidate path and a root → compares the candidate to the root and to the root’s descendants → returns true when the candidate is inside or exactly the root, otherwise false.

**Call relations**: contained_file, contained_dir, contained_remove, and is_contained_regular use this after resolving paths. It is deliberately small so the same inside-root rule is applied consistently.

*Call graph*: called by 4 (contained_dir, contained_file, contained_remove, is_contained_regular).


##### `_open_root`  (lines 460–464)

```
def _open_root(root: Path) -> int
```

**Purpose**: Opens the sandbox root directory in a way that refuses symlinks and non-directories. It starts the safe descriptor-based walk through the filesystem.

**Data flow**: It receives a root Path → asks the operating system to open it as a directory with no symlink following → returns the directory file descriptor, or raises a containment error if it cannot be opened safely.

**Call relations**: contained_file, contained_dir, and contained_remove call this before descending into child components. The descriptor it returns is the first pinned location.

*Call graph*: called by 3 (contained_dir, contained_file, contained_remove); 2 external calls (__init__, open).


##### `_descend`  (lines 467–480)

```
def _descend(descriptor: int, part: str, target: Path) -> int
```

**Purpose**: Moves one directory level deeper during a safe filesystem walk. It opens the next component without following symlinks and closes the directory descriptor it just left.

**Data flow**: It receives the current directory descriptor, the next path part, and the full target path for error messages → opens the child directory relative to the current descriptor with symlinks disabled → closes the old descriptor → returns the child descriptor, or raises a clear error for missing paths, symlinks, or non-directories.

**Call relations**: contained_file, contained_dir, and contained_remove call this repeatedly as they walk from the root to a target’s parent or directory. It is the step-by-step mechanism that keeps checked paths from being swapped underneath the program.

*Call graph*: called by 3 (contained_dir, contained_file, contained_remove); 5 external calls (__init__, __init__, __init__, close, open).


### Workspace actors and credentials
These files bind work to a workspace and acting agent, then safely store, select, and retrieve workspace or member credentials for provider access.

### `core/src/ufo/runtime/workspace.py`

`orchestration` · `cross-cutting during request, turn, and background job execution`

This file solves a safety problem: many parts of the system need to know “which workspace am I acting for?” when they read secrets, write database records, or charge usage. Instead of making every function accept a workspace ID by hand, this file creates a temporary workspace scope using `with ws(workspace_id):`. Inside that block, code can ask `ws_current()` for the current workspace, much like checking the current room you are standing in before opening a cabinet or writing on a ledger.

The workspace scope is used for three important things. First, it retrieves credentials. If the workspace has its own stored key, that key is used. If not, the system falls back to a platform-wide environment variable. Second, it records billable model usage. Code opens a `billable_event()` block, adds usage records as provider calls happen, and the file writes those charges when the block exits. Third, it shares the same workspace identity with database transaction code, so database access can be limited to the right workspace.

The important behavior is that missing context fails loudly. If code tries to get the current workspace without first entering `with ws(...)`, it raises `WorkspaceUnbound`. That prevents accidental cross-workspace secret access or untracked billing.

#### Function details

##### `init_workspace_credentials`  (lines 28–32)

```
def init_workspace_credentials(store: CredentialStore | None) -> None
```

**Purpose**: Installs the credential store the application should use for workspace-owned secrets. This is normally done once during startup so later workspace calls know where to look for stored keys.

**Data flow**: A credential store, or `None`, comes in. The function saves it in a module-wide variable. After that, workspace credential lookups either use this store first or, if it is `None`, can only fall back to platform environment variables.

**Call relations**: Startup or setup code calls this before normal workspace work begins. Later, methods such as `WorkspaceScope.credential`, `WorkspaceScope.credential_is_stored`, `WorkspaceScope.rotate_credential`, and `WorkspaceScope.put_credential` read the stored value to decide whether workspace-specific credentials are available.


##### `BillableEvent.usage`  (lines 48–58)

```
def usage(self, model: str, usage: Usage, pricing: Pricing=CORE_PRICING, byok: bool=False) -> None
```

**Purpose**: Adds one reported provider usage item to a billable event. Code uses this whenever a model call has consumed tokens or other metered usage that may need to be charged to the workspace.

**Data flow**: The caller gives the model name, the usage amount, pricing information, and whether the workspace used its own provider key. The function stores that item in an internal list. Nothing is written to the database yet; it is saved for the surrounding billing block to commit later.

**Call relations**: This is used inside `WorkspaceScope.billable_event`. The billable event collects usage during the protected block, then `WorkspaceScope.billable_event` reads the collected items on exit and sends them to the billing recorder.


##### `WorkspaceScope.credential`  (lines 67–82)

```
async def credential(self, slot: str, env: str | None=None) -> str
```

**Purpose**: Returns the secret value for a named credential slot, such as an API key, for this workspace. It protects against using a key without first naming the workspace that owns or inherits it.

**Data flow**: The method starts with a workspace ID already stored in the `WorkspaceScope`, plus a credential slot name and optionally an environment variable name. It first asks the configured credential store for this workspace’s stored value. If there is no stored value, it asks the deployment environment for a platform default. If neither exists, it raises `CredentialSlotUnset`; otherwise it returns the secret string.

**Call relations**: Code that needs a secret should first enter a workspace with `ws(...)` or obtain one through `ws_current()`, then call this method. It may call the external credential store, uses `deploy_env` for the fallback environment lookup, and raises `CredentialSlotUnset` when the requested key is not configured.

*Call graph*: 2 external calls (__init__, deploy_env).


##### `WorkspaceScope.credential_is_stored`  (lines 84–94)

```
async def credential_is_stored(self, slot: str) -> bool
```

**Purpose**: Answers whether this workspace has its own stored credential for a slot. This matters because usage paid through the workspace’s own provider key should not be billed as platform spend.

**Data flow**: The method reads the workspace ID and slot name. If there is no credential store, it returns `false`. If there is a store, it tries to fetch the workspace’s credential. A successful fetch becomes `true`; a missing credential becomes `false`.

**Call relations**: Billing-aware model code can call this near the time it fetches a credential. Its answer can be passed along to `BillableEvent.usage` as the `byok` flag, telling the billing flow whether the workspace brought its own key.


##### `WorkspaceScope.rotate_credential`  (lines 96–101)

```
async def rotate_credential(self, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Replaces an existing workspace credential only if it matches an expected current value. This is a safe update pattern that helps avoid overwriting a secret that changed between read and write.

**Data flow**: The method receives a slot name, an expected existing value, and the new plaintext secret. If no credential store is configured, it returns `false`. Otherwise it asks the store to rotate the credential for this workspace and returns whether that rotation succeeded.

**Call relations**: Credential administration code calls this through a bound `WorkspaceScope`. It delegates the actual encrypted storage update to the credential store configured by `init_workspace_credentials`.


##### `WorkspaceScope.put_credential`  (lines 103–107)

```
async def put_credential(self, slot: str, plaintext: str) -> None
```

**Purpose**: Stores a new workspace-owned credential for a slot. It is used when an authorized owner first adds a secret for the workspace.

**Data flow**: The method receives a slot name and plaintext secret. If no credential store is configured, it raises an error because there is nowhere safe to store it. Otherwise it passes the workspace ID, slot, and secret to the credential store.

**Call relations**: Workspace credential setup code calls this after obtaining a `WorkspaceScope`, usually through `ws(...)` or `ws_current()`. Like rotation, it relies on the credential store installed by `init_workspace_credentials`.


##### `WorkspaceScope.billable_event`  (lines 110–122)

```
async def billable_event(self) -> AsyncIterator[BillableEvent]
```

**Purpose**: Creates a block where model usage can be collected and then charged to this workspace when the block finishes. It ensures reported provider usage is recorded even if later processing cannot use the model’s output.

**Data flow**: When entered, it creates an empty `BillableEvent` and gives it to the caller. The caller adds usage records during the block. When the block exits, the method checks the collected records; if there are any, it opens a workspace database transaction and writes each usage item with the workspace ID, model, usage, pricing, and bring-your-own-key flag.

**Call relations**: Model-calling code uses this around provider work and calls `BillableEvent.usage` inside it. On exit, this method opens `workspace_tx` for database access and hands each record to `record_workspace_usage` so the billing ledger is updated for the same workspace.

*Call graph*: 3 external calls (__init__, record_workspace_usage, workspace_tx).


##### `ws`  (lines 126–134)

```
def ws(workspace_id: UUID) -> Iterator[WorkspaceScope]
```

**Purpose**: Temporarily binds a workspace ID as the current workspace for everything inside a `with` block. It is the main doorway that turns ordinary code into workspace-scoped code.

**Data flow**: A workspace ID comes in. The function stores it in the current execution context, yields a `WorkspaceScope` for that ID, and then restores the previous context when the block ends. Before the block, there may be no current workspace; inside, `ws_current()` and workspace-aware database code can see this workspace; after, the binding is removed or restored.

**Call relations**: Turn handlers, request handlers, or background jobs call this at their boundary. Inside the block, code can call `ws_current()` and database transaction helpers can read the same workspace context. The function uses `current_workspace.set` at entry and `current_workspace.reset` at exit.

*Call graph*: 3 external calls (__init__, reset, set).


##### `ws_current`  (lines 137–143)

```
def ws_current() -> WorkspaceScope
```

**Purpose**: Returns the workspace scope currently bound to this execution. If no workspace has been bound, it raises a clear error instead of guessing.

**Data flow**: The function reads the current workspace ID from the execution context. If it finds one, it wraps it in a `WorkspaceScope` and returns it. If it finds nothing, it raises `WorkspaceUnbound` with a message telling the caller to wrap the work in `with ws(workspace_id):`.

**Call relations**: Any code that needs credentials, billing, or workspace-scoped behavior calls this after an outer layer has entered `ws(...)`. It depends on the workspace value set by `ws`; if that setup did not happen, this function stops the operation before it can read secrets or bill the wrong workspace.

*Call graph*: 3 external calls (__init__, __init__, get).


### `extensions/coding/ufo_ext_coding/github_app.py`

`domain_logic` · `credential resolution during rule derivation and GitHub request preparation`

This file solves a trust problem around GitHub access. If an organization installed this project’s GitHub App, the system should authenticate as that App installation, not as an individual user. But it must not accept a plain installation number typed into storage, because that could let one workspace borrow another organization’s installation. So the workspace stores a sealed installation binding: an encrypted and authenticated value that proves the callback created it for that exact workspace.

The main class, GitHubAppTokens, reads that sealed binding, opens it, and asks GitHub for an installation token. That token is short-lived, so it is safer than keeping a long-lasting secret around. The class also caches the token until shortly before it expires, like reusing a valid train ticket instead of buying a new one for every stop.

If there is no App installation, the code can fall back to a stored personal token. But if a stored installation value exists and cannot be opened, it fails instead of falling back. That is important: using a different identity from the one the organization approved is worse than refusing access.

GitHubAPIAuth wraps this for GitHub API calls by returning a normal bearer authorization value. The app_tokens helper reads the deployed App id and private key from environment variables and builds the token minter.

#### Function details

##### `_segment`  (lines 58–59)

```
def _segment(payload: dict[str, object]) -> bytes
```

**Purpose**: This helper turns one part of a JSON Web Token into the compact text form GitHub expects. A JSON Web Token, or JWT, is a signed short message that proves this service owns the GitHub App private key.

**Data flow**: It receives a small dictionary of values, converts it to compact JSON text, encodes that text using URL-safe base64, and removes padding characters. The result is a byte string ready to become one section of a JWT.

**Call relations**: GitHubAppTokens._jwt uses this helper twice: once for the JWT header and once for the JWT body. This keeps the token-building code focused on signing, while this helper handles the repeated formatting step.

*Call graph*: called by 1 (_jwt); 2 external calls (urlsafe_b64encode, dumps).


##### `GitHubAppTokens.bound`  (lines 83–95)

```
async def bound(self, workspace_id: UUID, store: CredentialStore) -> bool
```

**Purpose**: This checks whether a workspace has a valid GitHub App installation binding. It does not mint a token; it only answers whether the App path is available and trustworthy.

**Data flow**: It receives a workspace id and a credential store. It asks the store for the installation slot. If the slot is missing, it returns false. If a value is present, it tries to open the sealed installation value for that workspace; if opening succeeds, it returns true, and if opening fails, the failure is allowed to surface.

**Call relations**: This method is used when the wider credential system needs to know whether this GitHub App credential exists for a workspace. It relies on CredentialStore.get to read the stored value and open_installation to prove the value really belongs to this workspace and slot.

*Call graph*: calls 1 internal fn (get); 1 external calls (open_installation).


##### `GitHubAppTokens.secret`  (lines 97–121)

```
async def secret(self, workspace_id: UUID, store: CredentialStore) -> str | None
```

**Purpose**: This returns a usable GitHub installation token for a workspace, or returns nothing when the workspace has no App installation. It is careful not to fall back silently if an installation value exists but is invalid.

**Data flow**: It receives a workspace id and credential store. It reads the sealed installation value. If there is no value, it returns None so another credential can be used. If there is a value, it opens it, checks whether a still-fresh token is already cached for that workspace and installation, and returns it if so. Otherwise it starts or joins an in-progress minting task, waits for the new token, and returns the token text.

**Call relations**: This is the main path callers use when they need the actual GitHub App secret. It reads from CredentialStore.get, verifies with open_installation, and hands new-token work to GitHubAppTokens._mint. It uses asyncio.create_task and asyncio.shield so that overlapping requests for the same installation share one minting operation instead of all asking GitHub separately.

*Call graph*: calls 2 internal fn (get, _mint); 4 external calls (create_task, shield, time, open_installation).


##### `GitHubAppTokens._mint`  (lines 123–131)

```
async def _mint(self, key: tuple[UUID, str], installation: str) -> tuple[str, float]
```

**Purpose**: This performs one coordinated token-minting attempt and records the result in the cache. It also cleans up the “minting in progress” marker afterward.

**Data flow**: It receives the cache key and GitHub installation id. It asks GitHubAppTokens._installation_token to get a fresh token and its expiry time, stores that pair in the minted-token cache, and returns it. Whether minting succeeds or fails, it removes the matching in-progress task marker if this task is still the current one.

**Call relations**: GitHubAppTokens.secret creates this task when no fresh cached token exists. This method delegates the actual GitHub API exchange to GitHubAppTokens._installation_token, while secret remains responsible for deciding whether minting is needed.

*Call graph*: calls 1 internal fn (_installation_token); called by 1 (secret); 1 external calls (current_task).


##### `GitHubAppTokens._installation_token`  (lines 133–188)

```
async def _installation_token(self, installation: str) -> tuple[str, float]
```

**Purpose**: This talks to GitHub to exchange the App’s signed JWT for an installation access token. It also checks GitHub’s response and reports clear minting failures.

**Data flow**: It receives a GitHub installation id. It creates a short-lived JWT with GitHubAppTokens._jwt, sends it to GitHub’s installation-token endpoint, and optionally asks GitHub for specific permissions. If the HTTP request fails, GitHub returns the wrong status, or the response cannot be read, it raises CredentialMintFailed. On success, it returns the token text and the token’s expiry time. If no specific permissions were requested, it also inspects the granted permissions and logs a warning for important missing ones.

**Call relations**: GitHubAppTokens._mint calls this when a fresh token is needed. This method is the point where the local credential logic crosses the network boundary to GitHub, using httpx.AsyncClient for the HTTP request and warn to record non-fatal permission problems.

*Call graph*: calls 1 internal fn (_jwt); called by 1 (_mint); 4 external calls (__init__, fromisoformat, AsyncClient, warn).


##### `GitHubAppTokens._jwt`  (lines 190–197)

```
def _jwt(self) -> str
```

**Purpose**: This builds and signs the short-lived JWT that proves this service owns the GitHub App. GitHub requires this proof before it will issue an installation token.

**Data flow**: It reads the current time and the configured App id. It creates a JWT header and body, signs them with the App’s RSA private key using SHA-256, base64-encodes the signature, and combines the three pieces into the final token string.

**Call relations**: GitHubAppTokens._installation_token calls this just before contacting GitHub. It uses _segment to format the header and body, then cryptographic signing tools to make the result trustworthy to GitHub.

*Call graph*: calls 1 internal fn (_segment); called by 1 (_installation_token); 4 external calls (urlsafe_b64encode, PKCS1v15, SHA256, time).


##### `GitHubAPIAuth.bound`  (lines 207–214)

```
async def bound(self, workspace_id: UUID, store: CredentialStore) -> bool
```

**Purpose**: This checks whether a workspace has any usable GitHub API authentication, either through the GitHub App or through a stored fallback token. It answers only availability, not the secret itself.

**Data flow**: It receives a workspace id and credential store. If GitHub App tokens are configured, it first asks GitHubAppTokens.bound whether an App installation is valid. If so, it returns true. Otherwise it looks for the fallback credential slot. If that stored token exists, it returns true; if the slot is unset, it returns false.

**Call relations**: This method fits above GitHubAppTokens.bound as a broader availability check for API authentication. It first prefers the App identity, then uses CredentialStore.get to check whether the personal-token fallback exists.

*Call graph*: calls 1 internal fn (get).


##### `GitHubAPIAuth.secret`  (lines 216–223)

```
async def secret(self, workspace_id: UUID, store: CredentialStore) -> str | None
```

**Purpose**: This returns the Authorization header value used for GitHub API requests. It prefers the GitHub App token and falls back to a stored personal token only when there is no App installation token.

**Data flow**: It receives a workspace id and credential store. If App token support exists, it asks GitHubAppTokens.secret for a token. If that returns None, it reads the fallback token from the credential store. If neither token exists, it returns None. If it gets a token from either path, it prefixes it with “Bearer ” so it can be sent as a GitHub API authentication header.

**Call relations**: This is the small adapter between the credential source and GitHub API calls. It calls into GitHubAppTokens.secret for the preferred App identity and uses CredentialStore.get for the fallback path.

*Call graph*: calls 1 internal fn (get).


##### `app_tokens`  (lines 226–245)

```
def app_tokens(installation_slot: str, permissions: tuple[tuple[str, str], ...] | None=GIT_INSTALLATION_PERMISSIONS) -> GitHubAppTokens
```

**Purpose**: This builds a GitHubAppTokens object from deployment environment variables. It makes startup fail loudly if the GitHub App private key is missing or not the expected RSA key type.

**Data flow**: It receives the name of the credential slot that stores installation bindings and, optionally, the permissions to request. It reads GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY from the environment, parses the private key, checks that it is an RSA private key, and returns a configured GitHubAppTokens instance.

**Call relations**: This helper is used when wiring the extension into the larger system. It hands the loaded App id, private key, installation slot, and requested permissions into GitHubAppTokens.__init__, so later credential lookups can mint installation tokens.

*Call graph*: 2 external calls (__init__, load_pem_private_key).


### `core/src/ufo/runtime/access/credentials.py`

`domain_logic` · `cross-cutting: startup setup, credential prompts, provider callbacks, sandbox opening, and proxy rule derivation`

This file is the project’s safety box for “bring your own key” credentials. A workspace may need an API key, OAuth token, or provider installation ID, but those values must not appear in chat logs or inside the sandbox. This code creates named credential slots, encrypts stored values with Fernet (a shared-key encryption format), and later decrypts them only inside the server process when the proxy needs to swap a harmless placeholder for the real secret.

It also protects the act of filling a credential. When a user is asked for a private value, the request is “sealed”: encrypted together with the workspace, member, slot names, purpose, and a short lifetime. When the private prompt is fulfilled, the seal is checked before anything is written. This is like a coat-check ticket: the ticket proves which coat can be handed back, and it expires if it is no longer valid.

Some credentials are not typed by members. They are created by provider flows, such as an installation callback. This file seals those bindings too, so a guessed installation ID cannot be used for another workspace. It also supports provider-specific host choices, but only from a declared list, so a stored value cannot trick the proxy into connecting to an unsafe internal address.

#### Function details

##### `deploy_env`  (lines 29–35)

```
def deploy_env(name: str) -> str | None
```

**Purpose**: Reads a deploy-level secret or setting from environment variables. It first looks for a UFO-specific name, then falls back to the plain upstream name, so this app can have its own secret without forcing every other tool to see it.

**Data flow**: It receives a variable name. It checks the process environment for `UFO_` plus that name, then for the bare name, treating an empty value as missing. It returns the first non-empty value it finds, or `None` if neither is set.

**Call relations**: This is a small helper used when configuring credentials from the deployment environment. It does not call other project code; it simply gives the rest of the credential system a safe convention for finding platform secrets.


##### `credential_object_name`  (lines 38–41)

```
def credential_object_name(slot: str) -> str
```

**Purpose**: Turns a credential slot name into a clean object name that can be shown or addressed consistently elsewhere. It lowercases the name and replaces punctuation or spaces with dashes.

**Data flow**: It receives a slot name string. It normalizes the text into a simple lowercase, dash-separated form and removes leading or trailing dashes. It returns that normalized name.

**Call relations**: The slot-grouping helper `named_slots` calls this when it needs stable public names for declared credential slots. It relies on regular-expression replacement to do the text cleanup.

*Call graph*: called by 1 (named_slots); 1 external calls (sub).


##### `named_slots`  (lines 44–59)

```
def named_slots(slots: 'tuple[DeclaredSlot, ...]') -> 'dict[str, DeclaredSlot]'
```

**Purpose**: Builds the public name map for declared credential slots. If two slots would get the same simple name, it adds a short stable digest so both can still be addressed safely.

**Data flow**: It receives a tuple of declared slot objects. It first groups them by their normalized object name, then keeps unique names as-is and disambiguates collisions using a hash of the extension and original slot name. It returns a dictionary from public object name to the slot declaration.

**Call relations**: This sits between extension declarations and the parts of the system that read or modify credential objects. It calls `credential_object_name` for the plain name and uses SHA-256 hashing to make collision suffixes stable.

*Call graph*: calls 1 internal fn (credential_object_name); 1 external calls (sha256).


##### `seal_credential_request`  (lines 103–104)

```
def seal_credential_request(fernet: Fernet, state: CredentialRequestState) -> str
```

**Purpose**: Encrypts a credential request state into a sealed string that can be handed around without exposing or allowing changes to its contents.

**Data flow**: It receives a Fernet encryptor and a `CredentialRequestState`. It turns the state into JSON bytes, encrypts those bytes, and returns the encrypted text as a string.

**Call relations**: This is the common sealing step used by `CredentialRequests.seal`, `CredentialRequests.authorize`, and `seal_installation`. Those higher-level functions decide what the seal means; this helper performs the actual encryption.

*Call graph*: called by 3 (authorize, seal, seal_installation); 2 external calls (model_dump_json, encrypt).


##### `open_credential_request`  (lines 107–133)

```
def open_credential_request(fernet: Fernet, sealed: str, *, purpose: str, ttl: int | None=CREDENTIAL_REQUEST_TTL_SECONDS) -> CredentialRequestState
```

**Purpose**: Decrypts and checks a sealed credential state. It rejects seals that are expired, tampered with, malformed, or meant for a different purpose.

**Data flow**: It receives a Fernet encryptor, a sealed string, an expected purpose, and optionally a time limit. It tries to decrypt the seal, parse the JSON into a credential request state, and compare the stored purpose with the expected one. It returns the parsed state, or raises `CredentialRequestInvalid` if anything is wrong.

**Call relations**: `CredentialRequests.open_authorization`, `authorized_slot_workspace`, and `open_installation` all use this as their trusted opener. This keeps the difficult checks in one place instead of making every caller remember them.

*Call graph*: called by 3 (open_authorization, authorized_slot_workspace, open_installation); 2 external calls (__init__, decrypt).


##### `CredentialRequests.seal`  (lines 152–165)

```
def seal(self, workspace_id: UUID, member_id: UUID, slots: tuple[str, ...]) -> str
```

**Purpose**: Creates a short-lived sealed request for a member to privately fill one or more credential slots. It refuses unknown slots and slots that members are not allowed to type by hand.

**Data flow**: It receives a workspace ID, member ID, and slot names. It checks the requested slots against the declared and member-fillable slot sets. If they are valid, it creates a request state and returns an encrypted seal for that exact workspace, member, and slots.

**Call relations**: This is used when the system asks a capable user interface to collect secrets privately. It hands off to `seal_credential_request` after enforcing which slots may be requested.

*Call graph*: calls 1 internal fn (seal_credential_request); 1 external calls (__init__).


##### `CredentialRequests.authorize`  (lines 167–180)

```
def authorize(self, workspace_id: UUID, member_id: UUID, slot: str, payload: str) -> str
```

**Purpose**: Creates a sealed authorization request for a provider flow, such as an OAuth-style callback. It records which workspace, member, slot, and provider state belong together.

**Data flow**: It receives a workspace ID, member ID, slot name, and provider payload. It confirms the slot is declared and the payload is not empty, then seals those details into an encrypted string. The output can travel through a browser redirect without exposing the trusted state.

**Call relations**: Provider authorization setup calls this before sending the member away to the provider. Like `CredentialRequests.seal`, it delegates encryption to `seal_credential_request`, but it is for provider state rather than a typed secret.

*Call graph*: calls 1 internal fn (seal_credential_request); 1 external calls (__init__).


##### `CredentialRequests.open_authorization`  (lines 182–196)

```
def open_authorization(self, sealed: str, workspace_id: UUID, member_id: UUID, slot: str) -> str
```

**Purpose**: Checks a returned provider authorization seal and extracts its provider payload. It makes sure the seal belongs to the expected workspace, member, and slot.

**Data flow**: It receives a sealed string plus the workspace, member, and slot that the caller expects. It opens the seal, compares every important claim, confirms the slot is declared, and returns the stored payload. If the seal is for anything else, it raises an error.

**Call relations**: This is used after an authorization flow needs to prove that the callback matches the original request. It relies on `open_credential_request` for decryption and purpose checking, then performs the workspace, member, and slot checks itself.

*Call graph*: calls 1 internal fn (open_credential_request); 1 external calls (__init__).


##### `seal_installation`  (lines 199–213)

```
def seal_installation(fernet: Fernet, workspace_id: UUID, slot: str, installation_id: str) -> str
```

**Purpose**: Seals a provider installation ID so it can be stored safely as a workspace-bound credential value. This prevents someone from typing or guessing a bare installation ID and using another organization’s installation.

**Data flow**: It receives a Fernet encryptor, workspace ID, slot name, and installation ID. It builds a credential state marked with the installation-binding purpose and encrypts it. It returns the sealed binding string.

**Call relations**: Provider installation code uses this before storing an installation reference. It shares the general sealing helper with member credential requests, but marks the purpose differently so the two kinds of seal cannot be swapped.

*Call graph*: calls 1 internal fn (seal_credential_request); 1 external calls (__init__).


##### `open_installation`  (lines 216–227)

```
def open_installation(fernet: Fernet, workspace_id: UUID, slot: str, sealed: str) -> str
```

**Purpose**: Opens a sealed provider installation binding and returns the installation ID only if it belongs to the expected workspace and slot.

**Data flow**: It receives a Fernet encryptor, workspace ID, slot name, and sealed binding. It decrypts the binding without an expiry time, checks its purpose, workspace, slot, and payload, then returns the installation ID. Invalid or mismatched data raises `CredentialRequestInvalid`.

**Call relations**: Provider code uses this when it needs to mint or use credentials from a stored installation binding. It calls `open_credential_request` with the installation-binding purpose so ordinary credential request seals cannot pass.

*Call graph*: calls 1 internal fn (open_credential_request); 1 external calls (__init__).


##### `install_credential_requests`  (lines 233–239)

```
def install_credential_requests(requests: CredentialRequests | None) -> None
```

**Purpose**: Installs the process-wide credential request authority. This gives routes that do not have normal request context, such as browser callbacks, access to the same Fernet key and slot rules.

**Data flow**: It receives a `CredentialRequests` object or `None`. It stores that value in a module-level variable. It does not return anything, but it changes what later global lookup functions will see.

**Call relations**: Startup code calls this once when the server is configured. Later, callback helpers such as `authorized_slot_workspace` depend on the installed value to verify seals outside the usual turn/session flow.


##### `installed_credential_requests`  (lines 242–245)

```
def installed_credential_requests() -> CredentialRequests
```

**Purpose**: Returns the process-wide credential request authority, or fails clearly if credential authorization is not configured.

**Data flow**: It reads the module-level installed credential request object. If one exists, it returns it. If not, it raises a runtime error explaining that no credential key is configured.

**Call relations**: Code that needs the configured credential request authority can call this instead of reading the global variable directly. It provides a clear failure path for deployments without credential support.


##### `authorized_slot_workspace`  (lines 248–264)

```
def authorized_slot_workspace(sealed: str, slot: str, payload: str) -> UUID | None
```

**Purpose**: Finds which workspace a provider authorization seal belongs to, but only if the seal exactly matches the expected slot and provider payload. It returns `None` instead of throwing for bad or unrelated seals.

**Data flow**: It receives a sealed authorization string, a slot name, and a payload. It opens the seal using the installed credential request authority, checks that the slot and payload match, and returns the workspace ID. If credentials are not installed or validation fails, it returns `None`.

**Call relations**: A provider callback route uses this when a browser returns without a normal workspace session. It calls `open_credential_request` to verify the seal, then uses the result to decide which workspace the callback belongs to.

*Call graph*: calls 1 internal fn (open_credential_request).


##### `CredentialStore.put`  (lines 271–293)

```
async def put(self, workspace_id: UUID, slot: str, plaintext: str) -> None
```

**Purpose**: Stores a credential value for a workspace and slot, encrypted before it reaches the database. It either updates the existing row or creates a new one.

**Data flow**: It receives a workspace ID, slot name, and plaintext secret. It rejects empty secrets, encrypts the plaintext, opens a workspace database transaction, and tries to update the matching credential row. If no row exists, it inserts one. It returns nothing, but the database now contains the encrypted value.

**Call relations**: Credential fulfillment and provider-binding code use this when a slot receives a new value. It uses database transaction helpers and SQL update/insert operations, while keeping the raw secret out of the database.

*Call graph*: 3 external calls (insert, update, workspace_tx).


##### `CredentialStore.get`  (lines 295–307)

```
async def get(self, workspace_id: UUID, slot: str) -> str
```

**Purpose**: Retrieves and decrypts the stored secret for a workspace slot. If no value has been stored, it signals that the slot is unset.

**Data flow**: It receives a workspace ID and slot name. It reads the encrypted credential row from the database, raises `CredentialSlotUnset` if there is none, decrypts the ciphertext if present, and returns the plaintext string.

**Call relations**: This is the main read path for stored credentials. It is called by `slot_secret`, `slot_is_set`, `credential_host`, and GitHub extension authentication code when they need to know whether a workspace has a usable stored value.

*Call graph*: called by 7 (credential_host, slot_is_set, slot_secret, bound, secret, bound, secret); 3 external calls (__init__, select, workspace_tx).


##### `CredentialStore.rotate`  (lines 309–340)

```
async def rotate(self, workspace_id: UUID, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Safely replaces an existing credential only if it still has the expected old value. This prevents two simultaneous refreshes from accidentally overwriting a newer token with an older one.

**Data flow**: It receives a workspace ID, slot name, expected current plaintext, and replacement plaintext. It rejects empty replacements, loads the current encrypted value, decrypts it, and compares it with the expected value. If they match, it writes a newly encrypted replacement, but only if the database row has not changed since it was read. It returns `True` for a successful replacement and `False` otherwise.

**Call relations**: OAuth-style clients use this after refreshing a token from an external provider. It uses database select and update operations inside a transaction to make the change conditional.

*Call graph*: 3 external calls (select, update, workspace_tx).


##### `HostChoice.__post_init__`  (lines 364–369)

```
def __post_init__(self) -> None
```

**Purpose**: Validates that a host-choice declaration is internally consistent. The default host must be one of the allowed hosts.

**Data flow**: It runs after a `HostChoice` object is created. It reads the default host and the allowed host list. If the default is not offered by that same list, it raises a value error; otherwise the object remains valid.

**Call relations**: This protects later host resolution from impossible declarations. By catching the mistake at object creation, `credential_host` can safely fall back to the declared default.


##### `HostChoice.resolve`  (lines 371–376)

```
def resolve(self, selected: str) -> str | None
```

**Purpose**: Turns a stored host selection into the exact declared host string, if it is allowed. It matches case-insensitively, because domain names are normally case-insensitive.

**Data flow**: It receives the stored selection text. It trims spaces, lowercases it for comparison, and searches the declared host list. It returns the canonical declared host if found, or `None` if the stored choice is not allowed.

**Call relations**: `credential_host` calls this after reading a workspace’s selected host from the credential store. This keeps member-provided text from becoming an arbitrary network destination.


##### `CredentialSource.secret`  (lines 384–384)

```
async def secret(self, workspace_id: UUID, store: 'CredentialStore') -> str | None
```

**Purpose**: Defines the interface for sources that can mint a fresh secret for a workspace instead of reading a member-stored one. For example, a provider binding might create a short-lived token when needed.

**Data flow**: An implementation receives a workspace ID and credential store. It may read stored binding information, contact or compute against its provider, and returns a secret string or `None` if it has nothing to mint.

**Call relations**: `slot_secret` calls this when a declared slot has a credential source. This function is a protocol method, meaning this file describes the expected behavior while concrete extensions provide the real implementation.

*Call graph*: called by 1 (slot_secret).


##### `CredentialSource.bound`  (lines 386–394)

```
async def bound(self, workspace_id: UUID, store: 'CredentialStore') -> bool
```

**Purpose**: Defines the interface for checking whether a workspace has enough provider binding to mint a secret, without actually minting one. This matters because simple “is it configured?” checks should not call external providers.

**Data flow**: An implementation receives a workspace ID and credential store. It checks local binding state and returns `True` if a secret could be minted, `False` if not, or raises if the binding exists but cannot be used safely.

**Call relations**: `slot_is_set` calls this when it needs to know whether a slot would be available. Like `CredentialSource.secret`, this is a protocol method implemented by provider-specific code outside this file.

*Call graph*: called by 1 (slot_is_set).


##### `slot_secret`  (lines 397–412)

```
async def slot_secret(name: str, source: CredentialSource | None, workspace_id: UUID, store: CredentialStore) -> str | None
```

**Purpose**: Answers the central question: what secret should this workspace use for this slot? It prefers a freshly minted provider secret when a source exists, otherwise it falls back to the stored member value.

**Data flow**: It receives a slot name, optional credential source, workspace ID, and credential store. If a source exists, it asks the source for a secret and returns it if present. If not, it reads the stored value from the store. If the slot is unset, it returns `None`.

**Call relations**: Proxy rules, sandbox exports, and other credential consumers are meant to resolve secrets through this single path. It calls `CredentialSource.secret` for minted values and `CredentialStore.get` for stored values so every role gets the same answer.

*Call graph*: calls 2 internal fn (secret, get).


##### `slot_is_set`  (lines 415–433)

```
async def slot_is_set(name: str, source: CredentialSource | None, workspace_id: UUID, store: CredentialStore) -> bool
```

**Purpose**: Checks whether a slot would produce a secret, without actually producing or refreshing that secret. This lets the sandbox decide whether to configure a client without doing a provider round trip.

**Data flow**: It receives a slot name, optional credential source, workspace ID, and credential store. If a source exists and reports that it is bound, it returns `True`. Otherwise it tries to read the stored credential. It returns `False` only when the stored slot is unset; source errors are allowed to surface.

**Call relations**: Sandbox-opening code can use this lighter check before exporting configuration. It calls `CredentialSource.bound` for provider-backed slots and `CredentialStore.get` for ordinary stored slots, mirroring `slot_secret` without minting.

*Call graph*: calls 2 internal fn (bound, get).


##### `credential_host`  (lines 436–453)

```
async def credential_host(store: CredentialStore, workspace_id: UUID, host: str | HostChoice) -> str | None
```

**Purpose**: Resolves the provider host that a credential is allowed to reach for a workspace. It supports both fixed declared hosts and workspace-selected hosts from a closed, safe list.

**Data flow**: It receives a credential store, workspace ID, and either a fixed host string or a `HostChoice`. For a fixed string, it returns that string. For a host choice, it reads the workspace’s selected value from the store, falls back to the declared default if unset, and returns the matching allowed host or `None` if the stored value is not allowed.

**Call relations**: The egress proxy and sandbox export logic use this so they agree on the same destination host for a credential. It calls `CredentialStore.get` to read a workspace’s host selection and, through `HostChoice.resolve`, keeps the result limited to declared hosts.

*Call graph*: calls 1 internal fn (get).


### `core/src/ufo/runtime/agent_scope.py`

`domain_logic` · `cross-cutting`

This file creates an “ambient” agent identity: a current agent that code can look up when it needs to check or use agent-owned permissions. Ambient means it is stored in the running context, rather than handed directly from function to function. A useful analogy is a visitor badge: once you enter a secured room, the badge says who you are, and tools in that room can check it without asking you to reintroduce yourself every time.

The file ties an agent to a workspace. A workspace is the larger boundary the agent belongs to, and the code refuses to let an agent identity leak across that boundary. The `AgentScope` record stores both IDs together. The hidden `_current_agent` context variable stores the current scope separately for each running task, so one request or task does not accidentally borrow another task’s agent identity.

The `agent` context manager is used with `with agent(agent_id):`. It binds the given agent to the current workspace for the duration of that block, then restores the previous state afterward. It also prevents switching to a different agent while one is already bound. The `agent_current` function is the safe lookup point. It fails loudly if no agent is bound, or if the workspace has changed underneath it. Without these checks, code could accidentally run privileged agent actions under the wrong identity or in the wrong workspace.

#### Function details

##### `agent`  (lines 28–38)

```
def agent(agent_id: UUID) -> Iterator[AgentScope]
```

**Purpose**: This function temporarily marks one agent as the active agent inside the current workspace. Code uses it around a block of work so that lower-level functions can safely discover which agent owns the action.

**Data flow**: It takes an `agent_id` and reads the current workspace ID from `ws_current()`. It combines those into an `AgentScope`, checks whether another different agent is already bound, then stores the new scope in the context variable. It yields that scope to the caller’s `with` block, and when the block finishes, it restores the previous context so the binding does not leak into later work.

**Call relations**: This is the entry point for creating an agent boundary. It calls `ws_current()` to anchor the agent to the workspace that is active at the time, then creates an `AgentScope` for that pair. Later, code inside the block can call `agent_current` to retrieve the same scope and confirm that the agent identity is still valid.

*Call graph*: 2 external calls (__init__, ws_current).


##### `agent_current`  (lines 41–48)

```
def agent_current() -> AgentScope
```

**Purpose**: This function returns the agent that is currently bound to the running context. It is the guardrail for agent-scoped capabilities: if code tries to use such a capability outside an agent boundary, it raises an error instead of guessing.

**Data flow**: It reads the current agent scope from the context variable. If nothing is stored there, it raises `AgentUnbound` with a message telling the caller to wrap the work in `with agent(agent_id):`. If a scope exists, it reads the current workspace through `ws_current()` and compares it with the workspace saved in the scope. If they match, it returns the scope; if they do not, it raises an error because the agent binding no longer belongs to the active workspace.

**Call relations**: This is called by code that needs to know the active agent. It relies on `agent` having already set the scope for the current block of work. It also calls `ws_current()` so it can catch cases where the workspace context and agent context have drifted apart.

*Call graph*: 2 external calls (__init__, ws_current).


### `extensions/sources/ufo_ext_sources/direct.py`

`domain_logic` · `source sync authentication`

Some data sources need an API key that the platform cannot obtain through a normal connected-account broker. In those cases, a workspace member brings their own key. This file is the small bridge that retrieves that key safely when a source sync job needs it.

The important boundary is where the secret is allowed to go. The sync job runs on the host side, where it may read encrypted credentials through a controlled credential access object. The key is fetched from the credential slot named after the provider, then wrapped as a bearer credential, meaning it will be used like an HTTP “Authorization: Bearer ...” token. It is not passed to the sandbox or exposed to the agent-facing parts of the system.

The `DirectAuthProxy` class is intentionally simple. It is given a `CredentialAccess` object, which is the workspace-scoped doorway to approved credential slots. When asked for credentials for a provider, it reads that provider’s stored secret and returns a `Credential` object containing it. The `account` value is present because this class fits the same shape as other auth proxy backends, but for this direct path the real proof of access is the stored provider key itself.

#### Function details

##### `DirectAuthProxy.credential`  (lines 29–30)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: This function retrieves the stored API key for a given provider and returns it in the standard credential form used by source sync code. It is used when the source is configured for direct, bring-your-own-key authentication.

**Data flow**: It receives a workspace ID, a provider name, and an account handle. The provider name is used to read the matching secret from the credential store through `CredentialAccess`; the workspace context is already part of that access object. The fetched secret is then placed into a `Credential` as a bearer token and returned to the caller. It does not modify anything and does not expose the secret anywhere else.

**Call relations**: When a source sync run is routed to this direct auth backend, the caller asks `DirectAuthProxy.credential` for usable provider credentials. The function then builds and returns a `Credential` object, handing the sync code the authentication material it needs for provider HTTP requests while keeping the secret inside the trusted host-side flow.

*Call graph*: 1 external calls (__init__).


### Member audiences
These files define who may access agents, seats, transcripts, and conversation content inside a workspace.

### `extensions/web/ufo_ext_web/audience.py`

`domain_logic` · `request handling`

The web portal needs a clear answer to a simple question: “When this person signs in, which agents are they allowed to reach?” This file is that rulebook for the web surface. It treats a member’s email address as their web identity, and stores explicit access grants as small rows keyed by agent and email. Without this file, the portal would not know how to separate public workspace agents, private agents, owner access, admin access, and special private conversation access.

The main idea is: workspace admins can reach every agent. Non-admin members can reach agents that are visible to the whole workspace, agents explicitly granted to their email, agents they own, and some agent conversations tied to their own private extension activity. The WebAudience object is the packaged answer: it says whether the person is an admin, which agents they can generally use, and which extra agents they may open only for chat-related reasons.

The file also exposes three tool actions. Admins can grant or revoke a member’s access to an agent in the web portal. Admins can also record that they opened another member’s private transcript; this is an audit acknowledgement, like signing a logbook before reading a private file. These actions deliberately refuse non-members, non-admins, missing targets, and unsafe shared-channel contexts.

#### Function details

##### `web_extension`  (lines 38–45)

```
def web_extension() -> ExtensionContext
```

**Purpose**: Builds the web extension’s own access handle so code running from the web surface can read and write the web audience store. Someone uses this when they need the web extension’s private storage area, not some global unrestricted database.

**Data flow**: It takes no input. It creates a scoped store for the web extension and an empty credential declaration, then wraps them in an ExtensionContext. The result is a ready-to-use context for transactions and store access belonging to the web extension.

**Call relations**: This is a setup helper used when web surface code needs to consult the audience records. It hands back the ExtensionContext that later operations can use to open transactions and read or write the extension’s rows.

*Call graph*: 3 external calls (__init__, __init__, __init__).


##### `_grant_key`  (lines 48–49)

```
def _grant_key(agent_id: UUID, email: str) -> str
```

**Purpose**: Creates the storage key for one web access grant. It turns an agent id and a member email into the exact row name used in the extension store.

**Data flow**: It receives an agent UUID and an email address. It trims spaces, lowercases the email, and combines it with the audience prefix and agent id. It returns a single string key such as an address label on a filing cabinet drawer.

**Call relations**: The grant and revoke actions both use this helper so they write and delete the exact same kind of row. That consistency is what lets a grant made by _grant be found and removed later by _revoke.

*Call graph*: called by 2 (_grant, _revoke).


##### `granted_emails`  (lines 52–59)

```
async def granted_emails(store: ScopedStore) -> dict[UUID, tuple[str, ...]]
```

**Purpose**: Reads all stored web access grants and groups them by agent. This is useful for an administration view that wants to show who has been explicitly granted access to each agent.

**Data flow**: It receives a scoped store. It lists all rows whose keys start with the audience prefix, extracts the agent id and email from each key, groups emails under their agent UUID, sorts the emails, and returns a dictionary from agent id to a tuple of emails.

**Call relations**: This function reads the same store rows that _grant writes and _revoke deletes. It does not decide whether someone can enter the portal by itself; instead, it provides a broad readout of the grant list for display or administration.

*Call graph*: calls 1 internal fn (list); 1 external calls (UUID).


##### `_granted_agent_ids`  (lines 62–69)

```
async def _granted_agent_ids(store: ScopedStore, email: str) -> frozenset[UUID]
```

**Purpose**: Finds the private agents that a specific email address has been explicitly granted in the web portal. It is part of building a member’s final web audience.

**Data flow**: It receives a scoped store and an email address. It normalizes the email, scans all audience grant rows, and keeps the agent ids whose stored email matches. It returns those agent UUIDs as a frozen set, meaning a set that cannot be changed by the caller.

**Call relations**: web_audience calls this after it has established that the member is not an admin. The returned agent ids are then combined with workspace-visible agents and owned agents to build the member’s allowed agent list.

*Call graph*: calls 1 internal fn (list); called by 1 (web_audience); 1 external calls (UUID).


##### `WebAudience.allows`  (lines 83–84)

```
def allows(self, agent_id: UUID) -> bool
```

**Purpose**: Answers whether this web audience includes a particular agent for normal access. It is a quick yes-or-no check used before letting the portal proceed with an agent.

**Data flow**: It receives an agent UUID. It compares that id against the ids in the WebAudience’s main agents list. It returns true if one matches, otherwise false, without changing anything.

**Call relations**: The web surface’s chat resolution code calls this when deciding whether an agent is available to the current member. It relies on web_audience having already built the WebAudience object with the correct rules.

*Call graph*: called by 1 (_resolve_chat).


##### `WebAudience.allows_chat`  (lines 86–87)

```
def allows_chat(self, agent_id: UUID) -> bool
```

**Purpose**: Answers whether this member may chat with a particular agent, including extra conversation-only access. It is broader than allows because some agents can be reachable through member-private extension conversations.

**Data flow**: It receives an agent UUID. It checks that id against chat_agents, which is the combined list of normal agents and conversation-only agents. It returns true or false and does not alter the audience.

**Call relations**: This method depends on the chat_agents property to merge both kinds of accessible agents. It is meant for places that care about chat availability rather than only the main portal agent list.


##### `WebAudience.chat_agents`  (lines 90–91)

```
def chat_agents(self) -> tuple[AgentSummary, ...]
```

**Purpose**: Provides the full list of agents this member may chat with. It combines ordinary accessible agents with agents available because of private extension conversations.

**Data flow**: It reads the WebAudience’s agents tuple and conversation_agents tuple. It returns a new tuple containing both, in that order. It does not fetch fresh data or change stored access.

**Call relations**: allows_chat uses this property to perform its yes-or-no check. The property exists so callers do not have to remember to combine the two internal lists themselves.


##### `web_audience`  (lines 94–126)

```
async def web_audience(surface: SurfaceContext, extension: ExtensionContext, email: str) -> WebAudience
```

**Purpose**: Builds the complete web access picture for one email address in one workspace. This is the main decision function that says which agents the signed-in member can see or chat with.

**Data flow**: It receives a SurfaceContext, an ExtensionContext, and an email address. It normalizes the email, reads the workspace seat snapshot to find the member and whether they are an admin, lists all agents, and then applies the access rules. Admins get all agents. Non-admins get workspace-visible agents, explicitly granted agents, agents they own, plus separate conversation-only agents from their member-private extension conversations. It returns a WebAudience object containing that final answer.

**Call relations**: This function pulls together several sources of truth: workspace membership from Seats, agent summaries from the surface, explicit grant rows from _granted_agent_ids, and extension-conversation access from the surface. Other portal routes can then use the returned WebAudience instead of repeating all those rules.

*Call graph*: calls 4 internal fn (transaction, list_agents, member_extension_agent_ids, _granted_agent_ids); 2 external calls (__init__, __init__).


##### `_refusal`  (lines 136–137)

```
def _refusal(text: str) -> ToolResult
```

**Purpose**: Creates a standard error-style tool response with a human-readable refusal message. It is used when an action is not allowed, such as when the speaker is not an admin.

**Data flow**: It receives plain text explaining why the request is refused. It wraps that text in TextContent, places it in a ToolResult, marks the result as an error, and returns it.

**Call relations**: _gate and _read_private_transcript call this whenever they need to stop an action safely. This keeps refusal responses consistent across the admin tools in this file.

*Call graph*: called by 2 (_gate, _read_private_transcript); 2 external calls (__init__, __init__).


##### `_target_agent`  (lines 140–147)

```
def _target_agent(ctx: ToolContext) -> tuple[UUID, str]
```

**Purpose**: Figures out which agent an access-related action is about. If the tool call names an agent, it uses that; otherwise it falls back to the current agent running the turn.

**Data flow**: It receives a ToolContext. It checks that the action has a target object. If the target includes an agent, it returns that agent’s id and name. If no agent is named, it returns the current turn’s agent id and the label “this agent.” If there is no target at all, it raises an error because the tool was dispatched incorrectly.

**Call relations**: _grant, _revoke, and _read_private_transcript all call this so they agree on how to interpret the agent target. It acts like a shared translator between the tool call structure and the storage or audit action.

*Call graph*: called by 3 (_grant, _read_private_transcript, _revoke).


##### `_gate`  (lines 150–165)

```
async def _gate(ctx: ToolContext, extension: ExtensionContext) -> ToolResult | SeatEntry
```

**Purpose**: Performs the permission checks for changing a member’s web access. It makes sure the speaker is a real member, is a workspace admin, and that the target member exists.

**Data flow**: It receives a ToolContext and ExtensionContext. It checks whether there is a speaking member id, asks the context whether that speaker is an admin, reads the workspace member snapshot, and looks up the target member by id. If anything fails, it returns a refusal ToolResult. If everything passes, it returns the matching SeatEntry for the target member.

**Call relations**: _grant and _revoke call this before touching access rows. In the larger flow, _gate is the locked door: only after it returns a member record do the grant and revoke functions proceed to write or delete storage entries.

*Call graph*: calls 3 internal fn (transaction, speaker_is_admin, _refusal); called by 2 (_grant, _revoke); 2 external calls (__init__, UUID).


##### `_grant`  (lines 168–185)

```
async def _grant(ctx: ToolContext, args: WebAccessInput) -> ToolResult
```

**Purpose**: Implements the admin tool that gives a workspace member access to an agent in the web portal. It records the grant under the member’s normalized email address.

**Data flow**: It receives the tool context and an empty input model. It checks that an ExtensionContext exists, runs _gate to confirm the speaker may act on the target member, finds the relevant agent with _target_agent, and handles the special case where the main agent already answers every member. Otherwise it stores a grant row using _grant_key and returns a success message naming the email and agent.

**Call relations**: This is the handler behind the grant_web_access tool definition. It depends on _gate for authorization, _target_agent for deciding which agent is affected, and _grant_key for writing the same key format that web_audience later reads through _granted_agent_ids.

*Call graph*: calls 4 internal fn (agent_is_main, _gate, _grant_key, _target_agent); 2 external calls (__init__, __init__).


##### `_revoke`  (lines 188–208)

```
async def _revoke(ctx: ToolContext, args: WebAccessInput) -> ToolResult
```

**Purpose**: Implements the admin tool that removes a member’s explicit web access grant for an agent. It deletes the stored grant row for that member email and agent.

**Data flow**: It receives the tool context and an empty input model. It verifies that extension context is present, runs _gate, determines the agent with _target_agent, normalizes the target member’s email, and deletes the matching grant key from the store. It then returns a message explaining the result, including a special note that the main agent remains reachable because it is available to every member.

**Call relations**: This is the handler behind the revoke_web_access tool definition. It mirrors _grant: both pass through _gate, both use _target_agent, and both rely on _grant_key so deleting a grant targets the same row format that granting created.

*Call graph*: calls 4 internal fn (agent_is_main, _gate, _grant_key, _target_agent); 2 external calls (__init__, __init__).


##### `_read_private_transcript`  (lines 219–255)

```
async def _read_private_transcript(ctx: ToolContext, args: PrivateTranscriptInput) -> ToolResult
```

**Purpose**: Implements the admin tool that records an acknowledgement before opening another member’s private conversation transcript in the web portal. It does not return the transcript; it records that the admin opened it.

**Data flow**: It receives the tool context and an empty input model. It checks that the speaker is a member, is an admin, and is not acting from a shared external-audience channel. It reads the target conversation id, identifies the relevant agent with _target_agent, and asks record_transcript_access to write the audit record. If there is nothing valid to acknowledge, it returns a refusal. If recording succeeds, it returns a message saying whose private conversation was opened and that the access is now on record.

**Call relations**: This is the handler behind the read_private_transcript tool definition. It shares refusal formatting through _refusal and agent interpretation through _target_agent, then hands the actual audit recording to record_transcript_access so the portal’s later content gate can rely on that record.

*Call graph*: calls 3 internal fn (speaker_is_admin, _refusal, _target_agent); 4 external calls (__init__, __init__, record_transcript_access, UUID).


### `core/src/ufo/runtime/seats.py`

`domain_logic` · `cross-cutting: onboarding, admission, per-turn checks, admin seat changes, and background candidate selection`

A “seat” here means permission for a workspace member to receive answers from the agent. The file treats membership and access as separate ideas: a member row can stay forever as part of the workspace’s history, while its seat can be granted or revoked. Without this split, removing access might erase identity and memory, or a revoked person might still keep running work through old turns.

The main class, Seats, wraps the rules for one workspace. It can ask whether one member is currently admitted, whether a whole set of members are still seated, show a snapshot of all members and their seat state, grant a seat, and revoke a seat. Revoking has an important safety rule: the last seated admin cannot be unseated, because seat management happens through chat and someone must remain able to restore access.

The rest of the file supports member lookup and creation. It normalizes and checks email addresses, derives workspace identities from signup subjects, finds workspaces by email domain, and creates member rows in a race-safe way. Think of it like the front desk of a shared office: it keeps the list of people, checks badges at the door, can deactivate a badge, but does not tear up the person’s permanent file.

#### Function details

##### `gate_member`  (lines 37–48)

```
def gate_member(speaker_member_id: UUID | None, on_behalf_of_member_id: UUID | None) -> UUID | None
```

**Purpose**: Chooses which member a turn should be checked against for seat access. If there is a direct speaker, that person is checked; otherwise the turn is checked against the member it is acting for.

**Data flow**: It receives a possible speaker member ID and a possible “on behalf of” member ID. It returns the speaker ID when present, otherwise the on-behalf-of ID, or nothing if neither exists. It does not read or change stored data.

**Call relations**: This small rule is shared by admission, resuming paused work, dispatch checks, and per-round checks so every part of the system agrees on whose seat matters for a turn.


##### `SeatSnapshot.seated`  (lines 72–73)

```
def seated(self) -> int
```

**Purpose**: Counts how many members in a snapshot currently have seats. It is a simple summary for displaying or reporting seat state.

**Data flow**: It reads the snapshot’s member entries, counts the entries marked as seated, and returns that number. It does not change the snapshot.

**Call relations**: It is used after Seats.snapshot has built a frozen view of the workspace’s members, giving callers a quick total without repeating the counting logic.


##### `Seats.admits`  (lines 84–98)

```
async def admits(self, connection: AsyncConnection, member_id: UUID) -> bool
```

**Purpose**: Answers the basic access question: may this member currently receive answers from the agent in this workspace? It returns true only if the member belongs to the workspace and still has a seat.

**Data flow**: It receives a database connection and a member ID. It looks up that member row within this Seats object’s workspace and checks whether the seated_at field is filled in. It returns a yes-or-no value and does not modify the database.

**Call relations**: Admission checks, running turns, and resume checks can call this whenever they need the current truth from the database. It hands the actual database read to SQLAlchemy, the database query library used here.

*Call graph*: 2 external calls (execute, select).


##### `Seats.all_seated`  (lines 100–120)

```
async def all_seated(self, connection: AsyncConnection, member_ids: Collection[UUID]) -> bool
```

**Purpose**: Checks whether every member in a given group still has a seat. This is useful when one turn depends on several people, because any revoked seat should stop that work.

**Data flow**: It receives a database connection and a collection of member IDs. If the collection is empty, it returns true. Otherwise it counts how many of those IDs are seated members of this workspace and compares that count with the number requested. It returns true only when all requested IDs match seated rows.

**Call relations**: Per-round enforcement, parked-turn checks, and dispatch sweeps all ask this kind of group question. The function performs one grouped database read instead of checking each member one by one.

*Call graph*: 2 external calls (execute, select).


##### `Seats.snapshot`  (lines 122–145)

```
async def snapshot(self, connection: AsyncConnection) -> SeatSnapshot
```

**Purpose**: Builds a read-only picture of all members in the workspace and whether each is seated or an admin. This is the view an admin tool or report can show to a human.

**Data flow**: It receives a database connection, reads all member rows for this workspace in creation order, and turns each row into a SeatEntry. It returns a SeatSnapshot containing those entries and does not change the database.

**Call relations**: This function is the bridge from raw member table rows to a friendly seat-status object. It relies on SQLAlchemy for the query and on the SeatEntry and SeatSnapshot data objects for the returned shape.

*Call graph*: 4 external calls (__init__, __init__, execute, select).


##### `Seats.grant`  (lines 147–157)

```
async def grant(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Restores access for the workspace member with the given email address. If the member already has a seat, it quietly does nothing.

**Data flow**: It receives a database connection and an email address. It first finds the matching member in this workspace. If that member is already seated, nothing changes; otherwise it updates the member row with the current time in seated_at and updated_at. It returns no value.

**Call relations**: Admin-facing tools use this when restoring someone’s access. It delegates the email lookup to Seats._member_by_email so grant and revoke use the same scoped, case-insensitive lookup rule.

*Call graph*: calls 1 internal fn (_member_by_email); 2 external calls (execute, update).


##### `Seats.revoke`  (lines 159–182)

```
async def revoke(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Removes access for the workspace member with the given email address. It refuses to revoke the final seated admin, because then nobody could manage seats from chat.

**Data flow**: It receives a database connection and an email address. It locks the workspace row so two revocations cannot race past each other, finds the member, and returns early if the member is already unseated. If the member is the only seated admin, it raises LastAdminSeatRevocation. Otherwise it clears seated_at and updates updated_at. It does not directly stop running turns; later gate checks see the change.

**Call relations**: Admin tools call this to remove a seat. It uses Seats._member_by_email to find the target and Seats._seated_admin_count to enforce the last-admin safety rule before issuing the database update.

*Call graph*: calls 2 internal fn (_member_by_email, _seated_admin_count); 4 external calls (__init__, execute, select, update).


##### `Seats._member_by_email`  (lines 184–201)

```
async def _member_by_email(self, connection: AsyncConnection, email: str) -> tuple[UUID, datetime | None, bool]
```

**Purpose**: Finds a member in this workspace by email address and returns the facts seat changes need. It raises a clear error if the email does not name a workspace member.

**Data flow**: It receives a database connection and an email address. It trims and lowercases the email for comparison, reads the matching member’s ID, seat timestamp, and admin flag, and returns those three values. If no row exists, it raises UnknownMember.

**Call relations**: Seats.grant and Seats.revoke both call this so they do not duplicate member lookup rules. It keeps email matching scoped to the current workspace.

*Call graph*: called by 2 (grant, revoke); 3 external calls (__init__, execute, select).


##### `Seats._seated_admin_count`  (lines 203–212)

```
async def _seated_admin_count(self, connection: AsyncConnection) -> int
```

**Purpose**: Counts how many admins in the workspace currently have seats. This supports the rule that at least one seated admin must remain.

**Data flow**: It receives a database connection, counts member rows in this workspace where the member is both seated and an admin, and returns the count as an integer. It does not modify anything.

**Call relations**: Seats.revoke calls this only when the target member is an admin. The answer decides whether revocation is safe or must be blocked.

*Call graph*: called by 1 (revoke); 2 external calls (execute, select).


##### `email_domain`  (lines 215–228)

```
def email_domain(email: str) -> str
```

**Purpose**: Extracts a clean email domain from an address, such as turning “A@Example.com” into “example.com”. It returns an empty string for malformed addresses so bad values cannot accidentally match a workspace domain or become members.

**Data flow**: It receives an email-like string, trims and lowercases it, checks that it has exactly one local part and one domain part, and rejects whitespace or extra @ signs. It returns the domain when valid, otherwise an empty string.

**Call relations**: create_member uses this as the shared address-shape rule before inserting a member. workspace_subject, workspace_domain, and workspace_by_domain use it when deriving or matching workspace identity from email domains.

*Call graph*: called by 4 (create_member, workspace_by_domain, workspace_domain, workspace_subject).


##### `signup_workspace_id`  (lines 231–233)

```
def signup_workspace_id(subject: str) -> UUID
```

**Purpose**: Creates the deterministic workspace ID for a hosted signup subject. “Deterministic” means the same subject always produces the same UUID.

**Data flow**: It receives a subject string, lowercases it, and feeds it to UUID version 5 generation using the DNS namespace. It returns the resulting UUID and changes nothing else.

**Call relations**: workspace_subject, workspace_domain, and workspace_by_domain use this to tell whether a workspace is keyed by an exact personal email address or by a broader email domain.

*Call graph*: called by 3 (workspace_by_domain, workspace_domain, workspace_subject); 1 external calls (uuid5).


##### `workspace_subject`  (lines 236–245)

```
def workspace_subject(first_email: str, workspace_id: UUID) -> str
```

**Purpose**: Returns the signup subject that represents a workspace: either the first member’s exact email address or that email’s domain. This keeps invitations, labels, and access checks speaking the same language.

**Data flow**: It receives the first member’s email and the workspace ID. If the exact email would generate that workspace ID, it returns the email; otherwise it returns the email’s domain. It does not touch the database.

**Call relations**: It combines signup_workspace_id and email_domain so callers do not each invent their own version of the workspace label rule.

*Call graph*: calls 2 internal fn (email_domain, signup_workspace_id).


##### `workspace_domain`  (lines 248–266)

```
async def workspace_domain(connection: AsyncConnection, workspace_id: UUID) -> str | None
```

**Purpose**: Finds the email domain that belongs to a workspace, but only when the workspace is truly domain-based. Personal-email workspaces return nothing so a shared provider domain, like a public mail service, does not grant access.

**Data flow**: It receives a database connection and workspace ID. It reads the earliest member’s email, extracts its domain, and checks whether the workspace was keyed by the exact email instead. It returns the domain, or null if there is no member, no valid domain, or the workspace is personal-email based.

**Call relations**: This uses the same email_domain and signup_workspace_id helpers as other signup logic. Database access is limited to finding the first member, which acts as the workspace’s origin record.

*Call graph*: calls 2 internal fn (email_domain, signup_workspace_id); 2 external calls (execute, select).


##### `workspace_by_domain`  (lines 269–302)

```
async def workspace_by_domain(connection: AsyncConnection, domain: str) -> UUID | None
```

**Purpose**: Looks up which workspace, if any, is addressed by an email domain. It deliberately skips personal-email workspaces so a domain like a public mail provider does not point to one user’s workspace.

**Data flow**: It receives a database connection and a domain string. It validates and normalizes the domain, searches for workspaces whose first member email ends in that domain, orders possible matches by age, skips workspaces keyed by an exact personal email, and returns the first matching workspace ID or null.

**Call relations**: This is used when a verified email domain should route someone toward a workspace. It relies on email_domain for safe domain shape and signup_workspace_id to filter out personal-email workspaces.

*Call graph*: calls 2 internal fn (email_domain, signup_workspace_id); 2 external calls (execute, select).


##### `member_by_email`  (lines 305–321)

```
async def member_by_email(connection: AsyncConnection, workspace_id: UUID, email: str) -> UUID | None
```

**Purpose**: Finds the member ID for an email address inside one specific workspace. It is lookup only; it never creates a member.

**Data flow**: It receives a database connection, workspace ID, and email address. It trims and lowercases the email for comparison, searches only within the given workspace, and returns the member ID if found or null if not.

**Call relations**: Routes that have verified an email can use this to connect that address to a workspace member. The workspace condition is part of the query, which avoids accidentally reading a member from another workspace first.

*Call graph*: 2 external calls (execute, select).


##### `member_is_admin`  (lines 324–334)

```
async def member_is_admin(connection: AsyncConnection, workspace_id: UUID, member_id: UUID) -> bool
```

**Purpose**: Checks whether a given member is an admin in a given workspace. It returns false if the member does not belong to that workspace.

**Data flow**: It receives a database connection, workspace ID, and member ID. It reads the is_admin value from the matching member row and converts the result to a plain true-or-false answer.

**Call relations**: Permission checks can call this before allowing admin-only actions such as seat changes. It keeps the admin check tied to both the member and the workspace.

*Call graph*: 2 external calls (execute, select).


##### `create_member`  (lines 337–408)

```
async def create_member(connection: AsyncConnection, workspace_id: UUID, email: str, *, is_admin: bool=False, invited_by: UUID | None=None) -> UUID
```

**Purpose**: Creates a member row for a workspace using the project’s single shared creation path. New members are seated by default, and duplicate creation attempts for the same email settle on the already-created row.

**Data flow**: It receives a database connection, workspace ID, email address, and optional admin and invitation details. It first validates the email shape, lowercases it, locks the workspace row to avoid conflicting insert races, and tries to insert a new member with a fresh UUID. If the insert succeeds, it returns the new ID; if another caller already created the same member, it reads and returns the existing ID. It may raise ValueError for an invalid email.

**Call relations**: Onboarding, teammate joins, hosted signup, and admin invitations all use this instead of writing member rows directly. It calls email_domain for the shared email rule, uses UUID generation for new IDs, and relies on database conflict handling to make races safe.

*Call graph*: calls 1 internal fn (email_domain); 3 external calls (execute, select, uuid4).


##### `member_workspaces`  (lines 411–419)

```
def member_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a candidate source for jobs that need to run over workspaces with members. It keeps that query owned by the core seating code instead of making extensions know the member table details.

**Data flow**: It defines a small query that selects distinct workspace IDs from the member table, wraps that query in a WorkspaceCandidates object through owner_candidates, and returns it. It does not run the query immediately.

**Call relations**: Extensions can declare this as their candidate provider for seat- or member-related reporting. Inside it, the nested member_workspaces.with_a_member function supplies the actual SQL shape.

*Call graph*: 1 external calls (owner_candidates).


##### `member_workspaces.with_a_member`  (lines 416–417)

```
def with_a_member() -> sa.Select[tuple[UUID]]
```

**Purpose**: Describes the database query for “all workspaces that have at least one member.” It is kept nested because it is only used to build the member_workspaces candidate source.

**Data flow**: It takes no inputs from callers. It returns a SQL select statement that asks for distinct workspace IDs from the member table. It does not execute the query itself.

**Call relations**: member_workspaces hands this query-building function to owner_candidates, which can later use it when selecting workspaces for a job.

*Call graph*: 1 external calls (select).


### `core/src/ufo/runtime/turns/audience.py`

`domain_logic` · `cross-cutting during conversation reads, writes, and audience validation`

A conversation can be shared with everyone, tied to one member, tied to an internal room, or tied to a room that includes outsiders. This file gives those audience labels one clear format, such as a shared label, a member label, or a room label, and refuses labels that do not match the expected shape. Think of it like printing destination names on envelopes: the system needs the address to be written in a strict way before it will deliver or reuse the contents.

The central type is `Audience`, which is just a string marked with a more specific meaning so programmers do not confuse it with any random text. Helper functions build valid audience strings for shared conversations, member conversations, internal rooms, and foreign rooms. `parse_audience` is the gatekeeper: it takes text, checks that it is a real audience label, and returns it only if it can be rebuilt exactly by the official builders.

The file also answers practical privacy questions. `readable_audiences` says which audience labels a member may read. `audience_subjects` says which stored knowledge a conversation may draw from, with a special rule that externally shared rooms do not get access to workspace-shared knowledge. `narrow_audience` lets code move from a broad audience to a more specific one when safe, but raises an error if the conversation would change to an unrelated audience.

#### Function details

##### `conversation_audience`  (lines 14–15)

```
def conversation_audience(member_id: UUID | None) -> Audience
```

**Purpose**: Creates the audience label for a normal conversation. If there is no specific member, it returns the shared workspace audience; otherwise it returns the label for that one member.

**Data flow**: It receives either a member identifier or no member at all. With no member, it returns the shared audience label. With a member identifier, it turns that identifier into the standard member-audience string and wraps it as an `Audience`.

**Call relations**: Other code in this file uses it whenever it needs the official form of a member or shared audience. `parse_audience` uses it to verify that a parsed member label matches the canonical spelling, and `readable_audiences` uses it to include a member's own private audience.

*Call graph*: called by 2 (parse_audience, readable_audiences).


##### `room_audience`  (lines 18–19)

```
def room_audience(surface: str, room: str) -> Audience
```

**Purpose**: Creates an audience label for an internal room on a particular surface, such as a chat or collaboration area. This marks content as belonging to that room rather than to one person or the whole workspace.

**Data flow**: It receives a surface name and a room name. It passes them, along with the internal-room prefix, to the shared room-label builder. The result is a validated `Audience` string for that room.

**Call relations**: This is the public helper for internal room audiences. It delegates the actual formatting and safety checks to `_room_audience`, and `parse_audience` calls it when checking that a text value is a valid internal room audience.

*Call graph*: calls 1 internal fn (_room_audience); called by 1 (parse_audience).


##### `foreign_room_audience`  (lines 22–23)

```
def foreign_room_audience(surface: str, room: str) -> Audience
```

**Purpose**: Creates an audience label for a room that is shared with an outside organization or external party. This distinction matters because externally shared rooms have stricter recall rules.

**Data flow**: It receives a surface name and a room name. It sends them to the shared room-label builder with the foreign-room prefix. The output is a validated `Audience` string that marks the room as external-facing.

**Call relations**: This mirrors `room_audience`, but for foreign rooms. It uses `_room_audience` for the common checks and formatting, and `parse_audience` uses it to confirm that an incoming foreign-room label is written exactly as expected.

*Call graph*: calls 1 internal fn (_room_audience); called by 1 (parse_audience).


##### `_room_audience`  (lines 26–29)

```
def _room_audience(prefix: str, surface: str, room: str) -> Audience
```

**Purpose**: Builds the common text format for both internal and foreign room audiences. It also prevents ambiguous labels by rejecting empty names or names containing colons.

**Data flow**: It receives a prefix, a surface, and a room. First it checks that the surface and room are present and do not contain `:`, because colons are used as separators in the label. If the inputs are safe, it returns one audience string made from the prefix, surface, and room; otherwise it raises a `ValueError`.

**Call relations**: This is the shared worker behind `room_audience` and `foreign_room_audience`. Keeping the validation here means both kinds of room label follow the same rules.

*Call graph*: called by 2 (foreign_room_audience, room_audience).


##### `parse_audience`  (lines 32–55)

```
def parse_audience(value: str) -> Audience
```

**Purpose**: Checks whether a plain string is a valid audience label and returns it as an `Audience` only if it passes all rules. This is the main guard against malformed or misleading audience text.

**Data flow**: It receives a string. It first accepts the exact shared-audience label. Otherwise it splits the string at separators, checks whether it is a member, internal room, or foreign room label, and rebuilds the expected label using the official helper for that kind. For member labels, it also checks that the member part is a real UUID, which is a standard unique identifier. If anything is malformed or not canonical, it raises a `ValueError`; if everything matches, it returns the audience.

**Call relations**: Several privacy-related helpers rely on this as their first step. `audience_member`, `audience_subjects`, and `narrow_audience` call it before making decisions, so they work only with audience labels that have already been proven valid.

*Call graph*: calls 3 internal fn (conversation_audience, foreign_room_audience, room_audience); called by 3 (audience_member, audience_subjects, narrow_audience); 1 external calls (UUID).


##### `readable_audiences`  (lines 58–63)

```
def readable_audiences(member_id: UUID) -> tuple[Audience, ...]
```

**Purpose**: Returns the conversation audiences a particular member is allowed to read from the workspace point of view. For a member, that means shared workspace conversations plus their own member-specific conversations.

**Data flow**: It receives a member identifier. It combines the shared audience with the member's own audience label and returns both as a tuple. It does not include room or foreign-room audiences because this file assumes the workspace does not know room membership here.

**Call relations**: This function uses `conversation_audience` to create the member-specific label. It is meant to be used by member-facing reads, such as listing conversations or objects tied to conversations, so those reads consistently use the same audience boundary.

*Call graph*: calls 1 internal fn (conversation_audience).


##### `audience_member`  (lines 66–70)

```
def audience_member(audience: Audience) -> UUID | None
```

**Purpose**: Finds out whether an audience belongs to one specific member, and if so returns that member's identifier. For shared or room audiences, it returns nothing.

**Data flow**: It receives an `Audience`. It first runs it through `parse_audience` to make sure it is valid. If the label does not start with the member prefix, it returns `None`. If it is a member label, it removes the prefix, converts the remaining text into a UUID, and returns that UUID.

**Call relations**: This function depends on `parse_audience` for safety before extracting information. It is useful when later code needs to know whether an audience points at a single member rather than a shared or room-based audience.

*Call graph*: calls 1 internal fn (parse_audience); 1 external calls (UUID).


##### `audience_subjects`  (lines 73–80)

```
def audience_subjects(audience: Audience) -> frozenset[str]
```

**Purpose**: Returns the stored knowledge subjects that a conversation with this audience may read. It enforces the important rule that externally shared rooms cannot read the workspace-shared subject.

**Data flow**: It receives an `Audience` and validates it with `parse_audience`. If the audience is a foreign-room audience, it returns only that audience as the allowed subject. Otherwise it returns two subjects: the workspace-shared subject and the audience's own subject.

**Call relations**: This function uses `parse_audience` before applying recall rules. It is the place where audience labels turn into read permissions for conversation memory or anchored objects.

*Call graph*: calls 1 internal fn (parse_audience).


##### `narrow_audience`  (lines 83–98)

```
def narrow_audience(current: Audience, requested: Audience) -> Audience
```

**Purpose**: Chooses the safest audience when code has a current audience and a newly requested one. It allows staying the same, becoming more specific from shared, or resolving internal versus foreign versions of the same room, but rejects unrelated changes.

**Data flow**: It receives the current audience and the requested audience. It validates both with `parse_audience`. If the request is shared, it keeps the current audience. If the current audience is shared, it allows the requested one. If both are room-style labels for the same surface and room, it keeps the stricter foreign audience when one side is foreign. If the two audiences are unrelated, it raises a `ValueError` instead of silently changing who the conversation is for.

**Call relations**: This function is called when an audience may be refined during a conversation flow. It relies on `parse_audience` to avoid comparing invalid labels, then applies the privacy rule itself rather than handing off to another helper.

*Call graph*: calls 1 internal fn (parse_audience); 1 external calls (partition).


### `core/src/ufo/runtime/turns/subjects.py`

`domain_logic` · `cross-cutting`

This file is a tiny but important vocabulary helper for visibility. In this system, a “subject” is a string label that represents an audience for some content: either the whole workspace or one particular member. Think of it like putting mail into either a shared office inbox or into a named person’s mailbox.

The file defines one fixed subject, `shared`, meaning content readable by every member of the workspace. It also defines a prefix, `member:`, used to build a member-specific subject from that member’s unique ID. This keeps subject strings consistent everywhere else in the code. Without this shared rule, different parts of the system might spell or interpret visibility labels differently, which could lead to content being hidden when it should be visible, or worse, shown to the wrong audience.

It also provides a simple check for whether a subject is the shared one. The comment on that check is important: not every non-member audience counts as shared. For example, a room or externally shared channel is not treated as workspace-wide shared content here, because there is no member ownership fact that proves every workspace member can read it.

#### Function details

##### `member_subject`  (lines 9–10)

```
def member_subject(member_id: UUID) -> str
```

**Purpose**: This function turns a member’s unique ID into the standard subject string for that member. Code uses it when it needs to mark content as belonging to, or readable by, one specific workspace member.

**Data flow**: It takes in a member ID, which is a UUID, meaning a globally unique identifier. It prefixes that ID with `member:` and returns the combined text, such as `member:<id>`. It does not change any stored data; it only creates a consistent label.

**Call relations**: This is a small building block for any code that needs to create member-scoped visibility labels. Instead of each caller inventing the string format itself, they call this function so the rest of the system can recognize the subject reliably.


##### `subject_shared`  (lines 13–18)

```
def subject_shared(subject: str) -> bool
```

**Purpose**: This function answers the question: does this subject mean content is readable by every member of the workspace? It is used to separate truly shared workspace content from member-specific or other audience labels.

**Data flow**: It takes in a subject string, compares it with the exact shared label `shared`, and returns `true` if they match or `false` if they do not. It only reads the input text and produces a yes-or-no answer.

**Call relations**: This function supports code that is deciding whether a piece of content belongs to the workspace-wide shared side of visibility. When other parts of the system need that decision, they can ask this helper instead of repeating the comparison and possibly misunderstanding what counts as shared.


### `core/src/ufo/runtime/turns/untrusted.py`

`util` · `cross-cutting`

This file solves a subtle but important safety problem: outside text can contain sentences that look like commands. For example, a web page or third-party tool result might say “ignore your previous instructions.” The system must pass that text to an agent so it can be useful, but it must also clearly mark it as untrusted.

The file defines one shared way to do that marking. It adds a notice explaining that the content is external, then wraps the content inside an `<untrusted-content>` block that names the source. This is like putting a suspicious document in a sealed evidence bag with a label: people can read what is inside, but the bag makes clear it is not an official instruction.

One important detail is that the file protects the wrapper itself. If the outside content already contains the closing tag `</untrusted-content>`, it is escaped, meaning it is changed into harmless text. Without that, malicious content could pretend to end the protected block early and then continue with fake instructions outside the boundary.

This file matters because multiple parts of the system need to present untrusted content. By using one shared function, they all use the same warning, boundary, and escaping rules instead of each inventing a slightly different version.

#### Function details

##### `wall`  (lines 22–30)

```
def wall(source: str, content: str) -> str
```

**Purpose**: This function wraps external or otherwise untrusted text in a clear warning and boundary. Someone would use it before sending outside content to an agent, so the agent can read it as data without treating it as instructions.

**Data flow**: It takes a source name and a piece of content. It writes the source name into a warning message and into the opening wrapper tag, replaces any fake closing wrapper inside the content with safe text, then returns one combined string: warning, opening boundary, protected content, and closing boundary. It does not change anything outside itself.

**Call relations**: This is the shared safety step used by paths that deliver untrusted material to an agent. The tool-result path uses it when a tool returns content from outside trust, and the hand-back flow uses it when a parent agent receives a background child’s untrusted output. Both rely on this function so there is one consistent meaning of “put this content behind a wall.”


### Signed sessions and surfaces
These files create, verify, and safely expose signed tokens for members, link-addressed surfaces, operator tools, and extensions.

### `core/src/ufo/runtime/auth/bearer.py`

`domain_logic` · `login, request handling, and session validation`

This file is the shared rulebook for UFO bearer tokens, which are pieces of text a client can present as proof of login. The token says three things: the workspace id, the member email, and an expiry time. To stop people from editing those values, the file signs the token with HMAC, a cryptographic stamp made from a secret key. Think of it like sealing an envelope with a wax seal: anyone can carry the envelope, but only someone with the seal can make a valid one.

The important point is that the token is self-contained. There is no database lookup needed just to know who it claims to be. Instead, verification recalculates the signature using `UFO_TOKEN_SECRET`, compares it safely, decodes the saved JSON, and rejects the token if it is malformed, expired, unsigned, or signed with the wrong secret.

The file also provides two ways to use a verified token. `verify_token` checks that the token belongs to one expected workspace, which is useful when a process is dedicated to one tenant. `workspace_claim` extracts the signed workspace id, which is useful when one shared service receives requests for many workspaces. The cookie and path constants name the login, logout, join, and browser session locations used around this token system.

#### Function details

##### `mint_token`  (lines 37–54)

```
def mint_token(secret: str, workspace_id: str, email: str, ttl: timedelta, now: datetime | None=None) -> str
```

**Purpose**: Creates a new signed bearer token for a workspace member. It is used when the system wants to issue proof that a given email belongs to a given workspace until a specific expiry time.

**Data flow**: It receives a signing secret, a workspace id, an email address, a time-to-live, and optionally a fixed current time. It trims and lowercases the email, builds a small JSON message with workspace, email, and expiry time, base64-encodes that message into URL-safe text, signs that text with HMAC-SHA256 using the secret, and returns one token string made from the encoded body plus the signature. If the secret is empty, it stops with an error instead of creating an unsafe token.

**Call relations**: This is the issuing half of the token story. Other services or setup tools call it when they need to mint a member token, and the verification functions in this same file later expect exactly the same body-and-signature format.

*Call graph*: 4 external calls (urlsafe_b64encode, now, new, dumps).


##### `verified_claims`  (lines 57–80)

```
def verified_claims(token: str, now: int | None=None) -> tuple[str, str] | None
```

**Purpose**: Checks whether a bearer token is real and still valid, then returns the workspace and email it proves. If anything looks wrong, it returns nothing rather than trusting partial information.

**Data flow**: It receives a token string and optionally a current timestamp for testing or controlled checks. It reads the shared signing secret from the environment, splits the token into its encoded body and signature, recalculates the expected signature, compares the two signatures in a timing-safe way, decodes the JSON body, checks that the workspace, email, and expiry fields have the expected types, and rejects the token if it has expired. The result is either a pair of strings, workspace and email, or `None` when the token cannot be trusted.

**Call relations**: This is the common verification core. `verify_token` calls it before checking against one known workspace, and `workspace_claim` calls it before turning the signed workspace text into a UUID. It delegates secret lookup to `_secret` and base64 decoding to `_b64url_decode`.

*Call graph*: calls 2 internal fn (_b64url_decode, _secret); called by 2 (verify_token, workspace_claim); 4 external calls (now, compare_digest, new, loads).


##### `verify_token`  (lines 83–94)

```
def verify_token(token: str, workspace_id: UUID, now: int | None=None) -> str | None
```

**Purpose**: Confirms that a token authenticates a member for one specific workspace. It is useful in a deployment where the service already knows which workspace it is serving.

**Data flow**: It receives a token, an expected workspace UUID, and optionally a current timestamp. First it asks `verified_claims` whether the token is signed, readable, and unexpired. Then it compares the token's workspace claim with the expected workspace id. If they match, it returns the member email in lowercase; if not, it returns `None`.

**Call relations**: This function sits on top of the general token checker. After `verified_claims` proves the token is valid in general, `verify_token` adds the tenant boundary check so a token from one workspace cannot be reused in another.

*Call graph*: calls 1 internal fn (verified_claims).


##### `workspace_claim`  (lines 97–108)

```
def workspace_claim(token: str, now: int | None=None) -> UUID | None
```

**Purpose**: Extracts the workspace UUID from a valid token. It is useful for shared services that serve many workspaces and need the signed token itself to say which workspace the request belongs to.

**Data flow**: It receives a token and optionally a current timestamp. It first asks `verified_claims` to prove that the token is signed, readable, and not expired. Then it tries to parse the workspace claim as a UUID. It returns that UUID when successful, or `None` if verification fails or the workspace text is not a valid UUID.

**Call relations**: This function reuses the same trusted verification path as `verify_token`, but instead of comparing the workspace to a preconfigured value, it hands back the workspace id for the caller to use as request scope.

*Call graph*: calls 1 internal fn (verified_claims); 1 external calls (UUID).


##### `_secret`  (lines 111–115)

```
def _secret() -> str
```

**Purpose**: Reads the shared token-signing secret from the process environment. Verification cannot happen safely without this secret, so the function fails loudly if it is missing.

**Data flow**: It looks up `UFO_TOKEN_SECRET` in environment variables. If a non-empty value is present, it returns that value. If not, it raises a runtime error explaining that the secret must be set before member bearer tokens can be verified.

**Call relations**: `verified_claims` calls this before checking any token signature. Keeping this lookup in one small helper makes all verification use the same environment variable and the same missing-secret behavior.

*Call graph*: called by 1 (verified_claims).


##### `_b64url_decode`  (lines 118–119)

```
def _b64url_decode(value: str) -> bytes
```

**Purpose**: Decodes the URL-safe base64 body of a token back into raw bytes. It also restores missing padding, because the token format strips padding to keep the token shorter and cleaner.

**Data flow**: It receives the encoded body text from a token. It adds the right number of `=` padding characters for base64 decoding, decodes the URL-safe base64 text, and returns the original bytes, which should contain the JSON payload.

**Call relations**: `verified_claims` calls this after a token's signature has matched. That order matters: the code avoids trusting or interpreting the token body until the cryptographic stamp has been checked.

*Call graph*: called by 1 (verified_claims); 1 external calls (urlsafe_b64decode).


### `core/src/ufo/runtime/auth/surface_token.py`

`domain_logic` · `request handling`

A surface token is like a tamper-proof label attached to a link. When someone opens a surface route directly from a URL, the system may not yet have a login cookie or workspace context. This file lets the URL carry a small set of string claims, such as identifiers needed to find the right workspace, and proves that those claims were minted by this deployment.

The file does this with a signed token. “Signed” means the token includes a cryptographic check, using a secret stored in the environment, so that if anyone changes the contents the check fails. The secret itself is never put in the token.

There is one important safety rule: every token includes the name of the surface that created it. When the token is verified, the caller must provide the expected surface name, and the token is accepted only if the names match. This prevents a token made for one surface from being reused at another surface’s route.

The token does not expire and does not grant access by itself. It only supplies trustworthy routing claims. Any route that uses it still needs to apply its own access checks afterward.

#### Function details

##### `mint_surface_token`  (lines 24–35)

```
def mint_surface_token(surface: str, payload: Mapping[str, str]) -> str
```

**Purpose**: This function creates a signed surface token from a surface name and a set of string claims. It is used when the system needs to make a URL-safe address that later proves which surface minted it and what claims it carried.

**Data flow**: It receives a surface name and a mapping of claim names to claim values. It first rejects an empty surface name, and it also rejects any payload that tries to set the reserved claim named "surface". Then it builds a small JSON body containing the surface name plus the supplied claims, reads the signing secret, and passes the bytes to the token-signing helper. The result is a string token that can be placed in a URL or passed around as an opaque address.

**Call relations**: When a caller needs to create one of these surface addresses, this function does the packaging work. It calls _secret to fetch the deployment’s signing key, uses json.dumps to turn the claims into a stable text form, and hands that body to sign_token so the result cannot be quietly changed later.

*Call graph*: calls 1 internal fn (_secret); 2 external calls (dumps, sign_token).


##### `verify_surface_token`  (lines 38–51)

```
def verify_surface_token(surface: str, token: str) -> dict[str, str] | None
```

**Purpose**: This function checks whether a surface token is genuine and meant for the surface that is asking. If the token is valid, it returns the claims inside it; if anything looks wrong, it returns None instead of trusting the token.

**Data flow**: It receives the expected surface name and a token string. It reads the signing secret and asks the token verifier to prove the token was signed with that secret. If the signature is bad, the token cannot be decoded as valid JSON, the payload is not a dictionary, or the embedded surface name does not match the expected one, the function returns None. If those checks pass, it removes the reserved surface claim, confirms all remaining claim names and values are strings, and returns those claims as a dictionary.

**Call relations**: This is the receiving half of the surface-token flow. A route or resolver calls it when a request arrives with a token from a URL. It relies on _secret for the same deployment secret used during minting, uses verify_token to reject forged or altered tokens, and uses json.loads to turn the verified body back into claims the route can use.

*Call graph*: calls 1 internal fn (_secret); 2 external calls (loads, verify_token).


##### `_secret`  (lines 54–58)

```
def _secret() -> str
```

**Purpose**: This helper fetches the shared signing secret from the process environment. It keeps token creation and token verification using the same required setting.

**Data flow**: It reads the environment variable named by UFO_TOKEN_SECRET_ENV. If the variable is present and non-empty, it returns that string. If it is missing or empty, it raises an error, because surface tokens cannot be safely signed or verified without the secret.

**Call relations**: Both mint_surface_token and verify_surface_token call this helper before doing cryptographic work. That means token minting and token checking fail loudly if the deployment has not been configured with the required secret, rather than silently producing unsafe or meaningless results.

*Call graph*: called by 2 (mint_surface_token, verify_surface_token).


### `core/src/ufo/runtime/auth/token_signing.py`

`domain_logic` · `request handling`

This file solves a common authentication problem: the system wants to send or store a small payload, then later know that nobody changed it. The token is “opaque,” meaning this file does not care what the payload means. It only turns raw bytes into a safe text form and attaches a signature.

The signature is made with HMAC, a standard method that combines a secret key with a message to produce a short proof that only someone with the same secret can recreate. The payload is encoded with base64url, which turns bytes into text that is safe to put in URLs or headers. The final token looks like two parts joined by a dot: the encoded payload, then the signature.

When checking a token, the file first makes sure the token has both parts. It then recomputes the expected signature from the payload text and compares it carefully using a constant-time comparison, which avoids leaking clues through timing differences. Only after the signature is proven correct does it decode the payload back into bytes. If anything is missing, changed, or unreadable, it raises SignedTokenError, a clear signal that the token cannot be trusted.

#### Function details

##### `sign_detached`  (lines 12–14)

```
def sign_detached(secret: bytes, message: bytes) -> str
```

**Purpose**: This function makes a standalone signature for a message using a shared secret. It is useful when the caller wants a tamper-evidence code for some bytes, but does not want this function to package the message and signature together.

**Data flow**: It receives a secret key as bytes and a message as bytes. It mixes them with HMAC using SHA-256, then turns the resulting raw signature bytes into base64url text and removes padding characters. It returns that signature as a string.

**Call relations**: When sign_token builds a full token, it calls this function to seal the encoded payload. verify_detached also calls it to recreate the expected seal before comparing it with the seal that came in from outside.

*Call graph*: called by 2 (sign_token, verify_detached); 2 external calls (urlsafe_b64encode, new).


##### `verify_detached`  (lines 17–18)

```
def verify_detached(secret: bytes, message: bytes, signature: str) -> bool
```

**Purpose**: This function checks whether a given signature really matches a message and secret. It answers yes or no without decoding or interpreting the message itself.

**Data flow**: It receives the secret, the message, and a signature string to check. It uses sign_detached to calculate what the signature should be, then compares the provided and expected signatures with a timing-safe comparison. It returns true if they match and false if they do not.

**Call relations**: verify_token relies on this function after splitting a token into its payload part and signature part. This function hands back the trust decision, letting verify_token decide whether to continue or reject the token.

*Call graph*: calls 1 internal fn (sign_detached); called by 1 (verify_token); 1 external calls (compare_digest).


##### `sign_token`  (lines 21–23)

```
def sign_token(secret: bytes, payload: bytes) -> str
```

**Purpose**: This function turns raw payload bytes into a complete signed token string. Someone would use it when they need to give a client or another part of the system a value that can later be checked for tampering.

**Data flow**: It receives a secret and a payload as bytes. It first encodes the payload into base64url text, then asks sign_detached to sign that text. It returns one string made from the encoded payload, a dot, and the signature.

**Call relations**: This is the token-making side of the file. It builds on sign_detached for the cryptographic seal, producing the exact format that verify_token expects to receive later.

*Call graph*: calls 1 internal fn (sign_detached); 1 external calls (urlsafe_b64encode).


##### `verify_token`  (lines 26–35)

```
def verify_token(token: str, secret: bytes) -> bytes
```

**Purpose**: This function checks a complete signed token and returns the original payload if the token is trustworthy. If the token is badly shaped, has been changed, or cannot be decoded, it raises SignedTokenError instead of returning unsafe data.

**Data flow**: It receives a token string and the shared secret. It splits the token around the dot into the encoded payload and signature, rejects it if either part is missing, then uses verify_detached to confirm the signature. If the signature is valid, it decodes the base64url payload back into bytes and returns those bytes. If any step fails, it raises a clear token-signing error.

**Call relations**: This is the checking side of the file. Code that receives a token calls this function before trusting its contents. Internally it delegates the signature decision to verify_detached, then performs the final payload decoding only after the signature has passed.

*Call graph*: calls 1 internal fn (verify_detached); 2 external calls (__init__, b64decode).


### `core/src/ufo/runtime/ext/operator.py`

`domain_logic` · `operator surface request handling`

Operator tools need stronger and broader access than normal workspace pages: an operator may need to inspect many workspaces, but the system must not leak the long-lived access token into URLs, browser history, or logs. This file solves that by accepting the token only from an Authorization header, a private browser cookie, or the one form POST that opens the session. It never accepts the token from a query string.

The first part checks a request’s bearer token and turns it into two facts: the claimed workspace and the user email. It then applies the important gate: only email addresses from the configured operator domain may use these surfaces. Once that is true, the request can either use its own claimed workspace or choose another workspace through `?ws=`.

The second part creates the browser session cookie after sign-in. One cookie is shared by all operator surfaces, so the operator does not have to log in separately for each tool.

The last part builds a “fleet directory”: a compact index of all workspaces and recently active conversations. It deliberately does this in two passes. First it reads only IDs and timestamps across the whole fleet. Then it re-enters each workspace separately to read human-facing details like domain, title, and member counts under the normal workspace safety rules.

#### Function details

##### `operator_claims`  (lines 45–63)

```
async def operator_claims(request: Request) -> tuple[str, str] | None
```

**Purpose**: This function tries to prove who the operator is from the credentials carried by an incoming web request. It checks safe places only: the Authorization header, then the operator session cookie, then the posted form token used during sign-in.

**Data flow**: It receives a request. It first looks for a bearer token in the Authorization header, then for the shared operator cookie, and finally, only on POST requests, for a `token` field in the submitted form. Each candidate token is passed to the token checker. If one works, the function returns the workspace and email from the token; if none work, it returns nothing.

**Call relations**: When an operator-only surface needs to decide whether a request is allowed, `resolve_operator_workspace` calls this function first. This function delegates the actual token verification to `_candidate_claims` and reads form data from the request only for the sign-in POST path.

*Call graph*: calls 1 internal fn (_candidate_claims); called by 1 (resolve_operator_workspace); 1 external calls (form).


##### `_candidate_claims`  (lines 66–68)

```
def _candidate_claims(candidate: str) -> tuple[str, str] | None
```

**Purpose**: This small helper cleans up one possible token and asks the authentication system whether it is valid. It keeps the rest of the file from repeating the same trim-and-check step.

**Data flow**: It receives a possible token as text. It removes surrounding spaces. If the result is empty, it returns nothing. Otherwise it sends the token to `verified_claims`, which checks the signed bearer token and returns the claims if the token is valid.

**Call relations**: `operator_claims` calls this helper for each possible credential source. The helper is the narrow bridge to the bearer-token verifier, so this file does not keep or directly handle the signing secret itself.

*Call graph*: called by 1 (operator_claims); 1 external calls (verified_claims).


##### `resolve_operator_workspace`  (lines 71–111)

```
async def resolve_operator_workspace(request: Request, _auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: This function decides which workspace an operator request is allowed to act within. It also redirects a browser page visit to the sign-in page when the operator has not yet opened a session.

**Data flow**: It receives the web request and the surface authentication object. It asks `operator_claims` for verified token claims. If there are no claims and this is a browser GET for an operator surface page, it returns a redirect to the login page; otherwise it rejects by returning nothing. If claims exist, it checks that the email domain is the operator domain. Then it chooses the workspace: without `?ws=`, it uses the workspace ID from the token; with `?ws=`, it accepts either a workspace UUID or a domain name that is resolved to a workspace ID. If no current workspace exists for that domain, it creates the predictable UUID that such a domain workspace would use.

**Call relations**: Operator surfaces use this function as their gatekeeper before serving a page or API request. It relies on `operator_claims` to authenticate the request, uses `email_domain` to enforce the operator-only rule, may query the owner database through `owner_tx` and `workspace_by_domain`, and returns either a workspace ID, a redirect response, or rejection.

*Call graph*: calls 1 internal fn (operator_claims); 6 external calls (owner_tx, RedirectResponse, email_domain, workspace_by_domain, UUID, uuid5).


##### `bind_operator_session`  (lines 114–132)

```
async def bind_operator_session(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function opens the shared operator browser session after sign-in. It takes the posted bearer token, stores it in a private HTTP-only cookie, and sends the browser back to the operator page.

**Data flow**: It receives the surface context and the request. It reads the submitted form and looks for the `token` field. If the token is missing or blank, it returns a JSON error with a bad-request status. If the token is present, it creates a redirect response back to the current URL and attaches the operator session cookie, using the surface’s secure-cookie setting.

**Call relations**: This is called after the surface’s authentication resolver has already verified the posted token. It uses request form reading, `JSONResponse` for the error case, `RedirectResponse` for the success case, and `set_session_cookie` to write the browser cookie that later calls to `operator_claims` will read.

*Call graph*: 4 external calls (JSONResponse, RedirectResponse, form, set_session_cookie).


##### `FleetWorkspace._aware_utc`  (lines 150–151)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: This validator makes sure a workspace activity time has timezone information. That prevents later code from accidentally mixing timezone-aware and timezone-less dates.

**Data flow**: It receives the `last_turn_at` value while a `FleetWorkspace` model is being built. If the value is missing, or already has a timezone, it leaves it alone. If it is a plain timestamp without a timezone, it marks it as UTC and returns that adjusted value.

**Call relations**: Pydantic, the data-model library used here, calls this automatically when creating a `FleetWorkspace`. It is not called directly by the directory reader, but it runs when `_scoped` constructs a workspace listing item.

*Call graph*: 1 external calls (replace).


##### `FleetThread._aware_utc`  (lines 169–170)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: This validator makes sure a recent-conversation timestamp is timezone-aware. It protects the operator index from confusing or unsafe date comparisons and display behavior.

**Data flow**: It receives the `last_turn_at` timestamp while a `FleetThread` model is being created. If the timestamp already includes timezone information, it is returned unchanged. If it does not, the function marks it as UTC and returns the corrected timestamp.

**Call relations**: Pydantic calls this automatically during `FleetThread` creation. `FleetDirectory.read` creates those thread objects after collecting recent activity, so this validator quietly normalizes their timestamps at that point.

*Call graph*: 1 external calls (replace).


##### `FleetDirectory.read`  (lines 202–233)

```
async def read(self) -> FleetListing
```

**Purpose**: This is the main method that builds the operator’s fleet index: the list of workspaces and the most recently active conversations across them. It turns database rows into clean, display-ready objects.

**Data flow**: It starts by asking `_enumerate` for two cross-fleet lists: workspace activity and recent conversation IDs. It groups the recent conversation IDs by workspace. Then, for each workspace, it temporarily scopes the system to that workspace and calls `_scoped` to read details such as domain, member count, conversation count, and conversation titles. Finally it combines the workspace summaries and recent thread summaries into one `FleetListing` result.

**Call relations**: This is the public entry point of `FleetDirectory`. It calls `_enumerate` for the broad ID-and-time scan, uses `ws` to re-bind each later read to one workspace, calls `_scoped` for human-readable details, and then builds `FleetWorkspace`, `FleetThread`, and `FleetListing` objects for the operator UI.

*Call graph*: calls 2 internal fn (_enumerate, _scoped); 3 external calls (__init__, __init__, ws).


##### `FleetDirectory._enumerate`  (lines 235–273)

```
async def _enumerate(self) -> tuple[Sequence[sa.Row[Any]], Sequence[sa.Row[Any]]]
```

**Purpose**: This method performs the fleet-wide scan that finds all workspaces and the most recently active conversations. It intentionally reads only identifiers and timestamps, not user-facing text.

**Data flow**: It builds two database queries. One lists every workspace, including workspaces with no turns yet, ordered by latest root-level activity. The other finds the most recently active conversations, counts their root-level turns, and limits the result to the directory’s thread limit. It runs both queries through the owner-level database connection and returns the two sets of rows.

**Call relations**: `FleetDirectory.read` calls this first. This method uses `owner_tx`, which is appropriate for cross-workspace ID discovery, and SQLAlchemy query building. It avoids reading names, titles, or other scoped details; those are handed off to `_scoped` later.

*Call graph*: called by 1 (read); 2 external calls (select, owner_tx).


##### `FleetDirectory._scoped`  (lines 275–323)

```
async def _scoped(self, workspace_id: UUID, last_turn_at: datetime | None, conversation_ids: Sequence[UUID]) -> tuple[FleetWorkspace, dict[UUID, sa.Row[Any]]]
```

**Purpose**: This method reads the human-readable details for one workspace while the system is scoped to that workspace. It fills in the parts of the operator directory that people actually see, such as domain, counts, and conversation titles.

**Data flow**: It receives a workspace ID, that workspace’s latest activity time, and the conversation IDs that should be opened for display. Inside a workspace-scoped database transaction, it reads the workspace domain, counts members, counts non-subagent conversations, and fetches surface, queue key, and title for the requested conversations. It returns one `FleetWorkspace` summary plus a dictionary of conversation rows keyed by conversation ID.

**Call relations**: `FleetDirectory.read` calls this once for each workspace after entering that workspace with `ws`. It uses `workspace_tx` for the scoped transaction, `workspace_domain` for the displayed domain, SQL queries for counts and conversation details, and constructs the `FleetWorkspace` object that later becomes part of the final `FleetListing`.

*Call graph*: called by 1 (read); 4 external calls (__init__, select, workspace_tx, workspace_domain).


### `core/src/ufo/sdk/bearer.py`

`io_transport` · `request handling`

This file is a small public doorway into the project’s bearer-token authentication code. A bearer token is like a stamped wristband: whoever presents it can be recognized, but the important question is whether the stamp is real. The actual checking logic lives in `ufo.runtime.auth.bearer`; this file simply makes the safe parts available under the SDK path for surface extensions to import.

The key design point is separation of power. Extensions need to verify tokens that the gateway minted, but they should not be able to mint their own tokens. To support that, this module exposes constants such as the login path, logout path, and session cookie name, plus helper functions that verify a token and read trusted claims from it. The comment explains that these helpers resolve `UFO_TOKEN_SECRET` themselves, meaning extension authors do not receive or pass around the signing secret directly.

Without this file, extension code would either need to import from an internal authentication module, which makes the project harder to keep stable, or it might be tempted to handle secrets itself. This file acts like a clean service window: extensions can ask “is this token valid and what workspace does it belong to?” without gaining access to the machinery that creates tokens.

## 📊 State Registers Touched

- `reg-deployment-config` — The merged deployment settings that tell the system what product, services, addresses, databases, sandboxes, and safety defaults to use.
- `reg-workspace-records` — The saved workspace records that identify each customer space and hold its limits, setup state, balance settings, and routing boundaries.
- `reg-member-identity` — The shared record of who each user is, how they logged in, what workspace they belong to, and what timezone or invitation state is known.
- `reg-auth-tokens` — The signed login, surface, artifact, and SDK tokens used to prove that a caller or link is allowed to act.
- `reg-agent-registry` — The durable list of agents, including their names, visibility, owners, purposes, model behavior, provisioning source, and tool policy.
- `reg-conversation-turn-state` — The conversation and turn queue state that tracks each unit of agent work from admission through running, completion, cancellation, or recovery.
- `reg-inbound-message-log` — The saved incoming-message log that keeps outside chat, terminal, web, and scheduled events ordered, unique, and ready to become turns.
- `reg-surface-routing` — The shared mapping from external surfaces such as Slack, iMessage, web, terminal, and hosted sites to the right workspace, agent, and conversation.
- `reg-transcript-history` — The saved conversation transcript, including compacted summaries and durable final results that later stages read instead of relying on memory.
- `reg-credentials-and-grants` — The encrypted secrets, account connections, and grants that say which member or agent may use an outside service.
- `reg-egress-network-policy` — The outbound network permission state that decides which external hosts, proxies, and secret injections are allowed for a workspace or agent.
- `reg-sandbox-handles` — The remembered sandbox or workspace handle for each conversation so tools can resume the same isolated files, terminals, browsers, and services.
- `reg-execution-environment` — The controlled runtime environment given to commands, files, terminals, browsers, and documents, including safe environment variables and containment rules.
- `reg-tool-catalog-policy` — The current tool catalog and allowlist rules that say which built-in, extension, connector, MCP, and sandbox tools may be called.
- `reg-observability-traces` — The shared logs, metrics, traces, traceparent links, and safety-filtered operator views used to understand what the system is doing.
- `reg-object-store` — The workspace object records and change journal for agents, members, files, credentials, sites, connectors, memory records, reports, and extension objects.
- `reg-audience-visibility` — The saved visibility and audience rules that decide who may see a conversation, transcript, agent, source, artifact, or object.
- `reg-artifact-blob-store` — The shared file, blob, attachment, artifact, signed download, and media-preview storage used to publish and recover produced work.
- `reg-source-page-sync-state` — The source and page records that remember connected feeds, cursors, backoff, deletes, ownership, grants, and the latest synced content.
- `reg-scheduled-jobs` — The durable background-job state for scheduled tasks, pauses, monitor checks, report writing, thumbnail repair, product metrics, and self-improvement runs.
- `reg-hosted-site-registry` — The hosted-site records that remember who owns each site, which conversation created it, where it runs, and how previews or sharing are allowed.
- `reg-prompt-and-delivery-policy` — The prompt, delivery-rule, compaction, and prompt-change proposal state that controls what instructions are rendered and how replies should be shaped.
- `reg-request-actor-scope` — Context-local current workspace, member, acting agent, and object/action scope carried through authorization, database boundaries, object APIs, tools, and egress checks.
- `reg-membership-access-policy` — Durable workspace membership, owner/admin role, seat, invitation, and mutation-permission state used to decide what a member may manage beyond simple object visibility.
- `reg-security-audit-log` — Durable audit records for sensitive reads and administrative/object changes, such as transcript access and object-change journaling.
- `reg-backend-provider-registry` — Process-local registry mapping provider names to active backend implementations for models, search, embeddings, memory, connectors, browser access, auth, billing, and feature services.

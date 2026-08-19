# Network egress policy and proxy authorization  `stage-20.4`

This stage is shared behind-the-scenes support for sandboxed agent runs. When an agent tries to reach the outside network, it should not be able to call any service or use any secret by accident. This stage acts like a checkpoint between the sandbox and the internet.

egress_rules.py builds the actual rulebook. It looks at things like enabled models, user credentials, granted permissions, connectors, and artifact storage, then turns them into specific proxy rules: which destinations are allowed, which credentials can be attached, and how usage should be counted.

egress_resolver.py chooses the right rulebook for one particular agent run or probe. It makes sure rules are tied to the correct workspace, agent, and user, so credentials or permissions do not leak between jobs.

egress_control.py exposes a private web API for the Rust egress proxy. The proxy asks this API before allowing outbound traffic or attaching secrets. Core keeps the sensitive decisions and billing records, while the proxy simply enforces the answer.

## Files in this stage

### Egress resolution
Determines the applicable outbound access policy for an agent run or probe while preserving workspace, agent, and user boundaries.

### `core/src/ufo/egress_resolver.py`

`domain_logic` · `request handling`

When an agent tries to connect to the outside world, the proxy needs a fresh answer to a sensitive question: “What is this specific run allowed to reach, and what secrets may it use?” This file builds that answer.

The main class, PerAgentRules, starts with a base set of rules that apply everywhere. If the caller presents a valid run token or probe token, it looks up which agent that token belongs to inside that token’s workspace. A workspace is a tenant boundary: one customer’s data and secrets must not leak into another’s. It then adds only the pieces that are allowed for that agent: internet access if enabled, cache and preview service hosts, workspace credential injections, OAuth-style grants, and command-line connector forwarding rules.

A useful analogy is a building security desk. The token is the badge, the database is the visitor log, and the returned rules are the doors this person may open right now. The desk checks the badge every time instead of trusting yesterday’s list, so newly added grants work quickly and ended runs stop getting access.

Probe tokens follow almost the same path as normal runs, but one important secret is removed: the deployment’s own model key. That keeps probes from silently inheriting a privileged model credential they should not carry.

#### Function details

##### `PerAgentRules.resolve`  (lines 79–121)

```
async def resolve(self, principal: EgressPrincipal | None) -> tuple[Rule, ...]
```

**Purpose**: Builds the complete egress rule list for a run token, a probe token, or no token. It is the main policy decision point: it decides which hosts, credential injections, and connector forwards the proxy should allow for this one request.

**Data flow**: It receives either no principal, a RunToken, or a ProbeToken. With no principal, it returns only the base rules. With a token, it reads the token’s workspace, finds the agent and internet policy through _turn_of or _conversation_of, then layers in allowed internet rules, service hosts, stored credentials, active grants, and CLI forwarding rules. If the token is for a probe, it removes the special model-key injection before returning the final tuple of rules.

**Call relations**: This is the hub of the file. It calls _turn_of for normal run tokens and _conversation_of for probe tokens to learn whose authority the request carries. It uses the workspace and agent context helpers so later credential and grant reads happen under the right tenant and agent. It then hands active grants to derive_grant_rules and derive_cli_rules, asks derive_credential_rules to turn configured credential slots into injection rules, creates ServiceRule entries for internal service hosts, and finally calls _without_the_model_key when the caller is a probe.

*Call graph*: calls 3 internal fn (_conversation_of, _turn_of, _without_the_model_key); 6 external calls (__init__, agent, derive_cli_rules, derive_credential_rules, derive_grant_rules, ws).


##### `PerAgentRules._turn_of`  (lines 123–147)

```
async def _turn_of(self, run: RunToken) -> _Authority | None
```

**Purpose**: Finds the agent and internet-access setting for a normal run token. This tells resolve which agent’s policy should be used for that run.

**Data flow**: It receives a RunToken containing a workspace id, turn id, and acting member id. It opens a workspace-scoped database transaction and looks up the turn joined to its agent, making sure both belong to the same workspace. If a matching row exists, it returns an _Authority object with the agent id, the agent’s internet setting, and the acting member from the token; if not, it returns None.

**Call relations**: resolve calls this when the principal is a RunToken. The result either lets resolve continue building agent-specific rules or, if no row is found, causes resolve to fall back to the base rules only. Internally it uses SQLAlchemy to form the database query and workspace_tx to run it safely inside the current workspace database context.

*Call graph*: called by 1 (resolve); 3 external calls (__init__, select, workspace_tx).


##### `PerAgentRules._conversation_of`  (lines 149–178)

```
async def _conversation_of(self, probe: ProbeToken) -> _Authority | None
```

**Purpose**: Finds the agent and internet-access setting for a probe token. Probes are tied to a conversation rather than a specific turn, so this function follows that different path to reach the same kind of authority information.

**Data flow**: It receives a ProbeToken with a workspace id, conversation id, and possibly an acting member id. It opens a workspace-scoped database transaction, looks up the conversation joined to its agent, and checks that both records belong to the token’s workspace. If found, it returns an _Authority with the agent id, internet setting, and acting member from the token; otherwise it returns None.

**Call relations**: resolve calls this when the principal is a ProbeToken. Its answer lets probe requests reuse the same rule-building chain as normal runs, while still starting from the conversation named by the probe. Like _turn_of, it relies on SQLAlchemy for the query and workspace_tx for the scoped database read.

*Call graph*: called by 1 (resolve); 3 external calls (__init__, select, workspace_tx).


##### `PerAgentRules._without_the_model_key`  (lines 180–191)

```
def _without_the_model_key(self, rules: tuple[Rule, ...]) -> tuple[Rule, ...]
```

**Purpose**: Removes the deployment’s own model-key injection from a rule list. This matters because probes should be allowed to reach model hosts only without automatically receiving that privileged key.

**Data flow**: It receives a tuple of already-built rules. It filters through them and drops any InjectionRule whose sentinel marker contains the special model-key marker. It returns a new tuple containing every other rule unchanged.

**Call relations**: resolve calls this only after it has built rules for a ProbeToken. It is the final safety pass for probes: all normal credential and grant rules may remain, but the special model credential is withheld before the rule list goes back to the proxy.

*Call graph*: called by 1 (resolve).


##### `PerAgentRules.turn_live`  (lines 193–224)

```
async def turn_live(self, run: RunToken) -> int | None
```

**Purpose**: Checks whether a run token still names a turn that is currently running, and returns the workspace’s egress-rule generation number if it does. This is the live gate that prevents ended or unknown runs from continuing to use secret-bearing network rules.

**Data flow**: It receives a RunToken. Inside that token’s workspace, it reads the turn’s status together with the workspace’s egress rules generation counter. If the turn is missing or is not marked RUNNING, it returns None. If the turn is running, it returns the generation number, which tells callers which version of the rules they are authorizing against.

**Call relations**: This method is separate from resolve because it answers a slightly different question: not “what are the rules?” but “is this run still allowed to connect right now?” It uses the workspace context helper, opens a workspace_tx database transaction, and builds a SQL query with SQLAlchemy. The surrounding egress-control path can use its result before allowing a CONNECT request to receive credential injections.

*Call graph*: 3 external calls (select, workspace_tx, ws).


##### `PerAgentRules.rules_generation`  (lines 226–237)

```
async def rules_generation(self, workspace_id: UUID) -> int
```

**Purpose**: Reads the current egress-rule generation counter for a workspace. The counter lets callers notice when cached rules may be stale and should be refreshed.

**Data flow**: It receives a workspace id. It enters that workspace context, opens a database transaction, selects the workspace’s egress_rules_generation value, and returns it as an integer.

**Call relations**: This is a small lookup used by the egress-control flow when it needs the latest workspace rule version directly, especially for paths that do not go through turn_live. It uses the same workspace scoping and database transaction pattern as turn_live so the read is tied to the correct tenant.

*Call graph*: 3 external calls (select, workspace_tx, ws).


### Proxy authorization API
Exposes the private core-facing API used by the Rust egress proxy to authorize traffic, attach credentials, and record usage.

### `core/src/ufo/egress_control.py`

`io_transport` · `request handling`

The Rust egress proxy sits in the data path, where it sees outgoing network requests from a sandbox. But it is deliberately kept “thin”: it does not know customer secrets, billing rules, or workspace policy. This file provides the private control panel it calls instead.

The main class, EgressControl, builds FastAPI routers. FastAPI is a Python web framework; a router is a bundle of URL endpoints. The proxy calls endpoints under `/internal/egress/` using a shared bearer token, like showing a staff badge before entering a back office. Each request also carries a run or probe token, which identifies the sandbox session and workspace. EgressControl checks that token before answering.

The endpoints cover four jobs. They say whether a connection is still allowed, return the current rule set in the JSON shape the Rust proxy expects, accept batched metering records for billing and metrics, and forward selected credential-backed requests through connector code. A separate router, guarded by a different token, lets a cache daemon ask only for Git credentials, so that credential cannot be used to access the broader egress control API.

The file also contains small request and response models. These describe the JSON bodies exchanged with the proxy and help FastAPI validate them before the logic runs.

#### Function details

##### `rule_json`  (lines 53–82)

```
def rule_json(rule: Rule) -> dict[str, object]
```

**Purpose**: Turns one internal egress rule into the exact JSON format expected by the Rust proxy. This matters because the proxy enforces rules on live network traffic, so both sides must agree on the names and shape of each rule.

**Data flow**: It receives a rule object, checks which kind of rule it is, and copies the useful fields into a plain dictionary. For example, a host allow-list becomes a `scope` rule with sorted hosts, while a secret-injection rule becomes a dictionary containing the host, header, fake marker, and real value. The output is JSON-ready data that can be sent over the private API.

**Call relations**: EgressControl._resolve calls this after asking the resolver for the allowed rules. It acts as the translator between Python’s rule objects and the Rust proxy’s wire format.

*Call graph*: called by 1 (_resolve).


##### `EgressControl.router`  (lines 169–175)

```
def router(self) -> APIRouter
```

**Purpose**: Builds the private egress-control API used by the Rust proxy. It groups the authorization, rule resolution, metering, and forwarding endpoints under one URL prefix and protects them with the main control token.

**Data flow**: It starts with the EgressControl instance, creates a FastAPI router under `/internal/egress`, attaches the shared-token guard, and registers four POST routes. The result is a router object that the wider server can mount.

**Call relations**: This is called during server setup when core exposes its internal routes. Once mounted, FastAPI sends incoming proxy requests to _authorize, _resolve, _meter, or _forward, with _guard checked first.

*Call graph*: 2 external calls (APIRouter, Depends).


##### `EgressControl.git_credential_router`  (lines 177–182)

```
def git_credential_router(self) -> APIRouter
```

**Purpose**: Builds a separate private endpoint for the cache daemon to request Git credentials. It is intentionally separate from the main egress API so the cache daemon’s token cannot be used for broader secrets or metering operations.

**Data flow**: It creates a FastAPI router under `/internal`, attaches the cache-only token guard, and registers one POST route at `/git-credential`. The output is a router the server can mount alongside the main API.

**Call relations**: This is used during server setup for the cache credential callback. When the cache daemon calls that endpoint, FastAPI first runs _cache_guard and then hands the request to _git_credential.

*Call graph*: 2 external calls (APIRouter, Depends).


##### `EgressControl._guard`  (lines 184–186)

```
async def _guard(self, authorization: Annotated[str, Header()]='') -> None
```

**Purpose**: Checks that a request to the main egress-control API has the correct bearer token. It stops unauthenticated callers before any policy lookup, secret access, or billing write can happen.

**Data flow**: It reads the HTTP Authorization header and compares it with `Bearer ` plus the configured control token. If they match, it returns normally and the request continues. If not, it raises a 401 unauthorized web error.

**Call relations**: FastAPI runs this as a dependency for every route created by EgressControl.router. It is the front-door badge check for _authorize, _resolve, _meter, and _forward.

*Call graph*: 1 external calls (HTTPException).


##### `EgressControl._cache_guard`  (lines 188–190)

```
async def _cache_guard(self, authorization: Annotated[str, Header()]='') -> None
```

**Purpose**: Checks that a Git credential request has the cache daemon’s separate bearer token. This keeps the cache daemon limited to its one credential endpoint.

**Data flow**: It reads the Authorization header and compares it with the configured cache-control token. A match lets the request continue; a mismatch raises a 401 unauthorized web error.

**Call relations**: FastAPI runs this before requests to the router created by EgressControl.git_credential_router. It protects _git_credential without granting access to the main egress-control routes.

*Call graph*: 1 external calls (HTTPException).


##### `EgressControl._authorize`  (lines 192–203)

```
async def _authorize(self, body: AuthorizeRequest) -> AuthorizeResponse
```

**Purpose**: Answers the proxy’s question: “Is this sandbox session still allowed to make egress requests?” It also returns a rule generation number so the proxy can know whether its cached rules are current.

**Data flow**: It receives a request containing the raw proxy authorization token. It decodes that token into either a run token, a probe token, or nothing. For a run token, it asks the resolver whether the turn is still live. For a probe token, it checks the expiry time and then reads the workspace’s rule generation. It returns an AuthorizeResponse saying allowed or denied, with a generation when available.

**Call relations**: FastAPI calls this for the `/authorize` endpoint after _guard passes. It relies on _principal to understand the token, then uses the resolver as the source of truth for liveness and rule generation.

*Call graph*: calls 1 internal fn (_principal); 2 external calls (__init__, now).


##### `EgressControl._resolve`  (lines 205–207)

```
async def _resolve(self, body: ResolveRequest) -> dict[str, object]
```

**Purpose**: Returns the actual egress rules that the proxy should enforce for a run or probe. This is how policy stored in core becomes concrete instructions in the network proxy.

**Data flow**: It receives a proxy authorization token, decodes it through _principal, and asks the resolver for the rules that apply to that principal. Each rule is converted with rule_json. The result is a dictionary containing a list of JSON-ready rules.

**Call relations**: FastAPI calls this for the `/resolve` endpoint after _guard passes. It sits between the policy resolver and the Rust proxy, handing resolver output to rule_json so it can cross the API boundary safely.

*Call graph*: calls 2 internal fn (_principal, rule_json).


##### `EgressControl._meter`  (lines 209–264)

```
async def _meter(self, body: MeterRequest) -> dict[str, object]
```

**Purpose**: Accepts usage reports from the proxy and records them for metrics and billing. It batches repeated records together first, so one proxy call can efficiently report many events.

**Data flow**: It receives a list of meter records. It groups plain egress counts by workspace and turn, groups token usage by workspace, turn, and model, and counts custom metric events by host and dimension. It emits metric counters, then opens each workspace context and database transaction to write probe egress, normal egress, and model token usage. It returns an empty response after the writes are done.

**Call relations**: FastAPI calls this for the `/meter` endpoint after _guard passes. It calls _priced_cache_write before token billing so cache-write usage is priced correctly, then hands final accounting data to record_probe_egress_request, record_egress_request, and record_sandbox_tokens.

*Call graph*: calls 1 internal fn (_priced_cache_write); 7 external calls (__init__, record_egress_request, record_probe_egress_request, record_sandbox_tokens, workspace_tx, emit_metric, ws).


##### `EgressControl._priced_cache_write`  (lines 266–279)

```
def _priced_cache_write(self, model: str, usage: Usage) -> Usage
```

**Purpose**: Adjusts token usage when a model does not have a separate price for 30-minute cache writes. In that case, those tokens are billed as normal input tokens instead.

**Data flow**: It receives a model name and a Usage object. It looks up that model’s pricing. If the model supports 30-minute cache-write pricing, or there are no such tokens, it returns the usage unchanged. Otherwise, it creates a copy where the 30-minute cache-write count is moved into input tokens and set to zero.

**Call relations**: EgressControl._meter calls this right before recording sandbox token usage. It keeps proxy-reported usage aligned with the same pricing rules used elsewhere in core.

*Call graph*: called by 1 (_meter); 1 external calls (model_copy).


##### `EgressControl._forward`  (lines 281–315)

```
async def _forward(self, body: ForwardRequest) -> ForwardResponse
```

**Purpose**: Lets the proxy forward a credential-backed request through the right connector, but only if the sandbox principal is allowed to use that account. This prevents the proxy from needing to hold connector credentials itself.

**Data flow**: It receives the proxy token, target account ID, HTTP method, URL, headers, and a base64-encoded body. It decodes the principal and rejects the request if the token is invalid. It then checks the workspace database for the requested account and confirms the account is either shared or owned by the acting member. If the provider has a configured CLI forwarder, it decodes the body, sends the request through that forwarder, and returns the status, headers, and base64-encoded response body.

**Call relations**: FastAPI calls this for the `/forward` endpoint after _guard passes. It uses _principal for identity, workspace_tx and ws for scoped database access, and then hands the permitted request to the provider-specific connector forwarder.

*Call graph*: calls 1 internal fn (_principal); 7 external calls (__init__, b64decode, b64encode, HTTPException, select, workspace_tx, ws).


##### `EgressControl._git_credential`  (lines 317–331)

```
async def _git_credential(self, body: GitCredentialRequest) -> dict[str, object]
```

**Purpose**: Answers the cache daemon’s request for a Git credential for one workspace and host. If no matching credential can be found, it safely tells the daemon to fetch anonymously instead of leaking another credential.

**Data flow**: It receives an optional workspace ID and host. If either is missing, or if no credential system is configured, it returns a public principal. Otherwise, it enters the workspace context and asks _git_credential_for to find a matching username and secret. If none is found, it returns no credential and a public principal; if one is found, it returns the username, token, and a workspace-specific principal label.

**Call relations**: FastAPI calls this for the `/internal/git-credential` endpoint after _cache_guard passes. It delegates the careful slot-by-slot lookup to _git_credential_for.

*Call graph*: calls 1 internal fn (_git_credential_for); 1 external calls (ws).


##### `EgressControl._git_credential_for`  (lines 333–359)

```
async def _git_credential_for(self, workspace_id: UUID, host: str) -> tuple[str, str] | None
```

**Purpose**: Searches the workspace’s configured credential slots for the Git basic-auth secret that matches a specific host. It is careful to skip broken or non-matching slots instead of falling through to an unrelated identity.

**Data flow**: It receives a workspace ID and host. It walks through the resolver’s credential slots, keeps only slots configured for Git basic-user injection, checks whether each slot is set, compares the stored credential host with the requested host, and then reads the secret. If a lookup fails, it logs a warning and continues. It returns a `(username, secret)` pair for the first valid match, or `None` if there is no match.

**Call relations**: EgressControl._git_credential calls this after entering the correct workspace context. It uses the credential helper functions to inspect and read slots, and uses warn to record slot failures without failing the whole Git credential request.

*Call graph*: called by 1 (_git_credential); 4 external calls (credential_host, slot_is_set, slot_secret, warn).


##### `EgressControl._principal`  (lines 361–371)

```
def _principal(self, proxy_auth: str) -> EgressPrincipal | None
```

**Purpose**: Decodes the proxy authorization value into the sandbox identity it represents. It accepts both normal run tokens and probe tokens, returning nothing when the value is missing or invalid.

**Data flow**: It receives the raw proxy authorization string. If the string is empty, it returns `None`. Otherwise, it first tries to decode it as a run token using the configured run-token codec. If that fails, it tries to decode it as a probe token using the same secret. The output is a RunToken, a ProbeToken, or `None`.

**Call relations**: EgressControl._authorize, EgressControl._resolve, and EgressControl._forward call this before making decisions tied to a sandbox session. It is the shared identity parser for the egress-control API.

*Call graph*: called by 3 (_authorize, _forward, _resolve); 1 external calls (__init__).


### Rule synthesis
Builds concrete egress proxy rules from models, credentials, grants, connectors, and artifact storage settings.

### `core/src/ufo/egress_rules.py`

`domain_logic` · `per-turn rule derivation before sandbox network access`

A sandbox should not be able to freely call any website or see raw secrets. This file builds the rule list that the egress proxy reads before allowing outbound traffic. Think of it like writing a temporary visitor badge: it says which doors may be opened, which requests should be counted, and which fake placeholder values should be swapped for real credentials outside the sandbox.

The rules are deliberately derived from existing facts, not registered by extensions as a separate API. If a model is used, the model provider host is allowed and the model key is injected. If an extension asks for sandbox internet, a broad internet rule is added. If artifacts are stored in S3, only the needed S3 host is allowed so file sharing still works without opening the whole internet. If a credential slot has a stored secret, this file allows that credential’s host and arranges safe header injection. If a connector grant exists, it allows and meters the connector’s host, but does not inject a token because the broker runs that work server-side.

A key safety detail is that credential failures are isolated. If one credential slot cannot be resolved, the file logs a warning and skips that slot instead of breaking all egress for the turn.

#### Function details

##### `provider_host`  (lines 112–116)

```
def provider_host(model: str) -> str
```

**Purpose**: Finds which API host should be used for a model name. For example, a model name beginning with an OpenAI-style prefix maps to OpenAI’s API host.

**Data flow**: It receives a model name as text, checks it against known model-name prefixes, and returns the matching provider host. If no prefix matches, it raises an error because the system does not know where that model should be reached.

**Call relations**: This is used by derive_model_rules when building the network rules for the selected model. It supplies the host that later becomes allowed, metered, and paired with the right authentication header.

*Call graph*: called by 1 (derive_model_rules).


##### `derive_model_rules`  (lines 119–134)

```
def derive_model_rules(model: str, real_key: str) -> tuple[Rule, ...]
```

**Purpose**: Builds the basic egress rules needed for the sandbox to call its model provider safely. It allows the provider host, swaps the sandbox’s placeholder key for the real key at the proxy, and marks model traffic for token metering.

**Data flow**: It takes a model name and the real model API key. It looks up the provider host, chooses the provider’s expected auth header shape, creates a host allow rule, creates a secret-injection rule from the sentinel value to the real key, and creates a metering rule. It returns those rules as a tuple.

**Call relations**: This function calls provider_host first to identify the destination. It then creates ScopeRule, InjectionRule, and MeterRule objects, which the egress proxy later reads to decide whether and how model requests may leave the sandbox.

*Call graph*: calls 1 internal fn (provider_host); 3 external calls (__init__, __init__, __init__).


##### `derive_manifest_rules`  (lines 137–139)

```
def derive_manifest_rules(manifests: tuple[Manifest, ...]) -> tuple[InternetRule, ...]
```

**Purpose**: Adds public internet access when any deployed extension says the sandbox needs it. Without this, extensions that genuinely require live internet access would be blocked.

**Data flow**: It receives the extension manifests and checks whether any manifest has sandbox internet enabled. If yes, it returns one InternetRule; if not, it returns an empty tuple.

**Call relations**: This function is one piece of the larger rule-building process. It creates InternetRule only when the manifests justify it, leaving more specific rules from other functions to cover model, credential, grant, and storage traffic.

*Call graph*: 1 external calls (__init__).


##### `derive_artifact_store_rules`  (lines 142–158)

```
async def derive_artifact_store_rules(blob: FilesystemBlobStore | S3BlobStore) -> tuple[Rule, ...]
```

**Purpose**: Allows sandbox file sharing to work when artifacts are stored in S3. It admits only the exact S3 host needed, rather than granting broad internet access.

**Data flow**: It receives the configured blob store. If the store is S3, it asks the store for the host used for uploads, then returns a host allow rule and a request-metering rule for that host. If the store is filesystem-based or anything else without a network host, it returns no rules.

**Call relations**: This runs during rule derivation for artifact sharing. It creates ScopeRule and MeterRule so the proxy can tunnel presigned S3 upload requests without exposing any extra hosts.

*Call graph*: 3 external calls (__init__, __init__, put_host).


##### `derive_credential_rules`  (lines 161–224)

```
async def derive_credential_rules(slots: tuple[CredentialSlot, ...], workspace_id: UUID, store: CredentialStore) -> tuple[Rule, ...]
```

**Purpose**: Turns declared credential slots into safe network rules. When a workspace has a stored secret for a slot, this function allows the chosen host and tells the proxy how to replace the sandbox’s harmless placeholder with the real secret.

**Data flow**: It receives credential slot declarations, the workspace id, and the credential store. For each slot that supports injection, it tries to read the real secret and resolve the host. If either is missing or fails, it logs a warning and skips only that slot. For working slots, it creates injection rules, groups them by host, adds one host allow rule per host, and adds metering rules when the slot declares a spending dimension. For Git basic authentication, it combines the username and secret into a Basic auth header value.

**Call relations**: This function is called as part of building a turn’s egress policy. It depends on slot_secret and credential_host to resolve workspace-specific credential data, uses warn to record skipped slots, and produces ScopeRule, InjectionRule, and MeterRule objects that the proxy later enforces.

*Call graph*: 7 external calls (__init__, __init__, __init__, b64encode, credential_host, slot_secret, warn).


##### `derive_grant_rules`  (lines 227–244)

```
def derive_grant_rules(grants: tuple[Grant, ...], transfer_hosts: 'ConnectorTransferHosts | None'=None) -> tuple[Rule, ...]
```

**Purpose**: Allows network access for active connector grants and meters those requests. A grant opens the connector’s host and any needed file-transfer hosts, but it does not inject secrets because the broker owns and uses the account token server-side.

**Data flow**: It receives the active grants and, optionally, connector transfer-host lookup data. For each grant, it combines the grant’s provider host with any transfer hosts, removes duplicates and empty hosts, then creates one allow rule covering those hosts and one request-metering rule per host. It returns all of those rules.

**Call relations**: This function may ask ConnectorTransferHosts.of for extra file-store hosts tied to a connector provider. It then creates ScopeRule and MeterRule objects, which let the sandbox reach granted connector endpoints and broker file-transfer URLs while keeping those requests counted.

*Call graph*: 2 external calls (__init__, __init__).


##### `derive_cli_rules`  (lines 247–267)

```
def derive_cli_rules(grants: tuple[Grant, ...], acting_member_id: UUID | None, clis: Mapping[str, CliCredential]) -> tuple[Rule, ...]
```

**Purpose**: Creates forwarding rules for connector CLI credentials. These rules let eligible sandbox requests be executed through the broker, where the real credential lives, instead of exposing that credential to the sandbox or local deploy.

**Data flow**: It receives grants, the acting member id, and the connector CLI credential declarations. For each grant with a matching CLI credential, it checks whether the acting member is allowed to use it: either the grant is shared with the workspace or it belongs to that member. If allowed, it creates a ForwardRule containing the host, header, sentinel value, account id, and forwarding behavior.

**Call relations**: This function uses grant_sentinel to build the placeholder value that identifies a granted account on the wire. It returns ForwardRule objects that tell the proxy to hand matching requests to the broker rather than sending them directly upstream.

*Call graph*: 2 external calls (__init__, grant_sentinel).


##### `ConnectorTransferHosts.of`  (lines 281–282)

```
def of(self, provider: str) -> tuple[str, ...]
```

**Purpose**: Looks up which broker file-store hosts should be allowed for a connector provider. If the provider was explicitly registered, its own declared hosts are used; otherwise the default open-namespace hosts are used.

**Data flow**: It receives a provider name. It checks the explicit provider-to-host mapping, and if the provider is present there, returns that tuple of hosts. If not present, it returns the default host tuple.

**Call relations**: derive_grant_rules calls this when grants may need extra transfer hosts for tool file inputs and outputs. This small lookup keeps the grant-rule code from needing to know how those host lists were assembled.


##### `connector_transfer_hosts`  (lines 285–296)

```
def connector_transfer_hosts(manifests: tuple[Manifest, ...]) -> ConnectorTransferHosts
```

**Purpose**: Builds the transfer-host lookup table used by grant rule derivation. It collects each registered connector’s declared file-transfer hosts and also records default hosts from the open connector namespace, if one exists.

**Data flow**: It receives the manifests. It scans every connector in every manifest and maps each connector provider to its declared transfer hosts. It also asks for the open connector namespace and takes its transfer hosts as the default when available. It returns a ConnectorTransferHosts object containing both pieces.

**Call relations**: This function prepares the data that ConnectorTransferHosts.of later serves to derive_grant_rules. It calls open_connector_namespace to find the fallback namespace and constructs the lookup object used during egress rule building.

*Call graph*: 2 external calls (__init__, open_connector_namespace).

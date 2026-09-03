# Secrets, network egress, and connector access  `stage-21.2` (cross-cutting infrastructure)

This stage is shared safety plumbing for anything that must leave the sandbox and talk to the outside world. A sandbox is the locked-down place where an agent’s code runs. Before it can call an API, download from a host, or use a private connector, this stage decides what is allowed and how secrets are protected.

The egress resolver reads a signed, short-lived token and turns it into fresh network rules for one run. The egress rules builder combines model choices, extensions, connector grants, artifact storage, and saved credentials into exact proxy instructions: which hosts may be reached and when a hidden credential should be inserted into a request. The credentials code stores API keys safely and supports sealed requests so users can provide secrets without exposing them in chat or the sandbox.

The private egress control API is the gate the Rust proxy asks before letting traffic out, fetching secrets, or recording usage. Grant code keeps connected OpenAI or Anthropic accounts refreshed. Pipedream proxy and token support let external accounts be used without exposing their tokens. The direct source connector path does the same for member-supplied API keys.

## Files in this stage

### Egress policy resolution
Transforms signed run or probe context into concrete sandbox network permissions and secret-injection rules.

### `core/src/ufo/runtime/access/egress_resolver.py`

`domain_logic` · `request handling`

This file is the control center for outbound network permissions. When an agent tries to connect to the outside world, the proxy does not guess what is allowed and does not hold all the secrets itself. Instead, it asks this code to resolve the rules for the exact run or probe token making the request.

The important idea is that rules are rebuilt each time from live information. The resolver checks the workspace named by the token, finds the agent behind the turn or conversation, confirms that the turn or probe is still valid, and checks whether the member named by the token still has an active seat. Only then does it add extra permissions such as internet access, tool bridge access, preview access, workspace credential injections, OAuth grant forwarding, and command-line credential support.

An analogy is a security desk issuing a temporary visitor pass. The pass is not copied from yesterday’s list; the desk checks today’s building roster, the visitor’s host, and which rooms are currently approved. If the meeting ended or the host lost access, no pass is issued.

The file also has special care for probes. A probe can use many of the same workspace and agent credentials, but it must not receive the deployment’s own model API key. That removal happens here, at the enforcement point, so it does not depend on what happened to be present in the probe’s environment.

#### Function details

##### `_seat_scope`  (lines 50–67)

```
def _seat_scope(workspace_id: UUID, authority: ExecutionAuthority) -> tuple[sa.ColumnElement[bool], ...]
```

**Purpose**: This helper builds the database condition that proves a member is still allowed to act in a workspace. Workspace-wide authority needs no extra check, while member authority must still point to a seated member.

**Data flow**: It receives a workspace ID and an execution authority. If the authority is workspace-wide, it returns no extra database filter. If it names a member, it returns a condition requiring that member to belong to the workspace and still have a seat. If the authority is not one of the expected kinds, it raises an error instead of silently allowing access.

**Call relations**: The live-check and lookup methods call this helper whenever they query turns or conversations. It gives those queries the shared safety rule: a token tied to a removed member should stop working immediately.

*Call graph*: called by 4 (_conversation_of, _turn_of, probe_live, turn_live); 2 external calls (exists, select).


##### `PerAgentRules.resolve`  (lines 110–166)

```
async def resolve(self, principal: EgressPrincipal | None) -> tuple[Rule, ...]
```

**Purpose**: This is the main rule-building function. Given a run token, probe token, or no token, it returns the network rules the proxy should enforce for that principal.

**Data flow**: It starts with a principal token. With no token, it returns only the base rules. With a run or probe token, it enters that token’s workspace, looks up the live agent and authority, and stops with no rules if the token no longer represents live work. For live work, it builds a rule list from the base rules, optional internet rules, service routes, preview token injection, stored credentials, OAuth grants, and CLI credential rules. For probes, it removes the model-key injection before returning the final tuple.

**Call relations**: The proxy-facing flow depends on this method to translate identity into concrete allow and injection rules. It delegates run lookup to `PerAgentRules._turn_of`, probe lookup to `PerAgentRules._conversation_of`, and uses the rule-derivation helpers for credentials and grants so that each kind of permission is added in the same way across the system.

*Call graph*: calls 3 internal fn (_conversation_of, _turn_of, _without_the_model_key); 7 external calls (__init__, __init__, derive_cli_rules, derive_credential_rules, derive_grant_rules, agent, ws).


##### `PerAgentRules.git_credential`  (lines 168–215)

```
async def git_credential(self, principal: EgressPrincipal, host: str) -> tuple[GitWire, str, str] | None
```

**Purpose**: This function chooses a Git credential for a cache daemon fetch on behalf of a run or probe. It tries to fetch using the same connected account the sandbox would expose, and returns nothing if there is no safe single account to use.

**Data flow**: It receives a principal token and a Git host name. It checks that the principal is live, loads the agent’s active grants, then searches configured CLI connectors for one whose Git host matches. If exactly one usable account fits the authority, it asks the connector’s secret broker for that account token. On success it returns the Git wiring information, token, and account ID; on failure or ambiguity it returns `None`, meaning the daemon should fetch anonymously.

**Call relations**: This is used by the cache side of network access rather than by the normal rule list. It follows the same authority lookup path as `PerAgentRules.resolve`, and it uses `usable_cli_accounts` so the cache fetch mirrors the identity that command-line tools inside the sandbox would use.

*Call graph*: calls 2 internal fn (_conversation_of, _turn_of); 5 external calls (warn, usable_cli_accounts, agent, authority_member_id, ws).


##### `PerAgentRules._turn_of`  (lines 217–252)

```
async def _turn_of(self, run: RunToken) -> _Authority | None
```

**Purpose**: This private helper confirms that a run token names a currently running turn and finds the agent and internet policy for that turn. It is the run-token liveness check used before granting network rules.

**Data flow**: It receives a run token. Inside a workspace database transaction, it looks for a matching turn in the same workspace, joined to its agent, with status `RUNNING`, plus any required member-seat condition. If no row matches, it returns `None`. If a row matches, it reads any turn runtime configuration, calculates whether internet access is currently allowed, and returns an `_Authority` object containing the agent ID, internet flag, and execution authority.

**Call relations**: `PerAgentRules.resolve` and `PerAgentRules.git_credential` call this when the presented principal is a run token. It uses `_seat_scope` so a member losing their seat also makes the run token stop resolving to authority.

*Call graph*: calls 1 internal fn (_seat_scope); called by 2 (git_credential, resolve); 4 external calls (__init__, model_validate, select, workspace_tx).


##### `PerAgentRules._conversation_of`  (lines 254–286)

```
async def _conversation_of(self, probe: ProbeToken) -> _Authority | None
```

**Purpose**: This private helper confirms that a probe token is still valid and finds the agent and internet policy for the conversation it belongs to. Probes do not name a turn, so this lookup goes through the conversation instead.

**Data flow**: It receives a probe token. First it checks the token expiration time against the current time. If expired, it returns `None`. Otherwise it queries the workspace database for the named conversation and its agent, again applying the member-seat condition when needed. If found, it returns an `_Authority` object with the conversation’s agent, the agent’s internet setting, and the token’s execution authority.

**Call relations**: `PerAgentRules.resolve` and `PerAgentRules.git_credential` call this when the principal is a probe token. It parallels `PerAgentRules._turn_of`, but adapts the lookup to probes, which are attached to conversations rather than running turns.

*Call graph*: calls 1 internal fn (_seat_scope); called by 2 (git_credential, resolve); 4 external calls (__init__, now, select, workspace_tx).


##### `PerAgentRules._without_the_model_key`  (lines 288–299)

```
def _without_the_model_key(self, rules: tuple[Rule, ...]) -> tuple[Rule, ...]
```

**Purpose**: This helper removes the deployment’s own model-key injection from a set of rules. It lets probes keep normal workspace and grant-based access without receiving the system’s model API credential.

**Data flow**: It receives a tuple of rules. It filters out any injection rule whose sentinel marker is the model-key sentinel, and keeps every other rule unchanged. The returned tuple is therefore the same policy minus that one sensitive model-key path.

**Call relations**: `PerAgentRules.resolve` calls this only for probe tokens, after building the full rule set. That placement matters because probes should otherwise follow the same derivation path as turns, with only the model credential withheld at the end.

*Call graph*: called by 1 (resolve).


##### `PerAgentRules.turn_live`  (lines 301–333)

```
async def turn_live(self, run: RunToken) -> int | None
```

**Purpose**: This function answers whether a run token still names a running turn that may use egress rules. It also returns the workspace’s current egress-rule generation number, which helps callers know whether cached rules are still current.

**Data flow**: It receives a run token, enters the token’s workspace, and queries the turn joined to its workspace. The query requires the matching turn and workspace, plus any live member-seat condition. If the row is missing or the turn is not running, it returns `None`. If the turn is live, it returns the workspace’s egress rules generation number.

**Call relations**: This is the quick authorization gate for CONNECT-style checks. It shares `_seat_scope` with the deeper rule-resolution lookups, so the same live-member rule applies whether the caller is checking liveness or building the full rule set.

*Call graph*: calls 1 internal fn (_seat_scope); 3 external calls (select, workspace_tx, ws).


##### `PerAgentRules.probe_live`  (lines 335–357)

```
async def probe_live(self, probe: ProbeToken) -> int | None
```

**Purpose**: This function answers whether a probe token is still valid for egress checks. If it is valid, it returns the workspace’s current egress-rule generation number.

**Data flow**: It receives a probe token. It first rejects expired probes. For unexpired probes, it enters the workspace and queries for the named conversation joined to its workspace, applying any required member-seat condition. If a matching conversation exists, it returns the current egress rules generation; otherwise it returns `None`.

**Call relations**: This is the probe counterpart to `PerAgentRules.turn_live`. It gives the proxy or cache layer a lightweight way to confirm a probe can still use its rules without rebuilding the entire rule list.

*Call graph*: calls 1 internal fn (_seat_scope); 4 external calls (now, select, workspace_tx, ws).


### `core/src/ufo/runtime/access/egress_rules.py`

`domain_logic` · `turn setup and request policy derivation`

A sandbox should not be able to call any website or see raw secrets by default. This file builds the rulebook for the egress proxy, which is the gatekeeper for outbound network traffic. Think of it like a security desk: it decides which doors are open, which visits are counted, and when a sealed envelope should be handed to an approved destination without exposing its contents inside the building.

The rules are small value objects. A ScopeRule says exactly which hosts are allowed. An InternetRule allows broader public internet access for live turns that requested it. An InjectionRule tells the proxy to replace a harmless placeholder value, called a sentinel, with the real secret only as the request leaves the sandbox. A MeterRule says requests to a host should be counted under a spending or usage dimension. A ServiceRule describes special local service hosts served by daemons.

The functions in this file derive those rules from different sources: the chosen AI model, extension manifests, S3 artifact storage, workspace credentials, connector grants, and command-line connector credentials. The important safety pattern is that failures are usually isolated. If one credential slot or connector token cannot be read, this file logs a warning and skips only that piece instead of opening access too broadly or breaking unrelated access.

#### Function details

##### `provider_host`  (lines 95–99)

```
def provider_host(model: str) -> str
```

**Purpose**: This function maps a model name, such as an OpenAI or Anthropic model, to the network host that serves it. It is used so later rules can allow only the correct provider host instead of granting broad internet access.

**Data flow**: It receives a model name as text. It checks known model-name prefixes against a table of provider hosts. If it finds a match, it returns the host name; if no prefix matches, it raises an error because the system does not know where that model should connect.

**Call relations**: When model access rules are being built, derive_model_rules asks this function which provider host belongs to the selected model. The answer becomes the host used for allowlisting, credential injection, and metering.

*Call graph*: called by 1 (derive_model_rules).


##### `derive_model_rules`  (lines 102–117)

```
def derive_model_rules(model: str, real_key: str) -> tuple[Rule, ...]
```

**Purpose**: This function builds the network rules needed for the run to talk to its chosen AI model provider. It also ensures the model API key is injected at the proxy, so the sandbox sees only a placeholder rather than the real key.

**Data flow**: It receives a model name and the real API key. It first finds the provider host, then chooses the correct authorization header style for that provider. It returns rules that allow that host, replace the sentinel model key with the real key on outgoing requests, and meter usage under tokens.

**Call relations**: This is the model-specific rule builder. It relies on provider_host to identify the destination, then creates the scope, injection, and metering rules that the egress proxy later reads while forwarding model traffic.

*Call graph*: calls 1 internal fn (provider_host); 3 external calls (__init__, __init__, __init__).


##### `derive_manifest_rules`  (lines 120–122)

```
def derive_manifest_rules(manifests: tuple[Manifest, ...]) -> tuple[InternetRule, ...]
```

**Purpose**: This function checks whether any loaded extension says the sandbox needs public internet access. If so, it adds the rule that permits broader internet use during live turns.

**Data flow**: It receives the extension manifests. It scans them for a sandbox_internet flag. If any manifest asks for internet access, it returns an InternetRule; otherwise it returns no rules.

**Call relations**: This function contributes the broad internet permission part of the overall rule set. Other rule builders add exact hosts, but this one is the bridge from extension configuration to general public internet access.

*Call graph*: 1 external calls (__init__).


##### `derive_artifact_store_rules`  (lines 125–141)

```
async def derive_artifact_store_rules(blob: FilesystemBlobStore | S3BlobStore) -> tuple[Rule, ...]
```

**Purpose**: This function allows the sandbox to upload shared files when the artifact store is backed by S3. Without these rules, a file produced in the sandbox could be blocked when it tries to leave through a presigned S3 URL.

**Data flow**: It receives the blob store used for artifacts. If the store is S3, it asks the store for the upload host and returns rules that allow that exact host and count requests to it. If the store is local filesystem storage, it returns no network rules because no network upload is needed.

**Call relations**: This function is called as part of building the full egress rulebook for a run. It hands the proxy a narrow permission for artifact sharing, separate from full public internet access, so even restricted agents can still share files safely.

*Call graph*: 3 external calls (__init__, __init__, put_host).


##### `derive_credential_rules`  (lines 144–199)

```
async def derive_credential_rules(slots: tuple[CredentialSlot, ...], workspace_id: UUID, store: CredentialStore) -> tuple[Rule, ...]
```

**Purpose**: This function turns saved workspace credentials into proxy rules for approved hosts. It lets requests carry real secrets only after they leave the sandbox, replacing sentinel values with stored secrets at the proxy.

**Data flow**: It receives credential slot declarations, a workspace ID, and the credential store. For each slot that declares an injection target, it tries to read the saved secret and resolve the host. If either is missing or fails, it skips that slot and may log a warning. For successful slots, it groups injections by host, then returns rules that allow each host, inject the needed headers, and optionally meter requests to that host.

**Call relations**: This is the workspace-credential part of the rule derivation flow. It reads from CredentialStore and credential_host, creates injection, scope, and metering rules, and uses warn when one slot cannot be resolved so the rest of the run can continue safely.

*Call graph*: calls 1 internal fn (get); 5 external calls (__init__, __init__, __init__, warn, credential_host).


##### `derive_grant_rules`  (lines 202–220)

```
def derive_grant_rules(grants: tuple[Grant, ...], transfer_hosts: 'ConnectorTransferHosts | None'=None) -> tuple[Rule, ...]
```

**Purpose**: This function builds access rules from active connector grants. A grant means the sandbox may reach that connector provider’s host, and possibly extra file-transfer hosts used by the broker.

**Data flow**: It receives grants and, optionally, a ConnectorTransferHosts lookup. For each grant, it combines the grant’s own host with any transfer hosts for that provider, removes blanks and duplicates, then returns a scope rule for those hosts plus request metering rules for each one.

**Call relations**: This function supplies the host access created by connector grants. When transfer host information is available, it asks ConnectorTransferHosts.of which additional file-store hosts belong to the provider, then emits the rules the proxy will enforce.

*Call graph*: 2 external calls (__init__, __init__).


##### `derive_cli_rules`  (lines 223–270)

```
async def derive_cli_rules(grants: tuple[Grant, ...], authority: ExecutionAuthority, clis: Mapping[str, CliCredential], workspace_id: UUID) -> tuple[Rule, ...]
```

**Purpose**: This function creates secret-injection rules for connector command-line credentials. It is used when a granted connector needs a real account token inserted into outgoing provider or git requests, without exposing that token inside the sandbox.

**Data flow**: It receives grants, the execution authority, known CLI credential declarations, and a workspace ID. It determines which member, if any, the authority represents. For each grant whose connector has a CLI credential and whose account may be used, it asks the credential broker for the real token. If that succeeds, it creates an injection rule for the provider host. If the connector also has a git host, it groups git injection rules by host, then adds allowlist and metering rules for those git hosts. If token lookup fails for one grant, it logs a warning and skips only that grant.

**Call relations**: This function complements derive_grant_rules. Grant rules allow provider or transfer hosts, while this function supplies the actual token substitution for CLI-style connector access and adds git-host access when needed. It uses authority_member_id to enforce who may use which account and grant_sentinel to know which placeholder should be replaced.

*Call graph*: 6 external calls (__init__, __init__, __init__, warn, grant_sentinel, authority_member_id).


##### `ConnectorTransferHosts.of`  (lines 284–285)

```
def of(self, provider: str) -> tuple[str, ...]
```

**Purpose**: This method answers which broker file-transfer hosts should be allowed for a connector provider. It keeps connector-specific declarations separate from the default open-namespace hosts.

**Data flow**: It receives a provider name. It first checks the explicit provider-to-host mapping. If that provider was registered there, it returns that provider’s declared hosts, even if the list is empty. If the provider is not explicitly registered, it returns the default host tuple.

**Call relations**: derive_grant_rules calls this method while turning grants into proxy rules. The returned hosts are added beside the grant’s own provider host so the sandbox can fetch or stage connector files through the broker when appropriate.


##### `connector_transfer_hosts`  (lines 288–299)

```
def connector_transfer_hosts(manifests: tuple[Manifest, ...]) -> ConnectorTransferHosts
```

**Purpose**: This function builds the lookup table used to decide which file-transfer hosts belong to connector grants. It reads the deploy’s manifests so the egress rules follow the connector declarations that are actually present.

**Data flow**: It receives all manifests. It collects each registered connector’s provider name and declared transfer hosts into an explicit mapping. It also asks for the open connector namespace, if one exists, and uses that namespace’s transfer hosts as the default for providers not explicitly registered. It returns a ConnectorTransferHosts object containing both pieces.

**Call relations**: This function prepares data for derive_grant_rules. It calls open_connector_namespace to find the default namespace, then packages explicit and default hosts into ConnectorTransferHosts so grant-rule derivation can do a simple provider lookup later.

*Call graph*: 2 external calls (__init__, open_connector_namespace).


### Credential and grant storage
Models renewable connector grants and protects user-provided secrets until they are needed by the proxy or connector runtime.

### `core/src/ufo/harness/models/grant.py`

`data_model` · `credential loading and request handling`

This file solves the problem of connected provider accounts that expire. An OAuth grant is like a rechargeable transit card: the access token is today’s ride, while the refresh token is what lets the system load a new ride later. If the system stored only the access token, an account could look connected even after it stopped working. So this file stores the full grant: the current access token, the refresh token, and the time the access token expires.

It also records which OAuth client identity should be used when talking to OpenAI or Anthropic. A deployment can provide its own client ID through environment variables, or fall back to public default client IDs.

The `Grant` model is the stored shape of a connected account. It can say whether the token is effectively spent, using a safety margin before the real expiry time so a long-running operation does not fail halfway through. It can also say whether another caller has already claimed the refresh token. That matters because refresh tokens may be single-use, so two callers trying to refresh at once could break the account.

The helper functions turn provider responses into `Grant` objects, read stored strings back into grants, and refresh a grant by calling the provider’s token endpoint. If refresh fails, the file raises `GrantRefusedRefresh`, which means the member must reconnect the account.

#### Function details

##### `openai_client_id`  (lines 36–39)

```
def openai_client_id() -> str
```

**Purpose**: This chooses the client ID the deployment should present when refreshing an OpenAI connected account. It lets a deployment use its own OpenAI OAuth client, while still having a public fallback.

**Data flow**: It reads the `UFO_OPENAI_OAUTH_CLIENT_ID` environment variable. If that value exists, it returns it; otherwise it returns the built-in public OpenAI client ID.

**Call relations**: When `refreshed` refreshes an OpenAI grant, it uses the client lookup stored in `GRANT_CLIENTS`. That lookup calls `openai_client_id` at refresh time so the refresh request uses the same kind of client identity expected by the original sign-in flow.


##### `anthropic_client_id`  (lines 42–45)

```
def anthropic_client_id() -> str
```

**Purpose**: This chooses the client ID the deployment should present when refreshing an Anthropic connected account. It supports either a deployment-specific Anthropic OAuth client or a public default client.

**Data flow**: It reads the `UFO_ANTHROPIC_OAUTH_CLIENT_ID` environment variable. If that value is set, it returns it; otherwise it returns the built-in public Anthropic client ID.

**Call relations**: When `refreshed` refreshes an Anthropic grant, it uses the client lookup stored in `GRANT_CLIENTS`. That lookup calls `anthropic_client_id` during the refresh so environment changes are respected when the token request is made.


##### `GrantRefusedRefresh.__init__`  (lines 71–73)

```
def __init__(self, slot: str) -> None
```

**Purpose**: This creates a clear error for the case where a provider will not exchange a refresh token for a new grant. It signals that the connected account cannot be repaired automatically and the member needs to connect again.

**Data flow**: It receives the slot name, such as the place where an OpenAI or Anthropic credential is stored. It builds a human-readable error message and saves the slot on the exception so later code can tell which connected account failed.

**Call relations**: `refreshed` uses this when the provider cannot be reached, returns a bad status, sends unreadable data, or omits required token fields. `WorkspaceScope._refreshed_credential` also uses the same exception to report refresh refusal in the wider workspace credential flow.

*Call graph*: called by 2 (refreshed, _refreshed_credential).


##### `Grant.spent`  (lines 88–89)

```
def spent(self) -> bool
```

**Purpose**: This tells whether a grant should be treated as no longer safe to use. It marks a token as spent before its exact expiry time so work does not start with a token that may die mid-operation.

**Data flow**: It reads the current clock time and compares it with the grant’s `expires_at` time minus a refresh safety margin. It returns `true` when the token is inside that danger window, and `false` when it still has enough time left.

**Call relations**: Other credential code can ask this property before using an access token. If it is spent, the larger flow knows it should refresh the grant rather than handing the old token to provider calls.

*Call graph*: 1 external calls (time).


##### `Grant.claimed`  (lines 92–93)

```
def claimed(self) -> bool
```

**Purpose**: This tells whether another caller is currently considered to have claimed the right to refresh this grant. It helps avoid two tasks spending the same one-time refresh token at the same time.

**Data flow**: It reads the current clock time and compares it with `refreshing_until`. It returns `true` if the claim period is still active, and `false` once that period has passed.

**Call relations**: The wider credential refresh flow can use this property before attempting a refresh. If the grant is claimed, another caller is expected to be doing the refresh already, so this check helps prevent a race.

*Call graph*: 1 external calls (time).


##### `Grant.stored`  (lines 95–96)

```
def stored(self) -> str
```

**Purpose**: This turns a `Grant` object into the string form that can be saved in a credential slot. It keeps all needed fields together, including both tokens and the expiry time.

**Data flow**: It reads the fields on the `Grant` object and serializes them as JSON text. The result is a string suitable for storage, and the grant object itself is not changed.

**Call relations**: After a grant is created or refreshed, storage code can call `stored` to save the full renewable account state. Later, `read_grant` can reverse this process and rebuild a `Grant` from the stored string.


##### `granted`  (lines 99–112)

```
def granted(payload: dict[str, object]) -> Grant | None
```

**Purpose**: This checks a provider token response and turns it into a `Grant` only if it contains everything needed for a renewable connected account. It refuses incomplete responses, because an access token without a refresh token would work briefly and then strand the user.

**Data flow**: It receives a dictionary from a token endpoint response. It looks for a non-empty access token, a non-empty refresh token, and a numeric expiry duration. If all are valid, it creates a `Grant` whose expiry time is the current time plus that duration; otherwise it returns `None`.

**Call relations**: `refreshed` calls `granted` after a provider answers a refresh request. `granted` is the gatekeeper that decides whether the provider’s response is complete enough to replace the stored grant.

*Call graph*: called by 1 (refreshed); 2 external calls (__init__, time).


##### `read_grant`  (lines 115–127)

```
def read_grant(stored: str) -> Grant | None
```

**Purpose**: This tries to interpret a stored credential string as a renewable grant. If the string is just a plain API key or invalid data, it returns `None` instead of treating it as an expiring connected account.

**Data flow**: It receives a stored string. It tries to parse it as JSON, checks that the result is an object, and validates that object as a `Grant`. If any step fails, it returns `None`; if all steps succeed, it returns the reconstructed `Grant`.

**Call relations**: Credential-loading code can use `read_grant` when it finds a value in a credential slot. A returned `Grant` means the slot contains a connected account that may need refresh; `None` means the value should be treated as something else, such as a plain API key.

*Call graph*: 1 external calls (loads).


##### `refreshed`  (lines 130–155)

```
async def refreshed(grant: Grant, slot: str) -> Grant
```

**Purpose**: This exchanges an old grant’s refresh token for a new grant from OpenAI or Anthropic. It is the main piece that keeps a member’s connected account working after the current access token grows stale.

**Data flow**: It receives the current `Grant` and the slot name that identifies which provider account is being refreshed. It looks up the provider token endpoint and client ID function, sends an HTTP POST request containing the refresh token and client ID, checks that the provider answered successfully, parses the JSON response, and turns that response into a new `Grant`. If the network call, status code, JSON parsing, or token contents fail, it raises `GrantRefusedRefresh`.

**Call relations**: Higher-level workspace credential code calls `refreshed` when a stored grant is spent and needs replacement. Inside, it uses `httpx.AsyncClient` to talk to the provider, calls `granted` to validate the provider’s answer, and raises `GrantRefusedRefresh` whenever the account cannot be refreshed safely.

*Call graph*: calls 2 internal fn (__init__, granted); 1 external calls (AsyncClient).


### `core/src/ufo/runtime/access/credentials.py`

`domain_logic` · `cross-cutting`

This file is the project’s safe deposit box for “bring your own key” credentials. A workspace may need an external API key, but the key must not appear in logs, chat history, or inside the sandbox where code runs. Without this file, secrets would either be unavailable to tools or would have to travel through unsafe places.

The main idea is simple: store only encrypted values in the database, and decrypt them only inside trusted code when they are needed. Fernet is the encryption tool used here; it both hides the text and detects tampering. The file also defines “sealed” credential requests. A sealed request is like a tamper-proof envelope: it says which workspace, member, and credential slots are allowed, and it expires after a short time.

`CredentialRequests` creates and checks those envelopes. `CredentialStore` writes, reads, clears, updates, and safely rotates encrypted secrets in the database. It also makes sure a one-time credential request cannot be fulfilled twice and that only a seated workspace admin can fulfill one. `HostChoice` covers providers whose API host must be chosen from a fixed list, avoiding free-text hostnames that could make the proxy connect somewhere unsafe. Together, these pieces let extensions ask for secrets, store them safely, and use them only through controlled proxy rules.

#### Function details

##### `deploy_env`  (lines 33–39)

```
def deploy_env(name: str) -> str | None
```

**Purpose**: Reads a deployment-level secret from environment variables. It first looks for a UFO-specific name, then falls back to the ordinary name, so this project can have its own private setting without breaking existing deployments.

**Data flow**: It receives a variable name, checks `UFO_<name>` in the process environment, then checks `<name>`, and treats an empty value as missing. It returns the found string or `None` if neither form is set.

**Call relations**: This is a small helper for configuration-time code that needs secrets from the host environment. It does not call other project functions and gives callers a single safe lookup rule to follow.


##### `credential_object_name`  (lines 42–45)

```
def credential_object_name(slot: str) -> str
```

**Purpose**: Turns a credential slot name into a clean object name that can be shown or addressed consistently elsewhere. It lowercases the name and replaces punctuation or spaces with dashes.

**Data flow**: It receives a slot name, normalizes it into a lowercase dash-separated label, trims extra dashes from the ends, and returns that label. For example, a messy slot name becomes a simple slug-like name.

**Call relations**: The `named_slots` function calls this when building the public names for declared credential slots. This keeps different parts of the system using the same name for the same slot.

*Call graph*: called by 1 (named_slots); 1 external calls (sub).


##### `member_slot`  (lines 51–55)

```
def member_slot(slot: str, member_id: UUID) -> str
```

**Purpose**: Builds the storage key for a credential value that belongs to one specific member rather than the whole workspace. This lets the same encrypted credential table hold both shared workspace secrets and per-person secrets.

**Data flow**: It receives a base slot name and a member UUID, joins them with a special `:member:` marker, and returns the combined slot string. It does not read or write storage itself.

**Call relations**: Other credential code can use this helper whenever it needs a predictable per-member slot name. It is a naming convention helper, not a database operation.


##### `named_slots`  (lines 58–73)

```
def named_slots(slots: 'tuple[DeclaredSlot, ...]') -> 'dict[str, DeclaredSlot]'
```

**Purpose**: Creates the stable names used to refer to declared credential slots. If two slots would produce the same simple name, it adds a short hash-based suffix so they do not collide.

**Data flow**: It receives a tuple of declared credential slots, groups them by their cleaned object name, and returns a dictionary from final object name to the matching slot declaration. When there is a collision, it uses the slot’s extension and name to make a stable digest suffix.

**Call relations**: It calls `credential_object_name` for the human-friendly base name and uses a SHA-256 hash when a collision needs a tie-breaker. This lets portal rows, reads, and delete intents all refer to the same credential object consistently.

*Call graph*: calls 1 internal fn (credential_object_name); 1 external calls (sha256).


##### `seal_credential_request`  (lines 102–103)

```
def seal_credential_request(fernet: Fernet, state: CredentialRequestState) -> str
```

**Purpose**: Turns a credential request state into an encrypted, tamper-detecting string. Callers use it when they need to hand a private credential prompt to a user-facing surface safely.

**Data flow**: It receives a Fernet encryption object and a `CredentialRequestState`. It serializes the state to JSON, encrypts it, and returns the encrypted text as a string.

**Call relations**: `CredentialRequests.seal` and `CredentialRequests.authorize` call this after they have built the exact request state. It is the shared sealing step for both ordinary credential prompts and provider authorization prompts.

*Call graph*: called by 2 (authorize, seal); 2 external calls (model_dump_json, encrypt).


##### `open_credential_request`  (lines 106–120)

```
def open_credential_request(fernet: Fernet, sealed: str, *, ttl: int=CREDENTIAL_REQUEST_TTL_SECONDS) -> CredentialRequestState
```

**Purpose**: Opens and verifies a sealed credential request. It gives callers either a trusted request state or a clear `CredentialRequestInvalid` error, so they do not have to separately handle bad encryption, expiry, or malformed data.

**Data flow**: It receives a Fernet encryption object, a sealed string, and an expiry time. It decrypts the string, checks the age through Fernet, parses the JSON into a request state, and returns that state. If anything is expired, tampered with, or not the expected shape, it raises `CredentialRequestInvalid`.

**Call relations**: `CredentialRequests.open_authorization` calls this before checking that the opened request belongs to the right workspace, member, and slot. This function is the first gate: it proves the envelope was minted by this deployment and is still valid.

*Call graph*: called by 1 (open_authorization); 2 external calls (__init__, decrypt).


##### `CredentialRequests.seal`  (lines 132–145)

```
def seal(self, workspace_id: UUID, member_id: UUID, slots: tuple[str, ...]) -> str
```

**Purpose**: Creates a sealed one-time prompt for a member to fill one or more credential slots. It refuses slots that no installed extension declared, which prevents prompts for unknown secrets.

**Data flow**: It receives a workspace ID, member ID, and slot names. It checks every slot against the declared slot set, adds a fresh request ID and issue time, seals the state, and returns the encrypted request string.

**Call relations**: It calls `seal_credential_request` after constructing the state. Later, fulfillment code can use the sealed request information to prove that a private credential submission matches the original prompt.

*Call graph*: calls 1 internal fn (seal_credential_request); 3 external calls (__init__, time, uuid4).


##### `CredentialRequests.authorize`  (lines 147–160)

```
def authorize(self, workspace_id: UUID, member_id: UUID, slot: str, payload: str) -> str
```

**Purpose**: Creates a sealed authorization handoff for a provider flow, such as an OAuth-style flow that carries private provider state. It binds that state to one workspace, one member, and one credential slot.

**Data flow**: It receives a workspace ID, member ID, slot name, and provider payload. It verifies the slot is declared and the payload is not empty, stores them in a request state, encrypts that state, and returns the sealed string.

**Call relations**: It uses `seal_credential_request` to make the encrypted handoff. `CredentialRequests.open_authorization` later opens and checks this seal before returning the provider payload.

*Call graph*: calls 1 internal fn (seal_credential_request); 1 external calls (__init__).


##### `CredentialRequests.open_authorization`  (lines 162–176)

```
def open_authorization(self, sealed: str, workspace_id: UUID, member_id: UUID, slot: str) -> str
```

**Purpose**: Verifies a sealed provider authorization handoff and returns its private payload. It makes sure the seal is for exactly the expected workspace, member, and credential slot.

**Data flow**: It receives a sealed string plus the workspace, member, and slot expected by the caller. It opens the seal, compares the embedded claims to those expected values, checks the slot is declared, and returns the payload. If the seal belongs to another context or has no payload, it raises an error.

**Call relations**: It calls `open_credential_request` to do the cryptographic opening first. Then it performs the more specific authorization checks before handing the provider state back to the caller.

*Call graph*: calls 1 internal fn (open_credential_request); 1 external calls (__init__).


##### `CredentialStore.put`  (lines 183–205)

```
async def put(self, workspace_id: UUID, slot: str, plaintext: str) -> None
```

**Purpose**: Stores a plaintext credential value for a workspace slot after encrypting it. It is used when trusted code already has a complete secret and needs to save it safely.

**Data flow**: It receives a workspace ID, slot name, and plaintext secret. It rejects an empty secret, encrypts the value, opens a workspace database transaction, and either updates the existing row or inserts a new row. The database ends up holding ciphertext, not the original text.

**Call relations**: It uses the workspace transaction helper and SQL update/insert operations. This is one of the direct write paths into the encrypted credential table.

*Call graph*: 3 external calls (insert, update, workspace_tx).


##### `CredentialStore.fulfill`  (lines 207–294)

```
async def fulfill(self, workspace_id: UUID, slot: str, submitted: str, request_id: UUID | None, member_id: UUID, merge: Callable[[str | None, str], str] | None) -> None
```

**Purpose**: Accepts a private credential submission for a sealed member prompt and writes it to the encrypted store. It also enforces that the member is currently a seated admin and that a request ID can only be fulfilled once.

**Data flow**: It receives the workspace, slot, submitted secret, optional request ID, member ID, and optional merge function. It locks the workspace row, checks the member has admin authority, optionally claims the request so it cannot be reused, reads any current encrypted value, decrypts it if present, merges or replaces it, re-encrypts the result, and writes it back. It raises errors for empty values, unauthorized members, or already-used requests.

**Call relations**: This function is the guarded bridge from a private user prompt into stored credentials. It uses database selects, inserts, and updates inside `workspace_tx`, and it raises `CredentialRequestInvalid` when the sealed-prompt rules are broken.

*Call graph*: 5 external calls (__init__, insert, select, update, workspace_tx).


##### `CredentialStore.clear`  (lines 296–305)

```
async def clear(self, workspace_id: UUID, slot: str) -> None
```

**Purpose**: Deletes the stored credential value for one workspace slot. It is safe to call even if the slot is already empty.

**Data flow**: It receives a workspace ID and slot name, opens a workspace transaction, and deletes matching rows from the credential table. It returns nothing; the after-state is simply that no stored value remains for that slot.

**Call relations**: It uses the workspace transaction helper and a SQL delete. Callers can use it for disconnect or cleanup flows without first checking whether a credential exists.

*Call graph*: 2 external calls (delete, workspace_tx).


##### `CredentialStore.update`  (lines 307–354)

```
async def update(self, workspace_id: UUID, slot: str, submitted: str, merge: Callable[[str | None, str], str]) -> None
```

**Purpose**: Merges a private submitted value into an existing credential slot while holding a workspace write lock. This is useful for structured secrets where a new submission updates part of the stored value rather than replacing everything blindly.

**Data flow**: It receives a workspace ID, slot name, submitted text, and merge function. It rejects empty input, locks the workspace row, reads and decrypts the current value if one exists, asks the merge function to produce the new plaintext, rejects an empty merged result, encrypts it, and inserts or updates the credential row.

**Call relations**: It follows the same encrypted read-modify-write pattern as `fulfill`, but without the sealed request and admin checks. It relies on `workspace_tx` and SQL select/insert/update calls to keep the database change consistent.

*Call graph*: 4 external calls (insert, select, update, workspace_tx).


##### `CredentialStore.get`  (lines 356–368)

```
async def get(self, workspace_id: UUID, slot: str) -> str
```

**Purpose**: Reads and decrypts one credential slot for a workspace. Callers use it only in trusted paths that need the real secret or need to derive behavior from the stored value.

**Data flow**: It receives a workspace ID and slot name, looks up the encrypted row in the credential table, and closes the transaction. If no row exists, it raises `CredentialSlotUnset`; otherwise it decrypts the ciphertext and returns the plaintext string.

**Call relations**: This is called by sandbox environment setup, credential host resolution, and egress rule derivation when they need credential-backed information. It is the main read path out of the encrypted credential store.

*Call graph*: called by 3 (_keyed_provider_env, credential_host, derive_credential_rules); 3 external calls (__init__, select, workspace_tx).


##### `CredentialStore.rotate`  (lines 370–401)

```
async def rotate(self, workspace_id: UUID, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Replaces a stored credential only if it still contains an expected old value. This prevents two concurrent refreshes from accidentally overwriting a newer secret with an older one.

**Data flow**: It receives a workspace ID, slot name, expected current plaintext, and replacement plaintext. It rejects an empty replacement, reads the stored encrypted value, decrypts and compares it to the expected value, and only then updates the row while also checking the database row still has the same ciphertext. It returns `true` if the replacement happened and `false` if the slot was missing or had changed.

**Call relations**: OAuth-style refresh code can call this after receiving a new token from an external provider. It uses `workspace_tx` plus SQL select and update operations to make the compare-and-replace step safe.

*Call graph*: 3 external calls (select, update, workspace_tx).


##### `HostChoice.__post_init__`  (lines 425–430)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that a host choice declaration is internally valid. In particular, the default host must be one of the allowed hosts.

**Data flow**: After a `HostChoice` object is created, it compares the default host against the tuple of allowed hosts. If the default is not included, it raises a `ValueError`; otherwise object creation finishes normally.

**Call relations**: This validation runs automatically when a `HostChoice` is constructed. It protects later calls to `credential_host` and `HostChoice.resolve` from dealing with an impossible default.


##### `HostChoice.resolve`  (lines 432–437)

```
def resolve(self, selected: str) -> str | None
```

**Purpose**: Turns a stored host selection into the exact declared host string, or rejects it by returning `None`. It matches case-insensitively, because domain names are normally case-insensitive, but it never returns the user’s free-form spelling.

**Data flow**: It receives a selected string, trims whitespace, lowercases it for comparison, and searches the allowed host list. If it finds a match, it returns the canonical host from the declaration; if not, it returns `None`.

**Call relations**: `credential_host` uses this after reading a workspace’s selected host from the credential store. This keeps proxy decisions tied to a fixed allow-list instead of trusting arbitrary stored text.


##### `credential_host`  (lines 440–457)

```
async def credential_host(store: CredentialStore, workspace_id: UUID, host: str | HostChoice) -> str | None
```

**Purpose**: Finds the provider host that should be used for a credential in a workspace. It supports both fixed hosts and host choices selected from a closed list.

**Data flow**: It receives a credential store, workspace ID, and either a plain host string or a `HostChoice`. If given a plain string, it returns it directly. If given a `HostChoice`, it reads the selected value from the credential store; if no value is stored, it returns the default host; if a value is stored, it resolves it against the allowed list and returns the canonical host or `None`.

**Call relations**: It calls `CredentialStore.get` when a host depends on a stored workspace choice. Both proxy rule generation and sandbox environment setup can use this same function so they agree on the host that is allowed and exported.

*Call graph*: calls 1 internal fn (get).


### Private egress control API
Exposes the internal HTTP interface used by the egress proxy to retrieve policy, fetch secrets, and report usage.

### `core/src/ufo/runtime/access/egress_control.py`

`io_transport` · `request handling`

The Rust egress proxy sits in front of sandbox network traffic, but it is deliberately kept “dumb”: it does not know customer secrets, billing rules, or workspace policy. This file is the trusted control desk it calls. Like a security guard phoning headquarters before opening a door, the proxy sends a signed run or probe token here and asks whether a connection is still allowed, which rules apply, and what usage should be recorded.

The main class, EgressControl, builds FastAPI routers. FastAPI is the web framework that turns Python functions into HTTP endpoints. One router is for the egress proxy and is protected by a shared control token. A separate router is only for Git credentials and uses a different token, so the cache daemon can ask for Git credentials without gaining access to the broader secrets and metering API.

For access decisions, the file decodes the proxy’s run or probe token, checks liveness through PerAgentRules, and returns policy rules in the exact JSON shape the Rust proxy expects. For metering, it groups many usage records together, emits simple counters, and writes billing data inside the correct workspace context. It also supports a limited “tool bridge” path for live runs, letting the proxy ask core to perform approved host-side tool work.

#### Function details

##### `rule_json`  (lines 47–67)

```
def rule_json(rule: Rule) -> dict[str, object]
```

**Purpose**: This turns one internal egress rule into the small JSON object the Rust proxy understands. It is used so both Python and Rust agree on the same plain wire format for network policy.

**Data flow**: It receives a rule object, checks which kind of rule it is, and copies out the fields that matter. The result is a dictionary with a "kind" label and the needed details, such as allowed hosts, injected headers, metering dimensions, or service routing prefixes.

**Call relations**: When EgressControl._resolve has collected the rules for a run or probe, it calls rule_json for each rule before sending the response back to the proxy. This is the translation step between Python policy objects and the proxy’s JSON protocol.

*Call graph*: called by 1 (_resolve).


##### `EgressControl.router`  (lines 144–150)

```
def router(self) -> APIRouter
```

**Purpose**: This builds the private egress-control HTTP routes used by the Rust proxy. It gives the proxy endpoints for authorization, rule resolution, metering, and tool bridge requests.

**Data flow**: It starts with this EgressControl instance and creates a FastAPI router under /internal/egress. It attaches a shared-token guard to the whole router, then registers the endpoint methods and returns the finished router for the main server to mount.

**Call relations**: During server setup, the application calls this to plug the egress-control API into FastAPI. After that, FastAPI calls the registered methods when the proxy sends matching POST requests.

*Call graph*: 2 external calls (APIRouter, Depends).


##### `EgressControl.git_credential_router`  (lines 152–157)

```
def git_credential_router(self) -> APIRouter
```

**Purpose**: This builds the separate private route used by the cache daemon to ask for Git credentials. It intentionally uses its own token so that a Git credential caller cannot reach the broader egress-control API.

**Data flow**: It creates a FastAPI router under /internal, protects it with the cache-specific guard, registers the /git-credential endpoint, and returns the router for mounting.

**Call relations**: The main server mounts this separately from the egress proxy routes. Later, FastAPI calls EgressControl._git_credential only after EgressControl._cache_guard has accepted the cache daemon’s authorization header.

*Call graph*: 2 external calls (APIRouter, Depends).


##### `EgressControl._guard`  (lines 159–161)

```
async def _guard(self, authorization: Annotated[str, Header()]='') -> None
```

**Purpose**: This checks whether a request to the main egress-control API has the correct shared bearer token. It stops unauthorized callers before any policy, secret, or billing work happens.

**Data flow**: It reads the HTTP Authorization header and compares it to the expected control token. If the value matches, it returns normally; if not, it raises an HTTP 401 error, meaning the request is rejected as unauthorized.

**Call relations**: FastAPI runs this guard before the routes created by EgressControl.router. If it rejects the request, methods such as _authorize, _resolve, _meter, and _tool_bridge are never reached.

*Call graph*: 1 external calls (HTTPException).


##### `EgressControl._cache_guard`  (lines 163–165)

```
async def _cache_guard(self, authorization: Annotated[str, Header()]='') -> None
```

**Purpose**: This checks whether a request to the Git credential callback has the cache daemon’s separate shared bearer token. It keeps the cache credential path isolated from the proxy’s broader control token.

**Data flow**: It reads the HTTP Authorization header and compares it to the expected cache control token. A match lets the request continue; a mismatch raises an HTTP 401 error.

**Call relations**: FastAPI runs this guard before routes created by EgressControl.git_credential_router. It protects EgressControl._git_credential without granting access to the other egress-control endpoints.

*Call graph*: 1 external calls (HTTPException).


##### `EgressControl._authorize`  (lines 167–170)

```
async def _authorize(self, body: AuthorizeRequest) -> AuthorizeResponse
```

**Purpose**: This answers the proxy’s basic question: “Is this run or probe token valid and still live?” It also returns a policy generation number when the token is accepted, so the proxy can tell whether cached rules are still current.

**Data flow**: It receives a body containing the raw proxy authorization value. It decodes that value into a run or probe principal, asks for the principal’s live generation if decoding succeeded, and returns an AuthorizeResponse saying whether access is authorized and what generation was found.

**Call relations**: FastAPI calls this for the /internal/egress/authorize endpoint after the shared-token guard passes. It relies on EgressControl._principal to decode the token and EgressControl._live_generation to ask PerAgentRules whether that run or probe is still active.

*Call graph*: calls 2 internal fn (_live_generation, _principal); 1 external calls (__init__).


##### `EgressControl._live_generation`  (lines 172–177)

```
async def _live_generation(self, principal: EgressPrincipal) -> int | None
```

**Purpose**: This asks the policy resolver whether a decoded run or probe is still live, and returns the current policy generation if it is. The generation acts like a version number for the proxy’s cached access rules.

**Data flow**: It receives either a run token or a probe token. For a run token it asks the resolver about the live turn; for a probe token it asks about the live probe. It returns an integer generation when live, or null when not live.

**Call relations**: EgressControl._authorize calls this after successfully decoding a token. It is the bridge between token identity and the resolver’s liveness checks.

*Call graph*: called by 1 (_authorize).


##### `EgressControl._resolve`  (lines 179–181)

```
async def _resolve(self, body: ResolveRequest) -> dict[str, object]
```

**Purpose**: This returns the full set of egress rules the proxy should enforce for a given run or probe token. It is how the proxy learns which hosts, services, injections, and meters are allowed.

**Data flow**: It receives a body containing the proxy authorization value, decodes it into a principal if possible, and asks the resolver for the matching rules. It then converts each internal rule into proxy-readable JSON and returns them in a list.

**Call relations**: FastAPI calls this for the /internal/egress/resolve endpoint after the main guard passes. It uses EgressControl._principal for token decoding and rule_json to produce the exact JSON shape that the Rust proxy expects.

*Call graph*: calls 2 internal fn (_principal, rule_json).


##### `EgressControl._meter`  (lines 183–238)

```
async def _meter(self, body: MeterRequest) -> dict[str, object]
```

**Purpose**: This records usage reported by the proxy, including egress request counts, model token usage, and simple metric counters. It batches records together before writing so many small proxy reports become fewer billing and metric operations.

**Data flow**: It receives a list of metering records. It groups egress counts by workspace and turn, groups token usage by workspace, turn, and model, and groups metric counters by host and dimension. It emits metric counters, then enters each affected workspace context and writes the appropriate billing records inside a workspace database transaction. It returns an empty response when finished.

**Call relations**: FastAPI calls this for the /internal/egress/meter endpoint after the main guard passes. It calls EgressControl._priced_cache_write before token billing so cache-write token usage is priced consistently, then hands counts and token usage to the accounting writer functions.

*Call graph*: calls 1 internal fn (_priced_cache_write); 7 external calls (__init__, workspace_tx, emit_metric, record_egress_request, record_probe_egress_request, record_sandbox_tokens, ws).


##### `EgressControl._priced_cache_write`  (lines 240–253)

```
def _priced_cache_write(self, model: str, usage: Usage) -> Usage
```

**Purpose**: This adjusts token usage for models that do not have a separate price for 30-minute cache writes. In that case, those cache-write tokens are billed as normal input tokens instead.

**Data flow**: It receives a model name and a Usage object. It looks up that model’s pricing. If the model has a 30-minute cache-write price, or there are no such tokens, it returns the usage unchanged. Otherwise, it returns a copied Usage object where those cache-write tokens have been moved into input tokens and zeroed out in the cache-write field.

**Call relations**: EgressControl._meter calls this just before recording sandbox token usage. This keeps the proxy from needing to know detailed pricing rules and makes billing match the host-side adapters.

*Call graph*: called by 1 (_meter); 1 external calls (model_copy).


##### `EgressControl._tool_bridge`  (lines 255–260)

```
async def _tool_bridge(self, body: ToolBridgeControlRequest) -> ToolBridgeResponse
```

**Purpose**: This lets the proxy forward a limited tool request into core, but only for a valid live run token and only when a tool bridge has been configured. It prevents probe tokens or unauthenticated callers from using host-side tool dispatch.

**Data flow**: It receives a proxy authorization value and a tool bridge request. It decodes the token, checks that it is a run token and that a bridge exists, and rejects the request with HTTP 403 if not. If allowed, it enters the run’s workspace context, sends the request to the bridge, and returns the bridge response.

**Call relations**: FastAPI calls this for the /internal/egress/tool-bridge endpoint after the main guard passes. It uses EgressControl._principal for token decoding and then hands the approved request to the configured ToolBridgeRequester.

*Call graph*: calls 1 internal fn (_principal); 2 external calls (HTTPException, ws).


##### `EgressControl._git_credential`  (lines 262–279)

```
async def _git_credential(self, body: GitCredentialRequest) -> dict[str, object]
```

**Purpose**: This answers the cache daemon’s question: “For this run or probe and this Git host, is there a credential I may use?” If not, it tells the daemon to fetch anonymously rather than leaking another account’s identity.

**Data flow**: It receives an optional proxy authorization value and an optional host. If either is missing or the token cannot be decoded, it returns a public result. Otherwise, it asks the resolver for a Git credential for that principal and host. If none is available, it again returns public access; if one is found, it returns the username, token, and a principal label tied to the workspace and account.

**Call relations**: FastAPI calls this for the separate /internal/git-credential endpoint after the cache-specific guard passes. It uses EgressControl._principal to identify the run or probe, then relies on the resolver to choose only credentials that principal is allowed to use.

*Call graph*: calls 1 internal fn (_principal).


##### `EgressControl._principal`  (lines 281–291)

```
def _principal(self, proxy_auth: str) -> EgressPrincipal | None
```

**Purpose**: This decodes the raw proxy authorization string into either a run token, a probe token, or no principal at all. It is the common identity check used before policy, credential, and tool decisions.

**Data flow**: It receives the raw authorization string. If it is empty, it returns null. Otherwise it first tries to decode it as a run token; if that fails, it tries to decode it as a probe token using the same secret. If both attempts fail, it returns null.

**Call relations**: EgressControl._authorize, _resolve, _tool_bridge, and _git_credential all call this before making decisions tied to a workspace or run. It centralizes token decoding so the rest of the file can work with a clear principal object instead of raw header text.

*Call graph*: called by 4 (_authorize, _git_credential, _resolve, _tool_bridge); 1 external calls (__init__).


### Connector credential adapters
Adapts protected credentials and connected accounts into provider-specific proxy calls, sandbox tokens, or direct bearer authentication.

### `extensions/pipedream/ufo_ext_pipedream/proxy.py`

`io_transport` · `request handling and connection teardown`

Some connected services, such as Gmail or other APIs, need private credentials to answer requests. In this project, Pipedream keeps those credentials hidden and injects them on the server side. This file is the bridge that makes that setup feel normal to the rest of the connector code.

The main piece is `PipedreamProxyTransport`, an HTTP transport for `httpx` (the Python HTTP client library). A transport is the part that actually sends a request over the network. Instead of sending a request straight to the provider, this transport reads the original request, wraps it in a new request to Pipedream’s proxy endpoint, and includes the account ID and external user ID so Pipedream knows which stored credential to use.

It also carefully rewrites headers. Pipedream only forwards headers that start with `x-pd-proxy-`, so useful provider headers are renamed with that prefix. Hop-by-hop or sensitive transport headers, like `authorization`, `cookie`, and `content-length`, are deliberately dropped so they do not leak or conflict.

The provider URL is placed into the proxy path after being base64-url encoded, which is like putting the original address into a safe envelope that can travel inside another URL. Pipedream then returns the provider’s real status code, headers, and body, so normal connector behavior, including pagination and error handling, still works.

#### Function details

##### `PipedreamProxyTransport.handle_async_request`  (lines 55–74)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: This function turns one ordinary provider HTTP request into a Pipedream Connect Proxy request. It is used so the connector can ask the provider for data while Pipedream, not this process, supplies the hidden credential.

**Data flow**: It receives an `httpx.Request` meant for the real provider. It asks the Pipedream client for an access token, reads the request body, copies safe headers while adding Pipedream’s required proxy prefix, encodes the original provider URL into a safe string, and builds a new request to Pipedream’s proxy URL with the account and external user in the query. It then sends that new request through the inner transport and returns the resulting `httpx.Response`, preserving the provider-like response for the caller.

**Call relations**: This method is called by `httpx` whenever an async client using this transport tries to send a request. Inside that moment, it uses standard helpers to read the original request body, encode the target URL, build the proxy URL, and create the new request. After rewriting the request, it hands the actual network sending off to the wrapped inner transport.

*Call graph*: 4 external calls (urlsafe_b64encode, Request, aread, URL).


##### `PipedreamProxyTransport.aclose`  (lines 76–77)

```
async def aclose(self) -> None
```

**Purpose**: This function shuts down the underlying HTTP transport when the proxy transport is no longer needed. It prevents the lower-level network resources from being left open.

**Data flow**: It receives no request data. It simply forwards the close operation to the inner transport. After it finishes, the underlying transport has had a chance to release its connections or other resources.

**Call relations**: This is called during cleanup when the async HTTP client or transport is being closed. Rather than doing its own shutdown work, it delegates to the inner transport because that is the part that owns the real network machinery.


### `extensions/pipedream/ufo_ext_pipedream/token.py`

`domain_logic` · `credential setup and use during sandbox connector access`

Pipedream stores OAuth tokens for connected accounts, but the sandbox sometimes needs a safe way to act as if it has one. This file defines that bridge. It does not simply expose a permanent token everywhere. Instead, it creates a small credential object that knows which environment variable a command-line tool should read, how outgoing requests should be marked, and how GitHub git authentication should work.

The main piece is `PipedreamGrantSecret`, which can fetch a granted account token from Pipedream. To avoid asking Pipedream again and again during the same short burst of work, it keeps each token in memory for five minutes. Think of it like checking out a key from a front desk and reusing it during one visit, instead of walking back to the desk before opening every door.

The helper `cli_credential` looks at a connector description. If that connector says it supports command-line credentials, this file builds a `CliCredential` for it. That credential includes the environment variable name, the HTTP authorization header name, the secret-fetching object, and GitHub-specific git login settings. If the connector does not need sandbox-side credentials, the function returns nothing.

#### Function details

##### `PipedreamGrantSecret.secret`  (lines 34–41)

```
async def secret(self, workspace_id: UUID, account_id: str) -> str
```

**Purpose**: This async method gets the token for a specific Pipedream connected account in a specific workspace. It reuses a recently fetched token for a short time so repeated credential lookups do not keep calling Pipedream.

**Data flow**: It receives a workspace ID and an account ID. It first checks its in-memory cache for that account and compares the saved expiry time with the current monotonic clock time, which is a clock used for measuring elapsed time safely. If the cached token is still fresh, it returns that token. Otherwise, it asks the Pipedream client for the account token, stores the token with a new five-minute expiry time, and returns the fresh token.

**Call relations**: When some sandbox credential needs the real secret value, this method is the piece that supplies it. It uses the current Pipedream client at the moment of the read, which matters because tests or different runtime setups may replace the transport underneath. Its only handoff is to the Pipedream client, which performs the actual token lookup.

*Call graph*: 2 external calls (monotonic, pipedream_client).


##### `cli_credential`  (lines 44–53)

```
def cli_credential(spec: pipedream.ConnectorSpec) -> CliCredential | None
```

**Purpose**: This function converts a Pipedream connector description into the command-line credential object the sandbox understands. If the connector does not declare an environment variable for command-line use, it returns `None` because there is no sandbox credential to create.

**Data flow**: It receives a connector specification. If the specification has no command-line environment variable, the function stops and returns `None`. If one is present, it builds a `CliCredential` containing that environment variable name, the authorization header name, a new `PipedreamGrantSecret` that can fetch the token later, and the GitHub git-login settings used by tools such as `git clone`.

**Call relations**: This is the file’s public assembly point: other code can call it while interpreting a connector specification. It does not fetch a token immediately. Instead, it packages the future token lookup into `PipedreamGrantSecret` and hands that package to `CliCredential`, so the actual secret is only read when the credential is used.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/sources/ufo_ext_sources/direct.py`

`domain_logic` · `during source sync authentication`

Some data sources cannot get credentials from the normal account-broker flow, or a deployment may choose to keep the API key itself. In that case, the member adds a key for a provider, and the system stores it encrypted under that provider’s name. This file is the small adapter that retrieves that key when a source sync needs it.

The main idea is deliberately simple: the account handle only says “use the direct path”; it does not carry the secret. When the sync job runs on the trusted host side, `DirectAuthProxy` uses `CredentialAccess`, which is a controlled doorway to the workspace’s credential store, to read the slot named after the connector’s provider. It then wraps the secret in a `Credential` object as a bearer token, meaning a token sent in the HTTP authorization header to the external provider.

The important safety boundary is that the decrypted key stays in the host-side sync process. It is not sent into the sandbox or exposed to an agent. Like a clerk retrieving a sealed key from a locked cabinet only at the moment it is needed, this file keeps the secret’s path narrow and predictable.

#### Function details

##### `DirectAuthProxy.credential`  (lines 29–30)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: This method fetches the API key for a provider from the credential store and turns it into a bearer credential that provider HTTP requests can use. It is used when a source is routed through the direct authentication path.

**Data flow**: It receives a workspace ID, a provider name, and an account handle. The provider name is used to look up the stored secret through `CredentialAccess`; the account handle is not used because, in this direct model, the provider-named secret is the real authentication material. The method returns a `Credential` containing that secret as a bearer token, without logging it or exposing it elsewhere.

**Call relations**: When the sync system needs credentials for a directly authenticated source, it asks this method for them. The method reads the provider’s credential slot, then hands the value into `Credential` so the rest of the sync code can authenticate outbound provider requests in the standard credential format.

*Call graph*: 1 external calls (__init__).

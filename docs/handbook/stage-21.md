# Security, authorization, credentials, access policy, and billing  `stage-21` (cross-cutting infrastructure)

This stage is shared safety support that runs around the main work of the system. Before an agent acts, it helps answer four practical questions: who is allowed to do this, which secrets may be used, what outside services may be contacted, and whether the workspace can pay for the work.

Identity and authorization checks are the “door locks.” They track whether work is being done for a person, a workspace, or an agent, then decide who may chat with agents, view transcripts, or read private scheduled tasks. Secrets, network egress, and connector access are the “customs checkpoint.” They let sandboxed code reach approved outside hosts, attach hidden credentials only when allowed, and keep API keys from leaking into chat or agent code. Usage accounting and prepaid billing are the “meter and wallet.” They price model calls and other resources, enforce limits, deduct prepaid credit, and connect to billing services.

The untrusted text wrapper marks outside content as evidence, not instructions. The workspace helper keeps identity, credentials, model accounts, and charges tied to the correct workspace.

## Sub-stages

- [Identity and authorization checks](stage-21.1.md) `stage-21.1` — 8 files
- [Secrets, network egress, and connector access](stage-21.2.md) `stage-21.2` — 8 files
- [Usage accounting and prepaid billing](stage-21.3.md) `stage-21.3` — 5 files

## Files in this stage

### Workspace safety boundaries
These files establish safe handling for untrusted input and bind runtime activity to the correct workspace for credentials, model accounts, and billing.

### `core/src/ufo/harness/untrusted.py`

`util` · `cross-cutting during tool results and agent hand-back`

Some tool results may come from places the system does not trust, such as a web page or a third-party service. That kind of text can contain misleading instructions like “ignore your previous rules.” This file solves that problem by putting the text inside a standard “untrusted content” wall. Think of it like placing a suspicious letter inside a clear evidence bag: the reader can examine it, but it is clearly marked as something not to follow.

The file defines the exact warning message, the opening and closing tags around the content, and the escape rule that keeps the content from breaking out of the wrapper. The important detail is the closing tag. If outside text included the same closing marker, it might try to end the protected section early and make later text look trusted. The `wall` function prevents that by replacing any closing marker inside the content with a harmless escaped version.

This matters because different parts of the system pass untrusted text back to agents. By using one shared helper, they all mark that text in the same way, with the same warning and the same protection against escaping the boundary.

#### Function details

##### `wall`  (lines 22–30)

```
def wall(source: str, content: str) -> str
```

**Purpose**: Wraps outside or untrusted text in a clear warning and a protected tag block. Someone uses this when content should be available for reading, but must not be treated as instructions.

**Data flow**: It takes a `source`, which names where the content came from, and `content`, which is the outside text itself. It builds a warning that names the source, opens an `<untrusted-content>` section, escapes any closing tag found inside the content, and then adds the real closing tag at the end. The result is one string that safely presents the outside text as untrusted data.

**Call relations**: This function is the shared doorway for any path that gives untrusted content to an agent. The tool-result path uses it when a tool returns data from outside the trusted workspace, and the hand-back path uses it when a background child agent returns untrusted output to a parent. By both calling `wall`, those paths use the same wording and the same escape behavior instead of inventing separate versions.


### `core/src/ufo/runtime/workspace.py`

`orchestration` · `cross-cutting`

A workspace is like a customer account or tenant. Many parts of the system need to read secrets, call AI model providers, or record usage charges, and all of those actions must belong to the right workspace. This file creates a temporary workspace scope: code enters `with ws(workspace_id):`, and anything inside can ask `ws_current()` for the active workspace instead of passing the workspace ID through every function.

The file also protects credentials. When code asks for a credential, it first checks the configured credential store for the workspace’s own saved key. If none exists, it falls back to a platform default from environment variables. For some model calls, it can also route to a specific member’s connected provider account, but only when that member and model were explicitly bound.

For model clients, the file records not just the key but who pays: the platform, a stored key owner, or a connected-account plan. This matters because retries must not silently switch from one payer to another.

Finally, `billable_event()` collects model usage during a block of work and writes it to the workspace ledger when the block exits. The important idea is simple: bind the workspace once at the boundary of a turn or job, then all secrets, row-level database scope, and billing follow that same workspace automatically.

#### Function details

##### `model_authority`  (lines 71–84)

```
def model_authority(authority: ExecutionAuthority, models: frozenset[str]=frozenset()) -> Iterator[None]
```

**Purpose**: Temporarily says that a particular member’s connected model account should be used for specific model names. This prevents a member’s personal provider account from being used for unrelated background or workspace-wide model calls.

**Data flow**: It receives an execution authority, meaning who is allowed to act, and a set of model names. It stores that pair in a context-local variable for the duration of the `with` block, then restores the previous value afterward. Nothing is returned; the visible change is that credential lookup inside the block can see this temporary routing rule.

**Call relations**: Code around a model call uses this as a short-lived wrapper when that call should run on a member’s own connected account. Later, workspace credential functions consult the bound authority through `_slot_order` to decide whether to try the member-specific credential before the workspace credential.


##### `init_workspace_credentials`  (lines 90–94)

```
def init_workspace_credentials(store: CredentialStore | None) -> None
```

**Purpose**: Installs the credential store that workspace-scoped code will use to read and write saved secrets. It is normally called once during application startup.

**Data flow**: It receives either a credential store object or `None`. It saves that value in a module-level variable. After this, workspace credential methods either use the store for saved BYOK credentials, meaning “bring your own key,” or fall back to platform environment variables if no store is configured.

**Call relations**: Startup code calls this before request or job handling begins. The credential-reading and credential-writing methods on `WorkspaceScope` later rely on the stored value instead of receiving the store as an argument each time.


##### `ResolvedModelClient.complete`  (lines 124–125)

```
def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Starts a model completion using an already resolved model client. The surrounding object keeps the payer facts beside the client so billing decisions stay attached to the call.

**Data flow**: It receives a model request and passes it directly to the wrapped model client. The result is an asynchronous stream of model events, such as partial output or usage information. It does not change the payer information stored on the wrapper.

**Call relations**: Callers use this after they have already chosen the credential and payer for a model request. This method hands the actual work off to the underlying `ModelClient.complete` method while the `ResolvedModelClient` object remains the record of who funded the call.


##### `BillableEvent.usage`  (lines 135–145)

```
def usage(self, model: str, usage: Usage, pricing: Pricing=CORE_PRICING, byok: bool=False) -> None
```

**Purpose**: Adds one reported model-usage item to a billable event. It is used when a provider has said how many tokens or other metered units were consumed.

**Data flow**: It receives the model name, the usage numbers, a pricing table, and a flag saying whether the call used a non-platform key. It appends those facts to the event’s internal list. Nothing is written to the database yet; the information is saved until the billable block exits.

**Call relations**: Model-calling code records usage through this method inside `WorkspaceScope.billable_event`. When that context block finishes, `billable_event` reads the accumulated entries and records them for the active workspace.


##### `WorkspaceScope.credential`  (lines 161–183)

```
async def credential(self, slot: str, env: str | None=None, model: str | None=None) -> str
```

**Purpose**: Returns the secret value for a named credential slot within this workspace. It makes sure a key is always fetched in the context of a specific workspace, and it fails clearly if no key is available.

**Data flow**: It takes a credential slot, an optional environment-variable name, and optionally the model asking for the key. It builds the order of possible slots with `_slot_order`, tries the credential store if one exists, and skips missing stored values. If nothing is stored, it reads a platform default from the environment. The output is the credential string, or it raises `CredentialSlotUnset` if no usable value exists.

**Call relations**: Any code that needs a workspace-scoped secret calls this through `ws_current()`. It delegates the member-versus-workspace decision to `_slot_order` and uses `deploy_env` only as the final fallback for platform defaults.

*Call graph*: calls 1 internal fn (_slot_order); 2 external calls (__init__, deploy_env).


##### `WorkspaceScope.model_credential`  (lines 185–205)

```
async def model_credential(self, slot: str, env: str | None, model: str) -> ModelCredential
```

**Purpose**: Returns both the credential for a model call and the payer category attached to that credential. This prevents a model attempt from being retried with a different payer while keeping the original billing decision.

**Data flow**: It receives a credential slot, an optional environment fallback name, and the model being called. It tries candidate stored slots from `_slot_order`. A plain stored key becomes key-funded, a valid connected-account grant becomes plan-funded, and a spent grant is refreshed before use. If no stored value is found, it reads the platform environment key and marks it platform-funded. The output is a `ModelCredential` containing the secret, funding type, and payer name.

**Call relations**: Model-client setup calls this before creating or using a provider client. It calls `_refreshed_credential` when a connected-account grant has expired, and otherwise uses `read_grant` and `deploy_env` to classify the credential source.

*Call graph*: calls 2 internal fn (_refreshed_credential, _slot_order); 4 external calls (__init__, __init__, read_grant, deploy_env).


##### `WorkspaceScope._refreshed_credential`  (lines 207–236)

```
async def _refreshed_credential(self, store: 'CredentialStore', candidate: str, slot: str, stored: str, grant: Grant) -> ModelCredential
```

**Purpose**: Refreshes an expired connected-account grant safely when several calls may notice the expiry at the same time. It is careful to avoid spending the same one-time refresh token twice.

**Data flow**: It receives the credential store, the specific stored slot, the original slot name, the stored grant text, and the parsed grant. It tries to claim a short refresh lease by rotating the stored value. If it wins, it asks the provider for a refreshed grant and stores it. If it loses, it waits briefly, rereads the stored value, and uses the winner’s refreshed result when available. It returns a plan-funded `ModelCredential`, returns a plain key-funded credential if the stored value changed into a normal key, or raises `GrantRefusedRefresh` if refresh never succeeds in time.

**Call relations**: `WorkspaceScope.model_credential` calls this only for spent grants. This helper coordinates with the credential store, `refreshed`, `read_grant`, time checks, and short sleeps so concurrent model calls do not corrupt or revoke a connected provider account.

*Call graph*: calls 1 internal fn (__init__); called by 1 (model_credential); 6 external calls (__init__, sleep, model_copy, time, read_grant, refreshed).


##### `WorkspaceScope.member_routed_call`  (lines 238–241)

```
def member_routed_call(self, slot: str, model: str) -> bool
```

**Purpose**: Answers whether a particular model call should first try the speaking member’s own provider credential. This is used to distinguish member-paid coding calls from ordinary workspace or platform calls.

**Data flow**: It receives a credential slot and model name. It asks `_slot_order` what credential slots would be tried. If the order contains more than one slot, the first is member-specific, so the function returns true; otherwise it returns false.

**Call relations**: Callers use this as a simple yes-or-no check before or around model credential selection. The real routing decision is centralized in `_slot_order`, so this function stays consistent with `credential` and `model_credential`.

*Call graph*: calls 1 internal fn (_slot_order).


##### `WorkspaceScope.member_payer`  (lines 243–249)

```
def member_payer(self, slot: str, model: str) -> str | None
```

**Purpose**: Returns the member-specific payer slot for a model call, if that call is routed through a member’s own account. If the call is not member-routed, it returns nothing.

**Data flow**: It receives a credential slot and model name. It gets the candidate slot order from `_slot_order`. If a member-specific slot appears before the normal workspace slot, it returns that first slot as the expected payer; otherwise it returns `None`.

**Call relations**: Billing or model orchestration code can use this to know which member credential would pay for a routed call. It relies on `_slot_order` so it matches the same routing rules used by actual credential lookup.

*Call graph*: calls 1 internal fn (_slot_order).


##### `WorkspaceScope._slot_order`  (lines 251–262)

```
def _slot_order(self, slot: str, model: str | None) -> list[str]
```

**Purpose**: Builds the list of credential slots to try for a credential lookup. Its main job is deciding whether to try a member-specific slot before the workspace-wide slot.

**Data flow**: It reads the currently bound model authority, if any, and receives the requested slot plus an optional model name. If the slot is not one of the member-routable model slots, or no suitable member/model binding exists, it returns just the original slot. If a member authority is bound for that model, it returns the member’s slot first and the workspace slot second.

**Call relations**: This is the shared routing rule used by `credential`, `model_credential`, `credential_is_stored`, `member_routed_call`, and `member_payer`. It uses `member_slot` to form the member-specific credential name when member routing applies.

*Call graph*: called by 5 (credential, credential_is_stored, member_payer, member_routed_call, model_credential); 1 external calls (member_slot).


##### `WorkspaceScope.member_holds_own_model_key`  (lines 264–267)

```
async def member_holds_own_model_key(self, authority: ExecutionAuthority) -> bool
```

**Purpose**: Checks whether the acting member has connected at least one model-provider account of their own. It gives callers a simple boolean answer for onboarding or routing decisions.

**Data flow**: It receives an execution authority. It asks `member_model_provider` for the member’s first connected provider. If one exists, it returns true; otherwise it returns false.

**Call relations**: This is a convenience wrapper over `member_model_provider`. Code that only needs a yes-or-no answer can call it without inspecting provider names.

*Call graph*: calls 1 internal fn (member_model_provider).


##### `WorkspaceScope.member_model_provider`  (lines 269–275)

```
async def member_model_provider(self, authority: ExecutionAuthority) -> str | None
```

**Purpose**: Returns the first model provider account connected by the acting member, or nothing if they have connected none. The first provider is treated as the preferred one.

**Data flow**: It receives an execution authority and asks `member_model_providers` for all connected providers in configured order. It returns the first provider from that tuple, or `None` when the tuple is empty.

**Call relations**: `member_holds_own_model_key` calls this for a boolean check. This function in turn delegates the real store inspection to `member_model_providers`.

*Call graph*: calls 1 internal fn (member_model_providers); called by 1 (member_holds_own_model_key).


##### `WorkspaceScope.member_model_providers`  (lines 277–291)

```
async def member_model_providers(self, authority: ExecutionAuthority) -> tuple[str, ...]
```

**Purpose**: Finds every model provider account that the acting member has connected in this workspace. It checks the person’s own stored credentials, not the workspace or platform fallback.

**Data flow**: It receives an execution authority and extracts the member ID from it. If there is no credential store or no member ID, it returns an empty tuple. Otherwise it checks each member-routable credential slot in declaration order, looks for a member-specific stored credential, and collects the matching provider names. The result is a tuple of connected provider names.

**Call relations**: `member_model_provider` calls this when it needs the preferred provider. It uses `authority_member_id` to identify the member and `member_slot` to look up that member’s provider credentials.

*Call graph*: called by 1 (member_model_provider); 2 external calls (member_slot, authority_member_id).


##### `WorkspaceScope.credential_is_stored`  (lines 293–307)

```
async def credential_is_stored(self, slot: str, model: str | None=None) -> bool
```

**Purpose**: Checks whether a credential is saved in the credential store rather than coming from a platform environment default. This matters because stored provider keys are treated differently for billing.

**Data flow**: It receives a slot and optionally a model name. If no store is configured, it returns false. Otherwise it tries each candidate slot from `_slot_order`; the first stored value found makes it return true. If all candidates are missing, it returns false.

**Call relations**: Billing and model-routing code can call this to decide whether usage was paid by a workspace or member key instead of the platform. It shares `_slot_order` with actual credential lookup so the answer follows the same member-routing rules.

*Call graph*: calls 1 internal fn (_slot_order).


##### `WorkspaceScope.rotate_credential`  (lines 309–314)

```
async def rotate_credential(self, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Replaces an existing stored credential only if it still has the expected old value. This protects against overwriting someone else’s newer update.

**Data flow**: It receives a slot, the expected current stored text, and the new plaintext credential. If no credential store is configured, it returns false. Otherwise it asks the store to perform an atomic rotate for this workspace and returns whether that rotate succeeded.

**Call relations**: Credential update and grant-refresh flows use this compare-and-swap style operation when they need safe replacement. In this file, grant refresh uses the store’s rotation behavior directly for the same reason.


##### `WorkspaceScope.put_credential`  (lines 316–320)

```
async def put_credential(self, slot: str, plaintext: str) -> None
```

**Purpose**: Stores a new credential for the current workspace. It is used after authorization has already decided the caller is allowed to add that secret.

**Data flow**: It receives a slot name and plaintext credential. If no credential store is configured, it raises an error because there is nowhere safe to save it. Otherwise it writes the credential into the store under this workspace.

**Call relations**: Credential setup code calls this through the active workspace scope. Later calls to `credential` or `model_credential` can read the saved value from the same store.


##### `WorkspaceScope.billable_event`  (lines 323–335)

```
async def billable_event(self) -> AsyncIterator[BillableEvent]
```

**Purpose**: Creates a block where model usage can be collected and then recorded to this workspace’s ledger when the block ends. Charges are written even if later processing of the model output fails.

**Data flow**: It creates a fresh `BillableEvent` and yields it to the caller. The caller adds usage records to that event during the block. When the block exits, it opens a workspace database transaction and writes each collected usage item with `record_workspace_usage`. It does not return a separate result; its lasting effect is the recorded usage.

**Call relations**: Model-calling code wraps provider attempts in this context manager. `BillableEvent.usage` fills the event during the attempt, and this function hands the final records to `workspace_tx` and `record_workspace_usage` for durable accounting.

*Call graph*: 3 external calls (__init__, workspace_tx, record_workspace_usage).


##### `ws`  (lines 339–347)

```
def ws(workspace_id: UUID) -> Iterator[WorkspaceScope]
```

**Purpose**: Binds a workspace ID as the current workspace for a temporary block of code. This is the entry gate that makes later calls to secrets, billing, and workspace-scoped database work belong to the same workspace.

**Data flow**: It receives a workspace ID and stores it in a context-local database variable. It yields a `WorkspaceScope` for that ID to the block. When the block finishes, it restores the previous workspace binding so the scope does not leak into unrelated work.

**Call relations**: Turn handlers and job runners call this at their boundary. Inside the block, `ws_current` can recover the workspace scope, and database code using the shared current-workspace context can apply the same workspace restriction.

*Call graph*: 3 external calls (__init__, reset, set).


##### `ws_current`  (lines 350–356)

```
def ws_current() -> WorkspaceScope
```

**Purpose**: Returns the currently bound workspace scope. If no workspace has been bound, it raises a clear error instead of risking an unscoped secret read or unbilled operation.

**Data flow**: It reads the current workspace ID from the context-local database variable. If the ID is missing, it raises `WorkspaceUnbound`. If present, it returns a new `WorkspaceScope` object for that workspace ID.

**Call relations**: Code throughout the runtime calls this when it needs the active workspace without receiving it as an argument. It depends on `ws` having already established the workspace at the start of the turn or job.

*Call graph*: 3 external calls (__init__, __init__, get).

## 📊 State Registers Touched

- `reg-config-stack` — The merged settings that tell the whole service how to start, connect, and behave.
- `reg-feature-flags` — The shared on/off switches and rollout choices that let operators change behavior without redeploying.
- `reg-model-catalog` — The shared list of available AI models, their abilities, providers, prices, and credential needs.
- `reg-database-schema` — The durable database layout and connection layer used to store and retrieve system records safely.
- `reg-workspace-directory` — The saved list of workspaces, members, agents, admins, and workspace-level settings.
- `reg-auth-sessions` — The sign-in state and signed tokens that prove who a web, surface, or API request belongs to.
- `reg-acting-authority` — The shared record of whether work is acting as a member, an agent, or only the workspace.
- `reg-credentials-connections` — The stored secrets, connected accounts, grants, and refreshable permissions used to call outside services.
- `reg-access-subjects` — The shared visibility rules that say which members or audiences may read conversations, sources, and objects.
- `reg-egress-policy` — The network access rules that decide which outside hosts sandboxed or connector code may contact.
- `reg-billing-ledger` — The shared meter and wallet state for usage costs, spend caps, prepaid balances, and billing identity.
- `reg-turn-runtime-config` — The per-turn saved runtime settings that must survive retries and keep a turn using the same execution choices.
- `reg-host-environment` — The assembled per-turn world given to the agent: prompts, skills, files, model choice, tools, and extension context.
- `reg-tool-catalog` — The shared catalog of tools and the policies that decide which tools may run with which permissions.
- `reg-sandbox-state` — The remembered sandbox handles and execution environments where commands, files, and risky work run safely.
- `reg-connector-brokers` — The shared catalog and runtime state for service connectors, MCP servers, broker accounts, and approved actions.
- `reg-source-index-memory` — The saved external pages, search chunks, embeddings, memories, and recall indexes used as workspace knowledge.
- `reg-scheduled-jobs` — The durable background work list for timers, recurring conversations, monitors, reports, and long-running tasks.
- `reg-artifact-publication` — The shared state for files, previews, signed downloads, hosted sites, app pages, and published outputs.
- `reg-object-system` — The common address book and audit trail for durable workspace objects such as agents, members, artifacts, and connectors.
- `reg-agent-provisioning` — The saved provenance, setup needs, policies, and ownership for agents that are shipped by extensions or created in workspaces.
- `reg-transcript-access-audit` — The durable audit trail recording privileged reads of private transcripts for later security review.
- `reg-enrichment-state` — The consent, fetched profile/company enrichment results, replay data, and provider rate-limit pause state for enrichment pipelines.
- `reg-credential-fulfillment` — Durable one-time markers that a requested credential/setup slot has been fulfilled for a workspace.
- `reg-ledger-export-state` — Saved progress and options for exporting billing/ledger records, including BYOK-related export bookkeeping.
- `reg-egress-policy-cache-generation` — Workspace egress-rule generation counters and proxy cache-invalidation state for refreshed network access decisions.
- `reg-model-client-pools` — Shared outbound model/provider client sessions, connection pools, retry state, and provider-side rate-limit/backoff buckets used across turns.
- `reg-action-proposal-state` — Durable proposal records for actions or changes that require later review, approval, rejection, or replay.

# Runtime workspace, authority, agent scope, and shared path limits  `stage-20.2`

This stage is shared behind-the-scenes support. It sets the “current context” for code while the system is running, so later work knows whose workspace it belongs to, which person or workspace has authority, and which agent is allowed to act. Think of it like badges and room labels in a building: before doing sensitive work, code checks the badge and the room.

workspace.py records the current workspace, meaning the customer area the work is happening in. That keeps secrets, model costs, and billing attached to the right place. authority.py describes whether the work is using a particular member’s private credentials or broader workspace authority, and it can convert this into the older stored format. agent_scope.py tracks the active agent for agent-owned abilities and blocks mistakes such as using no agent or an agent from another workspace. object_scope.py adds a temporary override for object actions, so an object handler can act as the normal agent or a specific dispatched agent. file_changes.py provides one shared maximum path length for file-change handling.

## Files in this stage

### Actor authority and agent scopes
Defines the member or workspace authority and the active agent context used while runtime work and object actions execute.

### `core/src/ufo/runtime/agent_scope.py`

`domain_logic` · `cross-cutting`

Some parts of the system need to know, “Which agent is acting right now?” This file provides that answer in a safe way. It uses a context variable, which is like a task-local name tag: code running in one flow can have its own current agent without interfering with another flow running at the same time.

The main idea is an agent scope: a pair made of a workspace ID and an agent ID. The workspace is the larger area where work is happening, and the agent is the actor inside it. The `agent` context manager temporarily pins an agent to the current workspace. Code inside `with agent(agent_id):` can then ask for the current agent later without passing the ID through every function call.

The safety checks are important. If code tries to switch to a different agent while one is already bound, it fails instead of silently changing identity. If code asks for the current agent when none is bound, it raises a clear error. If the current workspace no longer matches the workspace captured when the agent was bound, it also fails. This is like checking both the badge and the building before opening a restricted door.

#### Function details

##### `agent`  (lines 28–38)

```
def agent(agent_id: UUID) -> Iterator[AgentScope]
```

**Purpose**: Temporarily marks a block of code as running for one specific agent in the current workspace. Someone uses it when entering an agent-owned operation so later code can safely know who the acting agent is.

**Data flow**: It receives an agent ID and reads the current workspace from `ws_current()`. It builds an `AgentScope` containing both IDs, checks whether another different agent is already bound, then stores this scope in the task-local context. While the caller’s `with` block runs, `agent_current()` can see that scope; when the block ends, the old context is restored.

**Call relations**: This is the entry point for creating an agent boundary. It calls `ws_current()` to tie the agent to the workspace that is active at that moment, then creates an `AgentScope`. Code later calls `agent_current()` inside that boundary to retrieve the same identity.

*Call graph*: 2 external calls (__init__, ws_current).


##### `agent_current`  (lines 41–48)

```
def agent_current() -> AgentScope
```

**Purpose**: Returns the agent scope that is currently bound, or loudly refuses if there is no valid agent identity. It is used by code that needs to prove it is running inside an agent boundary before using agent-owned capabilities.

**Data flow**: It reads the task-local current agent scope. If there is none, it raises `AgentUnbound` with a message telling the caller to wrap the work in `with agent(agent_id):`. If there is a scope, it compares that scope’s workspace ID with the current workspace from `ws_current()`. If they match, it returns the scope; if not, it raises an error because the agent identity no longer belongs to the active workspace.

**Call relations**: This function is the lookup side of the flow started by `agent`. It depends on `ws_current()` to confirm the workspace has not changed underneath the bound agent. Agent-owned code calls it when it needs the current workspace-and-agent identity before continuing.

*Call graph*: 2 external calls (__init__, ws_current).


### `core/src/ufo/runtime/authority.py`

`data_model` · `cross-cutting`

This file answers a simple but important question for every execution: “Whose permission is this using?” Sometimes work is done as a real member, using that member’s private credentials and subject to that member still having a valid seat. Other times work is done at the workspace level, with no individual member behind it. The file represents those two cases with small immutable data objects: MemberAuthority and WorkspaceAuthority. Immutable means the answer cannot quietly change halfway through a run, which matters for security and auditing.

The file also bridges between this clearer model and an existing storage format where “no member” is represented as null. In plain terms, it is like translating between a labeled badge system and an older sign-in sheet where a blank name means “workspace.” The helper functions turn a UUID, which is a unique member identifier, into an authority object, and turn an authority object back into either that UUID or null.

One special rule appears in turn_authority: a single turn cannot claim both a speaker member and a delegated “on behalf of” member. If both are present, the file rejects it immediately because that would make the execution’s authority ambiguous.

#### Function details

##### `authority_from_member_id`  (lines 28–30)

```
def authority_from_member_id(member_id: UUID | None) -> ExecutionAuthority
```

**Purpose**: This function converts the older nullable member-id representation into the clearer authority object used at runtime. A missing member id means workspace authority; a present member id means execution as that member.

**Data flow**: It receives either a UUID for a member or None. If the input is None, it returns the shared workspace authority object. If the input is a UUID, it creates and returns a MemberAuthority carrying that UUID. It does not change any outside state.

**Call relations**: turn_authority calls this after deciding which member id, if any, should define the turn’s execution authority. This function is the small translation step from stored or token-style data into the runtime authority model.

*Call graph*: called by 1 (turn_authority); 1 external calls (__init__).


##### `authority_member_id`  (lines 33–41)

```
def authority_member_id(authority: ExecutionAuthority) -> UUID | None
```

**Purpose**: This function converts a runtime authority back into the older storage-friendly shape: a member UUID or None. It is useful when code needs to write authority into records or tokens that still use a nullable member field.

**Data flow**: It receives an authority object. If it is a MemberAuthority, it extracts and returns the member UUID inside it. If it is a WorkspaceAuthority, it returns None. If something else is passed in, it raises a TypeError so bad data is caught instead of silently misread.

**Call relations**: This is the reverse of authority_from_member_id. It sits at the boundary where newer authority objects need to be encoded for older persistence or signing formats.


##### `turn_authority`  (lines 44–52)

```
def turn_authority(speaker_member_id: UUID | None, on_behalf_of_member_id: UUID | None) -> ExecutionAuthority
```

**Purpose**: This function decides the single authority for a conversation turn or similar unit of work. It enforces that the turn is either spoken by a member, delegated on behalf of a member, or workspace-level, but never both member forms at once.

**Data flow**: It receives two optional member UUIDs: one for the speaker and one for a delegated member. If both are present, it raises a ValueError because the authority would be unclear. Otherwise it chooses the speaker id if present, falls back to the delegated id if present, and then converts that chosen value into an ExecutionAuthority. If neither is present, the result is workspace authority.

**Call relations**: This function is a higher-level decoder built on authority_from_member_id. Code that knows about turn fields can call it to get one clean authority object, while authority_from_member_id does the final conversion into MemberAuthority or WorkspaceAuthority.

*Call graph*: calls 1 internal fn (authority_from_member_id).


### `core/src/ufo/runtime/object_scope.py`

`domain_logic` · `request handling`

Some object actions can be routed to a particular agent before the action’s handler code runs. This file stores that chosen agent in task-local state, meaning the value is local to the current async task or execution context and does not leak into unrelated work. A good analogy is putting a temporary name tag on one worker for one job, then removing it as soon as the job is done.

The `ObjectAgent` model is the small, frozen record for that selected agent: its unique ID and name. `ObjectActionTarget` is a fuller record of what an object action is acting on, such as the object kind, optional instance name, selected agent, and generation values used to make repeated or interrupted work safe. The models are frozen and reject unknown fields, so callers cannot accidentally mutate or smuggle in extra data.

The main behavior is built around a private context variable. `object_agent` temporarily places an `ObjectAgent` into that context while a block of code runs. `object_agent_id` then reads that temporary target. If no object-specific target was set, it falls back to the ordinary current agent from `agent_scope`. Without this file, object handlers would have to pass this target everywhere by hand, or risk using the wrong agent during cross-agent object dispatch.

#### Function details

##### `object_agent`  (lines 44–52)

```
def object_agent(target: ObjectAgent | None) -> Iterator[None]
```

**Purpose**: Temporarily marks the current execution as acting on behalf of a specific object-selected agent. Code inside the `with` block can then discover that agent without having it passed through every function call.

**Data flow**: It receives either an `ObjectAgent` or `None`. If it gets `None`, it simply lets the wrapped code run unchanged. If it gets an agent, it stores that agent in the task-local context before the wrapped code runs, then restores the previous value afterward, even if the wrapped code fails.

**Call relations**: This is the setup step for object action execution. The dispatcher or nearby runtime code can enter this context before calling a handler, and later calls to `object_agent_id` inside that same flow will see the temporary agent target.


##### `object_agent_id`  (lines 55–57)

```
def object_agent_id() -> UUID
```

**Purpose**: Returns the agent ID that object-related code should use right now. It chooses the temporary object target if one was set; otherwise it uses the normal current agent.

**Data flow**: It reads the private task-local target. If a target is present, it returns that target’s UUID. If no target is present, it asks `agent_current()` for the normal current agent and returns that agent’s ID.

**Call relations**: This is the lookup step used after `object_agent` may have set a temporary target. When there is no object-specific target, it hands off to `ufo.runtime.agent_scope.agent_current` so ordinary agent scoping still works.

*Call graph*: 1 external calls (agent_current).


### Shared file path limits
Provides the common path-length limit used by runtime file-change handling.

### `core/src/ufo/runtime/tools/file_changes.py`

`config` · `cross-cutting`

This file is very small, but it sets an important safety boundary. It defines `FILE_CHANGE_PATH_MAX_CHARS` as 4,096 characters, meaning file paths used in the file-change tooling should not be longer than that. A file path is the text address of a file, like `/home/user/project/app.py`. Without a shared limit like this, different parts of the system might accept different path lengths, which can lead to confusing errors, oversized messages, or unsafe assumptions. Think of it like a size limit on a package before it can go through a mail slot: every part of the delivery system needs to agree on the same limit. This file does not perform checks by itself. Instead, it provides the number that other code can import and use when validating, storing, or sending file-change information.


### Workspace scope
Defines the workspace context that ties runtime work, secrets, model usage, and billing to the correct customer workspace.

### `core/src/ufo/runtime/workspace.py`

`domain_logic` · `cross-cutting during turns, jobs, credential lookup, model calls, and billing`

Many parts of the system need to know “which workspace is this for?” They may need an API key, they may spend money on an AI model call, or they may write database rows that must belong to one workspace only. Passing the workspace ID into every function would be noisy and easy to forget, so this file provides a safer pattern: code enters `with ws(workspace_id):`, and everything inside can ask `ws_current()` for the active workspace.

Think of it like putting a colored wristband on every task at the door. Any later check for a key, a bill, or a workspace-limited database action reads the wristband instead of guessing.

The file also decides where model credentials come from. It first looks for a stored workspace or member key, then falls back to platform environment variables. For connected provider accounts, it can refresh an expired grant carefully so that several callers do not all try to spend the same one-time refresh token.

Billing is grouped with `billable_event()`: model usage is collected during a block and written to the workspace ledger when the block exits, even if later processing fails. Without this file, calls could accidentally use the wrong customer’s key, skip billing, or run without a clear workspace boundary.

#### Function details

##### `model_authority`  (lines 71–84)

```
def model_authority(authority: ExecutionAuthority, models: frozenset[str]=frozenset()) -> Iterator[None]
```

**Purpose**: Temporarily marks which member’s own connected model account should be used for certain models. This matters because some model calls should be paid by a member’s connected provider account, while other calls in the same workspace should still use the workspace or platform key.

**Data flow**: It receives an execution authority, which may identify a member, and a set of model names. It stores that pair in task-local context for the duration of the `with` block, then restores the previous value afterward. Nothing is returned; the change is only visible to code running inside the block.

**Call relations**: Credential resolution later asks `_slot_order` whether a member-specific slot should be tried before the normal workspace slot. This function sets the context that makes that decision possible, and its cleanup prevents the member routing from leaking into unrelated work.


##### `init_workspace_credentials`  (lines 90–94)

```
def init_workspace_credentials(store: CredentialStore | None) -> None
```

**Purpose**: Installs the credential store that workspace code will use to read and write stored secrets. It is called during setup so later workspace-scoped calls can look up customer-provided keys.

**Data flow**: It receives either a credential store object or `None`. It saves that value in a module-wide variable. Afterward, credential lookups either use that store or, if it is `None`, only fall back to platform environment variables.

**Call relations**: This is setup for methods such as `WorkspaceScope.credential`, `WorkspaceScope.model_credential`, `WorkspaceScope.put_credential`, and `WorkspaceScope.rotate_credential`. Those methods all depend on the store being configured when stored workspace or member secrets are expected.


##### `ResolvedModelClient.complete`  (lines 124–125)

```
def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Starts a model completion request using a model client that has already been matched with its funding and payer information. It keeps the actual model call attached to the billing facts chosen earlier.

**Data flow**: It receives a model request. It passes that request directly to the underlying model client’s `complete` method. The output is an asynchronous stream of model events, such as generated text or usage reports.

**Call relations**: Code that resolves a model client can keep the returned client and its payer facts together in a `ResolvedModelClient`. When a completion is needed, this method delegates to the real client without changing those facts.


##### `BillableEvent.usage`  (lines 135–145)

```
def usage(self, model: str, usage: Usage, pricing: Pricing=CORE_PRICING, byok: bool=False) -> None
```

**Purpose**: Records one reported model usage item inside a billing block. Callers use it when a provider says tokens were used, so that usage can later be written to the workspace bill.

**Data flow**: It receives the model name, a usage record, pricing information, and whether the call used a bring-your-own-key credential. It appends those details to the event’s internal list. It does not write to the database immediately; it prepares the data for the surrounding `billable_event` block to commit later.

**Call relations**: This method is used inside `WorkspaceScope.billable_event`. The surrounding context manager creates the event, lets callers add usage through this method, and then writes all collected usage when the block exits.


##### `WorkspaceScope.credential`  (lines 161–183)

```
async def credential(self, slot: str, env: str | None=None, model: str | None=None) -> str
```

**Purpose**: Fetches the secret value for a named credential slot in the current workspace. It gives priority to stored workspace or member secrets and only falls back to platform environment variables when no stored value exists.

**Data flow**: It receives a slot name, an optional environment variable name, and optionally the model asking for the credential. It builds the order of possible credential slots with `_slot_order`, tries each one in the configured credential store, and ignores missing slots. If no stored value is found, it reads from deployment environment variables. It returns the secret string, or raises `CredentialSlotUnset` if nothing usable exists.

**Call relations**: Model and service code call this when they need a secret under the active workspace. It relies on `_slot_order` to decide whether a member-specific credential should be tried first, and on `deploy_env` for the platform fallback.

*Call graph*: calls 1 internal fn (_slot_order); 2 external calls (__init__, deploy_env).


##### `WorkspaceScope.model_credential`  (lines 185–205)

```
async def model_credential(self, slot: str, env: str | None, model: str) -> ModelCredential
```

**Purpose**: Fetches a model credential together with the answer to “who is paying for this call?” This prevents retries or billing code from accidentally treating the same attempt as funded by a different account.

**Data flow**: It receives a credential slot, an optional environment variable name, and the model name. It checks stored candidate slots in order. A plain stored key becomes key-funded, a valid unspent grant becomes plan-funded, and a spent grant is refreshed before use. If no stored credential exists, it reads the platform environment value and marks the platform as payer. It returns a `ModelCredential` containing the secret value, funding type, and payer name, or raises `CredentialSlotUnset` if no credential exists.

**Call relations**: Model client setup uses this instead of a plain credential lookup when billing matters. It calls `_slot_order` to decide member versus workspace lookup, `read_grant` to recognize connected-account grants, `_refreshed_credential` when a grant must be renewed, and `deploy_env` for platform fallback.

*Call graph*: calls 2 internal fn (_refreshed_credential, _slot_order); 4 external calls (__init__, __init__, read_grant, deploy_env).


##### `WorkspaceScope._refreshed_credential`  (lines 207–236)

```
async def _refreshed_credential(self, store: 'CredentialStore', candidate: str, slot: str, stored: str, grant: Grant) -> ModelCredential
```

**Purpose**: Refreshes a spent connected-account grant without letting several concurrent callers refresh the same token at once. This protects provider accounts from token-reuse problems and keeps callers from racing each other.

**Data flow**: It receives the credential store, the exact stored slot being used, the base slot name, the stored grant text, and the parsed grant. It repeatedly tries to claim a short refresh lease by rotating the stored value. The caller that wins asks the provider for a refreshed grant and stores it. Callers that do not win wait briefly, reread the slot, and use the refreshed value once it appears. It returns a plan-funded `ModelCredential`, may return a key-funded credential if the stored value changed into a plain key, or raises `GrantRefusedRefresh` if no refresh succeeds in time.

**Call relations**: Only `WorkspaceScope.model_credential` calls this, after it finds a spent grant. This function hands off to the grant refresh helper for the provider exchange and uses the credential store’s rotation operation to make the refresh safe across overlapping calls.

*Call graph*: calls 1 internal fn (__init__); called by 1 (model_credential); 6 external calls (__init__, sleep, model_copy, time, read_grant, refreshed).


##### `WorkspaceScope.member_routed_call`  (lines 238–241)

```
def member_routed_call(self, slot: str, model: str) -> bool
```

**Purpose**: Answers whether a specific model call should first try the speaking member’s own credential slot. This is useful when later code needs to know if a model request is being routed through a personal connected account.

**Data flow**: It receives a base credential slot and model name. It asks `_slot_order` for the lookup order. If that order contains more than one slot, the first is member-specific and the function returns `true`; otherwise it returns `false`.

**Call relations**: It is a small question built on top of `_slot_order`. Other code can use it before or around model calls to understand whether member routing is active for that slot and model.

*Call graph*: calls 1 internal fn (_slot_order).


##### `WorkspaceScope.member_payer`  (lines 243–249)

```
def member_payer(self, slot: str, model: str) -> str | None
```

**Purpose**: Identifies the member-specific payer slot for a model call, if the call is supposed to use a member’s own connected account. If no member routing applies, it returns nothing.

**Data flow**: It receives a base slot and model name. It asks `_slot_order` for the candidate lookup order. If a member slot is first, it returns that slot name; otherwise it returns `None`.

**Call relations**: This gives billing or routing code a preview of who would pay when member routing is active. Like `member_routed_call`, it depends on `_slot_order` for the actual routing decision.

*Call graph*: calls 1 internal fn (_slot_order).


##### `WorkspaceScope._slot_order`  (lines 251–262)

```
def _slot_order(self, slot: str, model: str | None) -> list[str]
```

**Purpose**: Decides which credential slots should be tried, and in what order, for a requested slot and model. Its main job is to prevent a member’s personal key from being used except for the exact models that were explicitly bound to that member.

**Data flow**: It receives a base slot and optional model name. It reads the current model-authority context set by `model_authority`. If the slot is not member-routable, no authority is bound, no model is named, or the model is not included, it returns only the workspace slot. If a member authority is bound and the model matches, it returns the member-specific slot first and the workspace slot second.

**Call relations**: Credential-related methods call this before reading stored secrets. It is the central rulekeeper behind `credential`, `model_credential`, `credential_is_stored`, `member_routed_call`, and `member_payer`.

*Call graph*: called by 5 (credential, credential_is_stored, member_payer, member_routed_call, model_credential); 1 external calls (member_slot).


##### `WorkspaceScope.member_holds_own_model_key`  (lines 264–267)

```
async def member_holds_own_model_key(self, authority: ExecutionAuthority) -> bool
```

**Purpose**: Checks whether the given authority belongs to a member who has connected at least one model provider account. It gives callers a simple yes-or-no answer.

**Data flow**: It receives an execution authority. It asks `member_model_provider` for the first connected provider. If one exists, it returns `true`; otherwise it returns `false`.

**Call relations**: This is a convenience wrapper over `member_model_provider`. Code that only needs to know whether a member has any usable personal model account can call this instead of inspecting provider lists.

*Call graph*: calls 1 internal fn (member_model_provider).


##### `WorkspaceScope.member_model_provider`  (lines 269–275)

```
async def member_model_provider(self, authority: ExecutionAuthority) -> str | None
```

**Purpose**: Returns the first model provider account connected by the member behind an authority. This tells the system which provider should be tried first for that member’s coding work.

**Data flow**: It receives an execution authority. It asks `member_model_providers` for all connected providers in preferred order. It returns the first provider name if any exist, or `None` if the member has not connected one.

**Call relations**: This function is called by `member_holds_own_model_key` and builds on `member_model_providers`. It hides the list-handling when callers only need the primary provider.

*Call graph*: calls 1 internal fn (member_model_providers); called by 1 (member_holds_own_model_key).


##### `WorkspaceScope.member_model_providers`  (lines 277–291)

```
async def member_model_providers(self, authority: ExecutionAuthority) -> tuple[str, ...]
```

**Purpose**: Finds all model providers for which a specific member has stored personal credentials in this workspace. The order matters because the first provider is preferred and later providers can be fallbacks.

**Data flow**: It receives an execution authority and extracts the member ID from it. If there is no credential store or no member ID, it returns an empty tuple. Otherwise it checks each member-routed credential slot, looks for a stored member-specific credential, and collects the provider names for the slots that exist. It returns those provider names as an ordered tuple.

**Call relations**: This is the detailed provider lookup used by `member_model_provider`. It uses `authority_member_id` to identify the member and `member_slot` to build the exact credential slot names to check.

*Call graph*: called by 1 (member_model_provider); 2 external calls (member_slot, authority_member_id).


##### `WorkspaceScope.credential_is_stored`  (lines 293–307)

```
async def credential_is_stored(self, slot: str, model: str | None=None) -> bool
```

**Purpose**: Answers whether a credential would come from stored workspace or member data instead of the platform’s environment defaults. This matters because stored provider keys are paid by their owner, not by the platform.

**Data flow**: It receives a slot and optionally a model name. If no credential store is configured, it returns `false`. Otherwise it tries the candidate slots from `_slot_order`; if any stored value exists, it returns `true`, and if all are missing it returns `false`.

**Call relations**: Billing and model-routing code can use this to decide whether a call used a bring-your-own-key style credential. It uses the same `_slot_order` as real credential lookup so the answer matches what `credential` or `model_credential` would read.

*Call graph*: calls 1 internal fn (_slot_order).


##### `WorkspaceScope.rotate_credential`  (lines 309–314)

```
async def rotate_credential(self, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Replaces an existing stored credential only if it still has the expected old value. This “compare before replacing” behavior helps avoid overwriting someone else’s update.

**Data flow**: It receives a slot name, the expected current stored value, and the new plaintext value. If no credential store exists, it returns `false`. Otherwise it asks the store to rotate the credential for this workspace and returns whether that rotation succeeded.

**Call relations**: This is used when code needs a safe update to an existing secret, such as grant refresh flows or owner-authorized key changes. It delegates the actual storage operation to the configured credential store.


##### `WorkspaceScope.put_credential`  (lines 316–320)

```
async def put_credential(self, slot: str, plaintext: str) -> None
```

**Purpose**: Stores an initial credential value for the current workspace. It is used when an authorized owner provides a new secret that should be saved.

**Data flow**: It receives a slot name and plaintext credential. If no credential store is configured, it raises a runtime error because there is nowhere safe to save the secret. Otherwise it writes the credential into the store under this workspace and slot.

**Call relations**: Setup or account-connection flows call this after permission checks have already happened elsewhere. The method keeps the write tied to the bound `WorkspaceScope` so the secret is saved under the right workspace.


##### `WorkspaceScope.billable_event`  (lines 323–335)

```
async def billable_event(self) -> AsyncIterator[BillableEvent]
```

**Purpose**: Creates a billing block that collects model usage and writes it to the current workspace when the block ends. It ensures provider-reported usage is booked even if later handling of the model output fails.

**Data flow**: It creates a fresh `BillableEvent` and yields it to the caller. During the block, callers add usage records to that event. When the block exits, it opens a workspace database transaction and writes each collected usage item with its model, pricing, and bring-your-own-key flag. It does not return a final value; its lasting effect is the billing records it writes.

**Call relations**: Model-calling code wraps paid work in this context manager and calls `BillableEvent.usage` as usage arrives. On exit, this function hands the collected records to `record_workspace_usage` inside `workspace_tx`.

*Call graph*: 3 external calls (__init__, workspace_tx, record_workspace_usage).


##### `ws`  (lines 339–347)

```
def ws(workspace_id: UUID) -> Iterator[WorkspaceScope]
```

**Purpose**: Binds a workspace ID as the active workspace for a block of code. This is the boundary that makes later credential reads, billing writes, and workspace-limited database work know which workspace they belong to.

**Data flow**: It receives a workspace ID. It stores that ID in task-local context, yields a `WorkspaceScope` for use inside the block, and restores the previous context when the block finishes. Nothing is permanently returned beyond the block; the context binding is temporary.

**Call relations**: Turn handlers and background jobs use this at their outer boundary. Inside the block, `ws_current` can recover the workspace scope, and database helpers that read `current_workspace` see the same workspace ID.

*Call graph*: 3 external calls (__init__, reset, set).


##### `ws_current`  (lines 350–356)

```
def ws_current() -> WorkspaceScope
```

**Purpose**: Returns the currently bound workspace scope. If code tries to do workspace-sensitive work without first entering `ws(...)`, it fails loudly instead of guessing.

**Data flow**: It reads the current workspace ID from task-local context. If one is present, it wraps it in a `WorkspaceScope` and returns it. If none is present, it raises `WorkspaceUnbound` with a message telling the caller to use `with ws(workspace_id):`.

**Call relations**: Any code that needs credentials, billing, or workspace-scoped behavior can call this instead of receiving a workspace argument. It relies on `ws` having already set the context at the turn or job boundary.

*Call graph*: 3 external calls (__init__, __init__, get).

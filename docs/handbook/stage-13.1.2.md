# Agent objects and prompt governance  `stage-13.1.2`

This stage is shared behind-the-scenes support for managing agents safely. An agent is the system’s editable record for a helper: what it is called, which AI model it uses, what instructions, or prompt, it follows, who is allowed to see or change it, and whether it is active or archived.

The agents file is the main “filing cabinet” for those records. It gives the rest of the system consistent ways to create an agent, read its details, update it, archive it when it should no longer be used, and restore it later. It also keeps these actions tied to permissions, so members only do what they are allowed to do.

The governance file adds a safety gate around prompt changes. Because a prompt can strongly affect how an agent behaves, changes are first saved as proposals. An approval step must happen before the new prompt is applied. The approval also checks that the current prompt is still the one the proposal was based on, preventing someone from approving an outdated change by accident.

## Files in this stage

### Agent Records
Defines the editable workspace agent object, including its model, prompt, visibility, lifecycle, and permission-safe operations.

### `core/src/ufo/runtime/kinds/agents.py`

`domain_logic` · `request handling`

An agent in this system is treated like a workspace object, much like a shared document with an owner, settings, and a name. This file is the rulebook for that object. It says what information an agent stores, such as its prompt, model, internet access, sandbox size, icon, visibility, and optional input and output shapes for spawned tasks.

The file also protects important boundaries. Any speaking member can create an agent, but only the owner or a workspace admin can edit or archive it. The main workspace agent is special: everyone can use it, its visibility cannot be changed away from the workspace, and it cannot be archived.

Deleting an agent does not erase its history. Instead, it archives the row, gives it a durable hidden name like “~archived-<id>”, and frees the old name for reuse. This is like taking a shop sign down while keeping the business records in storage. Conversations, grants, spend, and connected history remain attached to the same database row. A restore action can later make that same row live again under an available name.

The file also checks that requested models are known and that declared JSON schemas are valid, so bad settings are rejected before they can break future agent runs.

#### Function details

##### `_effective_model`  (lines 70–75)

```
def _effective_model(ctx: ToolContext, stored: str) -> str
```

**Purpose**: Reports the model an agent effectively runs on. If the stored model is the special “auto” choice, it shows the concrete model that the current run already resolved.

**Data flow**: It receives the current tool context and the model value stored in the agent row. If the stored value is “auto”, it reads the actual model from the running agent in the context; otherwise it keeps the stored value. It returns the model name that should be shown to a member.

**Call relations**: Agent summaries and status reports call this when they need to display a model. It keeps read operations honest without changing the saved setting, so reading and writing an “auto” agent does not accidentally freeze it to today’s concrete model.

*Call graph*: called by 2 (_status, _agent_summary).


##### `AgentSpec._declared_schema`  (lines 166–171)

```
def _declared_schema(cls, value: dict[str, JsonValue] | None, info: ValidationInfo) -> dict[str, JsonValue] | None
```

**Purpose**: Checks that an agent’s optional input or output schema is acceptable before it is stored. A schema here means a JSON-based shape that spawned agent calls must follow.

**Data flow**: It receives a proposed schema value and information about which field is being checked. If there is no schema, it leaves it alone. If there is a schema, it sends it to the shared schema checker, then returns the original schema if it passes.

**Call relations**: Pydantic, the validation library used for request models, calls this automatically while building an AgentSpec. It hands the detailed checking to check_declared_schema so the agent object follows the same contract rules as the rest of the turn system.

*Call graph*: 1 external calls (check_declared_schema).


##### `_known_model`  (lines 174–191)

```
def _known_model(ctx: ToolContext, model: str, reasoning: ReasoningEffort) -> None
```

**Purpose**: Rejects an agent setting that names a model this deployment does not know how to run, or a reasoning setting that the chosen model cannot support. This prevents saving an agent that would fail every future turn.

**Data flow**: It receives the current context, a model name, and a reasoning-effort setting. It resolves “auto” to the deployment’s configured automatic model, checks the model registry when one is available, and checks whether reasoning can be turned off for that model. It returns nothing if the settings are valid, or raises an error if they are not.

**Call relations**: Agent creation and agent editing both call this before writing to the database. It acts as the guard at the door, catching a bad model choice while the member can still receive a clear correction.

*Call graph*: called by 2 (_create, _mutate).


##### `_agent_summary`  (lines 194–199)

```
def _agent_summary(ctx: ToolContext, row: sa.Row) -> str
```

**Purpose**: Builds the short human-readable line shown for an agent in lists. It says whether the agent is archived, whether it is the main agent, what model it uses, and whether public internet is allowed.

**Data flow**: It receives the current context and a database row for an agent. It first turns the stored model into the display model using _effective_model. Then it returns a short summary string based on whether the row is archived or live.

**Call relations**: The listing code calls this while converting database rows into workspace object rows. It is the small formatter that turns raw database fields into a useful one-line description.

*Call graph*: calls 1 internal fn (_effective_model); called by 1 (_owned_rows).


##### `AgentObjects._admin_can_apply`  (lines 214–215)

```
def _admin_can_apply(self, old: AgentSpec, spec: AgentSpec) -> bool
```

**Purpose**: Allows workspace admins to apply any valid change to an agent. In this file, there are no extra agent-specific limits placed on admins beyond the normal validation rules.

**Data flow**: It receives the previous agent spec and the proposed new spec. It does not inspect them in detail and simply returns true, meaning an admin is allowed to proceed.

**Call relations**: This method plugs into the shared member-owned object permission framework. When that framework asks whether an admin may apply a change, this agent implementation answers yes.


##### `AgentObjects.list`  (lines 217–223)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists agents, with live agents shown by default. Archived agents only appear when the caller explicitly asks for them with an archived filter.

**Data flow**: It receives the tool context and a list query. If the query does not mention archived status, it copies the query and adds a filter for non-archived agents. It then passes the query to the shared listing behavior and returns the resulting page.

**Call relations**: This method is the public list path for agent objects. It lightly adjusts the caller’s query, then relies on the parent object-store machinery to do the permission-aware listing.

*Call graph*: 1 external calls (replace).


##### `AgentObjects._owned_rows`  (lines 225–257)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[ObjectOwner], ...]
```

**Purpose**: Fetches all agent rows in the current workspace and turns them into ownership-aware list entries. These entries tell the object system who can see or act on each agent.

**Data flow**: It reads the current workspace id, queries the agent table for key fields, and converts each database row into an OwnedRow. For each row it builds a summary, records the owner member id, marks it shared if it is workspace-visible or is the agent running the current turn, and includes list fields such as id and archive status.

**Call relations**: The shared listing and permission code uses this method as the agent-specific source of rows. It calls _agent_summary for the display text and wraps the result in the common OwnedRow and ObjectOwner shapes used by other object kinds.

*Call graph*: calls 1 internal fn (_agent_summary); 5 external calls (__init__, __init__, select, workspace_tx, ws_current).


##### `AgentObjects._detail`  (lines 259–292)

```
async def _detail(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> ObjectDetail[AgentSpec] | None
```

**Purpose**: Builds the full readable details for one agent. This is what a caller needs when they want the complete editable spec, not just a list summary.

**Data flow**: It receives a context, an agent name, and ownership information. It looks up the row by name; if none exists, it returns nothing. If found, it copies the row’s settings into an AgentSpec and adds metadata such as creation time, update time, and a link from non-main agents back to the main agent they are scoped under.

**Call relations**: Object detail reads call this after the shared object layer has decided the caller is allowed to look. It uses _row for the database lookup and returns an ObjectDetail that the wider object API can present.

*Call graph*: calls 1 internal fn (_row); 4 external calls (__init__, __init__, __init__, __init__).


##### `AgentObjects._status`  (lines 294–313)

```
async def _status(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns operational status fields for one agent, such as whether it is main, archived, provisioned, and which effective model it is using. This is more like a dashboard snapshot than an editable spec.

**Data flow**: It receives a context, an agent name, and ownership information. It looks up the row, returns nothing if missing, and otherwise builds a plain dictionary with status fields. For the model field, it converts “auto” into the current concrete model for display.

**Call relations**: Status reads call this when they need facts about the current state of an agent. It shares the same _row lookup as detail reads, and uses _effective_model so status output matches the model a member would actually see running.

*Call graph*: calls 2 internal fn (_row, _effective_model).


##### `AgentObjects._apply_owned`  (lines 315–326)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: AgentSpec, old: AgentSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: Chooses whether an apply operation should create a new agent or update an existing one. It is the fork in the road for the object_apply action.

**Data flow**: It receives the target name, the proposed spec, the old spec if one exists, and ownership information. If there is no old spec, it treats the apply as a creation and calls _create. If an old spec exists, it treats the apply as an edit and calls _mutate. It returns nothing after the chosen write path finishes.

**Call relations**: The shared member-owned object framework calls this after permission checks. This method then hands off to the agent-specific create or mutate logic, keeping the higher-level object action simple.

*Call graph*: calls 2 internal fn (_create, _mutate).


##### `AgentObjects._mutate`  (lines 328–406)

```
async def _mutate(self, ctx: ToolContext, name: str, spec: AgentSpec) -> None
```

**Purpose**: Updates an existing agent’s settings while preserving fields that were intentionally omitted. It also enforces special rules, such as the main agent always staying visible to the workspace.

**Data flow**: It receives the current context, the agent name, and the proposed spec. It loads the existing row, validates the requested model and reasoning settings, calculates each next value by combining submitted fields with existing fields, rejects an empty prompt, and skips the database write if nothing changed. If something did change, it updates the agent row and refreshes its updated time.

**Call relations**: _apply_owned calls this when an apply operation targets an existing agent. It relies on _row to read the current state, _known_model to reject bad runtime settings, and the workspace database transaction to save the final values.

*Call graph*: calls 2 internal fn (_row, _known_model); called by 1 (_apply_owned); 4 external calls (__init__, update, workspace_tx, ws_current).


##### `AgentObjects._create`  (lines 408–454)

```
async def _create(self, ctx: ToolContext, name: str, spec: AgentSpec) -> None
```

**Purpose**: Creates a brand-new non-main agent owned by the member who asked for it. It stores only the submitted configuration and does not copy grants, credentials, sources, or past data from anywhere else.

**Data flow**: It receives the current context, desired name, and agent spec. It requires a speaking member, requires a non-empty prompt, validates the model settings, reads currently used icons, chooses an icon if none was supplied, and inserts a new row with a fresh id. If another agent already has the name, it turns the database conflict into a clear name-taken error.

**Call relations**: _apply_owned calls this when an apply operation names no existing agent. It uses shared helpers for model validation, database access, workspace identity, automatic icon choice, and id generation.

*Call graph*: calls 1 internal fn (_known_model); called by 1 (_apply_owned); 7 external calls (__init__, insert, select, workspace_tx, ws_current, auto_agent_icon, uuid4).


##### `AgentObjects.delete`  (lines 456–466)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Starts the delete path for an agent, but blocks deletion of the main agent. In this system, deleting an ordinary agent means archiving it rather than erasing it.

**Data flow**: It receives the context, agent name, and an optional expected generation value used by the wider object system for safe updates. It looks up the row, raises an error if it is the main agent, and otherwise delegates to the parent delete flow. The actual archive write happens later in _delete_owned.

**Call relations**: External delete requests enter here for agent objects. This method adds the agent-specific main-agent protection, then hands back to the shared object framework so normal ownership checks and delete handling can continue.

*Call graph*: calls 1 internal fn (_row); 1 external calls (__init__).


##### `AgentObjects._delete_owned`  (lines 468–498)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: Archives an ordinary agent after ownership checks have passed. Archiving removes it from normal use and frees its name, while keeping its history attached to the same row.

**Data flow**: It receives the context, agent name, and owner information. It loads the row, rejects missing agents, rejects the main agent, and rejects an agent that is already archived. Then it updates the row: the live name becomes a durable archived name, the old name is saved as archived_name, archive time is set, and updated time is refreshed.

**Call relations**: The shared delete flow calls this after confirming the caller may delete the object. It uses _row for the lookup and writes through the workspace transaction so the archive state is stored atomically.

*Call graph*: calls 1 internal fn (_row); 5 external calls (__init__, __init__, update, workspace_tx, ws_current).


##### `AgentObjects._row`  (lines 500–541)

```
async def _row(self, name: str) -> sa.Row | None
```

**Purpose**: Fetches the complete database row for one agent name in the current workspace. Other methods use it as their common way to read an agent before deciding what to show or change.

**Data flow**: It receives an agent name. It reads the current workspace id, queries the agent table for all fields needed by this file, and also includes the workspace’s main-agent name through a subquery. It returns one row if found, or nothing if no matching agent exists.

**Call relations**: Detail, status, edit, delete, and archive operations all call this before doing their work. It is the file’s central database lookup helper, keeping those higher-level methods from each writing their own query.

*Call graph*: called by 5 (_delete_owned, _detail, _mutate, _status, delete); 3 external calls (select, workspace_tx, ws_current).


##### `RestoreApplication.restore`  (lines 556–613)

```
async def restore(self, ctx: ToolContext, args: RestoreApplicationInput) -> ToolResult
```

**Purpose**: Brings an archived agent back to life under a requested name. It preserves the same underlying row, so the agent’s conversations, scheduled tasks, grants, and connected accounts remain with it.

**Data flow**: It receives the tool context and input containing the new live name. It confirms the restore action is bound to an archived agent target, requires a speaking member, validates the requested name, extracts the archived agent id from the durable archived name, and reads the matching row. It then checks that the speaker is either the owner or an admin, prepares a success message, and if the row is still archived, updates it to clear archive fields and set the new name. If the name is already taken or the archived row cannot be found, it raises a clear error.

**Call relations**: This is the handler for the restore_application tool bound to archived agent objects. It uses the tool context for target and permission information, the database for lookup and update, and returns a ToolResult with text that the user can see after a successful restore.

*Call graph*: calls 1 internal fn (speaker_is_admin); 10 external calls (__init__, __init__, __init__, __init__, select, update, workspace_tx, validate_object_name, ws_current, UUID).


### Prompt Governance
Protects sensitive agent prompt edits by requiring approved proposals that verify the prompt has not changed since proposal creation.

### `core/src/ufo/runtime/kinds/governance.py`

`domain_logic` · `agent configuration change and approval`

This file is a safety gate for changing an agent's configuration, specifically its prompt. Instead of letting code overwrite an agent prompt directly, it uses a proposal-and-approval process. Think of it like editing a shared document by first leaving a suggested change, then applying it only if nobody has changed the original paragraph in the meantime.

The key protection is a digest, which is a short fingerprint made from the prompt text. When a proposal is opened, the file records the fingerprint of the prompt the proposer believed was current, plus the fingerprint of the new prompt. Later, when someone approves the proposal, the code checks the agent's current prompt again. If the current fingerprint no longer matches the original one, the proposal is rejected instead of overwriting someone else's newer change.

The `Governance` class is tied to one workspace, so proposals cannot accidentally affect agents in another workspace. It also records which extension or part of the system made the proposal. Database work is done inside a transaction, meaning each approval or rejection is treated as one all-or-nothing change. Without this file, prompt updates could be applied blindly, causing lost edits or unsafe configuration changes.

#### Function details

##### `prompt_digest`  (lines 16–17)

```
def prompt_digest(prompt: str) -> str
```

**Purpose**: This function creates a stable fingerprint for a prompt. It is used to tell whether two prompt texts are exactly the same without comparing the full text everywhere.

**Data flow**: It takes prompt text as input, turns it into bytes, runs it through SHA-256, which is a standard one-way fingerprinting method, and returns the fingerprint as a text string. It does not change anything outside itself.

**Call relations**: When a change is proposed, `Governance.propose_change` uses this to record the fingerprint of the new prompt. When a proposal is approved, `Governance.approve_proposal` uses it again to compare the agent's current prompt with the prompt version the proposal was based on.

*Call graph*: called by 2 (approve_proposal, propose_change); 1 external calls (sha256).


##### `Governance.propose_change`  (lines 28–55)

```
async def propose_change(self, change: AgentChange) -> ProposalRef
```

**Purpose**: This function opens a new proposal to change an agent's prompt. It does not change the agent yet; it only records the requested change and the prompt version it expects to replace.

**Data flow**: It receives an `AgentChange`, which includes the agent to change, the expected old prompt fingerprint, and the new prompt text. It first checks the database to make sure that agent exists inside this governance object's workspace. If the agent is found, it creates a new proposal row with a fresh proposal ID, the proposer name, the old and new prompt fingerprints, the new prompt body, and a pending status. It returns a `ProposalRef`, which is a small reference containing the new proposal ID.

**Call relations**: This is the first half of the governance flow. A caller uses it when it wants to request a prompt change safely. It relies on `prompt_digest` to fingerprint the proposed prompt, uses the workspace database transaction to make the insert safely, and produces a proposal ID that can later be passed to `Governance.approve_proposal`.

*Call graph*: calls 1 internal fn (prompt_digest); 5 external calls (__init__, insert, select, workspace_tx, uuid4).


##### `Governance.approve_proposal`  (lines 57–114)

```
async def approve_proposal(self, proposal_id: UUID) -> None
```

**Purpose**: This function tries to approve and apply a pending proposal. It only updates the agent if the agent's current prompt still matches the prompt version recorded when the proposal was created.

**Data flow**: It receives a proposal ID and looks up that proposal within the current workspace. If the proposal does not exist, or if it is no longer pending, it raises an error. It then reads and locks the agent's current prompt so another update cannot race in at the same moment. If the current prompt fingerprint does not match the proposal's original fingerprint, it marks the proposal as rejected and logs that rejection. If the fingerprints match, it writes the proposed prompt into the agent row, marks the proposal as approved, and logs the approval.

**Call relations**: This is the second half of the governance flow, used after `Governance.propose_change` has created a pending proposal. It calls `prompt_digest` to perform the safety comparison, uses database updates to either apply or reject the proposal, and sends approval or rejection events to the logging system so the outcome can be observed later.

*Call graph*: calls 1 internal fn (prompt_digest); 4 external calls (select, update, workspace_tx, log).

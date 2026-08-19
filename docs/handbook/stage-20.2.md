# Workspace, member, and visibility boundaries  `stage-20.2`

This stage is shared behind-the-scenes support for keeping work inside the right boundaries. It answers questions like: “Which workspace are we in?”, “Which agent is acting?”, “Who is a member?”, and “Who is allowed to see this?”

The workspace file sets the main boundary, like putting a case folder on the desk before any work starts. Secrets, database access, and billing all follow that same workspace. The agent scope file adds the current agent identity inside that workspace, so agent-owned features do not accidentally run as the wrong agent.

Membership and seat files manage people. Members defines who belongs to a workspace, who is an admin, and lets admins add someone in advance. Seats decides which members the agent may answer, grants or removes access, and prevents changes that would leave nobody able to manage seats.

Subjects and audience provide consistent visibility labels. Subjects covers simple content access, such as everyone or one member. Audience handles conversation disclosure rules and keeps private, shared, room, and external-room content separate. Scheduled task visibility then uses these rules to decide who may read each task’s prompt and description.

## Files in this stage

### Workspace and agent scope
Defines the active workspace boundary and the agent identity allowed to operate within it.

### `core/src/ufo/workspace.py`

`orchestration` · `cross-cutting during request, turn, and background job execution`

Many parts of the system need to know which workspace they are acting for. That matters because a workspace has its own stored credentials, its own billing record, and its own database access scope. This file provides one safe doorway for all of that.

The main idea is: code enters a block like `with ws(workspace_id):`, and everything inside that block can ask `ws_current()` for the active workspace. This is like putting a colored wristband on a visitor at an event; every booth can check the wristband instead of asking the visitor to repeat their identity each time. If code tries to fetch a credential or record usage without that wristband, it fails loudly with `WorkspaceUnbound`.

`WorkspaceScope` is the object returned for the active workspace. It can fetch credentials, check whether a workspace has its own stored key, store or rotate keys, and open a `billable_event()` block. A billable event collects provider usage during the block, then writes the charges to the workspace when the block ends. If a workspace used its own provider key, that can be marked so the platform does not bill for usage it did not pay for.

The file also has a one-time setup hook, `init_workspace_credentials`, which installs the credential store used later by workspace-bound calls.

#### Function details

##### `init_workspace_credentials`  (lines 29–33)

```
def init_workspace_credentials(store: CredentialStore | None) -> None
```

**Purpose**: Installs the credential store that workspace-scoped code will use to look up workspace-owned secrets. This is usually called once during startup so later credential lookups know where to search.

**Data flow**: It receives either a credential store object or `None`. It saves that value in this module's shared `_store` variable. After that, workspace credential calls either use the installed store first, or fall back to environment variables if no store or no workspace-specific value is available.

**Call relations**: Startup code calls this before normal work begins. Later, methods on `WorkspaceScope` read the stored `_store` value when they need to fetch, save, or rotate a workspace credential.


##### `BillableEvent.usage`  (lines 49–59)

```
def usage(self, model: str, usage: Usage, pricing: Pricing=CORE_PRICING, byok: bool=False) -> None
```

**Purpose**: Adds one reported model usage item to a billable event. Callers use it when an outside provider has reported usage that may need to be charged to the workspace.

**Data flow**: It receives the model name, the usage numbers, the pricing table to apply, and a flag saying whether the workspace used its own key. It stores those facts in the event's internal list. Nothing is written to the database yet; it is only queued for the end of the billing block.

**Call relations**: Code inside `WorkspaceScope.billable_event` calls this as provider responses arrive. When the billable block later exits, `WorkspaceScope.billable_event` reads the accumulated usage records and sends them to accounting.


##### `WorkspaceScope.credential`  (lines 68–82)

```
async def credential(self, slot: str, env: str | None=None) -> str
```

**Purpose**: Fetches the secret value for a named credential slot for this specific workspace. It first tries the workspace's own stored credential, then falls back to a platform-wide environment variable if no workspace value is set.

**Data flow**: It starts with a credential slot name, such as a provider key name, and optionally an environment variable name. If a credential store is configured, it asks that store for the value belonging to this workspace. If the store has no value for that slot, it reads the environment variable instead. It returns the secret string, or raises `CredentialSlotUnset` if neither source has a usable value.

**Call relations**: Code that needs an API key or other secret should reach it through `ws_current().credential(...)`, so the lookup is tied to the active workspace. It relies on the credential store installed by `init_workspace_credentials`, and it uses `CredentialSlotUnset` to make missing configuration fail clearly.

*Call graph*: 1 external calls (__init__).


##### `WorkspaceScope.credential_is_stored`  (lines 84–94)

```
async def credential_is_stored(self, slot: str) -> bool
```

**Purpose**: Answers whether this workspace has its own stored credential for a given slot. This matters because usage paid with a workspace-owned provider key may be treated differently for billing.

**Data flow**: It receives a slot name. If no credential store is configured, it returns `false`. Otherwise, it tries to read the workspace's stored value for that slot. If the value exists, it returns `true`; if the slot is unset, it returns `false`.

**Call relations**: Billing-aware provider code can call this near a credential lookup to decide whether the call used a workspace-owned key. That result can then be passed into `BillableEvent.usage` as the `byok` flag.


##### `WorkspaceScope.rotate_credential`  (lines 96–101)

```
async def rotate_credential(self, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Replaces an existing workspace credential only if it still matches the expected current value. This compare-and-swap pattern helps avoid overwriting a secret that someone else changed at the same time.

**Data flow**: It receives a slot name, the expected existing secret, and the new plaintext secret. If no credential store is configured, it returns `false`. Otherwise, it asks the store to rotate the credential for this workspace and returns whether that rotation succeeded.

**Call relations**: Credential management flows call this when an owner wants to change a stored workspace key safely. It delegates the actual storage operation to the credential store installed by `init_workspace_credentials`.


##### `WorkspaceScope.put_credential`  (lines 103–107)

```
async def put_credential(self, slot: str, plaintext: str) -> None
```

**Purpose**: Stores a new workspace-owned credential for a named slot. It is meant for authorized setup flows where a workspace owner is adding a key.

**Data flow**: It receives a slot name and the plaintext secret to store. If no credential store is configured, it raises an error because there is nowhere safe to save it. Otherwise, it asks the store to save the credential under this workspace's ID.

**Call relations**: Credential setup code calls this through the current `WorkspaceScope`. The method depends on the credential store configured by `init_workspace_credentials`, keeping the write tied to the bound workspace.


##### `WorkspaceScope.billable_event`  (lines 110–122)

```
async def billable_event(self) -> AsyncIterator[BillableEvent]
```

**Purpose**: Creates a billing block for work done by this workspace. Any provider usage reported inside the block is written to the workspace's usage ledger when the block finishes, even if later processing of the provider output fails.

**Data flow**: It creates an empty `BillableEvent` and yields it to the caller. The caller adds usage records to that event during the block. When the block exits, the method opens a workspace database transaction and writes each collected usage item through the accounting layer. If no usage was recorded, it writes nothing.

**Call relations**: Callers wrap billable provider calls in this context manager and use `BillableEvent.usage` inside it. On exit, this method uses `workspace_tx` to get a workspace-scoped database connection, then hands each usage item to `record_workspace_usage` so accounting can persist the charge.

*Call graph*: 3 external calls (__init__, record_workspace_usage, workspace_tx).


##### `ws`  (lines 126–134)

```
def ws(workspace_id: UUID) -> Iterator[WorkspaceScope]
```

**Purpose**: Binds a workspace ID as the active workspace for a block of code. This lets all code inside the block find the same workspace without passing the ID through every function call.

**Data flow**: It receives a workspace ID. At the start of the block, it stores that ID in the current workspace context and yields a `WorkspaceScope` for it. When the block ends, it restores the previous context so the workspace does not leak into unrelated work.

**Call relations**: Turn handlers, job runners, or request boundaries use this at the outer edge of workspace-specific work. Inside the block, `ws_current` can recover the active workspace, and database code using the same context can stay scoped to that workspace.

*Call graph*: 3 external calls (__init__, reset, set).


##### `ws_current`  (lines 137–143)

```
def ws_current() -> WorkspaceScope
```

**Purpose**: Returns the currently bound workspace scope. If no workspace has been bound, it raises `WorkspaceUnbound` so code does not accidentally read secrets, access data, or bill usage without a workspace.

**Data flow**: It reads the current workspace ID from the shared context. If an ID is present, it returns a new `WorkspaceScope` for that ID. If no ID is present, it raises an error explaining that the caller must wrap the work in `with ws(workspace_id):`.

**Call relations**: Workspace-scoped code calls this whenever it needs credentials, billing, or the workspace identity. It depends on `ws` having already set the context at the boundary of the request, turn, or job.

*Call graph*: 3 external calls (__init__, __init__, get).


### `core/src/ufo/agent_scope.py`

`domain_logic` · `cross-cutting`

Some parts of the system need to know, “Which agent is doing this right now?” This file provides that answer safely. It uses a context variable, which is like a temporary label attached to the current flow of work, so code deeper down can read the active agent without every caller handing it over manually.

The main idea is an agent scope: a pair of IDs saying “this agent, inside this workspace.” The `agent` context manager creates that scope for a block of code. While the block is running, other code can call `agent_current()` to find the active agent. When the block ends, the old state is restored, like taking off a visitor badge when leaving a secure area.

The safety checks are important. If code tries to use an agent-scoped capability without first entering an agent scope, it raises a clear `AgentUnbound` error. If code tries to switch to a different agent while one is already bound, it fails instead of silently changing identity. And if the current workspace no longer matches the workspace captured when the agent was bound, it raises an error. Without this file, agent-owned operations could run with no agent, the wrong agent, or in the wrong workspace, which could cause serious ownership and permission mistakes.

#### Function details

##### `agent`  (lines 28–38)

```
def agent(agent_id: UUID) -> Iterator[AgentScope]
```

**Purpose**: This function opens a temporary agent boundary for a block of work. Someone uses it when code needs to run as a specific agent inside the current workspace.

**Data flow**: It receives an agent ID and reads the current workspace ID from the workspace context. It combines those into an `AgentScope`, checks whether another different agent is already active, and then stores the new scope in the current execution context. The code inside the `with agent(...):` block receives that scope; when the block finishes, the previous agent setting is restored.

**Call relations**: This is the entry point for binding an agent identity. It asks the workspace layer for the current workspace, builds the agent-and-workspace pairing, and makes it available so later calls to `agent_current` can retrieve it. It also guards against changing agents in the middle of an already-bound agent block.

*Call graph*: 2 external calls (__init__, ws_current).


##### `agent_current`  (lines 41–48)

```
def agent_current() -> AgentScope
```

**Purpose**: This function returns the agent scope that is currently active. It is used by code that needs to know which agent owns or is allowed to perform an operation.

**Data flow**: It reads the agent scope from the context variable. If there is no active scope, it raises `AgentUnbound` with a message telling the caller to wrap the work in `with agent(agent_id):`. If there is a scope, it also checks that the current workspace still matches the workspace stored in that scope. If everything matches, it returns the `AgentScope`.

**Call relations**: This function is the reader side of the agent boundary created by `agent`. It depends on the workspace layer to confirm that the surrounding workspace has not changed. If the agent was never bound, or if the workspace no longer lines up, it stops the flow loudly instead of returning an unsafe identity.

*Call graph*: 2 external calls (__init__, ws_current).


### Membership and seats
Manages workspace members, admin status, invitations, seat grants, and safeguards around seat control.

### `core/src/ufo/members.py`

`domain_logic` · `request handling`

A workspace needs a reliable roster: a list of people who can use it, who can administer it, and whose access is currently active. This file is that roster’s rulebook. Without it, the system would not have one clear place to decide who may see membership information, who may change roles, or how an admin can add someone who has not arrived yet.

The file treats each member as an object with two editable facts: `admin`, meaning the person can administer the workspace, and `seated`, meaning they are allowed through admission when they try to speak. “Unseating” is how access is removed without deleting the membership record.

Visibility is carefully limited. From the main agent, members can see the workspace roster. From a child agent, or in a channel shared with another organization, a person can only see their own row. Think of it like an office directory: visible inside the main office, but not handed out in a shared meeting room.

Changes are stricter than reads. Only a signed-in workspace admin, using the main agent, can change a member’s admin status or seat. The code also protects the workspace from losing its last admin or last seated admin. Members cannot be deleted through this object system. Separately, the `add_member` tool lets an admin add someone by email, even outside the workspace’s usual email domain, after checking that the email is valid and not already present.

#### Function details

##### `MemberObjects.list`  (lines 65–69)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows a page of member rows that the current speaker is allowed to see. It is used when the object system asks for the `member` list.

**Data flow**: It receives the current tool context and a paging/filtering query. It first gathers only the rows visible to this speaker, turns each database row into a simple display row, and then wraps those rows into an object page that respects the query.

**Call relations**: This is the public list entry for member objects. It relies on `MemberObjects._visible_rows` to enforce the privacy rule, then uses `_row` to turn each allowed member into a readable summary before handing the rows to the shared object paging helper.

*Call graph*: calls 2 internal fn (_visible_rows, _row); 1 external calls (object_page).


##### `MemberObjects.member_page`  (lines 71–87)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows a page of members for portal-style reads, where the signed-in member is reading outside a normal agent turn. It applies the same main-agent-versus-child-agent visibility rule as the normal list view.

**Data flow**: It receives the signed-in member’s id, whether the reader is an admin, and the page query. It asks which member rows this reader may see for the currently named agent, converts those rows into display summaries, and returns a paged result.

**Call relations**: This function serves the portal path rather than the tool-turn path. It calls `MemberObjects._member_rows` to decide whether the reader gets the full roster or only their own row, then formats rows with `_row` and passes them into the common page builder.

*Call graph*: calls 2 internal fn (_member_rows, _row); 1 external calls (object_page).


##### `MemberObjects.get`  (lines 89–91)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[MemberSpec] | None
```

**Purpose**: Fetches the editable detail for one visible member object. If the named member is hidden or does not exist from this speaker’s point of view, it returns nothing.

**Data flow**: It receives the current tool context and a member object name, which is expected to be the member id as text. It searches among the rows visible to the speaker; if it finds a match, it turns that row into a detail object containing the admin and seated settings plus timestamps.

**Call relations**: This is the detail companion to `MemberObjects.list`. It depends on `MemberObjects._visible_row` for both lookup and access control, and uses `_detail` only after a row has been proven visible.

*Call graph*: calls 2 internal fn (_visible_row, _detail).


##### `MemberObjects.member_detail`  (lines 93–108)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[MemberSpec] | None
```

**Purpose**: Fetches one member’s row and detail for portal reads. It follows the same rule as portal listing: the main agent can expose the roster, while other agents expose only the reader’s own membership.

**Data flow**: It receives the requested member name and the signed-in member’s id. It loads the member rows visible to that signed-in member, looks for the requested id, and if found returns both a short row summary and the editable detail.

**Call relations**: This is the portal version of `get`. It calls `MemberObjects._member_rows` to reuse the same visibility decision as `member_page`, then combines `_row` and `_detail` into a `MemberObject` response.

*Call graph*: calls 3 internal fn (_member_rows, _detail, _row); 1 external calls (__init__).


##### `MemberObjects.status`  (lines 110–125)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns a small status snapshot for one visible member, currently the member’s email and whether they are seated. This is useful when another part of the object system needs quick state rather than the full editable detail.

**Data flow**: It receives the current context, the member name, and an expected generation value that this implementation does not use. It looks up the visible row; if present, it returns a small dictionary with the email and seated flag, otherwise it returns nothing.

**Call relations**: This follows the same access path as `get` by calling `MemberObjects._visible_row`. Unlike `get`, it does not call `_detail`; it creates a minimal status dictionary directly from the visible database row.

*Call graph*: calls 1 internal fn (_visible_row).


##### `MemberObjects.apply`  (lines 127–206)

```
async def apply(self, ctx: ToolContext, name: str, spec: MemberSpec, old: MemberSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Changes an existing member’s admin role and/or seat. It is the guarded doorway for membership edits, and it refuses changes unless a real workspace admin is speaking through the main agent.

**Data flow**: It receives the current context, a member id as text, the desired `MemberSpec`, and the previous spec if any. It checks that the speaker is present, is using the main agent, and is actually an admin in the database; then it locks the workspace row so competing edits do not race. It finds the target member, grants or revokes their seat if needed, checks that the workspace will still have an admin and a seated admin, and finally updates the admin flag when it changed.

**Call relations**: This is called when the object system applies a new spec to a `member` object. It hands seat changes to `Seats.grant` or `Seats.revoke`, uses `member_is_admin` to verify the speaker’s authority, raises object-system errors for unsupported creation or missing members, and writes the final role change through SQL.

*Call graph*: calls 1 internal fn (agent_is_main); 10 external calls (__init__, __init__, __init__, __init__, select, update, workspace_tx, member_is_admin, ws_current, UUID).


##### `MemberObjects.delete`  (lines 208–215)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to delete member objects. The workspace keeps membership records and uses seating to remove access instead.

**Data flow**: It receives the same kind of delete request other objects support, including context, name, and an expected generation value. It does not read or change data; it always raises an error explaining that members cannot be deleted through objects.

**Call relations**: This is the delete endpoint required by the object store shape, but its whole job is to stop the flow. Callers that try to delete a member are redirected, by the raised `VerbNotSupported` error, toward the intended access-control model.

*Call graph*: 1 external calls (__init__).


##### `MemberObjects._visible_rows`  (lines 217–222)

```
async def _visible_rows(self, ctx: ToolContext) -> tuple[sa.Row, ...]
```

**Purpose**: Decides which member rows the current speaker may see during a normal tool turn. It encodes the file’s central privacy rule.

**Data flow**: It receives the tool context. If nobody is signed in, it returns an empty set. If the conversation is with a foreign audience, it returns only the speaker’s own row. Otherwise it asks whether the current agent is the main agent; main-agent reads get the whole roster, while other agent reads get only the speaker’s row.

**Call relations**: This helper feeds both `MemberObjects.list` and `MemberObjects._visible_row`. After making the visibility decision from the context, it delegates the actual database query to `MemberObjects._roster`.

*Call graph*: calls 2 internal fn (_roster, agent_is_main); called by 2 (_visible_row, list).


##### `MemberObjects._visible_row`  (lines 224–228)

```
async def _visible_row(self, ctx: ToolContext, name: str) -> sa.Row | None
```

**Purpose**: Finds one named member among the rows the speaker is allowed to see. It combines lookup with privacy, so hidden members look the same as missing members to the caller.

**Data flow**: It receives the context and a member name. It first gets the allowed rows from `MemberObjects._visible_rows`, then compares each row’s id to the requested name and returns the matching row or nothing.

**Call relations**: This helper is used by `MemberObjects.get` and `MemberObjects.status`. Those callers do not query the database directly; they rely on this function so every single-member read follows the same visibility rule as listing.

*Call graph*: calls 1 internal fn (_visible_rows); called by 2 (get, status).


##### `MemberObjects._member_rows`  (lines 230–242)

```
async def _member_rows(self, member_id: UUID) -> tuple[sa.Row, ...]
```

**Purpose**: Decides which member rows a signed-in member may see in portal reads, where there may not be a normal tool context. It checks whether the currently selected agent is the main agent and then applies the same roster rule.

**Data flow**: It receives the signed-in member id. It opens a workspace transaction, reads whether the current agent is marked as the main agent, then asks `_roster` for either the whole workspace roster or only that member’s row.

**Call relations**: This helper supports the portal-facing `member_page` and `member_detail` functions. It uses the current workspace and agent scopes to recreate the visibility decision that `MemberObjects._visible_rows` makes from a tool context.

*Call graph*: calls 1 internal fn (_roster); called by 2 (member_detail, member_page); 4 external calls (select, agent_current, workspace_tx, ws_current).


##### `MemberObjects._roster`  (lines 244–264)

```
async def _roster(self, member_id: UUID, *, whole: bool) -> tuple[sa.Row, ...]
```

**Purpose**: Reads member rows from the database, either the full workspace roster or one member’s own row. It is the shared database query behind both normal and portal membership views.

**Data flow**: It receives a member id and a `whole` flag. It builds a database query for the current workspace, selecting email, id, admin flag, seating timestamp, and timestamps, ordered by email. If `whole` is false, it narrows the query to the given member id; then it runs the query and returns the rows.

**Call relations**: This is the low-level reader used by `MemberObjects._visible_rows` and `MemberObjects._member_rows`. Those callers decide what is allowed; `_roster` performs the actual fetch from the workspace database.

*Call graph*: called by 2 (_member_rows, _visible_rows); 3 external calls (select, workspace_tx, ws_current).


##### `_row`  (lines 267–275)

```
def _row(row: sa.Row) -> ObjectRow
```

**Purpose**: Turns a member database row into a short object-list row that a person can read at a glance. It names the object by member id and summarizes email, role, and seat state.

**Data flow**: It receives a database row with member fields. It builds an `ObjectRow` whose name is the member id as text and whose summary says the email, whether the member is an admin or regular member, and whether they are seated or unseated.

**Call relations**: This formatter is used anywhere the code needs a list-style member row: `MemberObjects.list`, `MemberObjects.member_page`, and `MemberObjects.member_detail`. It keeps the wording of member summaries consistent across those views.

*Call graph*: called by 3 (list, member_detail, member_page); 1 external calls (__init__).


##### `_detail`  (lines 278–283)

```
def _detail(row: sa.Row) -> ObjectDetail[MemberSpec]
```

**Purpose**: Turns a member database row into the detailed object form used for reading or editing. The detail contains the editable spec plus creation and update times.

**Data flow**: It receives a database row. It converts the row’s admin flag and seating timestamp into a `MemberSpec`, attaches the row’s timestamps, and returns an `ObjectDetail`.

**Call relations**: This formatter is called by `MemberObjects.get` and `MemberObjects.member_detail` after those functions have already checked visibility. It is the bridge between raw database columns and the object system’s editable member shape.

*Call graph*: called by 2 (get, member_detail); 2 external calls (__init__, __init__).


##### `AddMember.add`  (lines 309–332)

```
async def add(self, ctx: ToolContext, args: AddMemberInput) -> ToolResult
```

**Purpose**: Adds a new workspace member by email before that person has contacted the agent. It lets an admin staff the workspace with colleagues, contractors, or advisors, even when their email domain is different.

**Data flow**: It receives the tool context and the requested email, admin flag, and human description. It checks that a signed-in member is using the main agent, rejects shared foreign-audience channels, normalizes and validates the email, locks the workspace, verifies the speaker is an admin, checks that the email is not already a member, creates the member, and returns a short success message.

**Call relations**: This is the handler registered for the `add_member` tool. During the add flow it calls `AddMember._absent` to prevent duplicates, relies on `member_is_admin` for authority, delegates creation to `create_member`, and wraps the final message in tool-result content.

*Call graph*: calls 2 internal fn (_absent, agent_is_main); 9 external calls (__init__, __init__, __init__, select, workspace_tx, create_member, email_domain, member_is_admin, ws_current).


##### `AddMember._absent`  (lines 334–347)

```
async def _absent(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Checks that an email address is not already a member of the current workspace. It prevents the add tool from silently duplicating people.

**Data flow**: It receives an open database connection and a normalized email address. It searches the current workspace for a member whose email matches case-insensitively. If none is found, it returns normally; if one is found, it raises a clear error telling the caller to edit the existing member instead.

**Call relations**: This helper is called inside `AddMember.add` after the caller has passed the admin and email checks but before a new member is created. It keeps duplicate detection close to the database transaction used for the actual add.

*Call graph*: called by 1 (add); 3 external calls (execute, select, ws_current).


### `core/src/ufo/seats.py`

`domain_logic` · `cross-cutting access checks, member creation, and admin seat changes`

A “seat” is this project’s word for a member’s permission to be answered by the agent. Members are not deleted when access is removed; instead, their seat is revoked. That matters because a member row is also part of the system’s memory and identity history. Without this file, different parts of the app could disagree about whether a person may speak, or an admin could accidentally remove the last person able to restore access.

The file provides one shared set of rules for admission, running turns, resume checks, and admin tools. The key object is `Seats`, which is tied to one workspace. It can check whether one member is seated, check a group of members at once, show a full seat list, grant a seat, or revoke one. Revoking has an important guardrail: the last seated admin cannot be unseated, because seat management happens through chat and someone must remain able to fix things.

The file also owns member creation. New members must have a clean `local@domain` email address, and emails are lowercased so one person cannot accidentally become two records. Database locks and conflict-safe inserts are used so two simultaneous joins do not create duplicates. Think of this file as the workspace doorman plus the guest ledger: it records who belongs, who currently has a pass, and makes every entrance use the same rules.

#### Function details

##### `gate_member`  (lines 37–48)

```
def gate_member(speaker_member_id: UUID | None, on_behalf_of_member_id: UUID | None) -> UUID | None
```

**Purpose**: Chooses which member a turn should be checked against for seat access. It uses the direct speaker when there is one, otherwise it uses the member the turn is acting for.

**Data flow**: It receives a possible speaker member ID and a possible “on behalf of” member ID. If the speaker ID exists, that becomes the gate; if not, the fallback member ID is used. It returns the chosen ID, or nothing if the turn is not tied to any member.

**Call relations**: This small rule keeps admission, resume, dispatch, and per-round checks from inventing different meanings for “whose seat matters.” Other parts of the system can call it before asking `Seats` whether that member is allowed.


##### `SeatSnapshot.seated`  (lines 72–73)

```
def seated(self) -> int
```

**Purpose**: Counts how many members in a snapshot currently hold seats. This is useful for reporting the current seating state in a simple number.

**Data flow**: It reads the snapshot’s stored member entries. It counts entries whose `seated` flag is true. It returns that count and changes nothing.

**Call relations**: A snapshot is built by `Seats.snapshot`; this property gives callers a quick summary without making another database query.


##### `Seats.admits`  (lines 84–98)

```
async def admits(self, connection: AsyncConnection, member_id: UUID) -> bool
```

**Purpose**: Answers the basic access question: may this member in this workspace be answered right now? It returns true only if the member exists in the workspace and currently has a seat.

**Data flow**: It takes a database connection and a member ID. It reads that member’s `seated_at` value from the workspace’s member table. It returns true if the row exists and the seat timestamp is present; otherwise it returns false.

**Call relations**: Admission checks, running turn checks, and resume checks can all call this same method, so an admin revocation takes effect everywhere the next time the system checks before answering.

*Call graph*: 2 external calls (execute, select).


##### `Seats.all_seated`  (lines 100–120)

```
async def all_seated(self, connection: AsyncConnection, member_ids: Collection[UUID]) -> bool
```

**Purpose**: Checks whether every member in a given group still has a seat. It is designed for turns that may involve more than one member.

**Data flow**: It receives a database connection and a collection of member IDs. If the collection is empty, it immediately returns true. Otherwise it counts how many of those IDs are seated members of this workspace and compares that count with the requested set size. The result is true only when every requested member is seated.

**Call relations**: Parts of the engine that inspect groups of members can use this instead of checking one by one. It asks the database once for the whole set, which keeps per-round and resume enforcement consistent and efficient.

*Call graph*: 2 external calls (execute, select).


##### `Seats.snapshot`  (lines 122–145)

```
async def snapshot(self, connection: AsyncConnection) -> SeatSnapshot
```

**Purpose**: Builds a full read-only picture of the workspace’s members and their seat/admin status. This is useful for showing admins who exists and who currently has access.

**Data flow**: It takes a database connection and reads all member rows for the workspace in creation order. For each row it turns the database fields into a `SeatEntry` with ID, email, seated flag, and admin flag. It returns a `SeatSnapshot` containing those entries.

**Call relations**: Admin tools or reports can call this when they need a human-readable list of the seating state. It creates the data that `SeatSnapshot.seated` can summarize.

*Call graph*: 4 external calls (__init__, __init__, execute, select).


##### `Seats.grant`  (lines 147–157)

```
async def grant(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Restores or gives a seat to an existing workspace member by email. If the member is already seated, it safely does nothing.

**Data flow**: It receives a database connection and an email address. It looks up the matching member in this workspace. If that member already has a seat timestamp, it returns without changing anything. If not, it updates the member row with the current time as `seated_at` and refreshes `updated_at`.

**Call relations**: Admin-facing tools use this when restoring access. It relies on `_member_by_email` to make sure the email belongs to a real member of this workspace before changing the database.

*Call graph*: calls 1 internal fn (_member_by_email); 2 external calls (execute, update).


##### `Seats.revoke`  (lines 159–182)

```
async def revoke(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Removes a member’s seat by email, which stops the agent from answering them. It refuses to remove the last seated admin, because then no one could restore seats through chat.

**Data flow**: It receives a database connection and an email. First it locks the workspace row so two revocations cannot race past each other. It looks up the member. If they are already unseated, it does nothing. If they are the only seated admin, it raises an error. Otherwise it clears `seated_at` and updates the row timestamp.

**Call relations**: Admin tools call this to revoke access. It uses `_member_by_email` to find the member and `_seated_admin_count` to enforce the last-admin safety rule before it writes the change.

*Call graph*: calls 2 internal fn (_member_by_email, _seated_admin_count); 4 external calls (__init__, execute, select, update).


##### `Seats._member_by_email`  (lines 184–201)

```
async def _member_by_email(self, connection: AsyncConnection, email: str) -> tuple[UUID, datetime | None, bool]
```

**Purpose**: Finds one workspace member by email and returns the facts needed for seat changes. It raises a clear error if the email is not a member of this workspace.

**Data flow**: It receives a database connection and an email. It trims and lowercases the email for comparison, then reads the matching member ID, seat timestamp, and admin flag. It returns those three values, or raises `UnknownMember` if no row matches.

**Call relations**: `Seats.grant` and `Seats.revoke` both call this before making changes. That keeps email lookup rules identical for granting and revoking.

*Call graph*: called by 2 (grant, revoke); 3 external calls (__init__, execute, select).


##### `Seats._seated_admin_count`  (lines 203–212)

```
async def _seated_admin_count(self, connection: AsyncConnection) -> int
```

**Purpose**: Counts how many admins in the workspace currently still have seats. This supports the rule that at least one seated admin must remain.

**Data flow**: It receives a database connection. It asks the database for the number of member rows in this workspace that are both admins and seated. It returns that integer count.

**Call relations**: `Seats.revoke` calls this only when the target member is an admin. The count decides whether revocation is allowed or must be blocked.

*Call graph*: called by 1 (revoke); 2 external calls (execute, select).


##### `email_domain`  (lines 215–228)

```
def email_domain(email: str) -> str
```

**Purpose**: Extracts the domain part of a clean email address, such as `example.com` from `person@example.com`. It returns an empty string for malformed addresses so bad values cannot pass domain-based checks.

**Data flow**: It receives an email string. It trims spaces, lowercases it, splits it around `@`, and rejects missing parts, extra `@` signs, or any whitespace. It returns the domain when the address has exactly the expected shape; otherwise it returns an empty string.

**Call relations**: `create_member` uses this as the shared validation gate before any member row is created. `workspace_domain` also uses it to derive the workspace’s domain in the same way.

*Call graph*: called by 2 (create_member, workspace_domain).


##### `workspace_domain`  (lines 231–248)

```
async def workspace_domain(connection: AsyncConnection, workspace_id: UUID) -> str | None
```

**Purpose**: Finds the email domain that represents a workspace, based on its first member’s email address. This gives join and sign-in logic one shared idea of the workspace’s domain.

**Data flow**: It receives a database connection and workspace ID. It reads the earliest member email for that workspace. If there is no member, it returns nothing. Otherwise it passes that email to `email_domain` and returns the resulting domain, or nothing if somehow no valid domain is found.

**Call relations**: Other admission or joining paths can use this instead of each deriving a workspace domain separately. It depends on `email_domain` so its idea of a valid address matches member creation.

*Call graph*: calls 1 internal fn (email_domain); 2 external calls (execute, select).


##### `member_by_email`  (lines 251–267)

```
async def member_by_email(connection: AsyncConnection, workspace_id: UUID, email: str) -> UUID | None
```

**Purpose**: Looks up whether a given email already belongs to a member of a specific workspace. It only reads; it never creates a member.

**Data flow**: It receives a database connection, workspace ID, and email. It normalizes the email for comparison and searches only inside that workspace. It returns the member ID if found, or nothing if the address is not a member there.

**Call relations**: Routes or sign-in flows can call this after an address has been verified. The workspace filter is part of the query, which prevents accidentally treating a member of another workspace as local.

*Call graph*: 2 external calls (execute, select).


##### `member_is_admin`  (lines 270–280)

```
async def member_is_admin(connection: AsyncConnection, workspace_id: UUID, member_id: UUID) -> bool
```

**Purpose**: Checks whether a particular member is an admin in a particular workspace. It returns false if the member is missing or belongs elsewhere.

**Data flow**: It receives a database connection, workspace ID, and member ID. It reads the `is_admin` flag for that exact member within that exact workspace. It returns the flag as a true-or-false value.

**Call relations**: Permission checks can use this before allowing admin-only actions. The function keeps the member and workspace tied together in one database query.

*Call graph*: 2 external calls (execute, select).


##### `create_member`  (lines 283–345)

```
async def create_member(connection: AsyncConnection, workspace_id: UUID, email: str, *, is_admin: bool=False) -> UUID
```

**Purpose**: Creates a workspace member in the one approved way used by all joining and onboarding paths. New members start seated, and duplicate creation races collapse into the already-created member.

**Data flow**: It receives a database connection, workspace ID, email, and optional admin flag. It first validates the email shape with `email_domain`, lowercases the email, and locks the workspace row to keep concurrent creations orderly. It then tries to insert a new member with a fresh ID. If the insert succeeds, it returns the new ID; if another process already created the same workspace/email pair, it reads and returns that existing ID.

**Call relations**: Onboarding, teammate joins, and future member-creation surfaces should all call this instead of writing member rows directly. It uses `email_domain` so every path follows the same email rule, and it uses conflict-safe database insertion so simultaneous joins do not create duplicates.

*Call graph*: calls 1 internal fn (email_domain); 3 external calls (execute, select, uuid4).


##### `member_workspaces`  (lines 348–356)

```
def member_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a reusable candidate list for jobs that should run for every workspace with at least one member. This lets extensions ask for the right workspaces without knowing the member table details.

**Data flow**: It defines a small query that selects distinct workspace IDs from the member table. It wraps that query with `owner_candidates`, producing a `WorkspaceCandidates` object that another job can use. It does not run the query itself.

**Call relations**: Extension jobs can declare these candidates when they need seat or member reporting. Inside it, `with_a_member` supplies the actual database query, and `owner_candidates` turns that query into the project’s standard candidate format.

*Call graph*: 1 external calls (owner_candidates).


##### `member_workspaces.with_a_member`  (lines 353–354)

```
def with_a_member() -> sa.Select[tuple[UUID]]
```

**Purpose**: Creates the database query used to find workspaces that have members. It is intentionally simple: any member is enough to include the workspace.

**Data flow**: It takes no direct input. It builds a SQL query that selects distinct workspace IDs from the member table. The query object is returned for another layer to execute later.

**Call relations**: This helper lives inside `member_workspaces` because it is only needed there. `member_workspaces` hands it to `owner_candidates`, which uses it as the source for workspace candidates.

*Call graph*: 1 external calls (select).


### Disclosure labels
Defines and validates audience and subject labels that describe who may read conversations, turns, or content.

### `core/src/ufo/subjects.py`

`data_model` · `cross-cutting`

This file is about visibility: who is allowed to read something. The system represents a reading audience with a simple string called a “subject.” There is one special subject, `shared`, which means the content is readable by every member of the workspace. There is also a member-specific form, such as `member:<uuid>`, which points to one particular workspace member.

The file works like a small label maker. If another part of the program has a member’s unique ID, it can ask `member_subject` to turn that ID into the exact subject string the rest of the system expects. If another part of the program has a subject string and needs to know whether it means “everyone,” it can ask `subject_shared`.

The important detail is that not every audience-like thing counts as `shared`. The comment explains that rooms or externally shared channels are not treated as shared here, because the system does not have the same kind of workspace membership fact for them. In other words, `shared` has a narrow meaning: readable by every member of the workspace. This prevents the access-control rules from accidentally treating other kinds of audiences as workspace-wide visibility.

#### Function details

##### `member_subject`  (lines 9–10)

```
def member_subject(member_id: UUID) -> str
```

**Purpose**: This function creates the standard subject string for one specific workspace member. It is used when the system needs to record or compare content visibility for an individual member.

**Data flow**: It takes in a member ID, represented as a UUID, which is a globally unique identifier. It adds the fixed prefix `member:` in front of that ID and returns the resulting text, such as `member:...`. It does not change any stored data.

**Call relations**: Other code can call this whenever it needs to turn a member identity into the shared subject format used across the system. It does not call into any other project function; it simply builds the label and hands it back.


##### `subject_shared`  (lines 13–18)

```
def subject_shared(subject: str) -> bool
```

**Purpose**: This function answers the yes-or-no question: does this subject mean content is readable by every member of the workspace? It protects the code from treating other audience strings as workspace-wide access by mistake.

**Data flow**: It takes in a subject string. It compares that string to the one exact shared label, `shared`. It returns `True` only for that exact match, and `False` for member-specific subjects or any other audience-like value.

**Call relations**: Other code can call this when deciding how to interpret a visibility subject. It does not delegate to other functions; it performs one direct comparison and returns the answer.


### `core/src/ufo/audience.py`

`domain_logic` · `cross-cutting during conversation creation, reading, and audience checks`

An "audience" here is a small text label that travels with a conversation and its turns, like a tag on a folder saying who may look inside. Some conversations are shared across the whole workspace. Some belong to one member. Some belong to a named room. Some belong to a room that is shared with an outside organization, which is treated more carefully.

This file gives the project one consistent way to create those tags and one consistent way to reject bad ones. That matters because these strings are security boundaries: if two parts of the system invented their own formats, private information could accidentally be recalled into the wrong place.

The main shape is `Audience`, a type-safe name for a string. Helper functions build audience strings for shared conversations, members, regular rooms, and foreign rooms. `parse_audience` is the gatekeeper: it takes raw text and proves it matches one of the allowed forms. Other helpers answer practical questions: which audiences a member may read, whether an audience belongs to a specific member, which stored subjects may be read by that audience, and whether a requested audience change is only making access narrower rather than leaking it wider. The foreign-room rule is especially important: externally shared rooms do not read workspace-shared memory.

#### Function details

##### `conversation_audience`  (lines 14–15)

```
def conversation_audience(member_id: UUID | None) -> Audience
```

**Purpose**: Builds the audience label for either a workspace-shared conversation or a conversation tied to one member. It gives the rest of the system a single spelling for these two common audience types.

**Data flow**: It receives a member ID, or `None` when the conversation is shared. If the input is `None`, it returns the shared audience label. If a member ID is present, it turns that ID into the standard member audience string and returns it as an `Audience`.

**Call relations**: This is the basic constructor used when `readable_audiences` says what a member can read. `parse_audience` also calls it to verify that a raw member-audience string is written in the exact expected form.

*Call graph*: called by 2 (parse_audience, readable_audiences).


##### `room_audience`  (lines 18–19)

```
def room_audience(surface: str, room: str) -> Audience
```

**Purpose**: Builds the audience label for a normal room on a named surface, such as a chat platform or other place where rooms exist. It hides the exact string format behind a clear function name.

**Data flow**: It receives a surface name and room name. It passes them, along with the regular-room prefix, to the shared room-building helper. The result is an `Audience` such as a regular room audience, or an error if the names are not safe to encode.

**Call relations**: This function delegates the common validation and string-building work to `_room_audience`. `parse_audience` calls it when checking that a raw regular-room audience string is valid and canonical.

*Call graph*: calls 1 internal fn (_room_audience); called by 1 (parse_audience).


##### `foreign_room_audience`  (lines 22–23)

```
def foreign_room_audience(surface: str, room: str) -> Audience
```

**Purpose**: Builds the audience label for a room that is shared with an outside organization. This separate label matters because externally shared rooms have stricter reading rules elsewhere in the file.

**Data flow**: It receives a surface name and room name. It passes them, with the foreign-room prefix, to the shared helper. The output is a foreign-room `Audience`, unless the surface or room text is empty or contains a colon.

**Call relations**: Like `room_audience`, it relies on `_room_audience` for the common room-label rules. `parse_audience` uses it to confirm that a raw foreign-room audience string is exactly the one the system would have built itself.

*Call graph*: calls 1 internal fn (_room_audience); called by 1 (parse_audience).


##### `_room_audience`  (lines 26–29)

```
def _room_audience(prefix: str, surface: str, room: str) -> Audience
```

**Purpose**: Creates the actual room-style audience string after checking that its parts are safe. The leading underscore means it is an internal helper for this file rather than the public way callers should think about rooms.

**Data flow**: It receives a prefix, a surface, and a room. It rejects empty surface or room names, and it rejects colons inside either part because colons are used as separators in the audience format. If the pieces are safe, it joins them into one audience string and returns it.

**Call relations**: Both `room_audience` and `foreign_room_audience` call this helper so regular rooms and foreign rooms follow the same formatting and validation rules.

*Call graph*: called by 2 (foreign_room_audience, room_audience).


##### `parse_audience`  (lines 32–55)

```
def parse_audience(value: str) -> Audience
```

**Purpose**: Checks that a raw string is a real, allowed audience label. This is the file’s main safety checkpoint for preventing malformed or misleading audience strings from entering later logic.

**Data flow**: It receives text and first treats it as an `Audience`. If it is the shared audience, it accepts it. Otherwise it splits the text at colons to identify the kind of audience. For member audiences, it checks that the rest is a valid UUID, meaning a standard unique member identifier. For room and foreign-room audiences, it rebuilds the expected label using the normal constructor. It returns the validated audience, or raises a `ValueError` if anything does not match the allowed format.

**Call relations**: Several other functions call this before trusting an audience: `audience_member`, `audience_subjects`, and `narrow_audience`. While validating, it calls `conversation_audience`, `room_audience`, and `foreign_room_audience` so parsing and construction stay in agreement.

*Call graph*: calls 3 internal fn (conversation_audience, foreign_room_audience, room_audience); called by 3 (audience_member, audience_subjects, narrow_audience); 1 external calls (UUID).


##### `readable_audiences`  (lines 58–63)

```
def readable_audiences(member_id: UUID) -> tuple[Audience, ...]
```

**Purpose**: Returns the conversation audiences whose content a given member is allowed to read from the workspace view. In plain terms, a member can read shared workspace conversations and their own member-specific conversations.

**Data flow**: It receives a member ID. It returns a two-item tuple: the shared audience and the audience built from that member ID. It deliberately does not include room or foreign-room audiences, because the workspace does not know room membership here.

**Call relations**: This function calls `conversation_audience` to build the member-specific entry. It is a ready-made answer for higher-level read paths that need to know which audience buckets to query for one member.

*Call graph*: calls 1 internal fn (conversation_audience).


##### `audience_member`  (lines 66–70)

```
def audience_member(audience: Audience) -> UUID | None
```

**Purpose**: Answers the question: "Does this audience belong to a specific member, and if so, which one?" It returns the member’s UUID for member audiences and `None` for all other audience types.

**Data flow**: It receives an audience, validates it with `parse_audience`, and then checks whether the validated text starts with the member prefix. If not, it returns `None`. If it does, it removes the prefix, turns the remaining text back into a UUID, and returns that ID.

**Call relations**: This function relies on `parse_audience` so it never extracts a member ID from a malformed label. It also uses UUID parsing to convert the stored text form back into the standard member identifier type.

*Call graph*: calls 1 internal fn (parse_audience); 1 external calls (UUID).


##### `audience_subjects`  (lines 73–80)

```
def audience_subjects(audience: Audience) -> frozenset[str]
```

**Purpose**: Tells the system which stored subjects a conversation with this audience is allowed to read. A subject is a storage/read key for facts or memory; this function keeps those keys inside the right privacy boundary.

**Data flow**: It receives an audience and first validates it with `parse_audience`. If the audience is for a foreign room, it returns only that foreign-room subject. For all other valid audiences, it returns both the workspace-shared subject and the audience’s own subject.

**Call relations**: This function is built on `parse_audience` because the read rules only make sense for known audience labels. Its most important policy is that foreign-room conversations do not receive the workspace-shared subject, which prevents internal shared memory from being pulled into an externally shared channel.

*Call graph*: calls 1 internal fn (parse_audience).


##### `narrow_audience`  (lines 83–98)

```
def narrow_audience(current: Audience, requested: Audience) -> Audience
```

**Purpose**: Chooses the safer audience when current and requested audiences differ, and rejects changes that would cross an unsafe boundary. It is used to make sure an audience change only narrows or preserves disclosure, rather than silently widening it.

**Data flow**: It receives the current audience and a requested audience. It validates both. If they are the same, or the request is merely for the shared audience, it keeps the current audience. If the current audience is shared, it accepts the more specific requested audience. For regular-room versus foreign-room versions of the same surface and room, it returns the stricter foreign-room audience when needed. If the two audiences do not fit an allowed narrowing pattern, it raises a `ValueError`.

**Call relations**: This function calls `parse_audience` first so all later comparisons use trusted labels. It then splits each audience string into kind and key to compare room identities. It is the policy checkpoint that prevents a conversation from being moved from one audience to an unrelated one.

*Call graph*: calls 1 internal fn (parse_audience); 1 external calls (partition).


### Scheduled task visibility
Applies workspace and subject visibility rules to decide who can read scheduled task prompts and descriptions.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/visibility.py`

`domain_logic` · `request handling`

Scheduled tasks can post their results into different kinds of conversations. Some conversations are shared with the whole workspace, while others belong to just one member. This file answers a simple but important question: “Can this person see the task’s original content?”

The rule is based mainly on where the task reports its output, not just who created it. If a task posts into a shared workspace conversation, then its prompt and description are also considered shared, because the results will be visible to everyone anyway. If the task posts into a private member conversation, then only the matching member should be able to read it. There is one extra allowance: the member who created the task can also read it, even if the reporting audience is not exactly their own private conversation.

This matters for privacy. Without this check, a task created for one person’s private workflow could accidentally reveal its instructions or description to other members. The code also treats anonymous or creatorless tasks carefully: if there is no current member asking, they only get access to content that is already shared with the workspace.

#### Function details

##### `task_content_visible`  (lines 7–20)

```
def task_content_visible(listed: ListedTask, member_id: UUID | None) -> bool
```

**Purpose**: Decides whether a given member may read a scheduled task’s prompt and description. It is used as a privacy gate before showing task content.

**Data flow**: It receives a listed scheduled task and either a member ID or no member ID. First it checks whether the task’s audience is shared with the whole workspace; if so, it returns true. If there is no member asking, it returns false for anything private. Otherwise it compares the task’s audience with that member’s own private subject, and also checks whether that member created the task. The result is a simple true or false answer: visible or not visible.

**Call relations**: When another part of the scheduled task system needs to show task details, it can call this function to make the visibility decision. Inside the decision, it asks `subject_shared` whether the audience is workspace-wide, and uses `member_subject` to build the private audience label for the requesting member so it can compare that label with the task’s audience.

*Call graph*: 2 external calls (member_subject, subject_shared).

# Workspace Context, Agent Scope, and Seats  `stage-22.3`

This stage is shared behind-the-scenes support for keeping work inside the right boundaries. The system may serve many workspaces, and different agents may act inside them. These files make sure each action knows which workspace it belongs to, which agent is acting, and which members are allowed to use that agent.

workspace.py is the anchor. It records the current workspace for the running task, so database access, credentials, and billing are not accidentally mixed between customers or teams. agent_scope.py adds the next layer: once a workspace is known, it tracks the current acting agent. Agent-owned code can ask “who am I?” and gets a clear answer only when it is running inside a valid agent scope. If not, it fails loudly instead of guessing.

seats.py controls permission to use an agent. A “seat” means a workspace member has access. Admins can grant, revoke, check, and list seats. Together, these parts work like badges at a secure building: workspace, agent identity, and member access must all line up.

## Files in this stage

### Workspace Agent Access Context
Defines the runtime boundaries for the current acting agent, workspace seat permissions, and the active workspace context.

### `core/src/ufo/agent_scope.py`

`domain_logic` · `cross-cutting during agent-scoped work`

Some parts of the system are meant to run on behalf of a specific agent, inside a specific workspace. This file provides that “ambient identity”: identity that does not have to be passed into every single function by hand, but is still tied to the current execution flow. A useful analogy is a visitor badge: once you enter a secure area, your badge says both who you are and which building you are allowed in.

The main record is `AgentScope`, which stores two identifiers: the workspace ID and the agent ID. The `agent` context manager creates one of these records using the current workspace from `ws_current()`, then temporarily binds it to the running context. Code inside `with agent(agent_id):` can later call `agent_current()` to retrieve that same bound identity.

The file also protects against two dangerous mistakes. First, it does not allow code to switch to a different agent while another agent is already bound in the same flow. Second, `agent_current()` checks that the current workspace still matches the workspace captured when the agent was bound. Without these checks, code might accidentally use one agent’s authority in another workspace, which could cause confusing bugs or security problems.

#### Function details

##### `agent`  (lines 28–38)

```
def agent(agent_id: UUID) -> Iterator[AgentScope]
```

**Purpose**: This function is used as a `with` block to temporarily bind an agent to the current execution flow. It lets later code know which agent is acting, without requiring every function call to carry the agent ID explicitly.

**Data flow**: It receives an agent ID. It reads the current workspace using `ws_current()`, combines the workspace ID and agent ID into an `AgentScope`, and stores that scope in a context-local variable, meaning it applies to this flow of work rather than globally to the whole program. It yields the scope to the code inside the `with` block, then restores the previous value when the block ends. If a different agent is already bound, it raises an error instead of silently switching identities.

**Call relations**: Code that starts agent-owned work wraps that work in `agent`. During setup it asks `ufo.workspace.ws_current` which workspace is active, then creates an `AgentScope`. Later, functions such as `agent_current` rely on this binding to know which agent is currently allowed to act.

*Call graph*: 2 external calls (__init__, ws_current).


##### `agent_current`  (lines 41–48)

```
def agent_current() -> AgentScope
```

**Purpose**: This function returns the agent identity currently bound by `agent`. It is the safe lookup point for code that needs to know the active agent and workspace.

**Data flow**: It reads the context-local agent scope. If no agent has been bound, it raises `AgentUnbound` with a message telling the caller to use `with agent(agent_id):`. If a scope exists, it also reads the current workspace through `ws_current()` and checks that it matches the workspace stored in the agent scope. If everything matches, it returns the `AgentScope`; if the workspace has changed, it raises an error.

**Call relations**: Agent-scoped capabilities call `agent_current` when they need the current agent’s identity. It depends on `agent` having already set that identity, and it checks back with `ufo.workspace.ws_current` to make sure the agent has not crossed into the wrong workspace before handing the scope back to the caller.

*Call graph*: 2 external calls (__init__, ws_current).


### `core/src/ufo/seats.py`

`domain_logic` · `cross-cutting: member creation, admission checks, running-turn enforcement, admin seat changes, and reporting jobs`

This file is the workspace access gate. A member can exist in the system even after their seat is removed, because their identity and history still matter. But without a seat, the agent must not answer them. Think of it like a building badge: the person’s employee record remains, but the door will not open until the badge is restored.

The file keeps the rules in one place so chat admission, running conversations, resumed work, and extension tools all make the same decision. The central rule is simple: a member is admitted only if their row belongs to the workspace and has a non-empty seating time. Admins can restore a seat with `grant` or remove it with `revoke`. Revoking the last seated admin is blocked, because seat management happens through chat; if the last admin lost access, nobody could restore anyone.

The file also owns member creation. New members are created with valid, lowercased email addresses, and are seated by default. It uses database locks and conflict-safe inserts so two callers adding the same person at the same time end up with one member, not duplicates. Helper functions resolve email domains, find members by email, check admin status, and provide workspace candidates for jobs that report on members.

#### Function details

##### `gate_member`  (lines 37–48)

```
def gate_member(speaker_member_id: UUID | None, on_behalf_of_member_id: UUID | None) -> UUID | None
```

**Purpose**: Chooses which member a turn should be checked against for seat access. If there is a direct speaker, that person is checked; otherwise the turn is checked against the member it is acting for.

**Data flow**: It receives a possible speaker member id and a possible “on behalf of” member id. It returns the speaker id when present, otherwise the on-behalf-of id, and returns nothing if neither exists. It does not read or change stored data.

**Call relations**: This is the shared rule used wherever the system needs to decide whose seat controls a turn. It keeps admission, resume checks, dispatch sweeps, and per-round checks from inventing different meanings for the same turn.


##### `SeatSnapshot.seated`  (lines 72–73)

```
def seated(self) -> int
```

**Purpose**: Counts how many members in a seat snapshot currently have access. It is a quick summary for displays or reports that need the seated total.

**Data flow**: It reads the snapshot’s member entries. It counts entries whose `seated` flag is true and returns that number. It does not change the snapshot.

**Call relations**: This property is used after `Seats.snapshot` has built a full picture of the workspace’s members. It turns that full list into a simple count.


##### `Seats.admits`  (lines 84–98)

```
async def admits(self, connection: AsyncConnection, member_id: UUID) -> bool
```

**Purpose**: Answers the core access question for one member: may the agent answer this person in this workspace right now? It returns true only when the member exists in the workspace and still has a seat.

**Data flow**: It receives a database connection and a member id. It reads that member’s `seated_at` value from the workspace’s member table. It returns true if a matching row exists and the seating time is present; otherwise it returns false.

**Call relations**: This function is called anywhere the system needs a one-person gate, such as admitting a new turn or checking continued access. It relies on the database query through the async connection, so revokes take effect everywhere that asks this question.

*Call graph*: 2 external calls (execute, select).


##### `Seats.all_seated`  (lines 100–120)

```
async def all_seated(self, connection: AsyncConnection, member_ids: Collection[UUID]) -> bool
```

**Purpose**: Checks whether every member in a group still has a seat. This is useful for turns that involve more than one member, where any unseated person should block or park the work.

**Data flow**: It receives a database connection and a collection of member ids. If the collection is empty, it returns true. Otherwise it counts how many of those ids are seated members of the workspace, then compares that count with the number requested. The result is a true-or-false answer.

**Call relations**: This is the set-based version of `Seats.admits`. Per-round checks, parked-work checks, and dispatch sweeps can ask one database question for the whole group instead of checking people one by one.

*Call graph*: 2 external calls (execute, select).


##### `Seats.snapshot`  (lines 122–145)

```
async def snapshot(self, connection: AsyncConnection) -> SeatSnapshot
```

**Purpose**: Builds a readable snapshot of all members in a workspace and whether each one is seated or an admin. This is the information an admin tool or report would show.

**Data flow**: It receives a database connection. It reads all member rows for the workspace, ordered by creation time and id, then turns each row into a `SeatEntry`. It returns a `SeatSnapshot` containing those entries.

**Call relations**: This function packages raw database rows into simple data objects. The snapshot’s `seated` property can then count active seats, while callers can also inspect individual members.

*Call graph*: 4 external calls (__init__, __init__, execute, select).


##### `Seats.grant`  (lines 147–157)

```
async def grant(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Restores access for an existing workspace member by email. If the member already has a seat, it quietly does nothing.

**Data flow**: It receives a database connection and an email address. It looks up the member in this workspace using `_member_by_email`. If the member is already seated, it stops. If not, it updates the member row with the current time as `seated_at` and refreshes `updated_at`.

**Call relations**: Admin-facing tools call this when an admin gives someone access again. It delegates the email lookup and unknown-member error to `_member_by_email`, then performs the database update itself.

*Call graph*: calls 1 internal fn (_member_by_email); 2 external calls (execute, update).


##### `Seats.revoke`  (lines 159–182)

```
async def revoke(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Removes access for an existing workspace member by email. It refuses to revoke the final seated admin, because that would leave the workspace with no seated person able to grant seats back.

**Data flow**: It receives a database connection and an email address. First it locks the workspace row, which is like taking a numbered ticket so two revokes cannot both make decisions from stale counts. It looks up the member. If they are already unseated, it does nothing. If they are the only seated admin, it raises `LastAdminSeatRevocation`. Otherwise it clears `seated_at` and updates the row timestamp.

**Call relations**: Admin-facing tools call this to remove access. It uses `_member_by_email` to find the target and `_seated_admin_count` to protect the last admin. It does not directly stop running turns; later admission and per-round seat checks notice the revoke and refuse or park work.

*Call graph*: calls 2 internal fn (_member_by_email, _seated_admin_count); 4 external calls (__init__, execute, select, update).


##### `Seats._member_by_email`  (lines 184–201)

```
async def _member_by_email(self, connection: AsyncConnection, email: str) -> tuple[UUID, datetime | None, bool]
```

**Purpose**: Finds a workspace member by email and returns the facts needed for seat changes. It raises a clear error if the email is not a member of this workspace.

**Data flow**: It receives a database connection and an email address. It strips and lowercases the email for comparison, reads the matching member row in this workspace, and returns the member id, seating time, and admin flag. If no row matches, it raises `UnknownMember`.

**Call relations**: This is an internal helper used by `Seats.grant` and `Seats.revoke`. It keeps both operations using the same workspace-scoped email lookup and the same error behavior.

*Call graph*: called by 2 (grant, revoke); 3 external calls (__init__, execute, select).


##### `Seats._seated_admin_count`  (lines 203–212)

```
async def _seated_admin_count(self, connection: AsyncConnection) -> int
```

**Purpose**: Counts how many admins in the workspace currently have seats. It exists mainly to protect against removing the last seated admin.

**Data flow**: It receives a database connection. It counts member rows in this workspace where `seated_at` is present and `is_admin` is true. It returns that count as an integer.

**Call relations**: This helper is called by `Seats.revoke` after the workspace row has been locked. That order makes the last-admin check safe even if two admins are being revoked at nearly the same time.

*Call graph*: called by 1 (revoke); 2 external calls (execute, select).


##### `email_domain`  (lines 215–228)

```
def email_domain(email: str) -> str
```

**Purpose**: Extracts the domain part of a valid email address, such as `example.com` from `person@example.com`. If the address is malformed, it returns an empty string so the bad value cannot match or create a member.

**Data flow**: It receives an email string. It trims spaces, lowercases it, checks that it has exactly one `@`, has both a local part and a domain, and contains no whitespace. It returns the domain when valid, or an empty string when invalid.

**Call relations**: This helper is shared by member creation and workspace-domain lookup functions. Because `create_member`, `workspace_domain`, and `workspace_by_domain` all use it, the system has one consistent idea of what a usable email domain looks like.

*Call graph*: called by 3 (create_member, workspace_by_domain, workspace_domain).


##### `workspace_domain`  (lines 231–248)

```
async def workspace_domain(connection: AsyncConnection, workspace_id: UUID) -> str | None
```

**Purpose**: Returns the email domain that names a workspace. The workspace domain is taken from the first member ever created in that workspace.

**Data flow**: It receives a database connection and workspace id. It reads the earliest member email for that workspace. If there is no member, it returns null. Otherwise it passes the email through `email_domain` and returns the domain, or null if no domain can be derived.

**Call relations**: This function uses the same domain parser as member creation. Other parts of the system can use it to compare joins, sign-ins, or operator checks against the workspace’s established domain.

*Call graph*: calls 1 internal fn (email_domain); 2 external calls (execute, select).


##### `workspace_by_domain`  (lines 251–285)

```
async def workspace_by_domain(connection: AsyncConnection, domain: str) -> UUID | None
```

**Purpose**: Finds the workspace addressed by an email domain. It is the reverse of `workspace_domain`: given a domain, it looks for a workspace whose first member has an address at that domain.

**Data flow**: It receives a database connection and a domain string. It normalizes and validates the domain by feeding a made-up address into `email_domain`. If invalid, it returns null. If valid, it searches first members across workspaces for matching email domains and returns the oldest matching workspace id, or null if none match.

**Call relations**: This function supports cross-workspace lookup, such as operator or directory flows. It calls `email_domain` so typed domains follow the same rules as stored member addresses, then uses a database query to pick the workspace.

*Call graph*: calls 1 internal fn (email_domain); 2 external calls (execute, select).


##### `member_by_email`  (lines 288–304)

```
async def member_by_email(connection: AsyncConnection, workspace_id: UUID, email: str) -> UUID | None
```

**Purpose**: Looks up a member id by email within one workspace. It answers only whether that address already belongs to a member there; it does not create anyone.

**Data flow**: It receives a database connection, workspace id, and email address. It compares the stripped, lowercased email inside that workspace and returns the matching member id if found. If no matching member exists, it returns null.

**Call relations**: Routes or sign-in flows can call this after they have an email and a workspace. The lookup is scoped in the database query itself, so it does not accidentally read a member from another workspace on the way.

*Call graph*: 2 external calls (execute, select).


##### `member_is_admin`  (lines 307–317)

```
async def member_is_admin(connection: AsyncConnection, workspace_id: UUID, member_id: UUID) -> bool
```

**Purpose**: Checks whether a given member is an admin in a given workspace. It returns false if the member does not belong to that workspace.

**Data flow**: It receives a database connection, workspace id, and member id. It reads the `is_admin` value for that exact member-workspace pair. It returns true for an admin row and false for a non-admin or missing row.

**Call relations**: Permission checks can call this before allowing admin-only actions. It uses a direct database read and does not rely on the caller to prove the member belongs to the workspace.

*Call graph*: 2 external calls (execute, select).


##### `create_member`  (lines 320–391)

```
async def create_member(connection: AsyncConnection, workspace_id: UUID, email: str, *, is_admin: bool=False, invited_by: UUID | None=None) -> UUID
```

**Purpose**: Creates a workspace member in the one approved way. New members are seated immediately by the database default, and duplicate creation races return the already-created member instead of making another row.

**Data flow**: It receives a database connection, workspace id, email address, and optional admin and invitation information. It first validates the email shape with `email_domain`, lowercases it, and locks the workspace row so concurrent member creations queue safely. It then tries to insert a member with a new UUID. If the insert succeeds, it returns the new id. If another caller already created the same workspace-email pair, it reads and returns the existing id.

**Call relations**: Every surface that creates members is expected to come through this function, whether onboarding, teammate joins, or future tools. It calls `email_domain` to enforce one address rule everywhere, uses `uuid4` for new ids, and relies on database conflict handling to collapse races.

*Call graph*: calls 1 internal fn (email_domain); 3 external calls (execute, select, uuid4).


##### `member_workspaces`  (lines 394–402)

```
def member_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a reusable workspace-candidate query for jobs that care about workspaces with members. It lets extension jobs ask for relevant workspaces without directly knowing the member-table query.

**Data flow**: It defines a small inner query that selects distinct workspace ids from the member table. It passes that query builder to `owner_candidates` and returns the resulting `WorkspaceCandidates` object.

**Call relations**: Extensions or scheduled jobs can declare these candidates when they need to report on seats or members. The function hands the actual database-selection detail to `member_workspaces.with_a_member` and wraps it through the runtime candidate helper.

*Call graph*: 1 external calls (owner_candidates).


##### `member_workspaces.with_a_member`  (lines 399–400)

```
def with_a_member() -> sa.Select[tuple[UUID]]
```

**Purpose**: Creates the database query used to find all workspaces that have at least one member. It is intentionally broad: one member is enough to make the workspace relevant.

**Data flow**: It takes no direct input. It builds and returns a SQL selection of distinct workspace ids from the member table. It does not execute the query itself.

**Call relations**: This inner function is supplied to `owner_candidates` by `member_workspaces`. The runtime can later execute it when choosing which workspaces a member-related job should visit.

*Call graph*: 1 external calls (select).


### `core/src/ufo/workspace.py`

`domain_logic` · `cross-cutting`

A workspace is like a customer account or tenant. Many parts of the system need to fetch secrets, read or write workspace-owned data, or record usage costs. This file makes sure those actions happen only after a workspace has been clearly bound for the current block of work.

The main idea is simple: code enters `with ws(workspace_id):`, and inside that block `ws_current()` can recover the active workspace. If code asks for a credential or tries to bill usage without such a block, it raises an error instead of guessing. That matters because guessing could leak one workspace’s key into another workspace’s work, or charge the wrong account.

`WorkspaceScope` is the handle for the active workspace. It can fetch a credential, first trying the workspace’s own stored key, then falling back to a platform-wide environment variable. It can also store or rotate workspace credentials. For billing, it offers `billable_event()`, a block where model usage is collected and then written to that workspace when the block exits.

The file also connects to the database workspace context, so database transactions made inside the workspace block are scoped the same way. In short, this is the project’s guardrail for tenant safety: bind once at the edge of a job or request, then secrets, billing, and database scope follow that workspace by construction.

#### Function details

##### `init_workspace_credentials`  (lines 28–32)

```
def init_workspace_credentials(store: CredentialStore | None) -> None
```

**Purpose**: This installs the credential store the deployment will use to look up workspace-owned secrets. It is meant to be called during startup, before normal workspace work needs credentials.

**Data flow**: It receives either a credential store object or `None`. It saves that value in this module’s shared `_store` variable. Later credential lookups use that stored object; if it is `None`, only platform environment defaults can be used.

**Call relations**: This sets up the shared credential path that `WorkspaceScope.credential`, `WorkspaceScope.credential_is_stored`, `WorkspaceScope.rotate_credential`, and `WorkspaceScope.put_credential` rely on. Without this setup, those methods either fall back to environment variables or report that stored workspace credentials are unavailable.


##### `BillableEvent.usage`  (lines 48–58)

```
def usage(self, model: str, usage: Usage, pricing: Pricing=CORE_PRICING, byok: bool=False) -> None
```

**Purpose**: This records one piece of billable model usage inside a billing block. Callers use it when a provider reports that a model call consumed tokens or another metered resource.

**Data flow**: It receives the model name, a usage record, pricing information, and a flag saying whether the workspace used its own provider key. It appends those details to the event’s private list. Nothing is written to the database yet; the information is saved until the surrounding billing block closes.

**Call relations**: This method is used inside the event object created by `WorkspaceScope.billable_event`. The billing block later reads the saved usage entries and hands them to `record_workspace_usage` so the correct workspace ledger is updated.


##### `WorkspaceScope.credential`  (lines 67–82)

```
async def credential(self, slot: str, env: str | None=None) -> str
```

**Purpose**: This fetches the secret value for a named credential slot for the current workspace. It prefers the workspace’s own stored key, and only falls back to the platform’s environment variable if the workspace has no stored value.

**Data flow**: It starts with a credential slot name and an optional environment-variable name. If a credential store is configured, it asks that store for this workspace’s value. If no stored value exists, it calls `deploy_env` to read the platform default from the environment. If neither path produces a value, it raises `CredentialSlotUnset` so the caller fails clearly instead of using a missing or wrong key.

**Call relations**: This is the safe secret-fetching path exposed by `WorkspaceScope`, which callers normally get through `ws_current()`. It calls into the credential store when available, uses `deploy_env` for environment fallback, and raises `CredentialSlotUnset` when the slot cannot be resolved.

*Call graph*: 2 external calls (__init__, deploy_env).


##### `WorkspaceScope.credential_is_stored`  (lines 84–94)

```
async def credential_is_stored(self, slot: str) -> bool
```

**Purpose**: This answers whether the active workspace has its own stored credential for a slot. The billing code can use that answer to know whether the platform should charge for provider usage or whether the workspace paid the provider directly with its own key.

**Data flow**: It receives a credential slot name. If no credential store is configured, it returns `false`. Otherwise it tries to fetch the workspace’s stored value; success becomes `true`, and a missing slot becomes `false`. It does not return the secret itself.

**Call relations**: This method uses the same configured credential store as `WorkspaceScope.credential`, but only as a yes-or-no check. It helps callers decide how to mark usage before they pass it into `BillableEvent.usage`.


##### `WorkspaceScope.rotate_credential`  (lines 96–101)

```
async def rotate_credential(self, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: This replaces an existing stored workspace credential, but only if the caller’s expected old value matches. That compare-before-replace behavior helps avoid overwriting a secret that changed since the caller last saw it.

**Data flow**: It receives a slot name, the expected current plaintext value, and the new plaintext value. If no credential store is configured, it returns `false`. Otherwise it asks the store to rotate the credential for this workspace and returns the store’s success-or-failure result.

**Call relations**: This is one of the credential-changing operations on `WorkspaceScope`. It delegates the actual storage work to the configured credential store that was installed by `init_workspace_credentials`.


##### `WorkspaceScope.put_credential`  (lines 103–107)

```
async def put_credential(self, slot: str, plaintext: str) -> None
```

**Purpose**: This stores an initial credential value for the active workspace. It is used when an authorized owner adds a workspace-specific key.

**Data flow**: It receives a slot name and plaintext secret. If no credential store is configured, it raises an error because there is nowhere safe to put the secret. Otherwise it sends the workspace id, slot, and plaintext value to the credential store.

**Call relations**: Like the other credential methods, this depends on the credential store installed by `init_workspace_credentials`. It is the write path that complements `WorkspaceScope.credential`, which later reads the stored value.


##### `WorkspaceScope.billable_event`  (lines 110–122)

```
async def billable_event(self) -> AsyncIterator[BillableEvent]
```

**Purpose**: This creates a billing block for the active workspace. Code inside the block can report provider usage, and when the block exits, all reported usage is booked to that workspace.

**Data flow**: It creates a fresh `BillableEvent` and yields it to the caller. The caller adds usage records to that event. When the block finishes, even if later output processing failed, it opens a workspace database transaction with `workspace_tx` and sends each saved usage entry to `record_workspace_usage` along with the workspace id.

**Call relations**: This method ties `BillableEvent.usage` to persistent billing. `BillableEvent` collects usage during the work, then `WorkspaceScope.billable_event` hands each entry to `record_workspace_usage` inside a `workspace_tx` transaction so the charge lands in the same workspace scope.

*Call graph*: 3 external calls (__init__, record_workspace_usage, workspace_tx).


##### `ws`  (lines 126–134)

```
def ws(workspace_id: UUID) -> Iterator[WorkspaceScope]
```

**Purpose**: This temporarily binds a workspace id as the active workspace for a block of code. It is the entry gate that makes later calls to `ws_current()` safe and unambiguous.

**Data flow**: It receives a workspace id. It stores that id in the current execution context, yields a `WorkspaceScope` for code inside the `with` block, and then restores the previous context when the block ends. The before-and-after cleanup prevents one workspace binding from leaking into later work.

**Call relations**: This function calls the database workspace context’s `set` and `reset` operations, and creates a `WorkspaceScope` for the block. Code inside the block can then call `ws_current()` and database transactions can see the same workspace context.

*Call graph*: 3 external calls (__init__, reset, set).


##### `ws_current`  (lines 137–143)

```
def ws_current() -> WorkspaceScope
```

**Purpose**: This returns the currently bound workspace scope. If no workspace has been bound, it raises `WorkspaceUnbound` so the mistake is caught immediately.

**Data flow**: It reads the current workspace id from the execution context. If the value is missing, it raises an error telling the caller to wrap the work in `with ws(workspace_id):`. If the value exists, it returns a new `WorkspaceScope` containing that id.

**Call relations**: This is the lookup counterpart to `ws`. `ws` places the workspace id into the current context; `ws_current` retrieves it later for credential lookup, billing, and other workspace-scoped work.

*Call graph*: 3 external calls (__init__, __init__, get).

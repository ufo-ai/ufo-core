# Identity and authorization checks  `stage-21.1` (cross-cutting infrastructure)

This stage is shared behind-the-scenes support for answering a basic question before work happens: “Who is allowed to do or see this?” It protects the system from mixing up people, workspaces, agents, and private content.

The access package marker simply lets this group of code be imported. The authority model defines whether a job is acting as a real workspace member or only as the workspace itself, without anyone’s private permissions. The agent scope tracker records which agent is currently acting, so agent code can safely ask who it represents.

Several parts then apply these identities to real decisions. Seats are the on/off access switch that decides which members may talk to an agent in a workspace. Web audience rules decide which members can see or chat with agents, and reserve sensitive actions, like opening someone else’s transcript, for admins with an audit trail. Turn audience and subject labels give conversations strict names for “everyone,” one member, private, shared, or room-based access. Scheduled task visibility uses these same ideas to decide who may read private task details.

## Files in this stage

### Runtime identity foundations
Defines the import boundary and runtime identities used to decide who or what a unit of work is acting as.

### `core/src/ufo/runtime/access/__init__.py`

`other` · `import/package discovery`

This is an empty package initializer. In Python, a file named `__init__.py` tells the import system that the surrounding folder should be treated as a package, meaning other files can refer to it with dotted names like `ufo.runtime.access`. Think of it like a label on a drawer: the label does not contain the tools, but it lets the rest of the project find the drawer reliably. Because this file is empty, it does not run setup code, expose shortcuts, or change any data. Its value is structural: without it, some Python environments or tooling might not recognize this folder as an importable part of the project.


### `core/src/ufo/runtime/agent_scope.py`

`orchestration` · `cross-cutting`

Some actions in this system belong to a specific agent, and that agent must also belong to the workspace currently in use. This file provides that “ambient identity”: information that code can read without passing the agent ID through every function call by hand. It is like putting on a clearly labeled badge when entering a room, so every tool in the room knows who is using it.

The main piece is `AgentScope`, a small immutable record containing both a workspace ID and an agent ID. The private context variable `_current_agent` stores the active badge for the current execution context. A context variable is a safe per-task storage area, so separate concurrent tasks do not accidentally share the same agent identity.

The `agent` context manager creates and temporarily installs an `AgentScope` for a given agent in the current workspace. It refuses to switch to a different agent while one is already bound, which prevents confusing nested identity changes. When the `with` block ends, it restores the previous state.

The `agent_current` function reads the active agent scope. It fails loudly if no agent is bound, or if the workspace has changed underneath it. Without these checks, code could silently perform agent-owned work under the wrong identity or in the wrong workspace.

#### Function details

##### `agent`  (lines 28–38)

```
def agent(agent_id: UUID) -> Iterator[AgentScope]
```

**Purpose**: Temporarily marks the current execution as being done by one specific agent inside the current workspace. Code uses it around a block of work so anything inside that block can later discover the active agent safely.

**Data flow**: It takes an `agent_id` as input and reads the current workspace ID from `ws_current()`. It combines those into an `AgentScope`, checks whether a different agent is already bound, and if not, stores the new scope in the context variable. It yields that scope to the caller’s `with` block, then restores the previous context when the block finishes.

**Call relations**: This function is used at the boundary where code begins doing work on behalf of an agent. It calls `ws_current()` to anchor the agent to the workspace that is active at that moment, then creates an `AgentScope` so later code, especially code that calls `agent_current`, can confirm the correct identity.

*Call graph*: 2 external calls (__init__, ws_current).


##### `agent_current`  (lines 41–48)

```
def agent_current() -> AgentScope
```

**Purpose**: Returns the agent identity currently bound to this execution. It is the safety check used by agent-owned capabilities before they trust that an agent is present and belongs to the active workspace.

**Data flow**: It reads the stored agent scope from the context variable. If nothing is stored, it raises `AgentUnbound` with a clear message telling the caller to use `with agent(agent_id):`. If a scope exists, it reads the current workspace from `ws_current()` and compares it with the workspace saved in the scope. If they match, it returns the `AgentScope`; if not, it raises an error because the identity no longer matches the workspace.

**Call relations**: This function is called by code that needs to know which agent is currently acting. It depends on `agent` having already installed a scope, and it calls `ws_current()` to make sure that the saved agent identity has not crossed into a different workspace by mistake.

*Call graph*: 2 external calls (__init__, ws_current).


### `core/src/ufo/runtime/authority.py`

`data_model` · `cross-cutting during execution and permission checks`

Every execution in the system needs a clear answer to the question: “Whose authority is this using?” This file gives that answer a small, fixed shape. If the work is acting as a real member, it uses `MemberAuthority` and carries that member’s ID. If the work is not using any member’s private credentials, it uses `WorkspaceAuthority`. Think of it like a badge: some badges have a person’s name on them, while others only say “building access.”

The file also provides helper functions for translating between this clearer in-memory model and an older storage shape where “no member” is represented by `None`. That matters because databases, durable records, or signed tokens may store only a nullable member ID, while runtime code wants a safer and more explicit authority object.

One important rule appears in `turn_authority`: a single turn of execution cannot claim both a direct speaker and a delegated “on behalf of” member at the same time. If both are present, the file rejects the situation with an error instead of guessing. Without this file, different parts of the system might interpret missing or present member IDs differently, which could lead to confusing or unsafe permission decisions.

#### Function details

##### `authority_from_member_id`  (lines 28–30)

```
def authority_from_member_id(member_id: UUID | None) -> ExecutionAuthority
```

**Purpose**: This function turns a stored member ID, which may be missing, into the explicit authority object used while code is running. Use it when reading authority from records or tokens that store `None` to mean “workspace authority.”

**Data flow**: It receives either a member UUID or `None`. If it gets `None`, it returns the shared workspace authority object. If it gets a UUID, it creates a `MemberAuthority` carrying that UUID. The result is always one clear execution authority.

**Call relations**: This is the shared decoder for nullable member IDs. `turn_authority` calls it after deciding which member ID, if any, represents the current turn’s authority.

*Call graph*: called by 1 (turn_authority); 1 external calls (__init__).


##### `authority_member_id`  (lines 33–41)

```
def authority_member_id(authority: ExecutionAuthority) -> UUID | None
```

**Purpose**: This function converts an explicit runtime authority back into the older storage-friendly form: a member UUID or `None`. Use it when saving or signing authority in places that expect a nullable member ID.

**Data flow**: It receives an execution authority object. If the authority belongs to a member, it extracts and returns that member’s UUID. If the authority is workspace-only, it returns `None`. If it receives something that is not a recognized authority type, it raises a `TypeError` instead of silently producing bad data.

**Call relations**: This function is the encoder that mirrors `authority_from_member_id`. It is meant for the boundary between runtime code and persistence or token formats, where authority must be represented as a nullable member ID.


##### `turn_authority`  (lines 44–52)

```
def turn_authority(speaker_member_id: UUID | None, on_behalf_of_member_id: UUID | None) -> ExecutionAuthority
```

**Purpose**: This function decides the one authority for a turn of work, using either the speaker member or a delegated member. It prevents a turn from carrying two member authorities at once.

**Data flow**: It receives two optional member IDs: one for the speaker and one for an “on behalf of” member. If both are present, it raises a `ValueError` because that would be ambiguous. Otherwise, it chooses the present ID, or `None` if neither is present, and passes that value to `authority_from_member_id`. The output is a single explicit execution authority.

**Call relations**: This function sits where turn information becomes runtime permission identity. After checking that the turn is not trying to use both direct and delegated authority, it hands the chosen nullable ID to `authority_from_member_id` so the rest of the system can work with a clear authority object.

*Call graph*: calls 1 internal fn (authority_from_member_id).


### Member access seats
Controls which workspace members may interact with agents and how portal-level access changes are authorized.

### `extensions/web/ufo_ext_web/audience.py`

`domain_logic` · `request handling and admin tool actions`

The web portal needs a clear answer to a simple question: “When this person signs in, which agents may they reach?” This file is that rulebook. It treats a member’s email address as their web identity, and stores explicit access grants as small records keyed by agent id and email. Without this file, the portal would not know how to separate public workspace agents from private agents, owner-created agents, or conversations that require special permission.

The main path is `web_audience`. It looks up the signed-in email in the workspace seat list, asks the surface for all agents, reads stored web grants, and builds a `WebAudience` object. Workspace admins can reach every agent. Regular seated members can reach workspace-visible agents, agents explicitly granted to their email, agents they own, and certain agent conversations tied to their private extension activity.

The file also exposes tool actions. These are like controlled buttons an admin can press from inside the system: grant web access, revoke web access, or acknowledge opening a private transcript. Before any of these actions change anything, `_gate` checks that the speaker is a real workspace member and an admin. The transcript action is especially careful: it refuses shared external channels and records the acknowledgement instead of silently revealing private content.

#### Function details

##### `web_extension`  (lines 38–45)

```
def web_extension() -> ExtensionContext
```

**Purpose**: Creates the web extension’s own context so code can read and write the web audience records. This matters because the web portal’s access list lives in the extension’s private store, not in a global shared table.

**Data flow**: It takes no input. It builds a scoped store for the `web` extension and an empty credential-access object, then returns an `ExtensionContext` that can open transactions and reach that store.

**Call relations**: Surface code uses this when it needs the web extension’s private storage while answering portal requests. It hands back the context used by the audience logic and admin actions to read or change grant rows.

*Call graph*: 3 external calls (__init__, __init__, __init__).


##### `_grant_key`  (lines 48–49)

```
def _grant_key(agent_id: UUID, email: str) -> str
```

**Purpose**: Builds the storage key for one web access grant. It makes sure the same email always maps to the same key by trimming spaces and lowercasing it.

**Data flow**: It receives an agent id and an email address. It turns them into a string shaped like an audience record path, then returns that string for storage lookup, creation, or deletion.

**Call relations**: _grant uses this key when writing a new access record. _revoke uses the same key shape when deleting that record, so grant and revoke talk about exactly the same stored row.

*Call graph*: called by 2 (_grant, _revoke).


##### `granted_emails`  (lines 52–59)

```
async def granted_emails(store: ScopedStore) -> dict[UUID, tuple[str, ...]]
```

**Purpose**: Reads all explicit web access grants and groups them by agent. This is useful for an administration view that wants to show who has been granted access to each agent.

**Data flow**: It receives a scoped store. It lists every stored audience record, splits each key into an agent id and email address, groups emails under their agents, sorts each email list, and returns a dictionary from agent id to email tuple.

**Call relations**: This reads the same records that the grant and revoke actions write and delete. It is the read-side companion to those admin actions, giving the portal an overview of current grants.

*Call graph*: calls 1 internal fn (list); 1 external calls (UUID).


##### `_granted_agent_ids`  (lines 62–69)

```
async def _granted_agent_ids(store: ScopedStore, email: str) -> frozenset[UUID]
```

**Purpose**: Finds the private agents explicitly granted to one email address. It answers the narrow question: “Which agent ids have a stored grant for this member?”

**Data flow**: It receives a scoped store and an email address. It normalizes the email, scans all audience grant records, keeps the agent ids whose stored email matches, and returns them as a frozen set.

**Call relations**: web_audience calls this while building a member’s portal view. The returned agent ids are one of the reasons a non-admin member may see an otherwise private agent.

*Call graph*: calls 1 internal fn (list); called by 1 (web_audience); 1 external calls (UUID).


##### `WebAudience.allows`  (lines 86–87)

```
def allows(self, agent_id: UUID) -> bool
```

**Purpose**: Checks whether this audience may reach a specific agent in the normal portal access list. It is a quick yes-or-no permission check.

**Data flow**: It receives an agent id and reads the `agents` stored on the `WebAudience` object. If any listed agent has that id, it returns true; otherwise it returns false. It does not change anything.

**Call relations**: The web surface calls this while resolving existing chats, new chats, and chat routing. It lets those routes refuse an agent before opening or creating a conversation for the user.

*Call graph*: called by 3 (_existing_chat_target, _new_chat_target, _resolve_chat).


##### `WebAudience.allows_chat`  (lines 89–90)

```
def allows_chat(self, agent_id: UUID) -> bool
```

**Purpose**: Checks whether this audience may chat with a specific agent, including special conversation-only access. This is slightly wider than ordinary browsing access.

**Data flow**: It receives an agent id and compares it against `chat_agents`, which combines normal agents with allowed conversation agents. It returns true if the id appears there, false otherwise.

**Call relations**: This method supports code that needs chat-specific permission rather than general portal visibility. It relies on the `chat_agents` property to include the extra conversation-based agents.


##### `WebAudience.chat_agents`  (lines 93–94)

```
def chat_agents(self) -> tuple[AgentSummary, ...]
```

**Purpose**: Returns the full set of agents this member may chat with. It combines agents they can generally reach with agents they can reach because of private extension conversations.

**Data flow**: It reads the object’s `agents` and `conversation_agents` tuples. It returns a new tuple containing both groups in order and does not modify the object.

**Call relations**: WebAudience.allows_chat uses this property for its yes-or-no check. It keeps the chat-specific widening in one place, so callers do not have to remember to combine the two lists themselves.


##### `web_audience`  (lines 97–127)

```
async def web_audience(surface: SurfaceContext, extension: ExtensionContext, email: str) -> WebAudience
```

**Purpose**: Builds the complete web portal audience for one signed-in email address. This is the main function that turns workspace membership, admin status, visibility, ownership, grants, and private conversation access into a single permission object.

**Data flow**: It receives a surface context, an extension context, and an email address. It normalizes the email, reads the workspace seat snapshot, finds the matching seated member, lists agents, reads explicit grants, asks which agents have member-private extension conversations, and returns a `WebAudience`. If the email is not a seated member, it returns an empty audience.

**Call relations**: Portal request code calls this when it needs to know what a member can see or open. It delegates stored grant lookup to `_granted_agent_ids`, reads workspace seating through `Seats`, and returns the object later used by surface routing checks such as `WebAudience.allows`.

*Call graph*: calls 4 internal fn (transaction, list_agents, member_extension_agent_ids, _granted_agent_ids); 2 external calls (__init__, __init__).


##### `_refusal`  (lines 137–138)

```
def _refusal(text: str) -> ToolResult
```

**Purpose**: Creates a standard error result for a tool action that must say no. It keeps refusal replies consistent and marks them as errors.

**Data flow**: It receives a plain text message. It wraps that message in `TextContent`, places it in a `ToolResult`, marks the result as an error, and returns it.

**Call relations**: _gate and `_read_private_transcript` call this whenever a speaker is not allowed to continue or the requested target is invalid. The calling action then returns the refusal instead of making any change.

*Call graph*: called by 2 (_gate, _read_private_transcript); 2 external calls (__init__, __init__).


##### `_target_agent`  (lines 141–148)

```
def _target_agent(ctx: ToolContext) -> tuple[UUID, str]
```

**Purpose**: Decides which agent an access action applies to. If the action named an agent, it uses that one; otherwise it uses the agent currently running the turn.

**Data flow**: It receives a tool context. It checks that the tool was dispatched with a target, then returns a pair: the chosen agent id and a human-friendly label such as the agent’s name or `this agent`.

**Call relations**: _grant, `_revoke`, and `_read_private_transcript` call this after their permission checks. It gives each action the exact agent id for storage or audit work, plus wording for the user-facing reply.

*Call graph*: called by 3 (_grant, _read_private_transcript, _revoke).


##### `_gate`  (lines 151–166)

```
async def _gate(ctx: ToolContext, extension: ExtensionContext) -> ToolResult | SeatEntry
```

**Purpose**: Performs the shared safety checks before changing a member’s web access. It confirms the speaker is a workspace member, is an admin, and that the target member actually exists.

**Data flow**: It receives a tool context and extension context. It reads the speaker identity and admin status, parses the target member id from the tool target, loads the workspace seat snapshot, and returns either the matching member entry or an error `ToolResult` explaining why the action is refused.

**Call relations**: _grant and `_revoke` both call this before touching grant records. By centralizing the checks here, both actions enforce the same admin-only rule and target-member validation.

*Call graph*: calls 3 internal fn (transaction, speaker_is_admin, _refusal); called by 2 (_grant, _revoke); 2 external calls (__init__, UUID).


##### `_grant`  (lines 169–186)

```
async def _grant(ctx: ToolContext, args: WebAccessInput) -> ToolResult
```

**Purpose**: Implements the admin tool action that gives a workspace member access to an agent in the web portal. It writes the explicit grant record used later by audience calculation.

**Data flow**: It receives a tool context and empty validated input. It checks that an extension context exists, runs `_gate`, chooses the target agent with `_target_agent`, handles the special case where the main agent already reaches everyone, writes a grant row keyed by agent id and member email, and returns a success message.

**Call relations**: This function is registered as the handler for the `grant_web_access` tool. When an admin invokes that tool on a member, `_grant` uses `_gate` for permission, `_grant_key` for the storage address, and then updates the extension store so future `web_audience` calls include that agent.

*Call graph*: calls 4 internal fn (agent_is_main, _gate, _grant_key, _target_agent); 2 external calls (__init__, __init__).


##### `_revoke`  (lines 189–209)

```
async def _revoke(ctx: ToolContext, args: WebAccessInput) -> ToolResult
```

**Purpose**: Implements the admin tool action that removes a member’s explicit web portal access to an agent. It deletes the stored grant record, while explaining cases where access still remains for other reasons.

**Data flow**: It receives a tool context and empty validated input. It checks for an extension context, runs `_gate`, chooses the target agent with `_target_agent`, normalizes the member email, deletes the matching grant key, and returns a message. If the target is the main agent, it warns that the member still reaches it because main is available to every member.

**Call relations**: This function is registered as the handler for the `revoke_web_access` tool. It mirrors `_grant`: both share `_gate`, `_target_agent`, and `_grant_key`, but this one removes the row that `_grant` would create.

*Call graph*: calls 4 internal fn (agent_is_main, _gate, _grant_key, _target_agent); 2 external calls (__init__, __init__).


##### `_read_private_transcript`  (lines 220–256)

```
async def _read_private_transcript(ctx: ToolContext, args: PrivateTranscriptInput) -> ToolResult
```

**Purpose**: Implements the admin tool action that acknowledges opening another member’s private conversation transcript. It does not fetch the transcript here; it records the permission/audit event that lets the portal open it.

**Data flow**: It receives a tool context and empty validated input. It checks that the speaker is a member and an admin, refuses use from a foreign/shared audience, reads the target conversation id, chooses the relevant agent, and asks `record_transcript_access` to store the acknowledgement. It returns either a refusal or a message saying whose private conversation was opened and that the access was recorded.

**Call relations**: This function is registered as the handler for the `read_private_transcript` tool. It uses `_refusal` for all denied cases and `_target_agent` to bind the acknowledgement to the correct agent before handing the actual audit recording to the surface helper.

*Call graph*: calls 3 internal fn (speaker_is_admin, _refusal, _target_agent); 4 external calls (__init__, __init__, record_transcript_access, UUID).


### `core/src/ufo/runtime/seats.py`

`domain_logic` · `cross-cutting access checks and member changes`

A workspace member can exist even when they are not allowed to use the agent. This file keeps those two ideas separate: the member row is the person’s identity and history, while the seat is their current permission to be answered. Think of it like a club roster and an active badge. Removing the badge does not erase the person from the roster.

The central type is `Seats`, which is tied to one workspace. It can check whether a caller’s authority is still live, list the current seating state, grant a seat back to a member, or revoke a seat. It also enforces an important safety rule: the last seated admin cannot be unseated, because admins use chat to restore seats. If the last admin lost access, nobody could fix access from inside the system.

The rest of the file supports member lookup and creation. It normalizes email addresses, finds workspaces by signup email domain, creates member rows safely when two callers race to add the same person, and answers simple questions like “is this seated member an admin?” Database writes are deliberately centralized here so every entry point follows the same rules instead of each feature inventing its own version.

#### Function details

##### `SeatSnapshot.seated`  (lines 58–59)

```
def seated(self) -> int
```

**Purpose**: This property counts how many members in a snapshot currently have seats. It gives callers a simple number instead of making them inspect every member entry themselves.

**Data flow**: It reads the snapshot’s member entries → checks each entry’s `seated` flag → returns the count of entries whose flag is true. It does not change anything.

**Call relations**: After `Seats.snapshot` builds a picture of the workspace’s members, this property can be used wherever the caller needs the active seated count from that picture.


##### `Seats.admits`  (lines 70–87)

```
async def admits(self, connection: AsyncConnection, authority: ExecutionAuthority) -> bool
```

**Purpose**: This checks whether a given execution authority is allowed to act in this workspace right now. A workspace-wide authority is always accepted, but a member authority must still belong to a seated member.

**Data flow**: It receives a database connection and an authority object → if the authority is workspace-level, it returns true → if it is member-level, it looks up that member in this workspace and checks whether `seated_at` is set → it returns true only when the member exists and still has a seat. If the authority is the wrong kind, it raises an error.

**Call relations**: Other runtime boundaries call this when they need to know whether an already-issued authority is still live. It hands the final yes-or-no answer back after reading the member table through the shared database connection.

*Call graph*: 2 external calls (execute, select).


##### `Seats.all_seated`  (lines 89–109)

```
async def all_seated(self, connection: AsyncConnection, member_ids: Collection[UUID]) -> bool
```

**Purpose**: This checks whether a whole group of member IDs all still have seats in the workspace. It is built for moments when one running turn may involve several speakers and all of them must still be allowed.

**Data flow**: It receives a connection and a collection of member IDs → an empty collection is treated as safely true → otherwise it asks the database how many of those IDs are seated members of this workspace → it returns true only if the count matches the number requested.

**Call relations**: The runtime can call this during repeated access checks, parked-turn checks, or dispatch sweeps. Instead of checking one person at a time, it asks the database once for the whole set and returns a single answer.

*Call graph*: 2 external calls (execute, select).


##### `Seats.snapshot`  (lines 111–134)

```
async def snapshot(self, connection: AsyncConnection) -> SeatSnapshot
```

**Purpose**: This builds a read-only picture of all members in the workspace and whether each one is seated and an admin. It is useful for showing or reporting the current seating state.

**Data flow**: It receives a connection → reads all member rows for this workspace in creation order → turns each row into a `SeatEntry` with ID, email, seated status, and admin status → wraps those entries in a `SeatSnapshot` and returns it.

**Call relations**: Callers use this when they need the full seating roster. The returned `SeatSnapshot` then provides simple access to the entries and to the seated count through `SeatSnapshot.seated`.

*Call graph*: 4 external calls (__init__, __init__, execute, select).


##### `Seats.grant`  (lines 136–146)

```
async def grant(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: This restores access for an existing workspace member by giving them a seat. If the member is already seated, it quietly does nothing.

**Data flow**: It receives a connection and an email address → finds the matching member in this workspace → if that member already has `seated_at`, nothing changes → otherwise it updates the member row with the current time as the new seat time and updates the modification time.

**Call relations**: Administrative flows call this when an admin wants to restore someone’s access. It relies on `Seats._member_by_email` to make sure the email belongs to a member of this workspace before writing the seat change.

*Call graph*: calls 1 internal fn (_member_by_email); 2 external calls (execute, update).


##### `Seats.revoke`  (lines 148–171)

```
async def revoke(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: This removes a member’s seat, which stops the agent from answering that member. It refuses to revoke the final seated admin, because that would leave the workspace unable to restore seats through chat.

**Data flow**: It receives a connection and an email address → locks the workspace row so competing revocations happen in a safe order → finds the member by email → if the member is already unseated, nothing changes → if the member is the only seated admin, it raises `LastAdminSeatRevocation` → otherwise it clears `seated_at` and updates the modification time.

**Call relations**: Administrative flows call this when removing access. It uses `Seats._member_by_email` to find the target and `Seats._seated_admin_count` to protect the last-admin rule before handing the final database update to SQLAlchemy.

*Call graph*: calls 2 internal fn (_member_by_email, _seated_admin_count); 4 external calls (__init__, execute, select, update).


##### `Seats._member_by_email`  (lines 173–190)

```
async def _member_by_email(self, connection: AsyncConnection, email: str) -> tuple[UUID, datetime | None, bool]
```

**Purpose**: This private helper finds a member of this workspace by email address. It centralizes the lookup used by grant and revoke so they both treat email matching the same way.

**Data flow**: It receives a connection and an email address → trims and lowercases the email for comparison → searches only inside this workspace → returns the member ID, current seat time, and admin flag. If no matching member exists, it raises `UnknownMember`.

**Call relations**: `Seats.grant` and `Seats.revoke` call this before changing a seat. It gives them the exact member facts they need and stops the flow early if the email does not name a workspace member.

*Call graph*: called by 2 (grant, revoke); 3 external calls (__init__, execute, select).


##### `Seats._seated_admin_count`  (lines 192–201)

```
async def _seated_admin_count(self, connection: AsyncConnection) -> int
```

**Purpose**: This private helper counts how many admins in the workspace currently have seats. It exists to enforce the rule that at least one seated admin must remain.

**Data flow**: It receives a connection → asks the database for the number of member rows in this workspace that are both seated and marked as admin → returns that number.

**Call relations**: `Seats.revoke` calls this only when the target member is an admin. The count tells revoke whether it is safe to clear the target’s seat or whether doing so would lock everyone out of seat management.

*Call graph*: called by 1 (revoke); 2 external calls (execute, select).


##### `email_domain`  (lines 204–217)

```
def email_domain(email: str) -> str
```

**Purpose**: This extracts the domain part of a valid email-shaped string, such as `example.com` from `person@example.com`. If the value is malformed, it returns an empty string so bad input cannot accidentally match or create a member.

**Data flow**: It receives an email string → trims and lowercases it → checks that it has exactly one `@`, has text on both sides, and contains no whitespace → returns the domain if valid, otherwise returns an empty string.

**Call relations**: Member creation and workspace-domain lookup functions all use this as the shared email shape gate. That keeps the system from accepting one kind of email in one path and rejecting it in another.

*Call graph*: called by 4 (create_member, workspace_by_domain, workspace_domain, workspace_subject).


##### `signup_workspace_id`  (lines 220–222)

```
def signup_workspace_id(subject: str) -> UUID
```

**Purpose**: This deterministically turns a signup subject into the hosted workspace ID for that subject. Deterministic means the same subject always produces the same UUID.

**Data flow**: It receives a subject string → lowercases it → feeds it into UUID version 5 generation using the DNS namespace → returns the resulting UUID.

**Call relations**: Workspace subject and domain lookup helpers use this to tell whether a workspace was keyed by a founder’s exact email address or by an email domain.

*Call graph*: called by 3 (workspace_by_domain, workspace_domain, workspace_subject); 1 external calls (uuid5).


##### `workspace_subject`  (lines 225–234)

```
def workspace_subject(first_email: str, workspace_id: UUID) -> str
```

**Purpose**: This decides what signup subject identifies a seated workspace: either the first member’s exact email address or that email’s domain. It keeps labels, invitations, and access checks using the same rule.

**Data flow**: It receives the first member’s email and the workspace ID → computes the workspace ID that would come from the exact email → if that matches, it returns the email itself → otherwise it returns the email’s domain.

**Call relations**: This function combines `signup_workspace_id` and `email_domain` so callers do not re-create the subject rule themselves. Anything that needs to display or check the workspace’s signup subject can call this one shared derivation.

*Call graph*: calls 2 internal fn (email_domain, signup_workspace_id).


##### `workspace_domain`  (lines 237–255)

```
async def workspace_domain(connection: AsyncConnection, workspace_id: UUID) -> str | None
```

**Purpose**: This returns the email domain that represents a workspace, but only when the workspace is truly domain-based. Personal-email workspaces return no domain so a large shared provider like Gmail does not accidentally grant access to unrelated people.

**Data flow**: It receives a connection and workspace ID → reads the earliest-created member’s email → extracts its domain → returns `None` if there is no member, the email is malformed, or the workspace was keyed by the exact email → otherwise returns the domain.

**Call relations**: Callers use this when they need to know whether a workspace has a domain-based signup identity. It relies on `email_domain` for safe parsing and `signup_workspace_id` to separate exact-email workspaces from domain workspaces.

*Call graph*: calls 2 internal fn (email_domain, signup_workspace_id); 2 external calls (execute, select).


##### `workspace_by_domain`  (lines 258–291)

```
async def workspace_by_domain(connection: AsyncConnection, domain: str) -> UUID | None
```

**Purpose**: This finds the workspace addressed by a given email domain, when such a domain-based workspace exists. It deliberately skips personal-email workspaces so shared email providers do not become workspace keys.

**Data flow**: It receives a connection and a domain string → validates and normalizes the domain by pretending it is part of an email → searches the first member of each workspace for a matching email domain → orders possible matches consistently → returns the first workspace whose ID is not based on that first member’s exact email, or `None` if none qualifies.

**Call relations**: Signup or join flows can call this when someone’s verified email domain might map them to an existing workspace. It uses `email_domain` to clean the input and `signup_workspace_id` to filter out personal-mail workspaces before returning a workspace ID.

*Call graph*: calls 2 internal fn (email_domain, signup_workspace_id); 2 external calls (execute, select).


##### `member_by_email`  (lines 294–310)

```
async def member_by_email(connection: AsyncConnection, workspace_id: UUID, email: str) -> UUID | None
```

**Purpose**: This looks up whether a specific email address is already a member of a specific workspace. It only reads; it never creates or seats a member.

**Data flow**: It receives a connection, workspace ID, and email address → trims and lowercases the email for comparison → searches for a member row inside that workspace → returns the member ID if found, otherwise `None`.

**Call relations**: Routes or admission code can use this after an email has been verified. The query is scoped to the workspace from the start, so it does not accidentally read a member from another workspace and then filter afterward.

*Call graph*: 2 external calls (execute, select).


##### `member_is_admin`  (lines 313–324)

```
async def member_is_admin(connection: AsyncConnection, workspace_id: UUID, member_id: UUID) -> bool
```

**Purpose**: This answers whether a given member is currently a seated admin in a workspace. It treats unseated admins as not currently able to exercise admin power.

**Data flow**: It receives a connection, workspace ID, and member ID → searches for that member only if they belong to the workspace and still have a seat → reads the admin flag → returns true if the seated member is an admin, otherwise false.

**Call relations**: Permission checks can call this before allowing admin-only actions. It folds together membership, liveness, and admin status into one database read.

*Call graph*: 2 external calls (execute, select).


##### `create_member`  (lines 327–398)

```
async def create_member(connection: AsyncConnection, workspace_id: UUID, email: str, *, is_admin: bool=False, invited_by: UUID | None=None) -> UUID
```

**Purpose**: This is the single approved way to create a member row. It validates and normalizes the email, creates the member as already seated, records invitation details when present, and safely handles two callers trying to create the same member at once.

**Data flow**: It receives a connection, workspace ID, email, optional admin flag, and optional inviter ID → rejects malformed email addresses → lowercases the email → locks the workspace row to keep competing creates orderly → tries to insert a new member with a fresh UUID and timestamps → if the insert succeeds, returns the new ID → if another caller already created the row, reads and returns the existing ID instead.

**Call relations**: Onboarding, teammate join, invitation, and any future member-creation path are meant to call this rather than writing the member table directly. It calls `email_domain` first so all creation paths share the same email rule, then delegates the actual insert/read work to the database.

*Call graph*: calls 1 internal fn (email_domain); 3 external calls (execute, select, uuid4).


##### `member_workspaces`  (lines 401–409)

```
def member_workspaces() -> WorkspaceCandidates
```

**Purpose**: This builds a workspace-candidate source for jobs that need to visit workspaces with members. It keeps the query inside core because core owns the member table.

**Data flow**: It defines a small query builder that selects distinct workspace IDs from the member table → wraps that query with `owner_candidates` → returns the resulting candidate provider.

**Call relations**: Extensions or background jobs can declare this as their candidate source instead of reaching into core database details. The nested `member_workspaces.with_a_member` function supplies the actual SQL query when the candidate machinery asks for it.

*Call graph*: 1 external calls (owner_candidates).


##### `member_workspaces.with_a_member`  (lines 406–407)

```
def with_a_member() -> sa.Select[tuple[UUID]]
```

**Purpose**: This nested helper describes the database query for “all workspaces that have at least one member.” It is intentionally broad and simple.

**Data flow**: It reads no runtime arguments → builds a SQL select for distinct workspace IDs from the member table → returns that query object for someone else to execute.

**Call relations**: `member_workspaces` passes this helper to `owner_candidates`. The candidate machinery can later call it to get the query it should run when choosing workspaces for a seat-related job.

*Call graph*: 1 external calls (select).


### Conversation and task visibility
Normalizes visibility labels for workspace content and applies them to conversation turns and scheduled task details.

### `core/src/ufo/runtime/turns/audience.py`

`domain_logic` · `conversation turn validation and read/write decisions`

A conversation can belong to different “audiences”: everyone in the workspace, one specific member, a named room, or a room that includes people outside the organization. This file gives those audiences a small shared language. Think of each audience as a label on a folder: if the label is wrong or too broad, private notes could end up in the wrong folder.

The file creates an `Audience` type, which is just text with a clearer meaning, and defines standard prefixes such as `room:` and `foreign:`. It then provides helper functions to build valid audience labels, check labels that came from storage or input, and decide what information an audience is allowed to read.

A key safety rule is that room names and surface names cannot be empty and cannot contain colons, because colons are used as separators inside the label. Another important rule is that an externally shared room reads only its own material, not the workspace-wide shared material. That prevents internal shared context from being recalled into a channel where another organization may be present.

The `narrow_audience` function protects an ongoing conversation from silently changing to an unrelated audience. It allows safe narrowing, such as moving from shared to a specific audience, but raises an error if the requested change would cross a boundary that should stay separate.

#### Function details

##### `conversation_audience`  (lines 14–15)

```
def conversation_audience(member_id: UUID | None) -> Audience
```

**Purpose**: Builds the audience label for a normal conversation. If there is no member, the conversation is workspace-shared; if there is a member ID, the conversation is private to that member.

**Data flow**: It receives either a member UUID or `None`. With `None`, it returns the shared audience label. With a UUID, it combines the member prefix with that ID and returns the resulting audience label.

**Call relations**: Other functions use this as the official way to form member audience labels. `parse_audience` uses it to confirm that a member-shaped string is exactly canonical, and `readable_audiences` uses it when building the list of audiences a member can read.

*Call graph*: called by 2 (parse_audience, readable_audiences).


##### `room_audience`  (lines 18–19)

```
def room_audience(surface: str, room: str) -> Audience
```

**Purpose**: Builds an audience label for a normal room inside a given surface, such as a chat area or other conversation surface. This marks content as belonging to that specific room.

**Data flow**: It receives a surface name and a room name. It passes them, along with the normal room prefix, to the shared room-label builder and returns the validated audience label.

**Call relations**: This is the public helper for internal room audiences. It delegates the common validation and formatting work to `_room_audience`, and `parse_audience` uses it to check whether a stored or incoming room label is valid.

*Call graph*: calls 1 internal fn (_room_audience); called by 1 (parse_audience).


##### `foreign_room_audience`  (lines 22–23)

```
def foreign_room_audience(surface: str, room: str) -> Audience
```

**Purpose**: Builds an audience label for a room that is shared with an outside organization. This distinction matters because foreign rooms must not automatically see internal workspace-shared context.

**Data flow**: It receives a surface name and a room name. It passes them, along with the foreign-room prefix, to the shared room-label builder and returns the validated audience label.

**Call relations**: This is the public helper for externally shared room audiences. It relies on `_room_audience` for the common checks, and `parse_audience` uses it to verify foreign-room labels.

*Call graph*: calls 1 internal fn (_room_audience); called by 1 (parse_audience).


##### `_room_audience`  (lines 26–29)

```
def _room_audience(prefix: str, surface: str, room: str) -> Audience
```

**Purpose**: Does the shared work of building room-style audience labels. It also enforces the rule that the surface and room parts must be simple names, not empty and not containing colons.

**Data flow**: It receives a prefix, a surface name, and a room name. It checks that the surface and room are usable as safe label parts. If either part is invalid, it raises an error; otherwise it returns one combined audience label in the form `prefix + surface + ':' + room`.

**Call relations**: This is an internal helper used by both `room_audience` and `foreign_room_audience`. Those two functions choose the meaning of the room, while this helper guarantees the shared format is safe and consistent.

*Call graph*: called by 2 (foreign_room_audience, room_audience).


##### `parse_audience`  (lines 32–55)

```
def parse_audience(value: str) -> Audience
```

**Purpose**: Checks that a text value is a valid audience label and returns it as an `Audience`. This is the gatekeeper for labels that may have come from outside the trusted code path.

**Data flow**: It receives raw text. It first accepts the exact shared audience label. Otherwise it splits the text at colons, checks whether it looks like a member, room, or foreign-room audience, and rebuilds the expected label using the official helper functions. If the text does not match one of the allowed forms exactly, it raises an error; if it does, it returns the audience.

**Call relations**: This function is the validator that other safety-sensitive helpers rely on. `audience_member`, `audience_subjects`, and `narrow_audience` all call it before making decisions, so they work only with known-good audience labels. It calls `conversation_audience`, `room_audience`, and `foreign_room_audience` to compare against the canonical forms, and uses `UUID` parsing to confirm member IDs are real UUIDs.

*Call graph*: calls 3 internal fn (conversation_audience, foreign_room_audience, room_audience); called by 3 (audience_member, audience_subjects, narrow_audience); 1 external calls (UUID).


##### `readable_audiences`  (lines 58–63)

```
def readable_audiences(member_id: UUID) -> tuple[Audience, ...]
```

**Purpose**: Returns the conversation audiences a member is allowed to read in normal member-facing views. That means the workspace-shared audience plus that member’s own private audience.

**Data flow**: It receives a member UUID. It creates that member’s private audience label and returns a pair containing the shared audience and the member audience.

**Call relations**: This function uses `conversation_audience` to create the member-specific label. It is meant to be the single definition for member-facing reads, so room and foreign-room audiences are intentionally left out because the workspace does not know room membership from this label alone.

*Call graph*: calls 1 internal fn (conversation_audience).


##### `audience_member`  (lines 66–70)

```
def audience_member(audience: Audience) -> UUID | None
```

**Purpose**: Extracts the member ID from a member-specific audience label. If the audience is shared, room-based, or foreign-room-based, it reports that there is no member ID.

**Data flow**: It receives an `Audience`. It first validates it with `parse_audience`. If the validated label starts with the member prefix, it removes that prefix and turns the rest into a UUID. Otherwise it returns `None`.

**Call relations**: This function depends on `parse_audience` so it never tries to interpret a malformed label. It is useful when later code needs to know whether an audience represents a single member or some broader conversation space.

*Call graph*: calls 1 internal fn (parse_audience); 1 external calls (UUID).


##### `audience_subjects`  (lines 73–80)

```
def audience_subjects(audience: Audience) -> frozenset[str]
```

**Purpose**: Decides which stored subjects a conversation audience may read from. A subject is another label used to group facts or memory that can be recalled into a conversation.

**Data flow**: It receives an audience label and validates it. For a foreign-room audience, it returns only that exact audience as a readable subject. For all other audiences, it returns both the shared subject and the audience’s own subject.

**Call relations**: This function uses `parse_audience` before making the access decision. Its most important relationship is with the privacy model: by treating foreign rooms differently, it prevents workspace-shared facts from being pulled into externally shared channels.

*Call graph*: calls 1 internal fn (parse_audience).


##### `narrow_audience`  (lines 83–98)

```
def narrow_audience(current: Audience, requested: Audience) -> Audience
```

**Purpose**: Chooses a safe final audience when existing conversation context and a newly requested audience both exist. It allows only changes that keep or narrow the audience, and rejects unrelated audience switches.

**Data flow**: It receives the current audience and the requested audience. It validates both. If they are the same, or if the request is only for shared context, it keeps the current audience. If the current audience is shared, it accepts the requested audience. If both are room-style audiences for the same surface and room, it chooses the safer or more specific room form according to the foreign-room rule. If none of these safe cases applies, it raises an error.

**Call relations**: This function relies on `parse_audience` to make sure both labels are valid before comparing them. It is used as a guardrail during conversation flow: when something asks to change audience, this function decides whether that request is a harmless narrowing or a dangerous jump to a different disclosure boundary.

*Call graph*: calls 1 internal fn (parse_audience); 1 external calls (partition).


### `core/src/ufo/runtime/turns/subjects.py`

`data_model` · `cross-cutting visibility checks`

This file is about visibility: deciding whether a piece of content is meant for the whole workspace or only for one member. It uses short string “subjects” as labels. The special subject `shared` means the content is readable by every member of the workspace. A member-specific subject starts with `member:` followed by that member’s unique ID, like putting a name on an envelope.

The file keeps these rules in one place so other parts of the system do not invent slightly different formats. Without it, one area might write `member-123` while another expects `member:123`, and privacy checks could fail or content could become unreadable.

There are two small helpers. `member_subject` builds the correct member-only label from a member UUID, which is a universally unique identifier. `subject_shared` checks whether a subject is exactly the shared subject. Its comment also explains an important boundary: some audiences, such as a room or externally shared channel, are not treated as “shared” here because this check is specifically about workspace-member readability, not every possible audience type.

#### Function details

##### `member_subject`  (lines 9–10)

```
def member_subject(member_id: UUID) -> str
```

**Purpose**: This function turns a workspace member’s unique ID into the standard subject string for content meant for that member. It prevents different parts of the code from formatting member visibility labels in different ways.

**Data flow**: It receives a UUID for a member. It converts that ID into text and places `member:` in front of it. It returns the finished subject string, such as `member:<uuid>`, without changing anything else.

**Call relations**: When other code needs to mark content as readable by one specific member, it can call this helper instead of building the text by hand. The resulting subject can then be stored, compared, or passed into visibility logic elsewhere.


##### `subject_shared`  (lines 13–18)

```
def subject_shared(subject: str) -> bool
```

**Purpose**: This function answers the question: “Does this subject mean readable by every member of the workspace?” It is a small but important privacy check.

**Data flow**: It receives a subject string. It compares that string with the exact shared label, `shared`. It returns `true` if they match and `false` otherwise; it does not modify any data.

**Call relations**: When other parts of the system are deciding who may read content, they can call this function to recognize the workspace-wide case. It deliberately only identifies the `shared` subject, leaving member-specific and other audience types to be handled by their own rules.


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/visibility.py`

`domain_logic` · `request handling`

Scheduled tasks can post their results into different kinds of conversations. Some conversations are shared with the whole workspace, while others belong to a single member. This file answers a simple but important question: “Can this person see what the task was asked to do?” Without this check, a private task could accidentally reveal its prompt or description to the wrong person.

The rule is intentionally tied to the task’s audience, meaning the conversation or subject where the task reports. If the audience is shared, then everyone can see the task content, because everyone can already see the replies it posts there. If the audience is private to one member, then only that member should see it. There is also a fallback: if the task was created by the current member, that member may see it too.

One important detail is that a missing creator does not make the task public. A creatorless task is still judged by where it reports. This keeps private conversations private. The file is small, but it is a gatekeeper: like checking the address on an envelope before opening it, it makes sure task content is only readable by the people the task is effectively speaking to.

#### Function details

##### `task_content_visible`  (lines 7–20)

```
def task_content_visible(listed: ListedTask, member_id: UUID | None) -> bool
```

**Purpose**: Decides whether a given member can read a listed scheduled task’s prompt and description. It is used when the system needs to show or hide sensitive task content based on the task’s audience and ownership.

**Data flow**: It receives a listed task and an optional member ID. First it checks whether the task reports to a shared audience; if so, it returns true. If there is no current member, it returns false unless the audience was shared. If there is a member, it checks whether the task reports directly to that member, and finally whether that member created the task. The result is a yes-or-no answer, and the function does not change any data.

**Call relations**: When this visibility decision is needed, this function asks the subject helpers two questions: whether the task’s audience is shared, and what the current member’s private subject would look like. It uses those answers to make the final access decision itself, rather than handing the decision off elsewhere.

*Call graph*: 2 external calls (member_subject, subject_shared).

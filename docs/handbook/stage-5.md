# Incoming event normalization, authentication, and authorization  `stage-5`

This stage is an early gatekeeper for anything coming into the system. Before a request, chat event, connector callback, form post, or account-linking return is allowed to do real work, it proves who sent it, what workspace it belongs to, and what that person or service is allowed to use. This protects the rest of the system from confused identities, stale sessions, bad signatures, or access to the wrong workspace.

The account-link and OAuth callback intake is the part that connects outside services. It sends members to approval pages, receives the callback, checks that the returned account or app installation belongs to the right user or organization, and records which agents may use the connection.

The onboarding control file is the trusted private doorway for creating or finding workspaces, adding signed-in members, and listing where a person may enter. The web audience file controls visibility inside the web portal: which members can see and chat with agents, how admins grant or remove access, and when private transcript access is recorded. Together, these parts turn messy outside signals into safe internal identities and permissions.

## Sub-stages

- [Account-link and OAuth callback intake](stage-5.1.md) `stage-5.1` — 6 files

## Files in this stage

### Workspace access control
Trusted onboarding and web audience rules authenticate members, bind them to workspaces, and determine which agents or transcripts they may access.

### `core/src/ufo/onboard_control.py`

`orchestration` · `request handling`

This file is the gatekeeper for hosted onboarding. When someone signs in through the external onboarding flow, the Rust control plane calls these internal routes to answer questions like: “Which workspace should this email enter?”, “Should a new workspace be created?”, and “Is this person an admin?” Without this file, several important rules could drift apart: who becomes the first admin, how signup credit is granted, what prompt the default agent starts with, and how untrusted intake-form text is kept from becoming instructions to the agent.

The API is deliberately private. Every route lives under `/internal/onboard` and must include a bearer token. That token is checked before any database work happens.

Most routes operate inside one workspace at a time, using the normal workspace-scoped database path. Two routes are different: `choices` and `fleet` need to look across workspaces, so they use the owner-level database path and log a warning each time. That is intentional: cross-workspace reads are powerful and should be visible to operators.

A key safety detail is the handling of intake form answers. The form is public, so its text is treated like a note from a stranger, not a trusted instruction. The file wraps that text in an “untrusted data” wall and also defuses prompt-template braces so a malicious or accidental answer cannot break every future agent turn for that workspace.

#### Function details

##### `_inert`  (lines 130–144)

```
def _inert(answer: str) -> str
```

**Purpose**: This helper makes a public form answer safe to place inside an agent prompt. It removes the special doubled braces used by the prompt template system, so a user’s text cannot accidentally or deliberately be read as a variable.

**Data flow**: It receives one text answer from the intake form. It repeatedly replaces `{{` with `{` and `}}` with `}` until no doubled braces remain. It returns the cleaned text, keeping it readable while making it unable to contain live prompt-template markers.

**Call relations**: This is used by `agent_prompt` when intake-form text is included in the default agent’s prompt. It is the first safety step before that text is wrapped by the untrusted-content wall.

*Call graph*: called by 1 (agent_prompt).


##### `agent_prompt`  (lines 147–166)

```
def agent_prompt(profile: SignupProfile | None) -> str
```

**Purpose**: This builds the starting prompt for the main agent in a newly created workspace. If the signup flow collected business goals, it adds them as background information, but clearly marks them as untrusted form data.

**Data flow**: It receives either no profile or a profile containing `business` and `goals` text. With no profile, it returns the normal default agent prompt unchanged. With a profile, it cleans each answer with `_inert`, formats them into a short intake note, passes that note through `wall` so it is labeled as untrusted data, and returns the full signup prompt.

**Call relations**: `OnboardControl._seat` calls this while creating the workspace’s main agent. The function hands off to `_inert` for brace defusing and to `ufo.untrusted.wall` for the stronger boundary that tells the agent this text is not an instruction.

*Call graph*: calls 1 internal fn (_inert); called by 1 (_seat); 1 external calls (wall).


##### `deterministic_workspace_id`  (lines 169–172)

```
def deterministic_workspace_id(domain: str) -> UUID
```

**Purpose**: This turns an email domain into the same workspace ID every time. It lets separate parts of the system agree that a verified domain, such as a company domain, names one predictable workspace.

**Data flow**: It receives a domain string, lowercases it, and uses UUID version 5, which creates a stable UUID from a namespace plus a name. The output is a UUID that will always be the same for the same domain.

**Call relations**: `OnboardControl._choices` calls this when deciding which workspace a verified domain would map to. It relies on the standard `uuid5` function to produce the stable identifier.

*Call graph*: called by 1 (_choices); 1 external calls (uuid5).


##### `_labelled`  (lines 175–203)

```
def _labelled(rows: Sequence[sa.RowMapping], domain: str) -> list[WorkspaceChoice]
```

**Purpose**: This turns raw database rows into a friendly list of workspace choices for a signing-in member. It also refuses confusing cases where one domain appears to point at more than one workspace.

**Data flow**: It receives database rows and the verified domain. It separates rows that came from domain matching from rows that came from direct membership, checks that there is at most one domain-matched workspace, chooses human-readable labels, adds a short UUID prefix when two labels would otherwise look identical, and returns `WorkspaceChoice` objects.

**Call relations**: `OnboardControl._choices` calls this after fetching possible workspaces. If the data is ambiguous, it raises an HTTP conflict error instead of guessing; otherwise it hands back the cleaned list that becomes the API response.

*Call graph*: called by 1 (_choices); 2 external calls (__init__, HTTPException).


##### `OnboardControl.router`  (lines 213–219)

```
def router(self) -> APIRouter
```

**Purpose**: This builds the FastAPI router for the internal onboarding API. It gives the four onboarding routes a shared URL prefix and attaches the token check to all of them.

**Data flow**: It reads the `OnboardControl` instance, especially its `_guard` method. It creates an API router at `/internal/onboard`, adds the `/seat`, `/membership`, `/choices`, and `/fleet` routes, and returns the router for the main web application to mount.

**Call relations**: This is the setup point for the file’s request handlers. FastAPI calls the registered route methods later when matching incoming internal HTTP requests, and it uses `_guard` as a shared dependency before allowing those methods to run.

*Call graph*: 2 external calls (APIRouter, Depends).


##### `OnboardControl._guard`  (lines 221–223)

```
async def _guard(self, authorization: Annotated[str, Header()]='') -> None
```

**Purpose**: This protects every onboarding route with a shared secret token. If the request does not prove it is from the trusted control plane, the route is rejected before it can read or write anything.

**Data flow**: It receives the HTTP `Authorization` header. It compares the header with `Bearer ` plus the configured control token. If they match, it returns normally; if not, it raises a 401 unauthorized error.

**Call relations**: `OnboardControl.router` attaches this guard to the router as a dependency, so FastAPI runs it before the route handlers. When it raises an HTTP error, the onboarding workflow stops immediately.

*Call graph*: 1 external calls (HTTPException).


##### `OnboardControl._seat`  (lines 225–304)

```
async def _seat(self, request: SeatRequest) -> EnsuredWorkspace
```

**Purpose**: This creates or joins the workspace for a verified signup and ensures the member has a seat there. It is where the main onboarding effects happen: workspace creation, first admin selection, default agent creation, and one-time signup credit for a newly founded workspace.

**Data flow**: It receives a `SeatRequest` containing a workspace ID, domain, email, and optional intake profile. It normalizes the email and domain, enters that workspace’s database context, tries to create the workspace, locks it so competing requests cannot race, checks that an existing workspace still belongs to the same domain, creates the member, creates the default main agent if needed, and grants signup credit only if this call founded the workspace. It returns the workspace ID and whether the member is an admin.

**Call relations**: This is called by FastAPI for the `/seat` route after `_guard` passes. It calls `agent_prompt` to build the main agent’s prompt, `create_member` to add the person, `credit` and `set_reserve` to fund a brand-new workspace, and workspace/database helpers to keep all writes scoped to the correct workspace.

*Call graph*: calls 1 internal fn (agent_prompt); 11 external calls (__init__, HTTPException, insert, select, credit, set_reserve, workspace_tx, create_member, email_domain, ws (+1 more)).


##### `OnboardControl._membership`  (lines 306–326)

```
async def _membership(self, workspace_id: UUID, email: str) -> Membership
```

**Purpose**: This checks whether an email is still a member of a specific workspace and whether that member is an admin. It prevents a sign-in flow from silently recreating a member who was removed between the workspace-choice step and the final selection.

**Data flow**: It receives a workspace ID and email address. It normalizes the email, opens a transaction scoped to that workspace, looks for that member’s admin flag, and then either returns `Membership(admin=...)` or raises a 404 error if the member is no longer present.

**Call relations**: FastAPI calls this for the `/membership` route after the shared token guard succeeds. It uses the workspace context and workspace transaction helpers so the lookup obeys the normal per-workspace database boundary.

*Call graph*: 5 external calls (__init__, HTTPException, select, workspace_tx, ws).


##### `OnboardControl._choices`  (lines 328–345)

```
async def _choices(self, email: str, domain: str) -> WorkspaceChoices
```

**Purpose**: This lists the workspaces a verified email address may enter. It combines explicit memberships for that email with the one workspace named by the verified email domain.

**Data flow**: It receives an email and domain, normalizes both, logs that a cross-workspace read is happening, opens the owner-level database connection, runs the workspace-choice SQL query, and converts the raw rows into user-facing choices with `_labelled`. It returns a `WorkspaceChoices` response.

**Call relations**: FastAPI calls this for the `/choices` route after `_guard` passes. It calls `deterministic_workspace_id` to find the domain-named workspace, uses `owner_tx` because the query must look across tenants, and then hands the result to `_labelled` to make the choices safe and understandable.

*Call graph*: calls 2 internal fn (_labelled, deterministic_workspace_id); 4 external calls (__init__, text, owner_tx, warn).


##### `OnboardControl._fleet`  (lines 347–354)

```
async def _fleet(self) -> Fleet
```

**Purpose**: This reports the total number of workspaces, described as one “craft” per workspace. It supports a landing-page style view of the live fleet size.

**Data flow**: It takes no request-specific business data. It logs that a cross-workspace read is happening, opens an owner-level database connection, counts rows in the workspace table, and returns that count inside a `Fleet` response.

**Call relations**: FastAPI calls this for the `/fleet` route after the token guard succeeds. Like `_choices`, it uses `owner_tx` because counting all workspaces crosses normal workspace boundaries, and it logs the read so operators can see that this broader access occurred.

*Call graph*: 4 external calls (__init__, select, owner_tx, warn).


### `extensions/web/ufo_ext_web/audience.py`

`domain_logic` · `request handling and admin tool execution`

The web portal needs a clear answer to a sensitive question: “Is this person allowed to reach this agent or conversation?” This file is that rulebook. It treats email as the member identity for web access, and stores explicit grants as simple rows keyed by agent and email. Without this file, private agents could be exposed too broadly, or legitimate members could be blocked from agents they should be able to use.

The main idea is that admins can reach every agent. Non-admin members can reach workspace-visible agents, agents explicitly granted to their email, and agents they own. There is also a narrower case for chat: a member may be allowed into an agent conversation because of their own private extension conversation, even if that does not make the agent generally visible in the portal.

The file also provides three chat tools. Workspace admins can grant a member access to the current agent, revoke that access, or acknowledge reading another member’s private transcript. The acknowledgement is important because it creates an audit trail: like signing a visitor log before opening a locked file cabinet. The portal later relies on that recorded access when deciding whether the transcript may be shown.

#### Function details

##### `web_extension`  (lines 28–35)

```
def web_extension() -> ExtensionContext
```

**Purpose**: Creates the web extension’s own context so web code can read and write the web extension’s private storage. This is the doorway used when a surface handler needs the audience grant records.

**Data flow**: It takes no input. It builds a scoped store for the web extension and pairs it with an empty credential declaration, then returns an extension context that other code can use for transactions and storage access.

**Call relations**: Surface code can call this when it needs to consult or update the web audience store. Internally it constructs the store, credential access object, and extension context that later functions use.

*Call graph*: 3 external calls (__init__, __init__, __init__).


##### `_grant_key`  (lines 38–39)

```
def _grant_key(agent_id: UUID, email: str) -> str
```

**Purpose**: Builds the storage key for one access grant: one agent and one member email. It makes sure emails are stored in a consistent lowercase, trimmed form so the same person does not get duplicate records because of capitalization or spaces.

**Data flow**: It receives an agent ID and an email address. It trims and lowercases the email, combines it with the audience prefix and agent ID, and returns the exact string key used in storage.

**Call relations**: _grant uses this key when saving an access grant, and _revoke uses the same key when deleting one. This keeps granting and revoking pointed at the same storage row.

*Call graph*: called by 2 (_grant, _revoke).


##### `granted_emails`  (lines 42–49)

```
async def granted_emails(store: ScopedStore) -> dict[UUID, tuple[str, ...]]
```

**Purpose**: Reads all stored web access grants and turns them into an admin-friendly view: each agent mapped to the emails that were granted access. This is useful for displaying or inspecting the current access list.

**Data flow**: It receives the scoped store where audience grants live. It lists all keys under the audience prefix, extracts the agent ID and email from each key, groups emails by agent, sorts each group, and returns a dictionary from agent IDs to email tuples.

**Call relations**: This function reads the same grant rows that _grant writes and _revoke deletes. It relies on the store’s list operation and converts stored agent ID text back into UUID values.

*Call graph*: calls 1 internal fn (list); 1 external calls (UUID).


##### `_granted_agent_ids`  (lines 52–59)

```
async def _granted_agent_ids(store: ScopedStore, email: str) -> frozenset[UUID]
```

**Purpose**: Finds every agent that has been explicitly granted to a particular email address. It answers the question, “Which private agents has this member been invited to use in the web portal?”

**Data flow**: It receives the grant store and an email address. It normalizes the email, scans all audience grant rows, keeps the agent IDs whose stored email matches, and returns those IDs as an immutable set.

**Call relations**: web_audience calls this while building a non-admin member’s view of the portal. It reads the grant rows from storage and hands back only the agent IDs relevant to that member.

*Call graph*: calls 1 internal fn (list); called by 1 (web_audience); 1 external calls (UUID).


##### `WebAudience.allows`  (lines 73–74)

```
def allows(self, agent_id: UUID) -> bool
```

**Purpose**: Checks whether this audience view includes a specific agent for normal portal access. It is a simple yes-or-no test used after the audience has already been built.

**Data flow**: It receives an agent ID. It looks through the audience’s visible agents and returns true if any agent has that ID, otherwise false.

**Call relations**: Code that already has a WebAudience can call this before showing an agent or allowing ordinary portal access. It uses the agents chosen earlier by web_audience.


##### `WebAudience.allows_chat`  (lines 76–77)

```
def allows_chat(self, agent_id: UUID) -> bool
```

**Purpose**: Checks whether this member is allowed to chat with a specific agent. Chat access can include both generally visible agents and special conversation-only agents.

**Data flow**: It receives an agent ID. It looks through the combined chat agent list and returns true if the ID is present, otherwise false.

**Call relations**: Portal routes can call this when deciding whether a chat is allowed. It depends on the chat_agents property, which combines the normal agent list with conversation-only access.


##### `WebAudience.chat_agents`  (lines 80–81)

```
def chat_agents(self) -> tuple[AgentSummary, ...]
```

**Purpose**: Combines the agents a member can generally see with agents they can reach only through specific extension conversations. This gives chat checks one complete list to inspect.

**Data flow**: It reads the WebAudience object’s agents and conversation_agents fields. It returns a new tuple containing both groups in order.

**Call relations**: WebAudience.allows_chat uses this property to decide chat permission. web_audience is responsible for filling the two source lists before this property is read.


##### `web_audience`  (lines 84–116)

```
async def web_audience(surface: SurfaceContext, extension: ExtensionContext, email: str) -> WebAudience
```

**Purpose**: Builds the full web portal audience for one email address in one workspace. In plain terms, it decides what that person can see and where they can chat.

**Data flow**: It receives a surface context, an extension context, and an email. It normalizes the email, reads the workspace seat list inside a transaction, checks whether the email belongs to an admin or member, lists all agents, and then applies the access rules. Admins get every agent. Non-admins get workspace-visible agents, explicitly granted agents, and agents they own; they may also get conversation-only chat access for private extension conversations. It returns a WebAudience object with those results.

**Call relations**: This is the main audience-building function used by portal code. It calls into Seats to learn who the workspace members are, asks the surface for agents and member-private extension agent IDs, and calls _granted_agent_ids to include explicit web grants.

*Call graph*: calls 4 internal fn (transaction, list_agents, member_extension_agent_ids, _granted_agent_ids); 2 external calls (__init__, __init__).


##### `_refusal`  (lines 127–128)

```
def _refusal(text: str) -> ToolResult
```

**Purpose**: Creates a standard error result for a tool command that is not allowed or cannot proceed. It keeps refusal messages consistent and marks them as errors.

**Data flow**: It receives a plain text explanation. It wraps that text in tool content, marks the tool result as an error, and returns it.

**Call relations**: _gate uses this for grant and revoke validation failures, and _read_private_transcript uses it when transcript access is not allowed or cannot be recorded.

*Call graph*: called by 2 (_gate, _read_private_transcript); 2 external calls (__init__, __init__).


##### `_gate`  (lines 131–149)

```
async def _gate(ctx: ToolContext, extension: ExtensionContext, args: WebAccessInput) -> ToolResult | None
```

**Purpose**: Checks the shared safety rules before an admin changes web access. It prevents non-members, non-admins, blank emails, and emails that do not belong to workspace members from changing the grant list.

**Data flow**: It receives the tool context, extension context, and the requested email input. It checks that there is a speaking member, confirms that speaker is a workspace admin, validates the email is not blank, reads the workspace member list, and verifies the email belongs to an existing member. It returns a refusal result if anything fails, or null if the request may continue.

**Call relations**: _grant and _revoke both call this before touching storage. It calls speaker_is_admin for the permission check, uses a transaction and Seats snapshot to verify workspace membership, and delegates error formatting to _refusal.

*Call graph*: calls 3 internal fn (transaction, speaker_is_admin, _refusal); called by 2 (_grant, _revoke); 1 external calls (__init__).


##### `_grant`  (lines 152–174)

```
async def _grant(ctx: ToolContext, args: WebAccessInput) -> ToolResult
```

**Purpose**: Implements the admin tool that gives a workspace member web access to the current agent. It writes the grant record unless the current agent is the main agent, which everyone can already reach.

**Data flow**: It receives the tool context and the email input. It first ensures the tool has an extension context, then asks _gate whether the request is allowed. If refused, it returns that refusal. If the current agent is the main agent, it returns an informational success message without writing a grant. Otherwise it stores a grant row keyed by the current agent and normalized email, including who granted it, and returns a confirmation message.

**Call relations**: This is the handler for the grant_web_access tool. It relies on _gate for shared admin checks, agent_is_main for the special main-agent rule, and _grant_key to write the grant in the same format that audience lookup later reads.

*Call graph*: calls 3 internal fn (agent_is_main, _gate, _grant_key); 2 external calls (__init__, __init__).


##### `_revoke`  (lines 177–199)

```
async def _revoke(ctx: ToolContext, args: WebAccessInput) -> ToolResult
```

**Purpose**: Implements the admin tool that removes a member’s explicit web access to the current agent. It deletes the grant record, while explaining that the main agent remains reachable to everyone.

**Data flow**: It receives the tool context and the email input. It verifies the extension context exists, runs _gate, and returns any refusal. If allowed, it deletes the grant row for the current agent and email. It then returns a message: for the main agent, the member still has access because the main agent is universal; for other agents, the member no longer reaches it through that grant.

**Call relations**: This is the handler for the revoke_web_access tool. It uses the same _gate validation and _grant_key storage format as _grant, so revocation targets exactly the row that granting created.

*Call graph*: calls 3 internal fn (agent_is_main, _gate, _grant_key); 2 external calls (__init__, __init__).


##### `_read_private_transcript`  (lines 213–240)

```
async def _read_private_transcript(ctx: ToolContext, args: PrivateTranscriptInput) -> ToolResult
```

**Purpose**: Implements the admin tool that records an acknowledgement before an admin reads another member’s private transcript. It does not fetch the transcript itself; it creates the audit record that later allows the portal to show it.

**Data flow**: It receives the tool context and a conversation ID. It checks that there is a speaking member and that the speaker is an admin. It then asks the surface layer to record transcript access for this workspace, conversation, agent, and admin member. If nothing needs or allows acknowledgement, it returns a refusal. If recording succeeds, it returns a message naming whose private conversation was opened and noting that the access is on record.

**Call relations**: This is the handler for the read_private_transcript tool. It uses speaker_is_admin for permission, _refusal for denied cases, and hands the actual audit write to record_transcript_access, which is also what the portal’s transcript gate relies on later.

*Call graph*: calls 2 internal fn (speaker_is_admin, _refusal); 3 external calls (__init__, __init__, record_transcript_access).

## 📊 State Registers Touched

- `reg-database-store` — The shared database connection and tables where workspaces, users, agents, turns, files, jobs, costs, and extension data are saved.
- `reg-workspace-principals` — The current workspace, members, agents, controlling users, and ownership identities used to decide who is acting.
- `reg-session-auth` — The login sessions, signed tokens, protected links, callback state, and request identities proving who a visitor or service is.
- `reg-visibility-boundaries` — The saved rules for who may see each conversation, agent, transcript, source, memory, artifact, or workspace object.
- `reg-credential-vault` — The encrypted store of API keys, OAuth tokens, and other secrets that can be injected only into approved places.
- `reg-connection-grants` — The saved account connections and per-agent permissions that say which outside accounts an agent may use.
- `reg-agent-settings` — The durable settings for each agent, including model choice, reasoning mode, internet access, sandbox size, tools, setup needs, icon, and visibility.
- `reg-surface-installations` — The saved bindings for web, Slack, iMessage, CLI, hosted sites, and other surfaces that connect outside channels to workspaces and agents.
- `reg-conversation-records` — The durable conversation rows that remember where a conversation came from, which agent owns it, its audience, title, sandbox, and current metadata.
- `reg-inbound-message-queue` — The saved holding area for incoming chat messages before they are admitted into a running or queued turn.
- `reg-file-blob-store` — The shared byte storage for uploads, generated files, previews, media, and other raw data, separated by workspace or deployment scope.
- `reg-seat-entitlements` — Workspace seat limits, included-seat counts, and seated-member marks that gate access and billing entitlement decisions.
- `reg-turn-surface-context` — Durable per-turn inbound context such as speaker, on-behalf-of member, timezone, original surface metadata, and connection-authorization status carried from admission into prompting, execution, and delivery.

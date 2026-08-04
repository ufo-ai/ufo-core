# Conversation, Member, Agent, and Workspace Admission  `stage-7`

This stage is the front door after someone is authenticated. It turns activity from chat apps or the web portal into real workspace records and actions. The surface bridge accepts outside events, finds or creates the member and conversation, admits each message once, and sends finished replies back to systems like Slack. Membership and seats decide who belongs, who is an admin, and whether the agent is allowed to answer them.

Once inside, the stage connects people to the right agents and objects. Audience rules decide which members may use which agents, while panels let web forms make the same audited changes as chat commands. Agents and conversations are exposed as workspace objects: agents can be listed and safely edited, while conversations can be read but not changed. The shared object system is the “shelf” for durable workspace items, checking names, ownership, and permissions before any specific object type acts. Object scope keeps each operation tied to the correct agent. Connector accounts and hosted sites plug into that shelf too, supporting listing, inspection, sharing, revocation, and deletion or disconnection.

## Files in this stage

### Member and Audience Administration
Defines workspace membership, admin powers, and which members may see or use specific agents in the web portal.

### `core/src/ufo/members.py`

`domain_logic` · `request handling`

A workspace needs a safe roster. This file is the rulebook for that roster. It lets the agent list members, show one member, report a member’s current state, and change whether a member is an admin or has a seat. A seat matters because unseated members are refused when they try to speak to the agent. An admin role matters because admins can change other members and see the workspace’s shape.

The file is careful about privacy and authority. In the main internal agent, members can see the workspace roster. In a child agent or a channel shared with another organization, the system only reveals the speaker’s own membership record. Changes are stricter: only a speaking workspace admin using the main agent can change roles or seats.

It also protects the workspace from locking itself out. It will not allow the last admin to stop being an admin, and it will not allow the last seated admin to lose that safety position. Adding a new member is also guarded: the email must be valid, must match the workspace’s own email domain, and must not already belong to an existing member. Think of this file like a building access desk: it keeps the staff list, issues or removes badges, and refuses changes that would leave nobody in charge.

#### Function details

##### `MemberObjects.list`  (lines 57–72)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows the members that the current speaker is allowed to see. It turns database rows into a simple list with each member’s email, admin/member role, and seated/unseated state.

**Data flow**: It receives a tool context, which says who is speaking and where, plus a list query for paging. It asks `_visible_rows` for only the rows this speaker may see, formats each row into a short summary, and passes those summaries into the object paging helper. The result is an `ObjectPage` containing the visible member objects.

**Call relations**: When the object system needs to list `member` objects, it calls this function. This function depends on `_visible_rows` to enforce the visibility rules first, then hands the formatted rows to `object_page` so the broader object API gets a normal paged response.

*Call graph*: calls 1 internal fn (_visible_rows); 2 external calls (__init__, object_page).


##### `MemberObjects.get`  (lines 74–82)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[MemberSpec] | None
```

**Purpose**: Fetches one member object by its name, where the name is the member’s UUID written as text. It returns the member’s editable settings: whether they are an admin and whether they are seated.

**Data flow**: It receives the current context and a requested member name. It asks `_visible_row` for that exact member, but only if the speaker is allowed to see it. If no visible row exists, it returns `None`; otherwise it builds a `MemberSpec` from the row and includes the record’s creation and update times in an `ObjectDetail`.

**Call relations**: The object system calls this when someone asks to inspect a member object. It relies on `_visible_row`, which in turn uses the same visibility rules as listing, so direct lookup cannot bypass privacy limits.

*Call graph*: calls 1 internal fn (_visible_row); 2 external calls (__init__, __init__).


##### `MemberObjects.status`  (lines 84–99)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns a small live status snapshot for one member. It exposes the member’s email and whether they currently have a seat.

**Data flow**: It receives the context, the member name, and an optional expected generation value, which this implementation does not use. It looks up the visible row for that member. If found, it returns a plain dictionary with `email` and `seated`; if not found, it returns `None`.

**Call relations**: The object system calls this when it needs a lightweight current-state view rather than the full object detail. It delegates visibility and lookup to `_visible_row`, keeping the same access rules as `get`.

*Call graph*: calls 1 internal fn (_visible_row).


##### `MemberObjects.apply`  (lines 101–180)

```
async def apply(self, ctx: ToolContext, name: str, spec: MemberSpec, old: MemberSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Changes an existing member’s admin role or seat. It is the controlled path for admins to grant or remove workspace authority and agent access.

**Data flow**: It receives the current context, the member name, the desired `MemberSpec`, the old spec if one existed, and an optional expected generation value. First it checks that the speaker is a real member using the main agent; if this is a creation attempt rather than an update, it refuses it. It parses the member name as a UUID, opens a workspace database transaction, locks the workspace row to avoid two conflicting membership changes at once, confirms the speaker is an admin, and loads the target member. If the seat setting changed, it calls the seat system to grant or revoke the seat. If the admin setting changed, it checks that the workspace will still have at least one admin and at least one seated admin when required, then updates the member row. It returns nothing, but it may change the database or raise an error explaining why the change is not allowed.

**Call relations**: The object system calls this when someone applies a new spec to a `member` object. It coordinates several outside pieces: the tool context for authority, `workspace_tx` for a safe database transaction, the seat system for seating changes, and the member table for role changes. It refuses unsupported creation and unknown member names so callers use `add_member` for new people instead.

*Call graph*: calls 1 internal fn (agent_is_main); 10 external calls (__init__, __init__, __init__, __init__, select, update, workspace_tx, member_is_admin, ws_current, UUID).


##### `MemberObjects.delete`  (lines 182–189)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Always refuses deletion of a workspace member through the object API. This keeps membership removal out of this object interface.

**Data flow**: It receives the context, member name, and optional expected generation value. It does not read or change any member data. It immediately raises a `VerbNotSupported` error with the message that members cannot be deleted through objects.

**Call relations**: The object system calls this if someone tries to delete a `member` object. Instead of handing off to the database, it stops the operation at the boundary and reports that this verb is not supported.

*Call graph*: 1 external calls (__init__).


##### `MemberObjects._visible_rows`  (lines 191–209)

```
async def _visible_rows(self, ctx: ToolContext) -> tuple[sa.Row, ...]
```

**Purpose**: Builds the actual set of member rows the current speaker is allowed to see. It is the privacy filter behind listing and single-member lookup.

**Data flow**: It receives a tool context. It starts with a database query for all members in the current workspace, ordered by email. If there is no speaker member, it returns an empty tuple. If the speaker is not using the main agent, or the conversation is with an outside organization, it narrows the query to only the speaker’s own member row. It then runs the query in a workspace transaction and returns the rows.

**Call relations**: `list` calls this directly to show visible members, and `_visible_row` calls it before selecting one row. Because both paths use this helper, the file has one shared place for the roster visibility rules.

*Call graph*: calls 1 internal fn (agent_is_main); called by 2 (_visible_row, list); 3 external calls (select, workspace_tx, ws_current).


##### `MemberObjects._visible_row`  (lines 211–215)

```
async def _visible_row(self, ctx: ToolContext, name: str) -> sa.Row | None
```

**Purpose**: Finds one visible member row by name. It is a small helper that prevents direct member lookup from seeing more than listing would reveal.

**Data flow**: It receives the context and a requested name. It asks `_visible_rows` for all rows visible to the speaker, then searches those rows for one whose UUID string matches the requested name. It returns that row if found, or `None` if the row is absent or hidden from this speaker.

**Call relations**: `get` and `status` call this whenever they need one member. It depends on `_visible_rows`, so the same privacy rules apply whether a caller asks for a whole roster or a single person.

*Call graph*: calls 1 internal fn (_visible_rows); called by 2 (get, status).


##### `AddMember.add`  (lines 242–277)

```
async def add(self, ctx: ToolContext, args: AddMemberInput) -> ToolResult
```

**Purpose**: Adds a new person to the workspace by email before they have contacted the agent. It is meant for an existing workspace admin staffing the workspace intentionally.

**Data flow**: It receives the tool context and input containing an email address, an admin flag, and a plain-language description. It first checks that the speaker is a member, is using the main agent, and is not in a channel shared with another organization. It normalizes the email to lowercase, extracts the email domain, and rejects invalid addresses. Inside a workspace transaction, it locks the workspace row, confirms the speaker is an admin, checks that the email domain matches the workspace’s own domain, checks that the email is not already present, and creates the member. It then reads whether the new member got a seat and returns a text result explaining the new role and whether the person can speak to the agent now.

**Call relations**: The tool registry calls this as the handler for the `add_member` tool. During the flow it hands off to `_domain_matches` for the workspace-domain rule, `_absent` for the duplicate-member check, and the shared member creation and seat logic for the actual database change.

*Call graph*: calls 3 internal fn (_absent, _domain_matches, agent_is_main); 9 external calls (__init__, __init__, __init__, select, workspace_tx, create_member, email_domain, member_is_admin, ws_current).


##### `AddMember._domain_matches`  (lines 279–289)

```
async def _domain_matches(self, connection: AsyncConnection, domain: str) -> None
```

**Purpose**: Checks that the email domain being added is the workspace’s own domain. This prevents adding someone whose email could never properly belong to this workspace’s sign-in path.

**Data flow**: It receives an open database connection and the proposed email domain. It reads the current workspace’s domain from the workspace data. If the workspace has no usable domain, it raises an error. If the proposed domain differs from the workspace domain, it raises an error naming the allowed domain. If the domains match, it returns without changing anything.

**Call relations**: `AddMember.add` calls this before creating the member. It uses the same workspace-domain source as the portal, so the admin-facing rule and the actual admission rule stay aligned.

*Call graph*: called by 1 (add); 2 external calls (workspace_domain, ws_current).


##### `AddMember._absent`  (lines 291–304)

```
async def _absent(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Checks that the email address is not already a member of the current workspace. This avoids creating duplicate member rows for the same person.

**Data flow**: It receives an open database connection and a normalized email address. It searches the member table in the current workspace using a lowercase email comparison. If no existing member is found, it returns quietly. If one is found, it raises an error telling the caller to change that existing member’s role or seat instead.

**Call relations**: `AddMember.add` calls this after the domain check and before creating the new member. It protects the later `create_member` step from being used as an accidental update path.

*Call graph*: called by 1 (add); 3 external calls (execute, select, ws_current).


### `extensions/web/ufo_ext_web/audience.py`

`domain_logic` · `request handling`

The web portal needs a clear rule for who can reach each agent. This file is that rulebook. It treats a member’s email address as their web identity, then stores access grants as simple rows keyed by agent and email. Workspace admins can reach every agent. Non-admin members can always reach the main agent, and can reach other agents only when their email has been granted access.

The file also provides the chat tools that admins use to change those grants. Before changing anything, it checks that the speaker is an actual workspace member, that they are an admin, and that the target email belongs to an existing workspace member. This prevents accidental grants to strangers or typo-only accounts. The main agent is special: everyone already reaches it, so granting or revoking it gives a friendly explanation rather than changing the basic rule.

There is one more privacy-related tool here: an admin can acknowledge that they are opening another member’s private transcript. The file does not read the transcript itself. Instead, it records the acknowledgement, like signing a visitor log before entering a private room. Without this file, the web portal would not have a consistent, auditable way to decide agent visibility or private transcript access.

#### Function details

##### `web_extension`  (lines 27–34)

```
def web_extension() -> ExtensionContext
```

**Purpose**: Builds the web extension’s own access handle so web code can read and write the web audience records. It gives the web surface a scoped store, meaning a storage area limited to this extension’s data.

**Data flow**: No caller-provided data goes in. The function creates a store scoped to the web extension and a credentials object with no declared credential access, then returns an ExtensionContext that bundles those pieces together.

**Call relations**: When web surface code needs to inspect or change audience data, this helper creates the extension context it will use. Internally it constructs the scoped store, credential access object, and extension context that later code can use for transactions and storage.

*Call graph*: 3 external calls (__init__, __init__, __init__).


##### `_grant_key`  (lines 37–38)

```
def _grant_key(agent_id: UUID, email: str) -> str
```

**Purpose**: Creates the storage key for one access grant: one agent plus one email address. It normalizes the email by trimming spaces and lowercasing it so the same person is not treated differently because of capitalization.

**Data flow**: An agent id and an email go in. The function cleans the email and combines it with a fixed audience prefix and the agent id. A single string key comes out, ready to use in the extension store.

**Call relations**: The grant and revoke flows both call this helper so they write and delete the exact same kind of key. That keeps the stored access rows consistent.

*Call graph*: called by 2 (_grant, _revoke).


##### `granted_emails`  (lines 41–48)

```
async def granted_emails(store: ScopedStore) -> dict[UUID, tuple[str, ...]]
```

**Purpose**: Reads all web audience grants and groups them by agent. This is useful for an administration view that needs to show who has been granted access to each agent.

**Data flow**: A scoped store goes in. The function lists every stored key under the audience prefix, extracts the agent id and email from each key, groups emails by agent, sorts each email list, and returns a dictionary from agent id to email tuple.

**Call relations**: This function reads the same stored rows that the grant and revoke tools modify. It depends on the store’s list operation and turns the raw storage keys back into human-meaningful agent and email information.

*Call graph*: calls 1 internal fn (list); 1 external calls (UUID).


##### `_granted_agent_ids`  (lines 51–58)

```
async def _granted_agent_ids(store: ScopedStore, email: str) -> frozenset[UUID]
```

**Purpose**: Finds the set of non-main agents a particular email address has been explicitly granted in the web portal. It is the lookup used when building one member’s portal view.

**Data flow**: A scoped store and an email go in. The email is trimmed and lowercased, then the function scans all audience grant keys. For every key whose stored email matches, it extracts the agent id. It returns a frozen set of matching agent ids.

**Call relations**: web_audience calls this for non-admin members after it has learned that the member is not an admin. The result is then combined with the always-visible main agent rule to decide which agents the member sees.

*Call graph*: calls 1 internal fn (list); called by 1 (web_audience); 1 external calls (UUID).


##### `WebAudience.allows`  (lines 73–74)

```
def allows(self, agent_id: UUID) -> bool
```

**Purpose**: Answers the simple question: is this agent visible in this member’s web audience? It checks the already-built list of agents available to that member.

**Data flow**: An agent id goes in. The method compares it with the ids of the AgentSummary objects stored in this WebAudience. It returns true if one matches, otherwise false.

**Call relations**: Code that receives a WebAudience can call this method when it needs a yes-or-no visibility check. It does not fetch data itself; it relies on web_audience having already built the correct agent list.


##### `WebAudience.granted`  (lines 76–77)

```
def granted(self, agent_id: UUID) -> bool
```

**Purpose**: Answers whether access to an agent counts as granted for stricter gates, such as usage or private views. Admins count as granted for every agent; non-admins count only for explicitly granted agent ids.

**Data flow**: An agent id goes in. If this WebAudience belongs to an admin, the method immediately returns true. Otherwise it checks whether the id is in the stored granted_ids set and returns that result.

**Call relations**: This method is used after a WebAudience has been assembled to distinguish broad visibility from explicit or admin-level permission. It is intentionally different from allows because the main agent may be visible to everyone without being an explicit grant.


##### `web_audience`  (lines 80–97)

```
async def web_audience(surface: SurfaceContext, extension: ExtensionContext, email: str) -> WebAudience
```

**Purpose**: Builds one member’s full web portal audience: whether they are an admin, which agents they can see, and which agent ids were explicitly granted. This is the central decision point for web agent visibility.

**Data flow**: A surface context, an extension context, and the member’s email go in. The function normalizes the email, reads the workspace seat snapshot inside a transaction, checks whether the member is an admin, and asks the surface for all agents. If the member is an admin, all agents come out in the WebAudience. Otherwise it looks up explicit grants and returns only the main agents plus granted agents.

**Call relations**: This function ties together the seat list, the web audience store, and the surface’s agent list. It calls _granted_agent_ids only for non-admins, then returns a WebAudience object that later request-handling code can use for access checks.

*Call graph*: calls 3 internal fn (transaction, list_agents, _granted_agent_ids); 2 external calls (__init__, __init__).


##### `_refusal`  (lines 108–109)

```
def _refusal(text: str) -> ToolResult
```

**Purpose**: Creates a standard error-style tool result with a plain message for the user. It keeps refusal responses consistent across the access tools.

**Data flow**: A text message goes in. The function wraps it as TextContent, marks the ToolResult as an error, and returns that result.

**Call relations**: _gate and _read_private_transcript call this whenever a user is not allowed to continue or the request cannot be honored. It is the shared exit path for polite, visible denials.

*Call graph*: called by 2 (_gate, _read_private_transcript); 2 external calls (__init__, __init__).


##### `_gate`  (lines 112–130)

```
async def _gate(ctx: ToolContext, extension: ExtensionContext, args: WebAccessInput) -> ToolResult | None
```

**Purpose**: Checks whether a web access change is allowed before grant or revoke touches storage. It verifies that the speaker is a workspace member, is an admin, supplied an email, and named an existing workspace member.

**Data flow**: A tool context, extension context, and parsed access input go in. The function checks who is speaking, asks whether they are an admin, validates the email text, reads the workspace member snapshot inside a transaction, and compares the target email against known members. It returns a refusal ToolResult if something is wrong, or None if the request may continue.

**Call relations**: _grant and _revoke both call this first. If _gate returns a refusal, they stop immediately; if it returns None, they proceed to change the stored grant row.

*Call graph*: calls 3 internal fn (transaction, speaker_is_admin, _refusal); called by 2 (_grant, _revoke); 1 external calls (__init__).


##### `_grant`  (lines 133–155)

```
async def _grant(ctx: ToolContext, args: WebAccessInput) -> ToolResult
```

**Purpose**: Implements the admin tool that gives a workspace member web access to the current agent. It is careful not to create unnecessary grants for the main agent, because the main agent is already available to every member.

**Data flow**: A tool context and web access input go in. The function requires an ExtensionContext, runs _gate, and stops if permission checks fail. If the current agent is the main agent, it returns an explanatory success message. Otherwise it writes a grant row to the extension store using the normalized agent-and-email key, then returns a message saying the member can now reach the agent.

**Call relations**: This is the handler behind the grant_web_access tool definition. It relies on _gate for safety checks and _grant_key for the storage key, then writes to the same store that web_audience later reads when deciding a member’s visible agents.

*Call graph*: calls 3 internal fn (agent_is_main, _gate, _grant_key); 2 external calls (__init__, __init__).


##### `_revoke`  (lines 158–180)

```
async def _revoke(ctx: ToolContext, args: WebAccessInput) -> ToolResult
```

**Purpose**: Implements the admin tool that removes a workspace member’s web access to the current agent. It also explains the special main-agent rule, where revoking a stored grant cannot stop basic portal access.

**Data flow**: A tool context and web access input go in. The function requires an ExtensionContext, runs _gate, and stops if permission checks fail. It deletes the matching grant row from the extension store. If the current agent is the main agent, it returns a message explaining that the member still reaches it; otherwise it returns a message saying access was removed.

**Call relations**: This is the handler behind the revoke_web_access tool definition. Like _grant, it uses _gate before changing anything and _grant_key to point at the same row format that the grant flow creates.

*Call graph*: calls 3 internal fn (agent_is_main, _gate, _grant_key); 2 external calls (__init__, __init__).


##### `_read_private_transcript`  (lines 194–221)

```
async def _read_private_transcript(ctx: ToolContext, args: PrivateTranscriptInput) -> ToolResult
```

**Purpose**: Records an admin’s acknowledgement before they read another member’s private conversation transcript in the web portal. It creates an audit trail instead of silently opening private material.

**Data flow**: A tool context and transcript input go in. The function checks that there is a speaking member and that the speaker is an admin. It then asks the surface layer to record transcript access for the workspace, conversation, current agent, and admin member. If nothing valid needs acknowledgement, it returns a refusal. If the record is created, it returns a message naming whose private conversation was opened and noting that the access was recorded.

**Call relations**: This is the handler behind the read_private_transcript tool definition. It uses _refusal for denied or irrelevant requests, and hands the actual audit-record creation to record_transcript_access so the portal’s transcript gate can later recognize the acknowledgement.

*Call graph*: calls 2 internal fn (speaker_is_admin, _refusal); 3 external calls (__init__, __init__, record_transcript_access).


### Surface Admission Bridge
Routes web and chat surface actions into the trusted core path for member identification, conversations, turns, audits, and durable replies.

### `extensions/web/ufo_ext_web/panels.py`

`orchestration` · `request handling`

The web portal has panels where a user can edit an agent, add a member, request a credential prompt, grant access, correct memory, or open a private transcript. This file defines the small set of changes those panels are allowed to submit, checks that each request has the right shape, turns it into a ToolIntent, and sends it through the existing conversation system.

That design matters because the portal does not get a separate “back door” for changing objects. Instead, every write is admitted as a turn in a durable intent conversation for that member and agent. Think of it like putting every form submission onto the same official conveyor belt used by chat commands, so ordering, permissions, results, and audit history all stay consistent.

The file also returns the final result synchronously when possible: after submitting an intent, it waits for the turn to finish, then reports whether the change was saved, refused, parked for later input, or timed out. For credentials, it never accepts the secret directly in the panel request. It only asks the system to create a sealed private credential prompt.

Finally, the file provides an agent overview endpoint. That endpoint gathers the agent’s current settings, available models, writable schema for settings forms, deployment limits, and, for admins, the web audience allowed to access the agent.

#### Function details

##### `ApplyIntent._verb_pairs_with_its_kind`  (lines 60–69)

```
def _verb_pairs_with_its_kind(self) -> 'ApplyIntent'
```

**Purpose**: This checks that a submitted object change makes sense before the system accepts it. It stops the portal from sending combinations that are not allowed, such as using a connection-only verb on another kind of object.

**Data flow**: It reads the already-parsed intent fields: the verb, the kind, and the optional spec data. It rejects invalid pairings with a clear validation error. If everything fits the rules, it returns the same intent object unchanged.

**Call relations**: This validator runs as part of building an ApplyIntent from a panel submission. Later, submit_intent relies on this validation so it can safely turn the intent into the correct ToolIntent without rechecking every forbidden combination.


##### `_tool_intent`  (lines 139–233)

```
def _tool_intent(submitted: ApplyIntent | AddMemberIntent | AudienceIntent | CorrectionIntent | CredentialIntent | TranscriptIntent, slot: CredentialSlotView | None) -> ToolIntent
```

**Purpose**: This converts a portal-specific request into the exact tool command the agent system already understands. It is the translation step between “a form was submitted” and “run this named system action.”

**Data flow**: It takes one validated panel intent, plus credential slot details when the request is about a credential. It chooses the matching tool name and builds an input dictionary for that tool. For ordinary object updates, it serializes the desired object into YAML, a human-readable structured text format. It returns a ToolIntent ready to be admitted as a conversation turn.

**Call relations**: submit_intent calls this after validating the request and looking up any needed credential slot. The returned ToolIntent is then handed to SurfaceContext.admit so the normal intent-processing lane can execute it.

*Call graph*: called by 1 (submit_intent); 2 external calls (__init__, safe_dump).


##### `_outcome`  (lines 236–252)

```
def _outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: This turns the final result of an intent turn into a web response the portal can show to the user. It hides internal error class prefixes and gives the page a simple success or failure message.

**Data flow**: It receives a terminal frame, which is the final status of the turn, and the turn id used for audit and follow-up. If the turn succeeded, it returns JSON saying the change was applied, and includes credential-request details when the result is a private credential prompt. If the turn failed, it chooses a readable reason and returns JSON saying it was not applied.

**Call relations**: submit_intent calls this when SurfaceContext.tail yields a Terminal frame. It is the last formatting step before the HTTP response goes back to the browser.

*Call graph*: called by 1 (submit_intent); 1 external calls (JSONResponse).


##### `submit_intent`  (lines 255–317)

```
async def submit_intent(ctx: SurfaceContext, request: Request, agent_id: UUID, member_id: UUID, email: str) -> Response
```

**Purpose**: This is the main request handler for panel form submissions. It accepts one prepared portal intent, validates it, sends it through the normal conversation-based action system, waits for the result, and returns a clear answer to the browser.

**Data flow**: It starts with the HTTP request body, the current surface context, the selected agent, and the submitting member. It rejects bodies that are too large, malformed JSON, invalid intent shapes, unknown agent models, and unknown credential slots. It then finds or creates the member’s durable intent conversation for this agent, converts the submission into a ToolIntent, admits it as a turn, and watches the turn stream until it finishes, parks, or times out. The output is an HTTP response saying whether the change was applied, failed, is still running, or needs attention.

**Call relations**: This function sits at the center of the panel write flow. It calls _tool_intent to translate the validated submission, SurfaceContext.conversation_for to choose the proper intent lane, SurfaceContext.admit to enqueue the turn, SurfaceContext.tail to watch the result, and _outcome to format a terminal result. It also uses list_credential_slots when a credential request must be matched to a known slot.

*Call graph*: calls 6 internal fn (admit, conversation_for, list_credential_slots, tail, _outcome, _tool_intent); 6 external calls (timeout, loads, conversation_audience, JSONResponse, body, Response).


##### `reasoning_levels`  (lines 320–324)

```
def reasoning_levels() -> list[JsonValue]
```

**Purpose**: This returns the list of reasoning-effort choices that the portal should offer when creating an agent. It avoids hard-coding those choices in the web layer.

**Data flow**: It reads the JSON schema generated from AgentSpec, finds the enum values for the reasoning field, and returns them as a list. The result can be used directly by a form or API response.

**Call relations**: This helper depends on AgentSpec as the source of truth. By reading the schema instead of duplicating the list, the portal stays aligned with what the agent configuration model actually accepts.

*Call graph*: 1 external calls (model_json_schema).


##### `_update_schema`  (lines 327–335)

```
def _update_schema() -> dict[str, JsonValue]
```

**Purpose**: This builds the schema used by the agent settings update form. It deliberately removes the prompt field because changing an existing agent’s prompt must happen through a different governed path.

**Data flow**: It starts with the full JSON schema generated from AgentSpec. It copies the writable properties except for prompt, then returns the modified schema. The output tells the portal which fields to render for normal settings updates.

**Call relations**: agent_overview calls this when preparing the data needed by the overview/settings page. It keeps the web form from showing a field that the update action would refuse.

*Call graph*: called by 1 (agent_overview); 1 external calls (model_json_schema).


##### `agent_overview`  (lines 338–370)

```
async def agent_overview(ctx: SurfaceContext, agent_id: UUID, *, admin: bool) -> Response
```

**Purpose**: This builds the data package for the portal’s agent overview page. It gives the browser the agent’s current settings, available choices, deployment limits, and, for admins, the list of web users granted access.

**Data flow**: It receives the surface context, an agent id, and whether the viewer is an admin. It asks the context for the agent details; if there is no such agent, it returns a 404 response. If the viewer is an admin, it also reads the granted web audience. It then returns JSON containing agent identity and prompt information, deployment internet capability, model names, current editable spec values, the update-form schema, and optional audience data.

**Call relations**: This is the read-side partner to submit_intent. It calls SurfaceContext.agent_detail to fetch the agent, _update_schema to describe editable settings, and the web-audience helpers when admin-only audience information is needed. The final JSONResponse is what the portal uses to render the overview panel.

*Call graph*: calls 2 internal fn (agent_detail, _update_schema); 5 external calls (__init__, JSONResponse, Response, granted_emails, web_extension).


### `core/src/ufo/ext/surface.py`

`orchestration` · `request handling and background delivery`

A “surface” is an outside place where people talk to the system, such as Slack, the web portal, or a live CLI-style connection. This file is the doorway those surfaces use when they need privileged core abilities. Those abilities are intentionally powerful: a surface can say who a member is, create or find the right conversation, put a member’s message onto the durable turn queue, read workspace-level views for the portal, and help with credentials, files, memory, spending, sources, and transcripts. Without this file, each surface would need to know too much about the database and runtime, and it would be easy to accidentally bypass privacy boundaries.

The file supports two reply styles. A live surface keeps a connection open and streams frames as the turn runs, like watching a printer produce pages. A durable surface, such as Slack, cannot rely on a live stream, so the system records that a finished turn needs “writeback” and a background poller later posts the final answer and attaches files. The poller uses claims, leases, retry delays, and saved reply references so multiple workers can safely cooperate without posting the same answer too often.

The file also contains small view models for portal screens, helper functions for message fencing and audience checks, installation binding, identity linking, transcript disclosure, and safe download links. It is a central seam: surfaces get one curated toolbox instead of raw, scattered access to core internals.

#### Function details

##### `mint_marker`  (lines 154–162)

```
def mint_marker() -> str
```

**Purpose**: Creates a short random marker used to label one member message inside a larger prompt. The marker makes the message boundary hard to fake from text the member typed.

**Data flow**: It takes no input, asks the secrets library for random bytes written as hex text, and returns that text as the marker.

**Call relations**: It is used with message fencing: a surface can mint a fresh marker before wrapping the member’s words so later code can identify exactly what the member said.

*Call graph*: 1 external calls (token_hex).


##### `fence_member_message`  (lines 165–178)

```
def fence_member_message(marker: str, ambient: str, body: str, attachments: str) -> str
```

**Purpose**: Wraps incoming surface text into clearly separated sections: surrounding channel context, the member’s own words, and optional attachment text. This keeps the model from confusing background context with the actual user message.

**Data flow**: It receives a marker, ambient context text, body text, and attachment text. It builds one combined plain-text block with tagged sections and returns that block.

**Call relations**: Surfaces use this before admitting a turn. Later, member_message_text can pull the member’s own words back out for display.


##### `member_message_text`  (lines 181–191)

```
def member_message_text(inbound: str) -> str
```

**Purpose**: Extracts the member’s own message from a fenced inbound prompt. If the text was never fenced, it safely treats the whole input as the message.

**Data flow**: It receives the stored inbound text, searches for the special member-message wrapper, and returns either the wrapped inner text or the original text.

**Call relations**: It is the read-side partner to fence_member_message, used when a view wants to show what the member said rather than the full prompt sent to the turn.


##### `MemberAdmitter.admit`  (lines 211–220)

```
async def admit(self, conversation_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None, intent: ToolIntent | None=None) -> Adm
```

**Purpose**: Defines the contract for putting a member message onto the durable turn queue. Implementations use it to create, resume, or join a turn for a conversation.

**Data flow**: It accepts a conversation id, message body, optional idempotency key, turn context, speaker member id, and optional prepared tool intent. It returns an Admitted result naming the turn and whether this call opened the run.

**Call relations**: SurfaceContext.admit delegates to this protocol. The concrete core admitter performs the real queue work while surfaces stay isolated from queue internals.


##### `TurnTailer.tail`  (lines 229–229)

```
def tail(self, turn_id: UUID, since: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: Defines the contract for reading live progress frames from a turn. Live surfaces use it to stream updates to a connected browser or client.

**Data flow**: It receives a turn id and optional replay cursor, then yields pairs of cursor and live frame until the turn ends.

**Call relations**: SurfaceContext.tail delegates to this protocol. Web, debugger, sample, and UFO surfaces use it instead of touching the live hub directly.


##### `AgentDetail._aware_utc`  (lines 333–334)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Ensures an agent timestamp has timezone information. This prevents portal clients from guessing what timezone a stored date meant.

**Data flow**: It receives a datetime value, leaves it alone if it already has a timezone, or marks it as UTC if it does not.

**Call relations**: Pydantic calls it when building AgentDetail objects, especially from database rows read by SurfaceContext.agent_detail.

*Call graph*: 1 external calls (replace).


##### `ConnectionView._aware_utc`  (lines 379–380)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Ensures a connector connection timestamp is timezone-aware. This keeps connection dates consistent for display and sorting.

**Data flow**: It receives a datetime, returns it unchanged if aware, or adds UTC if missing.

**Call relations**: Pydantic invokes it while SurfaceContext.list_agent_connections builds portal connection rows.

*Call graph*: 1 external calls (replace).


##### `_binding_fields`  (lines 421–434)

```
def _binding_fields(backend: str, config: dict[str, JsonValue]) -> dict[str, str | None]
```

**Purpose**: Turns a stored source connector configuration into the fields the portal needs to show or edit that source binding. If the config is not a connector config, it returns empty identity fields.

**Data flow**: It receives a backend name and config dictionary, tries to validate that config as a connector source, then returns a binding name, stream, account id, and base URL or None values.

**Call relations**: SurfaceContext.list_sources calls it for each source row so the web surface can display connector-managed sources without duplicating parsing rules.

*Call graph*: called by 1 (list_sources); 2 external calls (model_validate, binding_name).


##### `SourceView._aware_utc`  (lines 458–459)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Ensures a source sync timestamp includes a timezone. This avoids ambiguous “next sync” times in the portal.

**Data flow**: It receives a datetime and returns either the original aware value or a UTC-marked version.

**Call relations**: Pydantic runs it when SourceView objects are created by SurfaceContext.list_sources.

*Call graph*: 1 external calls (replace).


##### `ConversationSummary._aware_utc`  (lines 477–480)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Normalizes conversation timestamps so they are either absent or explicitly UTC-aware. This makes conversation lists safer to render.

**Data flow**: It receives a datetime or None. None stays None; timezone-aware values stay unchanged; naive values are marked as UTC.

**Call relations**: It runs while conversation summaries are built by list_conversations and list_agent_conversations.

*Call graph*: 1 external calls (replace).


##### `record_transcript_access`  (lines 493–554)

```
async def record_transcript_access(workspace_id: UUID, conversation_id: UUID, agent_id: UUID, member_id: UUID) -> TranscriptAccess | None
```

**Purpose**: Records that an admin acknowledged they are about to read another member’s private transcript. This creates the short-lived permission that later transcript reads check.

**Data flow**: It receives workspace, conversation, agent, and reader member ids. It verifies the conversation belongs to the agent, finds the private subject member, writes an access row, logs the disclosure, and returns the reader and subject emails or None if no disclosure is needed or allowed.

**Call relations**: The portal reaches this through a prepared intent before showing private content. SurfaceContext.readable_conversation later relies on the row it writes.

*Call graph*: 9 external calls (__init__, now, insert, select, audience_member, parse_audience, workspace_tx, log, uuid4).


##### `LedgerEntry._aware_utc`  (lines 581–582)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Makes ledger entry timestamps explicit UTC when needed. This helps accounting rows display consistently.

**Data flow**: It receives a datetime and returns it unchanged if timezone-aware or with UTC attached if not.

**Call relations**: It runs when SurfaceContext.turn_detail builds LedgerEntry objects for debugger and portal views.

*Call graph*: 1 external calls (replace).


##### `_fulfilled_marker_key`  (lines 595–600)

```
def _fulfilled_marker_key(workspace_id: UUID, sealed: str, slot: str) -> str
```

**Purpose**: Builds the blob-store key used to remember that one credential prompt slot has been answered. It lets one slot stop prompting without affecting other slots in the same request.

**Data flow**: It receives a workspace id, sealed request text, and slot name. It hashes the sealed request, combines that digest with the workspace and slot, and returns a blob key string.

**Call relations**: SurfaceContext.credential_prompt_pending reads this marker, and SurfaceContext.fulfill_credential_request writes it after storing the credential.

*Call graph*: called by 2 (credential_prompt_pending, fulfill_credential_request); 1 external calls (sha256).


##### `_readable_audience_values`  (lines 603–607)

```
def _readable_audience_values(member_id: UUID) -> tuple[str, ...]
```

**Purpose**: Computes the conversation audience values a member can read without extra disclosure: workspace-shared and their own private audience.

**Data flow**: It receives a member id and returns two audience strings.

**Call relations**: SurfaceContext.list_agent_conversations and readable_conversation use it so listing and content-reading decisions follow the same privacy rule.

*Call graph*: called by 2 (list_agent_conversations, readable_conversation); 1 external calls (conversation_audience).


##### `_main_agent`  (lines 610–623)

```
async def _main_agent(workspace_id: UUID) -> UUID
```

**Purpose**: Finds the workspace’s main agent. It is the fallback agent when a surface installation has not chosen a specific agent.

**Data flow**: It receives a workspace id, queries the agent table for the main agent, and returns its id. If none exists, it raises an error because the workspace is incomplete.

**Call relations**: _bind_surface_installation uses it when creating a surface binding, and SurfaceContext._surface_agent uses it as a fallback.

*Call graph*: called by 2 (_surface_agent, _bind_surface_installation); 2 external calls (select, workspace_tx).


##### `_bind_surface_installation`  (lines 626–658)

```
async def _bind_surface_installation(workspace_id: UUID, surface: str, installation_id: str) -> None
```

**Purpose**: Records which external installation, such as a Slack team, belongs to a workspace for a given surface. This is what lets future shared-surface requests resolve back to the right workspace.

**Data flow**: It receives a workspace id, surface name, and installation id. It validates the id, chooses the main agent for new bindings, upserts the binding row, and raises a conflict if another workspace already owns that installation.

**Call relations**: SurfaceContext.bind_installation uses it from a surface OAuth callback, and SurfaceInstallationAccess.bind uses it when a tool registers an installation.

*Call graph*: calls 1 internal fn (_main_agent); called by 2 (bind_installation, bind); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.deploy_extensions`  (lines 700–703)

```
def deploy_extensions(self) -> tuple[DeployExtensionView, ...]
```

**Purpose**: Returns the installed deploy-level extensions visible to administration screens. It is a read-only snapshot from process startup.

**Data flow**: It reads the context’s stored deploy extension tuple and returns it unchanged.

**Call relations**: Portal administration code reads this property through SurfaceContext when showing deployment status.


##### `SurfaceContext.deploy_sandbox_internet`  (lines 706–709)

```
def deploy_sandbox_internet(self) -> bool
```

**Purpose**: Reports whether this deployment allows sandbox public internet at all. The portal uses it as the upper limit for what an agent may be allowed to do.

**Data flow**: It reads the stored boolean on the context and returns it.

**Call relations**: Agent administration views combine this deploy-wide ceiling with each agent’s own setting.


##### `SurfaceContext.models`  (lines 712–716)

```
def models(self) -> tuple[str, ...]
```

**Purpose**: Returns the model ids available in this deployment. This gives UI surfaces a trusted list for model selection.

**Data flow**: It reads and returns the tuple of configured model names.

**Call relations**: Portal views use it when presenting model choices, while runtime code uses the same registry behind the scenes.


##### `SurfaceContext.credential`  (lines 718–721)

```
async def credential(self, slot: str) -> str
```

**Purpose**: Reads a workspace credential slot for a trusted surface. It is used for surface infrastructure secrets, such as Slack signing secrets, not for exposing values to members.

**Data flow**: It receives a slot name, checks that this context has a credential store, asks the store for the workspace’s value, and returns the secret string.

**Call relations**: Slack surface helpers call it when verifying requests, calling Slack APIs, and resolving identities. If no store is configured, it fails loudly.

*Call graph*: called by 10 (_ctx_signing_secret, _identity, _post_ephemeral, _room_audience, _run_identity_proof, _to_inbound, attach, ingest, interactive, post).


##### `SurfaceContext.credential_prompt_pending`  (lines 723–737)

```
async def credential_prompt_pending(self, sealed: str, slot: str) -> bool
```

**Purpose**: Checks whether a sealed credential request still needs one slot filled. This prevents already-answered or invalid prompts from reappearing.

**Data flow**: It receives sealed request text and a slot name, opens and validates the seal, checks workspace and slot membership, then looks for the fulfillment marker in blob storage and returns true only if the marker is absent.

**Call relations**: The web surface uses it when deciding which credential prompts to show after reconnects or reloads.

*Call graph*: calls 1 internal fn (_fulfilled_marker_key); called by 1 (_pending_prompts); 1 external calls (open_credential_request).


##### `SurfaceContext.open_credential_authorization`  (lines 739–749)

```
def open_credential_authorization(self, sealed: str) -> CredentialRequestState
```

**Purpose**: Opens a sealed credential authorization handoff and returns its claims. Surfaces use this after browser callbacks to recover which workspace, member, and slot the callback belongs to.

**Data flow**: It receives sealed text, requires a credential store, verifies and decrypts the seal, and returns the request state or raises if invalid.

**Call relations**: Slack’s OAuth callback calls this before fulfilling a credential request.

*Call graph*: called by 1 (oauth_callback); 1 external calls (open_credential_request).


##### `SurfaceContext.fulfill_credential_request`  (lines 751–776)

```
async def fulfill_credential_request(self, sealed: str, slot: str, value: str, member_id: UUID | None) -> None
```

**Purpose**: Stores a credential value for a member-authorized sealed request. It verifies that the right member is filling the right slot in the right workspace before writing anything.

**Data flow**: It receives the seal, slot, value, and member id. It opens the seal, checks workspace, member, and slot, writes the credential to the encrypted store, then writes a blob marker so the prompt no longer appears.

**Call relations**: Slack, web, and UFO surfaces call it when a member completes a credential handoff.

*Call graph*: calls 1 internal fn (_fulfilled_marker_key); called by 3 (oauth_callback, _fulfill_secret, fulfill_credential); 4 external calls (__init__, now, dumps, open_credential_request).


##### `SurfaceContext.bind_installation`  (lines 778–784)

```
async def bind_installation(self, installation_id: str) -> None
```

**Purpose**: Binds this surface’s external installation id to the current workspace. This lets later requests from that installation find the workspace.

**Data flow**: It receives an installation id and forwards the current workspace id and surface name to the shared binding helper.

**Call relations**: Slack’s OAuth callback calls it after a workspace installs or reconnects Slack.

*Call graph*: calls 1 internal fn (_bind_surface_installation); called by 1 (oauth_callback).


##### `SurfaceContext.public_base_url`  (lines 787–790)

```
def public_base_url(self) -> str | None
```

**Purpose**: Returns the deployment’s public base URL, if configured. Surfaces use it when they need to create externally reachable links or callback URLs.

**Data flow**: It reads the stored URL string and returns it, or returns None when unset.

**Call relations**: Surface-specific code reads this through SurfaceContext rather than reading deployment config directly.


##### `SurfaceContext.shared_artifacts`  (lines 792–824)

```
async def shared_artifacts(self, turn_id: UUID) -> tuple[SharedArtifact, ...]
```

**Purpose**: Lists the files a turn shared, in a stable order. Live surfaces use it to show download links, while durable writeback uses the same information for attachments.

**Data flow**: It receives a turn id, queries shared artifact rows for this workspace and turn, turns each row into a SharedArtifact, and returns the tuple.

**Call relations**: The web surface calls it when rendering files for a turn.

*Call graph*: called by 1 (_turn_files); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.artifact_link`  (lines 826–837)

```
def artifact_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a temporary signed download URL for a shared artifact. If link delivery is not configured, it returns None so the surface can mention the file without a link.

**Data flow**: It receives a SharedArtifact, checks for a token secret and public base URL, mints an expiring token for the blob key and filename, and returns a full URL.

**Call relations**: Slack uses it for oversized files, and web uses it for turn and workspace artifact views.

*Call graph*: called by 3 (_oversize_link_line, _turn_files, workspace_artifacts); 2 external calls (now, mint_artifact_token).


##### `SurfaceContext.ingress_url`  (lines 839–860)

```
def ingress_url(self, conversation_id: UUID, port: int) -> str | None
```

**Purpose**: Creates a temporary URL for opening a conversation’s sandbox port in a browser. This supports hosted sites without giving the surface the ingress service’s secret.

**Data flow**: It receives a conversation id and port, checks that ingress is configured, mints an expiring view token, builds a stable subdomain label, and returns the browser URL.

**Call relations**: The sites surface calls it when rendering an embedded or linked site frame.

*Call graph*: called by 1 (frame); 5 external calls (__init__, now, site_label, mint_ingress_token, urlsplit).


##### `SurfaceContext._identity_member`  (lines 862–875)

```
async def _identity_member(self, surface: str, external_id: str) -> UUID | None
```

**Purpose**: Looks up which workspace member an external surface user id is linked to. It is the shared private helper for identity checks across current and peer surfaces.

**Data flow**: It receives a surface name and external id, queries the surface_identity table, and returns the linked member id or None.

**Call relations**: SurfaceContext.linked_member uses it for this surface, while adopt_identity uses it to copy a peer surface’s known identity.

*Call graph*: called by 2 (adopt_identity, linked_member); 2 external calls (select, workspace_tx).


##### `SurfaceContext.linked_member`  (lines 877–878)

```
async def linked_member(self, external_id: str) -> UUID | None
```

**Purpose**: Finds the member currently linked to this surface’s external user id. Surfaces use it before deciding whether a speaker is known.

**Data flow**: It receives an external id and asks _identity_member for this context’s surface.

**Call relations**: Sample, sites, Slack, UFO, and web surfaces call it during authentication or message ingestion.

*Call graph*: calls 1 internal fn (_identity_member); called by 7 (_surface_ingest, _surface_live_admit, _viewer, _resolve_member, interactive, channel, _authenticate).


##### `SurfaceContext.is_operator_workspace`  (lines 880–887)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Checks whether this workspace belongs to the fleet operator. This gates internal-only rendering such as special Slack debug or accounting details.

**Data flow**: It reads the workspace email domain and compares it with the operator domain constant, returning a boolean.

**Call relations**: Slack post rendering calls it before showing operator-only footer information.

*Call graph*: calls 1 internal fn (workspace_domain); called by 1 (post).


##### `SurfaceContext.adopt_identity`  (lines 889–912)

```
async def adopt_identity(self, peer_surface: str, external_id: str) -> UUID | None
```

**Purpose**: Links this surface’s external user id to a member already known by another surface. This lets the same human keep one member identity across surfaces.

**Data flow**: It receives a peer surface and external id, finds the peer-linked member, inserts this surface’s identity link if possible, logs races, and returns the member id or None.

**Call relations**: The sample live surface uses it when a live identity should inherit an existing peer identity.

*Call graph*: calls 1 internal fn (_identity_member); called by 1 (_surface_live_admit); 3 external calls (insert, workspace_tx, log).


##### `SurfaceContext.link_member`  (lines 914–943)

```
async def link_member(self, external_id: str, email: str) -> UUID | None
```

**Purpose**: Links this surface’s external user id to an existing workspace member by email. If no member has that email, it leaves the external id unknown.

**Data flow**: It receives an external id and email, looks up a matching member case-insensitively, inserts the surface identity link, logs insert races, and returns the member id or None.

**Call relations**: Several surfaces call it during authentication, and join_member builds on it when Slack can safely auto-create same-domain members.

*Call graph*: called by 5 (join_member, _surface_ingest, _viewer, channel, _authenticate); 4 external calls (insert, select, workspace_tx, log).


##### `SurfaceContext.join_member`  (lines 945–962)

```
async def join_member(self, external_id: str, email: str) -> UUID | None
```

**Purpose**: Links an external id to a member, creating a new member first when the verified email belongs to the workspace’s own domain. This supports first-contact onboarding from trusted channels.

**Data flow**: It receives an external id and email. It first tries link_member, then checks the workspace domain, creates a member if the domain matches, and links again.

**Call relations**: Slack’s member resolution uses it because Slack can provide channel-verified email addresses.

*Call graph*: calls 2 internal fn (link_member, workspace_domain); called by 1 (_resolve_member); 3 external calls (workspace_tx, create_member, email_domain).


##### `SurfaceContext._conversation_lookup`  (lines 964–973)

```
def _conversation_lookup(self, queue_key: str) -> sa.Select
```

**Purpose**: Builds the database query for finding a conversation by this surface’s queue key. It keeps lookup criteria consistent in find and create paths.

**Data flow**: It receives a queue key and returns a SQL select scoped to workspace, surface, and queue key.

**Call relations**: find_conversation and conversation_for both use this helper before reading or creating conversation rows.

*Call graph*: called by 2 (conversation_for, find_conversation); 1 external calls (select).


##### `SurfaceContext.find_conversation`  (lines 975–981)

```
async def find_conversation(self, queue_key: str) -> UUID | None
```

**Purpose**: Finds an existing conversation for this surface and queue key without creating one. This is useful when a surface should only respond inside an already-known thread.

**Data flow**: It receives a queue key, runs the shared lookup query, and returns the conversation id or None.

**Call relations**: Slack uses it for participation checks, debug links, and interactive actions.

*Call graph*: calls 1 internal fn (_conversation_lookup); called by 3 (_debug_link, _participating_conversation, interactive); 1 external calls (workspace_tx).


##### `SurfaceContext.conversation_for`  (lines 983–1062)

```
async def conversation_for(self, queue_key: str, audience: Audience, agent_id: UUID | None=None, conversation_id: UUID | None=None) -> UUID
```

**Purpose**: Gets or creates the conversation for a surface queue key. It also narrows, but never widens, the conversation audience when better identity information arrives.

**Data flow**: It receives a queue key, audience, optional agent id, and optional conversation id. It reads any existing row, narrows audience if needed, validates or chooses an agent, inserts a new row on first contact, and handles creation races by rereading.

**Call relations**: All message-admitting surfaces call it before SurfaceContext.admit so a turn has the right conversation and agent binding.

*Call graph*: calls 2 internal fn (_conversation_lookup, _surface_agent); called by 7 (_surface_ingest, _surface_live_admit, ingest, interactive, channel, submit_intent, _open_conversation); 9 external calls (insert, select, update, audience_member, narrow_audience, parse_audience, workspace_tx, log, uuid4).


##### `SurfaceContext._surface_agent`  (lines 1064–1076)

```
async def _surface_agent(self) -> UUID
```

**Purpose**: Chooses the agent a new conversation for this surface should bind to. It uses the surface installation’s agent when present, otherwise the workspace main agent.

**Data flow**: It queries the surface_installation table for this workspace and surface, returns that agent id if found, or calls _main_agent.

**Call relations**: conversation_for uses it when the caller did not explicitly choose an agent.

*Call graph*: calls 1 internal fn (_main_agent); called by 1 (conversation_for); 2 external calls (select, workspace_tx).


##### `SurfaceContext.admit`  (lines 1078–1109)

```
async def admit(self, conversation_id: UUID, body: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None, intent: ToolIntent | None=None) -> Admitt
```

**Purpose**: Submits an inbound surface message or prepared intent as a turn. This is the main write path from surfaces into the agent runtime.

**Data flow**: It receives the conversation id, body, optional idempotency key, context, speaker member id, and optional intent, then delegates to the injected MemberAdmitter and returns the Admitted result.

**Call relations**: Sample, Slack, UFO, web chat, and web panel intent routes call it after resolving conversation and identity.

*Call graph*: called by 7 (_surface_ingest, _surface_live_admit, ingest, interactive, channel, submit_intent, chat).


##### `SurfaceContext.connect_url`  (lines 1111–1117)

```
async def connect_url(self, turn_id: UUID, member_id: UUID) -> str
```

**Purpose**: Creates an authorization URL for a terminal connect request as the speaking member. It turns a stored handoff into a browser link.

**Data flow**: It receives a turn id and member id, loads the installed connect flow, asks ConnectHandoff to authorize it, and returns the URL. If no flow exists, it raises a connect request error.

**Call relations**: Slack interactive handling and web event rendering call it when a turn asks the member to connect an account.

*Call graph*: called by 2 (interactive, _events); 3 external calls (__init__, __init__, installed_connect_flow).


##### `SurfaceContext.admitted_body`  (lines 1119–1144)

```
async def admitted_body(self, idempotency_key: str) -> str | None
```

**Purpose**: Finds what body was actually admitted for an idempotency key. This lets a surface tell which of several racing button clicks won.

**Data flow**: It receives an idempotency key, first checks stored turn inbounds, then queued inbound messages, and returns the body or None.

**Call relations**: Slack interactive handling and web chat use it to update answer affordances only for the click that truly landed.

*Call graph*: called by 2 (interactive, chat); 2 external calls (select, workspace_tx).


##### `SurfaceContext.turn_owner`  (lines 1146–1160)

```
async def turn_owner(self, turn_id: UUID) -> UUID | None
```

**Purpose**: Finds which member owns the conversation containing a turn. Live surfaces use this to stop one member from tailing another member’s turn.

**Data flow**: It receives a turn id, joins turn to conversation in the workspace, and returns the conversation member id or None.

**Call relations**: Sample live admission and web streaming call it before opening a live frame stream.

*Call graph*: called by 2 (_surface_live_admit, stream); 2 external calls (select, workspace_tx).


##### `SurfaceContext.turn_is_terminal`  (lines 1162–1179)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Checks whether a turn has reached a final state by reading the database, not the live stream. Missing turns count as terminal because there is nothing to report.

**Data flow**: It receives a turn id, reads the turn status, and returns true if the row is absent or the status is done, failed, or cancelled.

**Call relations**: Side-channel reporters can use it to avoid posting progress updates after the final answer has already been delivered.

*Call graph*: 2 external calls (select, workspace_tx).


##### `SurfaceContext.latest_turn`  (lines 1181–1198)

```
async def latest_turn(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Finds the newest turn in a conversation. Live surfaces use it when reconnecting or re-rendering the latest open handoffs.

**Data flow**: It receives a conversation id, queries turns in descending sequence order, and returns the first turn id or None.

**Call relations**: Slack, UFO, and web conversation resolution paths call it to resume or inspect the current turn.

*Call graph*: called by 4 (_participating_conversation, channel, _resolve_chat, transcript); 2 external calls (select, workspace_tx).


##### `SurfaceContext.tail`  (lines 1200–1203)

```
def tail(self, turn_id: UUID, since: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: Streams live frames for a turn through the injected tailer. This gives live surfaces one controlled way to watch progress.

**Data flow**: It receives a turn id and optional cursor, passes them to the TurnTailer, and returns the async stream of cursor-frame pairs.

**Call relations**: Debugger, sample, UFO, web event routes, and panel submit flows call it to deliver live updates.

*Call graph*: called by 5 (_events, _surface_frames, channel, submit_intent, _events).


##### `SurfaceContext.spend_rollup`  (lines 1205–1209)

```
async def spend_rollup(self, window_seconds: int) -> SpendReport
```

**Purpose**: Reads workspace-wide spend totals for a rolling time window. Admin or debug surfaces use it to show usage.

**Data flow**: It receives a window length in seconds, opens a workspace transaction, asks SpendRollup to read totals, and returns the report.

**Call relations**: The sample surface and web workspace usage route call it for spend displays.

*Call graph*: called by 2 (_surface_live_admit, workspace_usage); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.write_workspace_file`  (lines 1211–1226)

```
async def write_workspace_file(self, conversation_id: UUID, rel: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes an uploaded file into a conversation’s sandbox workspace before the turn runs. It enforces a maximum size while reading the stream.

**Data flow**: It receives a conversation id, relative path, and async byte chunks. It accumulates chunks up to the configured limit, then writes the bytes through the sandbox carrier.

**Call relations**: Sample, Slack file download, and web upload delivery paths call it so agents can see uploaded files in their workspace.

*Call graph*: called by 3 (_surface_ingest, _download_files, _deliver_uploads).


##### `SurfaceContext.list_agents`  (lines 1228–1254)

```
async def list_agents(self) -> tuple[AgentSummary, ...]
```

**Purpose**: Lists all agents in the workspace for a surface UI. Main agents appear first, followed by name order.

**Data flow**: It queries agent rows for this workspace and returns AgentSummary objects with id, name, model, main flag, and internet setting.

**Call relations**: The web audience code calls it when letting a member choose or view agents.

*Call graph*: called by 1 (web_audience); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.agent_detail`  (lines 1256–1303)

```
async def agent_detail(self, agent_id: UUID) -> AgentDetail | None
```

**Purpose**: Reads detailed configuration for one agent, including prompt digest and bound surfaces. It returns None if the agent is not in this workspace.

**Data flow**: It receives an agent id, reads the agent row and matching surface installations, computes the prompt digest, and returns an AgentDetail.

**Call relations**: The web agent overview panel calls it for the selected agent.

*Call graph*: called by 1 (agent_overview); 4 external calls (__init__, select, workspace_tx, prompt_digest).


##### `SurfaceContext.list_agent_tasks`  (lines 1305–1360)

```
async def list_agent_tasks(self, agent_id: UUID, viewer_member_id: UUID, viewer_is_admin: bool) -> tuple[PortalTask, ...]
```

**Purpose**: Lists recurring tasks for an agent, hiding task content from viewers who should not see it. Owners and admins see different amounts depending on who created the task.

**Data flow**: It receives agent id, viewer member id, and admin flag. It reads tasks under that agent scope, filters visible tasks, fetches creator emails, blanks hidden prompt and description fields, and returns PortalTask rows.

**Call relations**: The web tasks route calls it to render the task list with the same visibility rules chat tools use.

*Call graph*: called by 1 (tasks); 5 external calls (__init__, __init__, select, agent, workspace_tx).


##### `SurfaceContext.object_spec_schema`  (lines 1362–1366)

```
def object_spec_schema(self, kind: str) -> dict[str, Any] | None
```

**Purpose**: Returns the form schema for a registered object kind. This lets the portal render object forms from the same schema used elsewhere.

**Data flow**: It receives an object kind name and returns the stored schema dictionary or None.

**Call relations**: The web tasks page calls it when it needs object-kind form information.

*Call graph*: called by 1 (tasks).


##### `SurfaceContext.agent_skills`  (lines 1368–1385)

```
async def agent_skills(self, agent_id: UUID) -> tuple[PortalSkill, ...]
```

**Purpose**: Lists the skills that a selected agent would load, marking whether each came from the deployment or from a member. This mirrors the runtime skill composition.

**Data flow**: It receives an agent id, binds that agent scope, merges deploy skills with user skills, filters to top-level skills, and returns PortalSkill rows.

**Call relations**: The web skills route calls it to answer what the agent can do.

*Call graph*: called by 1 (skills); 2 external calls (__init__, agent).


##### `SurfaceContext.memory_available`  (lines 1388–1392)

```
def memory_available(self) -> bool
```

**Purpose**: Reports whether a memory search provider is installed. It is a simple gate for memory UI features.

**Data flow**: It checks whether the context has a memory provider and returns a boolean.

**Call relations**: Web memory pages use it before offering search or browse actions.


##### `SurfaceContext.search_memory`  (lines 1394–1404)

```
async def search_memory(self, reader: 'SourceReader', queries: tuple[str, ...]) -> 'tuple[MemoryMatch, ...]'
```

**Purpose**: Searches memory items visible to a given source reader. It refuses to run if no memory provider exists so callers do not mistake missing infrastructure for empty results.

**Data flow**: It receives a reader and query strings, checks that memory is installed, forwards the search, and returns memory matches.

**Call relations**: The web workspace memory route calls it for query-based memory search.

*Call graph*: called by 1 (workspace_memory).


##### `SurfaceContext.recent_memory`  (lines 1406–1419)

```
async def recent_memory(self, subjects: frozenset[str], limit: int, kinds: 'frozenset[str] | None'=None, cursor: 'ListingCursor | None'=None) -> 'ListingPage[MemoryMatch]'
```

**Purpose**: Lists recent memory items for readable subjects, optionally filtered by kind and cursor. This supports browsing memory without a search query.

**Data flow**: It receives subjects, limit, optional kinds, and optional cursor. It checks for a provider and asks it for a paged recent listing.

**Call relations**: The web workspace memory route calls it for memory browsing.

*Call graph*: called by 1 (workspace_memory).


##### `SurfaceContext.memory_kinds`  (lines 1422–1427)

```
def memory_kinds(self) -> tuple[str, ...]
```

**Purpose**: Returns the memory item kinds that can be listed. The portal uses it to build a filter menu.

**Data flow**: It checks that a memory provider exists and returns that provider’s listable kinds.

**Call relations**: Memory UI code reads it after checking memory_available.


##### `SurfaceContext.agent_spend`  (lines 1429–1435)

```
async def agent_spend(self, agent_id: UUID, window_seconds: int) -> AgentSpendReport
```

**Purpose**: Reads spend and caps for one agent in a rolling time window. This gives members an agent-specific usage view.

**Data flow**: It receives an agent id and window seconds, opens a transaction, asks SpendRollup for the agent report, and returns it.

**Call relations**: The web usage route calls it for selected-agent billing information.

*Call graph*: called by 1 (usage); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.member_spend`  (lines 1437–1444)

```
async def member_spend(self, member_id: UUID, window_seconds: int) -> MemberSpendReport
```

**Purpose**: Reads spend and caps for one member in a rolling time window. This lets a member see their own usage without exposing others.

**Data flow**: It receives a member id and window seconds, reads the member report through SpendRollup, and returns it.

**Call relations**: The web workspace usage route calls it for personal usage information.

*Call graph*: called by 1 (workspace_usage); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.list_agent_connections`  (lines 1446–1495)

```
async def list_agent_connections(self, agent_id: UUID, member_id: UUID, *, admin: bool) -> tuple[ConnectionView, ...]
```

**Purpose**: Lists connector accounts granted to an agent that the viewer is allowed to see. Owner emails are hidden unless the viewer is an admin or the owner.

**Data flow**: It receives an agent id, member id, and admin flag. It builds a visibility-filtered query, joins grants to connections and members, and returns ConnectionView rows.

**Call relations**: The web connections route calls it to render account grants with privacy rules applied in the query.

*Call graph*: called by 1 (connections); 5 external calls (__init__, or_, select, workspace_tx, account_object_name).


##### `SurfaceContext.list_artifacts`  (lines 1497–1563)

```
async def list_artifacts(self, member_id: UUID, *, admin: bool, limit: int, cursor: 'ListingCursor | None'=None) -> 'ListingPage[ListedArtifact]'
```

**Purpose**: Returns a paged list of shared files. Admins can see the workspace’s artifacts; regular members see only files from their own conversations.

**Data flow**: It receives viewer member id, admin flag, limit, and optional cursor. It queries shared artifacts joined through turns and conversations, applies visibility, pages by creation time and id, and returns a listing page of ListedArtifact items.

**Call relations**: The web workspace artifacts route calls it and then uses artifact_link to create download links.

*Call graph*: called by 1 (workspace_artifacts); 4 external calls (select, workspace_tx, page_of, page_query).


##### `SurfaceContext.list_member_objects`  (lines 1565–1582)

```
async def list_member_objects(self, kind: str, member_id: UUID | None, *, admin: bool) -> 'ObjectPage | None'
```

**Purpose**: Lists objects of a registered member-owned kind for the portal. It returns None when the kind is not installed.

**Data flow**: It receives a kind, optional member id, and admin flag. It finds the bound object kind, checks that its store supports member-owned listing, then asks the store for a member page.

**Call relations**: The web workspace sites route calls it for site-like objects provided by extensions.

*Call graph*: called by 1 (workspace_sites); 1 external calls (__init__).


##### `SurfaceContext.list_credential_slots`  (lines 1584–1615)

```
async def list_credential_slots(self) -> tuple[CredentialSlotView, ...]
```

**Purpose**: Lists member-fillable credential slots and whether each has a stored value, without ever returning the secret values. This powers credential administration panels.

**Data flow**: It reads filled slot names from the credential table, maps declared slots to object names, filters to member-fillable slots, and returns CredentialSlotView rows.

**Call relations**: Web credential pages and prepared intent submission use it to show which credentials can be filled or rotated.

*Call graph*: called by 2 (submit_intent, workspace_credentials); 4 external calls (__init__, select, named_slots, workspace_tx).


##### `SurfaceContext.workspace_domain`  (lines 1617–1623)

```
async def workspace_domain(self) -> str | None
```

**Purpose**: Reads the workspace’s own email domain. This is used for member onboarding and team administration displays.

**Data flow**: It opens a workspace transaction, calls the shared workspace_domain derivation, and returns the domain string or None.

**Call relations**: is_operator_workspace, join_member, and the web team page call it so all domain decisions use one source.

*Call graph*: called by 3 (is_operator_workspace, join_member, workspace_team); 2 external calls (workspace_tx, workspace_domain).


##### `SurfaceContext.list_members`  (lines 1625–1633)

```
async def list_members(self) -> tuple[SeatEntry, ...]
```

**Purpose**: Lists the workspace roster in stable email order. It supports team administration views.

**Data flow**: It reads a Seats snapshot in a workspace transaction, sorts the member entries by email, and returns them.

**Call relations**: The web workspace team route calls it when rendering members.

*Call graph*: called by 1 (workspace_team); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.list_sources`  (lines 1635–1682)

```
async def list_sources(self, member_id: UUID, *, admin: bool) -> tuple[SourceView, ...]
```

**Purpose**: Lists live source bindings the viewer may see. Shared sources and owned sources are visible to regular members; admins see all.

**Data flow**: It receives member id and admin flag, builds a visibility-filtered source query, converts connector configs with _binding_fields, and returns SourceView rows.

**Call relations**: The web workspace sources route calls it for the source management page.

*Call graph*: calls 1 internal fn (_binding_fields); called by 1 (workspace_sources); 4 external calls (__init__, or_, select, workspace_tx).


##### `SurfaceContext.spend_caps`  (lines 1684–1732)

```
async def spend_caps(self) -> tuple[SpendCapView, ...]
```

**Purpose**: Lists all spend caps for the workspace with readable subject names. This supports the administration billing view.

**Data flow**: It joins spend caps to agent or member rows where appropriate, orders them, and returns SpendCapView objects.

**Call relations**: The web admin index calls it when rendering billing and cap settings.

*Call graph*: called by 1 (admin_index); 4 external calls (__init__, and_, select, workspace_tx).


##### `SurfaceContext.list_installations`  (lines 1734–1750)

```
async def list_installations(self) -> tuple[InstallationSummary, ...]
```

**Purpose**: Lists surface installations bound to this workspace and the agent each one targets. This helps admins understand where chats will land.

**Data flow**: It queries surface_installation rows for the workspace, orders by surface, and returns InstallationSummary objects.

**Call relations**: The web admin index calls it for the agents and installations view.

*Call graph*: called by 1 (admin_index); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_conversations`  (lines 1752–1801)

```
async def list_conversations(self, limit: int=LIST_CONVERSATIONS_LIMIT) -> tuple[ConversationSummary, ...]
```

**Purpose**: Lists recent conversations across all surfaces for workspace debugging. It includes turn counts and last activity times.

**Data flow**: It builds a turn activity subquery, joins it to conversations and members, orders by most recent activity, limits the result, and returns ConversationSummary rows.

**Call relations**: The debugger surface calls it to show a workspace-wide conversation list.

*Call graph*: called by 1 (conversations); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_agent_conversations`  (lines 1803–1882)

```
async def list_agent_conversations(self, agent_id: UUID, member_id: UUID, *, admin: bool, limit: int, surface: str | None=None) -> tuple[ListedConversation, ...]
```

**Purpose**: Lists conversations for one agent as the portal sees them, with readable and disclosable flags. It excludes subagent conversations from the top-level list.

**Data flow**: It receives agent id, viewer member id, admin flag, limit, and optional surface filter. It queries conversations with activity, applies admin/member visibility, and returns ListedConversation objects.

**Call relations**: The web chats and conversations routes call it before deciding which transcript links to show.

*Call graph*: calls 1 internal fn (_readable_audience_values); called by 2 (chats_index, conversations); 7 external calls (__init__, __init__, select, audience_member, conversation_audience, parse_audience, workspace_tx).


##### `SurfaceContext.readable_conversation`  (lines 1884–1925)

```
async def readable_conversation(self, conversation_id: UUID, agent_id: UUID, member_id: UUID, *, admin: bool=False) -> bool
```

**Purpose**: Answers whether a member may read a conversation’s content. It covers own conversations, workspace-shared conversations, and recent admin disclosures for another member’s private conversation.

**Data flow**: It receives conversation, agent, member, and admin flag. It checks the conversation belongs to that agent, compares audience rules, and for admin private reads checks for a recent transcript_access row.

**Call relations**: The web surface’s conversation read gate calls it before serving turns, transcript, files, or subagent details.

*Call graph*: calls 1 internal fn (_readable_audience_values); called by 1 (_readable_conversation); 5 external calls (now, select, audience_member, parse_audience, workspace_tx).


##### `SurfaceContext.conversation_subagent_turns`  (lines 1927–1964)

```
async def conversation_subagent_turns(self, conversation_id: UUID, limit: int=LIST_TURNS_LIMIT) -> tuple[Turn, ...]
```

**Purpose**: Lists turns spawned by turns in a conversation, including nested subagents. This lets views show the tree of helper work under a main request.

**Data flow**: It receives a conversation id and limit, builds a recursive query following parent_turn_id, reads matching turn rows, converts them to Turn records, and returns them.

**Call relations**: The web conversation turns route calls it after the parent conversation has been authorized.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 1 (conversation_turns); 3 external calls (literal, select, workspace_tx).


##### `SurfaceContext.list_turns`  (lines 1966–1982)

```
async def list_turns(self, conversation_id: UUID, limit: int=LIST_TURNS_LIMIT) -> tuple[Turn, ...]
```

**Purpose**: Lists the most recent turns in a conversation in admission order. It returns full durable turn records.

**Data flow**: It receives a conversation id and limit, queries newest rows by sequence, reverses them back to oldest-first order, converts each row to a Turn, and returns the tuple.

**Call relations**: Debugger and web conversation turn routes call it to render conversation history.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 2 (conversation_turns, conversation_turns); 1 external calls (workspace_tx).


##### `SurfaceContext.turn_detail`  (lines 1984–2034)

```
async def turn_detail(self, turn_id: UUID) -> TurnDetail | None
```

**Purpose**: Reads one turn with its accounting ledger and direct child turns. This is the detailed inspection view for a single turn.

**Data flow**: It receives a turn id, reads the turn row, child turn rows, and ledger rows, converts them to Turn, LedgerEntry, and TurnDetail objects, and returns None if the turn is missing.

**Call relations**: Debugger and web routes call it for turn pages, streams, open handoffs, and chat resolution.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 5 (stream, turn, _open_handoffs, _resolve_chat, stream); 4 external calls (__init__, __init__, select, workspace_tx).


##### `SurfaceContext.read_transcript`  (lines 2036–2047)

```
async def read_transcript(self, conversation_id: UUID) -> Conversation | None
```

**Purpose**: Reads the durable transcript blob for a conversation after confirming the conversation belongs to this workspace. This prevents unscoped blob access from crossing tenants.

**Data flow**: It receives a conversation id, checks ownership, fetches the transcript blob if present, decodes it, and returns the Conversation object or None.

**Call relations**: Debugger and web transcript routes call it after higher-level read permission checks.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 2 (conversation_transcript, transcript); 2 external calls (decode, transcript_key).


##### `SurfaceContext.list_compactions`  (lines 2049–2060)

```
async def list_compactions(self, conversation_id: UUID) -> tuple[int, ...]
```

**Purpose**: Lists saved compaction record indexes for a conversation. Compactions are transcript summaries kept alongside the transcript.

**Data flow**: It receives a conversation id, checks ownership, lists matching blob keys, extracts numeric indexes, sorts them, and returns the tuple.

**Call relations**: The debugger compactions route calls it to show which compaction records can be opened.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (conversation_compactions).


##### `SurfaceContext.read_compaction`  (lines 2062–2068)

```
async def read_compaction(self, conversation_id: UUID, index: int) -> CompactionRecord | None
```

**Purpose**: Reads one persisted compaction record for a conversation, if it exists and belongs to this workspace.

**Data flow**: It receives a conversation id and index, checks ownership, then asks the transcript helper to read that compaction record from blob storage.

**Call relations**: The debugger compaction-record route calls it after listing or selecting an index.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (compaction_record); 1 external calls (read_compaction_record).


##### `SurfaceContext.list_workspace_files`  (lines 2070–2076)

```
async def list_workspace_files(self, conversation_id: UUID) -> tuple[WorkspaceFile, ...]
```

**Purpose**: Lists files currently present in a conversation’s sandbox workspace. It returns nothing for foreign or missing conversations.

**Data flow**: It receives a conversation id, checks ownership, and asks the sandbox manager for workspace file entries.

**Call relations**: Debugger and web conversation files routes call it to show files the agent can access.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 2 (workspace_files, conversation_files).


##### `SurfaceContext.read_workspace_file`  (lines 2078–2087)

```
async def read_workspace_file(self, conversation_id: UUID, rel: str) -> AsyncIterator[bytes] | None
```

**Purpose**: Streams one sandbox workspace file out to a surface. It first checks the conversation belongs to this workspace.

**Data flow**: It receives a conversation id and relative path, verifies ownership, then asks the sandbox manager for an async byte stream or None.

**Call relations**: Debugger and web file download routes call it after path and conversation authorization.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 2 (workspace_file, conversation_file).


##### `SurfaceContext.installation`  (lines 2089–2102)

```
async def installation(self, peer_surface: str) -> str | None
```

**Purpose**: Reads this workspace’s installation id for another surface. This helps views make links into the surface where a conversation lives.

**Data flow**: It receives a peer surface name, queries surface_installation for this workspace, and returns the installation id or None.

**Call relations**: The debugger workspace metadata route calls it when showing surface metadata.

*Call graph*: called by 1 (workspace_meta); 2 external calls (select, workspace_tx).


##### `SurfaceContext.transaction`  (lines 2105–2114)

```
async def transaction(self) -> AsyncIterator[AsyncConnection]
```

**Purpose**: Offers a raw workspace-scoped database transaction to a trusted surface extension. The surface is responsible for including workspace_id in its own queries.

**Data flow**: It opens a workspace transaction, yields the async connection, commits on normal exit, and rolls back on error through the transaction manager.

**Call relations**: Surface extensions use it when they need to read their own extension tables outside a turn context.

*Call graph*: 1 external calls (workspace_tx).


##### `SurfaceContext._owned_conversation`  (lines 2116–2126)

```
async def _owned_conversation(self, conversation_id: UUID) -> bool
```

**Purpose**: Checks whether a conversation id belongs to this workspace. It protects blob and sandbox reads that are not automatically scoped by the database.

**Data flow**: It receives a conversation id, queries the conversation table for this workspace, and returns true if a row exists.

**Call relations**: Transcript, compaction, workspace file listing, and workspace file read methods call it before touching blob storage or sandboxes.

*Call graph*: called by 5 (list_compactions, list_workspace_files, read_compaction, read_transcript, read_workspace_file); 2 external calls (select, workspace_tx).


##### `SurfaceContext._turn_query`  (lines 2128–2146)

```
def _turn_query(self) -> sa.Select
```

**Purpose**: Builds the standard select list for reading durable turn rows. This keeps all turn projections consistent.

**Data flow**: It takes no input and returns a SQL select containing the fields needed to build a Turn record.

**Call relations**: conversation_subagent_turns, list_turns, and turn_detail extend this query with their own filters.

*Call graph*: called by 3 (conversation_subagent_turns, list_turns, turn_detail); 1 external calls (select).


##### `SurfaceContext._turn_record`  (lines 2148–2166)

```
def _turn_record(self, row: sa.Row) -> Turn
```

**Purpose**: Converts a database row into a typed Turn object. It also validates nested context and terminal frame data.

**Data flow**: It receives a SQL row, copies scalar fields, parses optional context and terminal JSON into typed records, and returns a Turn.

**Call relations**: All turn-reading methods use it after running _turn_query.

*Call graph*: called by 3 (conversation_subagent_turns, list_turns, turn_detail); 3 external calls (__init__, model_validate, model_validate).


##### `SurfaceInstallationAccess.bind`  (lines 2187–2192)

```
async def bind(self, surface: str, installation_id: str) -> None
```

**Purpose**: Lets a tool bind an installation for a surface declared in its manifest. It prevents tools from registering arbitrary surface names.

**Data flow**: It receives a surface name and installation id, checks the surface is declared, reads the ambient workspace id, and calls the shared binding helper.

**Call relations**: Tool code uses this access object when installing or reconfiguring a surface binding from inside the workspace.

*Call graph*: calls 1 internal fn (_bind_surface_installation); 2 external calls (__init__, ws_current).


##### `SurfaceAuth.workspace`  (lines 2204–2214)

```
async def workspace(self, installation_id: str) -> UUID | None
```

**Purpose**: Finds which workspace owns a shared surface installation id before a request is bound to a workspace. This is the first step in routing shared ingress safely.

**Data flow**: It receives an installation id, queries owner-level surface_installation data for this surface, and returns the workspace id or None.

**Call relations**: Slack’s workspace resolver calls it when an incoming request names a Slack installation.

*Call graph*: called by 1 (resolve_workspace); 2 external calls (select, owner_tx).


##### `SurfaceAuth.open_credential_authorization`  (lines 2216–2228)

```
def open_credential_authorization(self, sealed: str) -> CredentialRequestState | None
```

**Purpose**: Opens a sealed credential authorization before a workspace has been resolved. It returns None instead of raising for invalid or expired seals.

**Data flow**: It receives sealed text, checks that a credential store exists, tries to open the seal, and returns the request state or None.

**Call relations**: Slack’s resolver uses it during OAuth-style pre-binding handshakes.

*Call graph*: called by 1 (resolve_workspace); 1 external calls (open_credential_request).


##### `SurfaceAuth.credential`  (lines 2230–2246)

```
async def credential(self, workspace_id: UUID, slot: str) -> str
```

**Purpose**: Reads a declared credential slot during pre-binding surface authentication. It verifies the workspace exists and that the slot was declared for this resolver.

**Data flow**: It receives workspace id and slot name, checks declaration and credential store, binds the workspace context, verifies the workspace row exists, and returns the credential value.

**Call relations**: Slack authentication uses it to read the signing secret needed to verify incoming requests.

*Call graph*: called by 1 (_auth_signing_secret); 4 external calls (__init__, select, workspace_tx, ws).


##### `SurfaceDeliveryError.__init__`  (lines 2272–2276)

```
def __init__(self, message: str, *, retry_after_seconds: int | None=None) -> None
```

**Purpose**: Creates a delivery error that may include a provider-requested retry delay. This lets the writeback poller respect rate limits such as Retry-After.

**Data flow**: It receives an error message and optional retry-after seconds, rejects negative delays, stores the retry value, and initializes the runtime error.

**Call relations**: Slack posting code raises it when the provider says delivery failed with a retry hint; WritebackPoller._fail_or_retry reads that hint.

*Call graph*: called by 1 (_chat_post).


##### `_writeback_due`  (lines 2318–2334)

```
def _writeback_due(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for writebacks ready to be claimed. A writeback is due only after its turn is terminal and its pending or expired claim state allows work.

**Data flow**: It receives the current time and returns a SQL boolean expression over turn and writeback status and claim expiry fields.

**Call relations**: writeback_workspaces.due uses it to find workspaces with work, and WritebackPoller._claim uses it to claim specific rows.

*Call graph*: called by 2 (_claim, due); 2 external calls (and_, or_).


##### `writeback_workspaces`  (lines 2337–2372)

```
def writeback_workspaces() -> WorkspaceCandidates
```

**Purpose**: Creates a rotating reader that finds workspaces with due writebacks. It keeps the background poller from scanning or draining every workspace at once.

**Data flow**: It initializes a cursor, builds an owner-level candidate reader around the nested due query, and returns the nested candidates function.

**Call relations**: A WritebackPoller receives the returned candidates callable and asks it for workspace ids each polling cycle.

*Call graph*: 1 external calls (owner_candidates).


##### `writeback_workspaces.due`  (lines 2344–2358)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds one paged owner-level query for workspace ids that currently have due writebacks. It advances from the current cursor when set.

**Data flow**: It reads the current time and cursor, selects workspace ids from due writeback rows, groups and orders them, applies a limit, and returns the SQL query.

**Call relations**: owner_candidates wraps this nested query so writeback_workspaces.candidates can execute it safely outside any one workspace.

*Call graph*: calls 1 internal fn (_writeback_due); 2 external calls (now, select).


##### `writeback_workspaces.candidates`  (lines 2362–2370)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: Returns the next page of workspace ids with due writebacks, wrapping around when it reaches the end. This spreads polling across workspaces over time.

**Data flow**: It calls the owner candidate reader, resets the cursor if needed, updates the cursor to the last returned workspace id, and returns the tuple of ids.

**Call relations**: WritebackPoller.run and drain call this through the poller’s candidates field.


##### `_WritebackDeliveryFailed.__init__`  (lines 2380–2383)

```
def __init__(self, phase: Literal['post', 'attach'], error: Exception) -> None
```

**Purpose**: Wraps a failed writeback phase with whether the failure happened while posting the reply or attaching files. This helps retry logging and backoff decisions explain what failed.

**Data flow**: It receives a phase name and original exception, stores both, and initializes the error message from the original exception.

**Call relations**: WritebackPoller._deliver_claimed raises it around surface post or attach failures, and _deliver catches it.

*Call graph*: called by 1 (_deliver_claimed).


##### `WritebackPoller.run`  (lines 2406–2439)

```
async def run(self) -> None
```

**Purpose**: Runs the continuous background loop that delivers completed turns back to durable surfaces. It schedules workspace drains with bounded concurrency and keeps running until cancelled.

**Data flow**: It creates a semaphore and in-flight task map, repeatedly removes finished tasks, asks for candidate workspaces, starts drain tasks for new workspaces, sleeps between polls, and cancels outstanding tasks during shutdown.

**Call relations**: This is the long-running poller entry used by the process. It hands actual work to _drain_workspace.

*Call graph*: calls 1 internal fn (_drain_workspace); 5 external calls (Semaphore, create_task, gather, sleep, log).


##### `WritebackPoller.drain`  (lines 2441–2450)

```
async def drain(self) -> None
```

**Purpose**: Performs one finite drain pass over currently due workspaces. It is useful for tests, maintenance, or one-shot draining.

**Data flow**: It asks for candidate workspace ids, creates a semaphore, gathers one _drain_workspace call per workspace, and raises an ExceptionGroup if any drains failed.

**Call relations**: Unlike run, it does not loop forever. It still uses the same workspace drain path.

*Call graph*: calls 1 internal fn (_drain_workspace); 2 external calls (Semaphore, gather).


##### `WritebackPoller._drain_workspace`  (lines 2452–2470)

```
async def _drain_workspace(self, workspace_id: UUID, semaphore: asyncio.Semaphore) -> None
```

**Purpose**: Claims and delivers a batch of due writebacks for one workspace. It also starts lease-renewal tasks while external delivery is in progress.

**Data flow**: It receives a workspace id and semaphore, enters the workspace context, claims rows, starts one renewal task per row, delivers each row, then cancels and awaits renewals.

**Call relations**: run and drain call it. It coordinates _claim, _renew_claim, and _deliver for the workspace.

*Call graph*: calls 3 internal fn (_claim, _deliver, _renew_claim); called by 2 (drain, run); 4 external calls (create_task, gather, log, ws).


##### `WritebackPoller._claim`  (lines 2472–2505)

```
async def _claim(self, workspace_id: UUID) -> Sequence[sa.Row]
```

**Purpose**: Claims a limited batch of due writeback rows for this worker. The claim prevents other poller workers from delivering the same row at the same time.

**Data flow**: It receives a workspace id, selects due writeback turn ids, updates them to claimed with this worker id and an expiry time, and returns turn ids, reply refs, and last errors.

**Call relations**: _drain_workspace calls it before starting delivery and renewal tasks.

*Call graph*: calls 1 internal fn (_writeback_due); called by 1 (_drain_workspace); 5 external calls (now, timedelta, select, update, workspace_tx).


##### `WritebackPoller._deliver`  (lines 2507–2539)

```
async def _deliver(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None, renewal: asyncio.Task[None]) -> None
```

**Purpose**: Delivers one claimed writeback and records logging, retries, or final failure. It is the error-handling wrapper around the lease-protected delivery path.

**Data flow**: It receives workspace id, turn id, optional reply reference, and renewal task. It times the attempt, calls _deliver_with_lease, handles lost claims and delivery failures, updates retry state through _fail_or_retry, and logs the outcome.

**Call relations**: _drain_workspace calls it for each claimed row.

*Call graph*: calls 2 internal fn (_deliver_with_lease, _fail_or_retry); called by 1 (_drain_workspace); 2 external calls (now, log).


##### `WritebackPoller._deliver_with_lease`  (lines 2541–2570)

```
async def _deliver_with_lease(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None, renewal: asyncio.Task[None]) -> None
```

**Purpose**: Runs external delivery while a claim renewal task keeps the lease alive. It stops renewal before marking the row delivered so its own refresher cannot block the final update.

**Data flow**: It receives workspace id, turn id, reply reference, and renewal task. It starts _deliver_claimed, waits until delivery or renewal finishes, cancels remaining tasks, then calls _mark_delivered after successful delivery.

**Call relations**: _deliver calls it; it hands provider-specific work to _deliver_claimed and closes the row with _mark_delivered.

*Call graph*: calls 2 internal fn (_deliver_claimed, _mark_delivered); called by 1 (_deliver); 3 external calls (create_task, gather, wait).


##### `WritebackPoller._deliver_claimed`  (lines 2572–2594)

```
async def _deliver_claimed(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None) -> None
```

**Purpose**: Builds the writeback payload and calls the registered surface’s post and attach functions. It skips delivery if the surface is missing or has no durable delivery hooks.

**Data flow**: It receives workspace id, turn id, and optional reply reference. It builds the Writeback, finds the SurfaceSpec, creates a SurfaceContext, posts the reply if no reference was recorded, records that reference, then attaches artifacts.

**Call relations**: _deliver_with_lease calls it. It is where core hands finished-turn data to surface extension code.

*Call graph*: calls 3 internal fn (_build, _record_ref, __init__); called by 1 (_deliver_with_lease); 1 external calls (log).


##### `WritebackPoller._renew_claim`  (lines 2596–2599)

```
async def _renew_claim(self, turn_id: UUID) -> None
```

**Purpose**: Keeps a claimed writeback lease alive while delivery is taking a long time. It loops until cancelled.

**Data flow**: It receives a turn id, sleeps for the refresh interval, then calls _refresh_claim repeatedly.

**Call relations**: _drain_workspace starts it beside each delivery, and _deliver_with_lease watches it for failures.

*Call graph*: calls 1 internal fn (_refresh_claim); called by 1 (_drain_workspace); 1 external calls (sleep).


##### `WritebackPoller._refresh_claim`  (lines 2601–2617)

```
async def _refresh_claim(self, turn_id: UUID) -> None
```

**Purpose**: Extends this worker’s claim expiry for a writeback row. If the row no longer belongs to this worker, it reports that the claim was lost.

**Data flow**: It receives a turn id, updates the claimed row with a new expiry time where status and worker id match, and raises _WritebackClaimLost if no row was updated.

**Call relations**: _renew_claim calls it periodically while _deliver_claimed talks to the external surface.

*Call graph*: called by 1 (_renew_claim); 5 external calls (__init__, now, timedelta, update, workspace_tx).


##### `WritebackPoller._build`  (lines 2619–2677)

```
async def _build(self, turn_id: UUID) -> tuple[Writeback, str]
```

**Purpose**: Assembles the Writeback object for a finished turn. This includes final text, usage metadata, handoffs, and shared artifacts.

**Data flow**: It receives a turn id, reads the terminal frame, conversation queue key, surface name, and artifact rows, validates the terminal frame, builds SharedArtifact objects, then returns the Writeback and surface name.

**Call relations**: _deliver_claimed calls it before invoking a surface’s post and attach handlers.

*Call graph*: called by 1 (_deliver_claimed); 5 external calls (__init__, __init__, model_validate, select, workspace_tx).


##### `WritebackPoller._record_ref`  (lines 2679–2691)

```
async def _record_ref(self, turn_id: UUID, reply_ref: str) -> None
```

**Purpose**: Saves the durable surface’s reply reference after posting the main reply. This lets recovery attach files later without reposting the text reply.

**Data flow**: It receives a turn id and reply reference, updates the claimed writeback row owned by this worker, and raises _WritebackClaimLost if the update did not land.

**Call relations**: _deliver_claimed calls it immediately after a successful post and before attachments.

*Call graph*: called by 1 (_deliver_claimed); 3 external calls (__init__, update, workspace_tx).


##### `WritebackPoller._mark_delivered`  (lines 2693–2710)

```
async def _mark_delivered(self, turn_id: UUID) -> None
```

**Purpose**: Marks a claimed writeback as fully delivered and releases the claim. This closes the writeback lifecycle.

**Data flow**: It receives a turn id, updates the row from claimed to delivered, clears claim owner and expiry, and raises _WritebackClaimLost if this worker no longer owns the row.

**Call relations**: _deliver_with_lease calls it only after post and attach have completed successfully.

*Call graph*: called by 1 (_deliver_with_lease); 3 external calls (__init__, update, workspace_tx).


##### `WritebackPoller._fail_or_retry`  (lines 2712–2761)

```
async def _fail_or_retry(self, turn_id: UUID, error: _WritebackDeliveryFailed) -> tuple[str, str, datetime | None]
```

**Purpose**: Turns a delivery failure into either a scheduled retry or a final failed state. It respects provider retry hints but caps them so rows cannot be parked forever.

**Data flow**: It receives a turn id and wrapped delivery error, computes the retry time or terminal failure based on age, truncates the stored error text, updates the writeback row, and returns the outcome, error text, and next attempt time.

**Call relations**: _deliver calls it when _deliver_with_lease reports a post or attach failure.

*Call graph*: called by 1 (_deliver); 5 external calls (now, timedelta, case, update, workspace_tx).


### Workspace Object Types
Exposes agents, conversations, connector accounts, and hosted sites as inspectable and governable workspace objects.

### `core/src/ufo/agents.py`

`domain_logic` · `request handling`

An agent is the workspace’s AI worker: it has a name, a model, a reasoning setting, a system prompt, and a rule for whether it may use public internet access. This file defines how those agents appear in the object system, which is the shared interface used to read and apply changes to workspace things.

The important rule is separation of ownership. Regular agent settings, such as model, reasoning effort, and internet access, can be changed here by an administrator. The system prompt is different: after an agent is created, prompt changes must go through a governed proposal path, which is like requiring a formal approval slip instead of letting someone edit the prompt directly. This prevents two different write paths from fighting over the same sensitive field.

The file also protects workspace boundaries. Every database read and write is scoped to the current workspace. Creating an agent requires both a workspace admin and the main agent, and the new agent starts empty: it does not inherit grants, credentials, sources, or memory. Deleting agents through this object interface is always refused.

One subtle behavior is the model value “auto”. The database may store “auto”, meaning “use whatever model this deployment currently chooses.” When showing status or list summaries, the file reports the actual model being used, without changing the stored setting.

#### Function details

##### `_effective_model`  (lines 53–58)

```
def _effective_model(ctx: ToolContext, stored: str) -> str
```

**Purpose**: Shows the real model an agent is using when its stored setting says “auto”. This lets readers see the concrete model currently in effect without rewriting the saved configuration.

**Data flow**: It receives the tool context and the model value stored in the database. If the stored value is the special “auto” marker, it reads the already-resolved model from the current agent in the context; otherwise it keeps the stored value. It returns the model name that should be shown to users.

**Call relations**: The listing and status views call this helper when they need to display an agent’s model. It sits between raw database values and user-facing output so that “auto” is explained as today’s actual model while still preserving “auto” in the saved spec.

*Call graph*: called by 2 (list, status).


##### `AgentObjects.list`  (lines 99–126)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists the agents in the current workspace and gives each one a short, readable summary. Someone uses this when they want to see what agents exist and their basic settings.

**Data flow**: It opens a workspace database transaction, reads agent rows for the current workspace, orders them by name, and turns each row into a simple object-list entry. Each summary says whether the agent is the main agent, what model it effectively runs on, and whether public internet is allowed. It returns a paged object list shaped by the incoming list query.

**Call relations**: This is the object system’s “show me all agents” path. It asks the database for rows, uses _effective_model to make model display friendly, wraps each result as an object row, and hands the finished rows to the shared pagination helper.

*Call graph*: calls 1 internal fn (_effective_model); 5 external calls (__init__, select, workspace_tx, object_page, ws_current).


##### `AgentObjects.get`  (lines 128–142)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[AgentSpec] | None
```

**Purpose**: Fetches the editable specification for one agent. It is used when a caller wants the saved settings that can be read and potentially applied back later.

**Data flow**: It receives an agent name, looks up that row in the current workspace, and returns nothing if no such agent exists. If it finds the row, it builds an AgentSpec containing the stored model, internet policy, and reasoning effort, plus object metadata such as creation and update times. The prompt is not included here as an editable field for existing agents.

**Call relations**: This method relies on _row for the database lookup, then packages the result into the common object-detail format. It is the read half of the object interface for an agent’s directly editable settings.

*Call graph*: calls 1 internal fn (_row); 2 external calls (__init__, __init__).


##### `AgentObjects.status`  (lines 144–159)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns live status information for one agent, including fields that are not directly editable through normal object apply. In particular, it exposes the current prompt and a digest, which is a compact fingerprint used by the governed prompt-change flow.

**Data flow**: It receives an agent name and looks up the matching row. If the row is missing, it returns nothing. Otherwise it builds a dictionary containing whether this is the main agent, the current prompt, the prompt’s digest, and the effective model that should be shown to users.

**Call relations**: This method uses _row to read the agent, prompt_digest to create the prompt fingerprint, and _effective_model to show the concrete model behind “auto”. It supports the proposal workflow by making the current prompt and its digest visible without allowing the prompt to be edited here.

*Call graph*: calls 2 internal fn (_row, _effective_model); 1 external calls (prompt_digest).


##### `AgentObjects.apply`  (lines 161–195)

```
async def apply(self, ctx: ToolContext, name: str, spec: AgentSpec, old: AgentSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Applies changes to an existing agent’s ordinary settings, or creates a new agent if the old object does not exist. It enforces the main safety rules: only admins can mutate agents, child agents cannot edit other agents, and existing prompts cannot be changed here.

**Data flow**: It receives the requested agent name, the desired spec, and the previous spec if one existed. If there is no previous object, it passes the request to _create. For an existing agent, it rejects any attempt to include a prompt, looks up the current row, checks that the caller has the required admin rights, and then updates the model, internet policy, reasoning effort, and update time in the database. It returns no value, but the database row is changed if all checks pass.

**Call relations**: This is the object system’s write path for agents. It calls _create for first-time creation, _row to confirm and inspect existing agents, and the ToolContext permission checks before writing. It raises the shared object errors when the request is unsafe, unsupported, or aimed at a missing agent.

*Call graph*: calls 4 internal fn (_create, _row, agent_is_main, speaker_is_admin); 6 external calls (__init__, __init__, __init__, update, workspace_tx, ws_current).


##### `AgentObjects._create`  (lines 197–222)

```
async def _create(self, ctx: ToolContext, name: str, spec: AgentSpec) -> None
```

**Purpose**: Creates a brand-new non-main agent with the submitted configuration and initial prompt. It exists because birth is the one moment when this object path is allowed to write a prompt directly.

**Data flow**: It receives the context, new agent name, and requested spec. It checks that the speaker is an admin and that the current agent is the main agent, then verifies that a non-empty prompt was supplied. It inserts a new agent row with a fresh UUID, the current workspace id, the submitted settings, and timestamps. If another row with the same name already exists, the database uniqueness error is turned into a clear name-conflict message.

**Call relations**: Only AgentObjects.apply calls this helper, and only when the target object did not previously exist. It performs the stricter creation checks and then hands the actual insert to the database transaction.

*Call graph*: calls 2 internal fn (agent_is_main, speaker_is_admin); called by 1 (apply); 5 external calls (__init__, insert, workspace_tx, ws_current, uuid4).


##### `AgentObjects.delete`  (lines 224–231)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses to delete an agent through the object interface. This protects agents from being removed by the same generic mechanism used to edit ordinary settings.

**Data flow**: It receives the context, agent name, and optional generation check, but does not inspect or change any database data. It immediately raises a “verb not supported” error explaining that agents cannot be deleted through objects.

**Call relations**: This is the object system’s delete hook for agent objects. Instead of handing off to database deletion, it stops the flow at the boundary with a clear refusal.

*Call graph*: 1 external calls (__init__).


##### `AgentObjects._row`  (lines 233–251)

```
async def _row(self, name: str) -> sa.Row | None
```

**Purpose**: Looks up one agent row in the current workspace. It is the shared database read helper used by the get, status, and apply paths.

**Data flow**: It receives an agent name, opens a workspace database transaction, and selects that agent’s prompt, id, model, main-agent flag, internet policy, reasoning effort, and timestamps. It returns the single matching row, or nothing if the workspace has no agent with that name.

**Call relations**: AgentObjects.get, AgentObjects.status, and AgentObjects.apply all call this helper before deciding what to return or change. By centralizing the lookup, those flows all read the same set of fields and all stay scoped to the current workspace.

*Call graph*: called by 3 (apply, get, status); 3 external calls (select, workspace_tx, ws_current).


### `core/src/ufo/conversations.py`

`domain_logic` · `request handling`

A conversation in this system is not something the object API is allowed to author. It is created by a chat surface, such as a user-facing interface, and later it may be linked from artifacts or scheduled tasks. This file provides the object-layer view of those conversations: enough information to resolve those links, show where a conversation happened, who could see it, when it was created, and what was said.

The main type, ConversationObjects, acts like a read-only service desk. If asked to list conversations, it looks up only the conversations in the current workspace, for the currently selected agent, and only for audiences the caller is allowed to read. If asked for one conversation, it checks that the supplied name is really a UUID (a unique identifier) and that the row is visible. If asked for status, it reads the stored transcript from blob storage, turns the messages into simple “role: text” lines, and, when the transcript is small enough, writes a plain text copy into the caller’s workspace.

A subtle but important safety check happens during status: after reading the transcript, the code confirms the conversation is still visible and unchanged in the relevant way. This avoids returning text for a conversation that became inaccessible while the request was in progress. All write-like operations raise a clear “not supported” error, because conversations are made and retired elsewhere.

#### Function details

##### `ConversationObjects.list`  (lines 54–63)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows the caller a page of conversations they are allowed to see. Each conversation is summarized by its id, chat surface, creation date, and a small field set that can be used for listing.

**Data flow**: It receives a tool context, which includes the caller’s read permissions, and a list query, which says how the results should be paged or filtered. It asks for the visible database rows, turns each row into a simple object-list entry, then passes those entries through the shared paging helper. It returns an ObjectPage containing only conversations visible to this caller.

**Call relations**: This is the public listing path for the conversation object kind. It relies on ConversationObjects._visible_rows to fetch the allowed rows, wraps them as ObjectRow entries, and hands them to object_page so the result follows the same paging rules as other object kinds.

*Call graph*: calls 1 internal fn (_visible_rows); 2 external calls (__init__, object_page).


##### `ConversationObjects.get`  (lines 65–76)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ConversationSpec] | None
```

**Purpose**: Looks up one conversation by name and returns its basic details if the caller may see it. The details include the surface, audience, and timestamps, but not the full transcript.

**Data flow**: It receives the caller context and a name, which is expected to be a UUID string. It asks ConversationObjects._find to locate a visible matching database row. If none is found, it returns null; otherwise it builds a ConversationSpec from the row and wraps it with creation and update times in an ObjectDetail.

**Call relations**: This is the public read path for a single conversation object. It delegates visibility and UUID parsing to ConversationObjects._find, then formats the result into the standard ObjectDetail shape used by the object system.

*Call graph*: calls 1 internal fn (_find); 2 external calls (__init__, __init__).


##### `ConversationObjects.status`  (lines 78–96)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Produces a status summary for a conversation, including how many transcript messages exist and, when small enough, a workspace text file containing the visible exchange. This is how a caller can inspect the conversation’s actual text through the object interface.

**Data flow**: It receives the caller context, the conversation name, and an optional expected generation value, though this object kind does not use that generation for mutation. It first finds the visible conversation row, then reads and formats the transcript. Before returning the transcript-derived result, it checks that the row is still visible with the same audience. It may write a text file into the sandbox workspace, and returns message count, byte size, and the workspace path if a file was written.

**Call relations**: This is the public status path. It calls ConversationObjects._find to confirm the object exists for the caller, ConversationObjects._exchange to read and format the transcript, and ConversationObjects._unchanged_visible as a final safety gate. If visibility disappears during the request, it raises UnknownObject instead of leaking transcript information.

*Call graph*: calls 3 internal fn (_exchange, _find, _unchanged_visible); 1 external calls (__init__).


##### `ConversationObjects.apply`  (lines 98–107)

```
async def apply(self, ctx: ToolContext, name: str, spec: ConversationSpec, old: ConversationSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to create or update a conversation through the object API. This protects the rule that conversations are made by chat surfaces, not by object writes.

**Data flow**: It receives the proposed name, new spec, old spec, caller context, and optional generation value. It does not inspect or save the proposed data. It immediately raises a VerbNotSupported error with a message explaining that conversations are surface-made.

**Call relations**: This is called when the object system tries to apply a create or update operation to a conversation. Instead of handing off to storage, it stops the flow at the boundary by raising VerbNotSupported.

*Call graph*: 1 external calls (__init__).


##### `ConversationObjects.delete`  (lines 109–116)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to delete a conversation through the object API. Deletion or closure is controlled elsewhere, such as by retention rules, so callers cannot remove conversations here.

**Data flow**: It receives the caller context, conversation name, and optional generation value. It does not look up the row or change storage. It immediately raises a VerbNotSupported error saying conversations are not authored through this path.

**Call relations**: This is called when the object system tries to delete a conversation object. It mirrors ConversationObjects.apply by blocking the write-like action before any database or blob operation can happen.

*Call graph*: 1 external calls (__init__).


##### `ConversationObjects._exchange`  (lines 118–138)

```
async def _exchange(self, ctx: ToolContext, conversation_id: UUID) -> tuple[str, ...]
```

**Purpose**: Reads the stored transcript for a conversation and turns it into plain text lines. Each output line is shaped like “role: text”, making the exchange easy to write into a workspace file.

**Data flow**: It receives the caller context and a conversation UUID. It builds the blob-storage key for that transcript, fetches the blob, and decodes it into transcript messages. If no blob exists, it returns an empty tuple. If the blob exists but cannot be decoded, it raises an error. For each message, it keeps string content directly or extracts text blocks from structured content, then returns the non-empty lines as a tuple.

**Call relations**: ConversationObjects.status calls this after it has found the conversation row. This helper does the transcript-specific work, using transcript_key to find the blob and decode to understand its stored format, then hands formatted lines back to status for counting and optional workspace writing.

*Call graph*: called by 1 (status); 2 external calls (decode, transcript_key).


##### `ConversationObjects._unchanged_visible`  (lines 140–153)

```
async def _unchanged_visible(self, ctx: ToolContext, row: sa.Row) -> bool
```

**Purpose**: Checks that a conversation row is still visible to the caller and still has the same audience as the row that was first found. This is a safety check against access changing while status is being prepared.

**Data flow**: It receives the caller context and a database row found earlier. It opens a workspace database transaction, builds the standard visibility query, narrows it to the same conversation id and audience, and asks the database whether such a row exists. It returns true if the conversation is still visible under those conditions, otherwise false.

**Call relations**: ConversationObjects.status calls this after reading the transcript but before returning the result. It reuses ConversationObjects._visible so the same workspace, agent, and audience rules are applied consistently.

*Call graph*: calls 1 internal fn (_visible); called by 1 (status); 3 external calls (exists, select, workspace_tx).


##### `ConversationObjects._find`  (lines 155–165)

```
async def _find(self, ctx: ToolContext, name: str) -> sa.Row | None
```

**Purpose**: Finds one visible conversation by its object name. The name must be a valid UUID, because conversation object names are stored as UUID identifiers.

**Data flow**: It receives the caller context and a name string. It first tries to parse the name as a UUID; if parsing fails, it returns null. If parsing succeeds, it opens a workspace database transaction, runs the standard visibility query narrowed to that id, and returns the single matching row or null if none is visible.

**Call relations**: ConversationObjects.get and ConversationObjects.status both use this as their front door for locating a conversation. It delegates the shared access rules to ConversationObjects._visible, so both callers obey the same visibility policy.

*Call graph*: calls 1 internal fn (_visible); called by 2 (get, status); 2 external calls (workspace_tx, UUID).


##### `ConversationObjects._visible_rows`  (lines 167–170)

```
async def _visible_rows(self, ctx: ToolContext) -> tuple[sa.Row, ...]
```

**Purpose**: Fetches all conversation rows that the caller is allowed to see. It is the database-reading helper behind the public list operation.

**Data flow**: It receives the caller context, opens a workspace database transaction, and executes the shared visibility query. It collects all matching rows and returns them as an immutable tuple.

**Call relations**: ConversationObjects.list calls this before turning rows into list entries. This helper depends on ConversationObjects._visible to define what “visible” means, keeping list behavior aligned with get and status.

*Call graph*: calls 1 internal fn (_visible); called by 1 (list); 1 external calls (workspace_tx).


##### `ConversationObjects._visible`  (lines 172–183)

```
def _visible(self, ctx: ToolContext) -> sa.Select
```

**Purpose**: Builds the database query that defines which conversations are visible to the caller. This is the central access rule for this file.

**Data flow**: It receives the caller context and reads three pieces of scope: the current workspace, the selected agent, and the caller’s allowed read audiences. It builds a SQL query selecting conversation id, surface, audience, and timestamps, filtered to that workspace, that agent, and audiences included in the caller’s read permissions. It returns the query object for other helpers to execute or refine.

**Call relations**: ConversationObjects._visible_rows uses this to list allowed conversations, ConversationObjects._find uses it to search for one allowed conversation, and ConversationObjects._unchanged_visible uses it to re-check access. Because these paths all share this helper, the file has one consistent definition of conversation visibility.

*Call graph*: called by 3 (_find, _unchanged_visible, _visible_rows); 3 external calls (select, object_agent_id, ws_current).


### `extensions/connectors/ufo_ext_connectors/objects.py`

`domain_logic` · `request handling for workspace object listing, inspection, sharing, revocation, and disconnect`

This file is the bridge between the connector system and the workspace object system. In plain terms, it turns “Alice connected her Google account” and “this agent may use Alice’s Google account” into objects that can be listed, inspected, and sometimes changed.

There are two object kinds. A `connection` is the real account link created when a member completes consent with an outside provider. A `connector_grant` is one agent’s access to that connection. The distinction matters: deleting a connection disconnects the account for every agent, while deleting a grant only removes one agent’s access.

The file also protects dangerous actions. Users cannot create these objects directly by applying a spec, because connecting an account requires a third-party consent flow. Instead, creation must happen through `connect_account`. Changing a grant is limited to flipping whether it is shared or private; it cannot secretly point to a different provider account. Disconnecting and revoking require a speaking member, meaning the system knows which human is taking responsibility for the action.

An everyday analogy: the connection is like owning a house key, and a connector grant is like lending a copy of that key to one helper. Taking back one copy does not destroy the original key, but destroying the original lock affects everyone.

#### Function details

##### `_AccountSummary.provider`  (lines 42–42)

```
def provider(self) -> str
```

**Purpose**: This protocol property says that any account summary used here must expose the name of the outside provider, such as a service or platform. It is a shape requirement rather than active behavior.

**Data flow**: An object that claims to be an account summary is expected to provide a provider string. Code that receives such an object can read that string and use it to name or describe the account.

**Call relations**: The `_named` helper relies on this property when it builds stable object names. Both connection summaries and grant summaries fit this expected shape, so the same naming helper can be used for both.


##### `_AccountSummary.account_id`  (lines 45–45)

```
def account_id(self) -> str
```

**Purpose**: This protocol property says that any account summary used here must expose the account identifier from the outside provider. It lets the file treat connection summaries and grant summaries in the same way when naming them.

**Data flow**: An account summary supplies an account ID string. That ID is combined with the provider name to form a workspace object name.

**Call relations**: The `_named` helper reads this property alongside `provider`. The object-listing methods use the resulting names when they publish connections and grants as workspace objects.


##### `_named`  (lines 48–49)

```
def _named(rows: tuple[SummaryT, ...]) -> dict[str, SummaryT]
```

**Purpose**: This helper gives each account-related summary a consistent object name. It prevents the connection and grant listing code from duplicating the same naming rule.

**Data flow**: It receives a tuple of summary records, each with a provider and account ID. For each record, it asks the grants SDK to build the official object name, then returns a dictionary from that name to the original record.

**Call relations**: Connection and grant stores call this while building their object rows. It hands off the actual name formatting to `account_object_name`, so all account objects use the same naming convention.

*Call graph*: called by 2 (_owned_rows, _owned_rows); 1 external calls (account_object_name).


##### `ConnectionObjects._owned_rows`  (lines 60–72)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This lists all connected provider accounts as member-owned workspace objects. It is used when the object system needs a compact catalogue of available connections.

**Data flow**: It asks the grants layer for connection summaries. Each summary becomes an `OwnedRow` containing a name, a human-readable sentence like which provider account it is, and ownership information showing which member owns it and which stored generation it came from.

**Call relations**: The workspace object framework calls this when listing `connection` objects. It uses `_named` to assign stable names, then wraps each grant-layer summary in the generic owner-and-row format expected by the object system.

*Call graph*: calls 1 internal fn (_named); 3 external calls (__init__, __init__, connection_summaries).


##### `ConnectionObjects._detail`  (lines 74–87)

```
async def _detail(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> ObjectDetail[ConnectionSpec] | None
```

**Purpose**: This returns the saved specification and timestamps for one connected account. It is used when someone asks for the full details of a `connection` object rather than just the list view.

**Data flow**: It receives the object name and owner metadata, then fetches current connection summaries and looks for the one whose stored generation ID matches the owner. If found, it returns the provider, account ID, creation time, and update time; if not, it returns nothing because the connection has changed or disappeared.

**Call relations**: The object framework calls this after a connection row has been selected. It reads from `connection_summaries` and packages the result as an `ObjectDetail` with a `ConnectionSpec` inside.

*Call graph*: 3 external calls (__init__, __init__, connection_summaries).


##### `ConnectionObjects._status`  (lines 89–102)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: This returns live status-style information for one connection, such as who owns it, which host it belongs to, and which agents are using it. It gives extra context beyond the basic spec.

**Data flow**: It receives the object name and owner metadata, then finds the matching connection summary by generation ID. If the connection still exists, it returns a small dictionary with owner member ID, host, and agent list; otherwise it returns nothing.

**Call relations**: The object framework calls this when status information is requested for a connection. It uses the same summary source as `_detail`, but returns operational context instead of the saved spec.

*Call graph*: 1 external calls (connection_summaries).


##### `ConnectionObjects._apply_owned`  (lines 104–112)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ConnectionSpec, old: ConnectionSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: This refuses attempts to create or edit a connection object directly. The reason is that connecting an account requires an outside consent flow, so a plain object update is not safe or meaningful.

**Data flow**: It receives the requested connection spec and any old owner information, but does not use them to make a change. It immediately raises a “verb not supported” error explaining that callers must use `connect_account` instead.

**Call relations**: The object framework calls this when someone tries to apply a `connection` object. This method deliberately stops that path and points users toward the connector-specific account connection flow.

*Call graph*: 1 external calls (__init__).


##### `ConnectionObjects._delete_owned`  (lines 114–124)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: This disconnects a connected account. Deleting a `connection` is intentionally broad: it removes the account connection and therefore cuts off every agent grant tied to it.

**Data flow**: It checks that the tool context has access to the grants service and knows which member is speaking. Then it asks the grants service to disconnect the stored connection generation on behalf of that member. If the disconnect does not happen, it raises an error because the connection likely changed during the operation.

**Call relations**: The object framework calls this when an allowed owner or workspace admin deletes a `connection` object. It hands the actual disconnect operation to `ctx.grants.disconnect`, which is the grants layer responsible for changing persisted access state.


##### `ConnectorGrantObjects._admin_can_apply`  (lines 135–136)

```
def _admin_can_apply(self, old: ConnectorGrantSpec, spec: ConnectorGrantSpec) -> bool
```

**Purpose**: This decides the one kind of grant edit a workspace admin is allowed to make: turning a shared grant back to private. It prevents admins from using edit rights to share someone else’s connection more widely.

**Data flow**: It receives the old grant spec and the requested new spec. It returns true only when the old grant was shared and the new spec is exactly the same except that `shared` has become false.

**Call relations**: The member-owned object framework uses this permission hook when deciding whether an admin may apply a change. It supports the policy described by `SHARE_GATE`: owners can share, but admins can only reduce disclosure.

*Call graph*: 1 external calls (model_copy).


##### `ConnectorGrantObjects._owned_rows`  (lines 138–153)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This lists the current agent’s connector grants as member-owned workspace objects. It shows which connected accounts the agent can use and whether each grant is shared or private.

**Data flow**: It asks the grants layer for grant summaries. Each summary is turned into an `OwnedRow` with a stable object name, a short description including provider, account ID, and sharing state, and owner metadata containing the member owner, shared flag, and generation ID.

**Call relations**: The object framework calls this when listing `connector_grant` objects. It uses `_named` for consistent names and `grant_summaries` as the source of truth for current agent access.

*Call graph*: calls 1 internal fn (_named); 3 external calls (__init__, __init__, grant_summaries).


##### `ConnectorGrantObjects._detail`  (lines 155–172)

```
async def _detail(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> ObjectDetail[ConnectorGrantSpec] | None
```

**Purpose**: This returns the saved details for one connector grant. It tells the object system which provider account the grant points to and whether that grant is currently shared.

**Data flow**: It receives an object name and owner metadata, then looks through grant summaries for the matching generation ID. If found, it builds a `ConnectorGrantSpec` with provider, account ID, and shared/private state, plus creation and update timestamps; if not, it returns nothing.

**Call relations**: The object framework calls this when someone inspects a specific `connector_grant`. It reads from `grant_summaries` and returns an `ObjectDetail` in the common object-system format.

*Call graph*: 3 external calls (__init__, __init__, grant_summaries).


##### `ConnectorGrantObjects._status`  (lines 174–188)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: This returns current context for one connector grant, including the owner, host, agent, and sharing state. It helps users understand not just what the grant is, but where and by whom it is being used.

**Data flow**: It receives the object name and owner metadata, then finds the matching grant summary by generation ID. If present, it returns a dictionary with owner member ID, host, agent, and shared flag; if missing, it returns nothing.

**Call relations**: The object framework calls this when status is requested for a grant. It uses the same grant summaries as the list and detail methods, but formats them as status information.

*Call graph*: 1 external calls (grant_summaries).


##### `ConnectorGrantObjects._apply_owned`  (lines 190–225)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ConnectorGrantSpec, old: ConnectorGrantSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: This changes only the sharing setting of an existing connector grant. It refuses creation and refuses any edit that would change which provider account the grant points to.

**Data flow**: It receives the requested spec, the old spec, and owner metadata. It first rejects creation, missing grant access, or missing speaking member. Then it reloads the current grant, verifies that the provider and account ID have not changed, and only proceeds if the requested change is a real shared/private flip. If needed, it asks the grants service to set the new shared value; if the stored grant changed during the edit, it raises an error.

**Call relations**: The object framework calls this when someone applies a `connector_grant` update. This method performs safety checks locally, then hands the actual state change to `ctx.grants.set_shared` so the grants layer remains the source of truth.

*Call graph*: 4 external calls (__init__, __init__, model_copy, grant_summaries).


##### `ConnectorGrantObjects._delete_owned`  (lines 227–237)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: This revokes one agent’s access to a connected account. It leaves the underlying connection and other agents’ grants untouched.

**Data flow**: It checks that a grants service is available and that a speaking member is known. Then it asks the grants service to revoke the grant generation on behalf of that member. If the revoke operation reports that nothing changed, it raises an error because the grant likely changed while being revoked.

**Call relations**: The object framework calls this when an allowed owner or admin deletes a `connector_grant` object. It delegates the real permission removal to `ctx.grants.revoke`, which updates the grant state outside this file.


### `extensions/sites/ufo_ext_sites/objects.py`

`domain_logic` · `request handling for site object list/get/apply/delete operations`

A deployed website is not just a running port in a sandbox. It also needs a stable workspace record so chat, tools, and the in-page site controls all agree about who can see it. This file is that bridge. It turns each hosted site into an object with a predictable name made from the site name plus a short digest of the conversation that created it. That matters because two different conversations might both deploy a site named `dashboard`, and they must not overwrite or confuse each other.

The central class, `SiteObjects`, plugs hosted sites into the project’s object system. It reads the site registry, presents each site with an owner, summary, detail view, and status information, and enforces the rules for changing or deleting it. A site’s owner is the member who deployed it. Private sites are visible only to that creator, while workspace or public sites are shared more broadly. Only the creator may widen or otherwise change visibility; a workspace admin is allowed only to narrow a site back to private. Creating a site through this object API is refused, because a site only truly exists after a deploy chooses and registers the serving port.

Deleting a site unregisters it, so the public link stops resolving. The sandbox may still keep the port alive until its own lifetime ends, but as far as the workspace link registry is concerned, the site is gone.

#### Function details

##### `site_object_name`  (lines 55–59)

```
def site_object_name(conversation_id: UUID, name: str) -> str
```

**Purpose**: Builds the workspace object name for a hosted site. It combines the human site name with a short fingerprint of the conversation ID so two conversations can safely use the same site name.

**Data flow**: It receives a conversation ID and a site name. It turns the conversation ID into a SHA-256 hash, keeps a short prefix, and appends that prefix to the site name. The result is a stable object name such as `dashboard-9f21c0a4e3b7`.

**Call relations**: This is the naming helper used by `_named` when turning a list of hosted site records into a lookup table. All later object lookups rely on this naming rule.

*Call graph*: called by 1 (_named); 1 external calls (sha256).


##### `_named`  (lines 62–63)

```
def _named(sites: Iterable[HostedSite]) -> dict[str, HostedSite]
```

**Purpose**: Turns a collection of hosted site records into a dictionary keyed by their object names. This makes it easy to find a site by the name the object system uses.

**Data flow**: It receives hosted site records. For each one, it calculates the object name from the site’s conversation ID and site name, then stores the site under that key. It returns a name-to-site map.

**Call relations**: It uses `site_object_name` to keep naming consistent. `SiteObjects._member_rows` uses it when listing sites, and `SiteObjects._find` uses it when searching for one named site.

*Call graph*: calls 1 internal fn (site_object_name); called by 2 (_find, _member_rows).


##### `_workspace`  (lines 66–69)

```
def _workspace(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Retrieves the extension workspace context from a tool request. This context is needed to know which workspace store and transaction should be used.

**Data flow**: It receives a tool context. If the context contains an extension context, it returns it. If not, it raises an error because site objects cannot be read or changed without workspace information.

**Call relations**: This is a small safety gate used before touching the site registry. `_sites`, `SiteObjects._owned_rows`, and `SiteObjects._status` call it when they need the active workspace.

*Call graph*: called by 3 (_owned_rows, _status, _sites).


##### `_sites`  (lines 72–74)

```
def _sites(ctx: ToolContext) -> HostedSites
```

**Purpose**: Creates access to the hosted-sites registry for the current workspace. The registry is the stored list of sites, their ports, owners, and visibility.

**Data flow**: It receives a tool context, extracts the workspace context, and uses the workspace ID and current transaction to create a `HostedSites` accessor. It returns that accessor so callers can read or update registered sites.

**Call relations**: It builds on `_workspace`. The object methods use it when changing visibility, unregistering a site, or finding a site by name.

*Call graph*: calls 1 internal fn (_workspace); called by 3 (_apply_owned, _delete_owned, _find); 1 external calls (__init__).


##### `_summary`  (lines 77–78)

```
def _summary(site: HostedSite) -> str
```

**Purpose**: Creates a short human-readable summary for a hosted site. It gives enough information to recognize the site in a list.

**Data flow**: It receives one hosted site record. It reads the site name, visibility, and sandbox port, then returns a compact text string containing those values.

**Call relations**: `SiteObjects._member_rows` calls this while building the list rows shown by the object system.

*Call graph*: called by 1 (_member_rows).


##### `SiteObjects._admin_can_apply`  (lines 93–94)

```
def _admin_can_apply(self, old: SiteSpec, spec: SiteSpec) -> bool
```

**Purpose**: Defines the one visibility change a workspace admin may make to someone else’s site. An admin may make a shared site private, but may not make a private site wider or otherwise broaden access.

**Data flow**: It receives the old site spec and the requested new spec. It compares their visibility values and returns true only when the old visibility was not private and the new visibility is private.

**Call relations**: This method is part of the inherited member-owned object permission flow. The surrounding object framework asks it when deciding whether an admin is allowed to apply a change.


##### `SiteObjects._owned_rows`  (lines 96–97)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[ObjectOwner], ...]
```

**Purpose**: Provides the object system with the list of site rows that have ownership information. These rows are what allow the shared object machinery to decide who can see which sites.

**Data flow**: It receives a tool context, gets the workspace context, and delegates to `_member_rows`. It returns the resulting rows, each describing a site name, summary, and owner.

**Call relations**: This is the entry point the inherited object listing flow uses for owned objects. It hands the real row-building work to `_member_rows` after confirming the workspace context exists.

*Call graph*: calls 2 internal fn (_member_rows, _workspace).


##### `SiteObjects._member_rows`  (lines 99–112)

```
async def _member_rows(self, ext: ExtensionContext | None) -> tuple[OwnedRow[ObjectOwner], ...]
```

**Purpose**: Builds the list of all hosted sites in a workspace, including who owns each one and whether it is shared. This is the raw material for list and visibility filtering.

**Data flow**: It receives an extension context. It opens the hosted-sites registry for that workspace, reads all sites, names them with `_named`, and turns each site into an `OwnedRow` containing the object name, a summary, and an owner record. The owner record marks the creator and whether the site is shared beyond private.

**Call relations**: `SiteObjects._owned_rows` calls this during object listing. It uses `_summary` for display text and `_named` so the listed names match the names used by lookup and mutation.

*Call graph*: calls 2 internal fn (_named, _summary); called by 1 (_owned_rows); 3 external calls (__init__, __init__, __init__).


##### `SiteObjects._detail`  (lines 114–130)

```
async def _detail(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> ObjectDetail[SiteSpec] | None
```

**Purpose**: Returns the durable object details for one site, mainly its visibility and the conversation that created it. This is used when someone asks for the object manifest-like view of a site.

**Data flow**: It receives a tool context, an object name, and the already-known owner information. It looks up the hosted site. If no site is found, it returns nothing. If found, it returns an object detail containing the visibility spec, timestamps, and a link back to the creating conversation.

**Call relations**: The object-get flow calls this after ownership checks. It relies on `_find` to locate the current site record, then packages that record into object-system types.

*Call graph*: calls 1 internal fn (_find); 4 external calls (__init__, __init__, __init__, __init__).


##### `SiteObjects._status`  (lines 132–148)

```
async def _status(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns live, practical status information for one hosted site, including the actual URL to open. This is the view someone needs when they want to visit or inspect the running site.

**Data flow**: It receives a tool context, object name, and owner information. It finds the site. If missing, it returns nothing. If present, it returns a dictionary with the original site name, sandbox port, creator member ID, and a generated site URL based on the public base URL and workspace.

**Call relations**: The object status flow calls this when it needs operational details beyond the stored spec. It uses `_find` for the registry record, `_workspace` for workspace identity, and `site_url` to build the link users open.

*Call graph*: calls 2 internal fn (_find, _workspace); 1 external calls (site_url).


##### `SiteObjects._apply_owned`  (lines 150–165)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SiteSpec, old: SiteSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: Applies an allowed change to an existing site object. In practice, the only supported change is updating the site’s visibility.

**Data flow**: It receives the tool context, object name, requested spec, old spec, and owner. If there is no existing object, it refuses creation because sites must be created by deployment. It then finds the current site record. If the site disappeared, it raises an error. If the requested visibility is already current, it does nothing. Otherwise, it writes the new visibility to the hosted-sites registry.

**Call relations**: The object apply flow calls this after permission checks. It uses `_find` to confirm the site still exists and `_sites` to write the visibility change.

*Call graph*: calls 2 internal fn (_find, _sites); 1 external calls (__init__).


##### `SiteObjects._delete_owned`  (lines 167–171)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: Unhosts an existing site by removing it from the site registry. Once removed, its workspace link no longer resolves.

**Data flow**: It receives the tool context, object name, and owner. It finds the current hosted site. If the site is already gone, it raises an error to report the race. If found, it unregisters that site using its conversation ID and site name.

**Call relations**: The object delete flow calls this after checking that the requester is allowed to delete. It uses `_find` to identify the site and `_sites` to remove it from the registry.

*Call graph*: calls 2 internal fn (_find, _sites).


##### `SiteObjects._find`  (lines 173–174)

```
async def _find(self, ctx: ToolContext, name: str) -> HostedSite | None
```

**Purpose**: Looks up one hosted site by its object name. It is the common helper used whenever another method needs the current registry record for a site.

**Data flow**: It receives a tool context and object name. It reads all hosted sites for the workspace, builds the object-name lookup table, and returns the matching hosted site if one exists. If there is no match, it returns nothing.

**Call relations**: `_detail`, `_status`, `_apply_owned`, and `_delete_owned` all call this before reading or changing one site. It uses `_sites` to read the registry and `_named` to apply the same naming rule used in list output.

*Call graph*: calls 2 internal fn (_named, _sites); called by 4 (_apply_owned, _delete_owned, _detail, _status).


### Object System Foundations
Provides the shared workspace object framework and per-operation agent scoping used by all object implementations.

### `core/src/ufo/objects.py`

`domain_logic` · `startup and request handling`

This file is the front door for “workspace objects,” which are saved items addressed by a kind and a name, like `schedule/daily-report`. Extensions register new object kinds, but this file enforces the common rules so each extension does not have to reinvent them. It checks that kind names and object names follow one grammar, that object specs are safe to store and show back to users, and that manifests are exactly one YAML document with `kind`, `name`, and `spec`.

The file has three main layers. First are small data shapes such as object references, links, listing rows, pages, and details. Second is the store contract, `ObjectStore`, which says what every object kind must be able to do. Third is `ObjectVerbs`, which exposes the five user-facing actions: list, get, explain, apply, and delete.

A major safety piece is `MemberOwnedObjects`, a reusable gate for objects owned by workspace members. It decides who can see, edit, or delete a row. Shared rows, owner rows, and admin rows are treated differently. Some generated rows also carry a generation ID, like a ticket number, so updates refuse to overwrite a row that changed after it was read.

Without this file, object tools would be inconsistent, unsafe to echo into transcripts, and easy to misuse across members, extensions, or agents.

#### Function details

##### `ObjectRef.validate_kind`  (lines 80–83)

```
def validate_kind(cls, value: str) -> str
```

**Purpose**: Checks that an object reference uses a valid kind name. This keeps all object identities in one predictable format.

**Data flow**: It receives the proposed kind string, compares it with the allowed kind-name pattern, and either returns the same string unchanged or raises an error explaining the rule.

**Call relations**: Pydantic calls this automatically when an `ObjectRef` is built. It is part of the object identity gate before references are stored, returned, or linked.


##### `ObjectRef.validate_name`  (lines 87–93)

```
def validate_name(cls, value: str) -> str
```

**Purpose**: Checks that an object reference uses a valid object name. It prevents names that are too long or shaped in a way other tools cannot reliably address.

**Data flow**: It receives the proposed object name, checks its length and allowed characters, and returns it unchanged if valid. If not, it raises an error with the naming rule.

**Call relations**: Pydantic runs this during `ObjectRef` creation. It protects every later use of that reference, including links between objects.


##### `ObjectRef.__str__`  (lines 95–96)

```
def __str__(self) -> str
```

**Purpose**: Turns an object reference into the familiar `kind/name` text form. This is useful for messages and logs.

**Data flow**: It reads the reference’s kind and name fields and combines them into one string. It does not include the optional agent field.

**Call relations**: This is called whenever Python needs a string version of an `ObjectRef`. It provides a short human-readable identity for the object.


##### `_ObjectCursor.validate_rank`  (lines 197–202)

```
def validate_rank(self) -> '_ObjectCursor'
```

**Purpose**: Checks that a list-page cursor contains the right type of saved sort value. This stops corrupted or mismatched cursors from producing confusing pagination.

**Data flow**: It reads the cursor’s rank and value. If the rank says the value should be empty, boolean-like, numeric, or text, it confirms the value matches; otherwise it raises an error.

**Call relations**: Pydantic runs this when `object_page` decodes a cursor from a previous page. It ensures the cursor can safely be used to continue sorting.


##### `object_page`  (lines 205–284)

```
def object_page(rows: tuple[ObjectRow, ...], query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Applies the shared listing behavior for object rows: search, exact filters, sorting, and pagination. Stores can hand it simple rows and get consistent list results.

**Data flow**: It receives lightweight rows and a listing query. It validates available fields, filters matching rows, sorts them with `_sortable`, skips past any cursor, returns up to the page size, and creates a next cursor if more rows remain.

**Call relations**: Member-owned stores call this from `MemberOwnedObjects.list` and `MemberOwnedObjects.member_page` after visibility has already been checked. It hands back an `ObjectPage` that the object-list tool can return to the caller.

*Call graph*: calls 1 internal fn (_sortable); called by 2 (list, member_page); 2 external calls (__init__, __init__).


##### `object_page.value`  (lines 229–234)

```
def value(row: ObjectRow, name: str) -> JsonValue
```

**Purpose**: Looks up the value of one named listing field on a row. It hides whether the field is built in, like `name`, or supplied by the object kind.

**Data flow**: It receives a row and a field name. For `name` and `summary` it returns the row’s direct value; otherwise it reads from the row’s extra fields.

**Call relations**: This helper is used inside `object_page` while searching, filtering, and sorting rows. It keeps those steps from repeating the same field lookup rules.


##### `_sortable`  (lines 287–300)

```
def _sortable(value: JsonValue, field_name: str) -> tuple[_SortRank, str | int | float]
```

**Purpose**: Turns a listing value into a safe sort key. It allows simple values only, so sorting is stable and understandable.

**Data flow**: It receives a value and the field name it came from. It converts missing values, booleans, numbers, and strings into ranked sortable pairs, and rejects complex values such as objects or lists.

**Call relations**: `object_page` calls this while ordering rows and while building or reading pagination boundaries. It is the shared rule for how different field types compare.

*Call graph*: called by 1 (object_page).


##### `ObjectStore.list`  (lines 315–315)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Defines the required list operation for an object kind’s storage layer. A concrete store uses it to return one page of lightweight objects.

**Data flow**: It accepts a tool context and an `ObjectListQuery`, then should read the kind’s storage and return an `ObjectPage`. As a protocol method, this file only states the expected shape.

**Call relations**: `ObjectVerbs._list` calls this on the registered kind’s store. Implementations decide how to find rows, while this file defines the contract they must satisfy.


##### `ObjectStore.get`  (lines 317–317)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SpecT] | None
```

**Purpose**: Defines the required read operation for one object. It returns the object’s spec and metadata if the object exists and is visible to the store.

**Data flow**: It accepts a tool context and object name, then should return an `ObjectDetail` or `None`. The detail may include timestamps, links, visibility of the spec, and a generation ID for race protection.

**Call relations**: `ObjectVerbs._get`, `_apply`, and `_delete` use this before reading, changing, or removing an object. Store implementations supply the real data.


##### `ObjectStore.status`  (lines 319–325)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Defines how a kind reports live, changing state beside the saved spec. For example, a scheduled object might report its next run time.

**Data flow**: It receives the context, object name, and an expected generation. It should return a JSON-like status dictionary, `None` if absent, or refuse if the row changed unexpectedly.

**Call relations**: `ObjectVerbs._get` calls this after `get` so the final response can include both saved configuration and current state. Stores use the expected generation to avoid mixing status from one row with a spec from another.


##### `ObjectStore.apply`  (lines 327–335)

```
async def apply(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Defines how a kind creates or updates an object after core validation has succeeded. The store performs the actual domain change.

**Data flow**: It receives the context, name, validated new spec, previous spec if any, and expected generation. It should write the change or raise a clear refusal or validation error.

**Call relations**: `ObjectVerbs._apply` calls this after parsing YAML, validating the spec, and reading any existing object. This is where extension-specific create or update behavior happens.


##### `ObjectStore.delete`  (lines 337–343)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Defines how a kind deletes an object. The store is responsible for removing the underlying row or refusing the operation.

**Data flow**: It receives the context, name, and expected generation. It should delete the object, report absence, or refuse if deletion is unsupported or unsafe.

**Call relations**: `ObjectVerbs._delete` calls this after reading the existing object. The protocol lets every kind implement deletion in its own storage while sharing the same tool behavior.


##### `MemberOwnedObjects.list`  (lines 399–407)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists only the member-owned objects the current actor is allowed to see. It is the standard visibility filter for in-turn object listings.

**Data flow**: It reads whether the speaker is an admin and the acting member ID, asks the subclass for owned rows, keeps only visible rows, converts them to listing rows, and passes them to `object_page`.

**Call relations**: Concrete member-owned stores inherit this instead of writing their own access checks. It calls `_owned_rows`, `_visible`, and then `object_page` to produce the final page.

*Call graph*: calls 4 internal fn (_owned_rows, _visible, object_page, speaker_is_admin); 1 external calls (__init__).


##### `MemberOwnedObjects.member_page`  (lines 409–426)

```
async def member_page(self, ext: 'ExtensionContext | None', *, member_id: UUID | None, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists visible member-owned objects for a portal-style read outside a normal tool turn. It gives the user interface the same visibility rules as the tool list.

**Data flow**: It receives an extension context, member ID, admin flag, and list query. It asks `_member_rows` for rows, filters them with `_visible`, then returns a paged result through `object_page`.

**Call relations**: Portal or member-facing code can call this when it needs listings without a full `ToolContext`. Subclasses opt in by implementing `_member_rows`.

*Call graph*: calls 3 internal fn (_member_rows, _visible, object_page); 1 external calls (__init__).


##### `MemberOwnedObjects._member_rows`  (lines 428–429)

```
async def _member_rows(self, ext: 'ExtensionContext | None') -> tuple[OwnedRow[OwnerT], ...]
```

**Purpose**: Placeholder for subclasses to provide rows readable outside a tool turn. By default it refuses, so kinds do not accidentally expose portal listings.

**Data flow**: It receives an optional extension context and raises `NotImplementedError` unless a subclass overrides it. No rows are returned by the base version.

**Call relations**: `MemberOwnedObjects.member_page` calls this. A concrete kind implements it when its rows can be safely listed from extension context alone.

*Call graph*: called by 1 (member_page).


##### `MemberOwnedObjects.get`  (lines 431–442)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SpecT] | None
```

**Purpose**: Reads one member-owned object only if the current actor may see it. If the row is generated, it attaches the row’s generation to protect later operations.

**Data flow**: It looks up the owner, checks visibility using the acting member and admin status, and returns `None` if hidden or absent. If visible, it asks `_detail` for the object and adds the generation when available.

**Call relations**: Stores inherited from `MemberOwnedObjects` use this as their `ObjectStore.get`. `ObjectVerbs._get`, `_apply`, and `_delete` rely on it before showing or changing an object.

*Call graph*: calls 4 internal fn (_detail, _owner, _visible, speaker_is_admin); 1 external calls (replace).


##### `MemberOwnedObjects.status`  (lines 444–464)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reads live status for a member-owned object while guarding against hidden rows and stale generated rows. It avoids showing status for the wrong version of a row.

**Data flow**: It finds the owner, checks the expected generation, verifies visibility, asks `_status` for live data, then rechecks owner, generation, and visibility after the read. It returns the status, `None`, or raises an unknown-object error.

**Call relations**: `ObjectVerbs._get` calls this after reading object detail. It calls `_owner`, `_require_current_generation`, `_visible`, and `_status` to make the status safe to display.

*Call graph*: calls 5 internal fn (_owner, _require_current_generation, _status, _visible, speaker_is_admin); 1 external calls (__init__).


##### `MemberOwnedObjects.apply`  (lines 466–492)

```
async def apply(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Creates or updates a member-owned object while enforcing ownership, admin rules, speaker requirements, and generation checks. It is the common edit gate for member-owned kinds.

**Data flow**: It reads the current owner, decides whether the row exists, checks visibility and freshness, verifies whether the actor owns it or has admin permission, optionally requires a live speaker, and finally calls `_apply_owned` to write the change.

**Call relations**: `ObjectVerbs._apply` reaches this through the kind’s store. The method delegates the actual domain write to `_apply_owned` only after all shared safety checks pass.

*Call graph*: calls 7 internal fn (_admin_can_apply, _apply_owned, _owned, _owner, _require_current_generation, _visible, speaker_is_admin); 2 external calls (__init__, __init__).


##### `MemberOwnedObjects.delete`  (lines 494–512)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Deletes a member-owned object only when the actor may see and remove it. It prevents non-owners from deleting private rows unless they are admins.

**Data flow**: It finds the owner, checks generation freshness, rejects missing or hidden rows as unknown, verifies owner or admin permission, optionally requires a live speaker, and calls `_delete_owned`.

**Call relations**: `ObjectVerbs._delete` reaches this through the kind’s store. It uses `_owner`, `_visible`, `_owned`, and `_require_current_generation` before handing deletion to the subclass.

*Call graph*: calls 6 internal fn (_delete_owned, _owned, _owner, _require_current_generation, _visible, speaker_is_admin); 2 external calls (__init__, __init__).


##### `MemberOwnedObjects._owned`  (lines 514–518)

```
def _owned(self, owner: OwnerT, acting: UUID | None) -> bool
```

**Purpose**: Checks whether the acting member is the actual owner of a row. Admin-only rows with no member owner are never treated as owned by a normal member.

**Data flow**: It receives an owner record and an acting member ID. It returns true only when the row has a member ID and it exactly matches the acting member.

**Call relations**: `_visible`, `apply`, and `delete` use this to separate owner permissions from admin permissions.

*Call graph*: called by 3 (_visible, apply, delete).


##### `MemberOwnedObjects._visible`  (lines 520–521)

```
def _visible(self, owner: OwnerT, acting: UUID | None, is_admin: bool) -> bool
```

**Purpose**: Decides whether a row can be seen by the current actor. A row is visible if it is shared, owned by the acting member, or the speaker is an admin.

**Data flow**: It receives an owner record, acting member ID, and admin flag. It checks sharing, ownership through `_owned`, and admin status, then returns a yes-or-no result.

**Call relations**: All member-owned read and write paths call this before exposing that a row exists. It is the central visibility rule for `list`, `member_page`, `get`, `status`, `apply`, and `delete`.

*Call graph*: calls 1 internal fn (_owned); called by 6 (apply, delete, get, list, member_page, status).


##### `MemberOwnedObjects._admin_can_apply`  (lines 523–524)

```
def _admin_can_apply(self, old: SpecT, spec: SpecT) -> bool
```

**Purpose**: Allows a subclass to grant admins a narrow kind-specific update path on member-owned rows. The base version says no.

**Data flow**: It receives the old spec and proposed new spec, then returns `False` unless overridden. It does not change anything.

**Call relations**: `MemberOwnedObjects.apply` calls this when an admin is trying to edit a row that is not simply theirs. Subclasses override it only for safe admin-approved edits.

*Call graph*: called by 1 (apply).


##### `MemberOwnedObjects._require_current_generation`  (lines 526–543)

```
def _require_current_generation(self, name: str, owner: OwnerT | None, expected_generation: UUID | None, action: str) -> None
```

**Purpose**: Refuses an operation if a generated row changed between the earlier read and the current action. This is like checking that the ticket number you read still matches the item on the shelf.

**Data flow**: It receives the object name, current owner, expected generation, and action word. It compares generations for generated rows, treats unexpected generations on plain rows as stale, and raises an error if the row changed.

**Call relations**: `status`, `apply`, and `delete` call this before using or changing a row. It protects generated objects from stale reads and accidental overwrites.

*Call graph*: called by 3 (apply, delete, status).


##### `MemberOwnedObjects._owner`  (lines 545–546)

```
async def _owner(self, ctx: ToolContext, name: str) -> OwnerT | None
```

**Purpose**: Finds the owner record for one named row. It gives the shared gate enough information to check visibility and permissions.

**Data flow**: It asks `_owned_rows` for all rows, searches for the requested name, and returns that row’s owner or `None` if no row matches.

**Call relations**: `get`, `status`, `apply`, and `delete` call this before deciding whether a row exists, is visible, or can be changed.

*Call graph*: calls 1 internal fn (_owned_rows); called by 4 (apply, delete, get, status).


##### `MemberOwnedObjects._owned_rows`  (lines 548–549)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[OwnerT], ...]
```

**Purpose**: Placeholder for subclasses to return all owned rows for the kind during a tool turn. The base class cannot know where each kind stores its rows.

**Data flow**: It receives the tool context and raises `NotImplementedError` unless a subclass overrides it. A concrete implementation returns row names, summaries, and owner records.

**Call relations**: `MemberOwnedObjects.list` and `_owner` call this. Subclasses provide the storage-specific data behind the common permission gate.

*Call graph*: called by 2 (_owner, list).


##### `MemberOwnedObjects._detail`  (lines 551–554)

```
async def _detail(self, ctx: ToolContext, name: str, owner: OwnerT) -> ObjectDetail[SpecT] | None
```

**Purpose**: Placeholder for subclasses to return the full detail for a visible row. The base class performs access checks first, then asks the kind for its actual data.

**Data flow**: It receives the context, object name, and owner. A concrete implementation returns `ObjectDetail` or `None`; the base version raises `NotImplementedError`.

**Call relations**: `MemberOwnedObjects.get` calls this only after the row is known to be visible. Subclasses fill in the saved spec, timestamps, links, and related detail.

*Call graph*: called by 1 (get).


##### `MemberOwnedObjects._status`  (lines 556–559)

```
async def _status(self, ctx: ToolContext, name: str, owner: OwnerT) -> dict[str, JsonValue] | None
```

**Purpose**: Placeholder for subclasses to return live status for a visible row. Live status is separate from the saved spec because it may change over time.

**Data flow**: It receives the context, object name, and owner. A concrete implementation returns a JSON-like status dictionary or `None`; the base version raises `NotImplementedError`.

**Call relations**: `MemberOwnedObjects.status` calls this between generation and visibility checks. Subclasses provide the kind-specific live state.

*Call graph*: called by 1 (status).


##### `MemberOwnedObjects._apply_owned`  (lines 561–569)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, owner: OwnerT | None) -> None
```

**Purpose**: Placeholder for subclasses to actually create or update a row after the common gate has approved it. This is where the kind’s real mutation happens.

**Data flow**: It receives the context, object name, validated spec, old spec if any, and owner if one already existed. A concrete implementation writes the change; the base version raises `NotImplementedError`.

**Call relations**: `MemberOwnedObjects.apply` calls this only after ownership, admin, speaker, visibility, and generation rules pass.

*Call graph*: called by 1 (apply).


##### `MemberOwnedObjects._delete_owned`  (lines 571–572)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: OwnerT) -> None
```

**Purpose**: Placeholder for subclasses to actually delete a row after the common gate has approved it. The base class does not know the kind’s storage layout.

**Data flow**: It receives the context, object name, and owner. A concrete implementation removes the row or related data; the base version raises `NotImplementedError`.

**Call relations**: `MemberOwnedObjects.delete` calls this only after all shared delete checks pass.

*Call graph*: called by 1 (delete).


##### `object_registry`  (lines 603–626)

```
def object_registry(bound: tuple[BoundKind, ...]) -> dict[str, BoundKind]
```

**Purpose**: Builds the deploy-time registry of object kinds and rejects unsafe or conflicting registrations. It is the boot-time gate for the whole object system.

**Data flow**: It receives bound kinds from core and extensions, checks kind-name grammar, detects duplicate names, checks allowed agent-target verbs, validates each spec model, and returns a dictionary keyed by kind name.

**Call relations**: Startup code uses this before serving tools. It calls `_validate_spec_model` for deeper schema safety so bad object kinds fail early instead of during a user request.

*Call graph*: calls 1 internal fn (_validate_spec_model).


##### `_validate_spec_model`  (lines 629–652)

```
def _validate_spec_model(owner: str, kind: ObjectKind) -> None
```

**Purpose**: Checks that a kind’s spec model is safe to store, render, and echo back to users. It blocks unknown keys, secret fields, unsupported JSON shapes, and invalid list field declarations.

**Data flow**: It receives the owner label and object kind, compares declared list fields with model fields, walks all reachable nested models, checks model configuration and field annotations, and asks Pydantic to produce a JSON schema.

**Call relations**: `object_registry` calls this for every registered kind during startup. It uses `_reachable_models` and `_annotation_types` to inspect nested model types.

*Call graph*: calls 2 internal fn (_annotation_types, _reachable_models); called by 1 (object_registry).


##### `_reachable_models`  (lines 655–669)

```
def _reachable_models(model: type[BaseModel]) -> tuple[type[BaseModel], ...]
```

**Purpose**: Finds every nested Pydantic model reachable from a spec model. This lets validation rules apply not just to the top-level spec but also to embedded objects.

**Data flow**: It starts with one model, walks through field annotations, collects nested `BaseModel` classes, avoids repeats, and returns the discovered models.

**Call relations**: `_validate_spec_model` calls this before checking model settings and fields. It relies on `_annotation_types` to unpack complicated type annotations.

*Call graph*: calls 1 internal fn (_annotation_types); called by 1 (_validate_spec_model).


##### `_annotation_types`  (lines 672–679)

```
def _annotation_types(annotation: object) -> tuple[object, ...]
```

**Purpose**: Flattens a type annotation into the actual types inside it. This helps the validator see through containers and unions.

**Data flow**: It receives an annotation, asks Python for its type arguments, recursively expands any nested arguments, and returns a tuple of discovered annotation pieces.

**Call relations**: `_reachable_models` uses this to find nested models, and `_validate_spec_model` uses it to detect secret-bearing field types.

*Call graph*: called by 2 (_reachable_models, _validate_spec_model); 1 external calls (get_args).


##### `ObjectVerbs.tools`  (lines 745–813)

```
def tools(self) -> tuple[ToolDef, ...]
```

**Purpose**: Creates the five tool definitions exposed to the assistant: list, get, explain, apply, and delete. These definitions tell the tool system what inputs each action accepts and which method runs it.

**Data flow**: It reads the `ObjectVerbs` instance, builds five `ToolDef` objects with names, descriptions, input models, handlers, and safety flags, and returns them as a tuple.

**Call relations**: Tool registration code calls this to make object actions available. Each returned tool points back to one of this class’s private handler methods.

*Call graph*: 1 external calls (__init__).


##### `ObjectVerbs._list`  (lines 815–847)

```
async def _list(self, ctx: ToolContext, args: ObjectListInput) -> ToolResult
```

**Purpose**: Implements the object listing tool. With no kind, it lists registered kinds; with a kind, it lists visible instances of that kind.

**Data flow**: It receives the tool context and list arguments. It either returns kind descriptions, or resolves the kind, checks any agent target, builds an `ObjectListQuery`, asks the store to list rows, and returns JSON with objects, cursor, and possibly agent name.

**Call relations**: The `object_list` tool calls this. It uses `_resolve`, `_target`, `_bound_ctx`, the store’s `list`, and `_json_result` to turn registry and store data into a tool response.

*Call graph*: calls 4 internal fn (_bound_ctx, _resolve, _target, _json_result); 2 external calls (__init__, object_agent).


##### `ObjectVerbs._get`  (lines 849–885)

```
async def _get(self, ctx: ToolContext, args: ObjectGetInput) -> ToolResult
```

**Purpose**: Implements the object read tool for one named object. It returns the saved spec, live status, links, timestamps, and agent information when relevant.

**Data flow**: It resolves the kind, binds the context to the owning extension, checks any agent target, reads detail from the store, raises if absent, reads status using the observed generation, formats links and timestamps, and returns YAML text.

**Call relations**: The `object_get` tool calls this. It coordinates `_resolve`, `_bound_ctx`, `_target`, the store’s `get` and `status`, and the object-agent scope.

*Call graph*: calls 3 internal fn (_bound_ctx, _resolve, _target); 5 external calls (__init__, __init__, __init__, object_agent, safe_dump).


##### `ObjectVerbs._explain`  (lines 887–900)

```
async def _explain(self, ctx: ToolContext, args: ObjectExplainInput) -> ToolResult
```

**Purpose**: Implements the kind explanation tool. It tells a caller how to author objects of a given kind before they create or update one.

**Data flow**: It receives the context and kind name, resolves the kind, and returns JSON containing the description, guidance, agent-target verbs, name rule, and JSON schema for the spec.

**Call relations**: The `object_explain` tool calls this. It uses `_resolve` and `_json_result`; it does not call the store because it only reports registered kind metadata.

*Call graph*: calls 2 internal fn (_resolve, _json_result).


##### `ObjectVerbs._apply`  (lines 902–944)

```
async def _apply(self, ctx: ToolContext, args: ObjectApplyInput) -> ToolResult
```

**Purpose**: Implements create or update from a YAML manifest. It validates the common envelope and spec before allowing the kind’s store to change anything.

**Data flow**: It parses the manifest, resolves the kind, checks agent targeting, validates the object name, validates the spec with the kind’s model, reads any existing object, decides create versus update, checks cross-agent support, calls the store’s `apply`, and returns JSON saying what happened.

**Call relations**: The `object_apply` tool calls this. It uses `_parse_envelope`, `_validate_name`, `_resolve`, `_target`, `_bound_ctx`, and `_json_result`, then hands the real write to the store.

*Call graph*: calls 6 internal fn (_bound_ctx, _resolve, _target, _json_result, _parse_envelope, _validate_name); 3 external calls (__init__, __init__, object_agent).


##### `ObjectVerbs._delete`  (lines 946–967)

```
async def _delete(self, ctx: ToolContext, args: ObjectDeleteInput) -> ToolResult
```

**Purpose**: Implements deletion of one object. It first reads the object so it can refuse missing names and echo the deleted spec afterward.

**Data flow**: It resolves the kind, binds the context, checks any agent target, reads the existing object, raises if missing, calls the store’s `delete` with the observed generation, and returns JSON with the deleted spec if visible.

**Call relations**: The `object_delete` tool calls this. It uses `_resolve`, `_bound_ctx`, `_target`, the store’s `get` and `delete`, and `_json_result`.

*Call graph*: calls 4 internal fn (_bound_ctx, _resolve, _target, _json_result); 2 external calls (__init__, object_agent).


##### `ObjectVerbs._resolve`  (lines 969–974)

```
def _resolve(self, kind: str) -> BoundKind
```

**Purpose**: Looks up a kind name in the registry and gives a helpful error if it is unknown. This keeps every verb’s kind lookup consistent.

**Data flow**: It receives a kind string, checks the registry, and returns the matching `BoundKind`. If not found, it raises `UnknownKind` with the registered kind names.

**Call relations**: All five object verb handlers call this before doing kind-specific work. It is the common doorway from user-provided kind text to registered kind behavior.

*Call graph*: called by 5 (_apply, _delete, _explain, _get, _list); 1 external calls (__init__).


##### `ObjectVerbs._bound_ctx`  (lines 976–977)

```
def _bound_ctx(self, ctx: ToolContext, bound: BoundKind) -> ToolContext
```

**Purpose**: Rebinds a tool context to the extension that owns the object kind. This lets store code run with the correct extension context.

**Data flow**: It receives the current tool context and a bound kind, copies the context, replaces its extension field, and returns the adjusted context.

**Call relations**: `_list`, `_get`, `_apply`, and `_delete` call this before invoking store methods. It connects core tool handling to extension-owned storage logic.

*Call graph*: called by 4 (_apply, _delete, _get, _list); 1 external calls (replace).


##### `ObjectVerbs._target`  (lines 979–1025)

```
async def _target(self, ctx: ToolContext, bound: BoundKind, name: str, verbs: frozenset[AgentTargetVerb]) -> ObjectAgent | None
```

**Purpose**: Validates and resolves an optional target agent for agent-scoped object kinds. It prevents ordinary agents, subagents, or background turns from reaching across agent boundaries.

**Data flow**: It receives the current context, bound kind, requested agent name, and allowed verbs. If no name is given it returns `None`; otherwise it checks the kind allows targeting, reads the current and target agents from the database, enforces main-agent and live-member rules, and returns an `ObjectAgent`.

**Call relations**: `_list`, `_get`, `_apply`, and `_delete` call this before entering an `object_agent` scope. It uses the workspace database transaction to confirm the current and target agents.

*Call graph*: called by 4 (_apply, _delete, _get, _list); 3 external calls (__init__, select, workspace_tx).


##### `_parse_envelope`  (lines 1028–1046)

```
def _parse_envelope(manifest: str) -> tuple[str, str, Mapping[str, object]]
```

**Purpose**: Parses and validates the YAML manifest used by object apply. It ensures the caller supplied exactly the shared wrapper fields before spec validation begins.

**Data flow**: It receives manifest text, checks byte size, safely loads YAML, confirms it is a mapping with exactly `kind`, `name`, and `spec`, checks their basic types, and returns the kind, name, and spec mapping.

**Call relations**: `ObjectVerbs._apply` calls this as its first validation step. Later steps resolve the kind, validate the name, and validate the spec model.

*Call graph*: called by 1 (_apply); 2 external calls (__init__, safe_load).


##### `_validate_name`  (lines 1049–1054)

```
def _validate_name(name: str) -> None
```

**Purpose**: Checks a create or update object name against the shared object-name rule. This keeps all kinds addressable in the same way.

**Data flow**: It receives a name string, checks length and allowed pattern, and either returns nothing on success or raises `InvalidName` on failure.

**Call relations**: `ObjectVerbs._apply` calls this after parsing the manifest and before asking the store to create or update anything.

*Call graph*: called by 1 (_apply); 1 external calls (__init__).


##### `_json_result`  (lines 1057–1058)

```
def _json_result(payload: Mapping[str, object]) -> ToolResult
```

**Purpose**: Wraps a Python mapping as a JSON tool response. It is a small helper so several object verbs return results in the same format.

**Data flow**: It receives a payload mapping, converts it to a JSON string, wraps that text in `TextContent`, then returns a `ToolResult`.

**Call relations**: `ObjectVerbs._list`, `_explain`, `_apply`, and `_delete` call this when their response is JSON rather than YAML.

*Call graph*: called by 4 (_apply, _delete, _explain, _list); 3 external calls (__init__, __init__, dumps).


### `core/src/ufo/object_scope.py`

`domain_logic` · `request handling`

Some parts of the system audit or record actions under an “agent,” meaning the named identity responsible for the action. Most of the time, that identity comes from the normal current agent context. But object dispatch can choose a more specific agent namespace for a particular object operation. This file provides the small private tool that makes that possible.

It uses a ContextVar, which is Python’s way of storing values that are local to the current task or request. That matters in asynchronous code: two tasks can run at the same time without accidentally sharing the same selected object agent. An everyday analogy is a sticky note attached to one worker’s clipboard, not a sign posted on the wall for everyone.

The ObjectAgent data class is the small record being stored: it has an agent UUID and a human-readable name. The object_agent context manager temporarily places one of these records into the current task’s context. When the block finishes, even if there is an error, the old value is restored. The object_agent_id function then answers the practical question: “Which agent ID should this object operation use?” If an object-specific target is set, it returns that. Otherwise, it falls back to the normal current agent.

#### Function details

##### `object_agent`  (lines 24–32)

```
def object_agent(target: ObjectAgent | None) -> Iterator[None]
```

**Purpose**: Temporarily selects an object-specific agent for the code inside a with-block. This is used when object dispatch has decided that an operation should be audited or interpreted under a particular agent identity.

**Data flow**: It receives either an ObjectAgent record or None. If it gets None, it simply lets the wrapped code run unchanged. If it gets an ObjectAgent, it stores that record in the current task-local context before the wrapped code runs, then restores the previous value afterward so the choice does not leak into later work.

**Call relations**: Code that performs object dispatch enters this context when it wants later lookups to see a specific object agent. While that block is active, object_agent_id can read the temporary target. When the block ends, object_agent cleans up the context so other operations fall back to their own agent state.


##### `object_agent_id`  (lines 35–37)

```
def object_agent_id() -> UUID
```

**Purpose**: Returns the agent ID that should be used for the current object operation. It prefers the object-specific agent if one has been selected; otherwise it uses the normal current agent.

**Data flow**: It reads the task-local object agent target. If one is present, it returns that target’s UUID. If no object target is set, it asks agent_current for the broader current agent and returns that agent’s ID.

**Call relations**: This is the read side of the object-agent scope. It is called when code needs the correct agent identity for auditing or object handling. If object_agent has set a temporary target, this function uses it; if not, it hands off to ufo.agent_scope.agent_current to get the usual agent identity.

*Call graph*: 1 external calls (agent_current).


### Seat Eligibility Policy
Centralizes the rules that decide whether a workspace member is allowed to receive agent answers.

### `core/src/ufo/seats.py`

`domain_logic` · `request handling and background enforcement`

A “seat” is permission for a workspace member to receive answers from the agent. This matters because some workspaces may have a limited number of allowed seats, while other deployments have no limit and should let everyone through with no extra database cost.

The file is the central rulebook for that decision. It can tell whether a workspace is gated at all, whether a specific member is currently seated, and what message should be used when someone cannot be answered. It also creates members, grants seats, revokes seats, and protects one important safety rule: the last seated admin cannot lose their seat, because admins manage seats through chat.

The code treats member creation and seating as separate ideas. A refused person can still exist as a member, with identity and memory, but the agent will not answer them until a seat is granted. Think of it like creating a building badge record before activating door access.

The `Seats` class is the main tool for one workspace’s seat state. It reads and writes through a database connection supplied by the caller, so the same rules can run inside admission checks, background sweeps, or billing-extension tools without drifting apart. The file also includes small helpers for email-domain validation, finding a workspace’s domain, checking admin status, locating an admin conversation, and building workspace candidates for seat-reporting jobs.

#### Function details

##### `gate_member`  (lines 44–58)

```
def gate_member(speaker_member_id: UUID | None, admission_source: TurnAdmissionSource, on_behalf_of_member_id: UUID | None) -> UUID | None
```

**Purpose**: Decides which member a turn should be checked against for seat access. Usually this is the speaker, but scheduled work is checked against the member it is acting for.

**Data flow**: It receives the speaker member id, the source of the turn, and an optional “on behalf of” member id. It first uses the speaker if there is one; if the turn is scheduled, it uses the member the scheduled turn represents; otherwise it returns no member to gate.

**Call relations**: This is the shared rule used wherever the system needs to know whose seat matters for a turn. By using one helper, admission, resume, dispatch, and per-round checks do not accidentally disagree about the same turn.


##### `seat_gate_absent`  (lines 61–67)

```
def seat_gate_absent(workspace_id: UUID) -> bool
```

**Purpose**: Provides a quick yes-or-no answer for whether a workspace was recently seen to have no seat limits. This lets common unlimited workspaces skip a database check for a short time.

**Data flow**: It receives a workspace id and looks in a small in-memory cache. If the cache says the workspace’s no-limit finding has not expired, it returns true; otherwise it returns false.

**Call relations**: Per-round enforcement can call this before doing heavier database work. The cache entries it reads are written by `_note_absent_limit` after `Seats.gated` or `Seats.admits` confirms the workspace has no seat-related limits.

*Call graph*: 1 external calls (monotonic).


##### `_note_absent_limit`  (lines 70–75)

```
def _note_absent_limit(workspace_id: UUID) -> None
```

**Purpose**: Records that a workspace currently has no seat limit or included-seat setting. This supports the short-lived fast path used by `seat_gate_absent`.

**Data flow**: It receives a workspace id, checks the current clock time, removes expired cache entries if the cache is full, and stores a new expiry time for this workspace.

**Call relations**: `Seats.gated` and `Seats.admits` call this after a database read proves the workspace is ungated. Later, `seat_gate_absent` can use that note to avoid another database round trip until the short timeout passes.

*Call graph*: called by 2 (admits, gated); 1 external calls (monotonic).


##### `SeatSnapshot.seated`  (lines 105–106)

```
def seated(self) -> int
```

**Purpose**: Counts how many members in a snapshot currently have seats. It gives callers a quick total without making them repeat the counting logic.

**Data flow**: It reads the snapshot’s member entries, counts the entries marked as seated, and returns that number.

**Call relations**: This property sits on the `SeatSnapshot` data object returned by `Seats.snapshot`. Any display or reporting code using that snapshot can ask it for the seated count directly.


##### `Seats.gated`  (lines 117–131)

```
async def gated(self, connection: AsyncConnection) -> bool
```

**Purpose**: Checks whether this workspace enforces seats at all. If both seat limit and included-seat allowance are unset, the workspace is treated as open to everyone.

**Data flow**: It reads the workspace’s seat settings from the database. If both settings are empty, it records the fast-path cache note and returns false; otherwise it returns true.

**Call relations**: Admission or enforcement code can call this when it only needs to know whether seat checks apply. When it discovers an ungated workspace, it hands that information to `_note_absent_limit` so later checks can be cheaper.

*Call graph*: calls 1 internal fn (_note_absent_limit); 2 external calls (execute, select).


##### `Seats.admits`  (lines 133–160)

```
async def admits(self, connection: AsyncConnection, member_id: UUID) -> bool
```

**Purpose**: Checks whether a particular member may currently be answered by the agent in this workspace. Ungated workspaces admit existing members automatically; gated workspaces require the member to be seated.

**Data flow**: It receives a database connection and member id, then reads the matching member row together with workspace seat settings. If there is no such member it returns false; if the workspace is ungated it caches that fact and returns true; otherwise it returns whether the member has a seat timestamp.

**Call relations**: This is the core yes-or-no gate used when deciding whether the agent may respond. It calls `_note_absent_limit` for unlimited workspaces, sharing the fast path with `Seats.gated`.

*Call graph*: calls 1 internal fn (_note_absent_limit); 2 external calls (execute, select).


##### `Seats.snapshot`  (lines 162–194)

```
async def snapshot(self, connection: AsyncConnection) -> SeatSnapshot
```

**Purpose**: Builds a readable picture of the workspace’s current seat state. It is useful for admin views, reports, or tools that need to show who is seated and what the limits are.

**Data flow**: It reads the workspace’s limit settings, then reads all workspace members in creation order. It turns each database row into a `SeatEntry`, wraps them with the limits in a `SeatSnapshot`, and returns that snapshot.

**Call relations**: This function is a read-only companion to the write methods such as `grant` and `revoke`. It gathers the same seat facts those methods change, but packages them for display or decision-making.

*Call graph*: 4 external calls (__init__, __init__, execute, select).


##### `Seats.grant`  (lines 196–208)

```
async def grant(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Gives a seat to the workspace member with the given email address. It is safe to call again for someone already seated, and it refuses if the workspace’s hard seat limit is already full.

**Data flow**: It locks and reads the workspace limits, finds the member by email, and stops early if the member is already seated. If a hard limit exists and the seated count has reached it, it raises `SeatLimitReached`; otherwise it writes a seated timestamp for the member.

**Call relations**: Admin tools or billing-extension flows call this when an explicit seat grant is approved. It relies on `_locked_limits`, `_member_by_email`, `_seated_count`, and `_seat` so the checking and writing happen in a controlled order.

*Call graph*: calls 4 internal fn (_locked_limits, _member_by_email, _seat, _seated_count); 1 external calls (__init__).


##### `Seats.revoke`  (lines 210–225)

```
async def revoke(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Removes a seat from the workspace member with the given email address. It is safe to call for someone already unseated, but it refuses to remove the last seated admin.

**Data flow**: It locks the workspace limits, finds the member by email, and returns if the member has no seat. If the member is an admin and is the only seated admin, it raises `LastAdminSeatRevocation`; otherwise it clears the member’s seated timestamp and updates the row time.

**Call relations**: Admin tools call this when a seat should be taken away. It uses `_locked_limits`, `_member_by_email`, and `_seated_admin_count` before writing, so active turns can later be parked by admission or per-round enforcement rather than being changed here.

*Call graph*: calls 3 internal fn (_locked_limits, _member_by_email, _seated_admin_count); 3 external calls (__init__, execute, update).


##### `Seats.ensure_limit`  (lines 227–239)

```
async def ensure_limit(self, connection: AsyncConnection, limit: int) -> None
```

**Purpose**: Sets the workspace’s hard seat limit only if no limit has been set yet. This prevents repeated setup events from overwriting an operator’s later change.

**Data flow**: It receives a desired limit, rejects values below one, and updates the workspace row only where the current seat limit is empty. It does not return a value.

**Call relations**: Billing or setup code can call this when establishing seat rules for the first time. It does not call other helpers because it is a narrow one-field database update.

*Call graph*: 2 external calls (execute, update).


##### `Seats.ensure_included`  (lines 241–253)

```
async def ensure_included(self, connection: AsyncConnection, included: int) -> None
```

**Purpose**: Sets the workspace’s included-seat allowance only if it is not already set. This is the number of seats that can be handed out automatically before explicit admin approval is needed.

**Data flow**: It receives an included-seat count, rejects values below one, and updates the workspace row only if the current included-seat value is empty. It does not return a value.

**Call relations**: Plan setup or billing-extension code can call this to record the free included allowance. It mirrors `Seats.ensure_limit` so initial setup is safe to repeat.

*Call graph*: 2 external calls (execute, update).


##### `Seats.auto_seat`  (lines 255–264)

```
async def auto_seat(self, connection: AsyncConnection, member_id: UUID) -> None
```

**Purpose**: Automatically seats a newly created member when there is still room in the silent allowance. If the allowance is full, it leaves the member unseated so an admin must explicitly grant access.

**Data flow**: It locks and reads the workspace limits, chooses the included-seat allowance if present or otherwise the hard limit, then counts currently seated members. If the chosen bound is full it returns without changing anything; otherwise it writes the new member’s seated timestamp.

**Call relations**: `create_member` calls this immediately after successfully inserting a new member. It uses `_locked_limits`, `_seated_count`, and `_seat` to apply the same seating rule for every member-creation path.

*Call graph*: calls 3 internal fn (_locked_limits, _seat, _seated_count).


##### `Seats._locked_limits`  (lines 266–274)

```
async def _locked_limits(self, connection: AsyncConnection) -> tuple[int | None, int | None]
```

**Purpose**: Reads the workspace’s seat limit and included-seat allowance while locking the workspace row. The lock helps prevent two concurrent changes from counting the same available seat.

**Data flow**: It receives a database connection, selects the workspace’s two seat settings with a database row lock, and returns them as a pair.

**Call relations**: `Seats.grant`, `Seats.revoke`, and `Seats.auto_seat` call this before making decisions that depend on current seat counts. It is an internal helper that keeps those write flows serialized around the workspace row.

*Call graph*: called by 3 (auto_seat, grant, revoke); 2 external calls (execute, select).


##### `Seats._member_by_email`  (lines 276–293)

```
async def _member_by_email(self, connection: AsyncConnection, email: str) -> tuple[UUID, datetime | None, bool]
```

**Purpose**: Finds a workspace member by email and returns the details needed for seat changes. It treats email case-insensitively and raises a clear error if no member exists.

**Data flow**: It receives an email address, trims and lowercases it for comparison, and searches within this workspace. If it finds a row, it returns the member id, current seated timestamp, and admin flag; if not, it raises `UnknownMember`.

**Call relations**: `Seats.grant` and `Seats.revoke` use this before changing a seat. It keeps the email lookup behavior identical for both grant and revoke operations.

*Call graph*: called by 2 (grant, revoke); 3 external calls (__init__, execute, select).


##### `Seats._seated_count`  (lines 295–303)

```
async def _seated_count(self, connection: AsyncConnection) -> int
```

**Purpose**: Counts how many members in the workspace currently have seats. This is used to decide whether another member can be seated.

**Data flow**: It queries the member table for this workspace, counts rows whose seated timestamp is present, and returns the count as an integer.

**Call relations**: `Seats.grant` uses this to enforce the hard limit, and `Seats.auto_seat` uses it to enforce the automatic-seating allowance.

*Call graph*: called by 2 (auto_seat, grant); 2 external calls (execute, select).


##### `Seats._seated_admin_count`  (lines 305–314)

```
async def _seated_admin_count(self, connection: AsyncConnection) -> int
```

**Purpose**: Counts how many admins in the workspace currently have seats. This protects the system from removing the last admin who can manage seats.

**Data flow**: It queries the member table for rows in this workspace that are both seated and marked as admins, then returns the count.

**Call relations**: `Seats.revoke` calls this only when the target member is an admin. If the count is one, revoke raises an error instead of clearing the seat.

*Call graph*: called by 1 (revoke); 2 external calls (execute, select).


##### `Seats._seat`  (lines 316–321)

```
async def _seat(self, connection: AsyncConnection, member_id: UUID) -> None
```

**Purpose**: Marks a member as seated. It is the small shared write used after the calling code has already decided seating is allowed.

**Data flow**: It receives a member id and updates that member row with the current time as `seated_at` and also refreshes `updated_at`. It does not return a value.

**Call relations**: `Seats.grant` calls this after limit checks pass, and `Seats.auto_seat` calls it when automatic seating still has room. The decision logic stays in those callers; this helper only performs the database update.

*Call graph*: called by 2 (auto_seat, grant); 2 external calls (execute, update).


##### `email_domain`  (lines 324–337)

```
def email_domain(email: str) -> str
```

**Purpose**: Extracts a clean domain from an email address and rejects malformed addresses. It returns an empty string for anything that is not exactly one simple `local@domain` address without whitespace.

**Data flow**: It receives an email string, trims and lowercases it, splits it around `@`, and checks that both sides are present, that there is no second `@`, and that there is no whitespace. If valid, it returns the domain; otherwise it returns an empty string.

**Call relations**: `create_member` uses this as the shared validation gate before inserting a member, and `workspace_domain` uses it to derive a workspace’s domain from its first member. This keeps all email-domain decisions based on the same shape rule.

*Call graph*: called by 2 (create_member, workspace_domain).


##### `workspace_domain`  (lines 340–357)

```
async def workspace_domain(connection: AsyncConnection, workspace_id: UUID) -> str | None
```

**Purpose**: Finds the workspace’s own email domain from its first member’s email address. This gives other parts of the system one consistent domain to compare against.

**Data flow**: It receives a workspace id, reads the earliest member email for that workspace, and returns no value if there is no member yet. If there is an email, it passes it through `email_domain` and returns the domain, or no value if the email somehow does not yield one.

**Call relations**: Join flows, operator checks, and portal displays can call this when they need the workspace domain. It delegates email parsing to `email_domain` so it does not invent a separate rule.

*Call graph*: calls 1 internal fn (email_domain); 2 external calls (execute, select).


##### `member_is_admin`  (lines 360–370)

```
async def member_is_admin(connection: AsyncConnection, workspace_id: UUID, member_id: UUID) -> bool
```

**Purpose**: Checks whether a particular member is an admin of a particular workspace. It is a small read helper for permission decisions.

**Data flow**: It receives a workspace id and member id, reads the member’s `is_admin` flag if the member belongs to that workspace, and returns true or false. Missing rows become false.

**Call relations**: Other code can call this before allowing admin-only actions. It does not call local helpers because it is a direct database lookup.

*Call graph*: 2 external calls (execute, select).


##### `create_member`  (lines 373–429)

```
async def create_member(connection: AsyncConnection, workspace_id: UUID, email: str, *, is_admin: bool=False) -> UUID
```

**Purpose**: Creates a workspace member through the one approved path, then applies the automatic seating rule. This ensures all ways of adding people obey the same email and seat behavior.

**Data flow**: It receives a workspace id, email, and optional admin flag. It validates the email shape, locks the workspace row, tries to insert a member with a new id, and if the insert succeeds calls `Seats.auto_seat`; if another concurrent caller already created the same member, it reads and returns the existing member id.

**Call relations**: Onboarding, teammate joins, hosted flows, and future member-creation surfaces should use this function instead of inserting directly. It calls `email_domain` for validation and hands the new member to `Seats.auto_seat` so creation and seating stay tied together.

*Call graph*: calls 1 internal fn (email_domain); 4 external calls (__init__, execute, select, uuid4).


##### `admin_conversation`  (lines 432–463)

```
async def admin_conversation(connection: AsyncConnection, workspace_id: UUID) -> tuple[UUID, UUID] | None
```

**Purpose**: Finds where the system can send a workspace-level request to an admin. It chooses the most recently active private conversation with a seated admin on the main agent.

**Data flow**: It receives a workspace id and searches conversations joined to members and agents. If it finds a matching seated admin conversation, it returns the conversation id and agent id; if not, it returns no value.

**Call relations**: Seat-refusal or workspace-management flows can use this when they need to ask an admin for action, such as granting a seat. It reads the current seated-admin state but does not change anything.

*Call graph*: 2 external calls (execute, select).


##### `member_workspaces`  (lines 466–474)

```
def member_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a candidate source for jobs that should run for every workspace with at least one member. It lets extensions ask for those workspaces without reaching directly into ownership internals.

**Data flow**: It defines a small query that selects distinct workspace ids from the member table, then wraps that query with `owner_candidates` and returns the result.

**Call relations**: A seat-reporting or billing-related job can declare this as its workspace candidate list. Inside it, `member_workspaces.with_a_member` supplies the actual query shape.

*Call graph*: 1 external calls (owner_candidates).


##### `member_workspaces.with_a_member`  (lines 471–472)

```
def with_a_member() -> sa.Select[tuple[UUID]]
```

**Purpose**: Defines the database query for workspaces that have members. It is intentionally broad: any workspace with at least one member is a candidate.

**Data flow**: It takes no input, builds a select statement for distinct workspace ids from the member table, and returns that statement to be run elsewhere.

**Call relations**: This nested helper is handed to `owner_candidates` by `member_workspaces`. It does not run the query itself; it describes what workspaces the candidate system should consider.

*Call graph*: 1 external calls (select).

## 📊 State Registers Touched

- `reg-workspace-tenant-state` — The saved customer workspace boundary, including its owners, admins, limits, main agent, and tenant separation rules.
- `reg-identity-auth-state` — The current proof of who is calling, such as member identity, cookies, bearer tokens, operator sessions, and signed access tokens.
- `reg-agent-profile` — The saved assistant setup for each workspace, including model choice, audience, internet access, skills, and control settings.
- `reg-conversation-state` — The durable record of each conversation, including its workspace, surface, audience, agent, sandbox link, and object identity.
- `reg-inbound-message-state` — The durable inbox of incoming messages and surface events waiting to be admitted into a conversation turn.
- `reg-turn-run-state` — The durable job ticket for each agent turn, including admission source, queue status, claim owner, parent turn, and final result.
- `reg-access-grants` — The saved approvals that say which workspace, member, agent, account, source, or conversation is allowed to use a protected resource.
- `reg-connector-account-state` — The connected-app state for OAuth, hosted connector accounts, GitHub installations, Slack setup, and provider action access.
- `reg-hosted-site-state` — The durable records for generated hosted sites, including names, ports, owners, conversations, sharing, and viewing permissions.
- `reg-workspace-object-state` — The shared shelf of workspace objects, their types, names, owners, permissions, listings, and object-specific actions.
- `reg-surface-delivery-state` — The surface installation keys, outbound delivery/writeback queue, and acknowledgement state used to send completed replies back to external surfaces.
- `reg-security-audit-log` — The durable audit records for sensitive access and administrative/security-relevant actions, distinct from operational traces.
- `reg-member-seat-state` — The durable workspace membership and seat-assignment state used to decide who belongs, who is an admin, and whether a member may admit or run work.
- `reg-user-question-state` — The pending human-question/answer state created when an agent asks the user for information and later resumed when the surface delivers a reply.

# Identity, workspace membership, and object authorization  `stage-6`

This stage is the system’s identity checkpoint. It runs behind the scenes whenever a request needs to know who is acting, which workspace they belong to, and what they are allowed to see or change. The workspace context code keeps all work tied to the right workspace, credentials, database, and billing. Bearer tokens are signed login passes that prove a member’s email and workspace without a server session.

Membership and seats are managed by the member, seat, and workspace inspection objects. They show who belongs, who is an admin, who has an active seat, and prevent unsafe changes like removing the last seated admin. Agent scope and object scope record which agent is currently acting, so audits and permissions stay accurate.

The object system is the shared doorway for workspace items. It checks names, shapes, and permissions before calling each object type. Agents, conversations, credentials, extensions, and the workspace itself are exposed mostly as controlled or read-only objects. Web audience rules decide which members can see which agents. Audience and subject labels describe who content is for, while scheduled-task visibility protects private task text.

## Files in this stage

### Workspace membership and seats
These files establish who belongs to a workspace, how seat access is managed, and how the current workspace and member identity are trusted.

### `core/src/ufo/members.py`

`domain_logic` · `request handling`

A workspace needs a safe roster: people should be able to see the right membership information, admins should be able to grant or remove access, and nobody should accidentally expose the whole team list in the wrong place. This file is that rulebook. It treats a member as an object with two editable facts: whether they are an admin, and whether they are seated. A “seat” means the person is allowed through admission; unseating someone is how the workspace removes their access without deleting their membership record.

The main idea is visibility control. When someone talks to the main agent in an internal conversation, they can see the workspace roster. When they are using a child agent or a channel shared with another organization, the view narrows to only their own member row. Think of it like a company directory that is visible inside the office, but only shows your own badge information when you are standing in a public lobby.

The file also enforces admin safety. Only a speaking workspace admin, using the main agent, may change another member’s admin status or seat. It refuses to delete members through the object system. It also protects the workspace from losing its last admin or last seated admin. Finally, the add_member tool creates a new member by email, even at an outside domain, but only after checking that the caller is an admin and that the email is not already present.

#### Function details

##### `MemberObjects.list`  (lines 65–69)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows a page of member rows that the current speaker is allowed to see. It is used when the object system needs to list member objects in a conversation.

**Data flow**: It receives the tool context, which includes who is speaking and what audience they are speaking in, plus paging and filtering information. It asks for the visible database rows, turns each row into a short display row, and wraps those rows into an object page. The result is a roster page, possibly the full workspace roster or possibly only the speaker’s own row.

**Call relations**: This is the public list path for member objects. It relies on MemberObjects._visible_rows to apply the privacy rule, then uses _row to make each database row readable before passing the collection to the shared object_page helper.

*Call graph*: calls 2 internal fn (_visible_rows, _row); 1 external calls (object_page).


##### `MemberObjects.member_page`  (lines 71–87)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows the member roster for portal-style reads, where a signed-in member is looking outside a normal chat turn. It applies the same main-agent visibility rule used during conversation.

**Data flow**: It receives the signed-in member’s id, whether they are an admin, and page query information. It fetches the rows that this member may see for the current agent, converts them into simple row summaries, and returns a paged result. The admin flag is accepted as part of the portal interface, but the visibility decision here is based on whether the current agent is the main one.

**Call relations**: This is the portal counterpart to MemberObjects.list. It calls MemberObjects._member_rows to reproduce the same roster rule outside a chat turn, then formats rows with _row and hands them to object_page.

*Call graph*: calls 2 internal fn (_member_rows, _row); 1 external calls (object_page).


##### `MemberObjects.get`  (lines 89–91)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[MemberSpec] | None
```

**Purpose**: Fetches the detailed editable information for one member, if the current speaker is allowed to see that member. It is used when the object system needs the detail view of a specific member object.

**Data flow**: It receives the tool context and a member object name, which is expected to be the member id as text. It looks for that member among the rows visible to the speaker. If it finds one, it turns the database row into a detail object containing admin and seated flags; if not, it returns nothing.

**Call relations**: This is the detail path that pairs with MemberObjects.list. It asks MemberObjects._visible_row to enforce the same visibility rule, then uses _detail to build the object detail returned to the object system.

*Call graph*: calls 2 internal fn (_visible_row, _detail).


##### `MemberObjects.member_detail`  (lines 93–108)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[MemberSpec] | None
```

**Purpose**: Fetches one member’s row and detail for portal-style reads. It lets the portal show the same kind of information that chat-based list and get would expose.

**Data flow**: It receives a member object name, the signed-in member’s id, and portal context values. It gets the rows visible to that signed-in member, searches for the requested id, and either returns nothing or returns both a display row and detailed admin/seated information. The result is a complete member object for display.

**Call relations**: This is the portal counterpart to MemberObjects.get. It calls MemberObjects._member_rows to get the allowed rows, then uses _row and _detail to assemble the MemberObject returned to the portal layer.

*Call graph*: calls 3 internal fn (_member_rows, _detail, _row); 1 external calls (__init__).


##### `MemberObjects.status`  (lines 110–125)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns a small status snapshot for one visible member. This gives other parts of the system a lightweight way to ask whether a member exists, what their email is, and whether they are seated.

**Data flow**: It receives the current tool context, a member name, and an expected generation value. It finds the requested member only within the speaker’s visible rows. If found, it returns a dictionary with the member’s email and seated state; if not, it returns nothing. It does not change the database.

**Call relations**: This uses MemberObjects._visible_row, so it follows the same privacy rule as get. It does not call the formatting helpers because it returns a compact machine-readable status rather than a full object row or detail.

*Call graph*: calls 1 internal fn (_visible_row).


##### `MemberObjects.apply`  (lines 127–206)

```
async def apply(self, ctx: ToolContext, name: str, spec: MemberSpec, old: MemberSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Changes an existing member’s admin role and/or seat, but only when a workspace admin is doing it from the main agent. It is the controlled edit path for membership access and admin power.

**Data flow**: It receives the current context, the member id as text, the desired new MemberSpec, and the old spec supplied by the object system. It first rejects anyone who is not a speaking member using the main agent, rejects attempts to create a member through this path, and checks that the name is a valid id. Inside a workspace transaction, it locks the workspace row, confirms the speaker is really an admin, loads the target member, grants or revokes the seat if needed, and updates the admin flag if needed. It may raise errors instead of changing anything if the target does not exist or if the change would leave the workspace without an admin or without a seated admin.

**Call relations**: This is the write path behind applying changes to member objects. It calls ToolContext.agent_is_main to check the setting, uses workspace_tx and SQL queries for a safe database update, asks member_is_admin to verify authority, and uses Seats when the seated flag changes. It deliberately hands creation off elsewhere by rejecting old=None and pointing users toward add_member.

*Call graph*: calls 1 internal fn (agent_is_main); 10 external calls (__init__, __init__, __init__, __init__, select, update, workspace_tx, member_is_admin, ws_current, UUID).


##### `MemberObjects.delete`  (lines 208–215)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses deletion of member objects. Membership records are kept, and access is removed by unseating someone instead of deleting them.

**Data flow**: It receives the context, member name, and expected generation value, but does not inspect or change the member. It immediately raises a not-supported error explaining that members cannot be deleted through objects.

**Call relations**: This is the delete hook required by the object store interface. Instead of calling lower-level database helpers, it stops the flow at the boundary and tells callers to use the supported membership controls.

*Call graph*: 1 external calls (__init__).


##### `MemberObjects._visible_rows`  (lines 217–222)

```
async def _visible_rows(self, ctx: ToolContext) -> tuple[sa.Row, ...]
```

**Purpose**: Decides which member rows the current chat speaker is allowed to see. It is the central privacy rule for conversation-based member listing and lookup.

**Data flow**: It reads the tool context to find the speaker member id, the audience, and whether the current agent is the main agent. If there is no speaker, it returns no rows. If the conversation is in an externally shared audience, it fetches only the speaker’s row. Otherwise it fetches either the whole roster for the main agent or only the speaker’s row for a non-main agent.

**Call relations**: MemberObjects.list uses this to build visible pages, and MemberObjects._visible_row uses it to find one visible member. It delegates the actual database read to MemberObjects._roster after deciding whether the request is allowed to see the whole roster.

*Call graph*: calls 2 internal fn (_roster, agent_is_main); called by 2 (_visible_row, list).


##### `MemberObjects._visible_row`  (lines 224–228)

```
async def _visible_row(self, ctx: ToolContext, name: str) -> sa.Row | None
```

**Purpose**: Finds one member row among the rows visible to the current speaker. It is a small helper that keeps single-member lookups under the same privacy rules as listing.

**Data flow**: It receives the context and the requested member name. It first asks for all rows visible in that context, then searches for a row whose id string matches the requested name. It returns that row if present, or nothing if the member is hidden or does not exist.

**Call relations**: MemberObjects.get and MemberObjects.status call this before returning member information. Because it depends on MemberObjects._visible_rows, those single-item reads cannot bypass the list visibility rule.

*Call graph*: calls 1 internal fn (_visible_rows); called by 2 (get, status).


##### `MemberObjects._member_rows`  (lines 230–242)

```
async def _member_rows(self, member_id: UUID) -> tuple[sa.Row, ...]
```

**Purpose**: Calculates which rows a signed-in member may see when the read is happening through the portal rather than inside a tool turn. It mirrors the chat visibility rule by checking whether the current agent is the main agent.

**Data flow**: It receives a member id. It opens a workspace transaction, looks up whether the current agent is marked as the main agent, and then asks for either the whole roster or only that member’s row. It returns the matching database rows.

**Call relations**: MemberObjects.member_page and MemberObjects.member_detail use this for portal reads. It checks the current agent through agent_current and ws_current, then delegates the roster query itself to MemberObjects._roster.

*Call graph*: calls 1 internal fn (_roster); called by 2 (member_detail, member_page); 4 external calls (select, agent_current, workspace_tx, ws_current).


##### `MemberObjects._roster`  (lines 244–264)

```
async def _roster(self, member_id: UUID, *, whole: bool) -> tuple[sa.Row, ...]
```

**Purpose**: Reads member rows from the database, either the full workspace roster or one member’s own row. This is the shared database query behind both chat and portal visibility.

**Data flow**: It receives a member id and a whole flag. It builds a database query for members in the current workspace, ordered by email, including each member’s id, email, admin flag, seat state, and timestamps. If whole is false, it adds a filter for just the given member id. It runs the query in a workspace transaction and returns the rows.

**Call relations**: MemberObjects._visible_rows and MemberObjects._member_rows both call this after they decide whether the reader may see the full roster. This keeps the actual database read in one place while allowing different entry paths to share the same result shape.

*Call graph*: called by 2 (_member_rows, _visible_rows); 3 external calls (select, workspace_tx, ws_current).


##### `_row`  (lines 267–275)

```
def _row(row: sa.Row) -> ObjectRow
```

**Purpose**: Turns a raw member database row into a short object-list row that people can read. It creates the one-line summary used in member lists.

**Data flow**: It receives a database row containing a member id, email, admin flag, and seat state. It makes the object name from the id and builds a summary such as email, workspace admin or member, and seated or unseated. It returns an ObjectRow ready for list displays.

**Call relations**: MemberObjects.list, MemberObjects.member_page, and MemberObjects.member_detail call this whenever they need the compact display version of a member. It is the final formatting step after visibility has already been decided.

*Call graph*: called by 3 (list, member_detail, member_page); 1 external calls (__init__).


##### `_detail`  (lines 278–283)

```
def _detail(row: sa.Row) -> ObjectDetail[MemberSpec]
```

**Purpose**: Turns a raw member database row into the detailed object information used for editing and inspection. It packages the admin and seated settings as a MemberSpec.

**Data flow**: It receives a database row with role, seat, and timestamp fields. It creates a MemberSpec from the admin flag and whether seated_at is present, then includes created and updated timestamps in an ObjectDetail. The result is the detailed object view returned by get-style calls.

**Call relations**: MemberObjects.get and MemberObjects.member_detail call this after they have found an allowed row. It provides the editable detail that pairs with _row’s short list summary.

*Call graph*: called by 2 (get, member_detail); 2 external calls (__init__, __init__).


##### `AddMember.add`  (lines 309–332)

```
async def add(self, ctx: ToolContext, args: AddMemberInput) -> ToolResult
```

**Purpose**: Adds a new person to the workspace by email before they have contacted the agent. It is meant for admins staffing the workspace, including adding contractors or advisors whose email domain may not match the workspace.

**Data flow**: It receives the current tool context and an input object containing email, desired admin flag, and a user-facing description. It checks that the caller is a signed-in member using the main agent, rejects externally shared channels, normalizes and validates the email, then opens a workspace transaction. Inside the transaction it locks the workspace, confirms the speaker is an admin, checks that the email is not already a member, and creates the member. It returns a tool result saying the person is now a workspace member or admin and can speak to the agent.

**Call relations**: This is the handler registered for the add_member tool. It uses ToolContext.agent_is_main and member_is_admin for authority checks, calls AddMember._absent to avoid duplicates, and then hands actual creation to create_member. Its returned TextContent becomes the user-visible confirmation.

*Call graph*: calls 2 internal fn (_absent, agent_is_main); 9 external calls (__init__, __init__, __init__, select, workspace_tx, create_member, email_domain, member_is_admin, ws_current).


##### `AddMember._absent`  (lines 334–347)

```
async def _absent(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Checks that an email is not already a member of the current workspace. It prevents the add_member tool from creating duplicate membership records.

**Data flow**: It receives an open database connection and a normalized email address. It searches the current workspace for a member with that email, ignoring letter case. If none is found, it returns normally; if one exists, it raises an error telling the caller to change that existing member’s role or seat instead.

**Call relations**: AddMember.add calls this inside the same transaction before creating a member. It is the duplicate-check gate between admin authorization and the final create_member call.

*Call graph*: called by 1 (add); 3 external calls (execute, select, ws_current).


### `core/src/ufo/workspace_kind.py`

`domain_logic` · `request handling`

This file gives the system a simple way to answer the question: “What is this workspace, and who can use it?” A workspace is treated as one permanent object, named by its workspace ID. Unlike many objects, it has no editable settings here. Its reported values are calculated from member records: the total number of members, the number with seats, and the roster showing each member’s email, seat status, and admin status.

Think of it like a building lobby directory. The building exists already, and this file does not remodel it or add tenants. It only shows who is listed and who currently has a key.

The main class, `WorkspaceObjects`, provides the object-store actions for this kind. Listing returns the single workspace row, unless the request comes from a foreign audience, in which case it shows nothing. Reading details returns timestamps and an empty spec, because there is nothing the caller can fill in or edit. Status returns the useful live information, including the roster, but it limits what is shown depending on who is asking: the main agent can show the whole roster to an internal member, while a child agent only shows the speaker’s own member row.

Any attempt to apply changes or delete the workspace is rejected with a clear message. The actual place to grant or remove access is the separate member object.

#### Function details

##### `WorkspaceObjects.list`  (lines 73–86)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows the workspace in a list view. Since there is only one workspace object, the list normally contains one row with member and seat counts; outside audiences see an empty list.

**Data flow**: It receives the request context and list query. It first checks who the request is for; if the audience is foreign, it returns an empty page. Otherwise it reads the current workspace ID and asks `_shape` for the latest counts, then turns those values into one list row and wraps it in a paged result.

**Call relations**: This is the list-facing entry for the workspace object kind. When it needs real workspace facts, it delegates to `_shape`, which does the database reading. It then hands the row to the shared object paging helper so the result looks like other object lists in the system.

*Call graph*: calls 1 internal fn (_shape); 3 external calls (__init__, object_page, ws_current).


##### `WorkspaceObjects.get`  (lines 88–98)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[WorkspaceSpec] | None
```

**Purpose**: Fetches the detail view for the one current workspace object. It confirms the requested name is the current workspace ID and returns timestamps plus an empty spec, because workspace fields are not edited here.

**Data flow**: It receives the request context and the object name. If the audience is foreign, or if the name does not match the current workspace ID, it returns nothing. If the name matches, it reads the workspace shape and returns an object detail with creation and update times and an empty `WorkspaceSpec`.

**Call relations**: This is used when a caller asks for one specific workspace object. Like the list operation, it relies on `_shape` for the stored facts, then packages those facts in the common object-detail format used by the object system.

*Call graph*: calls 1 internal fn (_shape); 3 external calls (__init__, __init__, ws_current).


##### `WorkspaceObjects.status`  (lines 100–121)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns the live status of the workspace: member count, seated count, and roster information. It also enforces the privacy rule for the roster, so callers only see the amount of member detail they are allowed to see.

**Data flow**: It receives the context, workspace name, and an optional expected generation value. It rejects foreign audiences and names that are not the current workspace ID by returning nothing. For a valid internal request, it reads the workspace shape, checks whether the speaker is talking to the main agent, and builds a status dictionary. That dictionary always includes counts, and includes either the whole roster or just the speaker’s own row.

**Call relations**: This function is the main source of the workspace’s meaningful information. It calls `_shape` to get the current roster snapshot, and asks the tool context whether the agent is the main one so it can decide how much of the roster to reveal.

*Call graph*: calls 2 internal fn (agent_is_main, _shape); 1 external calls (ws_current).


##### `WorkspaceObjects.apply`  (lines 123–132)

```
async def apply(self, ctx: ToolContext, name: str, spec: WorkspaceSpec, old: WorkspaceSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to create or update the workspace object. This protects the rule that seats are changed through member objects, not by editing the workspace itself.

**Data flow**: It receives the proposed workspace name, new spec, old spec, context, and optional generation check. It does not inspect or save those values. Instead it immediately raises a clear “verb not supported” error explaining that seating and unseating belong to the member object kind.

**Call relations**: The object system calls this when someone tries to apply a change to a workspace. Rather than passing work onward, it stops the flow and tells the caller which object kind should be used for seat changes.

*Call graph*: 1 external calls (__init__).


##### `WorkspaceObjects.delete`  (lines 134–141)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to delete the workspace. A workspace is treated as permanent after it is created, so deletion is not available through this object kind.

**Data flow**: It receives the request context, workspace name, and optional generation check. It does not remove anything or look anything up. It immediately raises a “verb not supported” error saying the workspace is permanent.

**Call relations**: The object system calls this when someone asks to delete the workspace object. This function acts as a guardrail and ends that request with a clear refusal.

*Call graph*: 1 external calls (__init__).


##### `WorkspaceObjects._shape`  (lines 143–161)

```
async def _shape(self) -> WorkspaceShape
```

**Purpose**: Collects the real facts that the public read methods need: workspace timestamps, total members, seated members, and the roster. It is the shared helper that turns database state into one small `WorkspaceShape` snapshot.

**Data flow**: It reads the current workspace ID, opens a workspace database transaction, fetches the workspace row’s creation and update times, and asks the seating helper for a member-seat snapshot. It then combines those pieces into a `WorkspaceShape` containing counts, roster entries, and timestamps.

**Call relations**: This helper sits behind `list`, `get`, and `status`. Those user-facing methods decide what the caller is allowed to see, while `_shape` does the common work of gathering the latest workspace and seating information from storage.

*Call graph*: called by 3 (get, list, status); 5 external calls (__init__, __init__, select, workspace_tx, ws_current).


### `core/src/ufo/bearer.py`

`domain_logic` · `request handling`

This file is the shared rulebook for UFO's bearer token, which is a small signed string proving “this user belongs to this workspace until this time.” A bearer token is like a wristband at an event: anyone who knows how to inspect the wristband can let the wearer through, but only the event staff with the secret stamp can make a real one.

The token contains three claims: the workspace id, the member email, and an expiry time. These claims are turned into compact JSON, encoded with URL-safe Base64, and signed with HMAC-SHA256. HMAC is a way to make a tamper-evident signature using a shared secret. If someone changes even one character in the token body, the signature check fails.

The file also defines the environment variable that holds the signing secret, plus the fixed browser cookie name and login path used by the session flow. Nothing here keeps a database record of sessions. Instead, verification re-computes the expected signature from the token body and the local secret, checks it in constant time to avoid timing leaks, decodes the payload, and rejects it if it is malformed or expired.

Two higher-level checks sit on top: one confirms that a token belongs to a specific pinned workspace, and the other extracts the workspace id for shared services that serve many workspaces.

#### Function details

##### `mint_token`  (lines 33–50)

```
def mint_token(secret: str, workspace_id: str, email: str, ttl: timedelta, now: datetime | None=None) -> str
```

**Purpose**: Creates a new signed bearer token for a workspace and member email. It is used by token issuers so every token has the same shape and can be verified by the matching code in this file.

**Data flow**: It receives a secret, workspace id, email address, time-to-live, and optionally a fixed current time. It trims and lowercases the email, builds a JSON payload with the workspace, email, and expiry timestamp, encodes that payload into a URL-safe text body, signs the body with HMAC-SHA256 using the secret, and returns one string made from the body plus the signature. If the secret is empty, it stops with an error instead of making an unsafe token.

**Call relations**: This is the issuing half of the token system. Other parts of the product that mint login tokens call this function so they all produce tokens that the verification half can understand later.

*Call graph*: 4 external calls (urlsafe_b64encode, now, new, dumps).


##### `verified_claims`  (lines 53–76)

```
def verified_claims(token: str, now: int | None=None) -> tuple[str, str] | None
```

**Purpose**: Checks whether a bearer token is genuine and still valid, then returns the workspace and email it proves. If the token is missing, forged, malformed, or expired, it returns nothing.

**Data flow**: It receives a token string and optionally a current timestamp for testing or controlled checks. It reads the signing secret from the environment, splits the token into body and signature, recomputes the expected signature, compares it safely, decodes the body back into JSON, checks that the workspace, email, and expiry fields have the right types, and confirms the expiry time is still in the future. The output is a pair of strings, workspace and email, or None if any check fails.

**Call relations**: This is the common verification core. verify_token calls it when a service already knows which workspace it should accept, and workspace_claim calls it when a shared service needs to discover the workspace from the signed token itself. It relies on _secret to get the signing key and _b64url_decode to turn the compact token body back into bytes.

*Call graph*: calls 2 internal fn (_b64url_decode, _secret); called by 2 (verify_token, workspace_claim); 4 external calls (now, compare_digest, new, loads).


##### `verify_token`  (lines 79–90)

```
def verify_token(token: str, workspace_id: UUID, now: int | None=None) -> str | None
```

**Purpose**: Confirms that a token is valid for one specific workspace and returns the member email. This prevents a token from one workspace being accepted in another workspace.

**Data flow**: It receives a token, an expected workspace UUID, and optionally a current timestamp. It first asks verified_claims to prove the token is signed and unexpired. If that succeeds, it compares the token's workspace claim with the expected workspace id. It returns the lowercased email when both checks pass, otherwise None.

**Call relations**: This function sits one level above the general token checker. It is used in flows where the running service or deployment is already pinned to one workspace, so after verified_claims proves the token, this function adds the tenant boundary check.

*Call graph*: calls 1 internal fn (verified_claims).


##### `workspace_claim`  (lines 93–104)

```
def workspace_claim(token: str, now: int | None=None) -> UUID | None
```

**Purpose**: Extracts the workspace id from a valid token for systems that serve many workspaces. It only trusts the workspace after the token signature and expiry have been checked.

**Data flow**: It receives a token and optionally a current timestamp. It asks verified_claims to validate the token and return its raw workspace string. Then it tries to parse that string as a UUID, which is the standard format for workspace ids. It returns the UUID if everything is valid, or None if verification fails or the workspace value is not a valid UUID.

**Call relations**: This is the shared-fleet path. Instead of comparing the token against one known workspace, it uses verified_claims to safely read the workspace from the token, then converts it into the UUID form other code can use for request scoping.

*Call graph*: calls 1 internal fn (verified_claims); 1 external calls (UUID).


##### `_secret`  (lines 107–111)

```
def _secret() -> str
```

**Purpose**: Reads the token signing secret from the process environment. This keeps verification tied to the secret configured for the current deployment.

**Data flow**: It looks up the UFO_TOKEN_SECRET environment variable. If the value exists, it returns that string. If it is missing or empty, it raises an error because verifying tokens without the shared secret would be unsafe and meaningless.

**Call relations**: verified_claims calls this before checking any token signature. By centralizing the lookup here, the rest of the verification code does not need to know where the secret is stored.

*Call graph*: called by 1 (verified_claims).


##### `_b64url_decode`  (lines 114–115)

```
def _b64url_decode(value: str) -> bytes
```

**Purpose**: Decodes the compact Base64 form used for the token body. It also restores any missing padding characters that were stripped to keep the token shorter.

**Data flow**: It receives the encoded body text from a token. It calculates how many '=' padding characters are needed, appends them, decodes the URL-safe Base64 text, and returns the original bytes. If the text is not valid Base64, the decoder raises an error that the caller can treat as a bad token.

**Call relations**: verified_claims calls this after the signature has passed, so it can turn the trusted token body back into JSON bytes. This helper keeps the low-level encoding detail out of the main verification flow.

*Call graph*: called by 1 (verified_claims); 1 external calls (urlsafe_b64decode).


### `core/src/ufo/seats.py`

`domain_logic` · `cross-cutting: member creation, admission checks, running-turn checks, scheduled/resume checks, and admin seat changes`

A “seat” here is the project’s access switch for a workspace member. If a member is seated, the agent may answer them. If an admin revokes the seat, the person still exists as a member, but the agent refuses their turns until the seat is restored. Think of it like a building badge: losing the badge does not erase the employee record, but it stops door access.

This file keeps that rule in one place so every path agrees: new messages, scheduled jobs, resumed work, running turns, and extension tools all ask the same questions. It can check one member, check a whole group of members, or produce a snapshot for reporting. It can grant a seat back, revoke a seat, and create a member safely.

The important safety rule is that the last seated admin cannot be unseated. Seat control happens through chat, so if the last admin lost access, nobody could restore anyone. The code locks the workspace row before sensitive writes so two overlapping changes cannot accidentally break that rule.

It also normalizes email addresses. Member creation only accepts a simple local@domain email, lowercases it, and uses database uniqueness so two callers racing to create the same person end up with one member row.

#### Function details

##### `gate_member`  (lines 38–52)

```
def gate_member(speaker_member_id: UUID | None, admission_source: TurnAdmissionSource, on_behalf_of_member_id: UUID | None) -> UUID | None
```

**Purpose**: Chooses which member a turn should be checked against for seating. Usually that is the speaker, but for a scheduled turn it may be the member the job is acting for.

**Data flow**: It receives a possible speaker member id, the source of the turn, and a possible “on behalf of” member id. If there is a speaker, it returns that speaker. If the turn came from scheduled admission, it returns the member the scheduled job represents. Otherwise it returns nothing, meaning there is no member seat to check.

**Call relations**: This is the shared rule used anywhere the system needs to decide whose seat controls a turn. By centralizing that choice, scheduled jobs, resumed work, dispatch checks, and per-round checks do not drift into different meanings of “the responsible member.”


##### `SeatSnapshot.seated`  (lines 76–77)

```
def seated(self) -> int
```

**Purpose**: Counts how many members in a snapshot currently have seats. This is useful for showing or reporting the current access state of a workspace.

**Data flow**: It reads the snapshot’s list of member entries. It counts only entries marked as seated. It returns that count as a number and does not change anything.

**Call relations**: This property is used after a snapshot has already been built by Seats.snapshot. It turns the detailed member list into a simple summary number.


##### `Seats.admits`  (lines 88–102)

```
async def admits(self, connection: AsyncConnection, member_id: UUID) -> bool
```

**Purpose**: Answers the basic admission question: may the agent answer this specific member right now? The answer is yes only if the member belongs to this workspace and has a current seat.

**Data flow**: It receives a database connection and a member id. It looks up that member row within this Seats object’s workspace and checks whether the seated_at field is filled in. It returns true for a seated workspace member and false for an unknown, outside, or unseated member.

**Call relations**: Admission and running-turn enforcement call on this kind of check whenever the system must decide whether to answer someone. It hands the database query to SQLAlchemy and the async connection, then turns the row it gets back into a simple yes or no.

*Call graph*: 2 external calls (execute, select).


##### `Seats.all_seated`  (lines 104–124)

```
async def all_seated(self, connection: AsyncConnection, member_ids: Collection[UUID]) -> bool
```

**Purpose**: Checks whether every member in a group still has a seat. This matters for turns that involve several people, because one revoked speaker should be enough to stop or park the work.

**Data flow**: It receives a database connection and a collection of member ids. If the collection is empty, it returns true. Otherwise it asks the database how many of those ids are seated members of this workspace, then compares that count with the number requested. It returns true only when every requested member is present and seated.

**Call relations**: The per-round check, parked-work check, and dispatch sweep can use this to test a whole set in one database trip. It relies on SQLAlchemy and the async connection to perform the count, then gives callers a single all-clear or not.

*Call graph*: 2 external calls (execute, select).


##### `Seats.snapshot`  (lines 126–149)

```
async def snapshot(self, connection: AsyncConnection) -> SeatSnapshot
```

**Purpose**: Builds a complete, ordered picture of the workspace’s members and their seat/admin status. This is the read side used when the system needs to show who is currently allowed to use the agent.

**Data flow**: It receives a database connection. It reads all member rows for this workspace, ordered by creation time and id. For each row it creates a SeatEntry with the member id, email, whether they are seated, and whether they are an admin. It returns a SeatSnapshot containing those entries.

**Call relations**: This is the reporting companion to the yes/no gate checks. It calls the database through SQLAlchemy, then packages raw rows into small data objects so other code can present or inspect the seating state without knowing table details.

*Call graph*: 4 external calls (__init__, __init__, execute, select).


##### `Seats.grant`  (lines 151–161)

```
async def grant(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Restores a member’s seat by email. Admin tools use this when a previously unseated member should be allowed to talk to the agent again.

**Data flow**: It receives a database connection and an email address. It first uses Seats._member_by_email to find the member in this workspace. If the member is already seated, it does nothing. If not, it updates the member row with a fresh seated_at time and updated_at time.

**Call relations**: Seat-granting flows call this instead of writing the database directly, so the lookup and idempotent behavior stay consistent. It delegates member lookup to Seats._member_by_email and sends the update through the async database connection.

*Call graph*: calls 1 internal fn (_member_by_email); 2 external calls (execute, update).


##### `Seats.revoke`  (lines 163–186)

```
async def revoke(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Removes a member’s seat by email, which stops the agent from answering that person. It refuses to revoke the last seated admin, because that would leave the workspace with nobody able to restore seats through chat.

**Data flow**: It receives a database connection and an email address. It locks the workspace row so overlapping revokes are serialized, then finds the member by email. If the member is already unseated, it does nothing. If the member is an admin and is the only seated admin, it raises LastAdminSeatRevocation. Otherwise it clears the member’s seated_at field and updates the timestamp.

**Call relations**: Admin seat-removal tools use this path so they all obey the same last-admin protection. It calls Seats._member_by_email to find the target, Seats._seated_admin_count when the target is an admin, and then writes the final update through the database connection.

*Call graph*: calls 2 internal fn (_member_by_email, _seated_admin_count); 4 external calls (__init__, execute, select, update).


##### `Seats._member_by_email`  (lines 188–205)

```
async def _member_by_email(self, connection: AsyncConnection, email: str) -> tuple[UUID, datetime | None, bool]
```

**Purpose**: Finds one member in this workspace by email and returns the facts needed for seat changes. It raises a clear error when the email does not name a workspace member.

**Data flow**: It receives a database connection and an email address. It trims and lowercases the email for comparison, then reads the matching member id, seated_at value, and admin flag from this workspace. It returns those three values, or raises UnknownMember if no row exists.

**Call relations**: Seats.grant and Seats.revoke both call this before changing a seat. That keeps email lookup behavior identical for restoring and removing access.

*Call graph*: called by 2 (grant, revoke); 3 external calls (__init__, execute, select).


##### `Seats._seated_admin_count`  (lines 207–216)

```
async def _seated_admin_count(self, connection: AsyncConnection) -> int
```

**Purpose**: Counts how many admins in this workspace currently have seats. It exists to protect against removing the final admin seat.

**Data flow**: It receives a database connection. It asks the database to count member rows in this workspace where the member is an admin and still has seated_at set. It returns that count as an integer.

**Call relations**: Seats.revoke calls this only when the target member is an admin. The result decides whether revocation may continue or must be blocked with LastAdminSeatRevocation.

*Call graph*: called by 1 (revoke); 2 external calls (execute, select).


##### `email_domain`  (lines 219–232)

```
def email_domain(email: str) -> str
```

**Purpose**: Extracts and validates the domain part of an email address. It deliberately returns an empty string for malformed values so invalid addresses cannot accidentally match a workspace domain or become member records.

**Data flow**: It receives an email string. It trims whitespace, lowercases it, checks that it has exactly one @ separator with text on both sides, and rejects any whitespace. It returns the domain part for a valid address, or an empty string for anything invalid.

**Call relations**: create_member calls this before accepting any new member email, and workspace_domain calls it when deriving a workspace’s domain from its first member. This makes domain handling consistent across creation and lookup paths.

*Call graph*: called by 2 (create_member, workspace_domain).


##### `workspace_domain`  (lines 235–252)

```
async def workspace_domain(connection: AsyncConnection, workspace_id: UUID) -> str | None
```

**Purpose**: Finds the email domain that represents a workspace. The project defines it as the domain of the first member created in that workspace.

**Data flow**: It receives a database connection and workspace id. It reads the earliest member email for that workspace. If there is no member, it returns nothing. If there is an email, it passes it through email_domain and returns the valid domain or nothing if somehow no valid domain is found.

**Call relations**: Workspace-joining and operator checks can use this shared derivation instead of each inventing their own. It gets the email from the database, then relies on email_domain for the exact parsing rule.

*Call graph*: calls 1 internal fn (email_domain); 2 external calls (execute, select).


##### `member_is_admin`  (lines 255–265)

```
async def member_is_admin(connection: AsyncConnection, workspace_id: UUID, member_id: UUID) -> bool
```

**Purpose**: Checks whether a particular member is an admin of a particular workspace. This is a small permission helper for code that needs an admin yes/no answer.

**Data flow**: It receives a database connection, workspace id, and member id. It reads the is_admin flag for that exact member within that exact workspace. It returns true if the stored flag is true, and false if the member is missing or not an admin.

**Call relations**: Permission-checking code can call this before allowing admin-only actions. It uses the database connection directly and returns a simple boolean rather than exposing raw rows.

*Call graph*: 2 external calls (execute, select).


##### `create_member`  (lines 268–330)

```
async def create_member(connection: AsyncConnection, workspace_id: UUID, email: str, *, is_admin: bool=False) -> UUID
```

**Purpose**: Creates a workspace member safely and consistently. New members are seated by default, and every creation path goes through this function so email validation, lowercasing, and race handling are the same everywhere.

**Data flow**: It receives a database connection, workspace id, email address, and an optional admin flag. It first validates the email with email_domain, lowercases it, and locks the workspace row to keep concurrent creations in a safe order. It then tries to insert a member with a new UUID. If another caller already created the same workspace/email row, the insert does nothing and the function reads back the existing member id. It returns the created or existing member id.

**Call relations**: Onboarding, verified teammate joins, and other member-creation surfaces use this as the single write path. It calls email_domain for validation, uuid4 for a new id when needed, and the database insert/select operations through the async connection.

*Call graph*: calls 1 internal fn (email_domain); 3 external calls (execute, select, uuid4).


##### `member_workspaces`  (lines 333–341)

```
def member_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a candidate source for jobs that should run for workspaces with at least one member. It keeps the knowledge of the member table inside core code instead of making extensions query it themselves.

**Data flow**: It takes no input. It defines a small query that selects distinct workspace ids from the member table, then wraps that query with owner_candidates. It returns a WorkspaceCandidates object that other job code can use.

**Call relations**: Extensions that need to report on seats or members can ask for these candidates rather than reaching into ownership tables directly. This function hands its nested query builder to owner_candidates, which turns it into the broader candidate mechanism.

*Call graph*: 1 external calls (owner_candidates).


##### `member_workspaces.with_a_member`  (lines 338–339)

```
def with_a_member() -> sa.Select[tuple[UUID]]
```

**Purpose**: Creates the actual database query for workspaces that have members. It is the small inner query used by member_workspaces.

**Data flow**: It takes no direct input. It builds a SELECT query over the member table that returns each workspace id only once. The result is a query object, not the rows themselves.

**Call relations**: member_workspaces gives this query-building function to owner_candidates. Later, when the candidate mechanism needs the workspace list, this query describes where to get it.

*Call graph*: 1 external calls (select).


### `core/src/ufo/workspace.py`

`orchestration` · `cross-cutting; active around turn or job execution`

A workspace is like a customer account or project area. Many parts of the system need to know the current workspace, but passing the workspace ID through every function would be noisy and easy to forget. This file solves that by creating a temporary workspace scope: code enters `with ws(workspace_id):`, and everything inside can ask `ws_current()` for the active workspace.

That scope matters for safety. Credentials are only fetched through the current workspace, so a provider key is always looked up for the right customer. If the workspace has its own stored key, that is used. If not, the system falls back to a platform-wide environment variable. If neither exists, the call fails loudly instead of silently using the wrong key.

The same scope is used for billing. Code opens a `billable_event()` block, records model usage as it happens, and only writes the charges if the whole block finishes successfully. If an error is raised, the usage is discarded, so failed work is not billed.

The file also connects to the database’s current-workspace setting, which helps workspace-scoped database operations stay inside the right boundary. In short, this is the “badge at the door” for workspace-aware work: once inside the badge-controlled area, secrets, billing, and database scope all follow the same workspace.

#### Function details

##### `init_workspace_credentials`  (lines 29–33)

```
def init_workspace_credentials(store: CredentialStore | None) -> None
```

**Purpose**: Installs the credential store that knows how to read and write workspace-specific secrets. It is meant to be called once during startup, so later workspace code can resolve customer-provided keys.

**Data flow**: It receives either a credential store object or `None`. It saves that value in this module’s shared `_store` variable. After that, credential lookups either use this store first or, if no store was provided, can only fall back to platform environment variables.

**Call relations**: This function prepares the ground for later calls to `WorkspaceScope.credential`, `credential_is_stored`, `rotate_credential`, and `put_credential`. Those methods all check the shared store that this function installs.


##### `BillableEvent.usage`  (lines 49–51)

```
def usage(self, model: str, usage: Usage, pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: Adds one piece of model usage to a billable event. Code uses it while work is happening to say, “this model call consumed this much, using this pricing.”

**Data flow**: It receives a model name, a usage record, and pricing information. It packages those three values together and appends them to the event’s internal list. It returns nothing; the visible change is that the event now has one more usage item waiting to be billed.

**Call relations**: This is used inside a `WorkspaceScope.billable_event` block. The block later reads the collected items and sends each one to the accounting system if the surrounding work finishes without an error.


##### `WorkspaceScope.credential`  (lines 60–74)

```
async def credential(self, slot: str, env: str | None=None) -> str
```

**Purpose**: Fetches the secret value for a named credential slot, always in the context of this workspace. It protects against accidentally reading a key without saying which workspace owns it.

**Data flow**: It takes a slot name, such as a provider key name, and optionally the name of an environment variable. First it asks the configured credential store for this workspace’s stored value. If the workspace has no stored value, it looks in the process environment using the provided environment name or the uppercased slot name. It returns the secret string, or raises `CredentialSlotUnset` if no usable value exists.

**Call relations**: Workspace-aware code calls this through `ws_current().credential(...)` after a workspace has been bound with `ws(...)`. If the credential store says the slot is unset, this method deliberately falls back to the platform environment before giving up.

*Call graph*: 1 external calls (__init__).


##### `WorkspaceScope.credential_is_stored`  (lines 76–86)

```
async def credential_is_stored(self, slot: str) -> bool
```

**Purpose**: Answers whether this workspace has its own stored value for a credential slot. This is useful because workspace-owned provider keys may be treated differently from platform-provided defaults, especially for billing.

**Data flow**: It receives a credential slot name. If no credential store is configured, it returns `false`. Otherwise it tries to read the workspace’s stored value for that slot. If the read succeeds, it returns `true`; if the slot is unset, it returns `false`.

**Call relations**: This method uses the same credential store path as `WorkspaceScope.credential`, but it only asks whether a workspace-owned key exists. Other workspace-scoped code can use that answer before deciding how to charge or route a provider call.


##### `WorkspaceScope.rotate_credential`  (lines 88–93)

```
async def rotate_credential(self, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Replaces an existing workspace credential only if the current stored value matches what the caller expects. This compare-and-swap style prevents overwriting someone else’s recent change by accident.

**Data flow**: It receives a slot name, the expected existing secret, and the new plaintext secret. If there is no credential store, it returns `false` because there is nothing to rotate. Otherwise it asks the store to rotate the credential for this workspace and returns the store’s success-or-failure result.

**Call relations**: This method hands the actual secure update to the configured credential store. It belongs inside a bound workspace flow, so the credential being rotated is tied to `self.workspace_id`.


##### `WorkspaceScope.put_credential`  (lines 95–99)

```
async def put_credential(self, slot: str, plaintext: str) -> None
```

**Purpose**: Stores an initial credential value for this workspace. It is used when an authorized owner adds a workspace-specific secret.

**Data flow**: It receives a slot name and the plaintext secret to store. If no credential store is configured, it raises an error because storing is impossible. Otherwise it passes the workspace ID, slot, and plaintext value to the credential store. It returns nothing after the store accepts the value.

**Call relations**: This method is the write-side partner to `WorkspaceScope.credential`. It delegates storage to the credential store, while this file supplies the workspace identity that keeps the secret attached to the right workspace.


##### `WorkspaceScope.billable_event`  (lines 102–112)

```
async def billable_event(self) -> AsyncIterator[BillableEvent]
```

**Purpose**: Creates a safe billing block for work done by this workspace. Usage is collected during the block and written only if the block exits normally.

**Data flow**: It creates a fresh `BillableEvent` and gives it to the caller’s block. The caller records usage on that event. When the block finishes without an exception, this method opens a workspace database transaction and writes each recorded usage item to the workspace ledger. If there is no usage, it writes nothing. If the block raises an error, the code after the yield does not complete, so the usage is not recorded.

**Call relations**: Callers use this as `async with ws_current().billable_event() as bill:` around model or provider work. Internally it creates the event, then hands each recorded item to `record_workspace_usage` inside `workspace_tx`, so accounting and database scope stay tied to the workspace.

*Call graph*: 3 external calls (__init__, record_workspace_usage, workspace_tx).


##### `ws`  (lines 116–124)

```
def ws(workspace_id: UUID) -> Iterator[WorkspaceScope]
```

**Purpose**: Temporarily binds a workspace ID as the current workspace for a block of code. It is the main doorway into workspace-scoped execution.

**Data flow**: It receives a workspace ID. On entry, it stores that ID in the current-workspace context and yields a `WorkspaceScope` for the same ID. When the block ends, even if there was an error, it resets the context back to what it was before.

**Call relations**: Turn and job runners use this at their boundary so everything inside can call `ws_current()` instead of passing the workspace ID around. It also updates `ufo.db.current_workspace`, which database transactions can use to stay in the same workspace scope.

*Call graph*: 3 external calls (__init__, reset, set).


##### `ws_current`  (lines 127–133)

```
def ws_current() -> WorkspaceScope
```

**Purpose**: Returns the workspace scope that is currently bound. If no workspace has been bound, it raises a clear error instead of letting code proceed unsafely.

**Data flow**: It reads the current workspace ID from the shared context. If an ID is present, it wraps it in a `WorkspaceScope` and returns it. If there is no ID, it raises `WorkspaceUnbound` with a message telling the caller to wrap the work in `with ws(workspace_id):`.

**Call relations**: Workspace-aware code calls this whenever it needs credentials, billing, or the workspace identity. It depends on `ws` having set the current workspace earlier; together, `ws` and `ws_current` form the file’s basic bind-and-read pattern.

*Call graph*: 3 external calls (__init__, __init__, get).


### Workspace object surfaces
These files expose agents, conversations, credentials, and extensions as workspace objects with tightly constrained read and mutation behavior.

### `core/src/ufo/agents.py`

`domain_logic` · `object listing, reading, status checks, and admin-controlled object updates`

An “agent” here is the workspace’s AI worker: it has a prompt, a model choice, a reasoning setting, a sandbox size, and a rule about public internet access. This file turns that agent record into a standard object kind so the rest of the system can list it, inspect it, show status, and apply changes through one shared object interface.

The main problem it solves is safety. Agent settings are powerful: changing the model, prompt, or internet access can change how the workspace behaves. So this file puts gates in front of writes. Regular members can read. A workspace admin can edit. In chat, even an admin-driven turn is more restricted: only the main agent may change only the prompt, unless the change came from a prepared admin intent. Creating a new agent is even stricter: it must be done by an admin while using the main agent, and the new agent starts fresh rather than copying permissions, credentials, sources, or memory.

The file also keeps one subtle distinction clear. An agent may store its model as “auto”, meaning “use the deployment’s configured model.” When members ask for status or lists, the file reports the concrete model currently being used, but it keeps “auto” stored so a read-and-save cycle does not accidentally freeze the agent to today’s model.

#### Function details

##### `_effective_model`  (lines 52–57)

```
def _effective_model(ctx: ToolContext, stored: str) -> str
```

**Purpose**: Shows the model name a member should see for an agent. If the stored value says “auto”, it replaces that with the actual model currently resolved for this turn.

**Data flow**: It receives the current tool context and the model value saved in the database. If the saved value is the special “auto” marker, it reads the concrete model from the current agent in the context; otherwise it keeps the saved value. It returns the model string to display.

**Call relations**: The listing and status paths call this helper when they need to present an agent’s model to a user. It keeps display behavior consistent without changing what is stored in the agent record.

*Call graph*: called by 2 (list, status).


##### `AgentObjects.list`  (lines 102–129)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Builds a paged list of all agents in the current workspace. Each row gives the agent’s name and a short human-readable summary, including whether it is the main agent, which model it runs on, and whether public internet is allowed.

**Data flow**: It reads the current workspace id, opens a workspace database transaction, and selects agent rows for that workspace ordered by name. It turns each database row into an object-list row with a summary, using the effective model helper for “auto” models. It returns an object page shaped according to the incoming list query.

**Call relations**: This is called by the object system when someone asks to browse agent objects. It talks to the database, uses `_effective_model` for display, then hands the rows to the shared `object_page` helper so pagination and object-list formatting stay consistent with other object kinds.

*Call graph*: calls 1 internal fn (_effective_model); 5 external calls (__init__, select, workspace_tx, object_page, ws_current).


##### `AgentObjects.get`  (lines 131–156)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[AgentSpec] | None
```

**Purpose**: Fetches the full saved specification for one named agent. It returns the editable settings plus timestamps and, for non-main agents, a link showing which main agent they are scoped under.

**Data flow**: It takes a context and an agent name, then asks `_row` for that agent’s database row. If no row exists, it returns nothing. If the row exists, it builds an `AgentSpec` from the stored settings, wraps it in an object detail response with creation and update times, and adds a `scoped_to` link when the agent is not the main one.

**Call relations**: The object system uses this when someone opens or reads a specific agent object. It relies on `_row` for the database lookup, then packages the result into the standard object-detail shape used elsewhere.

*Call graph*: calls 1 internal fn (_row); 4 external calls (__init__, __init__, __init__, __init__).


##### `AgentObjects.status`  (lines 158–171)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns a small live status view for one agent. It says whether the agent is the main agent and which concrete model it is currently using.

**Data flow**: It receives an agent name and looks up that row with `_row`. If the agent does not exist, it returns nothing. If it exists, it returns a simple dictionary containing the main-agent flag and the display-ready model name, resolving “auto” through `_effective_model`.

**Call relations**: This is used when the object layer needs a lightweight status report rather than the full saved spec. It shares the same lookup path as `get` and the same model-display rule as `list`.

*Call graph*: calls 2 internal fn (_row, _effective_model).


##### `AgentObjects.apply`  (lines 173–229)

```
async def apply(self, ctx: ToolContext, name: str, spec: AgentSpec, old: AgentSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Applies a requested create or update for an agent, while enforcing the file’s safety rules. It is the main write path for agent objects.

**Data flow**: It receives the current context, the target name, the requested spec, and the previous spec if one exists. If there is no previous spec, it delegates to `_create`. For updates, it reads the current database row, checks that the speaker is an admin, decides what the next prompt and sandbox size should be, detects whether prompt or settings actually changed, enforces the stricter chat-turn rule, rejects empty prompts, and writes the changed values back to the database only if something changed.

**Call relations**: The object system calls this when an agent object is applied. It may hand off to `_create` for new agents, uses `_row` to verify and compare the current state, asks `ToolContext` whether the speaker is an admin and whether the acting agent is the main agent, and finally performs the database update when the change is allowed.

*Call graph*: calls 4 internal fn (_create, _row, agent_is_main, speaker_is_admin); 5 external calls (__init__, __init__, update, workspace_tx, ws_current).


##### `AgentObjects._create`  (lines 231–257)

```
async def _create(self, ctx: ToolContext, name: str, spec: AgentSpec) -> None
```

**Purpose**: Creates a new non-main agent row from the submitted settings. It deliberately creates only the agent configuration and does not copy privileges, credentials, sources, memory, or other derived data.

**Data flow**: It receives the context, new agent name, and requested spec. It checks that the speaker is an admin and that the current agent is the main agent, then rejects a missing or blank prompt. If allowed, it inserts a new agent row with a fresh id, the current workspace id, the submitted settings, and timestamps. If the name is already taken, the database uniqueness error is turned into a clear value error.

**Call relations**: `AgentObjects.apply` calls this when applying a spec for a name that does not already have an old object. It uses context permission checks before touching the database, then uses the workspace transaction and current workspace id to create the row in the right workspace.

*Call graph*: calls 2 internal fn (agent_is_main, speaker_is_admin); called by 1 (apply); 5 external calls (__init__, insert, workspace_tx, ws_current, uuid4).


##### `AgentObjects.delete`  (lines 259–266)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses deletion of agent objects. Agents are intentionally not removable through this object interface.

**Data flow**: It receives the context, name, and expected generation, but does not read or change any stored data. It immediately raises a “verb not supported” error with the agent-specific explanation.

**Call relations**: The object system calls this if someone tries to delete an agent object. Instead of handing off to storage, it stops the request at once so no agent row is removed through this path.

*Call graph*: 1 external calls (__init__).


##### `AgentObjects._row`  (lines 268–295)

```
async def _row(self, name: str) -> sa.Row | None
```

**Purpose**: Looks up one agent row in the current workspace and includes the name of the workspace’s main agent. Other methods use it as their shared database read helper.

**Data flow**: It receives an agent name, opens a workspace database transaction, and selects the matching agent in the current workspace. Alongside the agent’s prompt, id, settings, and timestamps, it also runs a small subquery to find the main agent’s name. It returns the single matching row or nothing if no such agent exists.

**Call relations**: `get`, `status`, and `apply` call this whenever they need the current stored state for a named agent. Centralizing the lookup keeps all those paths scoped to the current workspace and gives them the same view of the agent record.

*Call graph*: called by 3 (apply, get, status); 3 external calls (select, workspace_tx, ws_current).


### `core/src/ufo/conversations.py`

`domain_logic` · `request handling`

A conversation in this system is not authored like a document. It is created by a “surface,” meaning the place where the chat happened, such as Slack or another chat interface. Other things, like artifacts or scheduled tasks, may point back to the conversation they came from, so the system needs a safe way to resolve those links and show what conversation they mean.

This file provides that safe view. It only shows conversations for the current workspace, the selected agent, and the audiences the caller is allowed to read. Think of it like a library catalogue card for a conversation: it shows the surface, the surface’s own label for the origin, the audience, and timestamps. The actual transcript can be materialized through the status call, which reads stored transcript data and writes a plain text copy into the caller’s workspace if it is small enough.

The important rule is that conversations are read-only here. Surfaces create them, and retention policies close or remove them. So apply and delete always refuse. The file also takes care to re-check visibility after reading a transcript, so a caller cannot keep a transcript if the conversation stopped being visible during the operation.

#### Function details

##### `ConversationObjects.list`  (lines 68–70)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists the conversations the current tool call is allowed to see. It is used when a caller wants a page of conversation summaries rather than one specific conversation.

**Data flow**: It receives a tool context with the caller’s readable audience subjects and a list query with paging, filtering, or ordering choices. It asks the database for all visible conversation rows, turns each row into a short object summary, then passes those summaries through the shared object paging helper. The result is an object page ready to return to the caller.

**Call relations**: This is the public listing path for conversation objects. It relies on _rows to fetch only visible database rows, uses _row to turn each database row into a friendly object-list entry, and hands the finished entries to object_page so they are shaped like other object listings in the system.

*Call graph*: calls 2 internal fn (_rows, _row); 1 external calls (object_page).


##### `ConversationObjects.get`  (lines 72–74)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ConversationSpec] | None
```

**Purpose**: Finds one visible conversation by name and returns its full detail. The name is expected to be the conversation’s UUID, which is a standard unique identifier.

**Data flow**: It receives the tool context and a requested name. It asks _find to parse the name and look up a visible matching row. If nothing is found, it returns null; if a row is found, it converts that row into a detailed object view with _detail.

**Call relations**: This is the normal public read path for one conversation object. It delegates the searching and permission filtering to _find, then delegates the object-detail shape to _detail so the returned data matches the rest of the object system.

*Call graph*: calls 2 internal fn (_find, _detail).


##### `ConversationObjects.member_detail`  (lines 76–91)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[ConversationSpec] | None
```

**Purpose**: Returns one conversation for the portal when a signed-in member is outside an active tool turn. It only allows conversations tied to that member’s own audience and the workspace-shared audience, even for an admin.

**Data flow**: It receives an optional extension context, a conversation name, and the signed-in member’s identity. It builds the set of audience subjects that are valid for that member’s own conversation, searches for the named visible row, and returns nothing if it is absent. If it is present, it returns both the list-style row and the detail-style information together in a MemberObject.

**Call relations**: This is the portal-facing version of reading a conversation. It uses conversation_audience and audience_subjects to decide what the member may see, then uses _find, _row, and _detail in the same way as the turn-based list and get paths. It creates a MemberObject so the portal receives the summary and detail side by side.

*Call graph*: calls 3 internal fn (_find, _detail, _row); 3 external calls (__init__, audience_subjects, conversation_audience).


##### `ConversationObjects.status`  (lines 93–111)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports transcript status for one visible conversation and, when possible, writes a readable text transcript into the workspace. This gives a caller a safe way to inspect the message exchange without changing the conversation object itself.

**Data flow**: It receives the tool context, conversation name, and an expected generation value that this implementation does not use. It finds the visible conversation row, reads and formats the transcript messages, then checks again that the same conversation is still visible. If visibility was lost, it raises an unknown-object error. Otherwise it counts the messages, measures the transcript text size, and writes the text to a workspace file only if it is non-empty and not too large. It returns the message count, byte size, and the workspace path if a file was written.

**Call relations**: This is the public status path for conversation objects. It uses _find to locate the row, _exchange to read the transcript, and _unchanged_visible as a final safety check before exposing transcript text. If the safety check fails, it raises UnknownObject so the caller sees the same result as if the conversation did not exist for them.

*Call graph*: calls 3 internal fn (_exchange, _find, _unchanged_visible); 1 external calls (__init__).


##### `ConversationObjects.apply`  (lines 113–122)

```
async def apply(self, ctx: ToolContext, name: str, spec: ConversationSpec, old: ConversationSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to create or change a conversation through the object API. This protects the rule that conversations are made by chat surfaces, not by object edits.

**Data flow**: It receives the requested name, new conversation specification, optional old specification, and expected generation. It does not read or write any conversation data. It immediately raises a not-supported error explaining that conversations are surface-made.

**Call relations**: This is the mutation path that would normally save an object. For conversation objects, it deliberately stops the flow by raising VerbNotSupported, so no later write operation is possible.

*Call graph*: 1 external calls (__init__).


##### `ConversationObjects.delete`  (lines 124–131)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to delete a conversation through the object API. Conversation lifetime is controlled elsewhere, such as by retention rules, not by object deletion.

**Data flow**: It receives the tool context, conversation name, and expected generation. It does not look up or alter the database row. It immediately raises a not-supported error with the shared explanation that conversations are made by surfaces and not authored here.

**Call relations**: This is the delete path for the object kind, but it is intentionally a dead end. It raises VerbNotSupported so callers cannot remove conversations through this store.

*Call graph*: 1 external calls (__init__).


##### `ConversationObjects._exchange`  (lines 133–153)

```
async def _exchange(self, ctx: ToolContext, conversation_id: UUID) -> tuple[str, ...]
```

**Purpose**: Reads a stored conversation transcript and turns it into simple text lines. Each returned line is shaped like “role: message,” such as a user line or assistant line.

**Data flow**: It receives the tool context and a conversation UUID. It builds the blob storage key for that transcript, reads the blob, and decodes it into transcript messages. If the blob is missing, it returns an empty exchange. If the blob is present but unreadable, it raises an error. For each message, it extracts plain text directly or gathers text blocks from richer message content, then returns all non-empty lines as a tuple of strings.

**Call relations**: This helper is called by status when transcript information is needed. It hands status a clean text exchange, leaving status to decide whether to write that text into the workspace and what summary numbers to return.

*Call graph*: called by 1 (status); 2 external calls (decode, transcript_key).


##### `ConversationObjects._unchanged_visible`  (lines 155–168)

```
async def _unchanged_visible(self, subjects: frozenset[str], row: sa.Row) -> bool
```

**Purpose**: Checks that a conversation is still visible to the same subjects and still has the same audience after transcript text has been read. This prevents a race where access changes in the middle of preparing a transcript.

**Data flow**: It receives the allowed audience subjects and the earlier database row. It opens a workspace database transaction, builds the same visibility query, narrows it to the same conversation id and audience, and asks the database whether such a row still exists. It returns true if the row is still visible, false otherwise.

**Call relations**: This helper is used by status after _exchange has read the transcript but before any text is exposed. It reuses _visible so its permission check matches the normal listing and lookup logic.

*Call graph*: calls 1 internal fn (_visible); called by 1 (status); 3 external calls (exists, select, workspace_tx).


##### `ConversationObjects._find`  (lines 170–176)

```
async def _find(self, subjects: frozenset[str], name: str) -> sa.Row | None
```

**Purpose**: Looks up one visible conversation by its object name. It treats invalid UUID names as a simple miss rather than an error.

**Data flow**: It receives the allowed audience subjects and a name string. It tries to parse the name as a UUID. If parsing fails, it returns null. If parsing succeeds, it asks _rows for matching visible rows and returns the first row if one exists, otherwise null.

**Call relations**: This helper is the shared lookup path for get, member_detail, and status. It keeps the name parsing and visible-row lookup in one place, and it relies on _rows for the actual database query.

*Call graph*: calls 1 internal fn (_rows); called by 3 (get, member_detail, status); 1 external calls (UUID).


##### `ConversationObjects._rows`  (lines 178–185)

```
async def _rows(self, subjects: frozenset[str], *, conversation_id: UUID | None) -> tuple[sa.Row, ...]
```

**Purpose**: Fetches conversation rows from the database that are visible to a set of audience subjects. It can fetch all visible rows or only one conversation id.

**Data flow**: It receives the allowed audience subjects and an optional conversation UUID. It starts with the shared visibility query from _visible, adds a conversation-id filter when one was provided, opens a workspace database transaction, runs the query, and returns the resulting rows as a tuple.

**Call relations**: This is the database-reading helper behind list and _find. It uses _visible to make sure every read applies the same workspace, agent, and audience limits before any caller turns rows into object results.

*Call graph*: calls 1 internal fn (_visible); called by 2 (_find, list); 1 external calls (workspace_tx).


##### `_visible`  (lines 188–209)

```
def _visible(subjects: frozenset[str]) -> sa.Select
```

**Purpose**: Builds the standard database query for conversations the caller is allowed to see. This is the central permission filter for this file’s database reads.

**Data flow**: It receives a frozen set of audience subject strings. It creates a SQL query that selects conversation fields and the related agent name, joins conversations to agents, and filters by the current workspace, the currently selected object agent, and audiences contained in the allowed subject set. It returns the query object without running it.

**Call relations**: _rows uses this query to fetch visible rows, and _unchanged_visible uses it to re-check access before transcript text is exposed. By centralizing the query here, listing, lookup, and safety checks all use the same visibility rules.

*Call graph*: called by 2 (_rows, _unchanged_visible); 3 external calls (select, object_agent_id, ws_current).


##### `_row`  (lines 212–225)

```
def _row(row: sa.Row) -> ObjectRow
```

**Purpose**: Turns a database conversation row into a short object summary for listings. It chooses a human-friendly summary such as the surface label on the surface, plus the creation date.

**Data flow**: It receives a database row with conversation fields. It builds an origin phrase from the surface and optional surface label, builds a small fields dictionary, and creates an ObjectRow whose name is the conversation id string and whose summary describes where and when the conversation started.

**Call relations**: ConversationObjects.list uses this for each listed row, and member_detail uses it for the portal’s summary portion. It is the small-card view that pairs with _detail’s fuller view.

*Call graph*: called by 2 (list, member_detail); 1 external calls (__init__).


##### `_detail`  (lines 228–240)

```
def _detail(row: sa.Row) -> ObjectDetail[ConversationSpec]
```

**Purpose**: Turns a database conversation row into the full object detail view. It includes the conversation specification, timestamps, and a link to the agent the conversation is scoped to.

**Data flow**: It receives a database row. It copies the surface, optional surface label, and audience into a ConversationSpec, copies the created and updated timestamps, and builds a scoped_to link pointing at the agent name. It returns an ObjectDetail containing all of that.

**Call relations**: ConversationObjects.get and member_detail call this when they need the detailed form of a conversation. It also creates the agent link that lets other parts of the object system understand which agent this conversation belongs to.

*Call graph*: called by 2 (get, member_detail); 4 external calls (__init__, __init__, __init__, __init__).


### `core/src/ufo/credential_kind.py`

`domain_logic` · `request handling`

Some extensions need outside secrets, such as API keys, but the system must not treat those secrets like ordinary editable data. This file turns each credential slot declared by an installed extension into a visible workspace object. Think of it like a row of labeled safety-deposit boxes: everyone can see that a box exists and whether it has something inside, but nobody can view the contents through this object interface.

The important split is between the declaration and the stored value. The declaration comes from an extension manifest: the slot name, description, extension name, and where the credential may be used. The database row only means “this slot has a sealed value.” If there is no row, the slot is still listed, but it is shown as empty.

Reads return only safe information: a list of slots, details about each slot, timestamps if it has been filled, and a filled-or-empty status. Filling or rotating a credential is deliberately refused here, because that involves a private secret handoff handled by `request_credentials`. Deleting is allowed only for workspace admins, and it removes the stored value without removing the slot declaration. The result is a safe public index of credential needs, not a way to inspect or casually edit secrets.

#### Function details

##### `CredentialObjects.list`  (lines 77–78)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Builds the normal workspace list of credential slots. It shows every declared slot and whether it is currently filled, without showing any secret value.

**Data flow**: It receives a tool context and a list query, asks `_rows` to build the safe row summaries, then passes those rows through `object_page` so filtering, ordering, or paging can be applied. It returns a page of object rows for the caller to display.

**Call relations**: This is the general list path for credential objects. It relies on `_rows` for the actual slot summaries, then hands those summaries to the shared object paging helper so credential slots behave like other workspace objects.

*Call graph*: calls 1 internal fn (_rows); 1 external calls (object_page).


##### `CredentialObjects.member_page`  (lines 80–90)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Builds the credential-slot list seen by a signed-in workspace member in the portal. It gives members the same safe filled-or-empty view, because no read path reveals a credential value.

**Data flow**: It receives portal-related inputs such as the extension context, member id, admin flag, and query. It ignores member-specific scoping because credential declarations are workspace-wide, gets the same safe rows from `_rows`, and returns them as a paged result.

**Call relations**: This is the member-facing version of `list`. Like `list`, it depends on `_rows` and `object_page`, keeping the portal view aligned with the regular workspace object view.

*Call graph*: calls 1 internal fn (_rows); 1 external calls (object_page).


##### `CredentialObjects.get`  (lines 92–93)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[CredentialSpec] | None
```

**Purpose**: Returns the safe detail view for one credential slot. It describes the declared slot and timestamps, but not the secret.

**Data flow**: It receives a tool context and a slot name, then asks `_detail` to look up the slot declaration and any fill timestamps. It returns that detail object, or `None` if no extension declared that slot.

**Call relations**: This is the normal single-object read path. It delegates the real work to `_detail`, which centralizes how credential declarations are turned into safe object details.

*Call graph*: calls 1 internal fn (_detail).


##### `CredentialObjects.member_detail`  (lines 95–110)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[CredentialSpec] | None
```

**Purpose**: Returns the portal view of one credential slot for a signed-in member. It combines the row-style summary with the detailed declaration, still without exposing the credential value.

**Data flow**: It receives a slot name plus member and admin information. It first asks `_detail` for the slot declaration and timestamps; if the slot is unknown, it returns `None`. Otherwise it rebuilds the safe row list with `_rows`, finds the matching row, and returns both row and detail together as a member object.

**Call relations**: This is the member-facing counterpart to `get`. It uses `_detail` for the slot details and `_rows` for the list-style summary, then packages both into `MemberObject` for the portal.

*Call graph*: calls 2 internal fn (_detail, _rows); 1 external calls (__init__).


##### `CredentialObjects.status`  (lines 112–136)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports the current safe status of one credential slot. The main answer is whether the slot is filled; when the slot has a host choice, it may also report the selected host.

**Data flow**: It receives a slot name and first checks the declared-slot map from `_named`. If the slot is not declared, it returns `None`. If it exists, it opens a workspace database transaction, checks whether a credential row exists for the current workspace and slot, and returns `{"filled": true}` or `{"filled": false}`. If host information is available through the credential store, it also asks `credential_host` for the current host and includes it.

**Call relations**: This function is used when callers need a compact live status rather than a full object detail. It combines declaration lookup through `_named`, workspace identity from `ws_current`, database access through `workspace_tx`, and optional host lookup through `credential_host`.

*Call graph*: calls 1 internal fn (_named); 4 external calls (select, credential_host, workspace_tx, ws_current).


##### `CredentialObjects.apply`  (lines 138–147)

```
async def apply(self, ctx: ToolContext, name: str, spec: CredentialSpec, old: CredentialSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create, fill, or update a credential through the ordinary object apply path. This protects secrets from being sent through the wrong channel.

**Data flow**: It receives the requested slot name, the proposed spec, any old spec, and generation information, but does not use them to change anything. Instead it immediately raises a `VerbNotSupported` error explaining that filling or rotating credentials must use `request_credentials`.

**Call relations**: This is a deliberate guardrail in the object system. While list and read operations are allowed, this function blocks the generic write path and points callers toward the private credential handoff flow.

*Call graph*: 1 external calls (__init__).


##### `CredentialObjects.delete`  (lines 149–165)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Clears the stored value for a credential slot. It does not remove the slot itself, because the slot is declared by an installed extension.

**Data flow**: It receives a tool context and slot name. It first asks the context whether the speaker is a workspace admin; if not, it raises `AdminRequired`. If allowed, it finds the declared slot with `_named`, opens a workspace database transaction, and deletes the credential row for the current workspace and that slot. Afterward the slot will still appear, but as empty.

**Call relations**: This is the only write-like operation this object kind permits. It depends on `ToolContext.speaker_is_admin` for the permission check, `_named` to resolve the slot, and the workspace database helpers to remove only the current workspace’s stored credential row.

*Call graph*: calls 2 internal fn (_named, speaker_is_admin); 4 external calls (__init__, delete, workspace_tx, ws_current).


##### `CredentialObjects._rows`  (lines 167–179)

```
async def _rows(self) -> tuple[ObjectRow, ...]
```

**Purpose**: Builds the safe list rows for all declared credential slots. Each row says which extension declared the slot and whether it is filled.

**Data flow**: It first asks `_filled_slots` which slot names currently have database rows. It also asks `_named` for all declared slots by name. It then creates one `ObjectRow` per declared slot, with a human-readable summary and fields for extension name and filled status, and returns the rows sorted by name.

**Call relations**: This helper is the shared source for list-style views. `list`, `member_page`, and `member_detail` all call it so the workspace and portal views describe credential slots consistently.

*Call graph*: calls 2 internal fn (_filled_slots, _named); called by 3 (list, member_detail, member_page); 1 external calls (__init__).


##### `CredentialObjects._detail`  (lines 181–205)

```
async def _detail(self, name: str) -> ObjectDetail[CredentialSpec] | None
```

**Purpose**: Builds the safe detailed record for one declared credential slot. It includes the declaration and fill timestamps, but never reads or returns the stored secret.

**Data flow**: It receives a slot name and looks it up in the declared-slot map from `_named`. If the name is unknown, it returns `None`. If known, it queries the credential table for the current workspace and slot to find creation and update timestamps. It then creates a `CredentialSpec` from the declaration, including host information if present, and wraps it in an `ObjectDetail` with timestamps or null timestamps if empty.

**Call relations**: This helper powers both `get` and `member_detail`. It joins two safe sources of information: extension declarations and database timestamps. It intentionally avoids the ciphertext column where the sealed secret value lives.

*Call graph*: calls 1 internal fn (_named); called by 2 (get, member_detail); 5 external calls (__init__, __init__, select, workspace_tx, ws_current).


##### `CredentialObjects._named`  (lines 207–208)

```
def _named(self) -> dict[str, DeclaredSlot]
```

**Purpose**: Turns the stored collection of declared credential slots into a lookup table by slot name. This makes it easy to answer questions about one named slot.

**Data flow**: It reads `self.slots`, passes them to `named_slots`, and returns a dictionary-like mapping from slot names to declared slot objects. It does not touch the database or secret values.

**Call relations**: This small helper supports the main credential operations. `_detail`, `_rows`, `delete`, and `status` all use it before working with a particular slot, so they only act on slots that extensions have actually declared.

*Call graph*: called by 4 (_detail, _rows, delete, status); 1 external calls (named_slots).


##### `CredentialObjects._filled_slots`  (lines 210–219)

```
async def _filled_slots(self) -> frozenset[str]
```

**Purpose**: Finds which declared credential slots currently have stored values in the current workspace. It returns only slot names, not the values.

**Data flow**: It opens a workspace database transaction, asks for all credential slot names stored for the current workspace, and collects those names into an immutable set. The output is a simple set such as “these slots are filled.”

**Call relations**: This helper feeds `_rows`. `_rows` uses its answer to mark each declared slot as filled or empty while keeping the actual credential data completely out of the listing path.

*Call graph*: called by 1 (_rows); 3 external calls (select, workspace_tx, ws_current).


### `core/src/ufo/ext/extension_kind.py`

`domain_logic` · `request handling`

This file exists so a user or tool can ask, “What extensions is this deploy running, and what do they contribute?” An extension manifest is like a package label: it declares the extension’s name, version, tools, credential slots, jobs, hooks, and other pieces. This file projects those labels into the normal object system so they can be listed and read in the same way as other workspace objects.

The important boundary is that these objects are declarations, not database rows. They come from the deploy’s loaded manifests, so they have no creation or update timestamps. They also never expose secret values. Credential slots are shown by name only, like seeing that a form has a field called “API key” without seeing what anyone typed into it.

The file first gives each manifest a safe object name: lowercase, hyphenated, and checked against the project’s object-name rules. If two extension names would collapse to the same object name, startup fails clearly rather than hiding one.

`ExtensionObjects` then provides the read-only behavior. Listing gives compact rows with version and contribution counts. Getting one returns the full declaration. Status returns what the extension asks from the deploy, such as sandbox internet access or required seams from other extensions. Any attempt to create, update, or delete is rejected because installing or removing extensions is a deploy-level action done through the lockfile.

#### Function details

##### `named_extensions`  (lines 46–61)

```
def named_extensions(manifests: tuple[Manifest, ...]) -> dict[str, Manifest]
```

**Purpose**: Builds the public object names for all active extensions. It makes extension names safe and predictable for object lookup, and it stops the deploy if two extensions would end up with the same object name.

**Data flow**: It receives the loaded extension manifests. For each manifest, it lowercases the manifest name, replaces non-letter-or-number runs with hyphens, trims extra hyphens, and checks that the result is a valid object name. It returns a dictionary from that object name to the original manifest, or raises an error if two manifests collide.

**Call relations**: This is used when the deploy is assembling the active extension set for the `extension` object kind. It relies on regular-expression cleanup and the shared object-name validator so the names here follow the same rules as other workspace objects.

*Call graph*: 2 external calls (sub, validate_object_name).


##### `ExtensionObjects.list`  (lines 90–107)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows a page of the active extensions in a compact list. Each row tells the reader the extension’s version and how many tools and credential slots it declares.

**Data flow**: It receives a tool context and a list query, reads the in-memory mapping of extension names to manifests, converts each manifest into an `ExtensionSpec`, and builds lightweight rows with summary text and sortable/filterable fields. It returns an object page shaped according to the query.

**Call relations**: When someone lists `extension` objects, this method is the front door. It asks `_spec` to translate each manifest into the plain declaration format, wraps those declarations into list rows, and hands the rows to the common paging helper so listing behaves like other object kinds.

*Call graph*: calls 1 internal fn (_spec); 2 external calls (__init__, object_page).


##### `ExtensionObjects.get`  (lines 109–113)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ExtensionSpec] | None
```

**Purpose**: Returns the full declaration for one extension, if that extension exists. This is how a caller sees exactly what tools, object kinds, credential slots, surfaces, jobs, hooks, sources, and subagents an extension contributes.

**Data flow**: It receives a tool context and an object name. It looks up that name in the active extension mapping. If no manifest is found, it returns nothing; if one is found, it converts the manifest into an `ExtensionSpec` and wraps it in an object detail with no timestamps.

**Call relations**: This is used when someone reads a single `extension` object. It delegates the manifest-to-spec translation to `_spec`, then packages the result as an object detail so it fits the same read shape as other object kinds.

*Call graph*: calls 1 internal fn (_spec); 1 external calls (__init__).


##### `ExtensionObjects.status`  (lines 115–128)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports what an extension asks the deploy to provide, rather than what the extension contributes to users. In practice, this exposes sandbox internet needs and required seams from other extensions.

**Data flow**: It receives a tool context, an extension object name, and an optional expected generation value. It looks up the manifest by name. If absent, it returns nothing; if present, it returns a small dictionary containing the manifest’s `sandbox_internet` setting and its `requires` list.

**Call relations**: This is used when a caller asks for the status side of an `extension` object. Unlike `get`, it does not build the full user-facing declaration; it directly reports deploy requirements stored on the manifest.


##### `ExtensionObjects.apply`  (lines 130–139)

```
async def apply(self, ctx: ToolContext, name: str, spec: ExtensionSpec, old: ExtensionSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses any attempt to create or update an extension object. This protects the rule that extensions are installed or removed through deploy configuration, not through normal object mutation.

**Data flow**: It receives the requested name, desired spec, possible old spec, context, and optional generation check. Instead of changing anything, it immediately raises a `VerbNotSupported` error with a message explaining that extension installation is a deploy lockfile action.

**Call relations**: This method is reached if the object system tries to apply a change to an `extension` object. It does not call any manifest-writing logic; it stops the flow right away so callers cannot mutate the loaded extension set through this interface.

*Call graph*: 1 external calls (__init__).


##### `ExtensionObjects.delete`  (lines 141–148)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses any attempt to delete an extension object. Removing an extension must happen through the deploy’s lockfile tools, not through this read-only projection.

**Data flow**: It receives the target extension name, context, and optional generation check. It ignores them for mutation purposes and raises a `VerbNotSupported` error with the standard explanation.

**Call relations**: This method is reached if the object system tries to delete an `extension` object. Like `apply`, it acts as a guardrail: it prevents chat/object operations from changing which extensions the deploy has loaded.

*Call graph*: 1 external calls (__init__).


##### `ExtensionObjects._spec`  (lines 150–168)

```
def _spec(self, manifest: Manifest) -> ExtensionSpec
```

**Purpose**: Turns one raw extension manifest into the clean declaration shown to readers. It keeps only names and public declarations, never stored secret values.

**Data flow**: It receives a manifest. It reads the manifest’s name, version, tools, connector tools, object kinds, credential slots, surfaces, jobs, hook events, source backends, and subagent profiles. It returns an `ExtensionSpec` containing those items as simple name lists.

**Call relations**: This is the shared translator used by both `list` and `get`. `list` uses it to compute summaries and counts, while `get` uses it to return the full declaration for one extension.

*Call graph*: called by 2 (get, list); 1 external calls (__init__).


### Object operation authorization
These files provide the shared object API doorway and the scoped agent identity used to audit and authorize object-level operations.

### `core/src/ufo/objects.py`

`domain_logic` · `startup validation and object tool request handling`

A workspace object is like a labeled file in a shared cabinet: it has a kind, a name, and a structured spec written as YAML. This file sets the rules for that cabinet. It checks that kind names and object names are safe, that specs can be shown back to users without leaking secrets, and that each extension gets a clean contract for storing its own objects.

The file has three main jobs. First, it defines the data shapes used everywhere: object references, links between objects, list rows, pages, ownership records, and registered object kinds. Second, it supplies reusable gates for common access patterns, especially member-owned objects where a row may be private, shared, admin-only, or protected from edits by someone else. Third, it exposes five tool actions: list, get, explain, apply, and delete. These tools parse user input, validate it, find the right registered kind, switch into that extension's context, and call the kind's store.

A key safety detail is the “generation” check, which works like checking that a document has not changed since you opened it. If a generated row changed between read and write, the operation refuses rather than accidentally editing the wrong version.

#### Function details

##### `ObjectRef.validate_kind`  (lines 92–95)

```
def validate_kind(cls, value: str) -> str
```

**Purpose**: Checks that an object reference uses a valid kind name. This keeps every object identity in the same simple snake_case style.

**Data flow**: It receives a kind string, compares it with the allowed pattern, and either returns the same string or raises an error explaining the bad name.

**Call relations**: This runs automatically when an ObjectRef is built, so later code can trust that a reference's kind field is already clean.


##### `ObjectRef.validate_name`  (lines 99–105)

```
def validate_name(cls, value: str) -> str
```

**Purpose**: Checks that an object reference uses a valid object name. This prevents references from carrying names that the object system itself would reject.

**Data flow**: It receives a name string, checks its length and allowed characters, and returns it unchanged if valid. If not, it raises a clear error.

**Call relations**: This is part of ObjectRef validation. Code that renders or follows links can rely on these names matching the shared object-name rule.

*Call graph*: 1 external calls (fullmatch).


##### `ObjectRef.__str__`  (lines 107–108)

```
def __str__(self) -> str
```

**Purpose**: Turns an object reference into a short human-readable label. The label uses the familiar kind/name form.

**Data flow**: It reads the reference's kind and name fields and joins them with a slash into one string.

**Call relations**: This is used whenever Python needs a text form of an ObjectRef, such as in logs or error messages.


##### `_ObjectCursor.validate_rank`  (lines 206–211)

```
def validate_rank(self) -> '_ObjectCursor'
```

**Purpose**: Checks that a saved list cursor contains the right kind of value for its sort category. This protects paging from malformed or mismatched continuation tokens.

**Data flow**: It reads the cursor's rank and value, confirms they fit together, and returns the cursor if they do. Otherwise it raises an error.

**Call relations**: This runs when object_page rebuilds a cursor from a user's next-page token, before using that token to skip already-seen rows.


##### `object_page`  (lines 214–293)

```
def object_page(rows: tuple[ObjectRow, ...], query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Applies the common listing behavior for object rows: search, exact filters, sorting, and page-sized results. Kinds use it so every object list behaves the same way.

**Data flow**: It receives lightweight rows and a listing query. It checks that fields are declared, filters rows by search text and exact matches, sorts them, applies any cursor, and returns an ObjectPage with rows plus a next cursor when more results remain.

**Call relations**: MemberOwnedObjects.list and MemberReadableObjects.member_page call this after they have already decided which rows the caller is allowed to see. It uses _sortable to compare field values and creates _ObjectCursor tokens for later pages.

*Call graph*: calls 1 internal fn (_sortable); called by 2 (list, member_page); 2 external calls (__init__, __init__).


##### `object_page.value`  (lines 238–243)

```
def value(row: ObjectRow, name: str) -> JsonValue
```

**Purpose**: Fetches the value of one sortable or filterable field from a row. It treats name and summary as built-in fields and everything else as a declared row field.

**Data flow**: It receives a row and a field name, then returns the row name, summary, or the matching value from the row's fields map.

**Call relations**: This helper is used inside object_page while searching, filtering, sorting, and cursor comparison.


##### `_sortable`  (lines 296–309)

```
def _sortable(value: JsonValue, field_name: str) -> tuple[_SortRank, str | int | float]
```

**Purpose**: Turns a simple field value into a form that can be safely sorted. It rejects complex values like lists or objects because there is no obvious plain ordering for them.

**Data flow**: It receives a field value and field name. It assigns nulls, booleans, numbers, and strings to ordered categories, returning a sortable pair, or raises an error for non-scalar values.

**Call relations**: object_page calls this whenever it needs to order rows or compare a row with a paging cursor.

*Call graph*: called by 1 (object_page).


##### `ObjectStore.list`  (lines 324–324)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Defines the contract for a kind to return a page of its objects. Each concrete object kind supplies the real storage-specific version.

**Data flow**: It takes a tool context and an ObjectListQuery, and is expected to return an ObjectPage containing lightweight rows.

**Call relations**: ObjectVerbs._list calls this through the registered kind's store after resolving the kind and target agent.


##### `ObjectStore.get`  (lines 326–326)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SpecT] | None
```

**Purpose**: Defines the contract for reading one full object by name. The result includes the spec and metadata needed by later actions.

**Data flow**: It takes a tool context and object name, and returns an ObjectDetail if found or None if absent or hidden by the store.

**Call relations**: ObjectVerbs._get, ObjectVerbs._apply, and ObjectVerbs._delete rely on this read before showing, updating, or deleting an object.


##### `ObjectStore.status`  (lines 328–334)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Defines the contract for reading live, kind-specific status beside an object's spec. Status is separate from the saved spec because it may change over time.

**Data flow**: It receives context, name, and an expected generation. It should return a JSON-like status map, or None if there is no status or the object is gone.

**Call relations**: ObjectVerbs._get calls this after get, passing the generation it just observed so stores can refuse stale reads.


##### `ObjectStore.apply`  (lines 336–344)

```
async def apply(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Defines the contract for creating or updating an object after the core system has validated its envelope, name, and spec. The kind decides what the change means in its own tables.

**Data flow**: It receives context, name, the new validated spec, the old spec if one existed, and an expected generation. It performs the mutation or raises a clear refusal.

**Call relations**: ObjectVerbs._apply calls this once it has parsed the manifest and checked create/update rules.


##### `ObjectStore.delete`  (lines 346–352)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Defines the contract for deleting an object by name. The kind decides how deletion maps onto its own storage.

**Data flow**: It receives context, name, and the generation observed by the earlier read. It removes the object or raises an error if deletion is not allowed.

**Call relations**: ObjectVerbs._delete calls this after first reading the object so it can pass a generation fence and echo the deleted spec.


##### `owner_emails`  (lines 371–386)

```
async def owner_emails(owners: Iterable[UUID | None]) -> dict[UUID | None, str]
```

**Purpose**: Looks up email addresses for a set of member owners in one database query. Listings can use this to show an owner_email field without doing one query per row.

**Data flow**: It receives member IDs, drops any None values, queries the workspace member table for matching emails, and returns a map from member ID to email.

**Call relations**: Object kinds can call this while building their list rows. It uses the workspace database transaction helper to read member records.

*Call graph*: 2 external calls (select, workspace_tx).


##### `MemberOwnedObjects.list`  (lines 428–436)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists only the member-owned rows the current actor is allowed to see. This keeps private and admin-only objects out of normal listings.

**Data flow**: It reads whether the speaker is an admin and who the acting member is, asks the subclass for owned rows, filters them through the visibility rule, converts them to ObjectRow values, and returns a paged result.

**Call relations**: ObjectStore.list implementations can inherit this. It delegates row retrieval to _owned_rows and final search/sort/page work to object_page.

*Call graph*: calls 4 internal fn (_owned_rows, _visible, object_page, speaker_is_admin); 1 external calls (__init__).


##### `MemberOwnedObjects.get`  (lines 438–449)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SpecT] | None
```

**Purpose**: Reads one member-owned object only if the actor may see it. If the object is generated, it attaches the generation value to protect later operations.

**Data flow**: It looks up the owner, checks visibility, asks the subclass for the detail, and returns that detail or None. For generated owners, it copies the detail with the owner's generation included.

**Call relations**: ObjectVerbs._get and other flows call store.get; subclasses using this base get the shared visibility gate before _detail is called.

*Call graph*: calls 4 internal fn (_detail, _owner, _visible, speaker_is_admin); 1 external calls (replace).


##### `MemberOwnedObjects.status`  (lines 451–471)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reads live status for a member-owned object without leaking information about objects the actor cannot see. It also checks that the row did not change during the status read.

**Data flow**: It finds the owner, checks the expected generation, verifies visibility, asks the subclass for status, then rechecks owner, generation, and visibility before returning the status.

**Call relations**: ObjectVerbs._get calls status after get. This method coordinates _owner, _status, _visible, and _require_current_generation to keep the read consistent.

*Call graph*: calls 5 internal fn (_owner, _require_current_generation, _status, _visible, speaker_is_admin); 1 external calls (__init__).


##### `MemberOwnedObjects.apply`  (lines 473–499)

```
async def apply(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Creates or updates a member-owned object while enforcing ownership, admin, visibility, live-speaker, and generation rules. It is the shared edit gate for these kinds.

**Data flow**: It reads the current owner, decides whether this is a create or update, checks visibility and freshness, checks whether the actor may edit, optionally requires a real speaking member, then passes the actual write to _apply_owned.

**Call relations**: ObjectVerbs._apply reaches this through the kind's store. The subclass supplies _apply_owned, while this base class supplies the repeated safety checks.

*Call graph*: calls 7 internal fn (_admin_can_apply, _apply_owned, _owned, _owner, _require_current_generation, _visible, speaker_is_admin); 2 external calls (__init__, __init__).


##### `MemberOwnedObjects.delete`  (lines 501–519)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Deletes a member-owned object only when the actor may delete it. Invisible rows look like missing rows, which avoids revealing private object names.

**Data flow**: It finds the owner, checks generation, rejects missing or invisible rows, verifies owner or admin permission, optionally requires a live speaker, and calls _delete_owned.

**Call relations**: ObjectVerbs._delete reaches this through the kind's store. It uses _owner, _visible, _owned, and _delete_owned to separate policy from storage.

*Call graph*: calls 6 internal fn (_delete_owned, _owned, _owner, _require_current_generation, _visible, speaker_is_admin); 2 external calls (__init__, __init__).


##### `MemberOwnedObjects._owned`  (lines 521–525)

```
def _owned(self, owner: OwnerT, acting: UUID | None) -> bool
```

**Purpose**: Answers whether the acting member is the actual owner of a row. Admin-only rows with no member owner are deliberately not considered owned by anyone.

**Data flow**: It compares the row owner's member ID with the acting member ID and returns true only when both are real and equal.

**Call relations**: _visible, apply, and delete use this as the basic ownership check before deciding what a caller may see or change.

*Call graph*: called by 3 (_visible, apply, delete).


##### `MemberOwnedObjects._visible`  (lines 527–528)

```
def _visible(self, owner: OwnerT, acting: UUID | None, is_admin: bool) -> bool
```

**Purpose**: Answers whether a row should be visible to the actor. A row is visible if it is shared, owned by the actor, or the actor is an admin.

**Data flow**: It receives owner information, acting member ID, and admin status, then returns a yes/no visibility result.

**Call relations**: List, get, status, apply, and delete all use this so they agree on what counts as visible.

*Call graph*: calls 1 internal fn (_owned); called by 5 (apply, delete, get, list, status).


##### `MemberOwnedObjects._admin_can_apply`  (lines 530–531)

```
def _admin_can_apply(self, old: SpecT, spec: SpecT) -> bool
```

**Purpose**: Lets a subclass opt into limited admin edits of someone else's object. The default is cautious and says no.

**Data flow**: It receives the old and new specs and returns False unless a subclass overrides it.

**Call relations**: MemberOwnedObjects.apply calls this when an admin tries to edit a member-owned row that is not their own.

*Call graph*: called by 1 (apply).


##### `MemberOwnedObjects._require_current_generation`  (lines 533–550)

```
def _require_current_generation(self, name: str, owner: OwnerT | None, expected_generation: UUID | None, action: str) -> None
```

**Purpose**: Refuses an operation if the object under a name has changed since it was read. This prevents editing or reading status from the wrong generated row.

**Data flow**: It receives the name, current owner, expected generation, and action text. It compares the current generation with the expected one and raises an error if they differ.

**Call relations**: Status, apply, and delete call this around operations that must be fenced against stale reads.

*Call graph*: called by 3 (apply, delete, status).


##### `MemberOwnedObjects._owner`  (lines 552–553)

```
async def _owner(self, ctx: ToolContext, name: str) -> OwnerT | None
```

**Purpose**: Finds the owner record for one object name by scanning the subclass's owned rows. It is the shared lookup step for permission checks.

**Data flow**: It asks _owned_rows for all rows available to the store, searches for the matching name, and returns that row's owner or None.

**Call relations**: Get, status, apply, and delete call this before deciding visibility, freshness, or permission.

*Call graph*: calls 1 internal fn (_owned_rows); called by 4 (apply, delete, get, status).


##### `MemberOwnedObjects._owned_rows`  (lines 555–556)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[OwnerT], ...]
```

**Purpose**: Defines the subclass hook for returning rows with owner information. The base class cannot know where each kind stores its rows.

**Data flow**: It receives a tool context and should return owned rows; the base implementation raises NotImplementedError.

**Call relations**: MemberOwnedObjects.list and _owner rely on subclasses implementing this hook.

*Call graph*: called by 2 (_owner, list).


##### `MemberOwnedObjects._detail`  (lines 558–561)

```
async def _detail(self, ctx: ToolContext, name: str, owner: OwnerT) -> ObjectDetail[SpecT] | None
```

**Purpose**: Defines the subclass hook for reading the full detail of one visible owned object. The base class handles visibility first.

**Data flow**: It receives context, name, and owner, and should return ObjectDetail or None; the base implementation raises NotImplementedError.

**Call relations**: MemberOwnedObjects.get calls this after finding a visible owner.

*Call graph*: called by 1 (get).


##### `MemberOwnedObjects._status`  (lines 563–566)

```
async def _status(self, ctx: ToolContext, name: str, owner: OwnerT) -> dict[str, JsonValue] | None
```

**Purpose**: Defines the subclass hook for reading live status for one owned object. The base class surrounds it with visibility and generation checks.

**Data flow**: It receives context, name, and owner, and should return a JSON-like status map or None; the base implementation raises NotImplementedError.

**Call relations**: MemberOwnedObjects.status calls this between its before-and-after safety checks.

*Call graph*: called by 1 (status).


##### `MemberOwnedObjects._apply_owned`  (lines 568–576)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, owner: OwnerT | None) -> None
```

**Purpose**: Defines the subclass hook that actually creates or updates the owned object. The base class has already enforced the common edit rules.

**Data flow**: It receives context, name, new spec, old spec if present, and owner if present, then should perform the write; the base implementation raises NotImplementedError.

**Call relations**: MemberOwnedObjects.apply calls this once permission and freshness checks have passed.

*Call graph*: called by 1 (apply).


##### `MemberOwnedObjects._delete_owned`  (lines 578–579)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: OwnerT) -> None
```

**Purpose**: Defines the subclass hook that actually removes an owned object. The base class has already checked that deletion is allowed.

**Data flow**: It receives context, name, and owner, then should perform the deletion; the base implementation raises NotImplementedError.

**Call relations**: MemberOwnedObjects.delete calls this after ownership, admin, speaker, and generation checks.

*Call graph*: called by 1 (delete).


##### `MemberReadable.member_detail`  (lines 601–608)

```
async def member_detail(self, ext: 'ExtensionContext | None', name: str, *, member_id: UUID, admin: bool) -> MemberObject | None
```

**Purpose**: Defines the portal-facing contract for reading one object as a signed-in member outside an agent turn. Kinds that implement it opt into portal detail pages.

**Data flow**: It receives an extension context, object name, member ID, and admin flag, and should return a MemberObject or None.

**Call relations**: Portal routes can use this protocol to read member-visible object details without going through a ToolContext.


##### `MemberListable.member_page`  (lines 616–623)

```
async def member_page(self, ext: 'ExtensionContext | None', *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Defines the portal-facing contract for listing objects as a signed-in member. It extends member_detail with indexed listing behavior.

**Data flow**: It receives extension context, member ID, admin flag, and an ObjectListQuery, and should return an ObjectPage.

**Call relations**: Portal index routes can use this protocol for kinds that are safe to list outside a turn.


##### `ConversationMemberListable.member_conversation_rows`  (lines 635–643)

```
async def member_conversation_rows(self, ext: 'ExtensionContext | None', conversation_id: UUID, *, member_id: UUID, admin: bool, limit: int) -> tuple[ConversationObjectGrant, ...]
```

**Purpose**: Defines a contract for listing object grants tied to a conversation for one member. It is used when the system needs conversation-scoped object visibility information.

**Data flow**: It receives extension context, conversation ID, member ID, admin flag, and limit, and should return grant rows showing names, generations, and whether content is visible.

**Call relations**: Kinds that implement this protocol can participate in conversation object listings without sharing unrelated storage details.


##### `MemberReadableObjects.member_page`  (lines 656–669)

```
async def member_page(self, ext: 'ExtensionContext | None', *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists member-readable objects for the portal using the same visibility rule as turn-time listing. This prevents the portal and tool views from drifting apart.

**Data flow**: It asks the subclass for member rows, filters them by visibility using the given member and admin flag, converts them to ObjectRow values, and returns a paged result.

**Call relations**: This is the portal counterpart to MemberOwnedObjects.list. It calls _member_rows and then object_page.

*Call graph*: calls 2 internal fn (_member_rows, object_page); 1 external calls (__init__).


##### `MemberReadableObjects.member_detail`  (lines 671–689)

```
async def member_detail(self, ext: 'ExtensionContext | None', name: str, *, member_id: UUID, admin: bool) -> MemberObject[SpecT] | None
```

**Purpose**: Reads one portal-visible object detail for a member. It returns both the list row and the full detail so a detail page can show summary fields beside the spec.

**Data flow**: It loads member rows, finds the requested name, checks visibility, asks _member_object for the detail, and returns a MemberObject or None.

**Call relations**: Portal detail routes use this path. The turn-side _detail method delegates to the same _member_object hook so both views match.

*Call graph*: calls 2 internal fn (_member_object, _member_rows); 2 external calls (__init__, __init__).


##### `MemberReadableObjects._owned_rows`  (lines 691–692)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[OwnerT], ...]
```

**Purpose**: Connects the turn-time owned-object gate to the member-readable row source. This avoids having separate row logic for tool calls and portal calls.

**Data flow**: It takes the ToolContext, extracts the extension context and acting member ID, and returns rows from _member_rows.

**Call relations**: MemberOwnedObjects.list and _owner call this inherited hook when the kind also supports member-readable behavior.

*Call graph*: calls 1 internal fn (_member_rows).


##### `MemberReadableObjects._detail`  (lines 694–697)

```
async def _detail(self, ctx: ToolContext, name: str, owner: OwnerT) -> ObjectDetail[SpecT] | None
```

**Purpose**: Connects the turn-time detail read to the member-readable object source. This keeps privacy filtering and content elision consistent.

**Data flow**: It takes context, name, and owner, then calls _member_object with the extension context and acting member ID.

**Call relations**: MemberOwnedObjects.get calls this after the common visibility gate.

*Call graph*: calls 1 internal fn (_member_object).


##### `MemberReadableObjects._member_rows`  (lines 699–702)

```
async def _member_rows(self, ext: 'ExtensionContext | None', *, member_id: UUID | None) -> tuple[OwnedRow[OwnerT], ...]
```

**Purpose**: Defines the subclass hook for portal and tool row listing. The subclass supplies the actual rows from its storage.

**Data flow**: It receives an extension context and optional member ID, and should return owned rows; the base implementation raises NotImplementedError.

**Call relations**: member_page, member_detail, and _owned_rows all depend on this hook.

*Call graph*: called by 3 (_owned_rows, member_detail, member_page).


##### `MemberReadableObjects._member_object`  (lines 704–712)

```
async def _member_object(self, ext: 'ExtensionContext | None', name: str, owner: OwnerT, *, member_id: UUID | None) -> ObjectDetail[SpecT] | None
```

**Purpose**: Defines the subclass hook for reading one member-facing object detail. The subclass decides how to load and possibly redact the content.

**Data flow**: It receives extension context, name, owner, and optional member ID, and should return ObjectDetail or None; the base implementation raises NotImplementedError.

**Call relations**: member_detail and _detail call this so portal reads and turn-time reads share the same detail source.

*Call graph*: called by 2 (_detail, member_detail).


##### `object_registry`  (lines 747–770)

```
def object_registry(bound: tuple[BoundKind, ...]) -> dict[str, BoundKind]
```

**Purpose**: Builds and validates the registry of all object kinds for a deployment. It fails early if extensions register unsafe or conflicting kinds.

**Data flow**: It receives bound kinds, checks each kind name, checks for duplicate names, checks allowed agent-target verbs, validates each spec model, and returns a dictionary keyed by kind name.

**Call relations**: Startup code uses this as the boot gate before serving requests. It calls _validate_spec_model for the deeper schema safety checks.

*Call graph*: calls 1 internal fn (_validate_spec_model).


##### `_validate_spec_model`  (lines 773–793)

```
def _validate_spec_model(owner: str, kind: ObjectKind) -> None
```

**Purpose**: Checks that a kind's spec model is safe to store, render, and echo back. In particular, it forbids unknown fields and secret-bearing fields.

**Data flow**: It walks the spec model and nested models, checks configuration and field annotations, asks Pydantic to produce a JSON schema, and raises an error if anything is unsafe.

**Call relations**: object_registry calls this for each registered kind. It uses _reachable_models and _annotation_types to inspect nested model structures.

*Call graph*: calls 2 internal fn (_annotation_types, _reachable_models); called by 1 (object_registry).


##### `_reachable_models`  (lines 796–810)

```
def _reachable_models(model: type[BaseModel]) -> tuple[type[BaseModel], ...]
```

**Purpose**: Finds all Pydantic models nested inside a spec model. This lets validation cover not just the top-level spec but also embedded objects.

**Data flow**: It starts from one model, walks through field annotations, collects each BaseModel type once, and returns them as a tuple.

**Call relations**: _validate_spec_model calls this before checking model configuration and secret fields.

*Call graph*: calls 1 internal fn (_annotation_types); called by 1 (_validate_spec_model).


##### `_annotation_types`  (lines 813–820)

```
def _annotation_types(annotation: object) -> tuple[object, ...]
```

**Purpose**: Flattens a type annotation into the concrete pieces inside it. This helps the code see through wrappers such as optional or list-like types.

**Data flow**: It receives an annotation, reads its type arguments if any, recursively expands them, and returns all discovered parts.

**Call relations**: _reachable_models and _validate_spec_model call this while inspecting spec fields.

*Call graph*: called by 2 (_reachable_models, _validate_spec_model); 1 external calls (get_args).


##### `ObjectVerbs.tools`  (lines 890–958)

```
def tools(self) -> tuple[ToolDef, ...]
```

**Purpose**: Creates the five tool definitions exposed to the agent: object_list, object_get, object_explain, object_apply, and object_delete. These definitions describe the tools and connect each one to its handler.

**Data flow**: It reads the ObjectVerbs registry and returns ToolDef objects with names, descriptions, input models, handlers, and safety flags.

**Call relations**: The tool registry calls this to make object operations available during agent turns.

*Call graph*: 1 external calls (__init__).


##### `ObjectVerbs._list`  (lines 960–992)

```
async def _list(self, ctx: ToolContext, args: ObjectListInput) -> ToolResult
```

**Purpose**: Implements the object_list tool. It either lists registered kinds or lists instances of one kind with search, filters, sorting, and paging.

**Data flow**: It receives tool context and list input. With no kind, it returns kind descriptions; with a kind, it resolves the kind, checks any agent target, builds an ObjectListQuery, calls the store's list method, and returns JSON.

**Call relations**: This is the handler installed by ObjectVerbs.tools. It uses _resolve, _target, _bound_ctx, object_agent, and _json_result to route the request safely.

*Call graph*: calls 4 internal fn (_bound_ctx, _resolve, _target, _json_result); 2 external calls (__init__, object_agent).


##### `ObjectVerbs._get`  (lines 994–1030)

```
async def _get(self, ctx: ToolContext, args: ObjectGetInput) -> ToolResult
```

**Purpose**: Implements the object_get tool. It reads one object's spec, status, links, and timestamps.

**Data flow**: It receives context and get input, resolves the kind, checks any agent target, reads detail from the store, reads status using the observed generation, formats links and timestamps, and returns YAML text.

**Call relations**: This handler is installed by ObjectVerbs.tools. It calls the kind store's get and status methods inside the selected object-agent scope.

*Call graph*: calls 3 internal fn (_bound_ctx, _resolve, _target); 5 external calls (__init__, __init__, __init__, object_agent, safe_dump).


##### `ObjectVerbs._explain`  (lines 1032–1045)

```
async def _explain(self, ctx: ToolContext, args: ObjectExplainInput) -> ToolResult
```

**Purpose**: Implements the object_explain tool. It tells a caller how to author or mutate a specific object kind.

**Data flow**: It receives a kind name, resolves it, and returns JSON containing the kind description, guidance, allowed agent-target verbs, name rule, and JSON schema for the spec.

**Call relations**: This is the safe read-only guide used before ObjectVerbs._apply creates or updates an object.

*Call graph*: calls 2 internal fn (_resolve, _json_result).


##### `ObjectVerbs._apply`  (lines 1047–1092)

```
async def _apply(self, ctx: ToolContext, args: ObjectApplyInput) -> ToolResult
```

**Purpose**: Implements the object_apply tool for creating or updating an object from a YAML manifest. It validates the manifest before any store code runs.

**Data flow**: It parses the manifest into kind, name, and spec data; resolves the kind; checks target-agent permission; validates the object name and spec model; reads any existing object; enforces create_only and cross-agent create/update rules; calls the store's apply method; and returns a JSON result.

**Call relations**: This handler is installed by ObjectVerbs.tools. It relies on _parse_envelope, _resolve, _target, _bound_ctx, and the registered store's get and apply methods.

*Call graph*: calls 5 internal fn (_bound_ctx, _resolve, _target, _json_result, _parse_envelope); 4 external calls (__init__, __init__, validate_object_name, object_agent).


##### `ObjectVerbs._delete`  (lines 1094–1115)

```
async def _delete(self, ctx: ToolContext, args: ObjectDeleteInput) -> ToolResult
```

**Purpose**: Implements the object_delete tool. It removes one object and returns enough of the old spec to help recover from an accidental delete when the spec was visible.

**Data flow**: It resolves the kind, checks any target agent, reads the existing object, raises not-found if absent, calls the store's delete method with the observed generation, and returns JSON with the deleted spec or null.

**Call relations**: This handler is installed by ObjectVerbs.tools. It uses _resolve, _target, _bound_ctx, object_agent, and _json_result around the store's get and delete calls.

*Call graph*: calls 4 internal fn (_bound_ctx, _resolve, _target, _json_result); 2 external calls (__init__, object_agent).


##### `ObjectVerbs._resolve`  (lines 1117–1122)

```
def _resolve(self, kind: str) -> BoundKind
```

**Purpose**: Finds the registered object kind for a kind name. It gives a helpful error that lists available kinds when the name is unknown.

**Data flow**: It looks up the kind in the registry and returns the BoundKind if present. If missing, it raises UnknownKind with the registered names.

**Call relations**: _list, _get, _explain, _apply, and _delete all call this before dispatching to a store.

*Call graph*: called by 5 (_apply, _delete, _explain, _get, _list); 1 external calls (__init__).


##### `ObjectVerbs._bound_ctx`  (lines 1124–1125)

```
def _bound_ctx(self, ctx: ToolContext, bound: BoundKind) -> ToolContext
```

**Purpose**: Rebinds a tool call to the extension context that owns the selected kind. This makes the same tool flow work for core kinds and extension kinds.

**Data flow**: It receives the current ToolContext and a BoundKind, copies the context with its ext field set to the bound extension context, and returns the copy.

**Call relations**: _list, _get, _apply, and _delete call this before invoking a kind's store handlers.

*Call graph*: called by 4 (_apply, _delete, _get, _list); 1 external calls (replace).


##### `ObjectVerbs._target`  (lines 1127–1173)

```
async def _target(self, ctx: ToolContext, bound: BoundKind, name: str, verbs: frozenset[AgentTargetVerb]) -> ObjectAgent | None
```

**Purpose**: Validates and resolves an optional target agent for agent-scoped object kinds. It prevents ordinary agents, subagents, or background calls from reaching across agent boundaries.

**Data flow**: It receives context, kind binding, requested agent name, and the verb being attempted. If no name is given it returns None; otherwise it checks the kind allows targeting, reads the current and target agents from the database, enforces main-agent and live-member rules, and returns an ObjectAgent.

**Call relations**: _list, _get, _apply, and _delete call this before entering the object_agent scope for cross-agent operations.

*Call graph*: called by 4 (_apply, _delete, _get, _list); 3 external calls (__init__, select, workspace_tx).


##### `_parse_envelope`  (lines 1176–1194)

```
def _parse_envelope(manifest: str) -> tuple[str, str, Mapping[str, object]]
```

**Purpose**: Parses and checks the YAML manifest used by object_apply. It ensures the top-level document is exactly kind, name, and spec.

**Data flow**: It receives manifest text, rejects oversized input, parses YAML, checks the document is a mapping with exactly the required keys, checks kind and name are strings and spec is a mapping, then returns those three pieces.

**Call relations**: ObjectVerbs._apply calls this before resolving the kind or validating the spec model.

*Call graph*: called by 1 (_apply); 2 external calls (__init__, safe_load).


##### `_json_result`  (lines 1197–1198)

```
def _json_result(payload: Mapping[str, object]) -> ToolResult
```

**Purpose**: Wraps a JSON-serializable payload as a tool result. It is the common output helper for tools that return compact JSON.

**Data flow**: It receives a mapping, converts it to a JSON string, wraps that text in TextContent, and returns a ToolResult.

**Call relations**: ObjectVerbs._list, _explain, _apply, and _delete use this for their responses.

*Call graph*: called by 4 (_apply, _delete, _explain, _list); 3 external calls (__init__, __init__, dumps).


### `core/src/ufo/object_scope.py`

`domain_logic` · `cross-cutting during object dispatch and audited object handling`

Some parts of the system need to know which agent is responsible for an action, especially when recording or auditing object operations. Normally, the current agent comes from the broader agent scope. But object dispatch can narrow that down to one exact agent namespace, and this file provides the small mechanism that makes that possible.

It defines an ObjectAgent value, which is just an immutable pair of an agent UUID and name. It also defines a task-local variable, meaning a value that is local to the current running task rather than shared globally. That matters because many tasks may run at the same time, and one task’s selected object agent must not leak into another task.

The object_agent context manager is like putting a temporary label on a folder while you work inside it: code inside the block sees the selected object agent, and when the block ends the old label is restored. If no target is provided, it simply does nothing.

Finally, object_agent_id asks, “Which agent ID should this object operation use?” If an object-specific agent was set, it returns that ID. Otherwise it falls back to the normal current agent. Without this file, object audits could be attributed to the wrong agent or could accidentally share state across concurrent work.

#### Function details

##### `object_agent`  (lines 24–32)

```
def object_agent(target: ObjectAgent | None) -> Iterator[None]
```

**Purpose**: Temporarily selects an object-specific agent for the current task. Code run inside its block will see that agent as the target for object-related auditing, and the previous value is restored afterward.

**Data flow**: It receives either an ObjectAgent or None. If it receives None, it leaves the current task’s object-agent setting unchanged and simply runs the enclosed work. If it receives an ObjectAgent, it stores that value in the task-local slot before the enclosed work starts, then resets the slot back to its previous value when the work finishes, even if an error occurs.

**Call relations**: This function is used around a stretch of object-handling work when object dispatch has chosen a more specific agent target. It does not call other project functions itself; instead, it prepares the local context so later code, especially object_agent_id, can read the right agent identity.


##### `object_agent_id`  (lines 35–37)

```
def object_agent_id() -> UUID
```

**Purpose**: Returns the agent UUID that should be used for the current object operation. It prefers the temporary object-specific agent when one has been set, and otherwise uses the normal current agent.

**Data flow**: It reads the task-local object-agent slot. If that slot contains an ObjectAgent, it returns that object agent’s UUID. If the slot is empty, it asks the wider agent scope for the current agent and returns that agent’s UUID instead.

**Call relations**: This function is called by code that needs to stamp an object operation with the correct agent identity. When object_agent has set a temporary target, this function uses it; when no such target exists, it hands off to ufo.agent_scope.agent_current to get the ordinary current agent.

*Call graph*: 1 external calls (agent_current).


### Agent and content visibility
These files define which agents, conversations, subjects, and scheduled task details are visible to which workspace members.

### `extensions/web/ufo_ext_web/audience.py`

`domain_logic` · `request handling and admin tool execution`

The web portal needs a clear answer to a simple question: “When this person signs in, which agents may they talk to or inspect?” This file is that rulebook. It treats a member’s email address as their web identity, then stores explicit grants as small records keyed by agent and email. The main agent is special: every member can reach it without a grant, like a front desk that always answers visitors. Other agents are shown only to workspace admins or to members who were explicitly granted access.

The file also exposes three admin tools. One grants web access to the current agent, one revokes it, and one records that an admin has acknowledged opening another member’s private conversation transcript. Before changing access, the code checks that the speaker is a real workspace member, is an admin, supplied an email, and that the email belongs to an existing member. These checks prevent accidental or unauthorized sharing.

A small `WebAudience` object represents one member’s view of the portal: whether they are an admin, which agents they can see, and which agent IDs were explicitly granted. Without this file, the web portal would not have one consistent place to answer audience and transcript-access questions, and different pages could accidentally expose too much or too little.

#### Function details

##### `web_extension`  (lines 27–34)

```
def web_extension() -> ExtensionContext
```

**Purpose**: Builds the web extension’s own access object so web code can read and write the web audience records. It is used when a web surface has a surface context but still needs the extension’s private store.

**Data flow**: It takes no outside input. It creates a scoped store for the web extension and an empty credential-access object, then packages them into an extension context. The result is a ready-to-use handle for the web extension’s saved rows and transactions.

**Call relations**: This is a setup helper for code that needs the web extension’s storage. It creates the same kind of extension context that later functions expect when they open transactions or read stored audience grants.

*Call graph*: 3 external calls (__init__, __init__, __init__).


##### `_grant_key`  (lines 37–38)

```
def _grant_key(agent_id: UUID, email: str) -> str
```

**Purpose**: Creates the storage key for one access grant: one agent and one member email. It keeps email matching consistent by trimming spaces and lowercasing the address.

**Data flow**: It receives an agent ID and an email address. It normalizes the email, combines it with the audience prefix and agent ID, and returns a single text key suitable for the store.

**Call relations**: When `_grant` writes a grant and `_revoke` removes one, both use this helper so they point at the exact same storage row. That prevents mismatches caused by email capitalization or extra spaces.

*Call graph*: called by 2 (_grant, _revoke).


##### `granted_emails`  (lines 41–48)

```
async def granted_emails(store: ScopedStore) -> dict[UUID, tuple[str, ...]]
```

**Purpose**: Reads all saved web access grants and reshapes them into an admin-friendly view: each agent ID mapped to the emails granted to it. This is useful for showing the current access list.

**Data flow**: It receives a scoped store. It lists all stored keys under the audience prefix, pulls the agent ID and email out of each key, groups emails by agent, sorts each group, and returns a dictionary from agent ID to email list.

**Call relations**: This function reads the same store rows that `_grant` creates and `_revoke` deletes. It does not decide access for one visitor; instead, it gives a broader overview for administration screens or similar readers.

*Call graph*: calls 1 internal fn (list); 1 external calls (UUID).


##### `_granted_agent_ids`  (lines 51–58)

```
async def _granted_agent_ids(store: ScopedStore, email: str) -> frozenset[UUID]
```

**Purpose**: Finds which non-main agents a particular email address has been explicitly allowed to reach. It is the lookup used when building a non-admin member’s web portal view.

**Data flow**: It receives the grant store and an email address. It normalizes the email, scans all audience grant keys, keeps the agent IDs whose stored email matches, and returns those IDs as an immutable set.

**Call relations**: `web_audience` calls this after it has determined the user is not an admin. The returned agent IDs are then used to filter the full agent list down to the main agent plus explicitly granted agents.

*Call graph*: calls 1 internal fn (list); called by 1 (web_audience); 1 external calls (UUID).


##### `WebAudience.allows`  (lines 73–74)

```
def allows(self, agent_id: UUID) -> bool
```

**Purpose**: Answers whether this member’s portal view includes a particular agent. It checks visibility, not whether the access came from an explicit grant.

**Data flow**: It receives an agent ID and looks through the `agents` already stored on the `WebAudience` object. If one of those agents has the same ID, it returns true; otherwise it returns false. It does not change anything.

**Call relations**: This method is used after `web_audience` has built the member’s allowed agent list. Callers can ask this simple yes-or-no question instead of repeating the filtering rules themselves.


##### `WebAudience.granted`  (lines 76–77)

```
def granted(self, agent_id: UUID) -> bool
```

**Purpose**: Answers whether a read that requires admin-level or explicit grant permission is allowed for an agent. Admins automatically pass; non-admins pass only for agent IDs explicitly granted to them.

**Data flow**: It receives an agent ID and reads two fields from the `WebAudience` object: whether the member is an admin and which agent IDs were granted. It returns true for admins or for a matching granted ID, and false otherwise.

**Call relations**: This method supports stricter checks than `allows`. For example, the main agent may be visible to everyone, but some panels should rely on explicit grant or admin status rather than simple visibility.


##### `web_audience`  (lines 80–97)

```
async def web_audience(surface: SurfaceContext, extension: ExtensionContext, email: str) -> WebAudience
```

**Purpose**: Builds the complete web audience view for one signed-in email address. It decides whether the person is an admin and which agents the portal should show them.

**Data flow**: It receives the current surface context, the web extension context, and an email address. It normalizes the email, reads the workspace seat snapshot to check admin status, asks the surface for all agents, and then returns a `WebAudience`: admins get every agent, while non-admins get the main agent plus explicitly granted agents.

**Call relations**: This is the central read path for portal access. It opens a transaction to read workspace membership, asks the surface for agent summaries, and, for non-admins, hands off to `_granted_agent_ids` to apply stored web grants.

*Call graph*: calls 3 internal fn (transaction, list_agents, _granted_agent_ids); 2 external calls (__init__, __init__).


##### `_refusal`  (lines 108–109)

```
def _refusal(text: str) -> ToolResult
```

**Purpose**: Creates a standard error result for a tool command that is not allowed or cannot be completed. It turns a plain explanation into the tool response format.

**Data flow**: It receives a text message. It wraps that message as text content, marks the tool result as an error, and returns it. It does not read or write stored data.

**Call relations**: `_gate` and `_read_private_transcript` call this whenever they need to stop with a clear user-facing reason, such as missing admin rights or an invalid transcript request.

*Call graph*: called by 2 (_gate, _read_private_transcript); 2 external calls (__init__, __init__).


##### `_gate`  (lines 112–130)

```
async def _gate(ctx: ToolContext, extension: ExtensionContext, args: WebAccessInput) -> ToolResult | None
```

**Purpose**: Performs the safety checks shared by the grant and revoke tools. It makes sure the speaker is a real member, is an admin, supplied an email, and named someone who already belongs to the workspace.

**Data flow**: It receives the tool context, the extension context, and the parsed email input. It checks the speaking member ID, asks whether the speaker is an admin, validates that the email is not blank, then reads the workspace seat snapshot to confirm the email exists. It returns `None` if everything is allowed, or an error tool result if something fails.

**Call relations**: `_grant` and `_revoke` call this before touching the audience store. When a check fails, `_gate` uses `_refusal` to produce the response and prevents the later write or delete from happening.

*Call graph*: calls 3 internal fn (transaction, speaker_is_admin, _refusal); called by 2 (_grant, _revoke); 1 external calls (__init__).


##### `_grant`  (lines 133–155)

```
async def _grant(ctx: ToolContext, args: WebAccessInput) -> ToolResult
```

**Purpose**: Implements the `grant_web_access` tool. It lets an admin give a workspace member access to the current agent in the web portal, unless the current agent is the main agent, which everyone can already reach.

**Data flow**: It receives the tool context and the grant input. It first verifies that an extension context is present, then runs `_gate`. If the request is refused, it returns that refusal. If the agent is the main agent, it returns an explanatory success message without writing a grant. Otherwise it writes a grant row keyed by the current agent and email, then returns a confirmation message.

**Call relations**: This function is called as the handler for the `grant_web_access` tool. It relies on `_gate` for permission checks, `_grant_key` for the exact storage key, and the tool context for the current agent, speaker, and store.

*Call graph*: calls 3 internal fn (agent_is_main, _gate, _grant_key); 2 external calls (__init__, __init__).


##### `_revoke`  (lines 158–180)

```
async def _revoke(ctx: ToolContext, args: WebAccessInput) -> ToolResult
```

**Purpose**: Implements the `revoke_web_access` tool. It lets an admin remove a member’s explicit web access grant for the current agent.

**Data flow**: It receives the tool context and the revoke input. It confirms an extension context exists, runs `_gate`, and stops if the request is refused. It deletes the grant row for the current agent and email. If the agent is the main agent, it explains that the member still reaches it because everyone does; otherwise it confirms the member no longer reaches this agent.

**Call relations**: This function is called as the handler for the `revoke_web_access` tool. Like `_grant`, it uses `_gate` before changing storage and `_grant_key` so it deletes the same key format that grants use.

*Call graph*: calls 3 internal fn (agent_is_main, _gate, _grant_key); 2 external calls (__init__, __init__).


##### `_read_private_transcript`  (lines 194–221)

```
async def _read_private_transcript(ctx: ToolContext, args: PrivateTranscriptInput) -> ToolResult
```

**Purpose**: Implements the `read_private_transcript` tool. It records that a workspace admin acknowledged opening another member’s private conversation transcript in the web portal.

**Data flow**: It receives the tool context and a conversation ID. It checks that there is a speaking member and that the speaker is an admin. It then asks the surface transcript system to record access for this workspace, conversation, agent, and admin member. If there is nothing valid to record, it returns an error explanation; otherwise it returns a confirmation naming whose private conversation was opened.

**Call relations**: This function is the handler for the `read_private_transcript` tool. It uses `_refusal` for failed checks and hands the real recording step to `record_transcript_access`, which is the shared surface-level function that writes the acknowledgement the portal later relies on.

*Call graph*: calls 2 internal fn (speaker_is_admin, _refusal); 3 external calls (__init__, __init__, record_transcript_access).


### `core/src/ufo/agent_scope.py`

`domain_logic` · `cross-cutting`

Some parts of the system need to know not just “what workspace am I in?” but also “which agent is acting here?” This file provides that ambient identity: a small piece of context that nearby code can read without passing the agent ID through every function call. Think of it like a temporary name badge worn while a task is running.

The file defines an AgentScope, which is just a pair of IDs: the current workspace ID and the current agent ID. It stores that pair in a ContextVar, which is Python’s way of keeping per-task state safely separated so one async task does not accidentally read another task’s identity.

The agent(...) context manager is used around code that should run as a particular agent. It records the current workspace at the moment the agent is bound. It also refuses to switch to a different agent while one is already bound, which prevents confusing nested identity changes.

The agent_current() function is the safety check and lookup point. Code that needs the current agent calls it. If no agent was bound, it raises AgentUnbound with a helpful message. If the workspace has changed since the agent was bound, it raises an error too, because an agent identity must not leak across workspace boundaries.

#### Function details

##### `agent`  (lines 28–38)

```
def agent(agent_id: UUID) -> Iterator[AgentScope]
```

**Purpose**: This context manager temporarily marks the current code as running under one specific agent in the current workspace. It is used to create a clear boundary around agent-owned work, so later code can safely ask “who is the current agent?”

**Data flow**: It takes an agent ID as input and reads the current workspace ID using ws_current(). It combines those into an AgentScope, stores that scope in task-local context, then yields it to the code inside the with block. When the block finishes, even if it fails, it restores the previous context so the agent identity does not leak into later work.

**Call relations**: When code enters an agent boundary, this function asks the workspace layer for the current workspace and builds an AgentScope for that workspace-agent pair. It does not hand off to other local functions, but it prepares the context that agent_current later reads. If a different agent is already bound, it stops immediately rather than allowing an in-place identity switch.

*Call graph*: 2 external calls (__init__, ws_current).


##### `agent_current`  (lines 41–48)

```
def agent_current() -> AgentScope
```

**Purpose**: This function returns the agent identity currently bound to the running task. It is the guardrail used by agent-scoped features to make sure they are being called inside a valid agent boundary.

**Data flow**: It reads the stored AgentScope from the task-local context. If there is no stored scope, it raises AgentUnbound to explain that the caller must wrap the work in with agent(agent_id). If there is a scope, it checks the current workspace from ws_current(); if the workspace still matches, it returns the scope, otherwise it raises an error because the agent identity no longer belongs to the active workspace.

**Call relations**: Code that needs to know the current agent calls this after some outer code has used agent to bind one. This function depends on the workspace layer to confirm the active workspace and creates an AgentUnbound error when no agent boundary exists. In that way, it turns hidden context into a deliberate, checked permission boundary.

*Call graph*: 2 external calls (__init__, ws_current).


### `core/src/ufo/audience.py`

`domain_logic` · `cross-cutting`

A conversation can be shared with everyone in a workspace, tied to one member, tied to a room, or tied to a room that includes people from another organization. This file gives those cases clear string labels, such as a shared audience, a member audience, or a room audience. Think of the audience label like the name on an envelope: before any content is read or carried forward, the system needs to know exactly who that envelope is addressed to.

The important job here is safety. The code does not accept arbitrary strings as audiences. It builds audience names in one consistent format and parses incoming names to make sure they match that format. This prevents a malformed or misleading label from slipping through and changing who can see what.

The file also contains the rules for what an audience may read. Normal shared, member, and internal room audiences can read the workspace-shared subject plus their own subject. A foreign room audience is stricter: it can read only itself, so internal workspace facts are not accidentally brought into a channel shared with another organization.

Finally, it defines how an audience can be narrowed. Moving from a broad audience to a more specific one is allowed in limited cases, but changing to an unrelated audience raises an error.

#### Function details

##### `conversation_audience`  (lines 14–15)

```
def conversation_audience(member_id: UUID | None) -> Audience
```

**Purpose**: Creates the audience label for a conversation that is either workspace-shared or tied to one specific member. Use it when the system knows a member id, or knows the conversation is not member-specific.

**Data flow**: It receives a member id, or no member id at all. If there is no member id, it returns the shared audience label. If there is a member id, it turns that id into the standard member-audience string and returns it as an Audience value.

**Call relations**: Other code in this file relies on this as the single trusted way to spell member and shared audiences. `parse_audience` uses it to confirm that an incoming member string is exactly canonical, and `readable_audiences` uses it when building the list of audiences a member may read.

*Call graph*: called by 2 (parse_audience, readable_audiences).


##### `room_audience`  (lines 18–19)

```
def room_audience(surface: str, room: str) -> Audience
```

**Purpose**: Creates the audience label for an internal room. This marks content as belonging to a named room on a named surface, such as a chat platform or other place where rooms exist.

**Data flow**: It receives a surface name and a room name. It passes them to the shared room-label builder with the internal-room prefix, and returns the finished Audience label.

**Call relations**: This is the public helper for internal room audiences. It delegates the common validation and formatting work to `_room_audience`, and `parse_audience` uses it to check whether an incoming internal room label is correctly written.

*Call graph*: calls 1 internal fn (_room_audience); called by 1 (parse_audience).


##### `foreign_room_audience`  (lines 22–23)

```
def foreign_room_audience(surface: str, room: str) -> Audience
```

**Purpose**: Creates the audience label for a room that is externally shared with another organization. This special label matters because foreign rooms get stricter reading rules.

**Data flow**: It receives a surface name and a room name. It sends them to the shared room-label builder with the foreign-room prefix, and returns the resulting Audience label.

**Call relations**: This mirrors `room_audience`, but for externally shared rooms. It relies on `_room_audience` for validation and formatting, and `parse_audience` calls it when checking that a foreign room audience string is valid.

*Call graph*: calls 1 internal fn (_room_audience); called by 1 (parse_audience).


##### `_room_audience`  (lines 26–29)

```
def _room_audience(prefix: str, surface: str, room: str) -> Audience
```

**Purpose**: Builds a room-style audience label after checking that its pieces are safe to combine. It exists so internal rooms and foreign rooms follow the same formatting rules.

**Data flow**: It receives a prefix, a surface, and a room. It rejects empty surface or room names, and also rejects names containing a colon because colons are used as separators in the audience string. If the pieces are safe, it joins them into one Audience label.

**Call relations**: `room_audience` and `foreign_room_audience` both call this helper. It is the shared checkpoint that keeps both types of room audience from being created with ambiguous or broken names.

*Call graph*: called by 2 (foreign_room_audience, room_audience).


##### `parse_audience`  (lines 32–55)

```
def parse_audience(value: str) -> Audience
```

**Purpose**: Checks that a raw string is a valid audience label and returns it as an Audience. This is the main gatekeeper for audience strings that may have come from storage, input, or another part of the system.

**Data flow**: It receives a string. It first accepts the exact shared audience. Otherwise, it splits the string into a prefix and the rest. For member audiences, it verifies that the rest is a valid UUID, meaning a standard unique identifier, and that the full string matches the canonical member format. For room and foreign-room audiences, it verifies that the surface and room are present and rebuilds the expected label to make sure the original is exact. If anything is wrong, it raises a ValueError instead of returning a questionable audience.

**Call relations**: Several safety-sensitive helpers call this before making decisions. `audience_member` uses it before extracting a member id, `audience_subjects` uses it before deciding what subjects can be read, and `narrow_audience` uses it before comparing old and new audiences. It calls the audience-building helpers so validation and construction stay consistent.

*Call graph*: calls 3 internal fn (conversation_audience, foreign_room_audience, room_audience); called by 3 (audience_member, audience_subjects, narrow_audience); 1 external calls (UUID).


##### `readable_audiences`  (lines 58–63)

```
def readable_audiences(member_id: UUID) -> tuple[Audience, ...]
```

**Purpose**: Returns the conversation audiences whose content a given workspace member is allowed to read. In this model, that means the workspace-shared audience and that member’s own audience.

**Data flow**: It receives a member id. It combines the shared audience with the member-specific audience made from that id, and returns both as a tuple.

**Call relations**: This function uses `conversation_audience` to create the member-specific label. It is meant to be the common answer for member-facing reads, such as listing conversations or objects attached to conversations.

*Call graph*: calls 1 internal fn (conversation_audience).


##### `audience_member`  (lines 66–70)

```
def audience_member(audience: Audience) -> UUID | None
```

**Purpose**: Extracts the member id from a member-specific audience, if the audience is actually for a member. If the audience is shared, room-based, or foreign-room-based, it reports that there is no member id.

**Data flow**: It receives an Audience value. It first validates it with `parse_audience`. If the validated label does not start with the member prefix, it returns None. If it is a member audience, it removes the prefix, turns the remaining text into a UUID, and returns that UUID.

**Call relations**: This function depends on `parse_audience` so it never extracts an id from a malformed label. It is useful when later code needs to know whether a conversation belongs to a particular member rather than to a shared or room audience.

*Call graph*: calls 1 internal fn (parse_audience); 1 external calls (UUID).


##### `audience_subjects`  (lines 73–80)

```
def audience_subjects(audience: Audience) -> frozenset[str]
```

**Purpose**: Returns the subject labels that a conversation with this audience is allowed to read. A subject is the lower-level disclosure bucket that content is attached to.

**Data flow**: It receives an Audience value and validates it with `parse_audience`. If it is a foreign-room audience, it returns only that audience itself as the readable subject. For every other valid audience, it returns both the workspace-shared subject and the audience’s own subject.

**Call relations**: This function is where an important disclosure rule is enforced after parsing. It relies on `parse_audience` to confirm the label first, then applies the special foreign-room rule so externally shared channels do not pull in internal workspace-shared information.

*Call graph*: calls 1 internal fn (parse_audience).


##### `narrow_audience`  (lines 83–98)

```
def narrow_audience(current: Audience, requested: Audience) -> Audience
```

**Purpose**: Decides whether a conversation audience may safely become more specific, and returns the audience that should continue forward. It prevents a conversation from silently changing to an unrelated audience.

**Data flow**: It receives the current audience and a requested audience. It validates both. If they are the same, or the requested audience is the shared audience, it keeps the current one. If the current audience is shared, it allows moving to the requested audience. For room and foreign-room audiences that refer to the same surface and room, it chooses the stricter foreign audience when either side is foreign. If the requested change does not fit these safe patterns, it raises a ValueError.

**Call relations**: This function calls `parse_audience` first so all comparisons are made on valid audience labels. It is the rulekeeper for audience transitions, such as when a broader conversation context is narrowed to a specific member or room without allowing an accidental jump to a different audience.

*Call graph*: calls 1 internal fn (parse_audience); 1 external calls (partition).


### `core/src/ufo/subjects.py`

`data_model` · `cross-cutting`

This file is about visibility: when the system stores or discusses some content, it needs a simple way to describe who the content is meant for. It uses the word “subject” for that audience label. There are two basic forms here. The shared subject is the literal text "shared", meaning the content is readable by every member of the workspace. A member subject starts with "member:" followed by that member’s unique ID, meaning the content is tied to one specific person.

The file is deliberately tiny because consistency matters more than complexity here. If different parts of the system invented their own strings for “shared” or for “this member,” permissions and audience checks could silently stop matching. This file acts like a label maker: everyone uses the same sticker format.

One important detail is that not every audience is considered “shared.” The helper only treats the exact subject "shared" as workspace-wide. Other things, such as rooms or externally shared channels, are not treated as shared here because the system cannot prove normal workspace membership for them in the same way.

#### Function details

##### `member_subject`  (lines 9–10)

```
def member_subject(member_id: UUID) -> str
```

**Purpose**: Builds the standard subject label for one workspace member. Someone would use this when they need to mark content as belonging or being visible to a particular member.

**Data flow**: It takes a member’s UUID, which is a unique identifier, and turns it into text by putting "member:" in front of it. The output is a string like "member:<id>" that other parts of the system can compare and store.

**Call relations**: This function is a shared formatting helper. When another part of the system needs a member-specific audience label, it should call this instead of building the text by hand, so all member subjects follow the same pattern.


##### `subject_shared`  (lines 13–18)

```
def subject_shared(subject: str) -> bool
```

**Purpose**: Answers whether a subject label means “readable by every member of the workspace.” It is used when the system needs to tell workspace-wide visibility apart from member-specific visibility.

**Data flow**: It takes a subject string and compares it with the one official shared label, "shared". It returns true only for that exact value, and false for member labels or any other audience-like string.

**Call relations**: This function is the companion check to the shared subject constant. Other code can call it before treating content as broadly visible, and it deliberately refuses to treat rooms or external channels as shared unless they use the exact shared subject label.


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/visibility.py`

`domain_logic` · `request handling`

Scheduled tasks can report their results in different places. Some report into a shared workspace conversation, where everyone can read the replies. Others report into a private conversation for one member. This file contains the rule that connects those reporting places to content visibility.

The main idea is simple: a task is as private as the place where it reports. If the task reports to a shared audience, then every member may read its content. If it reports to one member's own conversation, only that member should read it. This matters because the task prompt and description may contain sensitive instructions or personal context.

The file also covers two edge cases. First, if the caller is not tied to any member, they cannot read private task content; they only get access to tasks aimed at the shared workspace. Second, a task may have no creator recorded. In that case, the code does not treat it as public by mistake. It still looks at the reporting audience first.

As a final fallback, if the task is not shared and is not aimed directly at the current member, the creator of the task may read it. This lets a member see a task they created, while keeping it hidden from unrelated members.

#### Function details

##### `task_content_visible`  (lines 7–20)

```
def task_content_visible(listed: ListedTask, member_id: UUID | None) -> bool
```

**Purpose**: This function answers one yes-or-no question: may this member read the scheduled task's prompt and description? It is used to protect task content based on where the task reports and, if needed, who created it.

**Data flow**: It receives a listed scheduled task and an optional member ID. It first checks whether the task's audience is shared with the whole workspace; if so, it returns true. If there is no current member, it returns false for anything private. If there is a member, it checks whether the task reports directly to that member, and then whether that member created the task. The output is a boolean: true means the content may be shown, false means it should stay hidden.

**Call relations**: When making its decision, it asks `subject_shared` whether the task's audience is workspace-wide. For member-specific checks, it uses `member_subject` to build the audience value that represents the current member and compares that with the task's audience. These helper calls let this function focus on the visibility rule instead of knowing the exact shape of subject identifiers.

*Call graph*: 2 external calls (member_subject, subject_shared).

## 📊 State Registers Touched

- `reg-onboarding-claims` — Temporary signup, email-verification, invitation, and workspace-claim records used while a user joins.
- `reg-workspace-membership` — The roster of workspaces, members, admins, seats, and which people belong where.
- `reg-auth-tokens` — Signed passes that prove who a caller is or allow short-lived access to protected routes and links.
- `reg-agent-directory` — The saved assistants in each workspace and their settings, such as model behavior, sandbox size, and internet access.
- `reg-object-catalog` — The shared catalog of manageable workspace object types and the rules for who may view or change them.
- `reg-credential-vault` — Encrypted workspace secrets and short-lived brokered credentials used without exposing raw secrets to tools.
- `reg-connection-grants` — Connected third-party accounts and permissions saying which agents may use which external accounts.
- `reg-surface-installations` — Mappings from outside entry points like Slack, web, terminal, and hosted surfaces into workspaces, members, and agents.
- `reg-conversation-transcript` — The durable history of conversations, messages, speakers, titles, context, and results.
- `reg-memory-store` — Saved facts, memory pages, confidence, provenance, and audience labels that let the assistant remember useful context.
- `reg-accounting-ledger` — Usage, cost, spend limits, prepaid balances, billing exports, and price versions for workspace spending.
- `reg-audit-access-log` — Durable audit records such as transcript-access events and security-relevant reads or administrative actions.
- `reg-execution-scope-context` — Per-request, per-job, and per-turn scoped context carrying the active workspace, member, agent, turn, permissions, credentials, billing, and service handles through core code.
- `reg-source-access-grants` — Durable permissions mapping agents to the synced sources they are allowed to read or search, separate from third-party account connection grants.

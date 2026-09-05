# Built-in workspace and host object kinds  `stage-13.1.1`

This stage is shared behind-the-scenes support for the workspace. It defines several built-in object kinds, which are standard shapes the system uses to show important workspace information safely. These objects are mostly read-only, meaning callers can inspect them but cannot freely change them.

The conversation object gives tools, pages, artifacts, and scheduled jobs a safe way to refer back to earlier chats for the selected agent. It does not allow anyone to create or edit conversations. The credential slot object shows which extension-defined secret slots exist and whether they are filled, but never reveals the secret value; admins can only clear a stored value. The member object shows who belongs to the workspace, who is an admin, and who has an active seat. It also provides the controlled path for admins to add members. The surface object lists available chat entry points and whether they are connected. Finally, the workspace object gives a read-only summary of the workspace and its people. Together, these files act like labeled windows into the workspace, with only a few locked controls where change is allowed.

## Files in this stage

### Conversation provenance
Read-only conversation objects let other workspace artifacts and tasks refer back to their originating chats without allowing conversation mutation.

### `core/src/ufo/host/kinds/conversations.py`

`domain_logic` · `request handling`

A conversation in this system is like the label on a saved chat thread: it records where the chat happened, who it was visible to, when it was created or updated, and, when allowed, the text transcript. This file makes those saved conversations available through the project’s object system, so other things can say “this artifact was created in that conversation” or “this task reports to that conversation.”

The important rule is that conversations are not authored here. They are created by chat “surfaces” such as the portal, Slack, or an extension. This file only reads them. Any attempt to apply changes or delete one is rejected.

Most reads are filtered by audience. In plain terms, the caller only sees conversations whose visibility matches the subjects they are allowed to read. There is one careful exception: a workspace admin speaking in a normal local conversation may list or get metadata about another member’s private conversation, but only as metadata. The transcript is not exposed, and this widening is disabled for foreign/shared audiences.

For the web portal, the file also builds member-facing conversation lists: “mine” conversations plus some “others” conversations, with titles, speakers, surfaces, and last activity times. For status, it can turn a visible transcript into a plain text file in the workspace if it is small enough.

#### Function details

##### `ConversationObjects.list`  (lines 83–90)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists conversations for the selected agent that the current tool context is allowed to see. If the caller explicitly asks for private rows and is a qualifying admin, it also includes metadata-only rows for other members’ private conversations.

**Data flow**: It starts with the caller’s readable subjects from the tool context and asks the database for matching conversation rows. Each row is turned into a simple object-list entry. If the query contains `private: true` and the caller passes the admin widening check, it also reads private conversations outside the caller’s normal subjects and marks those rows as private. The combined rows are then passed through the standard paging and filtering helper, which returns the final page.

**Call relations**: This is the main list path for the conversation object. It relies on `_rows` for normally visible conversations, `_widens_for_admin` and `_private_rows` for the special admin-only metadata case, `_row` to shape each database row for display, and `object_page` to cut and order the final result.

*Call graph*: calls 4 internal fn (_private_rows, _rows, _widens_for_admin, _row); 1 external calls (object_page).


##### `ConversationObjects.get`  (lines 92–96)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ConversationSpec] | None
```

**Purpose**: Fetches one conversation by its object name, which is expected to be the conversation’s UUID string. It returns the conversation’s detailed metadata if the caller may see it.

**Data flow**: It receives a context and a name. First it tries to find a normally visible row using the caller’s readable subjects. If none is found, it tries the admin-only private lookup. If a row is found, it converts the row into a detail object containing the conversation spec, timestamps, and agent link; otherwise it returns nothing.

**Call relations**: This is the single-object read path. It delegates visibility and UUID parsing to `_find`, tries `_private_find` only as a fallback, and uses `_detail` to produce the object detail returned to the caller.

*Call graph*: calls 3 internal fn (_find, _private_find, _detail).


##### `ConversationObjects.member_page`  (lines 98–139)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Builds the conversation list shown to a signed-in member in the portal. It shows that member’s own conversations alongside a limited number of readable conversations involving others, without giving admins a broad view of every private chat.

**Data flow**: It reads the current workspace and selected agent, then asks the conversation directory for two groups: conversations where the member participates and conversations readable through others. It applies fixed limits to both groups, optionally narrows to portal-capable surfaces if the query asks for that, skips entries without titles, turns each remaining entry into a portal-friendly row, and finally pages the result.

**Call relations**: This is the portal listing path, separate from the tool-context listing path. It constructs a `ConversationDirectory`, uses `object_agent_id` and `ws_current` to stay within the current agent and workspace, sends each listed entry through `_member_row`, and hands the accumulated rows to `object_page`.

*Call graph*: calls 1 internal fn (_member_row); 4 external calls (__init__, object_agent_id, object_page, ws_current).


##### `ConversationObjects.member_detail`  (lines 141–156)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[ConversationSpec] | None
```

**Purpose**: Returns one conversation detail for a signed-in portal member outside an active turn. It only uses the visibility carried by that member’s own conversation audience, so admins do not automatically see other private conversations here.

**Data flow**: It takes the member id and conversation name. It builds the subjects that this member’s own conversation would be allowed to read, then searches for a matching conversation row. If found, it returns both a list-style row and a detail object; if not, it returns nothing.

**Call relations**: This is the portal single-conversation read path. It computes member visibility through `conversation_audience` and `audience_subjects`, reuses `_find` for the database lookup, then packages `_row` and `_detail` together inside a `MemberObject`.

*Call graph*: calls 3 internal fn (_find, _detail, _row); 3 external calls (__init__, audience_subjects, conversation_audience).


##### `ConversationObjects.status`  (lines 158–176)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports transcript status for a visible conversation and, when possible, writes the transcript text into the caller’s workspace. This gives tools a safe way to inspect the conversation text without changing the conversation object.

**Data flow**: It receives a conversation name and first finds a row visible to the caller. If none exists, it returns nothing. It then reads and formats the transcript, checks that the conversation is still visible and unchanged in the important audience fields, joins the transcript lines into bytes, and writes a text file only if there is content and it is under the materialization size limit. It returns message count, byte size, and the workspace path if a file was written.

**Call relations**: This is the transcript materialization path. It uses `_find` to locate the row, `_exchange` to read transcript text, and `_unchanged_visible` as a safety check before exposing content. If the row is no longer visible, it raises `UnknownObject` instead of returning stale or overexposed data.

*Call graph*: calls 3 internal fn (_exchange, _find, _unchanged_visible); 1 external calls (__init__).


##### `ConversationObjects.apply`  (lines 178–187)

```
async def apply(self, ctx: ToolContext, name: str, spec: ConversationSpec, old: ConversationSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to create or update a conversation through the object API. Conversations belong to chat surfaces, not to this read-only object interface.

**Data flow**: It receives the desired conversation spec and related update information, but does not inspect or save it. It immediately raises a “verb not supported” error explaining that conversations are surface-made.

**Call relations**: This is called when the general object system tries to apply a create or update operation to a conversation. It deliberately hands off only to `VerbNotSupported`, enforcing the file’s read-only contract.

*Call graph*: 1 external calls (__init__).


##### `ConversationObjects.delete`  (lines 189–196)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to delete a conversation through the object API. Conversation lifetime is controlled elsewhere, such as by retention rules.

**Data flow**: It receives the context, name, and optional generation check, but does not read or modify storage. It immediately raises a “verb not supported” error.

**Call relations**: This is called by the object system for delete requests. Like `apply`, it exists to make the refusal explicit and consistent.

*Call graph*: 1 external calls (__init__).


##### `ConversationObjects._exchange`  (lines 198–218)

```
async def _exchange(self, ctx: ToolContext, conversation_id: UUID) -> tuple[str, ...]
```

**Purpose**: Reads a conversation transcript from blob storage and turns it into plain text lines such as `user: hello`. It hides missing transcripts by treating them as empty, but treats unreadable transcript data as an error.

**Data flow**: It receives a tool context and conversation id. It builds the transcript blob key, fetches the blob, decodes it, then walks through the transcript messages. String content is used directly; structured content is reduced to text blocks only. Non-empty text becomes role-prefixed lines, and the function returns those lines as a tuple.

**Call relations**: This helper is used by `ConversationObjects.status` when status needs transcript text. It depends on `transcript_key` to locate the blob and `decode` to interpret the stored transcript format.

*Call graph*: called by 1 (status); 2 external calls (decode, transcript_key).


##### `ConversationObjects._unchanged_visible`  (lines 220–233)

```
async def _unchanged_visible(self, subjects: frozenset[str], row: sa.Row) -> bool
```

**Purpose**: Checks that a conversation row is still visible to the same subjects and still has the same audience before transcript text is exposed. This is a safety check against racing changes.

**Data flow**: It receives the caller’s readable subjects and a row that was found earlier. Inside a workspace database transaction, it asks whether a row still exists with the same id, the same audience, and an audience included in the caller’s subjects. It returns true or false.

**Call relations**: This is called by `ConversationObjects.status` after transcript loading and before writing transcript content. It builds on `_visible` and standard database existence checks to avoid using a row that became invisible or changed during the operation.

*Call graph*: calls 1 internal fn (_visible); called by 1 (status); 3 external calls (exists, select, workspace_tx).


##### `ConversationObjects._find`  (lines 235–241)

```
async def _find(self, subjects: frozenset[str], name: str) -> sa.Row | None
```

**Purpose**: Finds one normally visible conversation by UUID name. It also rejects names that are not valid UUIDs.

**Data flow**: It receives readable subjects and a name string. It tries to parse the name as a UUID; if parsing fails, it returns nothing. If parsing succeeds, it asks `_rows` for matching visible rows and returns the first row if present.

**Call relations**: This helper is shared by `get`, `member_detail`, and `status`. It keeps UUID parsing and normal visibility lookup in one place, while `_rows` does the database query.

*Call graph*: calls 1 internal fn (_rows); called by 3 (get, member_detail, status); 1 external calls (UUID).


##### `ConversationObjects._rows`  (lines 243–250)

```
async def _rows(self, subjects: frozenset[str], *, conversation_id: UUID | None) -> tuple[sa.Row, ...]
```

**Purpose**: Reads conversation rows for the selected agent that match the caller’s visible subjects. It can return all visible rows or just one specific conversation id.

**Data flow**: It starts with a visibility query built from the given subjects. If a conversation id was provided, it adds that id as another condition. It then opens a workspace database transaction, executes the query, and returns the resulting rows as a tuple.

**Call relations**: This is the normal database read helper for conversations. `list` uses it to gather all visible rows, while `_find` uses it to search for one row. It depends on `_visible` to build the shared visibility rules.

*Call graph*: calls 1 internal fn (_visible); called by 2 (_find, list); 1 external calls (workspace_tx).


##### `ConversationObjects._widens_for_admin`  (lines 252–255)

```
async def _widens_for_admin(self, ctx: ToolContext) -> bool
```

**Purpose**: Decides whether the current tool call may widen its view to include metadata about other members’ private conversations. It allows this only for a local, non-foreign audience and only when the speaker is a workspace admin.

**Data flow**: It reads the current audience from the tool context. If the audience is marked as foreign, it immediately returns false. Otherwise it asks the context whether the speaker is an admin and returns that answer.

**Call relations**: This helper protects the special private metadata path. `list` uses it before adding private rows, and `_private_find` uses it before looking up a private conversation by id.

*Call graph*: calls 1 internal fn (speaker_is_admin); called by 2 (_private_find, list).


##### `ConversationObjects._private_find`  (lines 257–265)

```
async def _private_find(self, ctx: ToolContext, name: str) -> sa.Row | None
```

**Purpose**: Finds one private conversation metadata row for an admin, when admin widening is allowed. It does not expose transcript content.

**Data flow**: It first checks whether the context qualifies for admin widening. If not, it returns nothing. Then it parses the name as a UUID; invalid names return nothing. Finally, it asks `_private_rows` for a matching row and returns the first result if there is one.

**Call relations**: This helper is used only as a fallback by `get` after the normal visible lookup fails. It relies on `_widens_for_admin` for permission and `_private_rows` for the database read.

*Call graph*: calls 2 internal fn (_private_rows, _widens_for_admin); called by 1 (get); 1 external calls (UUID).


##### `ConversationObjects._private_rows`  (lines 267–277)

```
async def _private_rows(self, ctx: ToolContext, *, conversation_id: UUID | None) -> tuple[sa.Row, ...]
```

**Purpose**: Reads private conversation metadata rows for the selected agent that are outside the caller’s normal readable subjects. This supports the limited admin view of other members’ private conversations.

**Data flow**: It builds a query for conversations belonging to the current agent whose audience looks like a member-private subject and is not among the caller’s readable subjects. If a conversation id is supplied, it narrows to that id. It executes the query inside a workspace database transaction and returns the rows.

**Call relations**: This is the database helper for the admin-only private metadata feature. It is called by `list` when the private filter is explicitly set and by `_private_find` when `get` falls back to the admin path. It starts from `_agent_conversations` so it stays scoped to the current workspace and agent.

*Call graph*: calls 1 internal fn (_agent_conversations); called by 2 (_private_find, list); 1 external calls (workspace_tx).


##### `_agent_conversations`  (lines 280–301)

```
def _agent_conversations() -> sa.Select
```

**Purpose**: Builds the base database query for conversations belonging to the current workspace and selected agent. Other queries add visibility or privacy filters on top of this base.

**Data flow**: It reads the current workspace id and selected agent id. It creates a database select that joins conversations to their agent, includes key conversation fields and the agent’s current or archived name, and restricts results to the current workspace and agent.

**Call relations**: This is the foundation for conversation database reads in this file. `_visible` uses it for normal readable conversations, and `_private_rows` uses it for the admin-only private metadata path.

*Call graph*: called by 2 (_private_rows, _visible); 3 external calls (select, object_agent_id, ws_current).


##### `_visible`  (lines 304–305)

```
def _visible(subjects: frozenset[str]) -> sa.Select
```

**Purpose**: Adds the normal audience rule to the base conversation query. A conversation is visible when its audience is one of the subjects the caller is allowed to read.

**Data flow**: It receives a set of readable subject strings. It starts from `_agent_conversations` and adds a condition that the conversation audience must be inside that set. The result is a database query, not rows yet.

**Call relations**: This query builder is used by `_rows` to fetch visible conversations and by `_unchanged_visible` to re-check visibility before exposing transcript text.

*Call graph*: calls 1 internal fn (_agent_conversations); called by 2 (_rows, _unchanged_visible).


##### `_member_row`  (lines 308–326)

```
def _member_row(entry: ListedConversation, *, mine: bool) -> ObjectRow
```

**Purpose**: Turns a directory-listed conversation into the row shape used by the portal’s conversation list. It includes human-facing fields such as title, whether it is mine, speaker, surface, portal support, and last activity time.

**Data flow**: It receives a listed conversation entry and a `mine` flag. It collects speaker names or emails, chooses the last turn time or creation time as the activity stamp, computes whether the surface is portal-capable, and returns an object row with those fields.

**Call relations**: This formatting helper is called by `ConversationObjects.member_page` for each conversation returned by the conversation directory. It hands back `ObjectRow` values that can be paged and displayed.

*Call graph*: called by 1 (member_page); 1 external calls (__init__).


##### `_row`  (lines 329–344)

```
def _row(row: sa.Row, *, private: bool=False) -> ObjectRow
```

**Purpose**: Turns a database conversation row into a compact object-list row for tool/object listings. It produces a readable summary like where the conversation happened and when it was created.

**Data flow**: It receives a database row and an optional private marker. It builds an origin label from the surface and surface label, collects fields such as surface and surface label, adds `private: true` when appropriate, and returns an object row named by the conversation id.

**Call relations**: This helper is used by `list` for ordinary and private metadata rows, and by `member_detail` to include a list-style row beside the detailed portal object.

*Call graph*: called by 2 (list, member_detail); 1 external calls (__init__).


##### `_detail`  (lines 347–359)

```
def _detail(row: sa.Row) -> ObjectDetail[ConversationSpec]
```

**Purpose**: Turns a database conversation row into the detailed object response. The detail includes the conversation spec, timestamps, and a link back to the agent the conversation is scoped to.

**Data flow**: It receives a database row. It builds a `ConversationSpec` from surface, surface label, and audience; copies created and updated timestamps; and creates a `scoped_to` link pointing at the agent name. It returns this bundled detail object.

**Call relations**: This helper is used by `get` and `member_detail` whenever a found row must be returned as full conversation metadata. It also creates the object link that lets readers navigate from a conversation back to its agent.

*Call graph*: called by 2 (get, member_detail); 4 external calls (__init__, __init__, __init__, __init__).


### Administrative workspace controls
Credential slots and member records expose controlled administrative views and actions without revealing secrets or bypassing access rules.

### `core/src/ufo/host/kinds/credential_kind.py`

`domain_logic` · `request handling`

Extensions can declare that they need a secret, such as an API key. This file turns those declarations into a readable workspace object kind called `credential`. The important safety rule is that the secret itself is never returned. A read only shows the slot’s declaration, such as its name, description, extension, and host information, plus whether a stored value exists.

Think of each credential slot like a labeled safe deposit box. The label is public to the workspace, and people can see whether the box is occupied, but nobody can see the contents through this object interface. If the slot is empty, there is no database row for it. If it is filled, the database row stores the sealed secret elsewhere, but this file only checks that the row exists and reads timestamps.

The `CredentialObjects` class provides the object operations. Listing builds one row per declared slot. Detail shows the slot declaration and fill timestamps. Status reports whether a slot is filled, and may include host information. Create and update are deliberately refused, because filling or rotating a secret must go through a separate private `request_credentials` flow. Delete does not remove the slot declaration; it only clears the stored value, and only a workspace admin may do that.

#### Function details

##### `CredentialObjects.list`  (lines 77–78)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows the workspace-level list of all declared credential slots. It is used when someone wants a paged index of slots and their filled-or-empty state.

**Data flow**: It receives a tool context and a list query. It asks `_rows` to build the full set of slot rows, then passes those rows and the query to `object_page`, which applies the requested paging and returns an `ObjectPage`.

**Call relations**: This is the public listing path for the object kind. It delegates the actual row building to `_rows`, then hands the result to the shared object paging helper so credential slots behave like other workspace objects.

*Call graph*: calls 1 internal fn (_rows); 1 external calls (object_page).


##### `CredentialObjects.member_page`  (lines 80–90)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows the same credential slot index to a signed-in member through the member-facing portal. Because slot declarations do not contain member-specific secrets, every member sees the same declared slots and fill states.

**Data flow**: It receives optional extension context, member identity details, admin status, and a list query. It builds rows with `_rows`, then sends them through `object_page` to produce a paged result.

**Call relations**: This mirrors `CredentialObjects.list` for the portal/member view. It relies on `_rows` for the shared workspace-wide view, then uses the common paging helper to shape the response.

*Call graph*: calls 1 internal fn (_rows); 1 external calls (object_page).


##### `CredentialObjects.get`  (lines 92–93)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[CredentialSpec] | None
```

**Purpose**: Returns the detail view for one credential slot by name. It shows the declaration and timestamps, but never the secret value.

**Data flow**: It receives a tool context and a slot name. It passes the name to `_detail`, which either builds the safe detail object or returns nothing if the slot is not declared.

**Call relations**: This is the workspace object detail path. It keeps the public method small and lets `_detail` contain the shared lookup and database timestamp logic also used by the member detail path.

*Call graph*: calls 1 internal fn (_detail).


##### `CredentialObjects.member_detail`  (lines 95–110)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[CredentialSpec] | None
```

**Purpose**: Returns one credential slot for the member-facing portal, combining the list row and the detailed declaration. It gives a member the same safe information as the workspace object view.

**Data flow**: It receives optional extension context, the slot name, member identity details, and admin status. It asks `_detail` for the slot declaration and timestamps; if the slot does not exist, it returns nothing. Otherwise it asks `_rows` for the current row information, finds the matching row, and wraps the row and detail together in a `MemberObject`.

**Call relations**: This function connects the two shared views: `_detail` supplies the full declaration, while `_rows` supplies the list-style summary and filled flag. It then packages both pieces for the member portal response.

*Call graph*: calls 2 internal fn (_detail, _rows); 1 external calls (__init__).


##### `CredentialObjects.status`  (lines 112–136)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports the current state of one credential slot in a compact form. It tells the caller whether the slot has a stored secret, and may add host information used when the credential is injected into an extension.

**Data flow**: It receives a tool context, slot name, and optional expected generation value. It looks up the declared slot by name, returns nothing if no such slot exists, then opens a workspace database transaction and checks whether a credential row exists for the current workspace and slot. It returns a dictionary with `filled: true` or `filled: false`; if host lookup is available, it also asks the credential store for the host and includes it.

**Call relations**: This is a quick status path separate from full listing or detail. It uses `_named` to confirm the slot declaration, reads the current workspace through `ws_current`, checks the database inside `workspace_tx`, and calls `credential_host` only when host information can be resolved.

*Call graph*: calls 1 internal fn (_named); 4 external calls (select, workspace_tx, credential_host, ws_current).


##### `CredentialObjects.apply`  (lines 138–147)

```
async def apply(self, ctx: ToolContext, name: str, spec: CredentialSpec, old: CredentialSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses create and update operations for credential slots. This protects secrets by forcing filling or rotating to happen through the separate private credential request flow.

**Data flow**: It receives the requested slot name, desired credential specification, previous specification, and optional expected generation value. Instead of writing anything, it raises `VerbNotSupported` with an explanation that secrets must be supplied through `request_credentials`.

**Call relations**: This function is the guardrail for the object write path. When the object system tries to create or update a credential object, this method stops the operation before any secret could be passed through the normal object interface.

*Call graph*: 1 external calls (__init__).


##### `CredentialObjects.delete`  (lines 149–165)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Clears the stored value for a credential slot. It does not remove the slot itself, because the slot comes from an extension declaration; after deletion, the slot remains visible as empty.

**Data flow**: It receives a tool context, slot name, and optional expected generation value. It first asks the context whether the speaker is a workspace admin. If not, it raises `AdminRequired`. If allowed, it finds the declared slot, opens a workspace database transaction, and deletes the credential row for the current workspace and slot.

**Call relations**: This is the only normal object operation here that changes stored credential state. It uses `_named` to translate the visible name to a declared slot, relies on `ToolContext.speaker_is_admin` for the admin gate, and performs the database delete inside `workspace_tx` for the current workspace.

*Call graph*: calls 2 internal fn (_named, speaker_is_admin); 4 external calls (__init__, delete, workspace_tx, ws_current).


##### `CredentialObjects._rows`  (lines 167–183)

```
async def _rows(self) -> tuple[ObjectRow, ...]
```

**Purpose**: Builds the safe list rows for all declared credential slots. Each row says what the slot is, which extension declared it, and whether it is filled.

**Data flow**: It first asks `_filled_slots` for the set of slot names that currently have database rows. It also asks `_named` for the declared slots keyed by name. For each declared slot, it creates an `ObjectRow` with a readable summary and fields for slot name, extension, and filled state, then returns all rows as a tuple.

**Call relations**: This helper feeds every list-style view. `CredentialObjects.list`, `CredentialObjects.member_page`, and `CredentialObjects.member_detail` call it when they need the current safe row representation of credential slots.

*Call graph*: calls 2 internal fn (_filled_slots, _named); called by 3 (list, member_detail, member_page); 1 external calls (__init__).


##### `CredentialObjects._detail`  (lines 185–209)

```
async def _detail(self, name: str) -> ObjectDetail[CredentialSpec] | None
```

**Purpose**: Builds the safe detail view for one declared credential slot. It includes the declaration and fill timestamps, but not the stored credential value.

**Data flow**: It receives a slot name and looks it up with `_named`. If no declaration exists, it returns nothing. If the slot exists, it opens a workspace database transaction and checks for the row that would contain the sealed value, reading only creation and update timestamps. It then returns an `ObjectDetail` containing a `CredentialSpec` made from the slot declaration and the timestamps, or null timestamps if the slot is empty.

**Call relations**: This helper is shared by `CredentialObjects.get` and `CredentialObjects.member_detail`. It combines declaration data from the extension manifest with limited database metadata from the current workspace.

*Call graph*: calls 1 internal fn (_named); called by 2 (get, member_detail); 5 external calls (__init__, __init__, select, workspace_tx, ws_current).


##### `CredentialObjects._named`  (lines 211–212)

```
def _named(self) -> dict[str, DeclaredSlot]
```

**Purpose**: Creates a name-to-slot lookup table for the declared credential slots. This lets the rest of the file quickly find a slot by the public name used in object requests.

**Data flow**: It reads the `slots` stored on the `CredentialObjects` instance and passes them to `named_slots`. The result is a dictionary whose keys are slot names and whose values are the declared slot records.

**Call relations**: This is a small shared lookup helper. `_detail`, `_rows`, `delete`, and `status` use it before they can work with a specific declared slot.

*Call graph*: called by 4 (_detail, _rows, delete, status); 1 external calls (named_slots).


##### `CredentialObjects._filled_slots`  (lines 214–223)

```
async def _filled_slots(self) -> frozenset[str]
```

**Purpose**: Finds which credential slots currently have stored values in the current workspace. It only returns slot names, not the secrets.

**Data flow**: It opens a workspace database transaction, reads credential rows for the current workspace, and selects only the slot column. It turns those rows into a frozen set of slot names and returns that set.

**Call relations**: This helper is called by `_rows` so list views can mark each declared slot as filled or empty. It uses `workspace_tx` and `ws_current` to make sure it only checks the active workspace.

*Call graph*: called by 1 (_rows); 3 external calls (select, workspace_tx, ws_current).


### `core/src/ufo/host/kinds/members.py`

`domain_logic` · `request handling and tool execution`

This file is the rulebook for workspace membership. It decides who can see the roster, who can change a member’s admin role or seat, and who can add someone new. Without it, the system would not have a safe, consistent way to answer questions like “Who is in this workspace?”, “Can this person still use the agent?”, or “May this admin invite a contractor?”

The main idea is that membership is sensitive internal information. In a normal internal conversation with the main agent, members can see the workspace roster. In a child agent or a channel shared with another organization, visibility is narrower, often only the speaker’s own row. Think of it like an office directory: available inside the company, but not something you read aloud in a meeting with outsiders.

The file also protects changes. A member cannot be deleted through the object system. A workspace admin, speaking through the main agent, may change another member’s admin flag or seat. A “seat” is the access switch: an unseated member still exists in the roster, but their messages are refused when they try to enter. The code also prevents locking the workspace out by removing the last admin or last seated admin.

Finally, the add_member tool creates a member by email, even outside the workspace’s usual email domain, after checking that the speaker is an admin and the person is not already a member.

#### Function details

##### `MemberObjects.list`  (lines 68–72)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows a page of member rows that the current speaker is allowed to see. This is used when the agent needs to list member objects during a conversation.

**Data flow**: It receives the current tool context and a paging/query request. It asks for the visible member database rows, turns each row into a simple object-list row, then packages those rows into a page. The result is a filtered roster, not necessarily the whole workspace.

**Call relations**: This is the public list path for the member object store. It relies on MemberObjects._visible_rows to enforce the privacy rules, uses _row to format each member, and hands the final collection to object_page so the object system can return a normal paged response.

*Call graph*: calls 2 internal fn (_visible_rows, _row); 1 external calls (object_page).


##### `MemberObjects.member_page`  (lines 74–91)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows a page of members for portal-style reads outside a live agent turn. It follows a stricter rule: the main agent can show the whole roster, while other agents show only the signed-in member’s own row.

**Data flow**: It receives the portal context, the signed-in member’s id, whether they are an admin, and the requested page options. It looks up which rows this member may see for the current agent, formats them into list rows, and returns a page.

**Call relations**: This is the portal counterpart to MemberObjects.list. Instead of using turn-time speaker rules, it calls MemberObjects._member_rows, then formats rows with _row and returns them through object_page.

*Call graph*: calls 2 internal fn (_member_rows, _row); 1 external calls (object_page).


##### `MemberObjects.get`  (lines 93–95)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[MemberSpec] | None
```

**Purpose**: Fetches the detailed member object for one visible member during a tool turn. If the requested member is hidden by the visibility rules, it behaves as if that member does not exist.

**Data flow**: It receives the current tool context and a member object name, which is expected to be the member id as text. It searches only the rows visible to this speaker. If it finds a match, it converts the database row into detailed member data; otherwise it returns nothing.

**Call relations**: This is the single-object companion to MemberObjects.list. It calls MemberObjects._visible_row for the access check and lookup, then _detail to build the editable member specification and timestamps.

*Call graph*: calls 2 internal fn (_visible_row, _detail).


##### `MemberObjects.member_detail`  (lines 97–113)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[MemberSpec] | None
```

**Purpose**: Fetches one member’s portal-facing detail view. It uses the portal visibility rule, where admins do not get extra widening from child agents.

**Data flow**: It receives a member name, the signed-in member’s id, and portal read information. It asks for the rows this member can see for the current agent, looks for the row whose id matches the requested name, and returns both a summary row and detailed member data if found.

**Call relations**: This is the portal version of MemberObjects.get. It gets allowed rows from MemberObjects._member_rows, formats the summary with _row, formats the details with _detail, and wraps both in a MemberObject for the portal response.

*Call graph*: calls 3 internal fn (_member_rows, _detail, _row); 1 external calls (__init__).


##### `MemberObjects.status`  (lines 115–130)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns a small status snapshot for a visible member: their email and whether they are seated. This gives callers a lightweight way to check the current access state without fetching the full detail object.

**Data flow**: It receives the current context, a member name, and an optional expected generation value. It looks for that member among the rows visible to the speaker. If found, it returns a small dictionary with email and seated status; if not found, it returns nothing.

**Call relations**: This function sits beside get as a smaller read path. It depends on MemberObjects._visible_row to apply the same visibility rules used elsewhere, then returns only the few fields needed for status.

*Call graph*: calls 1 internal fn (_visible_row).


##### `MemberObjects.apply`  (lines 132–211)

```
async def apply(self, ctx: ToolContext, name: str, spec: MemberSpec, old: MemberSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Changes an existing member’s admin role and/or seat, but only when a workspace admin is speaking through the main agent. It is the protected path for granting or removing admin power and access.

**Data flow**: It receives the current context, the member id as text, the desired new member settings, and the previous settings if present. It first rejects creation attempts, non-main-agent use, and callers who are not signed in as members. It then locks the workspace row in the database, confirms the speaker is an admin, finds the target member, grants or revokes their seat if needed, and updates their admin flag if allowed. It changes the database and returns no content on success.

**Call relations**: This is the write path for the member object. It calls context checks such as agent_is_main, uses workspace_tx for a safe database transaction, uses member_is_admin to confirm authority, and uses Seats to grant or revoke access. It raises AdminRequired, UnknownObject, or VerbNotSupported when the requested change is not allowed.

*Call graph*: calls 1 internal fn (agent_is_main); 10 external calls (__init__, __init__, __init__, __init__, select, update, workspace_tx, member_is_admin, ws_current, UUID).


##### `MemberObjects.delete`  (lines 213–220)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses deletion of member objects. Members can be unseated to remove access, but they are not deleted through this object interface.

**Data flow**: It receives the current context, member name, and optional generation check. It does not read or change any member data. It immediately raises an error explaining that deletion is not supported.

**Call relations**: This completes the object-store interface while enforcing the membership rule. Any caller trying to delete a member through objects is stopped here by VerbNotSupported.

*Call graph*: 1 external calls (__init__).


##### `MemberObjects._visible_rows`  (lines 222–228)

```
async def _visible_rows(self, ctx: ToolContext) -> tuple[sa.Row, ...]
```

**Purpose**: Decides which member rows the current speaker may see during a live tool turn. This is the central privacy filter for conversational member listing and lookup.

**Data flow**: It receives the tool context. If there is no signed-in speaker, it returns an empty set. If the conversation is in a foreign shared audience, it returns only the speaker’s own row. Otherwise it checks whether the agent is the main agent or the speaker is an admin, and returns either the whole roster or only the speaker’s row.

**Call relations**: MemberObjects.list and MemberObjects._visible_row call this before showing member data. After deciding whether visibility should be whole-workspace or self-only, it delegates the actual database query to MemberObjects._roster.

*Call graph*: calls 3 internal fn (_roster, agent_is_main, speaker_is_admin); called by 2 (_visible_row, list).


##### `MemberObjects._visible_row`  (lines 230–234)

```
async def _visible_row(self, ctx: ToolContext, name: str) -> sa.Row | None
```

**Purpose**: Finds one named member among the rows the current speaker is allowed to see. It combines lookup with the same privacy rules used for lists.

**Data flow**: It receives the tool context and the requested member name. It gets all rows visible to the speaker, compares each row’s id to the requested name, and returns the matching row if one is visible. If no visible row matches, it returns nothing.

**Call relations**: MemberObjects.get and MemberObjects.status use this helper so they cannot accidentally reveal hidden members. It gets its candidate rows from MemberObjects._visible_rows.

*Call graph*: calls 1 internal fn (_visible_rows); called by 2 (get, status).


##### `MemberObjects._member_rows`  (lines 236–248)

```
async def _member_rows(self, member_id: UUID) -> tuple[sa.Row, ...]
```

**Purpose**: Decides which member rows a signed-in member may see in portal reads outside a live turn. The rule depends on whether the current agent is the main agent.

**Data flow**: It receives the signed-in member’s id. It opens a workspace database transaction, checks whether the current agent is marked as the main agent, then asks for either the whole roster or only that member’s own row. It returns the selected database rows.

**Call relations**: MemberObjects.member_page and MemberObjects.member_detail call this for portal reads. It checks the current agent through agent_current and ws_current, then hands the final roster query to MemberObjects._roster.

*Call graph*: calls 1 internal fn (_roster); called by 2 (member_detail, member_page); 4 external calls (select, workspace_tx, agent_current, ws_current).


##### `MemberObjects._roster`  (lines 250–270)

```
async def _roster(self, member_id: UUID, *, whole: bool) -> tuple[sa.Row, ...]
```

**Purpose**: Reads the membership roster from the database, either as the whole workspace roster or as one member’s own row. This is the shared database reader behind both conversation and portal views.

**Data flow**: It receives a member id and a flag saying whether to fetch the whole roster. It builds a database query for members in the current workspace, ordered by email. If whole is false, it adds a filter for the one member id. It runs the query and returns the matching rows.

**Call relations**: MemberObjects._visible_rows and MemberObjects._member_rows call this after they decide the visibility level. It uses the current workspace and workspace_tx to read the member table safely and consistently.

*Call graph*: called by 2 (_member_rows, _visible_rows); 3 external calls (select, workspace_tx, ws_current).


##### `_row`  (lines 273–286)

```
def _row(row: sa.Row) -> ObjectRow
```

**Purpose**: Turns a database member row into the short summary shown in member lists. It makes the raw database fields readable as an object row.

**Data flow**: It receives a database row containing a member’s id, email, admin flag, and seat timestamp. It builds a name from the id, a human-readable summary like email plus role plus seated state, and fields for email, admin, and seated. It returns an ObjectRow.

**Call relations**: MemberObjects.list, MemberObjects.member_page, and MemberObjects.member_detail use this whenever they need the list-style version of a member. It is the formatting bridge between database rows and the object system’s list display.

*Call graph*: called by 3 (list, member_detail, member_page); 1 external calls (__init__).


##### `_detail`  (lines 289–294)

```
def _detail(row: sa.Row) -> ObjectDetail[MemberSpec]
```

**Purpose**: Turns a database member row into the detailed editable member object. It exposes the member’s admin and seated settings along with creation and update times.

**Data flow**: It receives a database row with role, seat, and timestamp data. It creates a MemberSpec from the admin flag and whether a seat timestamp exists, then wraps that spec with created and updated timestamps. It returns an ObjectDetail.

**Call relations**: MemberObjects.get and MemberObjects.member_detail use this when a caller asks for one member in detail. It pairs with _row: _row is for summaries, while _detail is for the full member object body.

*Call graph*: called by 2 (get, member_detail); 2 external calls (__init__, __init__).


##### `AddMember.add`  (lines 331–363)

```
async def add(self, ctx: ToolContext, args: AddMemberInput) -> ToolResult
```

**Purpose**: Adds a new member to the workspace by email, optionally making them an admin and optionally sending them an invitation email. This lets an admin add people before those people first contact the agent.

**Data flow**: It receives the tool context and input containing email, admin choice, and notify choice. It confirms the speaker is a signed-in member using the main agent, rejects use in externally shared rooms, normalizes and validates the email, locks the workspace in a database transaction, confirms the speaker is an admin, checks that the email is not already a member, and creates the member. It returns a short tool result telling the user what happened and whether an email will be sent.

**Call relations**: This is the handler registered by ADD_MEMBER_TOOL_DEF. It calls AddMember._absent before creation to prevent duplicates, uses member_is_admin for permission, create_member to insert the new member, and returns TextContent inside a ToolResult for the agent to show to the caller.

*Call graph*: calls 2 internal fn (_absent, agent_is_main); 9 external calls (__init__, __init__, __init__, select, workspace_tx, create_member, email_domain, member_is_admin, ws_current).


##### `AddMember._absent`  (lines 365–378)

```
async def _absent(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Checks that an email address is not already a member of the current workspace. It protects add_member from creating duplicate membership records.

**Data flow**: It receives an open database connection and a normalized email address. It searches the current workspace’s member table using a case-insensitive email match. If no existing member is found, it returns normally; if one exists, it raises an error telling the caller to change that existing member instead.

**Call relations**: AddMember.add calls this inside its database transaction just before create_member. This helper keeps the duplicate-checking rule separate from the larger add flow.

*Call graph*: called by 1 (add); 3 external calls (execute, select, ws_current).


### Workspace surfaces and metadata
Surface and workspace objects provide read-only inspection of connected chat surfaces and high-level workspace membership facts.

### `core/src/ufo/host/kinds/surface_kind.py`

`domain_logic` · `request handling`

A “surface” is a place where chat can happen, such as a browser UI, Slack workspace, or another provider channel. Extensions declare these surfaces in their manifests, and this file turns those declarations into objects that can be listed, inspected, and checked for connection status inside a workspace.

The key idea is that surfaces are not edited like normal user-created objects. They come from extension manifests, so creating or deleting one means installing or removing the extension. A surface may also have an installation row in the database, which is the workspace’s binding between that surface and an agent. Think of the manifest as a restaurant menu saying what entrances exist, and the installation table as the doorman’s list saying which entrance is actually connected for this workspace.

First, `registered_surfaces` reads all active manifests, checks that every surface name is valid, and refuses duplicate names. Then `SurfaceObjects` provides the read-only object behavior: list all registered surfaces, show details for one surface, report whether it is bound, and refuse apply/delete requests with clear explanations. It also hides these objects from foreign shared audiences, and shows member portal data only to admins because surface transport setup is administrative state.

The database query deliberately returns useful public state, such as bound agent name and timestamps, but not installation IDs or provider secrets.

#### Function details

##### `registered_surfaces`  (lines 54–79)

```
def registered_surfaces(manifests: tuple[Manifest, ...]) -> dict[str, RegisteredSurface]
```

**Purpose**: Builds the master list of surfaces declared by the currently active extensions. It also protects the system from bad or ambiguous surface names by failing early if a name is invalid or two extensions claim the same name.

**Data flow**: It receives a tuple of extension manifests. For each surface declaration inside each manifest, it checks the surface name against the object-name rules, then records the declaring extension and the surface’s routing traits. The result is a dictionary keyed by surface name, where each value says which extension declared it and how it behaves.

**Call relations**: This is the setup step that prepares the data later used by `SurfaceObjects`. It calls the object-name validator before accepting a surface, raises `InvalidName` with extension context when validation fails, and creates `RegisteredSurface` records for valid declarations.

*Call graph*: 3 external calls (__init__, __init__, validate_object_name).


##### `SurfaceObjects.list`  (lines 114–117)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists the registered surfaces visible to an internal tool conversation. It returns an empty page for foreign shared audiences so external channels cannot inspect workspace transport setup.

**Data flow**: It receives a tool context and a list query. If the audience is foreign, it immediately returns an empty paged result. Otherwise it reads current surface installations from the database, turns the registered surfaces into display rows, and wraps those rows in an object page using the query’s paging and filtering rules.

**Call relations**: This is one of the main read paths for the object kind. It calls `_installations` to learn what is bound in this workspace, `_rows` to make human-readable rows, and `object_page` to package them for the object API.

*Call graph*: calls 2 internal fn (_installations, _rows); 1 external calls (object_page).


##### `SurfaceObjects.member_page`  (lines 119–131)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists surfaces for the member-facing portal, but only for admins. Non-admin members see no rows because surface connection state is considered workspace administration information.

**Data flow**: It receives member identity information, an admin flag, and a list query. If the member is not an admin, it returns an empty page. If the member is an admin, it reads installations, builds rows for all registered surfaces, and returns them as a paged object result.

**Call relations**: This mirrors `SurfaceObjects.list` for the member portal. It relies on `_installations` and `_rows`, then hands the result to `object_page` so the portal gets the same row shape as other object listings.

*Call graph*: calls 2 internal fn (_installations, _rows); 1 external calls (object_page).


##### `SurfaceObjects.get`  (lines 133–138)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SurfaceObjectSpec] | None
```

**Purpose**: Returns the detailed declaration for one surface when an internal tool asks for it. It returns nothing if the caller is foreign or the surface name is not registered.

**Data flow**: It receives a tool context and a surface name. It first checks whether the request comes from a foreign shared audience, then checks whether the name exists in the registered surface map. If both checks pass, it reads current installation data and builds an object detail containing the surface declaration plus installation timestamps when present.

**Call relations**: This is the single-object read path for internal tools. It calls `_installations` to get binding state and `_detail` to format the declared surface data as an `ObjectDetail`.

*Call graph*: calls 2 internal fn (_detail, _installations).


##### `SurfaceObjects.member_detail`  (lines 140–156)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[SurfaceObjectSpec] | None
```

**Purpose**: Returns one surface for the member-facing portal, combining its list row and its detailed declaration. It only answers admins.

**Data flow**: It receives optional extension context, a surface name, member identity, and an admin flag. If the requester is not an admin or the surface is unknown, it returns nothing. Otherwise it reads installations once, builds the row version and the detail version, and returns them together as a member object.

**Call relations**: This is the portal’s detailed read path. It calls `_installations`, then uses `_row` and `_detail` to assemble the two views before wrapping them in `MemberObject`.

*Call graph*: calls 3 internal fn (_detail, _installations, _row); 1 external calls (__init__).


##### `SurfaceObjects.status`  (lines 158–177)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports whether one registered surface is bound to an installation in this workspace, and if so which agent it uses. This is a compact status check rather than a full object read.

**Data flow**: It receives a tool context, surface name, and an optional expected generation value. It ignores the generation value here because these read-only surfaces do not use object generations. If the audience is foreign or the surface is unknown, it returns nothing. Otherwise it reads installations and returns either `bound: false` or a small dictionary with the bound agent, whether that agent is archived, and whether the installation routes incoming traffic.

**Call relations**: This supports callers that need operational state instead of the full surface spec. It calls `_installations` directly and then translates the matching installation, if any, into a simple status dictionary.

*Call graph*: calls 1 internal fn (_installations).


##### `SurfaceObjects.apply`  (lines 179–188)

```
async def apply(self, ctx: ToolContext, name: str, spec: SurfaceObjectSpec, old: SurfaceObjectSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create or update a surface object. Surfaces are declared by extensions and connected through the surface’s own setup flow, not edited through this object API.

**Data flow**: It receives the requested surface name, new spec, old spec, context, and optional generation check. It does not inspect or change stored data. Instead it raises a `VerbNotSupported` error explaining that setup belongs to the surface’s own connect flow.

**Call relations**: This is the write path for create/update requests, but it intentionally stops the flow. It hands off only to `VerbNotSupported`, which communicates the refusal to the caller.

*Call graph*: 1 external calls (__init__).


##### `SurfaceObjects.delete`  (lines 190–197)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to delete a surface object. A surface disappears when its declaring extension is removed, not through object deletion.

**Data flow**: It receives the context, surface name, and optional generation check. It does not read or change any surface or installation data. It raises a `VerbNotSupported` error with a message explaining that registration belongs to the extension manifest.

**Call relations**: This is the delete path for the object kind, and it deliberately blocks deletion. It uses `VerbNotSupported` to return a clear reason to the object API caller.

*Call graph*: 1 external calls (__init__).


##### `SurfaceObjects._rows`  (lines 199–203)

```
def _rows(self, installed: Mapping[str, _BoundInstallation]) -> tuple[ObjectRow, ...]
```

**Purpose**: Builds the list-view rows for every registered surface. It sorts surfaces by name so listings are stable and easy to scan.

**Data flow**: It receives the current installation map, then walks through the registered surfaces in sorted order. For each one, it asks `_row` to combine the declaration and any matching installation into a display row. It returns a tuple of all those rows.

**Call relations**: `SurfaceObjects.list` and `SurfaceObjects.member_page` call this after reading installation state. `_rows` delegates the per-surface formatting to `_row` so the list views stay consistent.

*Call graph*: calls 1 internal fn (_row); called by 2 (list, member_page).


##### `SurfaceObjects._row`  (lines 205–228)

```
def _row(self, name: str, registered: RegisteredSurface, installed: Mapping[str, _BoundInstallation]) -> ObjectRow
```

**Purpose**: Creates one compact list row for a surface, including a short summary and filterable fields. This is what users see in a surface listing.

**Data flow**: It receives a surface name, its registered declaration, and the map of installations. If there is no installation, it describes the surface as not bound. If there is one, it says which agent it is bound to and notes if that agent is archived. It returns an `ObjectRow` with the name, summary, and fields such as extension, addressed, durable, home, and bound.

**Call relations**: `_rows` uses this for every surface in a listing, and `member_detail` uses it for the row half of a member detail response. It creates the `ObjectRow` that the object API can page, filter, and display.

*Call graph*: called by 2 (_rows, member_detail); 1 external calls (__init__).


##### `SurfaceObjects._detail`  (lines 230–245)

```
def _detail(self, name: str, installed: Mapping[str, _BoundInstallation]) -> ObjectDetail[SurfaceObjectSpec]
```

**Purpose**: Creates the detailed object view for one surface. It shows the manifest-declared surface settings and, when the workspace has a binding, the binding’s creation and update times.

**Data flow**: It receives a surface name and the current installation map. It looks up the registered declaration, checks whether that surface is installed, and builds a `SurfaceObjectSpec` from the declaration. It returns an `ObjectDetail` containing that spec plus `created_at` and `updated_at` timestamps when a binding exists, or `None` timestamps when it does not.

**Call relations**: `SurfaceObjects.get` and `SurfaceObjects.member_detail` call this when they need the full surface declaration. It creates both the `SurfaceObjectSpec` and the surrounding `ObjectDetail`.

*Call graph*: called by 2 (get, member_detail); 2 external calls (__init__, __init__).


##### `SurfaceObjects._installations`  (lines 247–278)

```
async def _installations(self) -> dict[str, _BoundInstallation]
```

**Purpose**: Reads the current workspace’s surface bindings from the database. It tells the rest of the file which registered surfaces are actually connected to agents in this workspace.

**Data flow**: It opens a workspace database transaction, selects surface installation rows for the current workspace, joins each one to its agent, and reads the surface name, routing flag, timestamps, agent name, and archive state. It returns a dictionary keyed by surface name, where each value describes the bound agent and installation metadata.

**Call relations**: All read paths that need binding state call this: `list`, `member_page`, `get`, `member_detail`, and `status`. It uses `workspace_tx` to talk to the database, `ws_current` to limit the query to the active workspace, SQLAlchemy to build the query, and `_BoundInstallation` to package each row for the rest of the file.

*Call graph*: called by 5 (get, list, member_detail, member_page, status); 4 external calls (__init__, select, workspace_tx, ws_current).


### `core/src/ufo/host/kinds/workspace_kind.py`

`domain_logic` · `request handling`

This file gives the system a small window onto the current workspace. A workspace here is not something a caller edits directly. It is more like a building directory: you can ask how many people belong, who currently has access, and when the workspace row was created or updated, but you do not rewrite the building itself from this screen.

The main object is `WorkspaceObjects`, which supplies the actions the object system expects: list, get, status, apply, and delete. Listing returns one row for the current workspace, named by its workspace id. Getting returns the empty editable form, because there is nothing a user is allowed to author on a workspace. Status returns the useful live facts: member count, seated count, and a roster.

The roster has an important privacy rule. Internal users talking to the main agent can see the whole roster. A child agent only shows the speaker’s own member row. External shared audiences see nothing at all.

The helper `_shape` gathers the shared data used by the read operations. It reads the workspace timestamps from the database and asks the seating system for a snapshot of members and seats. Mutations are intentionally rejected with clear messages: seats are changed on member objects, and the workspace itself is permanent.

#### Function details

##### `WorkspaceObjects.list`  (lines 73–86)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Returns the one visible workspace row for an internal caller. It shows a short summary with the total member count and how many members currently hold seats.

**Data flow**: It receives the tool context and a list query. If the audience is external, it returns an empty page. Otherwise it reads the current workspace id, asks `_shape` for the latest counts, builds one row with those counts, and wraps it in a paged result.

**Call relations**: This is called when the object system needs to list workspace objects. It relies on `_shape` to collect the real database-backed workspace facts, then hands the result to `object_page` so normal paging, filtering, or ordering rules can be applied.

*Call graph*: calls 1 internal fn (_shape); 3 external calls (__init__, object_page, ws_current).


##### `WorkspaceObjects.get`  (lines 88–98)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[WorkspaceSpec] | None
```

**Purpose**: Returns the stored details for the current workspace, but only when the caller asks for the actual current workspace id. The returned spec is empty because the workspace has no user-editable fields.

**Data flow**: It receives the tool context and an object name. External callers get nothing, and a name that does not match the current workspace id also gets nothing. For the correct workspace, it reads the shape, creates an empty `WorkspaceSpec`, and returns it with the workspace creation and update timestamps.

**Call relations**: This is used when someone opens or reads a specific workspace object. Like listing, it calls `_shape` for the shared facts, but it exposes only the object detail shape here, leaving live roster information to `status`.

*Call graph*: calls 1 internal fn (_shape); 3 external calls (__init__, __init__, ws_current).


##### `WorkspaceObjects.status`  (lines 100–121)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns the live status for the workspace: total members, seated members, and the roster the caller is allowed to see. This is where the file enforces the rule that not every caller may see every member.

**Data flow**: It receives the tool context, an object name, and an optional expected generation value. External callers and wrong workspace names receive nothing. For the current workspace, it reads the latest shape, checks whether the speaker is a member talking to the main agent, and then returns counts plus either the full roster or only the speaker’s own row.

**Call relations**: This function is called when the object system asks for runtime status rather than editable spec data. It depends on `_shape` for the roster and on `ToolContext.agent_is_main` to decide whether the caller may see everyone or only themselves.

*Call graph*: calls 2 internal fn (_shape, agent_is_main); 1 external calls (ws_current).


##### `WorkspaceObjects.apply`  (lines 123–132)

```
async def apply(self, ctx: ToolContext, name: str, spec: WorkspaceSpec, old: WorkspaceSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create or update the workspace object. The reason is that changing seats belongs to member objects, not to the workspace object.

**Data flow**: It receives the requested workspace name, the proposed spec, the old spec if any, and an optional expected generation value. It does not inspect or save them. Instead, it raises a clear “verb not supported” error explaining that seat changes must be made on member objects.

**Call relations**: This is reached when the generic object system tries to apply a change to a workspace. Rather than passing work onward, it stops the flow immediately so callers do not mistakenly think the workspace itself can be edited.

*Call graph*: 1 external calls (__init__).


##### `WorkspaceObjects.delete`  (lines 134–141)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to delete the workspace. In this system, the workspace is created for the installation or first run and is treated as permanent.

**Data flow**: It receives the tool context, workspace name, and optional expected generation value. It does not delete anything. It raises a “verb not supported” error with a message saying the workspace is never deleted.

**Call relations**: This is called by the object system when a delete verb is attempted. It acts as a guardrail, ending the delete path before any database change can happen.

*Call graph*: 1 external calls (__init__).


##### `WorkspaceObjects._shape`  (lines 143–161)

```
async def _shape(self) -> WorkspaceShape
```

**Purpose**: Collects the shared workspace facts used by list, get, and status. It packages database timestamps together with the current seating snapshot into one simple `WorkspaceShape` value.

**Data flow**: It reads the current workspace id, opens a workspace database transaction, fetches the workspace row’s creation and update times, and asks the seating system for a snapshot of members and seats. It then returns a `WorkspaceShape` containing member count, seated count, roster entries, and timestamps.

**Call relations**: This is the common helper behind the read operations. `list`, `get`, and `status` each call it so they all answer from the same source of truth instead of duplicating database and seating lookups.

*Call graph*: called by 3 (get, list, status); 5 external calls (__init__, __init__, select, workspace_tx, ws_current).

# Portal and object-oriented workspace reads and writes  `stage-7.2`

This stage is shared support for the portal, tools, and agents when they read or change workspace “objects,” meaning named things like agents, sites, tasks, members, or connected accounts. The central object system defines the common rules for listing, opening, creating, updating, deleting, explaining, and running actions on these things. Agent objects cover prompts, models, permissions, archiving, and restoration, while governance adds an approval step before prompt changes take effect. Object scope keeps actions tied to the right agent.

The built-in kinds expose safe views of workspaces, members, conversations, installed extensions, credential slots, and chat surfaces. Most of these are read-only or hide sensitive values. Extension object types add notifications, connectors, synced source pages, gbrain Markdown sources, memories and profiles, monitors, reports, scheduled tasks, hosted sites, and task visibility checks. Each one decides what users may inspect, share, stop, forget, dismiss, or delete.

The web panel code connects portal buttons and forms to the normal conversation-based write path. The package marker files simply make these modules importable.

## Files in this stage

### Built-in workspace kinds
Core host kinds expose read-only or tightly controlled views of extensions, conversations, credentials, members, surfaces, and the workspace itself.

### `core/src/ufo/host/ext/extension_kind.py`

`domain_logic` · `startup and request handling`

An extension is like a plug-in: it can add tools, object types, credentials it needs, background jobs, hooks, sources, chat surfaces, and subagent profiles. This file turns the set of extensions loaded by the deploy into an object kind named `extension`, so a user or tool can list them and inspect one by name.

The important idea is that these objects are declarations, not database records. They come from extension manifests that were loaded when the server started. Because of that, their creation and update timestamps are empty, and they cannot be created, edited, or deleted here. Installing or removing an extension is treated as a deployment action done through the lockfile, not as something a conversation can do.

The file first defines how manifest names become safe object names: lowercase, hyphenated, and checked against the project’s object-name rules. It refuses to start if two extensions would collapse to the same name, because otherwise one extension could hide another.

`ExtensionObjects` is the read-only doorway. Its `list` method shows a compact table with version and counts. Its `get` method returns the full declaration. Its `status` method shows what the extension asks from the deploy, such as sandbox internet access or required seams from other extensions. Mutation methods deliberately raise “not supported.”

#### Function details

##### `named_extensions`  (lines 46–61)

```
def named_extensions(manifests: tuple[Manifest, ...]) -> dict[str, Manifest]
```

**Purpose**: This function turns loaded extension manifests into the object names used by the `extension` object kind. It makes names predictable and safe, and it catches name collisions early during boot instead of letting one extension silently overwrite another.

**Data flow**: It receives a tuple of extension manifests. For each manifest, it lowercases the manifest name, replaces non-letter-or-number runs with hyphens, trims extra hyphens, and checks that the result is a valid object name. It returns a dictionary from that safe object name to the original manifest, or raises an error if two manifests produce the same object name.

**Call relations**: This is the naming step that prepares manifests for `ExtensionObjects`. It calls `re.sub` to reshape names and `validate_object_name` to enforce the shared object naming rules, so later list and get requests can use one consistent naming scheme.

*Call graph*: 2 external calls (sub, validate_object_name).


##### `ExtensionObjects.list`  (lines 90–107)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This function returns a paged list of all active extensions. It gives a quick overview: each extension’s display name, version, tool count, and credential-slot count.

**Data flow**: It reads the stored mapping of extension object names to manifests. It converts each manifest into an `ExtensionSpec`, builds a row with a human summary and sortable fields, then passes those rows and the incoming list query into `object_page`. The result is an `ObjectPage` containing only the rows requested by the query rules.

**Call relations**: When something asks to list `extension` objects, this method is the read path. It relies on `_spec` to extract the extension declaration from each manifest, wraps each entry as an `ObjectRow`, and hands the finished rows to `object_page` so normal object listing behavior like paging, filtering, or ordering can be applied.

*Call graph*: calls 1 internal fn (_spec); 2 external calls (__init__, object_page).


##### `ExtensionObjects.get`  (lines 109–113)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ExtensionSpec] | None
```

**Purpose**: This function returns the full declaration for one active extension. It is used when someone wants details beyond the short list view.

**Data flow**: It receives an object name and looks it up in the extension mapping. If no manifest exists under that name, it returns `None`. If it finds one, it converts the manifest into an `ExtensionSpec` and wraps it in an `ObjectDetail` with no creation or update timestamps, because the extension came from deployment configuration rather than stored object rows.

**Call relations**: When something asks to read one `extension` object, this method performs the lookup. It calls `_spec` to build the readable declaration and then constructs the object-detail response expected by the object system.

*Call graph*: calls 1 internal fn (_spec); 1 external calls (__init__).


##### `ExtensionObjects.status`  (lines 115–128)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This function reports what one extension asks from the deployment environment. It shows operational requirements, not secrets or editable settings.

**Data flow**: It receives an extension object name and looks up the matching manifest. If the name is unknown, it returns `None`. If found, it returns a plain dictionary containing whether the extension’s sandbox tools need public internet access and the list of required seams that another extension must provide.

**Call relations**: This is the status side of the `extension` object. While `get` explains what the extension contributes, `status` explains what it needs from the deploy. It reads directly from the manifest and does not call into mutation or storage code.


##### `ExtensionObjects.apply`  (lines 130–139)

```
async def apply(self, ctx: ToolContext, name: str, spec: ExtensionSpec, old: ExtensionSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This function rejects attempts to create or update an extension object. Extensions cannot be installed, edited, or removed through this object interface.

**Data flow**: It receives the requested name, new spec, optional old spec, and optional expected generation value, but it does not use them to change anything. Instead, it raises `VerbNotSupported` with a message explaining that extension installation and removal happen through the deploy lockfile and take effect on the next serve.

**Call relations**: This is called when the object system tries to apply a create or update operation to an `extension`. Its whole role is to stop that flow and point the caller to the deployment-level command path instead.

*Call graph*: 1 external calls (__init__).


##### `ExtensionObjects.delete`  (lines 141–148)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This function rejects attempts to delete an extension object. Removing an extension is a deployment action, not a chat-time object action.

**Data flow**: It receives the target extension name and optional expected generation value, but it does not remove anything. It raises `VerbNotSupported` with the same explanation used for updates: extensions are installed and removed through the deploy lockfile.

**Call relations**: This is called when the object system tries to delete an `extension`. Instead of handing off to storage or changing the manifest set, it immediately blocks the operation with `VerbNotSupported`.

*Call graph*: 1 external calls (__init__).


##### `ExtensionObjects._spec`  (lines 150–168)

```
def _spec(self, manifest: Manifest) -> ExtensionSpec
```

**Purpose**: This helper turns an internal extension manifest into the public declaration shown to users. It includes names of contributed things, but not secret values.

**Data flow**: It receives one manifest. It copies out the extension name and version, then gathers the names of tools, object kinds, credential slots, surfaces, jobs, hook events, source backends, and subagent profiles. It returns an `ExtensionSpec` containing those names as tuples.

**Call relations**: Both `ExtensionObjects.list` and `ExtensionObjects.get` call this helper so they describe extensions in exactly the same way. It constructs the `ExtensionSpec` object that becomes either the basis for a list row or the full detail response.

*Call graph*: called by 2 (get, list); 1 external calls (__init__).


### `core/src/ufo/host/kinds/conversations.py`

`domain_logic` · `request handling`

This file is the system’s front desk for old conversations. Artifacts and scheduled tasks can point back to the conversation they came from, and this code is what turns that link into something readable: which chat surface it happened on, what the surface called it, who could see it, when it was created or updated, and sometimes the text transcript.

The important rule is that conversations are not authored through this object API. Chat surfaces create them, and retention policies close or remove them. So attempts to create, edit, or delete a conversation through this file are refused.

Most reads are filtered by “subjects,” meaning labels that describe what the caller is allowed to see. A caller can only list or get conversations whose audience matches those subjects. There is one careful exception: a workspace admin speaking in a normal local audience can ask for private metadata rows for another member’s conversations, but only with an explicit private filter, and the transcript is still not exposed. A foreign shared-channel audience never gets this widening.

For portal use outside a live turn, the file has separate member-facing methods. These show a member’s own conversations plus shared ones, skip machine-only or untitled conversations, and mark whether each item belongs to the member. Status reads the stored transcript blob, converts messages into plain lines like “user: text,” and writes a small visible transcript file into the workspace if it is not too large.

#### Function details

##### `ConversationObjects.list`  (lines 83–90)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists conversation rows that the current tool call is allowed to see. If the caller explicitly asks for private rows and qualifies as a local workspace admin, it also includes private conversation metadata for the selected agent without exposing transcript content.

**Data flow**: It receives a tool context with the caller’s readable subjects and a list query with filters. It reads matching visible database rows, turns each row into a simple object listing, optionally adds private metadata rows, and then passes the combined rows through the normal paging and filtering helper. The output is an object page suitable for display or tool use.

**Call relations**: This is the main list entry for the conversation object kind. It relies on _rows for ordinary visibility, _widens_for_admin to decide whether private metadata may be added, _private_rows for that extra admin-only metadata, _row to format each listing, and object_page to apply the caller’s requested page rules.

*Call graph*: calls 4 internal fn (_private_rows, _rows, _widens_for_admin, _row); 1 external calls (object_page).


##### `ConversationObjects.get`  (lines 92–96)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ConversationSpec] | None
```

**Purpose**: Finds one conversation by name, where the name is expected to be the conversation’s UUID, and returns its full metadata detail if visible. It can also return admin-only private metadata when the normal visible lookup fails and the caller qualifies.

**Data flow**: It takes the tool context and a conversation name. It first searches among conversations visible to the caller’s subjects; if none is found, it tries the private admin path. If a row is found, it converts the row into a detailed object containing the conversation spec, timestamps, and link to the agent; otherwise it returns nothing.

**Call relations**: This is the single-object read path used by the object system. It asks _find for the normal visible lookup, falls back to _private_find for the special admin metadata case, and hands the found database row to _detail for final packaging.

*Call graph*: calls 3 internal fn (_find, _private_find, _detail).


##### `ConversationObjects.member_page`  (lines 98–139)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Builds the portal’s conversation list for a signed-in member outside an active turn. It shows the member’s own conversations and readable colleague conversations, while deliberately not turning an admin’s portal list into a view of everyone’s private chats.

**Data flow**: It receives the member id, admin flag, extension context, and page query. It opens the conversation directory for the current workspace and selected agent, reads two bounded groups of rows — “mine” and “others” — applies the optional portal-surface filter before paging, skips conversations with no title, formats each entry for the portal, and returns a paged object list.

**Call relations**: The portal calls this when it needs the conversation rail or chat list. The method gets the workspace and agent identity from ws_current and object_agent_id, asks ConversationDirectory for member-aware listings, uses _member_row to shape each visible entry, and finishes with object_page.

*Call graph*: calls 1 internal fn (_member_row); 4 external calls (__init__, object_agent_id, object_page, ws_current).


##### `ConversationObjects.member_detail`  (lines 141–156)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[ConversationSpec] | None
```

**Purpose**: Returns one portal-facing conversation detail for a signed-in member outside a turn. It only allows conversations visible through the member’s own conversation audience, not arbitrary private rooms or another member’s private conversation, even for admins.

**Data flow**: It receives a conversation name and member id. It derives the subjects that this member’s own conversation audience can read, searches for a matching visible row, and if found returns both a list-style row and a full detail object. If the row is outside those subjects or the name is invalid, it returns nothing.

**Call relations**: This is the portal counterpart to get. It builds the member’s allowed subjects using conversation_audience and audience_subjects, uses _find for the database lookup, and then combines _row and _detail into a MemberObject for the portal.

*Call graph*: calls 3 internal fn (_find, _detail, _row); 3 external calls (__init__, audience_subjects, conversation_audience).


##### `ConversationObjects.status`  (lines 158–176)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports transcript status for a visible conversation and, when possible, writes the visible text exchange into the caller’s workspace as a plain text file. This gives tools a safe way to inspect the conversation text without changing the conversation itself.

**Data flow**: It receives the tool context, conversation name, and an expected generation value that this implementation does not use. It finds the visible row, reads and formats the transcript, checks that the row is still visible and unchanged enough after the transcript read, and then counts messages and bytes. If the transcript has content and is under the materialization size limit, it writes a text file in the workspace and returns the message count, byte size, and path; if no row exists, it returns nothing.

**Call relations**: This is the read-status path for the object kind. It calls _find to confirm initial visibility, _exchange to read the transcript blob, and _unchanged_visible as a second safety check before returning data. If the conversation stopped being visible during the process, it raises UnknownObject instead of leaking information.

*Call graph*: calls 3 internal fn (_exchange, _find, _unchanged_visible); 1 external calls (__init__).


##### `ConversationObjects.apply`  (lines 178–187)

```
async def apply(self, ctx: ToolContext, name: str, spec: ConversationSpec, old: ConversationSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create or update a conversation through the object API. This protects the rule that conversations come from chat surfaces, not from tools editing object specs.

**Data flow**: It receives the target name, desired conversation spec, optional old spec, context, and expected generation. It does not read or write any conversation data. It immediately raises a clear “verb not supported” error explaining that conversations are surface-made.

**Call relations**: The object system calls this when something tries to apply a new desired state to a conversation. Instead of handing off to storage, it stops the flow with VerbNotSupported.

*Call graph*: 1 external calls (__init__).


##### `ConversationObjects.delete`  (lines 189–196)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to delete a conversation through the object API. Conversation lifetime is controlled elsewhere, such as by the chat surface or retention process.

**Data flow**: It receives the target name, context, and expected generation. It ignores those details for mutation purposes and raises a “verb not supported” error. No database row or transcript is changed.

**Call relations**: The object system calls this for delete requests. This method is the guardrail that prevents object users from removing conversations directly, using VerbNotSupported to report the refusal.

*Call graph*: 1 external calls (__init__).


##### `ConversationObjects._exchange`  (lines 198–218)

```
async def _exchange(self, ctx: ToolContext, conversation_id: UUID) -> tuple[str, ...]
```

**Purpose**: Reads a conversation transcript and turns it into simple human-readable text lines. It is the helper that makes status able to summarize and materialize the conversation content.

**Data flow**: It receives a tool context and conversation id. It asks the blob store for the transcript data using the transcript key, decodes it, walks through each message, extracts plain text from either simple string content or text blocks, and prefixes each line with the message role. It returns a tuple of formatted lines; if the transcript blob is missing, it returns an empty tuple; if the transcript cannot be decoded, it raises an error.

**Call relations**: ConversationObjects.status calls this after finding a visible conversation. This helper delegates the storage key and decoding work to transcript_key and decode, then hands status the clean text lines that can be counted and written to the workspace.

*Call graph*: called by 1 (status); 2 external calls (decode, transcript_key).


##### `ConversationObjects._unchanged_visible`  (lines 220–233)

```
async def _unchanged_visible(self, subjects: frozenset[str], row: sa.Row) -> bool
```

**Purpose**: Checks that a conversation is still visible to the same subjects and still has the same audience after transcript reading. This is a safety check against a race, like rechecking a door is still unlocked before carrying papers through it.

**Data flow**: It receives the allowed subjects and the database row that was found earlier. It opens a workspace database transaction and asks whether a conversation with the same id and audience is still present in the visible query. It returns true if the row still qualifies, false otherwise.

**Call relations**: ConversationObjects.status calls this after _exchange. It uses _visible to build the allowed-row query and workspace_tx to run it, helping status avoid returning transcript information if visibility changed midway.

*Call graph*: calls 1 internal fn (_visible); called by 1 (status); 3 external calls (exists, select, workspace_tx).


##### `ConversationObjects._find`  (lines 235–241)

```
async def _find(self, subjects: frozenset[str], name: str) -> sa.Row | None
```

**Purpose**: Looks up one visible conversation by UUID-like name. It quietly rejects names that are not valid UUIDs.

**Data flow**: It receives a set of readable subjects and a name string. It tries to parse the name as a UUID; if parsing fails, it returns nothing. If parsing succeeds, it asks _rows for matching visible rows with that id and returns the first row if one exists.

**Call relations**: This helper is used by get, member_detail, and status whenever they need the normal visibility-checked lookup. It delegates the actual database read to _rows.

*Call graph*: calls 1 internal fn (_rows); called by 3 (get, member_detail, status); 1 external calls (UUID).


##### `ConversationObjects._rows`  (lines 243–250)

```
async def _rows(self, subjects: frozenset[str], *, conversation_id: UUID | None) -> tuple[sa.Row, ...]
```

**Purpose**: Reads conversation database rows for the selected agent that match the caller’s visible subjects. It can read either all visible rows or one specific conversation id.

**Data flow**: It receives a set of subjects and an optional conversation id. It builds the visible-conversations query, adds an id condition when requested, opens a workspace database transaction, executes the query, and returns the resulting rows as a tuple.

**Call relations**: ConversationObjects.list uses this to list all ordinary visible conversations, and _find uses it to look up one. It builds on _visible, which supplies the shared visibility filter.

*Call graph*: calls 1 internal fn (_visible); called by 2 (_find, list); 1 external calls (workspace_tx).


##### `ConversationObjects._widens_for_admin`  (lines 252–255)

```
async def _widens_for_admin(self, ctx: ToolContext) -> bool
```

**Purpose**: Decides whether the current speaker may see extra private conversation metadata. The widening is only for local workspace admins, never for foreign or shared-channel audiences.

**Data flow**: It receives the tool context. It first checks whether the context audience begins with the foreign-audience prefix; if so, it returns false. Otherwise it asks the context whether the speaker is an admin and returns that answer.

**Call relations**: ConversationObjects.list calls this before adding private rows, and _private_find calls it before trying a private lookup. It is the single gatekeeper for the admin-only metadata exception.

*Call graph*: calls 1 internal fn (speaker_is_admin); called by 2 (_private_find, list).


##### `ConversationObjects._private_find`  (lines 257–265)

```
async def _private_find(self, ctx: ToolContext, name: str) -> sa.Row | None
```

**Purpose**: Looks up one private conversation metadata row for an admin-only case. It does not expose transcript content; it only supports finding the row so get can return metadata.

**Data flow**: It receives the tool context and name. It first checks whether admin widening is allowed; if not, it returns nothing. It then parses the name as a UUID, returning nothing if invalid, asks _private_rows for that id, and returns the first matching row if present.

**Call relations**: ConversationObjects.get calls this only after the ordinary visible lookup fails. It depends on _widens_for_admin for permission and _private_rows for the restricted database query.

*Call graph*: calls 2 internal fn (_private_rows, _widens_for_admin); called by 1 (get); 1 external calls (UUID).


##### `ConversationObjects._private_rows`  (lines 267–277)

```
async def _private_rows(self, ctx: ToolContext, *, conversation_id: UUID | None) -> tuple[sa.Row, ...]
```

**Purpose**: Reads private conversation metadata rows for the selected agent that are outside the caller’s normal readable subjects. This supports the narrow admin visibility exception while keeping content hidden.

**Data flow**: It receives the tool context and an optional conversation id. It builds a query for the current agent’s conversations whose audience looks like a member-private subject and is not already readable by the caller. It optionally narrows to one id, runs the query in a workspace transaction, and returns the rows.

**Call relations**: ConversationObjects.list uses this when an admin explicitly filters for private rows, and _private_find uses it for a single private metadata lookup. It starts from _agent_conversations so it stays scoped to the current workspace and selected agent.

*Call graph*: calls 1 internal fn (_agent_conversations); called by 2 (_private_find, list); 1 external calls (workspace_tx).


##### `_agent_conversations`  (lines 280–301)

```
def _agent_conversations() -> sa.Select
```

**Purpose**: Builds the base database query for conversations belonging to the current workspace and selected agent. Other helpers add visibility or privacy filters on top of this shared starting point.

**Data flow**: It reads the current workspace id and selected agent id, then constructs a SQL query joining conversations to their agent row. The selected columns include conversation identity, surface details, audience, timestamps, and the agent’s current or archived name. It returns the query object, not the rows themselves.

**Call relations**: _visible calls this to make the ordinary visible-conversations query, and _private_rows calls it to make the admin-private metadata query. It is the common foundation that keeps all reads tied to the same agent and workspace.

*Call graph*: called by 2 (_private_rows, _visible); 3 external calls (select, object_agent_id, ws_current).


##### `_visible`  (lines 304–305)

```
def _visible(subjects: frozenset[str]) -> sa.Select
```

**Purpose**: Adds the normal audience visibility rule to the base conversation query. A conversation is visible when its audience is one of the caller’s allowed subjects.

**Data flow**: It receives a frozen set of subject strings. It starts with the current agent’s conversations from _agent_conversations and adds a condition requiring the conversation audience to be in that subject set. It returns the filtered query.

**Call relations**: _rows uses this for ordinary reads, and _unchanged_visible uses it for the post-transcript safety check. It is the central expression of the file’s normal access rule.

*Call graph*: calls 1 internal fn (_agent_conversations); called by 2 (_rows, _unchanged_visible).


##### `_member_row`  (lines 308–326)

```
def _member_row(entry: ListedConversation, *, mine: bool) -> ObjectRow
```

**Purpose**: Formats a portal directory entry into a compact row for conversation lists. It includes friendly fields such as title, whether it is the member’s own conversation, the other speaker, surface information, portal support, and last activity time.

**Data flow**: It receives a listed conversation entry and a boolean saying whether it is “mine.” It chooses speaker names from sender or email, chooses the last activity timestamp, computes whether the surface is the portal or an extension-opened surface, and builds an ObjectRow with these fields. The output is a display-ready row.

**Call relations**: ConversationObjects.member_page calls this for every titled conversation returned by ConversationDirectory. The resulting rows are then paged and returned to the portal.

*Call graph*: called by 1 (member_page); 1 external calls (__init__).


##### `_row`  (lines 329–344)

```
def _row(row: sa.Row, *, private: bool=False) -> ObjectRow
```

**Purpose**: Formats a database conversation row into a simple object-list row. It gives the row a readable summary such as where the conversation happened and when it was created.

**Data flow**: It receives a database row and an optional private flag. It builds an origin phrase from the surface and surface label, prepares fields for the surface, optional surface label, and optional private marker, and returns an ObjectRow named by the conversation id.

**Call relations**: ConversationObjects.list uses this for ordinary and private metadata listings, and member_detail uses it as the row half of a portal detail response. It is the standard formatter for database-backed conversation rows.

*Call graph*: called by 2 (list, member_detail); 1 external calls (__init__).


##### `_detail`  (lines 347–359)

```
def _detail(row: sa.Row) -> ObjectDetail[ConversationSpec]
```

**Purpose**: Turns a database conversation row into the full detail object used by get and portal detail reads. It includes the conversation spec, timestamps, and a link back to the agent the conversation belongs to.

**Data flow**: It receives a database row. It copies surface, surface label, and audience into a ConversationSpec, copies created and updated timestamps, and creates a scoped-to link pointing at the agent name. It returns an ObjectDetail containing all of that.

**Call relations**: ConversationObjects.get and member_detail call this after they have found a row the caller may see. It packages raw database values into the object-system shape expected by callers.

*Call graph*: called by 2 (get, member_detail); 4 external calls (__init__, __init__, __init__, __init__).


### `core/src/ufo/host/kinds/credential_kind.py`

`domain_logic` · `request handling`

Some extensions need outside secrets, such as API keys, but those secrets must not be displayed through normal reads. This file defines the workspace object kind called `credential`, where each object is a declared slot from an installed extension. Think of it like a row of labeled safety deposit boxes: everyone can see the labels and whether a box has something inside, but nobody can inspect the contents through this interface.

The slot declaration comes from the extension manifest: its name, description, extension name, and where the credential will be injected. The database only records whether a value has been stored, plus timestamps for that stored value. If a slot is empty, there is no database row for it, but the slot still appears because the declaration still exists.

The file supports listing slots, reading one slot's public details, checking fill status, and clearing a stored value. It deliberately refuses create and update because filling or rotating a credential involves a private secret handoff through a separate `request_credentials` action. Deleting does not remove the slot declaration; it only clears the saved secret and requires a workspace admin.

#### Function details

##### `CredentialObjects.list`  (lines 77–78)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Builds the normal workspace list of credential slots. It shows every declared slot and whether it is filled, but not the stored credential value.

**Data flow**: It receives a tool context and a list query with paging, filtering, or ordering instructions. It asks `_rows` to build the visible slot rows, then passes those rows through `object_page` so the caller receives the requested page of results.

**Call relations**: When the object system needs to list credentials, this is the public entry for that read. It delegates the sensitive part to `_rows`, which only reports fill state, then hands the rows to the shared paging helper.

*Call graph*: calls 1 internal fn (_rows); 1 external calls (object_page).


##### `CredentialObjects.member_page`  (lines 80–90)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Builds the member-facing list of credential slots for the portal. Members see the same workspace-wide slot declarations and fill states as the tool-side list.

**Data flow**: It receives optional extension context, the member identity, whether the member is an admin, and the list query. It does not narrow the result by member; it asks `_rows` for all declared workspace slots and turns them into a page with `object_page`.

**Call relations**: This is the portal version of listing credentials. Like `list`, it relies on `_rows`, so both paths share the same rule: show declarations and filled-or-empty status, never secret values.

*Call graph*: calls 1 internal fn (_rows); 1 external calls (object_page).


##### `CredentialObjects.get`  (lines 92–93)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[CredentialSpec] | None
```

**Purpose**: Reads the public detail for one credential slot. It returns the slot declaration and timestamps if a value exists, but never returns the secret.

**Data flow**: It receives a tool context and a slot name. It passes the name to `_detail`, which looks up the declared slot and any stored row timestamps, then returns that detail or `None` if the slot is not declared.

**Call relations**: This is the tool-side single-object read. It keeps the public method small by handing the actual lookup and safe rendering to `_detail`.

*Call graph*: calls 1 internal fn (_detail).


##### `CredentialObjects.member_detail`  (lines 95–110)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[CredentialSpec] | None
```

**Purpose**: Reads one credential slot for a signed-in member. It combines the row-style summary with the detailed declaration so the portal can show a complete safe view.

**Data flow**: It receives optional extension context, a slot name, member identity, and admin status. It asks `_detail` for the safe declaration; if no such slot exists, it returns `None`. Otherwise it asks `_rows` for the list rows, finds the row for this slot, and wraps the row and detail in a `MemberObject`.

**Call relations**: This is the portal version of `get`. It uses `_detail` for the safe slot details and `_rows` for the same filled-or-empty row shown in listings, then packages both together for member-facing display.

*Call graph*: calls 2 internal fn (_detail, _rows); 1 external calls (__init__).


##### `CredentialObjects.status`  (lines 112–136)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns a small status report for one credential slot, mainly whether it is filled. If the slot has a host setting and a credential store is available, it also reports the resolved host.

**Data flow**: It receives a tool context, slot name, and optional expected generation value. It first checks the declared slot names. If the slot is unknown, it returns `None`. If known, it queries the current workspace's credential table for a matching row, turns that into `filled: true` or `filled: false`, and may add a host value by asking the credential store.

**Call relations**: Status is used when callers need a compact check rather than a full object detail. It relies on `_named` to confirm the slot declaration, uses the workspace transaction and current workspace ID to read the database, and may hand off to `credential_host` to resolve where the credential will be injected.

*Call graph*: calls 1 internal fn (_named); 4 external calls (select, workspace_tx, credential_host, ws_current).


##### `CredentialObjects.apply`  (lines 138–147)

```
async def apply(self, ctx: ToolContext, name: str, spec: CredentialSpec, old: CredentialSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create, fill, or update a credential through the object interface. This protects secrets by forcing filling and rotation through the separate private credential request flow.

**Data flow**: It receives the requested slot name, proposed credential spec, possible old spec, and optional expected generation value. Instead of writing anything, it immediately raises `VerbNotSupported` with an explanation.

**Call relations**: When the object system treats create or update as an apply operation, this method blocks it. It does not call storage helpers because this file intentionally does not accept secret values here.

*Call graph*: 1 external calls (__init__).


##### `CredentialObjects.delete`  (lines 149–165)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Clears the stored value for a credential slot. It does not remove the declared slot itself, so the slot will still appear afterward as empty.

**Data flow**: It receives a tool context, slot name, and optional expected generation value. It asks the context to prove the speaker is a workspace admin. If that check fails, it raises `AdminRequired`. If allowed, it finds the declared slot name and deletes the matching credential row for the current workspace from the database.

**Call relations**: This is the one write operation this object kind permits. It uses `_named` to map the public object name to the declared slot, relies on `ToolContext.require_speaking_admin` for the permission gate, then uses a workspace transaction and the current workspace ID to remove the stored secret row.

*Call graph*: calls 2 internal fn (_named, require_speaking_admin); 4 external calls (__init__, delete, workspace_tx, ws_current).


##### `CredentialObjects._rows`  (lines 167–183)

```
async def _rows(self) -> tuple[ObjectRow, ...]
```

**Purpose**: Builds the safe list rows for all declared credential slots. Each row says which extension declared the slot and whether it is filled.

**Data flow**: It asks `_filled_slots` for the set of slot names that have stored database rows. It asks `_named` for all declared slots by name. For each declared slot, it creates an `ObjectRow` with a human-readable summary and fields for slot name, extension, and filled status.

**Call relations**: `list`, `member_page`, and `member_detail` all call this helper so every listing-style view is consistent. It combines declaration data from `_named` with fill-state data from `_filled_slots`, without reading credential values.

*Call graph*: calls 2 internal fn (_filled_slots, _named); called by 3 (list, member_detail, member_page); 1 external calls (__init__).


##### `CredentialObjects._detail`  (lines 185–209)

```
async def _detail(self, name: str) -> ObjectDetail[CredentialSpec] | None
```

**Purpose**: Builds the safe detailed view for one credential slot. It includes the slot declaration and timestamps for the stored value, if one exists.

**Data flow**: It receives a slot name and checks `_named` for a matching declaration. If there is no declared slot, it returns `None`. If there is one, it queries the current workspace's credential table for creation and update timestamps, then creates an `ObjectDetail` containing a `CredentialSpec` and those timestamps. The secret value is not selected or returned.

**Call relations**: `get` and `member_detail` use this helper for single-slot reads. It centralizes the rule that details come from the extension declaration plus safe database metadata, never from the encrypted credential contents.

*Call graph*: calls 1 internal fn (_named); called by 2 (get, member_detail); 5 external calls (__init__, __init__, select, workspace_tx, ws_current).


##### `CredentialObjects._named`  (lines 211–212)

```
def _named(self) -> dict[str, DeclaredSlot]
```

**Purpose**: Turns the stored collection of declared slots into a dictionary keyed by slot name. This makes later lookups simple and consistent.

**Data flow**: It reads `self.slots`, the declared credential slots collected from active extension manifests. It passes them to `named_slots`, which returns a name-to-slot mapping.

**Call relations**: Several methods call this before reading or changing anything: `_detail`, `_rows`, `delete`, and `status`. It is the shared doorway from a user-provided slot name to the extension's actual slot declaration.

*Call graph*: called by 4 (_detail, _rows, delete, status); 1 external calls (named_slots).


##### `CredentialObjects._filled_slots`  (lines 214–223)

```
async def _filled_slots(self) -> frozenset[str]
```

**Purpose**: Finds which credential slots currently have stored values in this workspace. It returns only slot names, not the values.

**Data flow**: It opens a workspace database transaction, uses the current workspace ID, and selects slot names from the credential table. It turns the returned rows into an immutable set of filled slot names.

**Call relations**: `_rows` calls this when building lists. This helper supplies the database-backed fill state that gets combined with extension declarations to show each slot as filled or empty.

*Call graph*: called by 1 (_rows); 3 external calls (select, workspace_tx, ws_current).


### `core/src/ufo/host/kinds/members.py`

`domain_logic` · `request handling`

A workspace needs a safe roster: a list of people who belong, who can administer it, and who is allowed to use it right now. This file is the rulebook for that roster. Without it, the agent would not know who may see membership information, who may change roles, or how an admin can invite someone new.

The main idea is that membership is sensitive. In an internal conversation with the main agent, members can see the workspace roster. In a child agent or a channel shared with another organization, visibility is narrowed, often to just the speaker’s own row. This is like showing the full staff directory in the office, but only showing your own badge details when you are in a public meeting room.

The file also protects dangerous changes. Only a speaking workspace admin using the main agent can change another member’s admin flag or seat. A “seat” means active access; unseating someone blocks future admission without deleting their record. The code also prevents removing the last admin, and prevents leaving the workspace with no seated admin.

Finally, the `add_member` tool lets an admin create a member by email, even at an outside domain, and optionally send an invitation email. Existing members are not recreated; their role or seat must be changed through the member object instead.

#### Function details

##### `MemberObjects.list`  (lines 68–72)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows a page of member rows that the current speaker is allowed to see. It is used when the agent needs to list workspace members as objects.

**Data flow**: It receives the tool context and a paging or filtering query. It asks for the rows visible to this speaker, turns each database row into a simple object row, and returns a page of those rows.

**Call relations**: This is the public list operation for member objects during a tool turn. It relies on `MemberObjects._visible_rows` to enforce the visibility rules, then uses `_row` to format each allowed member before handing the result to `object_page` for paging.

*Call graph*: calls 2 internal fn (_visible_rows, _row); 1 external calls (object_page).


##### `MemberObjects.member_page`  (lines 74–91)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows a page of member rows for portal-style reads outside a normal agent turn. It applies the portal rule: the main agent can show the roster, while other agents show only the signed-in member’s own row.

**Data flow**: It receives the signed-in member id, whether that member is an admin, and a page query. It looks up the rows that member may see through the current agent, formats them as rows, and returns a paged result.

**Call relations**: This is used for portal reads rather than conversational tool calls. It calls `MemberObjects._member_rows`, then formats through `_row` and pages through `object_page`, keeping portal reads separate from the wider permissions an admin may have during a live turn.

*Call graph*: calls 2 internal fn (_member_rows, _row); 1 external calls (object_page).


##### `MemberObjects.get`  (lines 93–95)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[MemberSpec] | None
```

**Purpose**: Fetches one visible member object by name, where the name is the member id as text. It returns details only if the current speaker is allowed to see that member.

**Data flow**: It receives the tool context and requested member name. It searches the speaker’s visible rows for that name; if found, it turns the row into detailed member data, otherwise it returns nothing.

**Call relations**: This is the normal object detail read during a tool turn. It delegates permission filtering to `MemberObjects._visible_row` and uses `_detail` only after a row has passed those visibility checks.

*Call graph*: calls 2 internal fn (_visible_row, _detail).


##### `MemberObjects.member_detail`  (lines 97–113)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[MemberSpec] | None
```

**Purpose**: Fetches one member detail for portal-style reads. It follows the portal visibility rule instead of the live-turn admin widening rule.

**Data flow**: It receives the requested member name and the signed-in member id. It gets the rows visible to that member through the current agent, finds the matching id, and returns both a summary row and detailed spec if present.

**Call relations**: This is paired with `MemberObjects.member_page` for portal access. It uses `MemberObjects._member_rows` to decide what the portal reader may see, then combines `_row` and `_detail` into a `MemberObject` response.

*Call graph*: calls 3 internal fn (_member_rows, _detail, _row); 1 external calls (__init__).


##### `MemberObjects.status`  (lines 115–130)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns a small status snapshot for a visible member: their email and whether they are seated. This is useful when a caller needs quick state rather than the full editable detail.

**Data flow**: It receives the current tool context and member name. It finds that member among the rows visible to the speaker and, if found, returns a small dictionary with email and seated state; otherwise it returns nothing.

**Call relations**: This follows the same visibility path as `MemberObjects.get` by calling `MemberObjects._visible_row`. It does not build a full object detail because it is meant to answer a narrower status question.

*Call graph*: calls 1 internal fn (_visible_row).


##### `MemberObjects.apply`  (lines 132–208)

```
async def apply(self, ctx: ToolContext, name: str, spec: MemberSpec, old: MemberSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Changes an existing member’s admin role and/or seat, but only when a workspace admin is speaking through the main agent. It is the protected path for granting or removing administrative power and active access.

**Data flow**: It receives the tool context, member id text, the desired member spec, and the previous spec if supplied. It checks that there is a speaker, that the current agent is the main agent, that the target member exists, and that the speaker is an admin. It then grants or revokes the member’s seat if needed, checks that the workspace will still have an admin and a seated admin, updates the admin flag if needed, and writes the changes to the database.

**Call relations**: This is the write operation behind applying changes to a member object. It calls context permission checks, opens a workspace transaction, uses seat helpers when access changes, consults admin-checking logic, and raises clear errors when the caller is not allowed or the requested member cannot be found.

*Call graph*: calls 2 internal fn (agent_is_main, require_speaker); 10 external calls (__init__, __init__, __init__, __init__, select, update, workspace_tx, member_is_admin, ws_current, UUID).


##### `MemberObjects.delete`  (lines 210–217)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses deletion of member objects. Members can be unseated to remove access, but their membership record is not deleted through this object interface.

**Data flow**: It receives a context and member name, but does not inspect or change the database. It immediately raises an error explaining that deletion is not supported.

**Call relations**: This completes the member object interface by making the delete behavior explicit. Any caller trying to delete a member is stopped here and directed toward the supported model: change the seat instead.

*Call graph*: 1 external calls (__init__).


##### `MemberObjects._visible_rows`  (lines 219–225)

```
async def _visible_rows(self, ctx: ToolContext) -> tuple[sa.Row, ...]
```

**Purpose**: Decides which member rows the current conversational tool call may see. It centralizes the privacy rule for roster visibility.

**Data flow**: It reads the speaker member id, the audience, whether the current agent is the main agent, and whether the speaker is an admin. If there is no speaker it returns no rows; in an externally shared audience it returns only the speaker; otherwise it returns the whole roster only for the main agent or an admin, and the speaker’s own row for others.

**Call relations**: This helper is called by `MemberObjects.list` and by `MemberObjects._visible_row`. It hands the final database lookup to `MemberObjects._roster`, passing along whether the whole roster or only one row should be returned.

*Call graph*: calls 3 internal fn (_roster, agent_is_main, speaker_is_admin); called by 2 (_visible_row, list).


##### `MemberObjects._visible_row`  (lines 227–231)

```
async def _visible_row(self, ctx: ToolContext, name: str) -> sa.Row | None
```

**Purpose**: Finds one requested member inside the set of rows the current speaker is allowed to see. It prevents detail and status reads from bypassing list visibility rules.

**Data flow**: It receives the tool context and member name. It first gets all rows visible to the speaker, then returns the row whose id matches the name, or nothing if there is no match.

**Call relations**: This is the shared lookup used by `MemberObjects.get` and `MemberObjects.status`. Because it calls `MemberObjects._visible_rows`, single-object reads obey the same privacy rules as list reads.

*Call graph*: calls 1 internal fn (_visible_rows); called by 2 (get, status).


##### `MemberObjects._member_rows`  (lines 233–245)

```
async def _member_rows(self, member_id: UUID) -> tuple[sa.Row, ...]
```

**Purpose**: Decides which rows a signed-in member may read through the portal for the current agent. It separates portal access from live conversation access.

**Data flow**: It receives a member id. It checks in the database whether the current agent is the workspace’s main agent, then asks for either the whole roster or just that member’s own row based on that result.

**Call relations**: This helper supports `MemberObjects.member_page` and `MemberObjects.member_detail`. It calls `MemberObjects._roster` after checking the current agent, so portal pages and portal details share one consistent source of roster data.

*Call graph*: calls 1 internal fn (_roster); called by 2 (member_detail, member_page); 4 external calls (select, workspace_tx, agent_current, ws_current).


##### `MemberObjects._roster`  (lines 247–267)

```
async def _roster(self, member_id: UUID, *, whole: bool) -> tuple[sa.Row, ...]
```

**Purpose**: Reads member rows from the database, either for the whole workspace or only for one member. It is the common database query behind the visible roster.

**Data flow**: It receives a member id and a choice of whole roster versus single row. It builds a database query for the current workspace, optionally narrows it to the given member, orders rows by email, and returns the matching database rows.

**Call relations**: This is the lowest-level read helper for roster data in this file. Both `MemberObjects._visible_rows` and `MemberObjects._member_rows` call it after they have decided how much the reader is allowed to see.

*Call graph*: called by 2 (_member_rows, _visible_rows); 3 external calls (select, workspace_tx, ws_current).


##### `_row`  (lines 270–283)

```
def _row(row: sa.Row) -> ObjectRow
```

**Purpose**: Turns a database member row into the short summary form shown in member lists. It makes raw database fields understandable to object-list callers.

**Data flow**: It receives a database row containing the member id, email, admin flag, and seat state. It returns an object row with a text name, a readable summary, and structured fields for email, admin, and seated.

**Call relations**: This formatter is used by `MemberObjects.list`, `MemberObjects.member_page`, and `MemberObjects.member_detail`. Those functions first decide what may be seen, then call `_row` to present each allowed member consistently.

*Call graph*: called by 3 (list, member_detail, member_page); 1 external calls (__init__).


##### `_detail`  (lines 286–291)

```
def _detail(row: sa.Row) -> ObjectDetail[MemberSpec]
```

**Purpose**: Turns a database member row into the detailed editable shape for a member object. It captures the two fields that can be applied: admin and seated.

**Data flow**: It receives a database row with admin status, seat status, and timestamps. It creates a `MemberSpec` from the row and returns object detail including creation and update times.

**Call relations**: This formatter is used by `MemberObjects.get` and `MemberObjects.member_detail` after permission checks have already passed. It pairs with `_row`: `_row` is for summaries, while `_detail` is for full object detail.

*Call graph*: called by 2 (get, member_detail); 2 external calls (__init__, __init__).


##### `AddMember.add`  (lines 328–359)

```
async def add(self, ctx: ToolContext, args: AddMemberInput) -> ToolResult
```

**Purpose**: Adds a new person to the workspace by email before they have contacted the agent. Only a workspace admin using the main agent in an internal conversation can do this.

**Data flow**: It receives the tool context and requested email, admin flag, and notification choice. It verifies there is a speaker, checks the main-agent and internal-room rules, normalizes and validates the email, locks the workspace row for a safe update, confirms the speaker is an admin, confirms the email is not already a member, creates the member, and returns a message saying what happened.

**Call relations**: This is the handler behind the `add_member` tool definition. It calls `AddMember._absent` before creating the member, uses the shared member-creation helper for the actual database insert, and returns a tool result that the agent can show back to the user.

*Call graph*: calls 3 internal fn (_absent, agent_is_main, require_speaker); 9 external calls (__init__, __init__, __init__, select, workspace_tx, create_member, email_domain, member_is_admin, ws_current).


##### `AddMember._absent`  (lines 361–374)

```
async def _absent(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Checks that an email address is not already a member of the current workspace. It prevents duplicate member records and tells the caller to edit the existing member instead.

**Data flow**: It receives an open database connection and a normalized email address. It searches the current workspace for a member with that email, ignoring case; if one exists, it raises an error, and if not, it returns without changing anything.

**Call relations**: This helper is called by `AddMember.add` after permission checks and before member creation. It acts like a final duplicate check at the door before the new member row is minted.

*Call graph*: called by 1 (add); 3 external calls (execute, select, ws_current).


### `core/src/ufo/host/kinds/surface_kind.py`

`domain_logic` · `request handling for workspace object reads`

A "surface" is a place where chat can happen, such as a web chat, Slack-like provider, or another extension-declared channel. Extensions declare these surfaces in their manifests, but the core system is the only place that can see all manifests and the shared installation database. This file joins those two worlds: the declared list of possible surfaces and the workspace-specific records showing which ones are actually connected.

The file first turns extension manifests into a clean map of registered surfaces. It checks that every surface name is a valid object name and that two extensions do not claim the same name. That prevents a confusing situation where one surface silently hides another.

The main class, `SurfaceObjects`, presents these surfaces through the workspace object system. It can list surfaces, show details for one surface, and report status such as whether it is bound, which agent it runs as, whether that agent was archived, and whether inbound traffic should route through that binding. It refuses create, update, and delete operations because surfaces are not authored by users here. Think of this file like a notice board: it shows what doors exist and which doors are connected, but you cannot build or remove doors from the notice board itself.

It also hides surface information from foreign audiences and limits member-facing listings to admins, because transport setup is considered administrative workspace state.

#### Function details

##### `registered_surfaces`  (lines 54–79)

```
def registered_surfaces(manifests: tuple[Manifest, ...]) -> dict[str, RegisteredSurface]
```

**Purpose**: Builds the system's registry of chat surfaces from the active extension manifests. It also protects startup from bad or conflicting surface names, so the rest of the system can treat each surface name as unique and safe to use as an object address.

**Data flow**: It receives a tuple of extension manifests. For each declared surface, it checks the surface name against the object-name rules, rejects invalid names with an error that names the extension, rejects duplicate names across extensions, and stores the surface's declaring extension plus routing flags. It returns a dictionary keyed by surface name, where each value describes that registered surface.

**Call relations**: This is the setup step that prepares the data later used by `SurfaceObjects`. It calls the shared object-name validator before creating `RegisteredSurface` records, so the read-only object layer starts from a clean, unambiguous registry.

*Call graph*: 3 external calls (__init__, __init__, validate_object_name).


##### `SurfaceObjects.list`  (lines 114–117)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Returns a page of surface rows for an internal tool conversation. It shows every registered surface together with whether this workspace has bound an installation to it.

**Data flow**: It reads the tool context to see who is asking. If the audience is foreign, it returns an empty page. Otherwise it reads the workspace's surface installations from the database, turns the registered surfaces plus those installation records into display rows, and wraps them in a paged result using the requested list options.

**Call relations**: This is one of the public read paths for the object kind. It asks `_installations` for current binding state, asks `_rows` to build the visible rows, and hands those rows to `object_page` so the wider object system can paginate them.

*Call graph*: calls 2 internal fn (_installations, _rows); 1 external calls (object_page).


##### `SurfaceObjects.member_page`  (lines 119–131)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Returns the surface index as seen from the member portal, but only for admins. Non-admin members see no rows because surface installation state is workspace transport setup, not ordinary member content.

**Data flow**: It receives member and admin information plus a list query. If the member is not an admin, it returns an empty page. If the member is an admin, it reads installation bindings, builds rows for all registered surfaces, and returns a paged list.

**Call relations**: This mirrors `SurfaceObjects.list` for the member-facing portal. When allowed, it follows the same path through `_installations`, `_rows`, and `object_page`; when not allowed, it stops early.

*Call graph*: calls 2 internal fn (_installations, _rows); 1 external calls (object_page).


##### `SurfaceObjects.get`  (lines 133–138)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SurfaceObjectSpec] | None
```

**Purpose**: Returns the detailed read-only description of one surface for an internal tool conversation. It only answers for known registered surfaces and internal audiences.

**Data flow**: It receives a context and a surface name. If the request comes from a foreign audience, or if the name is not registered, it returns nothing. Otherwise it reads the workspace's installation bindings and builds an object detail containing the surface declaration and any binding timestamps.

**Call relations**: This is the single-object read path for tools. It delegates the database lookup to `_installations` and the final response shape to `_detail`.

*Call graph*: calls 2 internal fn (_detail, _installations).


##### `SurfaceObjects.member_detail`  (lines 140–156)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[SurfaceObjectSpec] | None
```

**Purpose**: Returns one surface for the member portal, combining the list-style row and the full detail view. It only answers for admins and for registered surface names.

**Data flow**: It receives member/admin information and a surface name. If the member is not an admin or the surface is unknown, it returns nothing. Otherwise it reads installation bindings once, builds a summary row and a detailed spec from the same data, and returns them together as a member object.

**Call relations**: This is the member-facing counterpart to `get`, with an added row summary. It calls `_installations` once, then passes the same installed-state map to `_row` and `_detail` so both parts of the answer agree.

*Call graph*: calls 3 internal fn (_detail, _installations, _row); 1 external calls (__init__).


##### `SurfaceObjects.status`  (lines 158–177)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports the live binding status of one registered surface. It tells callers whether the workspace has an installation for that surface and, when it does, which agent the surface runs as and whether inbound routing is enabled.

**Data flow**: It receives a context, a surface name, and an expected generation value that this implementation does not use. Foreign audiences and unknown surfaces get no answer. For a known internal surface, it reads installation bindings; if none exists for that name it returns `bound: false`, and if one exists it returns `bound: true` plus agent, archived, and routes-ingress information.

**Call relations**: This status endpoint is a lightweight read path focused on binding state rather than full object details. It relies on `_installations` for the workspace database view and then formats a small status dictionary directly.

*Call graph*: calls 1 internal fn (_installations).


##### `SurfaceObjects.apply`  (lines 179–188)

```
async def apply(self, ctx: ToolContext, name: str, spec: SurfaceObjectSpec, old: SurfaceObjectSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create or update a surface object. Surfaces are declared by extensions and connected through the surface's own setup flow, so changing them through this object API would be the wrong doorway.

**Data flow**: It receives the requested name, new spec, old spec, context, and expected generation. It does not inspect or save those values. Instead it raises a `VerbNotSupported` error with an explanation that setup belongs to the surface's own flow.

**Call relations**: This is the write path that the object system may call when someone tries to apply changes. It intentionally stops the flow immediately and hands back a clear refusal rather than modifying manifests or installation rows.

*Call graph*: 1 external calls (__init__).


##### `SurfaceObjects.delete`  (lines 190–197)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to delete a surface object. A surface disappears when its declaring extension is removed, not when someone deletes an object record.

**Data flow**: It receives the context, surface name, and expected generation. It does not delete database rows or change the registry. It raises a `VerbNotSupported` error explaining that registration belongs to the extension manifest.

**Call relations**: This protects the read-only nature of this object kind. If the wider object system routes a delete request here, the function ends that request with a clear unsupported-verb error.

*Call graph*: 1 external calls (__init__).


##### `SurfaceObjects._rows`  (lines 199–203)

```
def _rows(self, installed: Mapping[str, _BoundInstallation]) -> tuple[ObjectRow, ...]
```

**Purpose**: Builds the complete set of list rows for registered surfaces. It keeps the listing ordered by surface name so readers get a stable, predictable view.

**Data flow**: It receives the current map of installed bindings. It walks through the registered surfaces in sorted name order, asks `_row` to build one display row for each, and returns all rows as a tuple.

**Call relations**: `list` and `member_page` call this after reading installation state. `_rows` is the small assembly line that repeatedly uses `_row` so both listing entry points show the same summaries and fields.

*Call graph*: calls 1 internal fn (_row); called by 2 (list, member_page).


##### `SurfaceObjects._row`  (lines 205–228)

```
def _row(self, name: str, registered: RegisteredSurface, installed: Mapping[str, _BoundInstallation]) -> ObjectRow
```

**Purpose**: Creates the compact list-row view for one surface. The row tells readers which extension declared the surface and whether it is currently bound to an agent.

**Data flow**: It receives a surface name, the registered declaration for that surface, and the map of installed bindings. It checks whether this surface has a binding. If not, it writes a summary like "extension: not bound"; if yes, it writes the bound agent name and marks archived agents in the text. It returns an `ObjectRow` with filterable fields such as extension, addressed, durable, home, and bound.

**Call relations**: `_rows` uses this for every registered surface, and `member_detail` uses it when building the combined member view for one surface. It is the shared formatter for the short, table-like version of a surface.

*Call graph*: called by 2 (_rows, member_detail); 1 external calls (__init__).


##### `SurfaceObjects._detail`  (lines 230–245)

```
def _detail(self, name: str, installed: Mapping[str, _BoundInstallation]) -> ObjectDetail[SurfaceObjectSpec]
```

**Purpose**: Creates the full detail view for one registered surface. It shows the extension's declaration and, when there is an installation binding, the binding's creation and update times.

**Data flow**: It receives a surface name and the current installed-binding map. It looks up the registered declaration, checks whether the surface is bound, builds a `SurfaceObjectSpec` from the manifest-derived data, and returns an `ObjectDetail`. If the surface is not bound, the timestamps are empty; if it is bound, they come from the installation row.

**Call relations**: `get` uses this for internal single-object reads, and `member_detail` uses it for the admin portal's detail section. It is the shared formatter for the full, read-only declaration view.

*Call graph*: called by 2 (get, member_detail); 2 external calls (__init__, __init__).


##### `SurfaceObjects._installations`  (lines 247–278)

```
async def _installations(self) -> dict[str, _BoundInstallation]
```

**Purpose**: Reads the workspace's current surface installation bindings from the database. This is what connects the static manifest declarations to the live workspace state.

**Data flow**: It opens a workspace database transaction, looks at the current workspace ID, and selects surface installation rows joined to their agents. For each row, it keeps the surface name, whether ingress routes through it, timestamps, the agent's current or archived name, and whether the agent is archived. It returns a dictionary keyed by surface name, with `_BoundInstallation` values.

**Call relations**: All read paths that need binding state call this: `list`, `member_page`, `get`, `member_detail`, and `status`. It is the single database-reading helper, which keeps the rest of the class focused on deciding access and shaping responses.

*Call graph*: called by 5 (get, list, member_detail, member_page, status); 4 external calls (__init__, select, workspace_tx, ws_current).


### `core/src/ufo/host/kinds/workspace_kind.py`

`domain_logic` · `request handling`

A workspace is the shared home for members. This file makes that workspace visible through the project’s object system, but only as something to read. The important idea is that the workspace does not have user-written settings here. Its facts are derived from other records: how many members exist, how many currently have access through a seat, and who those people are.

Think of it like a building lobby directory. The directory can tell you who is in the building and who has a badge, but you do not edit the building by editing the directory. To grant or remove access, you change the member record instead.

The file defines an empty `WorkspaceSpec`, meaning callers have no fields they are allowed to fill in. It defines `WorkspaceShape`, a snapshot of the useful current facts. `WorkspaceObjects` then answers the supported object actions: listing the single workspace, reading its detail, and returning status. It also blocks create/update and delete requests with clear messages.

There is one important privacy rule. External shared conversations see no workspace information. Internal users can see workspace status, but the full roster is only shown when a member is talking to the main agent. A child agent only reveals the speaker’s own roster row.

#### Function details

##### `WorkspaceObjects.list`  (lines 73–86)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows the workspace as a single list row, with a short summary of how many members exist and how many are seated. It returns nothing for an external audience, so workspace membership is not exposed in shared outside channels.

**Data flow**: It receives a tool context and a list query. If the audience is external, it turns an empty set into a page. Otherwise it reads the current workspace id, asks `_shape` for the latest counts, builds one row named with the workspace id, and returns that row inside a paged result.

**Call relations**: This is called when the object system needs to list workspace objects. It depends on `_shape` for the database-backed facts, uses `ws_current` to name the one current workspace, and hands the finished row to `object_page` so normal list filtering and paging rules can be applied.

*Call graph*: calls 1 internal fn (_shape); 3 external calls (__init__, object_page, ws_current).


##### `WorkspaceObjects.get`  (lines 88–98)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[WorkspaceSpec] | None
```

**Purpose**: Returns the detail record for the one current workspace, if the caller asks for it by the correct workspace id. The detail contains timestamps and an empty spec, because there are no editable workspace fields here.

**Data flow**: It receives a tool context and an object name. If the request comes from an external audience, or if the name does not match the current workspace id, it returns nothing. Otherwise it reads the current workspace shape, creates an empty `WorkspaceSpec`, attaches the workspace creation and update times, and returns an object detail.

**Call relations**: This is used when the object system reads a specific workspace object. Like `list`, it calls `_shape` to get the saved timestamps, and it uses `ws_current` to make sure the requested name is really the active workspace.

*Call graph*: calls 1 internal fn (_shape); 3 external calls (__init__, __init__, ws_current).


##### `WorkspaceObjects.status`  (lines 100–121)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns the live status for the workspace: member count, seated count, and a roster showing each visible member’s email, seat state, and admin state. It enforces the rule that not every caller may see the full roster.

**Data flow**: It receives a tool context, workspace name, and an optional expected generation value. It ignores external audiences and wrong workspace names by returning nothing. For a valid internal request, it reads the workspace shape, checks whether the speaker is a member talking to the main agent, and then returns counts plus either the whole roster or only the speaker’s own roster entry.

**Call relations**: This is called when a caller asks for workspace status rather than just its static detail. It calls `_shape` for the member snapshot, calls `ToolContext.agent_is_main` to decide whether the full roster is allowed, and uses `ws_current` to verify the requested workspace.

*Call graph*: calls 2 internal fn (_shape, agent_is_main); 1 external calls (ws_current).


##### `WorkspaceObjects.apply`  (lines 123–132)

```
async def apply(self, ctx: ToolContext, name: str, spec: WorkspaceSpec, old: WorkspaceSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to create or update the workspace object. The file deliberately keeps workspace access changes on member objects, not on the workspace object.

**Data flow**: It receives the usual apply inputs: context, name, new spec, old spec, and optional expected generation. Instead of changing anything, it raises a `VerbNotSupported` error with a message explaining that seats must be granted or revoked on member objects.

**Call relations**: This is reached when the object system tries to apply a change to a workspace. It does not hand off to storage or validation, because mutation is not allowed for this object kind.

*Call graph*: 1 external calls (__init__).


##### `WorkspaceObjects.delete`  (lines 134–141)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to delete the workspace. A workspace is treated as permanent once created, so deletion is not part of this object kind’s behavior.

**Data flow**: It receives the context, workspace name, and optional expected generation. It makes no database changes and immediately raises a `VerbNotSupported` error explaining that the workspace is never deleted.

**Call relations**: This is called when the object system tries to delete a workspace object. It stops the flow right there with a clear error rather than passing work to any lower-level delete operation.

*Call graph*: 1 external calls (__init__).


##### `WorkspaceObjects._shape`  (lines 143–161)

```
async def _shape(self) -> WorkspaceShape
```

**Purpose**: Builds one fresh snapshot of the workspace facts used by list, get, and status. It gathers the workspace timestamps from the database and the current member/seat roster from the seats subsystem.

**Data flow**: It starts with the current workspace id. Inside a workspace database transaction, it reads the workspace row’s creation and update times, then asks `Seats` for a snapshot of all members and seat information. It turns those pieces into a `WorkspaceShape` containing counts, roster entries, and timestamps.

**Call relations**: This is the shared helper behind `WorkspaceObjects.list`, `WorkspaceObjects.get`, and `WorkspaceObjects.status`. Those public methods decide what the caller is allowed to see, while `_shape` supplies the raw current facts they all need.

*Call graph*: called by 3 (get, list, status); 5 external calls (__init__, __init__, select, workspace_tx, ws_current).


### Extension resource objects
Extension object handlers make notifications, connections, sources, memory, monitors, reports, scheduled tasks, sites, and pages available through the shared workspace object model.

### `extensions/app_notification/ufo_ext_app_notification/kind.py`

`domain_logic` · `request handling`

This file defines the “notification” object kind: the shape and rules for notifications that appear in a member’s Notification app inbox. In plain terms, it is the catalog desk for notifications. It can list what has been raised for a member, show the full details of one notification, report its status, and delete it when the member or an admin dismisses it.

The important rule is that this file is not the place where notifications are created. A notification needs to be tied to a live turn, meaning the active moment when an agent is doing work with a particular authority. Because of that, attempts to “apply” or create a notification through the normal object interface are refused with a message pointing users to the notify tool.

The file uses NotificationStore as the storage layer. It reads rows from that store, wraps them in object-system formats such as OwnedRow and ObjectDetail, and marks each notification as owned by the member it concerns. Ownership matters because the base member-readable object rules decide who may see or delete a row: the member themselves, or a workspace admin.

It also publishes NOTIFICATION_OBJECT, the object-kind registration that tells the larger system the notification kind’s name, description, fields available for listing, guidance text, data model, and backing store behavior.

#### Function details

##### `_require_ext`  (lines 49–52)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: This helper makes sure the notification code has the extension context it needs. The extension context is the project-specific bundle of services and state that lets this notification feature reach its store.

**Data flow**: It receives an ExtensionContext or nothing. If a context is present, it returns it unchanged; if it is missing, it raises an error saying the notification kind cannot work without the app_notification extension context.

**Call relations**: The listing, lookup, and delete paths call this before touching NotificationStore. It acts like a front-door check so the rest of the code does not accidentally try to read or change notifications without the required app setup.

*Call graph*: called by 3 (_delete_owned, _find, _member_rows).


##### `_owner`  (lines 55–56)

```
def _owner(row: Notification) -> ObjectOwner
```

**Purpose**: This helper turns a stored notification row into the object-system idea of an owner. For notifications, the owner is the member the notification is about, and the notification is not shared.

**Data flow**: It receives a Notification row. It reads the row’s member_id, builds an ObjectOwner with that member ID and shared set to false, and returns that owner object.

**Call relations**: The list-building function calls this for every notification row. The returned owner is then attached to each listed row so the wider object system can decide who is allowed to see or dismiss it.

*Call graph*: called by 1 (_member_rows); 1 external calls (__init__).


##### `NotificationObjects._member_rows`  (lines 68–87)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[ObjectOwner], ...]
```

**Purpose**: This builds the list view of notifications for the object system. It turns raw stored notifications into short rows with a name, summary, owner, and useful fields for filtering or sorting.

**Data flow**: It receives an optional extension context and the current member ID. It checks that the extension context exists, reads all notification rows from NotificationStore, and converts each one into an OwnedRow. Each row includes a short subject-and-body summary, ownership information, counts, producer name, triage status, delivery surface, creation time, and whether it belongs to the current member. It returns all of those listed rows as a tuple.

**Call relations**: The member-readable object framework calls this when someone lists notification objects. It asks NotificationStore for the source data, uses _owner to label each row with the right member, and hands the finished rows back to the object framework for display and permission-aware access.

*Call graph*: calls 2 internal fn (_owner, _require_ext); 2 external calls (__init__, __init__).


##### `NotificationObjects._member_object`  (lines 89–116)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: ObjectOwner, *, member_id: UUID | None) -> ObjectDetail[NotificationSpec] | None
```

**Purpose**: This builds the detail view for one notification. It gives the caller the notification’s subject and body, timestamps, and links to the conversation and agent that produced it.

**Data flow**: It receives an extension context, a notification name, an owner, and the current member ID. It looks up the matching stored notification by name. If none exists, it returns null. If it finds one, it creates a NotificationSpec containing the subject and body, adds created and updated times, and adds links showing where the notification was created and which agent it was scoped to. The result is an ObjectDetail ready for the object system to return.

**Call relations**: The object framework calls this when someone asks to get a single notification. It relies on _find to locate the stored row, then wraps that row in the standard object detail format so callers see the notification in the same style as other object kinds.

*Call graph*: calls 1 internal fn (_find); 4 external calls (__init__, __init__, __init__, __init__).


##### `NotificationObjects._status`  (lines 118–130)

```
async def _status(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: This returns machine-readable status information for one notification. It is useful when a caller needs facts such as how many times it was raised or whether a triage turn has read it.

**Data flow**: It receives a tool context, a notification name, and an owner. It uses the context’s extension data to find the stored row. If the row is missing, it returns null. If found, it returns a dictionary with occurrence count, first and last raised times, the triage turn ID if there is one, and the delivery surface.

**Call relations**: The wider tool or object flow calls this when it needs status for a notification rather than the full subject/body detail. It delegates the search to _find, then translates the stored row into simple JSON-friendly values.

*Call graph*: calls 1 internal fn (_find).


##### `NotificationObjects._apply_owned`  (lines 132–140)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: NotificationSpec, old: NotificationSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: This blocks attempts to create or update notifications through the normal object “apply” operation. Notifications must be raised by the notify tool during an agent turn, so applying a manifest here would lose the authority and turn context that make the record trustworthy.

**Data flow**: It receives the usual apply inputs: tool context, object name, desired notification spec, previous spec, and owner. It does not use them to write anything. Instead, it raises VerbNotSupported with an explanation that notifications must be raised through notify.

**Call relations**: The object framework calls this if someone tries to apply a notification object. Rather than handing off to storage, it stops the flow immediately and points the caller to the correct creation path.

*Call graph*: 1 external calls (__init__).


##### `NotificationObjects._delete_owned`  (lines 142–145)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: This dismisses one notification. Deleting a notification here means removing it from the member’s inbox once it is handled or unwanted.

**Data flow**: It receives a tool context, notification name, and owner. It looks up the stored notification. If the row exists, it asks NotificationStore to dismiss it by its internal ID. If the notification is missing, or if the store reports that dismissal failed because the row changed, it raises an error. On success, it returns nothing and the notification is dismissed in storage.

**Call relations**: The object framework calls this when an allowed member or workspace admin deletes a notification object. It uses _find to translate the public name into the stored row, checks the extension context before opening NotificationStore, and then asks the store to perform the actual dismissal.

*Call graph*: calls 2 internal fn (_find, _require_ext); 1 external calls (__init__).


##### `NotificationObjects._find`  (lines 147–151)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> Notification | None
```

**Purpose**: This is the shared lookup helper for finding one notification by its public object name. It keeps the “search all stored notification rows and pick the matching name” logic in one place.

**Data flow**: It receives an optional extension context and a notification name. It first requires a valid extension context, reads all notification rows from NotificationStore, scans them for a row whose name matches the requested name, and returns that row. If no row matches, it returns null.

**Call relations**: The detail, status, and delete flows all call this before they can work with a specific notification. It hides the store-reading and name-matching step so those higher-level functions can focus on presenting details, reporting status, or dismissing the found row.

*Call graph*: calls 1 internal fn (_require_ext); called by 3 (_delete_owned, _member_object, _status); 1 external calls (__init__).


### `extensions/connectors/ufo_ext_connectors/objects.py`

`domain_logic` · `object listing and object change requests during turns or portal use`

This file turns account connections into ordinary workspace objects so people and agents can reason about them in one consistent place. A "connection" is the real linked account, such as a provider account authorized by a member. A "connector grant" is one agent's permission to use that connection. The distinction matters: deleting a connection disconnects the account everywhere, while deleting a grant only removes one agent's access.

The file acts like a front desk with strict rules. It can list available connections and grants, show their details, and report status such as owner, host, sharing state, and attached agents. But it refuses to create a new account connection directly, because connecting an account requires a third-party consent flow. That must happen through `connect_account`.

For changes, the file checks that there is a real speaking member, because actions like disconnecting or sharing must be attributable to a person. It then calls the grants service stored on the tool context. It also guards against stale edits: if the underlying connection or grant changed while the action was being prepared, the operation fails instead of silently changing the wrong thing.

At the bottom, the two object kinds are registered with descriptions and guidance so the wider object system knows how to expose them.

#### Function details

##### `_AccountSummary.provider`  (lines 51–51)

```
def provider(self) -> str
```

**Purpose**: This protocol property says that any account summary used here must expose a provider name, such as the service or connector type. It is a shape requirement rather than working code.

**Data flow**: An object that claims to be an account summary must have this readable value. Code can then treat different summary objects the same way when naming accounts.

**Call relations**: The `_named` helper relies on this property when it builds stable object names for both connection summaries and grant summaries.


##### `_AccountSummary.account_id`  (lines 54–54)

```
def account_id(self) -> str
```

**Purpose**: This protocol property says that any account summary used here must expose the account's provider-side identifier. It lets the file name and compare accounts without caring about the exact summary class.

**Data flow**: An account summary object provides an account id. That id is combined with the provider name to form a workspace object name.

**Call relations**: The `_named` helper reads this property together with `provider` whenever connection or grant rows are converted into object names.


##### `_named`  (lines 57–58)

```
def _named(rows: tuple[SummaryT, ...]) -> dict[str, SummaryT]
```

**Purpose**: This helper gives each account summary a stable workspace object name. It is used so connections and grants can be looked up by human-readable names instead of raw database-style identifiers.

**Data flow**: It receives a tuple of summary rows. For each row, it reads the provider and account id, turns them into an object name, and returns a dictionary from that name to the original row.

**Call relations**: Both `ConnectionObjects._member_rows` and `ConnectorGrantObjects._member_rows` call this when they turn grant-service summaries into object rows for the workspace object system.

*Call graph*: called by 2 (_member_rows, _member_rows); 1 external calls (account_object_name).


##### `ConnectionObjects._member_rows`  (lines 69–86)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This lists connected accounts as workspace rows that a member can see. Each row has a name, a short summary, and ownership information used by the object system's permission checks.

**Data flow**: It asks the grants layer for current connection summaries. It names each account, formats a readable summary with provider, account id, owner email, and whether it is shared, then returns object rows with generated ownership data tied to the connection id.

**Call relations**: The member-readable object framework calls this when it needs to show available `connection` objects. It uses `_named` for consistent naming and returns rows built for the broader object system.

*Call graph*: calls 1 internal fn (_named); 3 external calls (__init__, __init__, connection_summaries).


##### `ConnectionObjects._member_object`  (lines 88–106)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[ConnectionSpec] | None
```

**Purpose**: This loads the detailed object view for one connected account. It returns the small editable specification and timestamps for that connection, or nothing if the connection no longer exists.

**Data flow**: It receives an object name and owner token, then asks for current connection summaries. It finds the row whose id matches the owner's generation value, builds a `ConnectionSpec` from provider and account id, and wraps it with creation and update times.

**Call relations**: The object system calls this after a connection row is selected or fetched. It depends on the grants summaries as the source of truth and hands back an `ObjectDetail` for display or comparison.

*Call graph*: 3 external calls (__init__, __init__, connection_summaries).


##### `ConnectionObjects._status`  (lines 108–123)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: This returns operational status for a connection, such as who owns it, whether it is shared, what host it belongs to, and which agents are using it. It is for inspection, not editing.

**Data flow**: It receives a tool context, object name, and owner token. It looks up the matching connection summary and, if found, returns a plain dictionary of status fields; if not found, it returns nothing.

**Call relations**: The object tooling calls this when someone asks for status on a `connection` object. It reads from connection summaries and does not hand off to any mutation operation.

*Call graph*: 1 external calls (connection_summaries).


##### `ConnectionObjects._apply_owned`  (lines 125–133)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ConnectionSpec, old: ConnectionSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: This deliberately refuses attempts to create or edit a connection object directly. A real account connection needs a third-party consent process, so the user must use `connect_account` instead.

**Data flow**: It receives the requested new connection spec and any old object state. Instead of applying the change, it raises a clear "verb not supported" error explaining why this path is not allowed.

**Call relations**: The object system reaches this when an apply-style operation targets a `connection`. This function stops that flow immediately and points the caller toward the correct connection flow.

*Call graph*: 1 external calls (__init__).


##### `ConnectionObjects._delete_owned`  (lines 135–145)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: This disconnects a connected account when an authorized person deletes the connection object. Deleting here is broad: it removes the account connection and therefore affects every agent that used it.

**Data flow**: It receives the tool context, object name, and owner token. It checks that the grants service is available and that there is a speaking member, then asks the grants service to disconnect the connection id; if the connection changed during the attempt, it raises an error.

**Call relations**: The object system calls this for delete operations on `connection` objects after the ownership gate has allowed the action. It hands the actual disconnect request to `ctx.grants.disconnect` with the speaking member recorded as the actor.

*Call graph*: 1 external calls (__init__).


##### `ConnectorGrantObjects._admin_can_apply`  (lines 156–157)

```
def _admin_can_apply(self, old: ConnectorGrantSpec, spec: ConnectorGrantSpec) -> bool
```

**Purpose**: This defines the one edit a workspace admin is allowed to make to someone else's connector grant: making a shared grant private. It prevents admins from broadening access or changing which account the grant points to.

**Data flow**: It compares the old grant spec with the requested new spec. It returns true only when the old grant was shared and the new grant is exactly the same except `shared` has been changed to false.

**Call relations**: The member-readable object framework uses this as part of permission checking for grant updates. The helper uses the spec model's copy operation to express the single allowed admin change.

*Call graph*: 1 external calls (model_copy).


##### `ConnectorGrantObjects._member_rows`  (lines 159–176)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This lists agent access grants as workspace rows. Each row represents one agent's permission to use a connected account.

**Data flow**: It asks the grants layer for grant summaries. It turns each grant into a stable object name, a readable summary showing provider, account id, owner, and sharing state, and owner metadata that includes whether the grant is shared and which grant id it represents.

**Call relations**: The object system calls this when it needs to show `connector_grant` objects. It mirrors the connection row listing, but reads grant summaries instead of connection summaries.

*Call graph*: calls 1 internal fn (_named); 3 external calls (__init__, __init__, grant_summaries).


##### `ConnectorGrantObjects._member_object`  (lines 178–219)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[ConnectorGrantSpec] | None
```

**Purpose**: This loads the detailed object view for one connector grant. It shows which account the grant uses, whether it is shared, and which agent the grant is scoped to.

**Data flow**: It receives a name and owner token, then finds the matching grant summary. It builds a `ConnectorGrantSpec`, adds timestamps, and attaches links: always a `scoped_to` link to the agent, and for private grants an `access_to` link back to the underlying connection.

**Call relations**: The object system calls this when fetching a specific `connector_grant`. It reads grant summaries, uses account naming for the connection link, and returns an `ObjectDetail` that the rest of the object system can display or follow.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, account_object_name, grant_summaries).


##### `ConnectorGrantObjects._status`  (lines 221–236)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: This returns live status information for one connector grant. It tells who owns the underlying account access, which host and agent are involved, and whether the grant is shared.

**Data flow**: It receives a context, object name, and owner token. It looks up the current grant summary by id and returns a plain dictionary of status fields, or nothing if the grant no longer exists.

**Call relations**: The object tooling calls this for status checks on `connector_grant` objects. It reads from grant summaries and does not change the grant.

*Call graph*: 1 external calls (grant_summaries).


##### `ConnectorGrantObjects._apply_owned`  (lines 238–288)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ConnectorGrantSpec, old: ConnectorGrantSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: This applies changes to a connector grant. It can attach an existing connection to an agent as a new grant, or change only the grant's shared/private setting; it refuses attempts to create a brand-new account connection through this route.

**Data flow**: For a new grant, it checks that the grants service and speaking member exist, then asks the grants service to attach the named provider account to the current conversation's agent with the requested sharing state. For an existing grant, it reloads the current grant, verifies the request is not trying to change the provider or account id, and if only `shared` changed, asks the grants service to update that flag.

**Call relations**: The object system calls this for create or update operations on `connector_grant` objects. It hands real mutations to `ctx.grants.attach` or `ctx.grants.set_shared`, and it raises clear errors when the caller is using the wrong path or the stored grant changed mid-edit.

*Call graph*: 5 external calls (__init__, __init__, __init__, model_copy, grant_summaries).


##### `ConnectorGrantObjects._delete_owned`  (lines 290–300)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: This revokes one agent's access to a connected account. Unlike deleting a connection, it leaves the underlying account connection and other agents' grants intact.

**Data flow**: It receives the tool context, object name, and owner token. It checks that grants are available and that a speaking member is present, then asks the grants service to revoke the grant id; if the grant changed or disappeared during the attempt, it reports that conflict.

**Call relations**: The object system calls this for delete operations on `connector_grant` objects after permission checks. It hands the revocation to `ctx.grants.revoke` with the speaking member recorded as the actor.

*Call graph*: 1 external calls (__init__).


### `extensions/gbrain/ufo_ext_gbrain/objects.py`

`domain_logic` · `request handling`

This file is the rulebook for gbrain source objects. In plain terms, it lets a user say “please remember the Markdown pages from this GitHub repo” and then lets the system track that source like a named object. Without this file, the sync machinery might still exist, but users would not have a safe, consistent way to register sources, find them later, share them with the workspace, resync them, or remove them.

The central idea is that a source’s name is not chosen freely. It is calculated from the origin: repository, branch, or folder path. This is like labeling a storage box by its contents instead of by someone’s nickname for it. If two people point at the same origin, they get the same object name.

The file also enforces trust boundaries. GitHub repositories can be registered through the object interface, but local server folders cannot; those are operator-controlled and must come from deployment configuration. Private sources belong to the member who registered them unless they are explicitly shared. Only the registrar or an admin can perform sensitive actions such as deleting or forcing a resync.

The main class, GbrainObjects, plugs these rules into UFO’s object system. Helper functions translate stored source rows into friendly object details, derive names, validate specs, and call the extension context to register, grant, resync, share, or remove real source rows.

#### Function details

##### `gbrain_source_name`  (lines 58–65)

```
def gbrain_source_name(repo: str | None, branch: str | None, root: str | None) -> str
```

**Purpose**: Creates the official object name for a gbrain source from its origin. This prevents two different names from pointing to the same repository, branch, or folder.

**Data flow**: It receives a repository name, branch name, and folder root, any of which may be missing depending on the source type. It turns those values into a stable JSON string, hashes that string, keeps the first few hexadecimal characters, and returns a name like `gbrain-1234abcd`.

**Call relations**: This is the shared naming rule used when building an origin in `_origin` and when a stored `_Registered` source reports its `name`. Those callers rely on it so stored rows and newly submitted specs settle on the same name.

*Call graph*: called by 2 (name, _origin); 2 external calls (sha256, dumps).


##### `GbrainSpec.validate_origin`  (lines 98–103)

```
def validate_origin(self) -> 'GbrainSpec'
```

**Purpose**: Checks that a requested gbrain source describes exactly one real origin. A user must choose either a GitHub repository or a local folder root, not both and not neither.

**Data flow**: It reads the fields already placed in a `GbrainSpec`. If the origin is invalid, or if a branch is given without a repository, it stops validation with a clear error; otherwise it returns the same spec as valid.

**Call relations**: This runs as part of Pydantic model validation, meaning it protects later code from receiving a malformed source request. The rest of the file can then assume the spec follows these basic rules.


##### `_origin`  (lines 113–127)

```
def _origin(spec: GbrainSpec) -> _Origin
```

**Purpose**: Turns a user-facing source spec into the internal backend choice and backend-specific configuration. It answers the question: “Is this a Git source or a folder source, and what exact name should it have?”

**Data flow**: It receives a `GbrainSpec`. If the spec names a folder root, it builds a folder configuration and derives the folder-based object name. If it names a repository, it builds a Git configuration, including the optional branch, and derives the Git-based object name. It returns all of that packaged as an `_Origin`.

**Call relations**: GbrainObjects calls this before applying a source, both to check for name conflicts in `apply` and to perform the actual registration in `_apply_owned`. It delegates the final name calculation to `gbrain_source_name`.

*Call graph*: calls 1 internal fn (gbrain_source_name); called by 2 (_apply_owned, apply); 3 external calls (__init__, __init__, __init__).


##### `_identity`  (lines 130–131)

```
def _identity(spec: GbrainSpec) -> tuple[str | None, str | None, str | None, bool]
```

**Purpose**: Extracts the fields that define whether two gbrain specs describe the same source state. It is a compact comparison key for repository, branch, root, and sharing choice.

**Data flow**: It receives a `GbrainSpec` and returns a tuple containing the repo, branch, root, and shared flag. Nothing is changed; the tuple is just easier to compare.

**Call relations**: The main apply flow uses this to recognize a no-op reapply. The resync flow also uses it to make sure a resync request is not secretly trying to edit the source at the same time.

*Call graph*: called by 2 (_resync, apply).


##### `_Registered.name`  (lines 148–149)

```
def name(self) -> str
```

**Purpose**: Reports the official object name for a source that is already stored. This keeps stored sources named by the same origin-based rule as new requests.

**Data flow**: It reads the registered source’s repository, branch, and root fields. It passes them to `gbrain_source_name` and returns the resulting name.

**Call relations**: Other helpers use this property when listing rows or looking up a registered source by name. Its use of `gbrain_source_name` ties database records back to the same naming rule used during registration.

*Call graph*: calls 1 internal fn (gbrain_source_name).


##### `_Registered.spec`  (lines 151–157)

```
def spec(self) -> GbrainSpec
```

**Purpose**: Converts a stored source row back into the public spec shape users see. This is how the object system can show what was registered.

**Data flow**: It reads the stored repository, branch, root, and subject. It converts the subject into a simple `shared` true-or-false value and returns a new `GbrainSpec` with `resync` left at its normal false value.

**Call relations**: GbrainObjects uses this when returning object details from `_member_object`. It bridges the internal storage format and the user-facing object manifest.

*Call graph*: 2 external calls (__init__, subject_shared).


##### `_Registered.summary`  (lines 159–163)

```
def summary(self) -> str
```

**Purpose**: Creates a short human-readable description of a registered source. This is used in lists and error messages so people can recognize the source.

**Data flow**: It reads whether the registered source is a folder or a GitHub repository. For a folder it returns `server directory ...`; for a repository it returns `github repository ...`, including the branch when present, and trims the result to the configured maximum length.

**Call relations**: The listing flow uses this summary in `_member_rows`, and the apply flow uses it when explaining that a private source already exists under another member.


##### `_require_ext`  (lines 166–169)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Makes sure the gbrain object code was given its extension context. The extension context is the object that knows how to read and change registered sources.

**Data flow**: It receives an optional extension context. If one is present, it returns it unchanged; if it is missing, it raises an error because the file cannot safely continue.

**Call relations**: Most operations call this before touching source storage, including lookups, listing, registration, grants, resync, and deletion. It acts like a guardrail before any real source operation happens.

*Call graph*: called by 6 (_apply_owned, _delete_owned, _grant_settled, _member_rows, _resync, _registered_named).


##### `_registered_from_ext`  (lines 172–197)

```
async def _registered_from_ext(ext: ExtensionContext) -> tuple[_Registered, ...]
```

**Purpose**: Reads all registered gbrain-related source rows from the extension context and turns them into this file’s simpler `_Registered` records.

**Data flow**: It asks the extension context for all source records. For each record, it keeps only the Git and folder backends used by gbrain, validates the backend-specific config, extracts repo, branch, or root, and returns a tuple of `_Registered` objects.

**Call relations**: Listing uses this directly through `_member_rows`, and name lookup uses it through `_registered_named`. It is the main translator between the extension’s stored source rows and this object kind’s view of them.

*Call graph*: calls 1 internal fn (sources); called by 2 (_member_rows, _registered_named); 3 external calls (__init__, model_validate, model_validate).


##### `_registered_named`  (lines 200–204)

```
async def _registered_named(ext: ExtensionContext | None, name: str) -> _Registered | None
```

**Purpose**: Finds one registered gbrain source by its official object name. It hides the scan through all source rows behind a simple name lookup.

**Data flow**: It receives an optional extension context and a name. It first requires a valid context, reads all registered gbrain sources, compares each source’s derived name with the requested name, and returns the matching `_Registered` record or `None`.

**Call relations**: Nearly every object operation uses this when it needs the real stored source behind a name: apply, grant, object detail, status, resync, and delete. It relies on `_registered_from_ext` for the raw list and `_require_ext` for safety.

*Call graph*: calls 2 internal fn (_registered_from_ext, _require_ext); called by 7 (_apply_owned, _delete_owned, _grant_settled, _member_object, _resync, _status, apply).


##### `GbrainObjects.apply`  (lines 222–253)

```
async def apply(self, ctx: ToolContext, name: str, spec: GbrainSpec, old: GbrainSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Controls what happens when someone applies a gbrain source object manifest. It separates three cases: force a resync, reapply the same source, or actually create/change a source.

**Data flow**: It receives the tool context, requested object name, new spec, any visible old spec, and an expected generation value. If `resync` is true, it routes to `_resync`. If the submitted spec is identical to the visible old one, it grants the current agent access through `_grant_settled`. Otherwise it checks whether an unseen private registration already exists for the same origin, and then passes the request to the base object apply flow.

**Call relations**: This is the top-level apply hook called by the object system. It calls `_identity`, `_origin`, `_registered_named`, `_grant_settled`, and `_resync` to enforce gbrain-specific rules before letting the parent class perform the standard permission-gated apply behavior.

*Call graph*: calls 6 internal fn (speaker_is_admin, _grant_settled, _resync, _identity, _origin, _registered_named); 3 external calls (__init__, authority_member_id, subject_shared).


##### `GbrainObjects._grant_settled`  (lines 255–270)

```
async def _grant_settled(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: Grants the current agent access to an already-settled source when a user reapplies the exact same spec. This avoids a confusing no-op where the object appears unchanged but the agent still lacks the feed.

**Data flow**: It reads the speaking member, the object owner, and the registered source row. If there is no speaking member, no owner, or the speaker is not allowed to use the source, it does nothing. Otherwise it asks the extension context to grant the source to the current turn’s agent.

**Call relations**: Only `GbrainObjects.apply` calls this, specifically for identical reapplications. It uses `_registered_named` to find the stored source and `_require_ext` before asking the extension context to perform the grant.

*Call graph*: calls 2 internal fn (_registered_named, _require_ext); called by 1 (apply).


##### `GbrainObjects._resync`  (lines 272–293)

```
async def _resync(self, ctx: ToolContext, name: str, spec: GbrainSpec, old: GbrainSpec | None) -> None
```

**Purpose**: Schedules an immediate sync of an existing source without changing its settings. It exists so users can say “sync this now” while preventing accidental edits from being bundled into that command.

**Data flow**: It receives the context, name, submitted spec, and old spec. It first confirms the submitted spec matches the current one except for the resync request. It checks that the source exists and is visible, then requires either the registering owner or a speaking admin. If allowed, it asks the extension context to schedule a sync for the source ID.

**Call relations**: `GbrainObjects.apply` sends resync requests here when `spec.resync` is true. This function uses `_identity` for the no-edit check, `_registered_named` to find the stored source, and `_require_ext` to call the scheduling operation.

*Call graph*: calls 5 internal fn (require_speaking_admin, speaker_is_admin, _identity, _registered_named, _require_ext); called by 1 (apply); 4 external calls (__init__, __init__, __init__, authority_member_id).


##### `GbrainObjects._member_rows`  (lines 295–308)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[ObjectOwner], ...]
```

**Purpose**: Builds the list entries for gbrain sources visible through the object system. Each entry includes the derived name, a short summary, and ownership information.

**Data flow**: It receives an extension context and an optional member ID. It reads all registered gbrain sources, turns each into an `OwnedRow` with an `ObjectOwner`, marks whether it is shared, and returns the rows as a tuple.

**Call relations**: This is part of the `MemberReadableObjects` listing flow. It calls `_registered_from_ext` after `_require_ext` so the base object system can later apply its normal visibility rules.

*Call graph*: calls 2 internal fn (_registered_from_ext, _require_ext); 3 external calls (__init__, __init__, subject_shared).


##### `GbrainObjects._member_object`  (lines 310–325)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: ObjectOwner, *, member_id: UUID | None) -> ObjectDetail[GbrainSpec] | None
```

**Purpose**: Builds the detailed object view for one named gbrain source. This is what lets a caller inspect the current spec and timestamps for a source.

**Data flow**: It receives the extension context, object name, owner information, and optional member ID. It looks up the registered source by name. If found, it converts it to a `GbrainSpec` and returns an `ObjectDetail` with creation and update times; if not found, it returns `None`.

**Call relations**: The object system calls this after it has identified a row the member may read. It relies on `_registered_named` to fetch the stored source and on `_Registered.spec` to present the stored data in manifest form.

*Call graph*: calls 1 internal fn (_registered_named); 1 external calls (__init__).


##### `GbrainObjects._status`  (lines 327–341)

```
async def _status(self, ctx: ToolContext, name: str, _owner: ObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns operational status for a gbrain source, such as when it will sync next and whether recent syncs have failed. This gives users or admins a health snapshot.

**Data flow**: It receives the tool context, source name, and owner. It looks up the registered source. If missing, it returns `None`; otherwise it returns a dictionary with sharing state, next sync time, error count, and, for private sources, the owner member ID when known.

**Call relations**: The object system calls this when status information is requested. It uses `_registered_named` for the stored row and converts the internal subject into a simple shared/private flag.

*Call graph*: calls 1 internal fn (_registered_named); 1 external calls (subject_shared).


##### `GbrainObjects._apply_owned`  (lines 343–381)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: GbrainSpec, old: GbrainSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: Performs the actual create-or-change work after the base object system has accepted the apply request. It registers a GitHub source, shares an existing private source when allowed, and grants the current agent access.

**Data flow**: It receives the context, requested name, spec, old spec, and owner. It requires a speaking member, refuses local folder roots, derives the official origin and name, and rejects mismatched names. If no source exists, it registers a new one with either a shared subject or the speaker’s private subject. If the source exists, it refuses unsharing, optionally flips private to shared, and grants the source to the current agent.

**Call relations**: This is called by the inherited apply flow after `GbrainObjects.apply` has done its pre-checks. It uses `_origin` for backend configuration, `_registered_named` to decide create versus update, and `_require_ext` to call extension operations such as register, share, and grant.

*Call graph*: calls 3 internal fn (_origin, _registered_named, _require_ext); 4 external calls (__init__, __init__, member_subject, subject_shared).


##### `GbrainObjects._delete_owned`  (lines 383–387)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: Deletes a registered gbrain source once the object system has confirmed the caller is allowed to delete it. Removing the source also lets the downstream page tombstone process clean up synced pages.

**Data flow**: It receives the context, source name, and owner. It looks up the registered source; if none exists, it raises an unknown-object error. If found, it asks the extension context to remove that source by ID.

**Call relations**: The base object deletion flow calls this after applying the registrar-or-admin delete rules. It uses `_registered_named` to find the stored row and `_require_ext` before performing the removal.

*Call graph*: calls 2 internal fn (_registered_named, _require_ext); 1 external calls (__init__).


### `extensions/memory/ufo_ext_memory/objects.py`

`domain_logic` · `request handling`

This file is the “display counter” for two kinds of information stored by the memory extension. The first kind is a memory item: a saved fact, note, decision, event, or similar text that can be opened by its durable id. The second kind is a profile: a short role and current focus for a workspace member, built from shared facts.

The file does not create memories or profiles. Instead, it reads rows that other jobs have already written, turns them into the object shapes the rest of UFO understands, and refuses direct edits. That matters because memory has special rules: old memories are not deleted, they may be superseded by newer ones; profile rows are written by a separate People pass; and visibility must follow the reader’s audience.

A memory list shows only live, non-retired, non-superseded memories, newest first. Opening a memory by id can still return an older superseded item, with a link to its replacement, so stale references still lead somewhere useful. If a memory was derived from a synced page, the code checks that the reader can still read that exact page revision. Profiles are simpler: because they are made only from workspace-shared facts, anyone who can read the shared subject can see all profiles, and anyone outside it sees none.

#### Function details

##### `_require_ext`  (lines 91–94)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Makes sure an extension context is present before this file tries to read the memory database. Without it, the object handlers would not know which workspace store or transaction system to use.

**Data flow**: It receives an optional extension context. If the context is missing, it stops immediately with an error; if it is present, it returns the same context unchanged.

**Call relations**: The public memory and profile methods call this first when they are invoked from tool or portal code. It acts like checking that you have the right key before opening the filing cabinet.

*Call graph*: called by 8 (get, list, member_detail, member_page, get, list, member_detail, member_page).


##### `_stamp`  (lines 97–101)

```
def _stamp(written: datetime) -> str
```

**Purpose**: Turns a stored date and time into one consistent text format for object output. This hides differences between databases, such as whether the database kept timezone information.

**Data flow**: It receives a datetime value from storage, makes it timezone-aware through the memory store helper, and returns an ISO-8601 string that callers can read and sort consistently.

**Call relations**: Row-building helpers use this whenever they place a written time into list fields. It hands the raw database time to the store’s time-normalizing helper before returning display-ready text.

*Call graph*: called by 2 (_profile_row, _row); 1 external calls (_aware).


##### `_row`  (lines 104–128)

```
def _row(name: str, body: str, subject: str, item_class: str, memory_kind: str, written: datetime | None, page_id: UUID | None, pages: Mapping[UUID, PageState]) -> ObjectRow
```

**Purpose**: Builds one list row for a memory item. It gives the item a short summary, includes useful fields like subject and memory kind, and adds page-title details when the memory came from a readable synced page.

**Data flow**: It receives the memory id, body text, visibility subject, classification fields, write time, optional source page id, and known page states. It clips long text to safe list-size limits, formats the timestamp, looks up page metadata if available, and returns an ObjectRow.

**Call relations**: Memory listing and member memory detail both use this to make memory rows look the same. It calls the timestamp and text-clipping helpers, then packages the result into the standard object-row shape.

*Call graph*: calls 1 internal fn (_stamp); called by 2 (_page, member_detail); 2 external calls (__init__, clip_to_word).


##### `_member_reader`  (lines 131–139)

```
def _member_reader(member_id: UUID) -> SourceReader
```

**Purpose**: Builds the read identity used when a signed-in member views memory outside a live conversation turn. It decides which subjects, meaning visibility labels, that member is allowed to read.

**Data flow**: It receives a member id. It reads the current agent id, calculates the member’s conversation audience, turns that into readable subjects, and returns a SourceReader containing all of that identity information.

**Call relations**: Member-facing memory page and detail calls use this before reading the database. It mirrors the same audience rules used during a normal tool turn, so portal reads and conversation reads see the same memories.

*Call graph*: called by 2 (member_detail, member_page); 4 external calls (__init__, audience_subjects, conversation_audience, agent_current).


##### `MemoryObjects.list`  (lines 148–149)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists visible live memory items for the current tool caller. It is the normal object-list entry point for the memory kind.

**Data flow**: It receives a tool context and list query. It checks for the extension context, asks the tool context who the current reader is, and passes both into the shared paging routine, which returns an ObjectPage.

**Call relations**: Object infrastructure calls this when someone lists memory objects. It delegates the real database work and visibility filtering to MemoryObjects._page.

*Call graph*: calls 3 internal fn (source_reader, _page, _require_ext).


##### `MemoryObjects.member_page`  (lines 151–164)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists live memory items as a particular signed-in member would see them in the member portal. Admin status does not broaden what private memory is visible here.

**Data flow**: It receives an extension context, member id, admin flag, and list query. It requires the context, builds the member’s reader identity, and asks MemoryObjects._page to produce the page.

**Call relations**: Portal-style member browsing calls this instead of the tool-context list method. It uses _member_reader to recreate the member’s audience, then relies on the same paging code used by MemoryObjects.list.

*Call graph*: calls 3 internal fn (_page, _member_reader, _require_ext).


##### `MemoryObjects.member_detail`  (lines 166–200)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[MemorySpec] | None
```

**Purpose**: Opens one memory item as a signed-in member would see it, and returns both a list-style row and full detail. This is useful for portal views that need the surrounding row information as well as the full memory body.

**Data flow**: It receives an optional extension context, memory name, member id, and admin flag. It builds the member reader, fetches the item by id, finds any created-from page link, reads visible page metadata for that page, builds a row, and returns a MemberObject; if the item is not visible or the id is invalid, it returns null.

**Call relations**: Member detail views call this. It depends on MemoryObjects._item for the visibility-checked full detail, then uses _row so the detail page matches list output.

*Call graph*: calls 4 internal fn (_item, _member_reader, _require_ext, _row); 2 external calls (__init__, UUID).


##### `MemoryObjects.get`  (lines 202–203)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[MemorySpec] | None
```

**Purpose**: Opens one memory item by id for the current tool caller. It can return a superseded memory too, so old references can point users to the replacement link.

**Data flow**: It receives a tool context and object name. It checks the extension context, gets the current source reader from the tool context, and asks MemoryObjects._item to fetch and check the item.

**Call relations**: Object-get calls for the memory kind reach this method. It is a thin public wrapper around MemoryObjects._item, which does the database lookup, page checks, and detail construction.

*Call graph*: calls 3 internal fn (source_reader, _item, _require_ext).


##### `MemoryObjects._page`  (lines 205–262)

```
async def _page(self, ext: ExtensionContext, reader: SourceReader, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Fetches and filters the memory list for a given reader. It is the central routine that decides which live memory rows can appear in listings.

**Data flow**: It receives an extension context, reader, and list query. It reads the reader’s allowed subjects, queries the memory table for this workspace’s non-superseded and non-retired rows in those subjects, asks the extension which cited pages are still readable, drops rows whose source page no longer matches the reader or revision, converts the survivors into rows, and returns a paged result.

**Call relations**: MemoryObjects.list and MemoryObjects.member_page both hand off to this function. It talks to the database through the extension transaction, calls readable_page_states for page permissions, uses _row to format each item, and wraps the result with object_page.

*Call graph*: calls 3 internal fn (readable_page_states, transaction, _row); called by 2 (list, member_page); 2 external calls (select, object_page).


##### `MemoryObjects._item`  (lines 264–330)

```
async def _item(self, ext: ExtensionContext, reader: SourceReader, name: str) -> ObjectDetail[MemorySpec] | None
```

**Purpose**: Fetches one memory item by UUID and proves the reader is allowed to see it. It also builds links to the source page and to a replacement memory when applicable.

**Data flow**: It receives an extension context, reader, and name string. It parses the name as a UUID, queries the memory table for that workspace and allowed subjects, checks source-page readability and exact revision if the item came from a page, builds link objects for created_from and superseded_by, and returns an ObjectDetail with the memory spec and timestamps. Invalid names, missing rows, or failed permission checks produce null.

**Call relations**: MemoryObjects.get and MemoryObjects.member_detail use this for single-item reads. It is the detailed counterpart to MemoryObjects._page and hands back structured data that callers can show directly or wrap in a member-facing object.

*Call graph*: calls 2 internal fn (readable_page_states, transaction); called by 2 (get, member_detail); 6 external calls (__init__, __init__, __init__, __init__, select, UUID).


##### `MemoryObjects.status`  (lines 332–339)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports no special status for memory objects. It exists to satisfy the object-store interface.

**Data flow**: It receives the tool context, object name, and expected generation value, but does not read or change anything. It always returns null.

**Call relations**: If object infrastructure asks for memory-object status, this method provides the answer. There are no downstream calls because memory status is not supported here.


##### `MemoryObjects.apply`  (lines 341–350)

```
async def apply(self, ctx: ToolContext, name: str, spec: MemorySpec, old: MemorySpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to create or edit memory objects through the generic object apply path. Memories must be written through the dedicated memory_update path instead.

**Data flow**: It receives the proposed memory spec, old spec, name, context, and generation value. It does not inspect or save the new content; it raises a VerbNotSupported error with an explanation.

**Call relations**: Generic object editing calls may reach this method, but it deliberately stops them. This protects the memory lifecycle by keeping writes in the purpose-built memory_update flow.

*Call graph*: 1 external calls (__init__).


##### `MemoryObjects.delete`  (lines 352–359)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to delete memory objects directly. Memories end by being superseded or retired by memory processes, not by object deletion.

**Data flow**: It receives the context, memory name, and generation value. It makes no database change and raises a VerbNotSupported error explaining that memory objects are not deletable here.

**Call relations**: Generic object deletion calls may reach this method. It blocks them so old ids and consolidation links remain recoverable.

*Call graph*: 1 external calls (__init__).


##### `ProfileObjects.list`  (lines 410–411)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists member profile rows visible to the current tool caller. A profile contains a member’s role and current focus as known from shared workspace facts.

**Data flow**: It receives a tool context and list query. It checks that the extension context exists, reads the caller’s allowed subjects from the context, and delegates to ProfileObjects._page to build the result.

**Call relations**: Object-list calls for the profile kind enter here. It is a thin wrapper that lets ProfileObjects._page enforce the shared-subject rule and query the database.

*Call graph*: calls 2 internal fn (_page, _require_ext).


##### `ProfileObjects.member_page`  (lines 413–426)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists the People-band profiles as a signed-in member would see them outside a live turn. Admin status does not widen the result because profiles are based only on shared facts.

**Data flow**: It receives an extension context, member id, admin flag, and query. It checks the context, calculates the member’s conversation subjects, and asks ProfileObjects._page for the visible profile page.

**Call relations**: Member portal browsing calls this. It uses the same audience helpers as other member reads, then hands off to ProfileObjects._page for the actual shared-profile listing.

*Call graph*: calls 2 internal fn (_page, _require_ext); 2 external calls (audience_subjects, conversation_audience).


##### `ProfileObjects.get`  (lines 428–430)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ProfileSpec] | None
```

**Purpose**: Opens one profile by member id for the current tool caller. It returns only the full detail, not the member-wrapper row.

**Data flow**: It receives a tool context and profile name. It checks for the extension context, calls ProfileObjects._entry with the caller’s readable subjects, and returns the detail part if an entry is found; otherwise it returns null.

**Call relations**: Object-get calls for the profile kind use this. It relies on ProfileObjects._entry to enforce visibility, parse the member id, query storage, and build the object.

*Call graph*: calls 2 internal fn (_entry, _require_ext).


##### `ProfileObjects.member_detail`  (lines 432–442)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[ProfileSpec] | None
```

**Purpose**: Opens one profile as a signed-in member would see it in the member portal. It returns both the row and the full detail when visible.

**Data flow**: It receives an extension context, profile name, member id, and admin flag. It checks the context, computes the member’s readable subjects, and calls ProfileObjects._entry, returning its MemberObject or null.

**Call relations**: Member-facing profile detail views call this. Like member_page, it rebuilds the member audience and then delegates to the shared entry-reading routine.

*Call graph*: calls 2 internal fn (_entry, _require_ext); 2 external calls (audience_subjects, conversation_audience).


##### `ProfileObjects._page`  (lines 444–463)

```
async def _page(self, ext: ExtensionContext, subjects: frozenset[str], query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Fetches the visible list of member profiles. Its key rule is simple: no shared subject means no profiles at all.

**Data flow**: It receives an extension context, a set of readable subjects, and a list query. If the workspace-shared subject is missing, it returns an empty page. Otherwise it queries profile rows for the workspace, orders newest first, converts each row with _profile_row, and returns a paged object result.

**Call relations**: ProfileObjects.list and ProfileObjects.member_page both use this for profile listings. It performs the database read through the extension transaction and formats rows before handing them to object_page.

*Call graph*: calls 2 internal fn (transaction, _profile_row); called by 2 (list, member_page); 2 external calls (select, object_page).


##### `ProfileObjects._entry`  (lines 465–498)

```
async def _entry(self, ext: ExtensionContext, subjects: frozenset[str], name: str) -> MemberObject[ProfileSpec] | None
```

**Purpose**: Fetches one member profile if the reader is allowed to see shared workspace facts. It turns the stored row into both a compact row and a full profile detail.

**Data flow**: It receives an extension context, readable subjects, and a name string. It returns null if the shared subject is absent or the name is not a valid UUID. Otherwise it queries the profile table for that workspace and member id, normalizes the written time, builds a ProfileSpec and ObjectDetail, wraps them with a row in a MemberObject, and returns it.

**Call relations**: ProfileObjects.get and ProfileObjects.member_detail call this for single-profile reads. It uses _profile_row for consistent row formatting and the store time helper for reliable timestamps.

*Call graph*: calls 2 internal fn (transaction, _profile_row); called by 2 (get, member_detail); 6 external calls (__init__, __init__, __init__, select, _aware, UUID).


##### `ProfileObjects.status`  (lines 500–507)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports no special status for profile objects. It is present because the object-store interface includes a status operation.

**Data flow**: It receives the context, profile name, and expected generation value. It performs no lookup and always returns null.

**Call relations**: Object infrastructure may call this when checking status for a profile. This implementation has no handoff because profile status is not tracked here.


##### `ProfileObjects.apply`  (lines 509–518)

```
async def apply(self, ctx: ToolContext, name: str, spec: ProfileSpec, old: ProfileSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to write profiles through the generic object apply path. Profiles are produced by the People pass from shared memory, not edited directly.

**Data flow**: It receives the proposed profile spec, old spec, name, context, and generation value. It makes no changes and raises a VerbNotSupported error explaining the allowed write path.

**Call relations**: Generic object-edit calls may reach this method. It stops them so profile content stays tied to the People pass and the shared facts it reads.

*Call graph*: 1 external calls (__init__).


##### `ProfileObjects.delete`  (lines 520–527)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to delete profiles through the object API. Profile rows are controlled by the same People pass that writes them.

**Data flow**: It receives the context, profile name, and expected generation value. It does not remove anything and raises a VerbNotSupported error.

**Call relations**: Generic object-delete calls may reach this method. It blocks deletion for the same reason apply is blocked: this object kind is read-only from the outside.

*Call graph*: 1 external calls (__init__).


##### `_profile_row`  (lines 530–540)

```
def _profile_row(row: sa.Row) -> ObjectRow
```

**Purpose**: Builds one list row for a member profile. It gives profile listings a readable summary and standard fields such as member id, role, focus, and written time.

**Data flow**: It receives a database row for a profile. It combines role and focus into a clipped summary, formats the written timestamp, copies the important values into fields, and returns an ObjectRow named by the member id.

**Call relations**: ProfileObjects._page uses this for every row in a profile list, and ProfileObjects._entry uses it for the row beside a single profile detail. It calls the same clipping and timestamp helpers used elsewhere so profile output stays compact and consistent.

*Call graph*: calls 1 internal fn (_stamp); called by 2 (_entry, _page); 2 external calls (__init__, clip_to_word).


### `extensions/monitors/ufo_ext_monitors/monitor_kind.py`

`domain_logic` · `request handling`

A monitor is like setting a reminder that keeps checking the same thing: it runs a command in a conversation's sandbox, compares the result with an original baseline, and later wakes the agent if something important happens. This file does not run the checks itself. Instead, it registers monitors as readable and deletable objects in the wider UFO system.

The main class, MonitorObjects, is the bridge between stored monitor records and the generic object commands people or agents can use. When someone lists monitors, it reads all armed monitors from MonitorStore, adds friendly summary fields such as the command, next probe time, deadline, owner email, and whether the monitor belongs to the current member. When someone gets one monitor, it returns the monitor's full recipe: command, interval, deadline, reason, and a link back to the conversation where it reports.

A key rule is that monitors cannot be created by applying a manifest here. Arming a monitor must happen through the chat action, because the system needs to run the command once immediately and save that first output as the baseline. Deleting is allowed, but only if the monitor still matches the expected stored generation, which prevents stopping the wrong monitor if it changed underneath the caller.

#### Function details

##### `_owner`  (lines 53–58)

```
def _owner(row: Monitor) -> GeneratedObjectOwner
```

**Purpose**: Builds the ownership record for a monitor so the object system knows who created it, who it is shared with, and which exact stored monitor version it represents. This matters because monitor visibility follows the conversation audience, not just the creator.

**Data flow**: It receives one stored Monitor row. It reads the creator member id, audience, and monitor id from that row, converts the audience into a shared-access description, and returns a GeneratedObjectOwner that the object system can use for access checks and identity.

**Call relations**: MonitorObjects._member_rows calls this while turning raw monitor rows into listable objects. The returned owner is attached to each listed row so later get, status, or delete requests can prove they are talking about the same monitor.

*Call graph*: called by 1 (_member_rows); 2 external calls (__init__, subject_shared).


##### `_require_ext`  (lines 61–64)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Checks that the monitor extension context is present before any monitor storage work happens. The extension context is the bundle of runtime services and state that MonitorStore needs to read or change monitors.

**Data flow**: It receives an ExtensionContext value or None. If the context is present, it returns it unchanged; if it is missing, it raises a runtime error explaining that monitor objects require the monitor extension context.

**Call relations**: MonitorObjects._member_rows, MonitorObjects._delete_owned, and MonitorObjects._find use this before opening MonitorStore. It acts like a guard at the door, making failures clear instead of letting storage code fail later in a confusing way.

*Call graph*: called by 3 (_delete_owned, _find, _member_rows).


##### `MonitorObjects._member_rows`  (lines 78–97)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Creates the rows shown when a member lists armed monitors. It turns internal monitor records into a compact, readable table with useful fields for sorting and filtering.

**Data flow**: It receives the extension context and, optionally, the current member id. It reads all armed monitors from MonitorStore, looks up owner email addresses, builds an owner record for each monitor, and returns a tuple of OwnedRow objects containing the monitor name, short summary, ownership, conversation id, next probe time, deadline, owner email, and whether it belongs to the current member.

**Call relations**: The generic object-listing flow calls this when someone asks to list monitor objects. Inside that flow, it relies on _require_ext to ensure storage can be used, calls MonitorStore to fetch active watches, asks owner_emails for friendly creator labels, and calls _owner so each row carries the correct visibility and identity information.

*Call graph*: calls 2 internal fn (_owner, _require_ext); 3 external calls (__init__, __init__, owner_emails).


##### `MonitorObjects._member_object`  (lines 99–125)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[MonitorSpec] | None
```

**Purpose**: Returns the detailed description of one monitor when a member asks to inspect it. It shows the monitor's command, timing, deadline, reason, and the conversation it reports to.

**Data flow**: It receives the extension context, the requested monitor name, the expected owner information, and optionally the current member id. It searches for the named armed monitor, confirms its stored id matches the requested owner generation, then returns an ObjectDetail containing a MonitorSpec plus timestamps and a link to the conversation. If the monitor is missing or no longer matches, it returns None.

**Call relations**: The generic object-get flow calls this after access rules have selected a monitor candidate. It delegates lookup to MonitorObjects._find, then packages the result into SDK object-detail types so the rest of the system can display it consistently with other object kinds.

*Call graph*: calls 1 internal fn (_find); 4 external calls (__init__, __init__, __init__, __init__).


##### `MonitorObjects._status`  (lines 127–142)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns the live status counters for one monitor. This is the operational view: when it was armed, when it last ran, how many probes ran, how many were quiet, failed, or skipped, and a short excerpt of the baseline output.

**Data flow**: It receives a tool context, monitor name, and expected owner information. It finds the matching armed monitor and verifies the stored id still matches the owner generation. If valid, it returns a dictionary of JSON-friendly values; dates are converted to text, missing last-probe time becomes null, and the baseline is shortened to a safe excerpt. If not valid, it returns None.

**Call relations**: The object status flow calls this when a caller wants more than the monitor's static recipe. It uses MonitorObjects._find for the actual lookup, then hands back simple status data that can be shown by tools or agents.

*Call graph*: calls 1 internal fn (_find).


##### `MonitorObjects._apply_owned`  (lines 144–152)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: MonitorSpec, old: MonitorSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: Refuses attempts to create or update monitors through the normal object apply path. This protects an important setup step: a monitor must be armed in chat so the command can be run immediately and its first output saved as the baseline.

**Data flow**: It receives the requested name, desired MonitorSpec, any old spec, and owner information, but does not use them to write anything. It always raises VerbNotSupported with a message telling the caller to use the monitor chat action instead.

**Call relations**: The generic object-apply flow calls this if someone tries to apply a monitor manifest. Rather than forwarding to storage, it stops the flow immediately and explains the correct route for arming a monitor.

*Call graph*: 1 external calls (__init__).


##### `MonitorObjects._delete_owned`  (lines 154–159)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: Stops an armed monitor. Deleting a monitor in this object system means disarming it so it will not probe or fire again.

**Data flow**: It receives a tool context, monitor name, and expected owner information. It finds the monitor by name, checks that its stored id matches the owner generation, then asks MonitorStore to disarm that exact row. If the monitor is gone, changed, or cannot be disarmed, it raises an error instead of silently stopping the wrong thing.

**Call relations**: The generic object-delete flow calls this after the object system has applied the delete permission rule. It uses MonitorObjects._find to locate the active watch, _require_ext to access the monitor store, and then hands the row to MonitorStore.disarm to make the stop permanent.

*Call graph*: calls 2 internal fn (_find, _require_ext); 1 external calls (__init__).


##### `MonitorObjects._find`  (lines 161–165)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> Monitor | None
```

**Purpose**: Looks up one armed monitor by its object name. It is the shared helper used by get, status, and delete operations so they all search the active monitor set the same way.

**Data flow**: It receives an extension context and a monitor name. It verifies the context exists, reads all armed monitors from MonitorStore, scans them for a row whose name matches the requested name, and returns that Monitor row or None if no active monitor has that name.

**Call relations**: MonitorObjects._member_object, MonitorObjects._status, and MonitorObjects._delete_owned call this whenever they need the current stored row for a named monitor. It centralizes the lookup so those higher-level actions can focus on packaging details, reporting status, or disarming.

*Call graph*: calls 1 internal fn (_require_ext); called by 3 (_delete_owned, _member_object, _status); 1 external calls (__init__).


### `extensions/report_digest/ufo_ext_report_digest/objects.py`

`domain_logic` · `request handling`

This file turns scheduled radar runs into something the rest of the system can list and inspect like ordinary workspace objects. Think of it as a display case: the real items are scheduled runs and digest database rows, and this file arranges them into a readable report shape.

A report is named by the run's turn id. When someone lists reports, the code first checks who the reader is. If there is no member identity, it returns nothing. Otherwise it asks the extension context for scheduled runs the reader is allowed to see. That permission boundary matters: even an admin does not get a wider report feed than the conversations and audiences they could already read.

For each run, the file adds extra detail. It looks up any digest entry already written for that run, looks up the scheduled task name if the run came from a task, and turns any shared artifacts into signed links and preview links. The result is an `ObjectRow` with fields like conversation, fired time, status, task, digest entry, and files.

The file also deliberately refuses create, update, and delete. Reports exist because scheduled tasks fire. If this file allowed editing, the object view could stop matching the real run history.

#### Function details

##### `ReportObjects.list`  (lines 66–76)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists the newest report objects visible to the current tool user. It first proves the caller is a workspace member, because reports are member-scoped and must not leak across permission boundaries.

**Data flow**: It receives a tool context and a list query. From the context it reads the caller's authority, current agent id, and readable audience subjects. If there is no member id, it turns an empty set into an empty page. If there is a member id, it passes the request to `_page`, which fetches runs and shapes them into report rows.

**Call relations**: This is the public listing path for the object kind. It uses `_ext` to get the extension context, asks the authority helper for the member id, and then hands the real work to `_page`; `_page` later returns rows that `object_page` packages according to the query.

*Call graph*: calls 2 internal fn (_page, _ext); 2 external calls (authority_member_id, object_page).


##### `ReportObjects.get`  (lines 78–88)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ReportSpec] | None
```

**Purpose**: Fetches one report object by name for the current tool user. The name is expected to be a turn id, which is the unique id of the scheduled run.

**Data flow**: It receives a tool context and a report name. It reads the caller's member id and readable subjects. If the caller is not a member, it returns nothing. Otherwise it asks `_one` to find the matching run and build full object detail; if `_one` finds a report, this function returns just the detailed object information.

**Call relations**: This is the public detail lookup path for the object kind. It uses `_ext` to recover the extension services and relies on `_one` to validate the name, read the scheduled run, and build the report detail.

*Call graph*: calls 2 internal fn (_one, _ext); 1 external calls (authority_member_id).


##### `ReportObjects.member_page`  (lines 90–98)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists report objects for a specified member outside the normal tool-call path. It is useful for member-level object browsing where the caller already supplies the member id directly.

**Data flow**: It receives an extension context or carrier, a member id, an admin flag, and a list query. It converts the carrier into an extension context, uses the system object-agent id rather than the current turn's agent id, and asks `_page` to produce the page of report rows.

**Call relations**: This is another entry into the same paging machinery used by `ReportObjects.list`. It calls `_ext` for the context, uses `object_agent_id` for the agent scope, and then delegates to `_page`.

*Call graph*: calls 2 internal fn (_page, _ext); 1 external calls (object_agent_id).


##### `ReportObjects.member_detail`  (lines 100–108)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[ReportSpec] | None
```

**Purpose**: Fetches one report object for a specified member outside the normal tool-call path. It is the member-level counterpart to `ReportObjects.get`.

**Data flow**: It receives an extension context or carrier, a report name, and a member id. It converts the carrier into an extension context and asks `_one` to look up the matching scheduled run and build the member object. The admin flag is accepted but does not widen what the report object itself reads.

**Call relations**: This function shares the same detail-building path as `ReportObjects.get`. It calls `_ext` and then hands off to `_one`, which does the actual run lookup and object construction.

*Call graph*: calls 2 internal fn (_one, _ext).


##### `ReportObjects.status`  (lines 110–117)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports no separate live status for a report object. A report's state is already represented by the underlying scheduled run fields, such as `status` and `fired_at`.

**Data flow**: It receives the context, report name, and optional expected generation value, but does not read or change anything. It always returns `None`, meaning there is no extra status payload.

**Call relations**: This sits in the object-kind interface because object kinds can expose status. For reports, no helper is called because status is not a separate operation.


##### `ReportObjects.apply`  (lines 119–128)

```
async def apply(self, ctx: ToolContext, name: str, spec: ReportSpec, old: ReportSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to create or update a report. Reports are historical records of scheduled runs, so editing them directly would make the object view lie about what actually happened.

**Data flow**: It receives the requested name, new spec, old spec, and optional generation check. Instead of saving anything, it raises a `VerbNotSupported` error with a message explaining that reports come from scheduled task runs.

**Call relations**: This is called when the object system tries to apply a desired object state. It does not call the report-building helpers; it immediately stops the write path by constructing the unsupported-verb error.

*Call graph*: 1 external calls (__init__).


##### `ReportObjects.delete`  (lines 130–137)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to delete a report object. The report exists because a scheduled run happened, so removing it through this object API would hide history rather than change the source of truth.

**Data flow**: It receives the context, report name, and optional generation check. It does not delete database rows or artifacts. It raises a `VerbNotSupported` error with the same explanation used for updates.

**Call relations**: This is the delete side of the object-kind interface. Like `apply`, it blocks the write path immediately and does not call any of the read helpers.

*Call graph*: 1 external calls (__init__).


##### `ReportObjects._page`  (lines 139–152)

```
async def _page(self, ext: ExtensionContext, member_id: UUID, *, agent_id: UUID, query: ObjectListQuery, subjects: frozenset[str] | None=None) -> ObjectPage
```

**Purpose**: Builds one page of report rows from scheduled runs the member is allowed to read. It is the shared worker behind the public list-style methods.

**Data flow**: It receives an extension context, member id, agent id, list query, and optionally readable subjects. It asks the extension context for up to 200 scheduled runs matching those limits. Then it turns those runs into object rows through `_rows` and wraps them into an object page using the query.

**Call relations**: `ReportObjects.list` and `ReportObjects.member_page` both call this when they need a report feed. It gets raw scheduled-run data from the extension context, asks `_rows` to enrich and format it, and then hands the rows to `object_page` for paging and filtering behavior.

*Call graph*: calls 2 internal fn (scheduled_runs, _rows); called by 2 (list, member_page); 1 external calls (object_page).


##### `ReportObjects._one`  (lines 154–186)

```
async def _one(self, ext: ExtensionContext, name: str, *, member_id: UUID, subjects: frozenset[str] | None=None) -> MemberObject[ReportSpec] | None
```

**Purpose**: Finds and builds one full report object from a report name. It turns the name into a run id, checks that the member can read that run, and adds detail information such as creation time and a link back to the conversation.

**Data flow**: It receives an extension context, a report name, member id, and optional readable subjects. First it tries to parse the name as a UUID; if that fails, it returns nothing. Then it asks for the one scheduled run with that turn id. If no run is visible, it returns nothing. If found, it builds a row with `_rows` and wraps it in a `MemberObject` with an empty `ReportSpec`, timestamps from the run's fired time, and a link to the conversation where the run happened.

**Call relations**: `ReportObjects.get` and `ReportObjects.member_detail` call this for single-report lookups. It uses the extension context to read scheduled runs, uses `_rows` for the same row-shaping logic as listings, and then creates the object-detail wrapper expected by the object system.

*Call graph*: calls 2 internal fn (scheduled_runs, _rows); called by 2 (get, member_detail); 6 external calls (__init__, __init__, __init__, __init__, __init__, UUID).


##### `ReportObjects._rows`  (lines 188–203)

```
async def _rows(self, ext: ExtensionContext, runs: tuple[ScheduledRun, ...]) -> tuple[ObjectRow, ...]
```

**Purpose**: Enriches scheduled runs with digest entries and task names, then converts them into report rows. This keeps list and detail views consistent.

**Data flow**: It receives a tuple of scheduled runs. It extracts their turn ids and asks `_entries` for digest data. It also extracts scheduled task ids from idempotency keys when possible and asks `_task_names` for human-readable task names. Finally, for each run, it calls `_row` with the matching digest entry and the task-name map, returning a tuple of finished object rows.

**Call relations**: `_page` and `_one` call this after they have selected the runs the reader may see. `_rows` coordinates the two lookup helpers, `_entries` and `_task_names`, then delegates the final per-run formatting to `_row`.

*Call graph*: calls 3 internal fn (_entries, _row, _task_names); called by 2 (_one, _page); 1 external calls (scheduled_fire_task_id).


##### `ReportObjects._row`  (lines 205–247)

```
def _row(self, ext: ExtensionContext, run: ScheduledRun, entry: dict[str, JsonValue] | None, tasks: dict[UUID, str]) -> ObjectRow
```

**Purpose**: Turns one scheduled run into the row shape shown to report readers. It chooses a useful summary and fills in all visible fields, including digest data and artifact links.

**Data flow**: It receives the extension context, one scheduled run, an optional digest entry, and a map of task ids to task names. It derives the task id from the run's idempotency key, finds the task name if available, and chooses a summary: digest title first, otherwise task/status/time, otherwise status/time. It returns an `ObjectRow` containing conversation id, agent id, fired time, status, task, surface, source, failure text when relevant, the digest entry, and artifact metadata with signed URLs.

**Call relations**: `_rows` calls this once per scheduled run after collecting shared lookup data. This function uses the extension context only for artifact links and preview links, then hands back a fully displayable row.

*Call graph*: calls 2 internal fn (artifact_link, artifact_preview_link); called by 1 (_rows); 2 external calls (__init__, scheduled_fire_task_id).


##### `ReportObjects._entries`  (lines 249–277)

```
async def _entries(self, ext: ExtensionContext, turn_ids: tuple[UUID, ...]) -> dict[UUID, dict[str, JsonValue]]
```

**Purpose**: Reads digest entries for a set of scheduled run ids. These entries provide the report title, summary, and bullet points written by the digest job.

**Data flow**: It receives an extension context and a tuple of turn ids. If the tuple is empty, it returns an empty dictionary. Otherwise it opens a database transaction, selects matching rows from the report digest entry table for the current workspace, and returns a dictionary keyed by turn id. Each value contains the title, summary, and simplified point data with text and actor.

**Call relations**: `_rows` calls this before building report rows so each run can include its digest entry if one exists. The function talks directly to the database through the extension context transaction and SQLAlchemy's select builder.

*Call graph*: calls 1 internal fn (transaction); called by 1 (_rows); 1 external calls (select).


##### `ReportObjects._task_names`  (lines 279–297)

```
async def _task_names(self, ext: ExtensionContext, task_ids: tuple[UUID, ...]) -> dict[UUID, str]
```

**Purpose**: Looks up readable names for scheduled tasks that fired the runs. This lets a report say which task produced it, while still showing the report even if the task was later deleted.

**Data flow**: It receives an extension context and task ids. If there are no task ids, it returns an empty dictionary. Otherwise it opens a database transaction, selects task ids and names for the current workspace, and returns a dictionary from task id to task name. Missing ids simply stay missing, so the report can show a null task name.

**Call relations**: `_rows` calls this after extracting task ids from the runs' idempotency keys. It reads only task names for runs already visible to the reader, then `_row` uses the returned map when building each report row.

*Call graph*: calls 1 internal fn (transaction); called by 1 (_rows); 1 external calls (select).


##### `_ext`  (lines 300–305)

```
def _ext(carrier: ToolContext | ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Finds the `ExtensionContext`, which is the object that gives this file access to extension services such as scheduled runs, database transactions, and artifact links. It gives the rest of the file one simple way to accept either a tool context or an extension context.

**Data flow**: It receives either an `ExtensionContext`, a `ToolContext`, or `None`. If it is already an extension context, it returns it. If it is a tool context with an attached extension context, it returns that. If neither path works, it raises a runtime error because report objects cannot function without extension services.

**Call relations**: The public entry methods `ReportObjects.list`, `ReportObjects.get`, `ReportObjects.member_page`, and `ReportObjects.member_detail` call this before doing real work. It supplies the context that later helpers need for scheduled-run reads, database queries, and artifact URL creation.

*Call graph*: called by 4 (get, list, member_detail, member_page).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/tools.py`

`domain_logic` · `tool use, object operations, and workflow pause/resume`

This file gives the system two related abilities. First, it lets agents create, list, inspect, update, and delete recurring scheduled tasks. A scheduled task is like a standing calendar reminder for an agent: at each matching time, the agent is invoked with the saved prompt and reports back into the conversation where the task was created. The file defines what a task’s editable shape looks like, checks that schedules and expiry times are valid, decides who may see or change each task, and translates stored task rows into object-list and object-detail views.

A key rule is that a task stays tied to its original creator and conversation. Updating the task can change its schedule or prompt, but it does not move where it reports. Visibility follows the reporting conversation: if the conversation is shared, the task can be seen there; if the prompt is private to the creator, other readers see a placeholder instead of the full content.

Second, the file defines `pause_and_wait`, which is not a workspace object. It records a temporary pause in a separate pause table, then tells the agent to stop after replying. The workflow can later resume either when a member sends a new message or when the timer expires. This is useful for things like waiting for approval, verification emails, or outside systems.

#### Function details

##### `ScheduledTaskSpec.validate_utc_expiry`  (lines 114–117)

```
def validate_utc_expiry(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Checks that a task expiry time is written in UTC, the shared world time standard. This prevents confusing schedules caused by local time zones or missing time-zone information.

**Data flow**: It receives the proposed `expires_at` value. If there is no expiry, it accepts it. If there is an expiry, it checks whether the timestamp has UTC time-zone information; if not, it raises a validation error. A valid timestamp is returned unchanged.

**Call relations**: This runs automatically when a `ScheduledTaskSpec` is built or validated. It protects later create and update work in `ScheduledTaskObjects._apply_owned` from receiving an ambiguous expiry time.

*Call graph*: 2 external calls (utcoffset, timedelta).


##### `_require_scheduler`  (lines 134–135)

```
def _require_scheduler(ext: ExtensionContext | None) -> ScheduleStore
```

**Purpose**: Creates the schedule-store helper used to read and write scheduled tasks. It also makes sure the scheduled-tasks extension context is actually present before any database-backed task work begins.

**Data flow**: It receives an optional extension context. It first passes that through `_require_ext`, which either returns a real context or raises an error. With the valid context, it creates and returns a `ScheduleStore`, the storage-facing object for scheduled tasks.

**Call relations**: Most task operations call this before touching stored schedules: listing rows, checking status, creating or updating tasks, deleting tasks, finding a task, and showing tasks attached to a conversation. It is the common doorway from object logic into persistent schedule storage.

*Call graph*: calls 1 internal fn (_require_ext); called by 6 (_apply_owned, _delete_owned, _find, _rows, _status, member_conversation_rows); 1 external calls (__init__).


##### `_require_ext`  (lines 138–141)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Ensures code that needs the scheduled-tasks extension has the required extension context. Without it, the code would not know how to reach this extension’s storage and runtime services.

**Data flow**: It receives an optional extension context. If the value is missing, it raises a runtime error with a clear message. If present, it returns the context unchanged.

**Call relations**: `_require_scheduler` uses this before creating a `ScheduleStore`, and `pause_and_wait` uses it before writing a pause. It is a small safety gate for all extension-specific work in this file.

*Call graph*: called by 2 (_require_scheduler, pause_and_wait).


##### `_summary`  (lines 144–145)

```
def _summary(task: ScheduledTask) -> str
```

**Purpose**: Builds the short one-line text shown for a scheduled task in lists. It combines the schedule with either the task description or, if no description exists, the prompt.

**Data flow**: It receives a stored scheduled task. It creates text in the form `schedule — description-or-prompt`, then cuts it down to the maximum summary length. The result is a compact string for display.

**Call relations**: `ScheduledTaskObjects._rows` calls this while building list rows for visible tasks. If the task’s prompt should not be visible to the current reader, `_rows` uses a private-placeholder summary instead.

*Call graph*: called by 1 (_rows).


##### `_validate_future_fire`  (lines 148–152)

```
def _validate_future_fire(next_run_at: datetime, expires_at: datetime | None, *, paused: bool) -> None
```

**Purpose**: Makes sure a running task does not expire before, or exactly when, its next planned run would happen. This avoids saving a schedule that can never legally fire.

**Data flow**: It receives the proposed next run time, an optional expiry time, and whether the task is paused. If the task is not paused and the expiry is at or before the next run, it raises an error. Otherwise it returns nothing and allows the save to continue.

**Call relations**: `ScheduledTaskObjects._apply_owned` calls this during both task creation and task update, after it has calculated the next fire time. It acts as the final sanity check before writing the task to the schedule store.

*Call graph*: called by 1 (_apply_owned).


##### `_owner`  (lines 155–163)

```
def _owner(listed: ListedTask) -> GeneratedObjectOwner
```

**Purpose**: Describes who owns a listed task and how widely it is shared. The owner information is used by the generic object system to decide who can see or act on the task.

**Data flow**: It receives a listed task, reads the creator member id, the task id, and the task’s audience, then builds a `GeneratedObjectOwner`. The result says who created the task, what sharing scope applies, and which stored generation of the object is being viewed.

**Call relations**: `ScheduledTaskObjects._rows` uses this for every row it returns, and `member_conversation_rows` uses it before granting a task inside a conversation view. It turns scheduling-specific facts into the ownership language understood by the wider object system.

*Call graph*: called by 2 (_rows, member_conversation_rows); 2 external calls (__init__, subject_shared).


##### `ScheduledTaskObjects._admin_can_apply`  (lines 185–189)

```
def _admin_can_apply(self, _old: ScheduledTaskSpec, spec: ScheduledTaskSpec) -> bool
```

**Purpose**: Decides which updates an administrator is allowed to make to someone else’s scheduled task. Admins may adjust timing-related settings, but not the task’s content or an immediate `run_now` action.

**Data flow**: It receives the old task spec and the proposed new spec. It checks which fields the update explicitly set. If the update includes `prompt`, `description`, or `run_now`, it returns false for admin-only application; otherwise it returns true.

**Call relations**: This supports the permission rules inherited from the generic member-readable object base class. It keeps the later apply path aligned with the policy that content belongs to the creator, while cadence, expiry, and pause can be administered.


##### `ScheduledTaskObjects.member_page`  (lines 191–213)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Builds a page of scheduled-task rows for a member, with special support for filtering by conversation. This lets the object list show only tasks that report into a particular conversation when requested.

**Data flow**: It receives the extension context, the requesting member, whether they are an admin, and the list query. If the query does not contain a usable conversation filter, it falls back to the base object listing behavior. If a valid conversation id is present, it loads rows for that conversation, filters out rows the member cannot see, wraps them as object rows, and returns a paged result.

**Call relations**: When object listing asks for scheduled tasks, this method either delegates to the parent listing flow or calls `_rows` for the conversation-specific path. It then hands the visible rows to `object_page`, which formats the page for the object API.

*Call graph*: calls 1 internal fn (_rows); 3 external calls (__init__, object_page, UUID).


##### `ScheduledTaskObjects.member_conversation_rows`  (lines 215–235)

```
async def member_conversation_rows(self, ext: ExtensionContext | None, conversation_id: UUID, *, member_id: UUID, admin: bool, limit: int) -> tuple[ConversationObjectGrant, ...]
```

**Purpose**: Finds scheduled tasks that belong in a conversation’s object slot. This is what lets a conversation show the recurring tasks that report back into it.

**Data flow**: It receives a conversation id, member id, admin flag, and limit. It asks the schedule store for tasks reporting to that conversation. For each task the member is allowed to see, it creates a grant containing the task name, stored generation id, and whether the task content itself is visible to that member.

**Call relations**: Conversation surfaces call this when they need attached scheduled-task objects. It uses `_require_scheduler` to read stored tasks, `_owner` to apply visibility rules, and `task_content_visible` to decide whether the prompt/details should be exposed.

*Call graph*: calls 2 internal fn (_owner, _require_scheduler); 2 external calls (__init__, task_content_visible).


##### `ScheduledTaskObjects._member_rows`  (lines 237–240)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Returns all scheduled-task rows readable by a particular member, before final visibility filtering by the base object system. It is the member-facing row source for ordinary object listing.

**Data flow**: It receives the extension context and an optional member id. It calls `_rows` with no prompt trimming, so rows can include full prompt text when the member is allowed to see it. It returns owned rows that include display fields and ownership information.

**Call relations**: The generic object framework calls this as part of listing objects for a member. It delegates the real row construction to `_rows`, keeping one shared path for building scheduled-task list data.

*Call graph*: calls 1 internal fn (_rows).


##### `ScheduledTaskObjects._owned_rows`  (lines 242–249)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Returns the scheduled-task rows that should be placed into an agent turn’s context. It limits prompt length so a huge saved prompt does not flood the model’s working context.

**Data flow**: It receives the current tool context. It extracts the acting member id from the authority information, then calls `_rows` with a prompt excerpt limit. The output is a tuple of owned rows suitable for the object-list tool view.

**Call relations**: The object tool system calls this when the agent reads scheduled tasks during a turn. It uses `_rows` just like other listing paths, but with prompt trimming because this data is being inserted into model context.

*Call graph*: calls 1 internal fn (_rows); 1 external calls (authority_member_id).


##### `ScheduledTaskObjects._rows`  (lines 251–297)

```
async def _rows(self, ext: ExtensionContext | None, *, member_id: UUID | None, prompt_max: int | None, conversation_id: UUID | None=None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Builds the detailed list-row data for scheduled tasks. It gathers stored tasks, owner emails, last-run inspection data, visibility decisions, and display fields into one object-list-friendly shape.

**Data flow**: It receives the extension context, optional member id, optional prompt length limit, and optional conversation filter. It loads reported tasks from the schedule store, fetches creator emails, inspects tasks for recent run status, and then creates one owned row per listed task. If the reader may see the content, the row includes the real prompt or an excerpt; otherwise it uses a private placeholder.

**Call relations**: This is the central row-building helper used by `member_page`, `_member_rows`, and `_owned_rows`. It calls `_require_scheduler` for storage access, `_owner` for ownership, `_summary` for visible summaries, `owner_emails` for display names, and `task_content_visible` for privacy decisions.

*Call graph*: calls 3 internal fn (_owner, _require_scheduler, _summary); called by 3 (_member_rows, _owned_rows, member_page); 3 external calls (__init__, owner_emails, task_content_visible).


##### `ScheduledTaskObjects._member_object`  (lines 299–328)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[ScheduledTaskSpec] | None
```

**Purpose**: Builds the full detail view for one scheduled task, if the requested stored generation still matches. This is what powers a “get this task” object operation.

**Data flow**: It receives the extension context, task name, expected owner/generation, and optional member id. It finds the current listed task by name, rejects it if it is missing or no longer the expected generation, then returns an object detail containing the task spec, timestamps, a link to the reporting conversation, and whether the spec content is visible.

**Call relations**: The generic object system calls this when a member asks to inspect a scheduled task. It uses `_find` to locate the task and `task_content_visible` to decide whether the full spec should be shown.

*Call graph*: calls 1 internal fn (_find); 5 external calls (__init__, __init__, __init__, __init__, task_content_visible).


##### `ScheduledTaskObjects._status`  (lines 330–362)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns live status information for one scheduled task, including pause state, next run, expiry, and the latest run if known. It gives a caller operational information without changing the task.

**Data flow**: It receives the tool context, task name, and expected owner/generation. It finds the task, verifies it is the expected stored version, asks the schedule store to inspect it, and builds a status dictionary. If there was a last run, it includes the turn id and status, and includes an excerpt of the response only when the current member may see the task content.

**Call relations**: The object system calls this when it needs status for a scheduled task. It depends on `_find` to locate the task, `_require_scheduler` to inspect stored run information, and `task_content_visible` plus `authority_member_id` to protect private output.

*Call graph*: calls 2 internal fn (_find, _require_scheduler); 2 external calls (authority_member_id, task_content_visible).


##### `ScheduledTaskObjects._apply_owned`  (lines 364–436)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ScheduledTaskSpec, old: ScheduledTaskSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: Creates or updates a scheduled task after checking schedule validity, permissions, ownership, expiry rules, and immediate-run behavior. This is the main write path for the scheduled-task object kind.

**Data flow**: It receives the current tool context, object name, proposed spec, previous spec if any, and owner/generation if updating. It validates the cron schedule, identifies the acting member, looks up any existing task, calculates the next run time, checks expiry timing, and then either creates a new stored task or updates the existing one. It raises clear errors when the task changed during editing, required fields are missing, there is no member speaker, or the caller lacks permission.

**Call relations**: The generic object apply/update flow calls this when a scheduled-task manifest is applied. It uses `_find` to detect the current stored task, `_require_scheduler` to write to storage, `_validate_future_fire` before saving, and the tool context’s admin check when someone other than the creator tries to change timing controls.

*Call graph*: calls 4 internal fn (require_speaking_admin, _find, _require_scheduler, _validate_future_fire); 7 external calls (__init__, __init__, now, authority_member_id, log, next_fire, validate_cron).


##### `ScheduledTaskObjects._delete_owned`  (lines 438–442)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: Cancels a scheduled task when an allowed caller deletes the object. It verifies that the task being cancelled is still the same stored generation the caller saw.

**Data flow**: It receives the tool context, task name, and expected owner/generation. It finds the current task by name. If it is missing or has changed generation, it raises an error to avoid cancelling the wrong task. Otherwise it asks the schedule store to cancel the task.

**Call relations**: The generic object delete flow calls this after permission checks. It uses `_find` for safe lookup and `_require_scheduler` for the actual cancellation in persistent schedule storage.

*Call graph*: calls 2 internal fn (_find, _require_scheduler).


##### `ScheduledTaskObjects._find`  (lines 444–452)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> ListedTask | None
```

**Purpose**: Looks up a scheduled task by its object name. It is a small helper used whenever another operation needs the current stored task before reading, updating, or deleting it.

**Data flow**: It receives the extension context and task name. It loads the reported scheduled tasks from the schedule store and returns the first listed task whose stored name matches. If none match, it returns nothing.

**Call relations**: `_member_object`, `_status`, `_apply_owned`, and `_delete_owned` all call this before acting on a named task. It centralizes the name lookup so those methods can focus on their own read, status, write, or delete behavior.

*Call graph*: calls 1 internal fn (_require_scheduler); called by 4 (_apply_owned, _delete_owned, _member_object, _status).


##### `pause_and_wait`  (lines 510–547)

```
async def pause_and_wait(ctx: ToolContext, args: PauseAndWaitInput) -> ToolResult
```

**Purpose**: Pauses the current workflow until either a new member message arrives or a durable timer expires. It is used when the agent must wait for something outside the current turn, such as approval or an email verification.

**Data flow**: It receives the tool context and pause arguments: the message to show now, wait length, resume instructions, reason, and optional metadata. It calculates the resume time, records a pause row with the conversation, agent, current turn position, arrival watermark, resume prompt, and creator member id, then returns a tool result telling the agent what to reply and that it must end its turn.

**Call relations**: The `PAUSE_AND_WAIT_TOOL` definition points to this as its handler. It uses `_require_ext` to get extension services, writes through `PauseStore`, and returns `TextContent` inside a `ToolResult` so the tool system can hand the pause directive back to the agent.

*Call graph*: calls 1 internal fn (_require_ext); 7 external calls (__init__, __init__, __init__, now, timedelta, dumps, authority_member_id).


### `extensions/sites/ufo_ext_sites/objects.py`

`domain_logic` · `request handling`

A deployed site is not just a running port. It also needs a stable identity in the workspace: who created it, which conversation made it, what link opens it, and whether it is private, workspace-only, or public. This file supplies that identity. Think of it like turning a live stall at a fair into an entry in the fair directory, with rules about who may see the entry and who may close the stall.

Each site object gets a name made from the site name plus a short fingerprint of the conversation that hosted it. That prevents two conversations that both deploy “dashboard” from colliding. The file can list sites a member may read, return detailed information for one site, report status such as the URL and source files, change visibility, and delete the site by unregistering it.

There is one important special case: a site can be bound as an agent’s homepage. Then the site no longer decides its own audience. Its visibility follows the agent object instead, and direct attempts to change the site’s visibility are refused. Homepage-bound sites are also hidden from ordinary browsing unless the caller asks for that binding. This keeps an agent’s page behaving like part of the agent, not like a normal shared site.

#### Function details

##### `site_object_name`  (lines 81–85)

```
def site_object_name(conversation_id: UUID, name: str) -> str
```

**Purpose**: Builds the workspace object name for a hosted site. It adds a short fingerprint of the conversation ID to the human site name so two conversations can use the same site name without clashing.

**Data flow**: It takes a conversation ID and a site name. It hashes the conversation ID, keeps a short beginning of that hash, and returns a combined name like `dashboard-9f21c0a4e3b7`.

**Call relations**: This is the common naming rule used whenever the file turns stored site rows into object names. Listing conversation grants, building the name-to-site lookup, and checking whether an object name belongs to a conversation all rely on this same rule.

*Call graph*: called by 3 (member_conversation_rows, _named, site_name_from_object); 1 external calls (sha256).


##### `site_name_from_object`  (lines 88–94)

```
def site_name_from_object(conversation_id: UUID, object_name: str) -> str | None
```

**Purpose**: Tries to recover the original site name from an object name, but only if the name matches the expected conversation fingerprint. This protects against treating a name from one conversation as if it belonged to another.

**Data flow**: It takes a conversation ID and an object name. It computes the suffix that should belong to that conversation, checks whether the object name ends with it, and returns the plain site name if the full name is valid; otherwise it returns nothing.

**Call relations**: It uses the same naming helper as the rest of the file, so parsing and creating names stay consistent. It is the reverse side of `site_object_name`.

*Call graph*: calls 1 internal fn (site_object_name); 1 external calls (sha256).


##### `_named`  (lines 97–98)

```
def _named(sites: Iterable[HostedSite]) -> dict[str, HostedSite]
```

**Purpose**: Turns a collection of stored hosted sites into a dictionary keyed by their workspace object names. This makes later lookup by object name simple and consistent.

**Data flow**: It receives stored site records. For each record, it creates the official object name from the site’s conversation and name, then returns a mapping from that object name to the site record.

**Call relations**: The site lookup path and member listing path both use this helper. It sits between the raw site registry and the object system’s name-based view.

*Call graph*: calls 1 internal fn (site_object_name); called by 2 (_find, _member_rows).


##### `_workspace`  (lines 101–104)

```
def _workspace(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Checks that the code has the extension context it needs to read workspace-specific data. The context is the bundle of workspace ID, transaction, URLs, and helper services for the current request.

**Data flow**: It receives an optional extension context. If the context is missing, it raises an error; otherwise it returns the context unchanged.

**Call relations**: Most operations in this file go through this guard before reading site data, agent visibility, workspace IDs, or public URLs. `_sites` also uses it before opening the site registry.

*Call graph*: called by 6 (_apply_owned, _member_object, _member_rows, _status, member_conversation_rows, _sites).


##### `_sites`  (lines 107–109)

```
def _sites(ext: ExtensionContext | None) -> HostedSites
```

**Purpose**: Opens the hosted-site registry for the current workspace. That registry is where deployed sites are recorded and updated.

**Data flow**: It receives the extension context, confirms it exists, reads the workspace ID and current database transaction from it, and returns a `HostedSites` access object scoped to that workspace.

**Call relations**: Listing, finding, changing visibility, deleting, and conversation grant checks all call this helper when they need the stored site records.

*Call graph*: calls 1 internal fn (_workspace); called by 5 (_apply_owned, _delete_owned, _find, _member_rows, member_conversation_rows); 1 external calls (__init__).


##### `effective_visibility`  (lines 112–118)

```
def effective_visibility(site: HostedSite, agents: Mapping[UUID, str]) -> Visibility
```

**Purpose**: Decides which visibility rule actually applies to a site. Normal sites use their own visibility setting, while sites used as an agent homepage use the agent’s visibility instead.

**Data flow**: It takes a stored site and a mapping of agent IDs to visibility levels. If the site is not bound to an agent, it returns the site’s own visibility. If it is bound, it looks up the agent and converts that agent visibility into the site visibility scale.

**Call relations**: Listing, object detail, and visibility-change checks call this before showing or comparing access levels. It is the key rule that keeps homepage sites tied to their agent instead of acting like independent shared sites.

*Call graph*: called by 3 (_apply_owned, _member_object, _member_rows); 1 external calls (visibility_level).


##### `_summary`  (lines 121–122)

```
def _summary(site: HostedSite, visibility: Visibility) -> str
```

**Purpose**: Creates the short one-line description shown for a site in object listings. It gives a quick human-readable snapshot: name, visibility, and sandbox port.

**Data flow**: It receives a site record and the visibility that should be shown. It formats those values into a compact string and returns it.

**Call relations**: The member listing builder uses this when creating each row shown through the object system.

*Call graph*: called by 1 (_member_rows).


##### `_preview_url`  (lines 125–132)

```
def _preview_url(scoped: ExtensionContext, site: HostedSite) -> str | None
```

**Purpose**: Returns a signed preview-image link for a site when the last deploy captured a screenshot. If no screenshot exists, it returns nothing.

**Data flow**: It reads the site’s stored preview blob key and byte size. If either is missing, it returns `None`; otherwise it asks the extension context to create a temporary image preview URL.

**Call relations**: The listing builder calls this so site rows can include `preview_url` when there is an image for the portal to show.

*Call graph*: calls 1 internal fn (image_preview_url); called by 1 (_member_rows).


##### `SiteObjects._admin_can_apply`  (lines 153–154)

```
def _admin_can_apply(self, old: SiteSpec, spec: SiteSpec) -> bool
```

**Purpose**: Defines the one visibility change a workspace admin is allowed to make when they are not the creator: they may narrow a site to private. They may not make someone else’s site more widely visible.

**Data flow**: It receives the old site specification and the requested new specification. It returns true only when the old visibility was not private and the requested visibility is private.

**Call relations**: This is used by the shared object permission flow when deciding whether an admin can apply a requested change. It enforces the file’s rule that widening access belongs to the creator.


##### `SiteObjects._listed`  (lines 156–157)

```
def _listed(self, row: OwnedRow[GeneratedObjectOwner], query: ObjectListQuery) -> bool
```

**Purpose**: Decides whether a site row should appear in a general listing. Homepage-bound sites are hidden unless the caller explicitly filters for homepage bindings.

**Data flow**: It receives one prepared object row and the list query. If the row is not a homepage site, it is allowed. If it is a homepage site, it is allowed only when the query includes the homepage filter.

**Call relations**: The object listing flow uses this after rows are prepared. It keeps an agent’s homepage from showing up as an ordinary shared site unless the read specifically asks for it.


##### `SiteObjects.list`  (lines 159–169)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists site objects, with a convenience shortcut for agents asking for their own homepage. The caller can filter `homepage_agent` as `mine`, and this method rewrites that to the current turn’s agent ID.

**Data flow**: It receives the tool context and a list query. If the query asks for `homepage_agent=mine`, it replaces that filter with the actual agent ID from the current turn, then passes the query to the base object-listing behavior.

**Call relations**: This method is the public listing entry for this object store. It performs the site-specific `mine` translation before handing off to the generic member-readable object listing machinery.

*Call graph*: 1 external calls (replace).


##### `SiteObjects._member_rows`  (lines 171–217)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Builds the full set of site rows that the object system can then filter and display for a member. Each row includes ownership, visibility, URLs, preview links, and useful fields such as conversation and deploy generation.

**Data flow**: It reads all hosted sites for the workspace, names them, fetches agent visibility levels, and looks up owner email addresses. For each site, it computes the effective visibility, builds an owner record, adds fields such as `site_url`, `preview_url`, `homepage_agent`, and whether the site is `mine`, and returns the prepared rows.

**Call relations**: The generic listing and reading system calls this to get the raw site rows for a member. It pulls together helpers for naming, summaries, preview links, workspace access, site storage, and homepage visibility so the rest of the object system sees a uniform table.

*Call graph*: calls 6 internal fn (_named, _preview_url, _sites, _summary, _workspace, effective_visibility); 4 external calls (__init__, __init__, owner_emails, site_url).


##### `SiteObjects.member_conversation_rows`  (lines 219–244)

```
async def member_conversation_rows(self, ext: ExtensionContext | None, conversation_id: UUID, *, member_id: UUID, admin: bool, limit: int) -> tuple[ConversationObjectGrant, ...]
```

**Purpose**: Reports which site objects should be visible inside a particular conversation for a particular member. This lets conversation views know which deployed sites belong there and can be mentioned or shown.

**Data flow**: It receives a conversation ID, member ID, admin flag, and limit. It reads agent visibility levels, asks the site registry for sites visible in that conversation, converts each site to its official object name, and returns grant records saying the content is visible.

**Call relations**: Conversation-level object discovery calls this when it needs site objects for one conversation. It asks the storage layer for the correct visible sites, including workspace-visible homepage agents, and then hands back object grants to the conversation object system.

*Call graph*: calls 3 internal fn (_sites, _workspace, site_object_name); 1 external calls (__init__).


##### `SiteObjects._member_object`  (lines 246–269)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[SiteSpec] | None
```

**Purpose**: Builds the detailed object view for one site when a member reads it. The detail includes its current effective visibility and a link back to the conversation that created it.

**Data flow**: It receives an object name, owner information, and the member context. It looks up the site; if none exists, it returns nothing. If found, it computes effective visibility, wraps that in a `SiteSpec`, attaches timestamps, adds a `created_in` link to the conversation, and returns the detail object.

**Call relations**: The object read flow calls this after a site row has been selected. It depends on `_find` for lookup and on `effective_visibility` so homepage-bound sites report the agent-controlled visibility.

*Call graph*: calls 3 internal fn (_find, _workspace, effective_visibility); 4 external calls (__init__, __init__, __init__, __init__).


##### `SiteObjects._status`  (lines 271–298)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns operational details for a site, such as its live URL, port, creator, deploy generation, homepage binding, and any stored source files. This is the information a tool or user needs to inspect or update a deployed site.

**Data flow**: It receives the tool context, object name, and owner. It finds the site; if absent, it returns nothing. It builds a status dictionary with the site URL and metadata. If the site has a saved source manifest, it materializes those files into the sandbox and adds the path and file list to the status.

**Call relations**: The object status flow calls this when someone asks for more actionable information about a site. It hands off URL construction to the surface helper and source restoration to `materialize_source` when source files are available.

*Call graph*: calls 2 internal fn (_find, _workspace); 2 external calls (materialize_source, site_url).


##### `SiteObjects._apply_owned`  (lines 300–336)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SiteSpec, old: SiteSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: Applies an allowed visibility change to an existing site. It refuses creation through this path, refuses changes to homepage-bound sites, and creates a share card when an old site becomes public and has a stored preview image.

**Data flow**: It receives the requested spec, previous spec, owner, and context. If there was no existing site, it rejects the request because sites must come from deployment. It finds the site, compares the requested visibility to the effective current visibility, refuses homepage changes, stores the new visibility, and may generate a public sharing card from the stored screenshot.

**Call relations**: The generic object apply/update flow calls this after permission checks. It uses `_find`, `_sites`, workspace agent visibility, and `effective_visibility` to enforce the site rules, then hands off to the share-card code only when publishing a public site needs a card.

*Call graph*: calls 4 internal fn (_find, _sites, _workspace, effective_visibility); 2 external calls (__init__, draw_from_stored_shot).


##### `SiteObjects._delete_owned`  (lines 338–342)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: Unhosts an existing site by removing its registration. After this, the permanent site link should no longer resolve through the site registry.

**Data flow**: It receives the tool context, object name, and owner. It finds the matching hosted site, raises an error if it disappeared mid-operation, and then unregisters that site by conversation ID and site name.

**Call relations**: The generic object delete flow calls this after confirming the caller is allowed to unhost. It uses `_find` for name lookup and `_sites` to perform the registry removal.

*Call graph*: calls 2 internal fn (_find, _sites).


##### `SiteObjects._find`  (lines 344–345)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> HostedSite | None
```

**Purpose**: Finds one hosted site by its workspace object name. It is the file’s simple bridge from object names back to stored site records.

**Data flow**: It receives the extension context and an object name. It reads all hosted sites, builds the name-to-site mapping, and returns the matching site record or nothing if no match exists.

**Call relations**: Object detail, status, update, and delete all call this before acting on a named site. It uses the same `_named` mapping as listings, so all paths agree on how site object names are interpreted.

*Call graph*: calls 2 internal fn (_named, _sites); called by 4 (_apply_owned, _delete_owned, _member_object, _status).


### `extensions/sources/ufo_ext_sources/pages.py`

`domain_logic` · `request handling`

A “page” here is one document brought in by a content-sync driver from an outside source, such as an issue tracker or another content provider. This file is the safe public window onto those synced rows. Without it, users and tools would not have a standard way to browse synced pages, read a page body, or remove a page through the same pipeline that cleans up related search/index data.

The file defines the shape of a page response with PageSpec, then wraps raw database-like page records in a small _Page helper. That helper turns internal IDs, timestamps, source names, and body references into user-facing object fields.

PageObjects is the main store for the object kind. Listing pages asks the extension context for only the pages the caller is allowed to see, then returns compact rows. Getting one page first finds the page, reads at most 65,536 bytes from blob storage, decodes it as UTF-8 text, and then checks the page is still current and readable before returning it. That final check matters: it avoids handing back content that changed or became invisible while it was being read. Create and update are refused because pages come only from syncing. Delete means “forget”: it tombstones the synced page, and only a workspace admin can do it.

#### Function details

##### `_require_ext`  (lines 62–65)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This small guard makes sure the tool request has an ExtensionContext attached. The extension context is the file’s doorway to source pages, source bindings, and page-forgetting actions.

**Data flow**: It receives a ToolContext. If the context contains an extension context, it returns that object. If not, it stops immediately with a runtime error, because the page object code cannot do useful work without it.

**Call relations**: PageObjects._pages, PageObjects.get, and PageObjects.delete call this before they touch extension-owned source data. It is the checkpoint that prevents those flows from silently running without the workspace extension services they need.

*Call graph*: called by 3 (_pages, delete, get).


##### `_page_timestamp`  (lines 68–78)

```
def _page_timestamp(provider_value: str | None, row_value: datetime) -> str
```

**Purpose**: This function turns a page timestamp into one consistent UTC text format. It accepts either a provider-supplied timestamp string or, if that is missing, the workspace row’s own timestamp.

**Data flow**: It receives an optional timestamp from the outside provider and a fallback datetime from the stored row. If the provider value is present, it parses it and requires that it include a timezone. If the provider value is absent, it uses the row timestamp and adds UTC if needed. It returns an ISO-formatted UTC timestamp with microseconds.

**Call relations**: _Page.spec and _Page.fields use this whenever they expose created_at or updated_at. That keeps both detailed reads and list rows speaking the same time language.

*Call graph*: called by 2 (fields, spec); 2 external calls (fromisoformat, replace).


##### `_Page.name`  (lines 99–100)

```
def name(self) -> str
```

**Purpose**: This property gives the page its object name. For pages, the name is simply the page row’s UUID written as text.

**Data flow**: It reads the _Page id field and converts it to a string. Nothing else changes.

**Call relations**: The list and lookup flows rely on this name when showing page rows and matching a requested object name. It connects the internal page identity to the object API’s naming system.


##### `_Page.links`  (lines 102–110)

```
def links(self) -> tuple[ObjectLink, ...]
```

**Purpose**: This builds a link from a page back to the source binding that synced it, when that source has a known object name. The link helps readers understand where the page came from.

**Data flow**: It reads the page’s source_name. If there is no source name, it returns no links. If there is one, it creates a single ObjectLink saying the page was synced by that source object.

**Call relations**: PageObjects.get includes these links in the detailed page response. The function hands off to ObjectRef and ObjectLink to express the relationship in the standard object format.

*Call graph*: 2 external calls (__init__, __init__).


##### `_Page.spec`  (lines 112–125)

```
def spec(self, body: str, body_truncated: bool) -> PageSpec
```

**Purpose**: This creates the full user-facing description of a page, including metadata and the bounded body text. It is used when someone asks to read a specific page.

**Data flow**: It receives body text and a flag saying whether the body was cut short. It combines those with the _Page’s stored metadata, normalizes timestamps through _page_timestamp, and returns a PageSpec object.

**Call relations**: PageObjects.get calls this after it has safely read the body from blob storage and rechecked that the page is still current. _Page.spec is the final packaging step before the object detail is returned.

*Call graph*: calls 1 internal fn (_page_timestamp); 1 external calls (__init__).


##### `_Page.summary`  (lines 127–128)

```
def summary(self) -> str
```

**Purpose**: This creates a short one-line label for a page in list results. It gives enough context to recognize the page without opening it.

**Data flow**: It reads the title, source backend, stream, and visibility subject, joins them into a short sentence, and cuts it to the configured maximum length. It returns that text.

**Call relations**: PageObjects.list uses this when building each ObjectRow. It is the list-view equivalent of a label on a file folder.


##### `_Page.fields`  (lines 130–138)

```
def fields(self) -> dict[str, JsonValue]
```

**Purpose**: This returns the sortable and filterable metadata fields for a page list row. These are the small facts users can browse without fetching the full body.

**Data flow**: It reads the page’s source id, source backend, stream, title, and timestamps. It normalizes the timestamps through _page_timestamp and returns a dictionary of simple JSON-friendly values.

**Call relations**: PageObjects.list puts these fields into each listed row. The object kind definition also declares these same fields as the ones callers may filter or order by.

*Call graph*: calls 1 internal fn (_page_timestamp).


##### `PageObjects.list`  (lines 149–154)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns a paged list of visible synced pages. It is what the object system uses when a caller browses pages instead of opening one page in full.

**Data flow**: It receives the tool context and a list query. It asks _pages for the pages the caller may read, turns each one into an ObjectRow with a name, summary, and fields, then passes those rows and the query into object_page to produce the final page of results.

**Call relations**: This is the top-level list handler for the PAGE_OBJECT store. It depends on _pages to gather safe, enriched page records, then hands the rows to the SDK’s paging helper so filtering, ordering, and slicing follow normal object behavior.

*Call graph*: calls 1 internal fn (_pages); 2 external calls (__init__, object_page).


##### `PageObjects.get`  (lines 156–201)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[PageSpec] | None
```

**Purpose**: This returns the detailed view of one page, including a bounded copy of its body text. It is careful not to return stale or newly unauthorized content.

**Data flow**: It receives the tool context and requested page name. It finds the page, reads the body bytes from blob storage up to one byte past the limit, closes the stream if needed, trims to 65,536 bytes, and decodes UTF-8 safely. Then it asks the extension context for the page’s current readable state for this caller. If the page disappeared, changed revision, changed digest, changed body reference, or is no longer readable, it returns None. Otherwise it returns ObjectDetail with the PageSpec, timestamps, and links.

**Call relations**: This is the top-level read handler for a single page. It calls _find to locate the requested page, uses _require_ext to recheck live readable state through the extension context, and finally builds the standard ObjectDetail response.

*Call graph*: calls 3 internal fn (source_reader, _find, _require_ext); 1 external calls (__init__).


##### `PageObjects.status`  (lines 203–210)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This reports no separate status for pages. A synced page is either available through list/get or not available.

**Data flow**: It receives the context, object name, and optional expected generation value. It ignores them and returns None, meaning there is no extra status payload to show.

**Call relations**: The object framework can ask stores for status information. For this object kind, the meaningful operations are listing, getting, and forgetting, so the status path deliberately has nothing to hand off.


##### `PageObjects.apply`  (lines 212–221)

```
async def apply(self, ctx: ToolContext, name: str, spec: PageSpec, old: PageSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This refuses create and update requests for pages. Pages are produced by the content-sync driver, not authored through the object API.

**Data flow**: It receives the requested name, new spec, old spec, and optional expected generation value. Instead of changing anything, it raises VerbNotSupported with a message explaining that synced pages must come from registered sources.

**Call relations**: The object framework calls apply for create-or-update style operations. This handler blocks that route so all page content continues to come from the sync system rather than manual edits.

*Call graph*: 1 external calls (__init__).


##### `PageObjects.delete`  (lines 223–235)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This forgets a synced page, but only for a workspace admin. Forgetting tombstones the page so the normal page-change pipeline can clean up derived state such as indexes.

**Data flow**: It receives the context, page name, and optional expected generation value. It first asks whether the current speaker is an admin for this action. If not, it raises AdminRequired. If yes, it finds the page by name; if no such page exists, it raises a ValueError. When the page is found, it asks the extension context to forget that page id.

**Call relations**: This is the top-level delete handler for PAGE_OBJECT. It uses the ToolContext admin check for permission, _find to locate the page, and _require_ext to reach the extension method that performs the tombstone operation.

*Call graph*: calls 3 internal fn (require_speaking_admin, _find, _require_ext); 1 external calls (__init__).


##### `PageObjects._find`  (lines 237–238)

```
async def _find(self, ctx: ToolContext, name: str) -> _Page | None
```

**Purpose**: This looks up one visible page by its object name. It is a helper for operations that start with a user-supplied page name.

**Data flow**: It receives the context and a name string. It asks _pages for the current readable pages, scans them for one whose name matches, and returns that _Page. If none match, it returns None.

**Call relations**: PageObjects.get and PageObjects.delete call this before acting on a specific page. Because it searches the same visible page set produced by _pages, those operations do not accidentally act on pages outside the caller’s readable view.

*Call graph*: calls 1 internal fn (_pages); called by 2 (delete, get).


##### `PageObjects._pages`  (lines 240–268)

```
async def _pages(self, ctx: ToolContext) -> tuple[_Page, ...]
```

**Purpose**: This gathers the live synced pages the caller is allowed to see and enriches them with source information. It is the shared data-loading step behind listing and finding pages.

**Data flow**: It receives the tool context. It gets the extension context, reads all sources, builds a map from source id to backend name, and, for known connector sources, computes the source object name from the connector configuration. Then it asks for source pages using the caller’s source reader grant and turns each returned record into a _Page with page metadata, sync source details, revision data, body reference, and optional source link name.

**Call relations**: PageObjects.list calls this to build browse results, and PageObjects._find calls it when get or delete needs one page. It calls the ToolContext for the reader grant, the extension context for source/page data, ConnectorSourceConfig to understand connector settings, and binding_name to produce stable source object names.

*Call graph*: calls 2 internal fn (source_reader, _require_ext); called by 2 (_find, list); 3 external calls (__init__, model_validate, binding_name).


### Portal agent management
The web portal routes settings forms and user actions into the conversation-based agent write path and the agent object implementation.

### `extensions/web/ufo_ext_web/panels.py`

`orchestration` · `request handling`

The portal lets people change things such as agents, connections, schedules, and credentials from forms and buttons. This file makes sure those clicks do not become a separate, unsafe shortcut around the rest of the system. Instead, every write is turned into a prepared tool request and admitted as a normal turn in a durable conversation, like putting a signed work order into the same queue every time. That matters because the conversation is the audit trail, and because queued turns run in order instead of overlapping and fighting each other.

The file defines the shapes of allowed panel submissions, including which verbs may be used with which object kinds. It rejects malformed, oversized, or impossible requests before a turn is created. For agent updates, it fills in required fields the form may not have shown, so changing one setting does not accidentally erase the rest.

It also knows how to read the final answer from the turn. Most actions simply become “saved” or “not applied,” but a few actions return special information, such as a Slack install link, an iMessage connection instruction, a billing portal URL, or a rebuild status message.

Finally, it exposes the agent settings projection: the current agent configuration, available models, schema for editable fields, deployment limits, and admin-only audience information.

#### Function details

##### `ApplyIntent.kinds`  (lines 94–98)

```
def kinds(cls) -> frozenset[str]
```

**Purpose**: Returns the fixed set of object kinds that a panel is allowed to submit changes for. This keeps the web controls and the backend gate tied to the same list.

**Data flow**: It reads the type annotation on the model’s kind field → extracts the literal allowed values from that annotation → returns them as an immutable set of strings.

**Call relations**: This is the basic source of truth for the kind list. Other helpers use the same model-level knowledge so the portal does not invent controls for object kinds this lane will later reject.

*Call graph*: 1 external calls (get_args).


##### `ApplyIntent.applying_kinds`  (lines 101–103)

```
def applying_kinds(cls) -> frozenset[str]
```

**Purpose**: Returns the object kinds that can be created or updated with an apply action. It excludes kinds that are only deleted or connected through a special flow.

**Data flow**: It starts with all allowed kinds → removes delete-only kinds and connection kinds → returns the remaining set for apply-capable panels.

**Call relations**: The web surface code calls this while building kind payloads, so the page can show create or edit controls only where this intent lane will accept them.

*Call graph*: called by 1 (_kind_payload).


##### `ApplyIntent.deleting_kinds`  (lines 106–112)

```
def deleting_kinds(cls) -> frozenset[str]
```

**Purpose**: Returns the object kinds that can be deleted through the panel lane. In this file, every named kind can be deleted, though some cannot be created or edited here.

**Data flow**: It reads the same allowed kind set as the validator → returns that full set as the delete-capable kinds.

**Call relations**: The web surface code calls this while preparing object-kind information for the portal. That lets the page draw delete controls in step with the backend’s rules.

*Call graph*: called by 1 (_kind_payload).


##### `ApplyIntent._verb_pairs_with_its_kind`  (lines 115–136)

```
def _verb_pairs_with_its_kind(self) -> 'ApplyIntent'
```

**Purpose**: Checks that a submitted verb makes sense for the object kind. For example, a connection can be connected or deleted, but not edited like a normal object.

**Data flow**: It receives an already parsed intent → compares the verb, kind, spec, and create_only flag against the allowed combinations → either returns the same intent or raises a validation error.

**Call relations**: Pydantic, the data validation library, runs this automatically when an ApplyIntent is built. It is an early safety gate before submit_intent can turn a browser request into a real tool call.


##### `Unlock._names_offered_tiles_and_a_drawn_mark`  (lines 325–334)

```
def _names_offered_tiles_and_a_drawn_mark(self) -> 'Unlock'
```

**Purpose**: Checks that each first-run unlock card refers only to provider tiles the portal actually offers, and that its icon can be drawn. This prevents broken cards with missing labels or icons.

**Data flow**: It receives an Unlock object → checks its icon against the known icon set and each provider name against the first-run catalog → returns the object or raises a validation error.

**Call relations**: This runs automatically when unlock objects are created. It protects the static catalog used by start screens from drifting out of sync with the provider tiles.


##### `Unlock.missing`  (lines 336–340)

```
def missing(self, held: frozenset[str]) -> tuple[str, ...]
```

**Purpose**: Tells the portal which provider accounts a member still needs to connect before an unlock can run. It treats each need group as “any one of these is enough.”

**Data flow**: It receives the set of provider names the member already has → walks each requirement group → for unmet groups, chooses the first preferred provider name → returns those missing names in catalog order.

**Call relations**: Start or onboarding views can use this to decide whether an unlock is ready now or should be shown as something the member can unlock by connecting more accounts.


##### `_action_intent`  (lines 513–524)

```
def _action_intent(kind: str, name: str | None, action: str, body: dict[str, JsonValue]) -> ToolIntent
```

**Purpose**: Builds the exact tool request for a portal-presented action. It binds the target from the route, not from the browser body, so the browser cannot redirect the action somewhere else.

**Data flow**: It receives a kind, optional object name, action name, and action body → creates an object_action input with route-owned target fields and the body under input → returns a ToolIntent ready to admit as a turn.

**Call relations**: submit_action calls this after it has checked that the action exists and the body is safe. The resulting ToolIntent is then admitted to the portal action conversation.

*Call graph*: called by 1 (submit_action); 1 external calls (__init__).


##### `_tool_intent`  (lines 527–562)

```
def _tool_intent(submitted: ApplyIntent) -> ToolIntent
```

**Purpose**: Turns a validated panel intent into the lower-level tool call the engine understands. It maps connect, delete, detach, and apply-style submissions to their proper tools.

**Data flow**: It receives an ApplyIntent → chooses the matching tool name and input shape → for apply, serializes the object data into a YAML manifest → returns a ToolIntent.

**Call relations**: submit_intent calls this only after request parsing and refusal checks pass. The returned tool intent becomes the durable turn input and audit record.

*Call graph*: called by 1 (submit_intent); 2 external calls (__init__, safe_dump).


##### `_outcome`  (lines 565–581)

```
def _outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Converts a finished turn into the simple JSON answer the portal expects. It says whether the change applied, gives a friendly message, and includes the turn id for traceability.

**Data flow**: It receives a terminal frame and turn id → checks whether the frame ended successfully, requested credentials, or failed → returns a JSON response with applied status, message, and turn id.

**Call relations**: This is the default result reader. _intent_result uses it for ordinary panel intents, and special readers such as _slack_outcome, _imessage_outcome, _portal_outcome, and _rebuild_outcome fall back to it when their turn did not finish successfully.

*Call graph*: called by 6 (_action_outcome, _imessage_outcome, _intent_result, _portal_outcome, _rebuild_outcome, _slack_outcome); 1 external calls (JSONResponse).


##### `_slack_outcome`  (lines 584–599)

```
def _slack_outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Reads the special result from a Slack connect action, including the installation URL if one was created. This lets the portal send the user to Slack’s install flow.

**Data flow**: It receives a terminal frame and turn id → if the turn failed, delegates to the normal outcome reader → otherwise extracts a stated JSON object from the frame text → returns applied status, message or hint, URL, and turn id.

**Call relations**: This function is selected through the action outcome table for Slack connect actions. It builds on _outcome for failure cases and is used indirectly when submit_action waits for a Slack action’s terminal frame.

*Call graph*: calls 1 internal fn (_outcome); 2 external calls (loads, JSONResponse).


##### `_imessage_outcome`  (lines 602–623)

```
def _imessage_outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Reads the special result from an iMessage connect action. It returns the connection state, user instruction, and optional opt-in link in a form the portal can display.

**Data flow**: It receives a terminal frame and turn id → if the turn failed, uses the normal outcome reader → otherwise parses the JSON state stated in the frame text → validates the expected fields → returns applied status, instruction, optional URL, and turn id.

**Call relations**: This is chosen for iMessage connect actions through the action outcome table. It is part of submit_action’s final response path for that special action.

*Call graph*: calls 1 internal fn (_outcome); 2 external calls (loads, JSONResponse).


##### `_portal_outcome`  (lines 626–640)

```
def _portal_outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Reads the special result from the billing portal action. It extracts the billing provider’s portal URL so the web page can open it for the user.

**Data flow**: It receives a terminal frame and turn id → if the turn failed, delegates to _outcome → otherwise parses the JSON object in the frame text → pulls out the portal URL → returns a JSON response with the URL and turn id.

**Call relations**: _action_outcome calls this when the workspace billing action asks for the portal operation. It handles the one billing case where a plain saved/refused message is not enough.

*Call graph*: calls 1 internal fn (_outcome); called by 1 (_action_outcome); 2 external calls (loads, JSONResponse).


##### `_rebuild_outcome`  (lines 643–650)

```
def _rebuild_outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Returns the tool’s own message for rebuild actions, because pressing rebuild usually queues background work rather than changing visible text immediately.

**Data flow**: It receives a terminal frame and turn id → if the turn failed, uses the normal outcome reader → otherwise returns applied true with the frame’s text as the message.

**Call relations**: This function is selected for report and page rebuild actions through the action outcome table. It lets submit_action answer with the specific queued-work sentence produced by the tool.

*Call graph*: calls 1 internal fn (_outcome); 1 external calls (JSONResponse).


##### `_action_outcome`  (lines 664–671)

```
def _action_outcome(kind: str, action: str, body: dict[str, JsonValue], frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Chooses the right way to translate an action’s terminal frame into an HTTP response. Most actions use the default outcome, but known special actions get custom readers.

**Data flow**: It receives the target kind, action name, original body, terminal frame, and turn id → checks for the billing portal special case → otherwise looks up a custom reader or falls back to the default → returns the chosen JSON response.

**Call relations**: submit_action calls this after the admitted action turn reaches its terminal frame. It dispatches to _portal_outcome for billing portal requests and otherwise to the registered special readers or _outcome.

*Call graph*: calls 2 internal fn (_outcome, _portal_outcome); called by 1 (submit_action).


##### `_complete_agent_spec`  (lines 702–729)

```
async def _complete_agent_spec(ctx: SurfaceContext, submitted: ApplyIntent, submitted_fields: frozenset[str], agent_id: UUID, member_id: UUID) -> ApplyIntent | Response
```

**Purpose**: Fills in required agent settings that a partial settings form did not submit. This lets a form update one part of an agent without needing to resend every required field.

**Data flow**: It receives context, the submitted intent, the fields actually submitted, agent id, and member id → if the intent is not a partial agent apply, returns it unchanged → otherwise reads the current agent detail → merges current required values underneath the submitted spec → returns the completed intent or an error response.

**Call relations**: _prepare_panel_intent calls this after parsing a panel request. It asks SurfaceContext for the current agent detail and produces an intent that _tool_intent can safely serialize.

*Call graph*: calls 1 internal fn (agent_detail); called by 1 (_prepare_panel_intent); 2 external calls (model_copy, JSONResponse).


##### `_intent_refusal`  (lines 732–749)

```
async def _intent_refusal(ctx: SurfaceContext, submitted: ApplyIntent, submitted_fields: frozenset[str]) -> Response | None
```

**Purpose**: Rejects panel intents that are structurally valid but impossible in the current deployment. For example, it catches an unknown model name or a credential slot that does not exist.

**Data flow**: It receives context, a submitted intent, and the submitted field names → checks agent model and sandbox-size constraints, and checks credential slot names when needed → returns a JSON refusal response or None if the intent may continue.

**Call relations**: _prepare_panel_intent calls this after completing any partial agent spec. It reads available credential slots from the surface context when validating credential deletion.

*Call graph*: calls 1 internal fn (list_credential_slots); called by 1 (_prepare_panel_intent); 1 external calls (JSONResponse).


##### `_prepare_panel_intent`  (lines 752–781)

```
async def _prepare_panel_intent(ctx: SurfaceContext, request: Request, agent_id: UUID, member_id: UUID) -> ApplyIntent | Response
```

**Purpose**: Reads and validates the raw HTTP request for a panel intent before any durable turn is created. It is the main front-door safety check for form submissions.

**Data flow**: It reads the request body → rejects oversized input → parses JSON into a PanelIntent and ApplyIntent → checks frame restrictions for connect actions → records which agent fields were actually submitted → completes partial agent specs → applies deployment-specific refusal checks → returns either a prepared ApplyIntent or an HTTP error response.

**Call relations**: submit_intent calls this first. It uses _complete_agent_spec and _intent_refusal to turn a browser post into a safe, ready-to-dispatch intent, or to stop the request before it enters the conversation queue.

*Call graph*: calls 3 internal fn (frame_admits, _complete_agent_spec, _intent_refusal); called by 1 (submit_intent); 3 external calls (loads, JSONResponse, body).


##### `_oversized_manifest`  (lines 784–798)

```
def _oversized_manifest(intent: ToolIntent) -> Response | None
```

**Purpose**: Performs a second size check after an apply intent has been converted into its serialized manifest. This catches cases where the final tool input is too large even if the original request was acceptable.

**Data flow**: It receives a ToolIntent → if it is not an object_apply intent, returns None → otherwise reads the manifest string, measures its encoded byte length → returns an HTTP 413 response if too large, or None if it fits.

**Call relations**: submit_intent calls this after _tool_intent. It protects the conversation admission path from storing an oversized serialized object manifest.

*Call graph*: called by 1 (submit_intent); 1 external calls (JSONResponse).


##### `_intent_result`  (lines 801–828)

```
async def _intent_result(ctx: SurfaceContext, turn_id: UUID) -> Response
```

**Purpose**: Waits for an admitted panel intent turn to finish and converts its final state into an HTTP response. This is what makes the form submit feel synchronous to the user.

**Data flow**: It receives context and a turn id → opens the turn’s frame stream and waits up to the configured timeout → if a terminal frame arrives, returns _outcome → if the turn parks, returns the parked message → if time runs out, returns a timeout response saying the change is still being applied.

**Call relations**: submit_intent calls this after admitting the tool intent. It listens through SurfaceContext.tail and uses _outcome for the normal terminal result.

*Call graph*: calls 2 internal fn (tail, _outcome); called by 1 (submit_intent); 2 external calls (timeout, JSONResponse).


##### `submit_intent`  (lines 831–867)

```
async def submit_intent(ctx: SurfaceContext, request: Request, agent_id: UUID, member_id: UUID, email: str) -> Response
```

**Purpose**: Accepts one prepared panel mutation, queues it on the member’s portal intent conversation, and waits for the result. This is the main write path for ordinary panel forms.

**Data flow**: It receives the surface context, HTTP request, agent id, member id, and email → prepares and validates the request → converts it to a ToolIntent → checks serialized size → finds or creates the member’s portal action conversation → gives that conversation a readable title → admits the turn with the member as speaker → waits for and returns the final result.

**Call relations**: This is the public handler that ties together _prepare_panel_intent, _tool_intent, _oversized_manifest, and _intent_result. It uses the surface context to create or reuse the durable conversation and admit the turn.

*Call graph*: calls 7 internal fn (admit, conversation_for, retitle_conversation, _intent_result, _oversized_manifest, _prepare_panel_intent, _tool_intent); 1 external calls (conversation_audience).


##### `submit_action`  (lines 870–959)

```
async def submit_action(ctx: SurfaceContext, request: Request, agent_id: UUID, member_id: UUID, email: str, *, kind: str, name: str | None, action: str) -> Response
```

**Purpose**: Accepts one action that the portal has presented for an object or collection, queues it as a conversation turn, and returns the action’s final answer. It prevents the browser body from changing the target named by the route.

**Data flow**: It reads the raw body → rejects oversized or malformed JSON → refuses any body field that tries to name route-owned envelope values → checks that the requested action is actually offered for the target → checks whether framed app pages may call it → builds an object_action intent → admits it to the portal action conversation → tails the turn until terminal, parked, or timed out → returns the appropriate action outcome.

**Call relations**: This handler uses _action_intent to build the safe tool request and _action_outcome to interpret the terminal frame. It also asks SurfaceContext for available object actions, frame permissions, the conversation, admission, and the turn stream.

*Call graph*: calls 8 internal fn (admit, conversation_for, frame_admits, object_actions, retitle_conversation, tail, _action_intent, _action_outcome); 5 external calls (timeout, loads, conversation_audience, JSONResponse, body).


##### `_update_schema`  (lines 962–974)

```
def _update_schema(sandbox_sizes: tuple[str, ...]) -> dict[str, JsonValue]
```

**Purpose**: Builds the schema used by the settings form for editable agent fields. It removes fields the page renders with custom controls or cannot offer in this deployment.

**Data flow**: It receives the available sandbox sizes → starts from AgentSpec’s JSON schema → removes prompt, icon, purpose, input/output schema fields, and sandbox_size when sandbox sizes are not offered → returns the trimmed schema.

**Call relations**: agent_settings calls this when building the settings projection. The portal can then render form fields from the same underlying AgentSpec definition instead of a separate hand-written list.

*Call graph*: called by 1 (agent_settings); 1 external calls (model_json_schema).


##### `agent_settings`  (lines 977–1021)

```
async def agent_settings(ctx: SurfaceContext, agent_id: UUID, member_id: UUID, *, admin: bool, archivable: bool) -> Response
```

**Purpose**: Returns the data the portal needs to show an agent’s settings page. It includes current agent values, deployment capabilities, available models, editable schema, and admin-only audience information.

**Data flow**: It receives context, agent id, member id, and flags for admin and archivable status → reads the agent detail → if missing, returns 404 → if admin, reads granted web audience emails → builds a JSON response containing agent metadata, deploy limits, model choices, current spec values, schema, and optional audience.

**Call relations**: This is the read-side companion to the write handlers in this file. It uses SurfaceContext.agent_detail, _update_schema, and the web audience helpers to produce the settings projection the portal displays.

*Call graph*: calls 2 internal fn (agent_detail, _update_schema); 5 external calls (__init__, JSONResponse, Response, granted_emails, web_extension).


### `core/src/ufo/runtime/kinds/agents.py`

`domain_logic` · `request handling`

An agent in this system is not just a chat bot running right now. It is a saved workspace object, like an app record in a database, with a name, owner, prompt, model choice, internet policy, sandbox size, portal visibility, and optional schemas that describe what spawned tasks must receive and return. This file is the rulebook for that object.

Without this file, the system would not know who may create or change agents, how to show them in the object tools, or how to safely retire one without losing its history. The main agent has special protection: it belongs to the whole workspace, stays visible to the whole workspace, and cannot be archived.

The central class, AgentObjects, plugs agents into the shared object system. It lists live agents by default, reads full details, reports status, creates new agent rows, updates existing rows, and archives agents when they are “deleted.” Archiving is more like moving an app into a locked storeroom than shredding it: the name is freed, but conversations, grants, schedules, and records stay attached to the same database row. RestoreApplication provides the tool action that brings an archived app back under an available name.

The file also validates important choices before saving them. For example, it rejects unknown model names, empty prompts, invalid object names, and bad JSON Schemas for spawned-agent input or output.

#### Function details

##### `_effective_model`  (lines 70–75)

```
def _effective_model(ctx: ToolContext, stored: str) -> str
```

**Purpose**: Shows the real model an agent is using when people read its status. If the saved setting says “auto,” it reports the model that the current run has already resolved to, instead of exposing the placeholder.

**Data flow**: It receives the current tool context and the model value stored on the agent row. If the stored value is the special automatic-model marker, it reads the concrete model from the current agent in the context; otherwise it returns the stored value unchanged.

**Call relations**: Status and listing summaries call this helper when they need to present a model name to a person. It keeps display code from accidentally turning an “auto” setting into a fixed model during read-and-write workflows.

*Call graph*: called by 2 (_status, _agent_summary).


##### `AgentSpec._declared_schema`  (lines 166–171)

```
def _declared_schema(cls, value: dict[str, JsonValue] | None, info: ValidationInfo) -> dict[str, JsonValue] | None
```

**Purpose**: Checks that an optional input or output schema is a valid declared contract for spawned agent work. A schema here means a JSON Schema, which is a machine-readable description of the shape of data.

**Data flow**: It receives the schema value being assigned and validation context that says which field is being checked. If the value is present, it passes it to the contract checker; if the checker accepts it, the same schema is returned.

**Call relations**: Pydantic, the data validation library used for AgentSpec, calls this automatically when an AgentSpec is built. It hands schema checking off to check_declared_schema so bad task input/output contracts are rejected before they can be saved.

*Call graph*: 1 external calls (check_declared_schema).


##### `_known_model`  (lines 174–191)

```
def _known_model(ctx: ToolContext, model: str, reasoning: ReasoningEffort) -> None
```

**Purpose**: Stops an agent from being saved with a model setting that the deployment cannot actually run. It also catches a reasoning setting that a specific model does not allow.

**Data flow**: It receives the tool context, the requested model name, and the requested reasoning effort. It resolves “auto” to the deployment’s automatic model, checks the model registry, and raises a clear error if the model is unknown or if reasoning is disabled for a model that requires it. It returns nothing when the settings are acceptable.

**Call relations**: Agent creation and agent editing both call this before writing to the database. This is important because later turns depend on the saved model; rejecting bad settings at write time gives the member a fixable error immediately.

*Call graph*: called by 2 (_create, _mutate).


##### `_agent_summary`  (lines 194–199)

```
def _agent_summary(ctx: ToolContext, row: sa.Row) -> str
```

**Purpose**: Builds the short human-readable line shown for an agent in lists. It tells whether the agent is archived, whether it is the main agent, which model it uses, and whether public internet is allowed.

**Data flow**: It receives the current context and a database row. It asks _effective_model for the display model, then formats either an archived summary with a date or a live summary with model and internet information.

**Call relations**: AgentObjects._owned_rows calls this while turning database rows into object-list entries. It keeps list display wording in one place and relies on _effective_model so “auto” agents are shown accurately.

*Call graph*: calls 1 internal fn (_effective_model); called by 1 (_owned_rows).


##### `AgentObjects._admin_can_apply`  (lines 214–215)

```
def _admin_can_apply(self, old: AgentSpec, spec: AgentSpec) -> bool
```

**Purpose**: Says that an administrator is allowed to apply any valid agent change once the shared ownership rules have already let them through. In plain terms, admins have full edit power over agent specs.

**Data flow**: It receives the old spec and the proposed new spec, but does not need to inspect them. It always returns true.

**Call relations**: This method is part of the shared member-owned object framework. The base object machinery consults it when deciding whether an admin may apply a change; this file’s answer is always yes.


##### `AgentObjects.list`  (lines 217–223)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists agents, defaulting to live agents only. Archived agents are included only when the caller explicitly asks for them with a filter.

**Data flow**: It receives a tool context and a list query. If the query does not mention archived status, it copies the query and adds an archived=false filter, then delegates the actual listing work to the shared parent class.

**Call relations**: This is the public listing path for agent objects. It lightly adjusts the caller’s query, then hands off to the generic object-listing system so agents follow the same object interface as other kinds.

*Call graph*: 1 external calls (replace).


##### `AgentObjects._owned_rows`  (lines 225–257)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[ObjectOwner], ...]
```

**Purpose**: Reads the workspace’s agent rows from the database and converts them into the ownership-aware rows used by the object system. This is where each agent gets its list name, summary, owner, sharing status, and extra list fields.

**Data flow**: It reads the current workspace id, queries the agent table, and receives database rows. For each row it builds an OwnedRow with a summary, an ObjectOwner, and fields such as id and archived date. The result is a tuple of rows ready for listing and permission checks.

**Call relations**: The shared object listing machinery calls this when it needs the available agent objects. It calls _agent_summary for the display text and marks an agent as shared when it is workspace-visible or when the current turn belongs to that agent.

*Call graph*: calls 1 internal fn (_agent_summary); 5 external calls (__init__, __init__, select, workspace_tx, ws_current).


##### `AgentObjects._detail`  (lines 259–292)

```
async def _detail(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> ObjectDetail[AgentSpec] | None
```

**Purpose**: Returns the full saved configuration for one named agent. This is the detailed view used when someone reads an agent object, not just a short list entry.

**Data flow**: It receives the context, the agent name, and ownership information. It loads the database row, turns the row’s settings into an AgentSpec, adds timestamps, and, for non-main agents, adds a link showing that they are scoped to the main agent. If no row exists, it returns nothing.

**Call relations**: The object system calls this when a caller asks for one agent’s details. It depends on _row for the database lookup and packages the result into the standard ObjectDetail shape.

*Call graph*: calls 1 internal fn (_row); 4 external calls (__init__, __init__, __init__, __init__).


##### `AgentObjects._status`  (lines 294–313)

```
async def _status(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns machine-readable status facts for one named agent. This is separate from the full editable spec and includes operational facts like archived state, owner id, and provisioning information.

**Data flow**: It receives the context, agent name, and owner information. It loads the row, converts stored values such as model and dates into readable JSON-friendly values, and returns a dictionary. If the agent does not exist, it returns nothing.

**Call relations**: The object system calls this when it needs status for an agent object. It uses _row to fetch the saved record and _effective_model so the reported model is the one the agent actually runs when its stored setting is “auto.”

*Call graph*: calls 2 internal fn (_row, _effective_model).


##### `AgentObjects._apply_owned`  (lines 315–326)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: AgentSpec, old: AgentSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: Chooses between creating a new agent and editing an existing one. It is the single write path after the ownership gate has decided the caller is allowed to apply the requested object change.

**Data flow**: It receives the target name, the requested AgentSpec, any old spec, and owner information. If there is no old spec, it treats the request as a create; otherwise it treats it as an update. It returns nothing after the database operation completes.

**Call relations**: The shared object framework calls this after resolving permissions. This method then sends the work to _create for new names or _mutate for existing agents.

*Call graph*: calls 2 internal fn (_create, _mutate).


##### `AgentObjects._mutate`  (lines 328–406)

```
async def _mutate(self, ctx: ToolContext, name: str, spec: AgentSpec) -> None
```

**Purpose**: Updates an existing agent’s saved settings. It carefully preserves fields that were omitted on an update, such as prompt or icon, so a partial update does not accidentally erase them.

**Data flow**: It receives the context, agent name, and proposed spec. It loads the existing row, checks that the requested model and reasoning are usable, computes the next values for each field, enforces special rules such as the main agent staying workspace-visible, rejects an empty prompt, and writes changed values to the database. If nothing changed, it exits without writing.

**Call relations**: _apply_owned calls this for edits. It relies on _row for the current state, _known_model for model validation, and the workspace transaction helper for the database update.

*Call graph*: calls 2 internal fn (_row, _known_model); called by 1 (_apply_owned); 4 external calls (__init__, update, workspace_tx, ws_current).


##### `AgentObjects._create`  (lines 408–453)

```
async def _create(self, ctx: ToolContext, name: str, spec: AgentSpec) -> None
```

**Purpose**: Creates a new non-main agent owned by the speaking member. It saves only the submitted configuration and deliberately does not copy grants, credentials, sources, or memory from any other agent.

**Data flow**: It receives the context, new name, and requested spec. It requires a speaking member, checks that a non-empty prompt is present, validates the model settings, gathers existing icons so it can choose an unused-looking automatic icon if needed, and inserts a new agent row. If another row already has the same name, it raises a clear name-taken error.

**Call relations**: _apply_owned calls this when an apply request names no existing agent. It uses the tool context to identify the creator, _known_model for validation, auto_agent_icon for default icon choice, and the database transaction to insert the row.

*Call graph*: calls 2 internal fn (_known_model, require_speaker); called by 1 (_apply_owned); 6 external calls (insert, select, workspace_tx, ws_current, auto_agent_icon, uuid4).


##### `AgentObjects.delete`  (lines 455–465)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Starts the delete operation for an agent, but blocks deletion of the main agent. In this system, deleting an agent means archiving it, not erasing its record.

**Data flow**: It receives the context, agent name, and optional expected generation value. It first loads the row; if the row is the main agent, it raises a not-supported error. Otherwise it delegates the rest of deletion to the parent object system.

**Call relations**: Callers use this as the delete entry point for agent objects. It performs the agent-specific main-agent protection before handing off to the shared delete flow, which will eventually use _delete_owned after permission checks.

*Call graph*: calls 1 internal fn (_row); 1 external calls (__init__).


##### `AgentObjects._delete_owned`  (lines 467–497)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: Archives a non-main agent after ownership checks have passed. Archiving hides it from normal use, prevents new turns from starting, and frees its old name while keeping its history attached.

**Data flow**: It receives the context, current agent name, and owner information. It loads the row, rejects missing, main, or already-archived agents, then updates the row: the live name becomes a durable archived name based on the id, the old name is saved as archived_name, and archived_at is set. It returns nothing.

**Call relations**: The shared delete flow calls this once it has decided the caller is allowed to delete the object. It uses _row to confirm the current state and writes the archive update inside a workspace database transaction.

*Call graph*: calls 1 internal fn (_row); 5 external calls (__init__, __init__, update, workspace_tx, ws_current).


##### `AgentObjects._row`  (lines 499–540)

```
async def _row(self, name: str) -> sa.Row | None
```

**Purpose**: Fetches one agent database row by name within the current workspace. It is the common lookup helper used by detail, status, edit, delete, and archive operations.

**Data flow**: It receives an agent name. It reads the current workspace id, queries the agent table for that name, includes many saved settings and timestamps, and also includes the current workspace’s main agent name as an extra field. It returns one row or nothing.

**Call relations**: Many AgentObjects methods call this before doing their work. By centralizing the query here, the rest of the file can make decisions using the same complete view of an agent record.

*Call graph*: called by 5 (_delete_owned, _detail, _mutate, _status, delete); 3 external calls (select, workspace_tx, ws_current).


##### `RestoreApplication.restore`  (lines 555–611)

```
async def restore(self, ctx: ToolContext, args: RestoreApplicationInput) -> ToolResult
```

**Purpose**: Restores an archived agent app so it can run again, under its old name or a new available name. Only the app’s owner or a workspace admin may do this.

**Data flow**: It receives the tool context and an input object containing the desired new name. It checks that the tool is bound to an archived-agent target, requires a speaking member, validates the requested name, extracts the archived agent id from the durable archived name, loads the row, checks ownership or admin rights, and updates the row back to live state. It returns a ToolResult with a short confirmation message.

**Call relations**: This is used by the restore_application tool bound to archived agent objects. It reads and writes the agent table directly, asks the tool context whether the speaker is an admin when needed, and returns user-facing text through ToolResult.

*Call graph*: calls 2 internal fn (require_speaker, speaker_is_admin); 9 external calls (__init__, __init__, __init__, select, update, workspace_tx, validate_object_name, ws_current, UUID).


### Object runtime safeguards
The shared runtime defines the object API, package surfaces, prompt-change governance, and scoped agent context used by object handlers.

### `core/src/ufo/runtime/objects.py`

`domain_logic` · `startup registration and request handling`

A workspace object is like a labeled file card: it has a kind, a name, and a structured spec. Extensions can register new kinds, but this file makes every kind follow the same rules before it is used. It checks names, checks that specs can be safely stored and shown back to users, rejects secret fields, and prevents two extensions from claiming the same kind or action name.

The file also provides the tool-facing verbs: list the catalog or rows, get one object, explain how to write one, apply a YAML manifest to create or update one, delete one, and discover or invoke extra object actions. The actual storage remains with each kind's store. Core validates the envelope, then calls the kind's own code.

A large part of the file is about safety. Member-owned objects pass through a visibility gate, so private rows only appear to their owner or admins unless shared. Some objects carry a generation value, like a claim ticket, so an update can be refused if the row changed after it was read. Mutations are journaled before they run, so failed or retried writes have a reliable audit trail.

#### Function details

##### `_ObjectCursor.validate_rank`  (lines 195–200)

```
def validate_rank(self) -> '_ObjectCursor'
```

**Purpose**: Checks that a saved list cursor has the right kind of value for its sort type. This prevents a broken or forged cursor from confusing pagination.

**Data flow**: It reads the cursor's rank and value → compares them against the allowed pairings, such as text rank with a string value or numeric rank with a number → returns the cursor unchanged if valid, or raises an error if not.

**Call relations**: This runs automatically when a cursor is decoded inside object_page. It protects the later ordering comparison from receiving mismatched data.


##### `object_page`  (lines 203–286)

```
def object_page(rows: tuple[ObjectRow, ...], query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Applies the common listing behavior for object rows: search, exact filters, ordering, and pagination. Kinds can provide simple rows, and this function gives them all the same user-facing list rules.

**Data flow**: It receives lightweight rows and a query → checks that fields are declared and do not collide with reserved fields → filters by search text and exact field matches → sorts the result → trims it to one page and may create a next cursor → returns an ObjectPage.

**Call relations**: MemberOwnedObjects.list and MemberReadableObjects.member_page call this after they have applied visibility rules. It uses _sortable to compare field values and _ObjectCursor to resume a list from a previous page.

*Call graph*: calls 1 internal fn (_sortable); called by 2 (list, member_page); 2 external calls (__init__, __init__).


##### `object_page.value`  (lines 231–236)

```
def value(row: ObjectRow, name: str) -> JsonValue
```

**Purpose**: Looks up the value of one named listing field on one row. It hides the difference between built-in fields and kind-specific fields.

**Data flow**: It receives a row and a field name → returns the row name, summary, or a value from the row's extra fields → gives object_page a single way to read fields.

**Call relations**: This helper lives inside object_page and is used during searching, filtering, sorting, and cursor creation.


##### `_sortable`  (lines 289–302)

```
def _sortable(value: JsonValue, field_name: str) -> tuple[_SortRank, str | int | float]
```

**Purpose**: Turns a field value into a safe sort key. It makes mixed values order consistently instead of letting Python compare incompatible types.

**Data flow**: It receives a JSON-like value and its field name → classifies null, booleans, numbers, and strings into ranked buckets → returns a rank plus comparable value, or raises an error for lists or objects.

**Call relations**: object_page calls this when sorting rows and when deciding where a cursor boundary starts.

*Call graph*: called by 1 (object_page).


##### `ObjectStore.list`  (lines 317–317)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Defines the required list operation for any object kind's storage layer. A kind implements this to return one page of lightweight rows.

**Data flow**: It receives a tool context and list query → the implementing store reads its own data and applies or delegates listing rules → returns an ObjectPage.

**Call relations**: ObjectVerbs._list calls this through the registered kind. This file only declares the contract; each object kind supplies the real behavior.


##### `ObjectStore.get`  (lines 319–319)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SpecT] | None
```

**Purpose**: Defines how a kind reads one object by name. It returns the full object detail if the row exists and is visible to that store's rules.

**Data flow**: It receives a tool context and object name → the implementing store looks up the row → returns ObjectDetail or None.

**Call relations**: ObjectVerbs._get, ObjectVerbs._apply, ObjectVerbs._delete, and ObjectVerbs.action_target rely on this contract before reading, changing, deleting, or targeting an object.


##### `ObjectStore.status`  (lines 321–327)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Defines how a kind reports live state beside the stored spec. Status is separate because it may change without changing the authored object.

**Data flow**: It receives context, name, and an expected generation → the store checks or uses that generation if needed, reads live state, and returns a JSON-like dictionary or None.

**Call relations**: ObjectVerbs._get calls this after get so the response can include both stored spec and current state. ObjectVerbs.action_target also calls it to confirm the target is still valid before an action runs.


##### `ObjectStore.apply`  (lines 329–337)

```
async def apply(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Defines how a kind creates or updates an object after core validation has passed. The kind decides whether the mutation is allowed and writes to its own tables.

**Data flow**: It receives context, name, validated spec, previous spec if any, and an expected generation → the store performs the create or update or raises a clear refusal → returns nothing on success.

**Call relations**: ObjectVerbs._apply calls this after parsing YAML, validating the spec, checking create-only rules, and writing a journal record.


##### `ObjectStore.delete`  (lines 339–345)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Defines how a kind deletes one named object. The kind owns the real deletion because its data lives in its own storage.

**Data flow**: It receives context, name, and an expected generation → the store removes the row or refuses → returns nothing on success.

**Call relations**: ObjectVerbs._delete calls this after confirming the object exists and journaling the planned deletion.


##### `owner_emails`  (lines 364–379)

```
async def owner_emails(owners: Iterable[UUID | None]) -> dict[UUID | None, str]
```

**Purpose**: Looks up email addresses for object owner member IDs in one database query. Listings can use this to show who owns each row without doing one query per row.

**Data flow**: It receives owner IDs, ignoring None values → opens a workspace database transaction and selects matching member emails → returns a dictionary from member ID to email.

**Call relations**: Object kinds can call this while building their listing rows. It uses workspace_tx for database access and SQLAlchemy to build the query.

*Call graph*: 2 external calls (select, workspace_tx).


##### `MemberOwnedObjects.list`  (lines 424–432)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists member-owned objects while enforcing who is allowed to see them. It centralizes the visibility rule so each kind does not have to rewrite it.

**Data flow**: It reads the acting member and whether the speaker is an admin → asks the subclass for owned rows → keeps only visible and listable rows → converts them to ObjectRow values → passes them to object_page and returns the page.

**Call relations**: This is the store-side list implementation used by member-owned kinds. It calls subclass-provided _owned_rows, then uses _visible, _listed, and object_page.

*Call graph*: calls 5 internal fn (_listed, _owned_rows, _visible, object_page, speaker_is_admin); 2 external calls (__init__, authority_member_id).


##### `MemberOwnedObjects.get`  (lines 434–445)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SpecT] | None
```

**Purpose**: Reads one member-owned object only if the caller is allowed to see it. Invisible rows look like missing rows, which avoids leaking private names.

**Data flow**: It receives context and name → finds the owner → checks visibility → asks the subclass for full detail → attaches a generation if the owner carries one → returns the detail or None.

**Call relations**: ObjectVerbs._get reaches this through a kind's store. It relies on _owner, _visible, and _detail to separate shared gatekeeping from kind-specific storage.

*Call graph*: calls 4 internal fn (_detail, _owner, _visible, speaker_is_admin); 2 external calls (replace, authority_member_id).


##### `MemberOwnedObjects.status`  (lines 447–468)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reads live status for a member-owned object with visibility and generation checks. It avoids showing status for a row that changed or became invisible during the read.

**Data flow**: It receives context, name, and expected generation → checks the current owner and generation → verifies visibility → reads status → re-checks owner, generation, and visibility → returns status, None, or raises a not-found-style error.

**Call relations**: ObjectVerbs._get calls store.status after reading detail. This method calls _owner, _require_current_generation, _visible, and subclass _status.

*Call graph*: calls 5 internal fn (_owner, _require_current_generation, _status, _visible, speaker_is_admin); 2 external calls (__init__, authority_member_id).


##### `MemberOwnedObjects.apply`  (lines 470–496)

```
async def apply(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Creates or updates a member-owned object while enforcing ownership, admin rules, live-speaker requirements, and generation fencing. It is the main safety gate before a kind-specific write.

**Data flow**: It receives context, name, new spec, old spec, and expected generation → checks whether the row exists and who owns it → refuses invisible or unauthorized edits → optionally requires a live speaker → calls the subclass write method on success.

**Call relations**: ObjectVerbs._apply calls this through the store after validation and journaling. It hands the final write to _apply_owned only after shared rules pass.

*Call graph*: calls 8 internal fn (_admin_can_apply, _apply_owned, _owned, _owner, _require_current_generation, _visible, require_speaker, speaker_is_admin); 3 external calls (__init__, __init__, authority_member_id).


##### `MemberOwnedObjects.delete`  (lines 498–516)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Deletes a member-owned object only when the caller may delete it. It treats invisible rows as missing and can require a live speaker for sensitive revocations.

**Data flow**: It receives context, name, and expected generation → finds the owner → checks generation, existence, visibility, speaker requirement, and ownership or admin status → calls the subclass delete method.

**Call relations**: ObjectVerbs._delete calls this through the store. It uses _owner, _visible, _owned, _require_current_generation, and _delete_owned.

*Call graph*: calls 7 internal fn (_delete_owned, _owned, _owner, _require_current_generation, _visible, require_speaker, speaker_is_admin); 3 external calls (__init__, __init__, authority_member_id).


##### `MemberOwnedObjects._owned`  (lines 518–522)

```
def _owned(self, owner: OwnerT, acting: UUID | None) -> bool
```

**Purpose**: Answers whether the acting member is the actual owner of a row. Admin-only rows with no member owner are deliberately not owned by anyone.

**Data flow**: It receives an owner record and acting member ID → compares the member IDs only if the row has a real owner → returns true or false.

**Call relations**: _visible uses this for read access, and apply and delete use it to decide whether a non-admin can change a row.

*Call graph*: called by 3 (_visible, apply, delete).


##### `MemberOwnedObjects._visible`  (lines 524–525)

```
def _visible(self, owner: OwnerT, acting: UUID | None, is_admin: bool) -> bool
```

**Purpose**: Answers whether a row can be seen by this caller. A row is visible if it is shared, owned by the acting member, or the caller is an admin.

**Data flow**: It receives owner information, acting member ID, and admin status → combines the shared flag, ownership check, and admin flag → returns true or false.

**Call relations**: The list, get, status, apply, and delete paths all call this before exposing or changing member-owned rows.

*Call graph*: calls 1 internal fn (_owned); called by 5 (apply, delete, get, list, status).


##### `MemberOwnedObjects._listed`  (lines 527–535)

```
def _listed(self, row: OwnedRow[OwnerT], query: ObjectListQuery) -> bool
```

**Purpose**: Lets a kind hide some otherwise visible rows from browsing while still allowing direct access by name. The default is simple: every visible row is listed.

**Data flow**: It receives a row and the list query → returns true by default → subclasses may override it to apply kind-specific listing limits.

**Call relations**: MemberOwnedObjects.list calls this after visibility checks. MemberReadableObjects.member_page also benefits from the same rule through inheritance.

*Call graph*: called by 1 (list).


##### `MemberOwnedObjects._admin_can_apply`  (lines 537–538)

```
def _admin_can_apply(self, old: SpecT, spec: SpecT) -> bool
```

**Purpose**: Lets a subclass allow a special admin edit that would normally be blocked. By default, admins cannot edit another member's owned row unless ordinary admin rules already allow it.

**Data flow**: It receives the old and new specs → returns false by default → subclasses can override with a narrow comparison.

**Call relations**: MemberOwnedObjects.apply calls this when an admin tries to update a row owned by a member.

*Call graph*: called by 1 (apply).


##### `MemberOwnedObjects._require_current_generation`  (lines 540–557)

```
def _require_current_generation(self, name: str, owner: OwnerT | None, expected_generation: UUID | None, action: str) -> None
```

**Purpose**: Refuses an operation if the object changed since it was read. This is the file's protection against overwriting the wrong generated row.

**Data flow**: It receives the object name, current owner, expected generation, and action word → compares expected and current generation when present → raises an error if stale, otherwise returns nothing.

**Call relations**: MemberOwnedObjects.status, apply, and delete call this before sensitive reads or writes.

*Call graph*: called by 3 (apply, delete, status).


##### `MemberOwnedObjects._owner`  (lines 559–560)

```
async def _owner(self, ctx: ToolContext, name: str) -> OwnerT | None
```

**Purpose**: Finds the owner record for one named object. This lets the shared gate make access decisions before reading full detail.

**Data flow**: It receives context and name → asks _owned_rows for all rows → searches for a matching name → returns the owner or None.

**Call relations**: get, status, apply, and delete call this. It depends on the subclass implementation of _owned_rows.

*Call graph*: calls 1 internal fn (_owned_rows); called by 4 (apply, delete, get, status).


##### `MemberOwnedObjects._owned_rows`  (lines 562–563)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[OwnerT], ...]
```

**Purpose**: Abstract hook where a concrete member-owned kind supplies its rows and owners. The base class cannot know where each kind stores its data.

**Data flow**: It receives context → a subclass should read its storage and return OwnedRow values → the base class uses those rows for visibility and listing.

**Call relations**: MemberOwnedObjects.list and _owner call this. In this base class it raises NotImplementedError until a subclass provides it.

*Call graph*: called by 2 (_owner, list).


##### `MemberOwnedObjects._detail`  (lines 565–568)

```
async def _detail(self, ctx: ToolContext, name: str, owner: OwnerT) -> ObjectDetail[SpecT] | None
```

**Purpose**: Abstract hook where a concrete kind reads the full detail for a visible object. The base class handles access, while the subclass handles storage.

**Data flow**: It receives context, name, and owner → a subclass should return ObjectDetail or None → the base get method returns that to the caller.

**Call relations**: MemberOwnedObjects.get calls this only after owner and visibility checks have passed.

*Call graph*: called by 1 (get).


##### `MemberOwnedObjects._status`  (lines 570–573)

```
async def _status(self, ctx: ToolContext, name: str, owner: OwnerT) -> dict[str, JsonValue] | None
```

**Purpose**: Abstract hook where a concrete kind reads live status for an object. Status is kind-specific, so the base class only surrounds it with safety checks.

**Data flow**: It receives context, name, and owner → a subclass returns a JSON-like status dictionary or None → the base status method re-checks safety before returning it.

**Call relations**: MemberOwnedObjects.status calls this between generation and visibility checks.

*Call graph*: called by 1 (status).


##### `MemberOwnedObjects._apply_owned`  (lines 575–583)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, owner: OwnerT | None) -> None
```

**Purpose**: Abstract hook where a concrete kind performs the actual create or update. The base class has already checked common ownership rules before calling it.

**Data flow**: It receives context, name, new spec, old spec, and current owner if any → a subclass writes the change to its own storage → returns nothing or raises a domain error.

**Call relations**: MemberOwnedObjects.apply calls this after validation, visibility, speaker, admin, and generation gates.

*Call graph*: called by 1 (apply).


##### `MemberOwnedObjects._delete_owned`  (lines 585–586)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: OwnerT) -> None
```

**Purpose**: Abstract hook where a concrete kind performs the actual deletion. The base class decides whether deletion is allowed first.

**Data flow**: It receives context, name, and owner → a subclass removes the row from its own storage → returns nothing or raises an error.

**Call relations**: MemberOwnedObjects.delete calls this after all common delete checks pass.

*Call graph*: called by 1 (delete).


##### `MemberReadable.member_detail`  (lines 608–615)

```
async def member_detail(self, ext: 'ExtensionContext | None', name: str, *, member_id: UUID, admin: bool) -> MemberObject | None
```

**Purpose**: Defines the portal-facing contract for reading one object as a signed-in member outside a chat turn. A kind implements this only if it can safely answer portal reads.

**Data flow**: It receives an extension context, object name, member ID, and admin flag → the implementation applies its visibility rule and reads detail → returns a MemberObject or None.

**Call relations**: Portal routes can check for this protocol before offering object detail pages. This file declares the shape, while concrete kinds implement it.


##### `MemberListable.member_page`  (lines 623–630)

```
async def member_page(self, ext: 'ExtensionContext | None', *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Defines the portal-facing contract for listing objects for one signed-in member. It is used by kinds that can show a searchable index outside a turn.

**Data flow**: It receives extension context, member ID, admin flag, and query → the implementation returns an ObjectPage of visible rows.

**Call relations**: This extends MemberReadable. MemberReadableObjects provides a reusable implementation for member-owned kinds.


##### `ConversationMemberListable.member_conversation_rows`  (lines 642–650)

```
async def member_conversation_rows(self, ext: 'ExtensionContext | None', conversation_id: UUID, *, member_id: UUID, admin: bool, limit: int) -> tuple[ConversationObjectGrant, ...]
```

**Purpose**: Defines a contract for listing object grants connected to a conversation for one member. It supports views that need to show which conversation-related objects a member may see.

**Data flow**: It receives extension context, conversation ID, member ID, admin flag, and a limit → the implementation returns grant records with object name, generation, and whether content is visible.

**Call relations**: Concrete object kinds can implement this protocol when conversation pages need their rows. The file only defines the interface.


##### `MemberReadableObjects.member_page`  (lines 663–676)

```
async def member_page(self, ext: 'ExtensionContext | None', *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists portal-visible member-owned objects for a signed-in member. It reuses the same visibility and paging rules as turn-time object listing.

**Data flow**: It receives extension context, member ID, admin flag, and query → asks _member_rows for rows → filters by visibility and _listed → converts rows to ObjectRow → returns object_page's result.

**Call relations**: This implements MemberListable for subclasses. It calls _member_rows and object_page so portal listing and tool listing stay consistent.

*Call graph*: calls 2 internal fn (_member_rows, object_page); 1 external calls (__init__).


##### `MemberReadableObjects.member_detail`  (lines 678–696)

```
async def member_detail(self, ext: 'ExtensionContext | None', name: str, *, member_id: UUID, admin: bool) -> MemberObject[SpecT] | None
```

**Purpose**: Reads one portal-visible member-owned object and returns both its listing row and full detail. This gives a detail page enough data without bypassing the visibility gate.

**Data flow**: It receives extension context, name, member ID, and admin flag → finds the row in _member_rows → checks visibility → asks _member_object for full detail → returns a MemberObject or None.

**Call relations**: Portal detail routes use this through the MemberReadable protocol. It calls subclass hooks _member_rows and _member_object.

*Call graph*: calls 2 internal fn (_member_object, _member_rows); 2 external calls (__init__, __init__).


##### `MemberReadableObjects._owned_rows`  (lines 698–699)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[OwnerT], ...]
```

**Purpose**: Adapts portal-style row loading to the turn-time MemberOwnedObjects interface. This keeps both paths using the same row source.

**Data flow**: It receives a ToolContext → extracts the acting member from the authority → calls _member_rows with the extension context and member ID → returns owned rows.

**Call relations**: MemberOwnedObjects.list and _owner call this through inheritance when a MemberReadableObjects subclass is used as a store.

*Call graph*: calls 1 internal fn (_member_rows); 1 external calls (authority_member_id).


##### `MemberReadableObjects._detail`  (lines 701–706)

```
async def _detail(self, ctx: ToolContext, name: str, owner: OwnerT) -> ObjectDetail[SpecT] | None
```

**Purpose**: Adapts portal-style object loading to the turn-time MemberOwnedObjects detail hook. It prevents the portal and tool paths from drifting apart.

**Data flow**: It receives a ToolContext, name, and owner → extracts the acting member → calls _member_object with the extension context, owner, and member ID → returns ObjectDetail or None.

**Call relations**: MemberOwnedObjects.get calls this through inheritance after visibility checks.

*Call graph*: calls 1 internal fn (_member_object); 1 external calls (authority_member_id).


##### `MemberReadableObjects._member_rows`  (lines 708–711)

```
async def _member_rows(self, ext: 'ExtensionContext | None', *, member_id: UUID | None) -> tuple[OwnedRow[OwnerT], ...]
```

**Purpose**: Abstract hook where a concrete readable kind supplies rows for one member. It is the shared row source for both portal and turn-time listing.

**Data flow**: It receives an extension context and member ID → a subclass reads storage and returns OwnedRow values → callers apply visibility and paging.

**Call relations**: member_page, member_detail, and _owned_rows call this. Subclasses must implement it.

*Call graph*: called by 3 (_owned_rows, member_detail, member_page).


##### `MemberReadableObjects._member_object`  (lines 713–721)

```
async def _member_object(self, ext: 'ExtensionContext | None', name: str, owner: OwnerT, *, member_id: UUID | None) -> ObjectDetail[SpecT] | None
```

**Purpose**: Abstract hook where a concrete readable kind supplies full detail for one member and one owned row. It is the shared detail source for portal and turn-time reads.

**Data flow**: It receives extension context, name, owner, and member ID → a subclass reads storage and returns ObjectDetail or None.

**Call relations**: member_detail and _detail call this. Subclasses must implement it.

*Call graph*: called by 2 (_detail, member_detail).


##### `action_registry`  (lines 774–819)

```
def action_registry(bound: tuple[BoundAction, ...], kinds: Mapping[str, BoundKind]) -> dict[str, dict[str, BoundAction]]
```

**Purpose**: Validates and indexes all object actions registered by core and extensions. It is a startup safety gate that prevents ambiguous, unsafe, or malformed action declarations.

**Data flow**: It receives bound actions and the registered kinds → checks bindings, names, target kind existence, collisions, reserved input fields, general tool declaration rules, and JSON-safe input models → returns a dictionary grouped by kind and action name.

**Call relations**: Startup assembly calls this after manifests are collected. It delegates model safety checks to _validate_spec_model and tool checks to validate_tool_declaration.

*Call graph*: calls 1 internal fn (_validate_spec_model); 2 external calls (fullmatch, validate_tool_declaration).


##### `object_registry`  (lines 822–845)

```
def object_registry(bound: tuple[BoundKind, ...]) -> dict[str, BoundKind]
```

**Purpose**: Validates and indexes all object kinds for one deployment. It makes sure every kind name is unique and every spec is safe to store and show.

**Data flow**: It receives bound kinds → checks name grammar, duplicate names, allowed agent-target verbs, and spec model safety → returns a dictionary from kind name to BoundKind.

**Call relations**: This is a boot-time gate before ObjectVerbs serves requests. It calls _validate_spec_model for each kind's spec model.

*Call graph*: calls 1 internal fn (_validate_spec_model); 1 external calls (fullmatch).


##### `_validate_spec_model`  (lines 848–867)

```
def _validate_spec_model(label: str, spec_model: type[BaseModel]) -> None
```

**Purpose**: Checks that a Pydantic spec or action input model is safe for object use. Specs are echoed back to users, so the model must reject unknown keys, avoid secrets, and be representable as JSON.

**Data flow**: It receives a label and model class → walks all nested models → checks extra fields are forbidden and secret types are absent → asks Pydantic for a JSON schema → raises an explanatory error if any check fails.

**Call relations**: object_registry uses this for kind specs, and action_registry uses it for action input models. It relies on _reachable_models and _annotation_types to inspect nested types.

*Call graph*: calls 2 internal fn (_annotation_types, _reachable_models); called by 2 (action_registry, object_registry).


##### `_reachable_models`  (lines 870–884)

```
def _reachable_models(model: type[BaseModel]) -> tuple[type[BaseModel], ...]
```

**Purpose**: Finds all Pydantic models nested inside another model. This lets validation rules cover embedded specs, not just the top-level object.

**Data flow**: It receives a model class → walks its field annotations, following nested BaseModel types while avoiding repeats → returns all discovered model classes.

**Call relations**: _validate_spec_model calls this before checking model configuration and secret fields. It uses _annotation_types to unpack complex type annotations.

*Call graph*: calls 1 internal fn (_annotation_types); called by 1 (_validate_spec_model).


##### `_annotation_types`  (lines 887–894)

```
def _annotation_types(annotation: object) -> tuple[object, ...]
```

**Purpose**: Flattens a type annotation into the concrete pieces inside it. For example, it can look through containers or unions to find nested model or secret types.

**Data flow**: It receives an annotation → asks Python typing for its arguments → recursively expands nested arguments → returns a tuple of contained annotation pieces.

**Call relations**: _reachable_models and _validate_spec_model call this while inspecting model fields.

*Call graph*: called by 2 (_reachable_models, _validate_spec_model); 1 external calls (get_args).


##### `ObjectGetInput.validate_ref`  (lines 920–923)

```
def validate_ref(cls, value: str) -> str
```

**Purpose**: Validates an object reference supplied to object_get. Empty is allowed for the special case that means “this turn's agent.”

**Data flow**: It receives the ref string → if non-empty, parses it as an ObjectRef to prove it has the expected kind/name shape → returns the original string.

**Call relations**: Pydantic runs this while validating ObjectGetInput before ObjectVerbs._get handles the request.

*Call graph*: calls 1 internal fn (parse).


##### `ObjectVerbs.tools`  (lines 1000–1086)

```
def tools(self) -> tuple[ToolDef, ...]
```

**Purpose**: Builds the tool definitions that expose object operations to the rest of the runtime. This is where list, get, explain, apply, delete, and action entry points are described.

**Data flow**: It reads the ObjectVerbs instance → creates ToolDef objects with names, descriptions, input models, handlers, and side-effect flags → returns them as a tuple.

**Call relations**: Tool registration code calls this to publish object tools. The handlers it wires are the private methods on ObjectVerbs.

*Call graph*: 1 external calls (__init__).


##### `ObjectVerbs._list`  (lines 1088–1155)

```
async def _list(self, ctx: ToolContext, args: ObjectListInput) -> ToolResult
```

**Purpose**: Implements object_list. It either returns the catalog of registered kinds or lists rows for one kind.

**Data flow**: It receives context and list input → if no kind is named, rejects row-specific filters and returns kind descriptions → if a kind is named, resolves it, checks any agent target, calls the kind's store list, adds refs, actions, agent, and cursor → returns JSON text.

**Call relations**: The object_list ToolDef calls this. It uses _resolve, _target, _bound_ctx, _action_views, _instance_action_discoveries, and _json_result.

*Call graph*: calls 7 internal fn (_action_views, _bound_ctx, _granted_actions, _instance_action_discoveries, _resolve, _target, _json_result); 3 external calls (__init__, __init__, object_agent).


##### `ObjectVerbs._get`  (lines 1157–1221)

```
async def _get(self, ctx: ToolContext, args: ObjectGetInput) -> ToolResult
```

**Purpose**: Implements object_get. It reads one object's spec, status, links, actions, timestamps, and optional generation.

**Data flow**: It receives context and get input → parses or derives the object reference → resolves the kind and agent target → calls store.get and store.status → formats links and actions → returns YAML text with the object detail.

**Call relations**: The object_get ToolDef calls this. It uses _resolve, _target, _bound_ctx, _action_views, database access for the empty-ref agent case, and the kind store.

*Call graph*: calls 5 internal fn (parse, _action_views, _bound_ctx, _resolve, _target); 8 external calls (__init__, __init__, __init__, __init__, select, workspace_tx, object_agent, safe_dump).


##### `ObjectVerbs._explain`  (lines 1223–1238)

```
async def _explain(self, ctx: ToolContext, args: ObjectExplainInput) -> ToolResult
```

**Purpose**: Implements object_explain. It tells a caller how to author or use objects of one kind.

**Data flow**: It receives context and kind input → resolves the kind → collects description, guidance, name rule, spec JSON schema, agent-target verbs, and action views → returns JSON text.

**Call relations**: The object_explain ToolDef calls this. It uses _resolve, _action_views, and _json_result.

*Call graph*: calls 3 internal fn (_action_views, _resolve, _json_result).


##### `ObjectVerbs._apply`  (lines 1240–1305)

```
async def _apply(self, ctx: ToolContext, args: ObjectApplyInput) -> ToolResult
```

**Purpose**: Implements object_apply, which creates or updates an object from a YAML manifest. It validates before writing and journals the intended change before the store mutates anything.

**Data flow**: It receives context and apply input → parses the manifest → resolves kind and agent target → validates the name and spec → reads any existing object → enforces create-only and cross-agent rules → writes a change journal → calls store.apply → withdraws the journal if the store refuses → returns created or updated JSON.

**Call relations**: The object_apply ToolDef calls this. It uses _parse_envelope, _resolve, _target, _bound_ctx, _journal_object_change, _withdraw_object_change, and _json_result.

*Call graph*: calls 7 internal fn (_bound_ctx, _resolve, _target, _journal_object_change, _json_result, _parse_envelope, _withdraw_object_change); 4 external calls (__init__, __init__, validate_object_name, object_agent).


##### `ObjectVerbs._delete`  (lines 1307–1342)

```
async def _delete(self, ctx: ToolContext, args: ObjectDeleteInput) -> ToolResult
```

**Purpose**: Implements object_delete. It deletes one object and returns the deleted spec when it is visible, so accidental deletes can often be recreated.

**Data flow**: It receives context and delete input → resolves kind and agent target → reads the old object → journals the delete → calls store.delete with the old generation → removes the journal if deletion fails → returns JSON with deletion details and old spec if allowed.

**Call relations**: The object_delete ToolDef calls this. It uses _resolve, _target, _bound_ctx, _journal_object_change, _withdraw_object_change, and _json_result.

*Call graph*: calls 6 internal fn (_bound_ctx, _resolve, _target, _journal_object_change, _json_result, _withdraw_object_change); 2 external calls (__init__, object_agent).


##### `ObjectVerbs._object_action`  (lines 1344–1345)

```
async def _object_action(self, ctx: ToolContext, args: ObjectActionInput) -> ToolResult
```

**Purpose**: Marks that object_action is exposed as a tool schema but is not dispatched through this method. The engine routes action calls to the already-bound action handler instead.

**Data flow**: It receives context and object action input → immediately raises a runtime error if reached → produces no normal result.

**Call relations**: ObjectVerbs.tools wires this for schema exposure. Correct action dispatch goes through the engine and uses ObjectVerbs.action_target beforehand.


##### `ObjectVerbs._granted_actions`  (lines 1347–1355)

```
def _granted_actions(self, ctx: ToolContext, kind: str) -> dict[str, BoundAction]
```

**Purpose**: Filters a kind's registered actions down to the ones the current turn is allowed to see. This keeps action discovery tied to granted capabilities.

**Data flow**: It receives context and kind name → looks up actions for that kind → keeps only actions whose canonical ID is present in ctx.granted_actions → returns a dictionary of allowed actions.

**Call relations**: _list, _action_views, and _instance_action_discoveries call this before showing actions to a caller.

*Call graph*: called by 3 (_action_views, _instance_action_discoveries, _list).


##### `ObjectVerbs._action_views`  (lines 1357–1387)

```
def _action_views(self, ctx: ToolContext, kind: str, binding: str, *, name: str | None=None, agent: str | None=None, generation: UUID | None=None) -> list[JsonValue]
```

**Purpose**: Builds ready-to-call action templates for collection or instance actions on a kind. These templates include the target fields established by the surrounding read.

**Data flow**: It receives context, kind, binding type, and optional name, agent, and generation → filters granted actions by binding, fixed target name, and agent-target support → converts each to a JSON-ready action view → returns a list.

**Call relations**: _list, _get, and _explain call this when they need to show available actions beside kinds or objects. It uses _granted_actions and action_view.

*Call graph*: calls 1 internal fn (_granted_actions); called by 3 (_explain, _get, _list); 1 external calls (action_view).


##### `ObjectVerbs._instance_action_discoveries`  (lines 1389–1411)

```
def _instance_action_discoveries(self, ctx: ToolContext, kind: str, *, agent: str | None) -> list[JsonValue]
```

**Purpose**: Builds lightweight descriptions of instance actions for a list response. This lets a caller know which actions may appear on individual objects without fetching every object first.

**Data flow**: It receives context, kind, and optional agent name → filters granted instance actions and agent-targetable actions → packages each action's name, description, input schema, and fixed target name if any → returns a list.

**Call relations**: ObjectVerbs._list calls this after listing rows for a kind. It uses _granted_actions and InstanceActionDiscovery.

*Call graph*: calls 1 internal fn (_granted_actions); called by 1 (_list); 1 external calls (__init__).


##### `ObjectVerbs.action_target`  (lines 1413–1457)

```
async def action_target(self, ctx: ToolContext, action: ToolDef, wire: ObjectActionInput) -> ObjectActionTarget
```

**Purpose**: Resolves and verifies the target of an object action before the action handler runs. For instance actions, it asks the owning kind whether the object exists and is visible.

**Data flow**: It receives context, the bound action definition, and wire input → checks that the action is object-bound → handles optional agent targeting → for collection actions returns a target with no name → for instance actions verifies fixed name rules, reads the object, checks status, and returns target details including live and supplied generations.

**Call relations**: The engine calls this before dispatching object_action. It uses _agent_gate, _resolve, _bound_ctx, and the kind store under the correct object agent scope.

*Call graph*: calls 3 internal fn (_agent_gate, _bound_ctx, _resolve); 3 external calls (__init__, __init__, object_agent).


##### `ObjectVerbs._resolve`  (lines 1459–1464)

```
def _resolve(self, kind: str) -> BoundKind
```

**Purpose**: Finds a registered object kind by name or raises a helpful error. It is the shared lookup step for every kind-specific operation.

**Data flow**: It receives a kind string → looks it up in the registry → returns the BoundKind if present, otherwise raises UnknownKind with the known kinds.

**Call relations**: _list, _get, _explain, _apply, _delete, and action_target call this before touching a kind's store.

*Call graph*: called by 6 (_apply, _delete, _explain, _get, _list, action_target); 1 external calls (__init__).


##### `ObjectVerbs._bound_ctx`  (lines 1466–1467)

```
def _bound_ctx(self, ctx: ToolContext, bound: BoundKind) -> ToolContext
```

**Purpose**: Rebinds a tool context to the extension context that owns a kind. This makes sure a kind's store runs with its own extension capabilities.

**Data flow**: It receives the current ToolContext and BoundKind → copies the context while replacing its extension field → returns the adjusted ToolContext.

**Call relations**: _list, _get, _apply, _delete, and action_target call this before invoking a store owned by an extension.

*Call graph*: called by 5 (_apply, _delete, _get, _list, action_target); 1 external calls (replace).


##### `ObjectVerbs._target`  (lines 1469–1482)

```
async def _target(self, ctx: ToolContext, bound: BoundKind, name: str, verbs: frozenset[AgentTargetVerb]) -> ObjectAgent | None
```

**Purpose**: Checks whether a request may target another agent for a given object verb. If no agent is named, it leaves the request in the current agent's namespace.

**Data flow**: It receives context, bound kind, target agent name, and allowed verbs → returns None when no target is named → rejects unsupported agent targeting → otherwise calls _agent_gate and returns the target agent.

**Call relations**: _list, _get, _apply, and _delete call this before entering an object_agent scope.

*Call graph*: calls 1 internal fn (_agent_gate); called by 4 (_apply, _delete, _get, _list).


##### `ObjectVerbs._agent_gate`  (lines 1484–1533)

```
async def _agent_gate(self, ctx: ToolContext, name: str) -> ObjectAgent | None
```

**Purpose**: Enforces the rules for one agent reading or writing another agent's objects. Only the workspace main agent, in a live member-requested call, can target another visible agent.

**Data flow**: It receives context and target agent name → reads the current agent from the database → returns None if the name is the current agent → checks main-agent, subagent, speaker, admin, and visibility rules → returns an ObjectAgent for the target or raises an error.

**Call relations**: _target and action_target call this whenever a request names an agent. It uses workspace_tx, SQLAlchemy queries, and member_is_admin.

*Call graph*: called by 2 (_target, action_target); 6 external calls (__init__, __init__, or_, select, workspace_tx, member_is_admin).


##### `_journal_object_change`  (lines 1536–1590)

```
async def _journal_object_change(ctx: ToolContext, kind: str, name: str, verb: Literal['create', 'update', 'delete'], before: BaseModel | None, after: BaseModel | None, agent_id: UUID) -> UUID | None
```

**Purpose**: Writes an audit record for a create, update, or delete before the actual mutation runs. This makes failures and retries safer because a reported write has a consistent record.

**Data flow**: It receives context, object identity, verb, before and after specs, and agent ID → derives a change ID, using the idempotency key when available → checks whether the record already exists → inserts the before and after specs as JSON if new → returns the inserted ID or None if it was already present.

**Call relations**: ObjectVerbs._apply and ObjectVerbs._delete call this before invoking the store. If the store then fails, they call _withdraw_object_change only for a newly inserted journal row.

*Call graph*: called by 2 (_apply, _delete); 7 external calls (dumps, model_dump, insert, select, workspace_tx, uuid4, uuid5).


##### `_withdraw_object_change`  (lines 1593–1600)

```
async def _withdraw_object_change(ctx: ToolContext, change_id: UUID) -> None
```

**Purpose**: Removes a journal record when the matching object mutation did not happen. It keeps the audit table from claiming a failed fresh attempt succeeded.

**Data flow**: It receives context and change ID → opens a workspace transaction → deletes that object_change row for the current workspace → returns nothing.

**Call relations**: ObjectVerbs._apply and ObjectVerbs._delete call this inside exception handling after a store refusal or failure.

*Call graph*: called by 2 (_apply, _delete); 2 external calls (delete, workspace_tx).


##### `_parse_envelope`  (lines 1603–1629)

```
def _parse_envelope(manifest: str) -> tuple[str, str, Mapping[str, object], UUID | None]
```

**Purpose**: Parses and validates the YAML manifest used by object_apply. It makes sure the request has exactly the expected wrapper before spec validation begins.

**Data flow**: It receives manifest text → checks byte size → YAML-parses it → requires a mapping with kind, name, spec, and optional generation → checks kind and name are strings and spec is a mapping → parses generation as a UUID if present → returns kind, name, spec mapping, and generation.

**Call relations**: ObjectVerbs._apply calls this as its first real validation step before resolving the kind or validating the spec model.

*Call graph*: called by 1 (_apply); 3 external calls (__init__, UUID, safe_load).


##### `_json_result`  (lines 1632–1633)

```
def _json_result(payload: Mapping[str, object]) -> ToolResult
```

**Purpose**: Wraps a JSON-serializable payload as a ToolResult. It is a small helper for object verbs that return compact JSON instead of YAML detail.

**Data flow**: It receives a mapping → serializes it with json.dumps → places the text in TextContent → returns a ToolResult.

**Call relations**: ObjectVerbs._list, _explain, _apply, and _delete call this to produce their tool responses.

*Call graph*: called by 4 (_apply, _delete, _explain, _list); 3 external calls (__init__, __init__, dumps).


### `core/src/ufo/host/kinds/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python projects, an `__init__.py` file tells Python that a folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may contain useful tools, but this label simply makes the drawer easy to find and refer to by name. Here, the drawer is `ufo.host.kinds`, which likely groups together different kinds of host-related implementations elsewhere in the same directory. Nothing runs from this file, and no functions, classes, or settings are defined here. Its main value is structural: without it, depending on the Python version and packaging setup, imports that expect `ufo.host.kinds` to be a normal package might fail or behave differently.


### `core/src/ufo/runtime/kinds/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder often needs an `__init__.py` file to be treated as a package, which means code elsewhere can import modules from it using normal dotted names. Here, the folder is `ufo.runtime.kinds`, which likely groups code related to different runtime “kinds” or categories used by the UFO runtime. Nothing is executed, configured, or exposed directly in this file. Its value is structural: without it, some import paths or packaging tools might not recognize this folder as part of the Python module tree, depending on how the project is run and packaged. Think of it like a label on a drawer. The drawer may contain useful tools, but this label mainly tells the rest of the system where that drawer belongs.


### `core/src/ufo/runtime/kinds/governance.py`

`domain_logic` · `proposal creation and approval`

This file is a safety gate for changing an agent’s prompt. A prompt is the instruction text that shapes how an agent behaves, so changing it can be important and risky. Instead of letting code overwrite the prompt directly, this file creates proposals that can later be approved.

The key idea is like checking a document’s fingerprint before replacing it. When a change is proposed, the file stores a digest, which is a short cryptographic fingerprint of the current prompt, along with the proposed new prompt. Later, when someone approves the proposal, the file checks the agent’s current prompt again. If its fingerprint still matches the one saved in the proposal, the change is applied. If not, the proposal is rejected, because something else changed the prompt in the meantime.

This is a compare-and-swap pattern: “only change this value if it is still the value I saw before.” That prevents stale approvals from accidentally overwriting newer work. The file also records proposal status, such as pending, approved, or rejected, and logs approval or rejection events so the system has an audit trail.

#### Function details

##### `prompt_digest`  (lines 16–17)

```
def prompt_digest(prompt: str) -> str
```

**Purpose**: This creates a stable fingerprint for a prompt string. The system uses that fingerprint to tell whether a prompt is still the same later, without comparing the whole text everywhere.

**Data flow**: It takes prompt text as input, turns it into bytes, and runs it through SHA-256, a standard one-way hashing method. It returns a hexadecimal string that represents the prompt’s fingerprint and does not change unless the prompt text changes.

**Call relations**: When a proposal is created, Governance.propose_change uses this to record the fingerprint of the proposed new prompt. When a proposal is approved, Governance.approve_proposal uses it to check whether the agent’s current prompt still matches the original prompt that the proposal was based on.

*Call graph*: called by 2 (approve_proposal, propose_change); 1 external calls (sha256).


##### `Governance.propose_change`  (lines 28–55)

```
async def propose_change(self, change: AgentChange) -> ProposalRef
```

**Purpose**: This opens a new proposal to change an agent’s prompt. It does not change the agent yet; it only records the requested change so it can be reviewed and approved later.

**Data flow**: It receives an AgentChange that names the agent, says what prompt fingerprint the change is based on, and includes the new prompt text. It starts a database transaction, checks that the agent belongs to this workspace, creates a new proposal ID, stores the proposal with pending status, and saves the new prompt inside the proposal body. It returns a ProposalRef containing the new proposal ID.

**Call relations**: This is the first half of the governance flow. A caller asks it to propose a prompt change, and it uses prompt_digest to store the fingerprint of the proposed new prompt. Later, Governance.approve_proposal can be called with the returned proposal ID to either apply or reject the change.

*Call graph*: calls 1 internal fn (prompt_digest); 5 external calls (__init__, insert, select, workspace_tx, uuid4).


##### `Governance.approve_proposal`  (lines 57–114)

```
async def approve_proposal(self, proposal_id: UUID) -> None
```

**Purpose**: This approves a pending prompt-change proposal, but only if the agent’s prompt has not changed since the proposal was made. If the prompt has changed, it rejects the proposal instead of overwriting newer work.

**Data flow**: It receives a proposal ID and opens a database transaction. It loads the proposal for this workspace, verifies that it exists and is still pending, then locks and reads the target agent’s current prompt. It compares the proposal’s saved original prompt fingerprint with a fresh fingerprint of the current prompt. If they differ, it marks the proposal rejected and logs that result. If they match, it writes the proposed prompt into the agent record, marks the proposal approved, and logs the approval.

**Call relations**: This is the second half of the governance flow after Governance.propose_change has created a pending proposal. It calls prompt_digest to make the safety comparison, updates the database to reflect approval or rejection, and uses the logging system to record what happened.

*Call graph*: calls 1 internal fn (prompt_digest); 4 external calls (select, update, workspace_tx, log).


### `core/src/ufo/runtime/object_scope.py`

`domain_logic` · `request handling`

Object actions can sometimes be aimed at a different agent namespace than the one currently running the code. This file is the small piece of runtime memory that makes that possible without passing the target agent through every function call by hand. Think of it like putting a temporary sticky note on the current task that says, “For this object action, use this agent.”

It defines two frozen data shapes. `ObjectAgent` describes the exact agent chosen for an object dispatch: its unique ID and name. `ObjectActionTarget` describes what the dispatch engine resolved before calling a handler: the object kind, optional instance name, optional target agent, and generation IDs used to understand which version of the object lookup was seen. These fields are not meant to come from the handler’s input; they are runtime facts discovered before the handler runs.

The file then uses a `ContextVar`, which is task-local storage: separate concurrent tasks can each have their own value without stepping on each other. `object_agent` temporarily sets the target agent for the current task and reliably restores the old value afterward. `object_agent_id` reads that target; if none was set, it falls back to the normal current agent. Without this file, audited object handlers could easily use the wrong agent identity during cross-agent object dispatch.

#### Function details

##### `object_agent`  (lines 44–52)

```
def object_agent(target: ObjectAgent | None) -> Iterator[None]
```

**Purpose**: This function creates a temporary scope where object-handling code sees a specific agent as its target. It is used when dispatch has already resolved that an object action should act through a particular agent namespace.

**Data flow**: It receives either an `ObjectAgent` or `None`. If it gets `None`, it simply runs the enclosed code without changing the current target. If it gets an agent, it stores that agent in task-local state before the enclosed code runs, then restores the previous value afterward, even if something inside fails.

**Call relations**: Higher-level dispatch code enters this scope before running object handler logic. Code deeper inside that handler can then call `object_agent_id` and get the dispatch-selected agent instead of needing the agent passed through every layer manually.


##### `object_agent_id`  (lines 55–57)

```
def object_agent_id() -> UUID
```

**Purpose**: This function answers the question, “Which agent ID should this object action use right now?” It returns the object-specific target agent if one has been set, otherwise it uses the normal current agent.

**Data flow**: It reads the task-local object target. If a target is present, it returns that target’s UUID. If no target is present, it calls `agent_current()` to read the surrounding runtime’s current agent and returns that agent’s ID.

**Call relations**: Object handler code calls this when it needs the correct agent identity for audited object work. It depends on `object_agent` when a dispatch has set a temporary object target, and it falls back to `ufo.runtime.agent_scope.agent_current` when no object-specific target is active.

*Call graph*: 1 external calls (agent_current).


### Scheduled task visibility
Scheduled task visibility rules decide who may inspect private task prompts and descriptions.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/visibility.py`

`domain_logic` · `request handling`

Scheduled tasks can post their results into different kinds of conversations. Some conversations are shared with the whole workspace, while others belong to one member. This file answers a simple but important privacy question: “Can this member read what the task is actually about?”

The rule is based mostly on where the task reports its output. If the task posts into a shared workspace place, then its content is treated as shared too, because everyone who can read the replies could learn from them. If the task posts into one member’s private conversation, then only that member should automatically be able to read it.

There are two extra details. First, if the caller has no member identity, they cannot read private task content unless it is shared with the workspace. Second, if the task was created by a member, that creator can read it too, even if the audience check did not already allow them. This matters for creator-owned tasks and for tasks whose creator is missing: a missing creator does not make the task public. The audience remains the main privacy signal, like deciding who may read a letter by looking at the mailbox it was delivered to.

#### Function details

##### `task_content_visible`  (lines 7–20)

```
def task_content_visible(listed: ListedTask, member_id: UUID | None) -> bool
```

**Purpose**: Decides whether a given member may read a listed scheduled task’s prompt and description. It is used as a privacy gate so task content is only shown when the task’s reporting audience or creator relationship allows it.

**Data flow**: It receives a listed task and an optional member ID. It first checks whether the task’s audience is shared with the workspace; if so, it returns true. If there is no member ID, it returns false for non-shared tasks. Otherwise it compares the task’s audience with that member’s private subject, and finally checks whether the same member created the task. The output is a yes-or-no answer, and it does not change the task or member data.

**Call relations**: When another part of the scheduled-tasks extension needs to show or hide task content, it can call this function for the current member. Inside the decision, it asks `subject_shared` whether the task reports to a shared audience, and uses `member_subject` to build the private audience identity for the member so it can compare that with the task’s audience.

*Call graph*: 2 external calls (member_subject, subject_shared).

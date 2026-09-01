# Workspace Object Request Handling  `stage-6.3`

This stage is the system’s front desk for workspace objects. It is used during normal operation whenever a person, tool, or agent asks to list, read, create, change, delete, or run an action on something in the workspace. Each file teaches the shared object system how one kind of item behaves and who may touch it.

Core files expose built-in objects: agents can be created, edited, archived, or restored; members can be viewed or changed with permission checks; the workspace summary is read-only; conversations can be inspected when allowed; credential slots show whether secrets are filled without revealing them; extensions and chat surfaces are visible but not editable.

Extension files add more object kinds to the same front desk. Sources, source triggers, synced pages, and gbrain sources describe outside content and how it is refreshed or shared. Memory records and profiles are readable only by allowed users. Monitors show scheduled watches that can wake an agent and can be stopped. Reports expose digest runs. Hosted sites can be inspected, shared, changed, or unhosted. Together, these files make many different features feel like one consistent workspace inventory.

## Files in this stage

### Core workspace catalogs
Read-only core object views expose deployed extensions, past conversations, and declared credential slots without allowing unsafe mutation or secret disclosure.

### `core/src/ufo/host/ext/extension_kind.py`

`domain_logic` · `startup and object request handling`

This file answers a simple question: “What extensions is this UFO deploy running, and what do they contribute?” An extension is described by a manifest, which is like a packing list: it names the tools, object kinds, credential slots, chat surfaces, jobs, hooks, sources, and subagents the extension brings. This file projects those manifests as `extension` objects that can be listed or read.

The important safety rule is that these objects are declarations, not editable records. They describe what was loaded when the server started. Installing or removing an extension changes the deploy lockfile and only takes effect on the next serve, so `apply` and `delete` always refuse. That prevents a chat action from silently changing the running system’s extension set.

The file also standardizes extension object names. Manifest names are lowercased and changed into hyphenated object names, so something like `scheduled_tasks` becomes `scheduled-tasks`. If two manifests would collapse to the same object name, startup fails loudly instead of hiding one behind the other.

When someone lists extensions, they see a compact summary with counts. When they get one extension, they see its full declaration. Status shows what the extension asks from the deploy, such as sandbox internet access or required seams from other extensions.

#### Function details

##### `named_extensions`  (lines 46–61)

```
def named_extensions(manifests: tuple[Manifest, ...]) -> dict[str, Manifest]
```

**Purpose**: Builds the public object-name map for the active extension manifests. It makes extension names safe and consistent for workspace object access, and catches name collisions during startup.

**Data flow**: It receives a tuple of extension manifests. For each manifest, it lowercases the manifest name, replaces runs of non-letter-or-number characters with hyphens, trims extra hyphens, checks that the result is a valid object name, and stores it in a dictionary. The result is a mapping from clean object names to manifests; if two manifests produce the same name, it raises an error instead of returning an ambiguous map.

**Call relations**: This is used when the active extension set is being prepared for projection as objects. It relies on regular-expression cleanup and the shared object-name validator so the names used here follow the same rules as the rest of the workspace object system.

*Call graph*: 2 external calls (sub, validate_object_name).


##### `ExtensionObjects.list`  (lines 90–107)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Returns a page of all loaded extensions in a compact list form. It helps a user quickly see which extensions are active, their versions, and how many tools and credential slots they declare.

**Data flow**: It reads the stored mapping of extension object names to manifests. For each manifest, it asks `_spec` to turn the manifest into a plain declaration, then builds list rows containing the name, a human-readable summary, and sortable fields such as version, tool count, and credential slot count. It then passes those rows and the user’s list query to the paging helper, which returns the requested page.

**Call relations**: When the object system needs to list `extension` objects, this method is the read path. It delegates the manifest-to-spec translation to `ExtensionObjects._spec`, wraps each result as an `ObjectRow`, and hands the final rows to `object_page` so normal listing features like paging, filtering, or ordering can be applied.

*Call graph*: calls 1 internal fn (_spec); 2 external calls (__init__, object_page).


##### `ExtensionObjects.get`  (lines 109–113)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ExtensionSpec] | None
```

**Purpose**: Returns the full declaration for one loaded extension. It lets a user inspect exactly what that extension contributes without exposing any secret values.

**Data flow**: It receives an object name and looks it up in the stored extension map. If no manifest exists for that name, it returns `None`. If it finds one, it turns the manifest into an `ExtensionSpec` using `_spec` and wraps it in an object detail whose creation and update timestamps are `None`, because these are loaded declarations rather than database rows.

**Call relations**: This is called when someone reads one `extension` object by name. Like the list path, it relies on `ExtensionObjects._spec` for the actual declaration shape, then packages that declaration as an `ObjectDetail` for the object system.

*Call graph*: calls 1 internal fn (_spec); 1 external calls (__init__).


##### `ExtensionObjects.status`  (lines 115–128)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns deploy-facing requirements for one extension. This separates what the extension contributes from what it asks the deploy to provide.

**Data flow**: It receives an object name and looks up the matching manifest. If there is no such extension, it returns `None`. If found, it returns a small dictionary containing whether the extension’s sandbox tools need public internet access and which required seams must be provided by other extensions.

**Call relations**: This is used when the object system asks for an extension object’s status. It does not call other helpers because it only exposes two direct manifest fields: `sandbox_internet` and `requires`.


##### `ExtensionObjects.apply`  (lines 130–139)

```
async def apply(self, ctx: ToolContext, name: str, spec: ExtensionSpec, old: ExtensionSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to create or update an extension object. Extension installation and removal are deploy operations done through the lockfile, not live object edits.

**Data flow**: It receives the attempted name, desired spec, old spec, and generation check information, but deliberately does not use them to change anything. Instead, it raises a `VerbNotSupported` error with a message explaining that extensions must be installed or removed with deploy tooling.

**Call relations**: This is called if the object system tries to apply a change to an `extension` object. Its whole role is to stop that path and point users toward the proper deploy-level command.

*Call graph*: 1 external calls (__init__).


##### `ExtensionObjects.delete`  (lines 141–148)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to delete an extension object. Removing an extension is not allowed through the workspace object interface.

**Data flow**: It receives the object name and generation check information, but it does not remove anything from the extension map. It immediately raises a `VerbNotSupported` error explaining that extension removal must happen through the deploy lockfile and take effect on the next serve.

**Call relations**: This is called if the object system tries to delete an `extension` object. Like `ExtensionObjects.apply`, it protects the running deploy from being changed through a chat or object mutation path.

*Call graph*: 1 external calls (__init__).


##### `ExtensionObjects._spec`  (lines 150–168)

```
def _spec(self, manifest: Manifest) -> ExtensionSpec
```

**Purpose**: Turns a raw extension manifest into the public `ExtensionSpec` shown to users. It includes names of contributed things, but not secret values or live runtime data.

**Data flow**: It receives one manifest. It copies out the extension name and version, gathers tool names from both top-level tools and connector tools, and collects the names of object kinds, credential slots, surfaces, jobs, hook events, source backends, and subagent profiles. It returns an `ExtensionSpec` containing only these public declarations.

**Call relations**: This is the shared translation step used by `ExtensionObjects.list` and `ExtensionObjects.get`. Those methods decide how much detail to return, while `_spec` decides how a manifest becomes the safe, user-facing declaration.

*Call graph*: called by 2 (get, list); 1 external calls (__init__).


### `core/src/ufo/host/kinds/conversations.py`

`domain_logic` · `request handling`

A conversation in this system is not something a tool creates directly. It is created by a chat surface, such as the web portal or an extension, and later other objects can point back to it. This file is the bridge that makes those stored conversation rows understandable through the object system.

The main class, ConversationObjects, answers questions like: “Which conversations can this caller see?”, “What are the details for this conversation id?”, and “Can I materialize the visible transcript into the workspace?” It is careful about privacy. Most reads are limited by audience subjects, which are labels saying who is allowed to see something. A workspace admin can see metadata for another member’s private conversation only in a narrow case, and even then not the transcript. That is like seeing a sealed envelope’s label, but not opening the envelope.

The file also supports the portal view for a signed-in member. That view shows the member’s own conversations plus readable shared ones, with friendly fields such as title, speaker, surface, and last activity time.

All write operations are refused. Without this file, links from artifacts or scheduled tasks to conversations would not resolve cleanly, and callers would either miss useful history or risk reading conversation data they should not see.

#### Function details

##### `ConversationObjects.list`  (lines 83–90)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists conversation objects visible to the current tool request. If the caller explicitly asks for private rows and is an allowed admin, it also includes limited metadata for other members’ private conversations.

**Data flow**: It receives a tool context, which carries the caller’s read permissions, and a list query with filters and paging rules. It reads visible conversation rows from the database, converts each row into a simple object-list row, optionally adds allowed private metadata rows, and then returns a paged object list.

**Call relations**: The object system calls this when someone asks to list conversations. It uses _rows for normal visible rows, _widens_for_admin and _private_rows for the special admin-only private metadata case, _row to shape database rows for display, and object_page to apply the query’s paging and ordering rules.

*Call graph*: calls 4 internal fn (_private_rows, _rows, _widens_for_admin, _row); 1 external calls (object_page).


##### `ConversationObjects.get`  (lines 92–96)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ConversationSpec] | None
```

**Purpose**: Looks up one conversation by its name, which is expected to be its unique id. It returns the conversation’s details if the caller is allowed to see them.

**Data flow**: It receives the tool context and a conversation name. It first searches the caller’s normally visible conversations; if that fails, it tries the limited admin private lookup. If a row is found, it turns it into detailed object information; otherwise it returns nothing.

**Call relations**: The object system calls this for a single conversation lookup. It relies on _find for normal access, _private_find for the special admin metadata path, and _detail to build the final detail object.

*Call graph*: calls 3 internal fn (_find, _private_find, _detail).


##### `ConversationObjects.member_page`  (lines 98–139)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Builds the portal’s conversation list for one signed-in member. It shows that member’s own conversations and readable conversations involving others, without expanding the view just because the member is an admin.

**Data flow**: It receives the extension context, member id, admin flag, and list query. It asks the conversation directory for two bounded sets: the member’s own conversations and other readable conversations. It skips rows with no title, turns the rest into portal-friendly object rows, and returns a paged list.

**Call relations**: The portal-facing object flow calls this outside an active tool turn. It creates a ConversationDirectory for the current workspace, uses object_agent_id to stay within the selected agent, calls _member_row for each listed directory entry, and passes the accumulated rows through object_page.

*Call graph*: calls 1 internal fn (_member_row); 4 external calls (__init__, object_agent_id, object_page, ws_current).


##### `ConversationObjects.member_detail`  (lines 141–156)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[ConversationSpec] | None
```

**Purpose**: Returns one conversation for the portal view of a signed-in member, outside a tool turn. It only allows conversations whose audience matches that member’s own readable subjects.

**Data flow**: It receives a member id and conversation name. It builds the audience subjects for that member, searches for a matching visible conversation, and if found returns both a list-style row and a detailed spec. If nothing is allowed or found, it returns nothing.

**Call relations**: The portal-facing detail flow calls this when a member opens one conversation. It uses conversation_audience and audience_subjects to compute what the member can read, _find to locate the row, _row for the summary part, _detail for the detail part, and MemberObject to package both together.

*Call graph*: calls 3 internal fn (_find, _detail, _row); 3 external calls (__init__, audience_subjects, conversation_audience).


##### `ConversationObjects.status`  (lines 158–176)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports transcript status for a visible conversation and, when the transcript is small enough, writes a plain text copy into the caller’s workspace. It refuses to reveal anything if the conversation stops being visible during the operation.

**Data flow**: It receives the tool context, conversation name, and an expected generation value that this implementation does not use. It finds the conversation, reads and formats its transcript, checks that the same row is still visible, optionally writes the text to a workspace file, and returns message count, byte size, and the file path if one was written.

**Call relations**: The object system calls this when a caller asks for status on a conversation. It uses _find to check access, _exchange to read transcript text, _unchanged_visible as a second safety check after reading, and raises UnknownObject if visibility changed before the result could be returned.

*Call graph*: calls 3 internal fn (_exchange, _find, _unchanged_visible); 1 external calls (__init__).


##### `ConversationObjects.apply`  (lines 178–187)

```
async def apply(self, ctx: ToolContext, name: str, spec: ConversationSpec, old: ConversationSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to create or update a conversation through the object system. Conversations must come from chat surfaces, not from object writes.

**Data flow**: It receives the requested conversation name, new spec, old spec, and context, but does not use them to change data. It immediately raises an error explaining that this verb is not supported.

**Call relations**: The object system calls this when someone tries to apply a conversation object. It hands control to VerbNotSupported, using the shared message that conversations are surface-made.

*Call graph*: 1 external calls (__init__).


##### `ConversationObjects.delete`  (lines 189–196)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to delete a conversation through the object system. Conversation cleanup is left to retention rules rather than caller-driven deletion here.

**Data flow**: It receives the tool context, conversation name, and expected generation value, but makes no database change. It immediately raises an error saying the operation is not supported.

**Call relations**: The object system calls this when someone tries to delete a conversation object. It uses VerbNotSupported with the same read-only explanation used by apply.

*Call graph*: 1 external calls (__init__).


##### `ConversationObjects._exchange`  (lines 198–218)

```
async def _exchange(self, ctx: ToolContext, conversation_id: UUID) -> tuple[str, ...]
```

**Purpose**: Reads a stored conversation transcript and turns it into simple text lines like “user: hello”. It hides missing transcripts by treating them as an empty exchange, but reports corrupted transcripts as an error.

**Data flow**: It receives a tool context and conversation id. It builds the blob key for that transcript, reads the stored blob, decodes it, extracts plain text from each message, and returns a tuple of formatted message lines. If the blob is missing, it returns an empty tuple.

**Call relations**: ConversationObjects.status calls this when it needs transcript text for a visible conversation. This helper delegates the storage key format to transcript_key and the transcript parsing to decode, then gives status plain lines that can be counted and written into the workspace.

*Call graph*: called by 1 (status); 2 external calls (decode, transcript_key).


##### `ConversationObjects._unchanged_visible`  (lines 220–233)

```
async def _unchanged_visible(self, subjects: frozenset[str], row: sa.Row) -> bool
```

**Purpose**: Checks that a conversation row is still visible to the same subjects and still has the same audience. This protects against a privacy race where access changes while transcript text is being prepared.

**Data flow**: It receives the caller’s readable subjects and the row that was previously found. It opens a workspace database transaction, asks whether a matching visible row still exists with the same id and audience, and returns true or false.

**Call relations**: ConversationObjects.status calls this after reading the transcript but before returning or writing the result. It builds its visibility query with _visible and runs an existence check through the workspace database transaction.

*Call graph*: calls 1 internal fn (_visible); called by 1 (status); 3 external calls (exists, select, workspace_tx).


##### `ConversationObjects._find`  (lines 235–241)

```
async def _find(self, subjects: frozenset[str], name: str) -> sa.Row | None
```

**Purpose**: Finds one visible conversation by id-like name. If the name is not a valid unique id, it simply says no row was found.

**Data flow**: It receives readable subjects and a name string. It tries to parse the name as a UUID, which is a standard unique identifier; if parsing works, it asks _rows for that specific conversation and returns the first match, or nothing.

**Call relations**: ConversationObjects.get, member_detail, and status use this as their normal lookup path. It keeps id parsing in one place and delegates the actual database read to _rows.

*Call graph*: calls 1 internal fn (_rows); called by 3 (get, member_detail, status); 1 external calls (UUID).


##### `ConversationObjects._rows`  (lines 243–250)

```
async def _rows(self, subjects: frozenset[str], *, conversation_id: UUID | None) -> tuple[sa.Row, ...]
```

**Purpose**: Reads conversation rows for the selected agent that are visible to a given set of subjects. It can read all visible rows or just one specific conversation.

**Data flow**: It receives readable subjects and an optional conversation id. It builds a database query limited to visible conversations, adds an id filter if supplied, runs the query inside a workspace transaction, and returns the resulting rows.

**Call relations**: ConversationObjects.list uses this to get all normally visible conversations, and _find uses it to get one. It depends on _visible to build the privacy-aware query and workspace_tx to run it against the current workspace database.

*Call graph*: calls 1 internal fn (_visible); called by 2 (_find, list); 1 external calls (workspace_tx).


##### `ConversationObjects._widens_for_admin`  (lines 252–255)

```
async def _widens_for_admin(self, ctx: ToolContext) -> bool
```

**Purpose**: Decides whether the current caller may see limited metadata for private conversations outside their normal audience. It allows this only for a speaking workspace admin and never for a foreign-audience turn.

**Data flow**: It reads the tool context’s audience string first. If the audience marks a foreign context, it returns false. Otherwise it asks whether the speaker is an admin and returns that answer.

**Call relations**: ConversationObjects.list calls this before adding private metadata rows, and _private_find calls it before looking up one private row. It delegates the admin check to ToolContext.speaker_is_admin.

*Call graph*: calls 1 internal fn (speaker_is_admin); called by 2 (_private_find, list).


##### `ConversationObjects._private_find`  (lines 257–265)

```
async def _private_find(self, ctx: ToolContext, name: str) -> sa.Row | None
```

**Purpose**: Finds one other member’s private conversation metadata for an allowed admin. It does not expose transcript content.

**Data flow**: It receives the tool context and a conversation name. It first checks whether admin widening is allowed, then parses the name as a UUID, reads matching private rows, and returns the first row if present.

**Call relations**: ConversationObjects.get uses this only after the normal visible lookup fails. It relies on _widens_for_admin for the permission gate and _private_rows for the database query.

*Call graph*: calls 2 internal fn (_private_rows, _widens_for_admin); called by 1 (get); 1 external calls (UUID).


##### `ConversationObjects._private_rows`  (lines 267–277)

```
async def _private_rows(self, ctx: ToolContext, *, conversation_id: UUID | None) -> tuple[sa.Row, ...]
```

**Purpose**: Reads private conversation metadata for the selected agent that belongs to member audiences outside the caller’s normal read subjects. This is the narrow admin-only metadata source.

**Data flow**: It receives the tool context and an optional conversation id. It builds a query for the selected agent’s conversations whose audience looks like a member-private audience and is not already visible to the caller, optionally narrows by id, runs it in a workspace transaction, and returns the rows.

**Call relations**: ConversationObjects.list calls this when an allowed admin lists with the private filter, and _private_find calls it for one id. It starts from _agent_conversations so the result is still limited to the current workspace and selected agent.

*Call graph*: calls 1 internal fn (_agent_conversations); called by 2 (_private_find, list); 1 external calls (workspace_tx).


##### `_agent_conversations`  (lines 280–301)

```
def _agent_conversations() -> sa.Select
```

**Purpose**: Builds the base database query for conversations belonging to the current workspace and selected agent. It also includes the agent’s display name so detail results can link back to that agent.

**Data flow**: It reads the current workspace id and selected agent id from ambient runtime state. It creates a SQL query joining conversation rows to their agent row, selects the fields this file needs, and returns the query object without running it yet.

**Call relations**: _visible uses this as the starting point for normal visibility filtering, and ConversationObjects._private_rows uses it for the private metadata query. It delegates workspace and agent identity to ws_current and object_agent_id.

*Call graph*: called by 2 (_private_rows, _visible); 3 external calls (select, object_agent_id, ws_current).


##### `_visible`  (lines 304–305)

```
def _visible(subjects: frozenset[str]) -> sa.Select
```

**Purpose**: Adds the normal audience privacy rule to the base conversation query. A conversation is visible only when its audience is one of the caller’s readable subjects.

**Data flow**: It receives a set of readable subject strings. It starts with the selected agent’s conversation query, adds an audience-in-subjects filter, and returns the filtered query for someone else to execute.

**Call relations**: ConversationObjects._rows uses this to read visible rows, and _unchanged_visible uses it to re-check visibility after transcript reading. It depends on _agent_conversations for the workspace-and-agent base query.

*Call graph*: calls 1 internal fn (_agent_conversations); called by 2 (_rows, _unchanged_visible).


##### `_member_row`  (lines 308–326)

```
def _member_row(entry: ListedConversation, *, mine: bool) -> ObjectRow
```

**Purpose**: Turns a directory-listed conversation into the compact row shown in a member’s portal conversation list. It adds friendly fields such as title, whether it is mine, speaker, surface, portal status, and last activity time.

**Data flow**: It receives a ListedConversation entry and a flag saying whether it belongs to the member. It chooses a speaker name when useful, chooses the latest activity timestamp, fills a dictionary of display fields, and returns an ObjectRow.

**Call relations**: ConversationObjects.member_page calls this for each directory entry that has a title. It hands the finished ObjectRow back to member_page, which later pages the full list with object_page.

*Call graph*: called by 1 (member_page); 1 external calls (__init__).


##### `_row`  (lines 329–344)

```
def _row(row: sa.Row, *, private: bool=False) -> ObjectRow
```

**Purpose**: Turns a database conversation row into a generic object-list row. It creates a readable summary such as where the conversation happened and when it was created.

**Data flow**: It receives a database row and an optional private flag. It builds an origin label from the surface and surface label, includes key fields, marks the row private when requested, and returns an ObjectRow.

**Call relations**: ConversationObjects.list uses this for normal and private metadata listings, and member_detail uses it for the row portion of a member detail response. It packages raw database values into the object system’s list-row shape.

*Call graph*: called by 2 (list, member_detail); 1 external calls (__init__).


##### `_detail`  (lines 347–359)

```
def _detail(row: sa.Row) -> ObjectDetail[ConversationSpec]
```

**Purpose**: Turns a database conversation row into full object detail. The detail includes the conversation spec, timestamps, and a link back to the agent the conversation is scoped to.

**Data flow**: It receives a database row. It copies the surface, surface label, and audience into a ConversationSpec, carries over created and updated times, builds an ObjectLink to the agent, and returns an ObjectDetail.

**Call relations**: ConversationObjects.get and member_detail call this when they have found a readable row. It uses ConversationSpec, ObjectDetail, ObjectLink, and ObjectRef to produce the standard detailed object shape.

*Call graph*: called by 2 (get, member_detail); 4 external calls (__init__, __init__, __init__, __init__).


### `core/src/ufo/host/kinds/credential_kind.py`

`domain_logic` · `request handling`

Some extensions need a user-provided secret, such as an API key. This file turns those declared secret places into normal workspace objects called credentials. Think of each slot like a labeled safe deposit box: the system can show that the box exists and whether something is inside, but it never opens the box for readers.

The slot declaration comes from an extension manifest, not from the database row that stores the encrypted secret. Because of that, an empty slot still appears in lists. If the secret has never been stored, there is simply no matching database row, and the displayed timestamps are blank.

The main class, CredentialObjects, offers the usual object-style actions: list all slots, get one slot’s details, check its status, and delete its stored value. Listing combines two sources of truth: the declared slots from active extensions and the database rows that say which slots are filled. Detail reads the declaration and, if a value exists, its created and updated times. It still never reads or returns the secret itself.

Creating or updating a credential is deliberately blocked here. Filling or rotating a secret must go through a separate private request_credentials flow. Deleting is allowed only for a workspace admin, and it clears the stored value while leaving the declared slot visible as empty.

#### Function details

##### `CredentialObjects.list`  (lines 77–78)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Returns a page of credential slots for the normal workspace object listing. It shows every declared slot and whether it is filled, but never includes the secret value.

**Data flow**: It receives a tool context and a list query. It asks _rows to build the full set of visible credential rows, then passes those rows and the query to object_page so filtering, ordering, or paging can be applied. The result is an ObjectPage ready to return to the caller.

**Call relations**: This is the public list path for this object kind. It relies on _rows to combine manifest declarations with database fill state, then hands the finished rows to the shared object_page helper so credentials behave like other workspace objects.

*Call graph*: calls 1 internal fn (_rows); 1 external calls (object_page).


##### `CredentialObjects.member_page`  (lines 80–90)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Returns the same kind of credential slot listing for a signed-in portal member. Since credential declarations are workspace-wide and reveal no secret value, members see the same slot index as the tool-side list.

**Data flow**: It receives optional extension context, the member identity, admin flag, and list query. It does not need member-specific filtering here; it builds rows through _rows and turns them into a paged result with object_page. The output is a page of declared credential slots with fill status.

**Call relations**: This is the member-facing listing path. Like list, it depends on _rows for the real slot information and uses object_page to present it in the standard page shape.

*Call graph*: calls 1 internal fn (_rows); 1 external calls (object_page).


##### `CredentialObjects.get`  (lines 92–93)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[CredentialSpec] | None
```

**Purpose**: Returns detailed information for one credential slot in the workspace object API. It describes the declared slot and its timestamps if filled, but not the stored secret.

**Data flow**: It receives a tool context and a slot name. It passes the name to _detail, which looks up the declaration and database timestamps. It returns that ObjectDetail, or None if no extension declared such a slot.

**Call relations**: This is the public detail path for tool-side reads. It delegates all lookup and shaping work to _detail so the same detail logic can also be reused by the member-facing path.

*Call graph*: calls 1 internal fn (_detail).


##### `CredentialObjects.member_detail`  (lines 95–110)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[CredentialSpec] | None
```

**Purpose**: Returns one credential slot as a portal member should see it: both the row-style summary and the detailed declaration. It keeps the same rule as other reads: no secret value is exposed.

**Data flow**: It receives optional extension context, the slot name, member identity, and admin flag. It first asks _detail for the slot declaration and timestamps; if the slot is unknown, it returns None. Otherwise it asks _rows for the current listing rows, finds the matching row, and combines the row and detail into a MemberObject.

**Call relations**: This is the member-facing detail path. It reuses _detail for the detailed declaration and _rows for the summary row, then packages both together through MemberObject so the portal gets the standard member object shape.

*Call graph*: calls 2 internal fn (_detail, _rows); 1 external calls (__init__).


##### `CredentialObjects.status`  (lines 112–136)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports the live status of one credential slot: mainly whether it is filled. If the slot has a host choice and a credential store is available, it can also report the resolved host value.

**Data flow**: It receives a tool context, slot name, and optional expected generation value. It first maps declared slot names with _named; if the slot is not declared, it returns None. For a known slot, it opens a workspace database transaction, checks whether a credential row exists for the current workspace and slot, and builds a status dictionary with filled set to true or false. If host resolution applies, it asks credential_host for the current host and adds it to the output.

**Call relations**: This function is used when the object system needs lightweight state rather than the full declaration. It uses _named to reject undeclared slots, workspace_tx and ws_current to read the right workspace’s row, and credential_host when host information must be resolved from the credential store.

*Call graph*: calls 1 internal fn (_named); 4 external calls (select, workspace_tx, credential_host, ws_current).


##### `CredentialObjects.apply`  (lines 138–147)

```
async def apply(self, ctx: ToolContext, name: str, spec: CredentialSpec, old: CredentialSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses create or update attempts for credential slots. This protects secrets by forcing fills and rotations through the separate private request_credentials process.

**Data flow**: It receives the requested slot name, new spec, optional old spec, and optional expected generation. Instead of storing anything, it immediately raises VerbNotSupported with an explanation. Nothing is written and no credential value is accepted through this path.

**Call relations**: This is called when the generic object system tries to apply a create or update verb to this object kind. It intentionally stops the flow at the door and points callers toward the credential request action, where secret handoff and authorization are designed to happen.

*Call graph*: 1 external calls (__init__).


##### `CredentialObjects.delete`  (lines 149–165)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Clears the stored value for a credential slot. It does not remove the slot itself, because the slot is declared by an extension and should still appear as empty afterward.

**Data flow**: It receives a tool context, slot name, and optional expected generation. It first asks the context whether the speaker is a workspace admin; if not, it raises AdminRequired. If allowed, it finds the declared slot with _named, opens a workspace database transaction, and deletes the credential row for the current workspace and slot. The result is no return value, but the stored secret is removed if it existed.

**Call relations**: This is the object delete path for credentials. It depends on ToolContext.speaker_is_admin for the permission gate, _named for the slot declaration, and workspace_tx with ws_current to delete only the row belonging to the current workspace.

*Call graph*: calls 2 internal fn (_named, speaker_is_admin); 4 external calls (__init__, delete, workspace_tx, ws_current).


##### `CredentialObjects._rows`  (lines 167–179)

```
async def _rows(self) -> tuple[ObjectRow, ...]
```

**Purpose**: Builds the list rows that show each declared credential slot and whether it is filled. This is the shared source for both normal and member-facing listings.

**Data flow**: It first asks _filled_slots for the set of slot names that have stored credential rows. It also asks _named for the declared slots keyed by name. For each declared slot, sorted by name, it creates an ObjectRow containing the slot name, a readable summary, the extension name, and a filled true-or-false field. It returns all rows as a tuple.

**Call relations**: list and member_page call this to produce pages, and member_detail calls it to attach the row summary beside a detailed member view. It hands off each row to ObjectRow so the rest of the object system can display credentials in the standard row format.

*Call graph*: calls 2 internal fn (_filled_slots, _named); called by 3 (list, member_detail, member_page); 1 external calls (__init__).


##### `CredentialObjects._detail`  (lines 181–205)

```
async def _detail(self, name: str) -> ObjectDetail[CredentialSpec] | None
```

**Purpose**: Builds the detailed read view for one credential slot. It reports the slot declaration and fill timestamps, while carefully avoiding the actual secret value.

**Data flow**: It receives a slot name. It looks up the declared slot through _named; if none exists, it returns None. For a declared slot, it reads the credential row’s created and updated times from the current workspace, if a row exists. It then creates a CredentialSpec from the declaration, including description, extension, and host information, and wraps it in an ObjectDetail with timestamps that are blank when the slot is empty.

**Call relations**: get uses this for tool-side detail reads, and member_detail uses it for portal detail reads. It relies on workspace_tx and ws_current to read only the current workspace’s timestamps, and on CredentialSpec and ObjectDetail to return the data in the standard object shape.

*Call graph*: calls 1 internal fn (_named); called by 2 (get, member_detail); 5 external calls (__init__, __init__, select, workspace_tx, ws_current).


##### `CredentialObjects._named`  (lines 207–208)

```
def _named(self) -> dict[str, DeclaredSlot]
```

**Purpose**: Turns the stored tuple of declared credential slots into a lookup by slot name. This lets other methods quickly find the declaration for a requested slot.

**Data flow**: It reads self.slots, which contains the declared slots collected from active extension manifests. It passes those slots to named_slots, which returns a dictionary keyed by slot name. The output is that dictionary of slot name to declaration.

**Call relations**: _rows uses this to iterate over all declared slots, while _detail, status, and delete use it to find one requested slot. It is the small translation step between the manifest-style slot list and the name-based object API.

*Call graph*: called by 4 (_detail, _rows, delete, status); 1 external calls (named_slots).


##### `CredentialObjects._filled_slots`  (lines 210–219)

```
async def _filled_slots(self) -> frozenset[str]
```

**Purpose**: Finds which credential slots currently have stored values in the database for the active workspace. It returns only slot names, not secret values.

**Data flow**: It opens a workspace database transaction, reads credential slot names where the workspace id matches the current workspace, and collects those names. It returns them as a frozen set, making it easy for callers to ask whether a declared slot is filled.

**Call relations**: _rows calls this before building the visible list of credential objects. By using workspace_tx and ws_current, it supplies fill state only for the current workspace, which _rows then combines with declared slots from _named.

*Call graph*: called by 1 (_rows); 3 external calls (select, workspace_tx, ws_current).


### Workspace roster and agents
Workspace membership, surfaces, workspace facts, and agents define the main editable and read-only objects that shape who can participate and what agents can do.

### `core/src/ufo/host/kinds/members.py`

`domain_logic` · `request handling`

A workspace member is more than an email address here. The member record says whether the person is an admin and whether they are “seated,” meaning allowed to speak to the agent. This file turns those records into a registered object kind called `member`, so the rest of the system can list members, read one member, update role or access, and refuse unsupported actions like deletion.

The central idea is privacy and authority. In an internal conversation with the main agent, the roster can be visible. In a child agent or a channel shared with another organization, the view is narrowed, often to just the signed-in speaker’s own row. That is like a company directory that is open inside the office, but not shown in a meeting room with outsiders.

The file also defines the `add_member` tool. This lets a workspace admin add someone before that person has contacted the system, including people at outside email domains. It checks that the speaker is a real member, is using the main agent, is not in an externally shared room, is an admin, and is not adding a duplicate. Then it creates the member and optionally sends an invitation email.

Important safeguards live here too: members cannot be deleted through this object interface, only admins can change role or seat, and the system refuses to leave a workspace with no admin or no seated admin.

#### Function details

##### `MemberObjects.list`  (lines 68–72)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows a page of member rows that the current speaker is allowed to see. It is used when someone asks for the workspace member list through the object system.

**Data flow**: It receives the tool context, which includes who is speaking, which agent they are using, and the audience, plus paging or filtering options. It first asks `_visible_rows` for the allowed database rows, converts each row into a simple display row with `_row`, and then packages those rows into an `ObjectPage`. The result is a page of member summaries, not raw database records.

**Call relations**: This is the public list path for the member object during a turn. It relies on `_visible_rows` to enforce privacy rules, uses `_row` to shape each database record for display, and hands the final collection to `object_page` so the object framework can present it consistently.

*Call graph*: calls 2 internal fn (_visible_rows, _row); 1 external calls (object_page).


##### `MemberObjects.member_page`  (lines 74–91)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows a page of members for portal-style reads, outside the normal tool turn. It applies the portal rule: the main agent may show the whole workspace roster, while other agents show only the requesting member’s own row.

**Data flow**: It receives the requesting member id, admin flag, optional extension context, and page query. It asks `_member_rows` what rows this member may see for the current agent, turns those rows into display rows with `_row`, and returns a paged object list. The admin flag is present in the call, but this portal read does not use it to widen access.

**Call relations**: This is parallel to `MemberObjects.list`, but for live portal reads rather than a conversation turn. It delegates visibility to `_member_rows`, formats through `_row`, and hands the result to `object_page` for standard paging.

*Call graph*: calls 2 internal fn (_member_rows, _row); 1 external calls (object_page).


##### `MemberObjects.get`  (lines 93–95)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[MemberSpec] | None
```

**Purpose**: Reads the full details for one visible member object during a tool turn. If the named member is not visible to the speaker, it acts as if the object is not there.

**Data flow**: It receives the tool context and the requested object name, which is expected to be a member id as text. It asks `_visible_row` to find that row within the speaker’s allowed view. If a row is found, `_detail` turns it into an object detail containing the editable member settings and timestamps; otherwise it returns `None`.

**Call relations**: This is the detail-read partner to `MemberObjects.list`. It depends on `_visible_row` for permission-aware lookup and `_detail` for shaping the response used by the object framework.

*Call graph*: calls 2 internal fn (_visible_row, _detail).


##### `MemberObjects.member_detail`  (lines 97–113)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[MemberSpec] | None
```

**Purpose**: Reads one member’s row and detail for portal-style access. It follows the same portal visibility rule as `member_page`: the main agent can expose the roster, while other agents narrow to the requesting member.

**Data flow**: It receives the requested member name, the signed-in member id, and portal context information. It gets the allowed rows from `_member_rows`, looks for a row whose id matches the requested name, and returns nothing if there is no match. If it finds one, it combines `_row` for the summary and `_detail` for the full editable fields into a `MemberObject`.

**Call relations**: This is the portal counterpart to `MemberObjects.get`. It uses `_member_rows` for the portal-specific visibility decision, then hands row formatting to `_row` and detail formatting to `_detail` before returning a complete member object.

*Call graph*: calls 3 internal fn (_member_rows, _detail, _row); 1 external calls (__init__).


##### `MemberObjects.status`  (lines 115–130)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns a small status snapshot for one visible member. It is a lightweight way to learn the member’s email and whether they currently have access.

**Data flow**: It receives the tool context, member name, and an optional expected generation value. It finds the allowed row with `_visible_row`. If the row is not visible or does not exist, it returns `None`; otherwise it returns a small dictionary with the email address and a true-or-false seated value.

**Call relations**: This function shares the same permission-aware lookup as `get`, but returns only status fields instead of a full object detail. It depends on `_visible_row` and does not call the formatting helpers because its output is intentionally smaller.

*Call graph*: calls 1 internal fn (_visible_row).


##### `MemberObjects.apply`  (lines 132–211)

```
async def apply(self, ctx: ToolContext, name: str, spec: MemberSpec, old: MemberSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Changes an existing member’s admin role or seated access. It is the guarded edit path for membership: only a workspace admin using the main agent can make these changes.

**Data flow**: It receives the tool context, a member id as text, the desired `MemberSpec`, the previous spec if any, and an optional generation check. It first rejects callers who are not signed-in members using the main agent, rejects attempts to create members through this path, and checks that the name is a valid member id. Inside a workspace database transaction, it locks the workspace row, verifies the speaker is still an admin, loads the target member, grants or revokes the seat if needed, and updates the admin flag if needed. It changes the database and returns no content when successful.

**Call relations**: This is the write path behind applying changes to a `member` object. It calls into the tool context for the main-agent check, uses `workspace_tx` and SQL queries for safe database changes, asks `member_is_admin` to confirm authority, and uses `Seats` to grant or revoke access. It raises object-system errors when the action is forbidden, unsupported, or points at an unknown member.

*Call graph*: calls 1 internal fn (agent_is_main); 10 external calls (__init__, __init__, __init__, __init__, select, update, workspace_tx, member_is_admin, ws_current, UUID).


##### `MemberObjects.delete`  (lines 213–220)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses deletion of member objects. The system intentionally removes access by unseating a member instead of deleting their membership record here.

**Data flow**: It receives the tool context, member name, and optional generation value, but does not read or change any member data. It immediately raises a `VerbNotSupported` error explaining that members cannot be deleted through objects.

**Call relations**: This is the delete hook required by the object interface. Instead of passing work onward, it stops the flow so callers use the supported path: edit the member’s `seated` field rather than deleting the row.

*Call graph*: 1 external calls (__init__).


##### `MemberObjects._visible_rows`  (lines 222–228)

```
async def _visible_rows(self, ctx: ToolContext) -> tuple[sa.Row, ...]
```

**Purpose**: Decides which member rows the current speaker may see during a tool turn. It is the privacy gate for roster listing and per-member lookup.

**Data flow**: It reads the tool context: whether there is a signed-in speaker, whether the audience is shared with another organization, whether the current agent is the main one, and whether the speaker is an admin. With no signed-in speaker it returns no rows. In a foreign/shared audience it returns only the speaker’s own row. Otherwise it returns the full roster only when the speaker is on the main agent or is an admin; in narrower cases it returns just the speaker.

**Call relations**: `MemberObjects.list` uses this to build member pages, and `_visible_row` uses it to search for one member. It delegates the actual database read to `_roster` after deciding whether the read should be whole-workspace or self-only.

*Call graph*: calls 3 internal fn (_roster, agent_is_main, speaker_is_admin); called by 2 (_visible_row, list).


##### `MemberObjects._visible_row`  (lines 230–234)

```
async def _visible_row(self, ctx: ToolContext, name: str) -> sa.Row | None
```

**Purpose**: Finds one named member within the rows the current speaker is allowed to see. It combines lookup with the same privacy rules used for listing.

**Data flow**: It receives the tool context and a requested member name. It asks `_visible_rows` for the allowed set, compares each row’s id to the requested name, and returns the matching row if present. If the member is outside the allowed view or does not exist in that view, it returns `None`.

**Call relations**: `MemberObjects.get` and `MemberObjects.status` call this whenever they need one member. By going through `_visible_rows`, those read paths cannot accidentally reveal a hidden roster entry.

*Call graph*: calls 1 internal fn (_visible_rows); called by 2 (get, status).


##### `MemberObjects._member_rows`  (lines 236–248)

```
async def _member_rows(self, member_id: UUID) -> tuple[sa.Row, ...]
```

**Purpose**: Decides which member rows a signed-in portal reader may see for the current agent. It is the portal version of the roster visibility rule.

**Data flow**: It receives the signed-in member id. It opens a workspace transaction, checks whether the current agent is the main agent, and then calls `_roster` with `whole` set to true only for the main agent. The output is either the whole workspace roster or just that member’s own row.

**Call relations**: `member_page` and `member_detail` use this for portal reads. It uses the current workspace and current agent to make the visibility decision, then hands the actual database query to `_roster`.

*Call graph*: calls 1 internal fn (_roster); called by 2 (member_detail, member_page); 4 external calls (select, workspace_tx, agent_current, ws_current).


##### `MemberObjects._roster`  (lines 250–270)

```
async def _roster(self, member_id: UUID, *, whole: bool) -> tuple[sa.Row, ...]
```

**Purpose**: Fetches member rows from the database, either the whole workspace roster or one member’s own row. It is the shared database reader behind both turn-based and portal-based member views.

**Data flow**: It receives a member id and a `whole` flag. It builds a database query for the current workspace, selecting email, id, admin flag, seat timestamp, and creation/update times, ordered by email. If `whole` is false, it adds a filter for the given member id. It returns the matching rows as a tuple.

**Call relations**: `_visible_rows` and `_member_rows` call this after they have decided how wide the view should be. This keeps the visibility decision separate from the database query that actually retrieves the roster.

*Call graph*: called by 2 (_member_rows, _visible_rows); 3 external calls (select, workspace_tx, ws_current).


##### `_row`  (lines 273–286)

```
def _row(row: sa.Row) -> ObjectRow
```

**Purpose**: Turns a database member row into a compact object-list row. It creates the summary shown when members are listed.

**Data flow**: It receives a database row containing the member id, email, admin flag, and seat status. It converts the id to the object name, builds a readable sentence such as email plus role plus seated state, and places key fields into a simple dictionary. The result is an `ObjectRow` ready for list-style display.

**Call relations**: `MemberObjects.list`, `member_page`, and `member_detail` call this whenever they need a display summary. It is the small formatting step between raw database data and the object framework’s list view.

*Call graph*: called by 3 (list, member_detail, member_page); 1 external calls (__init__).


##### `_detail`  (lines 289–294)

```
def _detail(row: sa.Row) -> ObjectDetail[MemberSpec]
```

**Purpose**: Turns a database member row into the detailed object data used for reading or editing a member. It captures the editable settings and the record timestamps.

**Data flow**: It receives a database row with admin status, seat status, creation time, and update time. It creates a `MemberSpec` with the admin and seated booleans, then wraps that spec with the timestamps in an `ObjectDetail`. The output is structured detail data rather than a display sentence.

**Call relations**: `MemberObjects.get` and `member_detail` call this after they have found an allowed member row. It pairs with `_row`: `_row` is for summaries, while `_detail` is for full object content.

*Call graph*: called by 2 (get, member_detail); 2 external calls (__init__, __init__).


##### `AddMember.add`  (lines 331–363)

```
async def add(self, ctx: ToolContext, args: AddMemberInput) -> ToolResult
```

**Purpose**: Adds a new person to the workspace by email before they have contacted the agent. It is meant for admins who need to invite colleagues, contractors, or advisors directly.

**Data flow**: It receives the tool context and input containing an email address, whether the new member should be an admin, and whether to notify them. It checks that the speaker is signed in, is using the main agent, and is not in a shared external audience. It normalizes and validates the email, opens a workspace transaction, locks the workspace, confirms the speaker is still an admin, checks `_absent` to prevent duplicates, and creates the member. It returns a tool result message saying the person can now speak to the agent, with an extra note if an email invitation will be sent.

**Call relations**: This is the handler wired into the `add_member` tool definition. It uses the tool context for caller checks, database transaction code for safe writes, `_absent` for duplicate protection, and `create_member` for the actual member creation. It returns `TextContent` inside a `ToolResult` so the caller gets a human-readable confirmation.

*Call graph*: calls 2 internal fn (_absent, agent_is_main); 9 external calls (__init__, __init__, __init__, select, workspace_tx, create_member, email_domain, member_is_admin, ws_current).


##### `AddMember._absent`  (lines 365–378)

```
async def _absent(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Checks that an email address is not already a member of the current workspace. It prevents accidentally adding the same person twice.

**Data flow**: It receives an open database connection and a normalized email address. It queries the current workspace for a member whose email matches case-insensitively. If none is found, it returns quietly; if one exists, it raises an error telling the caller to edit that existing member instead.

**Call relations**: `AddMember.add` calls this just before creating a member. It is deliberately used inside the same transaction as creation, so the add flow can fail early with a clear message rather than creating a duplicate.

*Call graph*: called by 1 (add); 3 external calls (execute, select, ws_current).


### `core/src/ufo/host/kinds/surface_kind.py`

`domain_logic` · `startup for registration, request handling for reads`

A “surface” is a place where chat can happen, such as a provider connection or browser-facing chat entry point. Extensions declare these surfaces in their manifests, but the core system owns the full list because it can see every installed extension and the shared workspace database. This file turns those declarations into objects that can be listed, inspected, and checked for status.

The file does two main jobs. First, `registered_surfaces` reads extension manifests at startup and builds a single map of surface names to their declarations. It rejects bad names and duplicate names early, so two extensions cannot silently claim the same address.

Second, `SurfaceObjects` answers read requests for those surfaces. It combines the static declaration from the extension with the workspace’s `surface_installation` database rows, which say whether a surface is bound to an agent and whether it routes incoming traffic. Think of the manifest as the product catalog, and the installation table as the local store’s inventory sticker.

All write operations are refused. A surface is not created or deleted through this object API; it appears because an extension declares it, and it becomes connected through that surface’s own setup flow. The file also hides surfaces from foreign audiences and limits member-facing listings to admins, because transport setup is workspace administration information.

#### Function details

##### `registered_surfaces`  (lines 54–79)

```
def registered_surfaces(manifests: tuple[Manifest, ...]) -> dict[str, RegisteredSurface]
```

**Purpose**: Builds the master list of chat surfaces declared by the active extensions. It also protects the system from confusing setup mistakes by rejecting invalid surface names and duplicate registrations.

**Data flow**: It takes a tuple of extension manifests. For each surface declaration, it checks that the surface name follows the object-name rules, then records the declaring extension and the surface’s routing traits. It returns a dictionary keyed by surface name; if a name is invalid or claimed twice, it raises an error instead of returning a partial or ambiguous result.

**Call relations**: This is the startup-style step that turns extension manifests into the `surfaces` map later stored on `SurfaceObjects`. It calls the shared object-name validator so surface names fit the same addressing rules as other objects, and it creates `RegisteredSurface` records for the read handlers to use later.

*Call graph*: 3 external calls (__init__, __init__, validate_object_name).


##### `SurfaceObjects.list`  (lines 114–117)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Returns a page of all registered surfaces for an internal tool request. It includes whether each surface is currently bound in this workspace.

**Data flow**: It reads the request context to see who is asking. If the audience is foreign, it returns an empty page. Otherwise it reads the workspace’s surface installation rows, turns the registered surfaces into display rows, applies the list query’s paging and filtering through `object_page`, and returns the page.

**Call relations**: This is the normal list path for tool callers. It asks `_installations` for database-backed binding information, passes that to `_rows` to make user-facing rows, and hands the result to `object_page` so the broader object system gets a standard paged response.

*Call graph*: calls 2 internal fn (_installations, _rows); 1 external calls (object_page).


##### `SurfaceObjects.member_page`  (lines 119–131)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Returns the admin-facing member portal list of surfaces. Non-admin members see nothing because surface transport setup is treated as administration data.

**Data flow**: It receives member identity information, an admin flag, and a list query. If the member is not an admin, it returns an empty page. If the member is an admin, it reads the workspace installation rows, builds surface rows, and returns them as a paged object list.

**Call relations**: This is the member-portal counterpart to `SurfaceObjects.list`. Like `list`, it relies on `_installations`, `_rows`, and `object_page`, but it adds the admin gate before showing anything.

*Call graph*: calls 2 internal fn (_installations, _rows); 1 external calls (object_page).


##### `SurfaceObjects.get`  (lines 133–138)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SurfaceObjectSpec] | None
```

**Purpose**: Returns the detailed declaration for one surface to an internal caller. It refuses foreign audiences and unknown surface names.

**Data flow**: It takes a request context and a surface name. If the caller is foreign, or the name is not one of the registered surfaces, it returns `None`. Otherwise it reads the workspace installation rows and builds an `ObjectDetail` showing the surface declaration plus installation timestamps when present.

**Call relations**: This is the single-object read path for tools. It uses `_installations` to learn whether the workspace has a binding, then delegates to `_detail` to shape the answer in the standard object-detail format.

*Call graph*: calls 2 internal fn (_detail, _installations).


##### `SurfaceObjects.member_detail`  (lines 140–156)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[SurfaceObjectSpec] | None
```

**Purpose**: Returns one surface for the member portal, but only for admins. The result includes both the list-style row and the full detail view.

**Data flow**: It receives the requested surface name, member identity, and an admin flag. If the member is not an admin or the surface is not registered, it returns `None`. Otherwise it reads installation data once, builds a row summary and a detailed spec, wraps both in a `MemberObject`, and returns it.

**Call relations**: This is the member-portal detail path. It combines `_row` and `_detail` so the portal can show both a compact summary and the full declaration without repeating the database lookup.

*Call graph*: calls 3 internal fn (_detail, _installations, _row); 1 external calls (__init__).


##### `SurfaceObjects.status`  (lines 158–177)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports the live binding status for one surface. This is the small status answer that says whether the workspace has connected the surface and, if so, which agent it runs as.

**Data flow**: It reads the request context and surface name. Foreign callers and unknown names receive `None`. For a known internal request, it loads the workspace installations and looks up the named surface. If no binding exists, it returns `{"bound": false}`. If a binding exists, it returns `bound`, the agent name, whether that agent is archived, and whether this binding routes incoming traffic.

**Call relations**: This is used when the object system needs current state rather than the full declaration. It only calls `_installations`, because the status response is built directly from the database-backed binding information.

*Call graph*: calls 1 internal fn (_installations).


##### `SurfaceObjects.apply`  (lines 179–188)

```
async def apply(self, ctx: ToolContext, name: str, spec: SurfaceObjectSpec, old: SurfaceObjectSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to create or update a surface object. Surfaces are declared by extensions and connected through their own setup flow, not edited through this object API.

**Data flow**: It receives the requested name, new spec, old spec, context, and expected generation value. It does not read or change stored data. It immediately raises `VerbNotSupported` with an explanation that setup belongs to the surface’s own flow.

**Call relations**: This is the write path for apply-style object changes, and it exists specifically to close that door. When the object framework tries to apply a change to a surface, this function hands back a clear refusal instead of pretending the object can be edited.

*Call graph*: 1 external calls (__init__).


##### `SurfaceObjects.delete`  (lines 190–197)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to delete a surface object. A surface disappears only when its declaring extension is removed, not through object deletion.

**Data flow**: It receives the request context, surface name, and expected generation value. It does not inspect the database or alter anything. It raises `VerbNotSupported` with a message explaining that registration belongs to the extension manifest.

**Call relations**: This is the delete path required by the object-kind interface. It protects the manifest-owned lifecycle by refusing deletion requests from the object system.

*Call graph*: 1 external calls (__init__).


##### `SurfaceObjects._rows`  (lines 199–203)

```
def _rows(self, installed: Mapping[str, _BoundInstallation]) -> tuple[ObjectRow, ...]
```

**Purpose**: Builds the list of display rows for all registered surfaces. Each row combines the extension’s declaration with this workspace’s binding state.

**Data flow**: It receives a mapping of installed surface bindings. It sorts the registered surfaces by name, calls `_row` for each one, and returns the resulting rows as a tuple.

**Call relations**: This helper sits between the public list methods and the row formatter. `SurfaceObjects.list` and `SurfaceObjects.member_page` call it after they have loaded installation data, and it delegates each individual row to `_row`.

*Call graph*: calls 1 internal fn (_row); called by 2 (list, member_page).


##### `SurfaceObjects._row`  (lines 205–228)

```
def _row(self, name: str, registered: RegisteredSurface, installed: Mapping[str, _BoundInstallation]) -> ObjectRow
```

**Purpose**: Creates one compact list row for a surface. The row says who declared it, what basic traits it has, and whether it is bound to an agent.

**Data flow**: It receives a surface name, that surface’s registered declaration, and the workspace installation map. It checks whether the surface has a binding. From that, it writes a human-readable summary such as “not bound” or “bound to agent,” marks archived agents when needed, fills structured fields like `extension`, `durable`, and `bound`, and returns an `ObjectRow`.

**Call relations**: This is the row-building worker used by `_rows` for list pages and by `member_detail` when the portal needs a single object’s summary. It turns raw declaration and installation facts into the standard row shape used by the object system.

*Call graph*: called by 2 (_rows, member_detail); 1 external calls (__init__).


##### `SurfaceObjects._detail`  (lines 230–245)

```
def _detail(self, name: str, installed: Mapping[str, _BoundInstallation]) -> ObjectDetail[SurfaceObjectSpec]
```

**Purpose**: Creates the full detail view for one surface. It shows the declared surface spec and, when installed, the binding’s creation and update timestamps.

**Data flow**: It receives a surface name and the workspace installation map. It looks up the registered declaration, checks whether there is a binding, builds a `SurfaceObjectSpec` from the declaration, and wraps it in an `ObjectDetail`. If the surface is not bound, the timestamps are `None`; if it is bound, they come from the installation row.

**Call relations**: This helper is used by `SurfaceObjects.get` and `SurfaceObjects.member_detail`. Those public methods decide whether the caller may see the surface; `_detail` focuses only on shaping the allowed answer.

*Call graph*: called by 2 (get, member_detail); 2 external calls (__init__, __init__).


##### `SurfaceObjects._installations`  (lines 247–278)

```
async def _installations(self) -> dict[str, _BoundInstallation]
```

**Purpose**: Reads this workspace’s surface bindings from the database. It is the bridge between declared surfaces and the workspace-specific setup state.

**Data flow**: It opens a workspace database transaction, selects surface installation rows for the current workspace, joins them to their agents, and reads fields such as surface name, routing flag, timestamps, agent name, and archived status. It then returns a dictionary keyed by surface name, where each value is a `_BoundInstallation` record.

**Call relations**: All read paths that need binding state call this helper: list, member list, get, member detail, and status. It uses `workspace_tx` to talk to the database, `ws_current` to know which workspace is active, and SQLAlchemy to build the query.

*Call graph*: called by 5 (get, list, member_detail, member_page, status); 4 external calls (__init__, select, workspace_tx, ws_current).


### `core/src/ufo/host/kinds/workspace_kind.py`

`domain_logic` · `request handling`

A workspace is the shared home that members belong to. This file gives the system a standard way to answer questions about that home, without allowing callers to change it here. Think of it like a building directory in a lobby: it can show who belongs and who currently has access, but it is not the place where leases or keys are issued.

The object has no user-written settings. Its “spec” is empty because all useful values are calculated from other records, especially member and seat records. When someone lists workspaces, the code returns the one current workspace, named by its workspace ID, with a short summary like “10 members, 8 seated.” When someone asks for status, it returns the counts plus a roster showing each member’s email, whether they are seated, and whether they are an admin.

There are two important privacy rules. External or foreign audiences see nothing. Internal callers may see the roster, but a child agent only sees the row for the speaking member, while the main agent can show the whole roster. Attempts to apply changes or delete the workspace are refused with clear messages, because the workspace is permanent and member access is controlled through the separate member object.

#### Function details

##### `WorkspaceObjects.list`  (lines 73–86)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Returns the current workspace as a single list row, unless the request comes from a foreign audience. The row gives a quick count of total members and seated members.

**Data flow**: It receives a tool context and a list query. If the audience is foreign, it returns an empty page. Otherwise it reads the current workspace ID, asks _shape for the latest member and seat counts, builds one row with those values, and wraps that row in a paged result shaped by the query.

**Call relations**: This is used when the object system asks to list objects of kind workspace. It relies on _shape to gather the real database-backed facts, then hands the finished row to object_page so the normal listing machinery can return it in the expected format.

*Call graph*: calls 1 internal fn (_shape); 3 external calls (__init__, object_page, ws_current).


##### `WorkspaceObjects.get`  (lines 88–98)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[WorkspaceSpec] | None
```

**Purpose**: Returns the basic detail record for the current workspace, but only when the requested name matches the current workspace ID and the audience is allowed to see it. The detail has an empty spec because there is nothing editable on the workspace object.

**Data flow**: It receives a context and a workspace name. It rejects foreign audiences and names that are not the current workspace ID. For a valid request, it reads the workspace shape, creates an empty WorkspaceSpec, and returns creation and update timestamps in an ObjectDetail.

**Call relations**: This is called when the object system needs the named workspace object itself rather than a list row or status. It calls _shape for the timestamps and counts, although the returned detail only exposes the empty spec and timestamps.

*Call graph*: calls 1 internal fn (_shape); 3 external calls (__init__, __init__, ws_current).


##### `WorkspaceObjects.status`  (lines 100–121)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns the live status for the workspace: member count, seated count, and a roster of members with seat and admin information. It also enforces who may see the full roster.

**Data flow**: It receives a context, a workspace name, and an optional expected generation value. It returns nothing for foreign audiences or the wrong workspace name. For an allowed request, it reads the current shape, checks whether the speaker is a member talking to the main agent, and then returns counts plus either the whole roster or just the speaker’s own roster entry.

**Call relations**: This is used when a caller asks what is currently true about the workspace. It depends on _shape for the roster snapshot and on ToolContext.agent_is_main to decide whether the caller gets the full directory or only their own row.

*Call graph*: calls 2 internal fn (_shape, agent_is_main); 1 external calls (ws_current).


##### `WorkspaceObjects.apply`  (lines 123–132)

```
async def apply(self, ctx: ToolContext, name: str, spec: WorkspaceSpec, old: WorkspaceSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create or update the workspace object. This protects the rule that workspace facts here are read-only and that seats must be changed through member objects.

**Data flow**: It receives the requested name, new spec, old spec, context, and optional generation check, but it does not use them to change anything. Instead it raises a VerbNotSupported error with a message explaining that seating and unseating belong to the member kind.

**Call relations**: This is reached when the object framework tries to apply a change to a workspace. Rather than handing off to storage or seat code, it stops the flow immediately and tells the caller which object kind should be used instead.

*Call graph*: 1 external calls (__init__).


##### `WorkspaceObjects.delete`  (lines 134–141)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to delete the workspace object. The workspace is treated as permanent once created.

**Data flow**: It receives the context, workspace name, and optional generation check, but performs no lookup and changes no data. It raises a VerbNotSupported error saying the workspace cannot be deleted.

**Call relations**: This is called by the object framework when someone tries to delete a workspace object. It deliberately does not call _shape or database code, because deletion is never allowed for this kind.

*Call graph*: 1 external calls (__init__).


##### `WorkspaceObjects._shape`  (lines 143–161)

```
async def _shape(self) -> WorkspaceShape
```

**Purpose**: Collects the real facts behind the workspace object in one place: timestamps, member count, seated count, and roster. The public read methods use this so they all speak from the same snapshot of information.

**Data flow**: It reads the current workspace ID, opens a workspace database transaction, fetches the workspace row’s creation and update times, and asks Seats for a snapshot of members and seat state. It then packages those values into a WorkspaceShape object and returns it.

**Call relations**: This is the shared helper behind list, get, and status. Those public methods decide what the caller is allowed to see, while _shape does the common work of reading the database and seat snapshot needed to answer them.

*Call graph*: called by 3 (get, list, status); 5 external calls (__init__, __init__, select, workspace_tx, ws_current).


### `core/src/ufo/runtime/kinds/agents.py`

`domain_logic` · `request handling`

An agent in this system is not just code that answers messages. It is also a workspace record with a name, prompt, model choice, internet policy, visibility in the portal, icon, and optional input/output rules for spawned runs. This file is the rulebook for that record.

The main job is to make agent changes safe and predictable. Creating an agent requires a speaking member and a non-empty prompt. Updates keep omitted fields unchanged, so a small edit does not accidentally erase the prompt or icon. The file also checks that the requested model is known and that the chosen reasoning setting is allowed for that model.

Deleting an agent does not truly erase it. It archives the row, gives it a durable hidden name like a box label in storage, and frees the old public name for reuse. This matters because conversations, grants, scheduled tasks, and spending history still need to point to the same agent record. A separate restore action can bring the archived app back under an available name. The main agent is special: every member can reach it, its visibility must stay workspace-wide, and it cannot be archived.

#### Function details

##### `_effective_model`  (lines 70–75)

```
def _effective_model(ctx: ToolContext, stored: str) -> str
```

**Purpose**: Reports the real model an agent is running. If the stored value is the special “auto” choice, it shows the concrete model selected for the current turn instead of exposing the placeholder.

**Data flow**: It receives the current tool context and the model value stored in the database. If the stored value is “auto”, it reads the already-resolved model from the current agent context; otherwise it returns the stored value unchanged.

**Call relations**: Status and list summaries call this helper before showing model information to a member, so readers see a useful model name without changing what is stored for future runs.

*Call graph*: called by 2 (_status, _agent_summary).


##### `AgentSpec._declared_schema`  (lines 166–171)

```
def _declared_schema(cls, value: dict[str, JsonValue] | None, info: ValidationInfo) -> dict[str, JsonValue] | None
```

**Purpose**: Checks that an agent’s optional input or output contract is a valid declared schema. A schema here means a JSON-shaped rule describing what data a spawned agent accepts or returns.

**Data flow**: It receives a schema value from the submitted agent specification. If the value is present, it passes it to the shared schema checker; if the checker accepts it, the same value continues through validation.

**Call relations**: Pydantic, the data validation library used for request models, calls this validator while building an AgentSpec. It hands the real schema rules to check_declared_schema so bad contracts are rejected before they reach the database.

*Call graph*: 1 external calls (check_declared_schema).


##### `_known_model`  (lines 174–191)

```
def _known_model(ctx: ToolContext, model: str, reasoning: ReasoningEffort) -> None
```

**Purpose**: Stops an agent from being saved with a model setting that this deployment cannot run. It also catches an invalid reasoning choice for models that require reasoning to stay on.

**Data flow**: It receives the tool context, a requested model name, and a reasoning setting. It resolves “auto” to the deployment’s current automatic model, checks the model registry, and raises an error if the model or reasoning combination is not allowed.

**Call relations**: Agent creation and update both call this before writing to the database. That makes the write path the place where the user gets a clear error, instead of leaving a broken agent that would fail on every later turn.

*Call graph*: called by 2 (_create, _mutate).


##### `_agent_summary`  (lines 194–199)

```
def _agent_summary(ctx: ToolContext, row: sa.Row) -> str
```

**Purpose**: Builds a short human-readable summary for an agent in object listings. It tells whether the agent is archived, whether it is the main agent, which model it uses, and whether public internet is allowed.

**Data flow**: It receives the current context and one database row. It first converts any “auto” model into the effective model name, then formats a brief sentence based on the row’s archived state and internet setting.

**Call relations**: AgentObjects._owned_rows uses this while preparing the list of agents visible through the object system. It relies on _effective_model so the summary shows the concrete model when appropriate.

*Call graph*: calls 1 internal fn (_effective_model); called by 1 (_owned_rows).


##### `AgentObjects._admin_can_apply`  (lines 214–215)

```
def _admin_can_apply(self, old: AgentSpec, spec: AgentSpec) -> bool
```

**Purpose**: Says that workspace admins are allowed to apply any valid agent specification change. The detailed safety checks still happen elsewhere.

**Data flow**: It receives the old and new agent specifications and returns true without changing anything. The result tells the shared ownership layer that admin edits are permitted.

**Call relations**: This method plugs into the generic member-owned object rules inherited by AgentObjects. When the object framework is deciding whether an admin may edit an agent, this method gives that approval.


##### `AgentObjects.list`  (lines 217–223)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists workspace agents, showing live agents by default. Archived agents are hidden unless the caller explicitly asks for an archived filter.

**Data flow**: It receives a list query. If the query does not mention archived status, it adds a filter for non-archived agents, then passes the updated query to the shared listing behavior.

**Call relations**: The object system calls this when someone lists agent objects. It lightly adjusts the query, then hands the real listing work to the parent MemberOwnedObjects implementation.

*Call graph*: 1 external calls (replace).


##### `AgentObjects.get`  (lines 225–242)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[AgentSpec] | None
```

**Purpose**: Fetches one agent’s object details, with a shortcut for “this turn’s own agent.” An empty name means the caller wants the agent currently running the turn.

**Data flow**: It receives a context and a name. If the name is not empty, it delegates normal lookup to the parent class. If the name is empty, it finds the row whose id matches the current turn’s agent id, fetches that named agent, and returns the detail with the real name filled in.

**Call relations**: The object framework calls this for object_get. It calls _owned_rows to find the current agent by id, then uses the inherited get flow so the usual permission and detail-building rules still apply.

*Call graph*: calls 1 internal fn (_owned_rows); 1 external calls (replace).


##### `AgentObjects._owned_rows`  (lines 244–276)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[ObjectOwner], ...]
```

**Purpose**: Builds the internal list of agent rows used for permission-aware listing and lookup. Each row is labeled with its owner, sharing status, summary, and archive metadata.

**Data flow**: It reads all agent records for the current workspace from the database. For each record, it creates an OwnedRow containing the public name, a short summary, owner information, and fields such as id and archived timestamp.

**Call relations**: AgentObjects.get uses this when resolving an empty name to the current turn’s agent. The shared object machinery also depends on this kind of owned-row view to decide what the caller may see or change.

*Call graph*: calls 1 internal fn (_agent_summary); called by 1 (get); 5 external calls (__init__, __init__, select, workspace_tx, ws_current).


##### `AgentObjects._detail`  (lines 278–311)

```
async def _detail(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> ObjectDetail[AgentSpec] | None
```

**Purpose**: Turns one stored agent row into the full object detail returned to callers. This includes the editable specification, timestamps, and a link from non-main agents back to the main agent.

**Data flow**: It receives a name and owner information, looks up the matching row, and returns nothing if no row exists. If found, it copies the row’s configuration into an AgentSpec and wraps it in an ObjectDetail with creation and update times.

**Call relations**: The inherited object get flow calls this after access checks. It calls _row for the database lookup, then constructs the detail object that the outside object API returns.

*Call graph*: calls 1 internal fn (_row); 4 external calls (__init__, __init__, __init__, __init__).


##### `AgentObjects._status`  (lines 313–332)

```
async def _status(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns machine-readable status facts about an agent. This is separate from the editable spec and includes things like whether it is main, archived, ownerless, or provisioned.

**Data flow**: It receives a context, name, and owner information. It looks up the row, converts the model to the effective model when needed, and returns a dictionary of status fields, or nothing if the agent does not exist.

**Call relations**: The object framework uses this when status information is requested for an agent. It calls _row for the stored record and _effective_model so “auto” is reported as the concrete model for the current context.

*Call graph*: calls 2 internal fn (_row, _effective_model).


##### `AgentObjects._apply_owned`  (lines 334–345)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: AgentSpec, old: AgentSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: Chooses whether an apply request means “create a new agent” or “update an existing one.” It is the fork in the write path after ownership rules have allowed the operation.

**Data flow**: It receives the target name, submitted specification, previous specification if any, and owner information. If there was no previous spec, it sends the request to creation; otherwise it sends it to mutation.

**Call relations**: The shared member-owned object layer calls this once it has decided the caller may apply a change. This method then hands off to _create or _mutate for the actual database write.

*Call graph*: calls 2 internal fn (_create, _mutate).


##### `AgentObjects._mutate`  (lines 347–425)

```
async def _mutate(self, ctx: ToolContext, name: str, spec: AgentSpec) -> None
```

**Purpose**: Updates an existing agent’s settings without accidentally clearing fields the caller omitted. It also protects special rules, such as keeping the main agent visible to the whole workspace.

**Data flow**: It receives a context, agent name, and new specification. It loads the current row, validates the model, fills in omitted fields from the old row, rejects an empty prompt, detects whether anything actually changed, and writes the updated values and timestamp to the database.

**Call relations**: AgentObjects._apply_owned calls this for edits to existing agents. It depends on _row to fetch the current state and _known_model to reject unusable model settings before committing the update.

*Call graph*: calls 2 internal fn (_row, _known_model); called by 1 (_apply_owned); 4 external calls (__init__, update, workspace_tx, ws_current).


##### `AgentObjects._create`  (lines 427–473)

```
async def _create(self, ctx: ToolContext, name: str, spec: AgentSpec) -> None
```

**Purpose**: Creates a new non-main agent owned by the member who asked for it. It deliberately copies only the submitted configuration, not grants, credentials, sources, or old derived data.

**Data flow**: It receives a context, new name, and specification. It requires a speaking member, requires a non-empty prompt, validates the model, chooses an icon if needed, and inserts a new agent row into the current workspace. If the name is already taken, it reports that clearly.

**Call relations**: AgentObjects._apply_owned calls this when an apply request targets a name with no existing agent. It uses _known_model before the insert and relies on the database uniqueness rule to settle competing attempts to create the same name.

*Call graph*: calls 1 internal fn (_known_model); called by 1 (_apply_owned); 7 external calls (__init__, insert, select, workspace_tx, ws_current, auto_agent_icon, uuid4).


##### `AgentObjects.delete`  (lines 475–485)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Starts deletion of an agent, but blocks deletion of the main agent. In this system, deleting an agent means archiving it rather than erasing its history.

**Data flow**: It receives the context, name, and optional expected generation value. It looks up the row; if the row is the main agent, it raises an unsupported-action error. Otherwise it delegates to the parent delete flow.

**Call relations**: The object system calls this for delete requests. It performs the main-agent guard first, then lets the shared ownership layer continue toward _delete_owned if the caller is allowed.

*Call graph*: calls 1 internal fn (_row); 1 external calls (__init__).


##### `AgentObjects._delete_owned`  (lines 487–517)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: Archives an agent after ownership checks have passed. Archiving makes the agent stop accepting new turns, removes it from normal listings, and frees its old name while keeping its history attached to the same row.

**Data flow**: It receives the context, name, and owner information. It loads the row, rejects missing, main, or already archived agents, then updates the database row with a hidden archived name, the old name as archived_name, an archive timestamp, and a fresh update timestamp.

**Call relations**: The shared delete flow calls this once the caller has passed the owner-or-admin gate. It uses _row to find the target and writes the archive state directly to the agent table.

*Call graph*: calls 1 internal fn (_row); 5 external calls (__init__, __init__, update, workspace_tx, ws_current).


##### `AgentObjects._row`  (lines 519–560)

```
async def _row(self, name: str) -> sa.Row | None
```

**Purpose**: Fetches the full database row for one agent name in the current workspace. It is the common lookup used by detail, status, edit, and archive operations.

**Data flow**: It receives an agent name. It queries the current workspace’s agent table for that name, also including the main agent’s name as a related value, and returns either the single matching row or nothing.

**Call relations**: Many AgentObjects methods call this before acting on an agent. By keeping the lookup in one place, detail, status, mutation, delete checks, and archive behavior all read the same set of stored fields.

*Call graph*: called by 5 (_delete_owned, _detail, _mutate, _status, delete); 3 external calls (select, workspace_tx, ws_current).


##### `RestoreApplication.restore`  (lines 575–632)

```
async def restore(self, ctx: ToolContext, args: RestoreApplicationInput) -> ToolResult
```

**Purpose**: Brings an archived agent back to life under a requested name. Only the original owner or a workspace admin can do this.

**Data flow**: It receives the tool context and a new name argument. It checks that the action is bound to an archived agent target, requires a speaking member, validates the new name, extracts the archived agent id from the hidden name, verifies ownership or admin status, and updates the row to clear its archived fields. It returns a tool result telling the user the app is live again.

**Call relations**: This is the handler for the restore_application tool bound to archived agent objects. It reads the target from the tool context, checks admin status through the context when needed, and writes the restored row back to the database unless it was already effectively restored under that name.

*Call graph*: calls 1 internal fn (speaker_is_admin); 10 external calls (__init__, __init__, __init__, __init__, select, update, workspace_tx, validate_object_name, ws_current, UUID).


### Knowledge and memory objects
Knowledge-oriented extensions expose synced gbrain sources and memory records or profiles as workspace objects with appropriate read, share, sync, and delete permissions.

### `extensions/gbrain/ufo_ext_gbrain/objects.py`

`domain_logic` · `object request handling and source sync control`

A gbrain source is one place where Markdown pages come from. Think of it like registering a library shelf: once the shelf is registered, the system knows where to look for documents and who is allowed to read or change that registration. This file sets the rules for those registrations.

The most important rule is identity. A source’s name is not chosen freely. It is calculated from the origin: the repository, branch, or local directory root. That means the same GitHub repo and branch always become the same object name, while changing the repo or branch creates a different source. This prevents two names from secretly pointing at the same content.

The file also enforces safety rules. GitHub sources can be applied by users. Local directory roots cannot, because they read the server’s own filesystem and must come from operator configuration at boot. Sources are private to the registering member unless explicitly shared. A private source can be made shared by its registrar, but a shared source cannot be made private again except by deleting and recreating it.

The GbrainObjects class connects these rules to the broader object verbs: apply, list, get, status, resync, and delete. It talks to the extension context, which is the bridge to the stored source rows and sync scheduler.

#### Function details

##### `gbrain_source_name`  (lines 57–64)

```
def gbrain_source_name(repo: str | None, branch: str | None, root: str | None) -> str
```

**Purpose**: Builds the official object name for a gbrain source from its origin. This keeps naming consistent so the same repository, branch, or directory always maps to the same name.

**Data flow**: It receives a repository name, branch name, and directory root, any of which may be absent depending on the source type. It turns those values into a stable JSON string, hashes that string, keeps the first short piece of the hash, and returns a name like `gbrain-1234abcd`.

**Call relations**: This is the shared naming rule used when creating an origin in `_origin` and when a stored source reports its `_Registered.name`. Other code relies on this so users cannot invent conflicting names for the same source.

*Call graph*: called by 2 (name, _origin); 2 external calls (sha256, dumps).


##### `GbrainSpec.validate_origin`  (lines 97–102)

```
def validate_origin(self) -> 'GbrainSpec'
```

**Purpose**: Checks that a requested source describes exactly one origin. A source must be either a GitHub repository or a local directory root, not both and not neither.

**Data flow**: It reads the fields of a `GbrainSpec`. If both `repo` and `root` are set, or both are missing, it rejects the spec. If a branch is given without a repository, it also rejects the spec. If the fields make sense, it returns the same spec unchanged.

**Call relations**: This validation runs as part of building or accepting a `GbrainSpec`. It protects later functions such as `_origin` and `GbrainObjects._apply_owned` from receiving unclear source descriptions.


##### `_origin`  (lines 112–126)

```
def _origin(spec: GbrainSpec) -> _Origin
```

**Purpose**: Turns a user-facing gbrain spec into the internal source origin that the sync system understands. It decides whether the source is a GitHub source or a local-folder source and calculates the required object name.

**Data flow**: It receives a `GbrainSpec`. If the spec has a `root`, it creates a folder configuration and derives a name from that root. If it has a `repo`, it creates a Git configuration using the repo and optional branch, then derives a name from those values. It returns a small `_Origin` record containing the backend type, backend config, and official name.

**Call relations**: It is used by `GbrainObjects.apply` to check for existing private registrations of the same origin, and by `GbrainObjects._apply_owned` when actually registering or updating a source.

*Call graph*: calls 1 internal fn (gbrain_source_name); called by 2 (_apply_owned, apply); 3 external calls (__init__, __init__, __init__).


##### `_identity`  (lines 129–130)

```
def _identity(spec: GbrainSpec) -> tuple[str | None, str | None, str | None, bool]
```

**Purpose**: Extracts the parts of a spec that define whether it is the same source request as another one. This is used to tell a harmless re-apply apart from a real change.

**Data flow**: It receives a `GbrainSpec` and returns a tuple containing its repository, branch, root, and shared flag. Nothing is changed; the function just packages the comparison fields together.

**Call relations**: It is used by `GbrainObjects.apply` to detect an identical re-apply and by `GbrainObjects._resync` to ensure a resync request is not secretly changing the source at the same time.

*Call graph*: called by 2 (_resync, apply).


##### `_Registered.name`  (lines 147–148)

```
def name(self) -> str
```

**Purpose**: Returns the official object name for a source that is already stored. This makes stored rows follow the same naming rule as newly requested sources.

**Data flow**: It reads the registered source’s repository, branch, and root values. It passes them to `gbrain_source_name` and returns the resulting `gbrain-...` name.

**Call relations**: This property is used whenever registered sources are compared or listed, especially through `_registered_named` and `_member_rows`. It ties database rows back to the public object names users see.

*Call graph*: calls 1 internal fn (gbrain_source_name).


##### `_Registered.spec`  (lines 150–156)

```
def spec(self) -> GbrainSpec
```

**Purpose**: Converts a stored source row back into the public spec shape shown to users. This lets a get operation describe the source in the same language used to apply it.

**Data flow**: It reads the stored repository, branch, root, and subject. It converts the subject into a simple `shared` true-or-false value and returns a `GbrainSpec`. The resync flag is not stored state, so it stays at its default false value.

**Call relations**: It is used by object-detail lookup so `GbrainObjects._member_object` can return a clean `GbrainSpec` for an existing registered source.

*Call graph*: 2 external calls (__init__, subject_shared).


##### `_Registered.summary`  (lines 158–162)

```
def summary(self) -> str
```

**Purpose**: Creates a short human-readable description of a registered source. This is the quick label shown in lists, such as “github repository owner/name” or “server directory /path”.

**Data flow**: It checks whether the registered source is a local directory or a repository. It builds a short text summary, including the branch when present, and trims it to the maximum summary length.

**Call relations**: This is used when listing member-visible rows in `GbrainObjects._member_rows` and in one refusal message inside `GbrainObjects.apply` when another member already privately registered the same origin.


##### `_require_ext`  (lines 165–168)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Makes sure the object code has an extension context, which is the connection to source storage and sync actions. Without it, this file cannot read, register, resync, grant, or remove sources.

**Data flow**: It receives an optional extension context. If the context is present, it returns it. If it is missing, it raises a runtime error because this object kind was called without the dependency it needs.

**Call relations**: Many functions call this before touching source records, including `_registered_named`, `_member_rows`, `_grant_settled`, `_resync`, `_apply_owned`, and `_delete_owned`. It acts like a guardrail at the boundary between object logic and extension storage.

*Call graph*: called by 6 (_apply_owned, _delete_owned, _grant_settled, _member_rows, _resync, _registered_named).


##### `_registered_from_ext`  (lines 171–196)

```
async def _registered_from_ext(ext: ExtensionContext) -> tuple[_Registered, ...]
```

**Purpose**: Reads all stored gbrain-related source rows from the extension context and converts them into `_Registered` records. It ignores source rows belonging to other backends.

**Data flow**: It asks the extension context for all sources. For each source, it checks whether the backend is the gbrain Git backend or the gbrain folder backend. It validates the backend-specific config, extracts the origin fields, and returns a tuple of normalized `_Registered` records.

**Call relations**: This is the main reader for stored source state. `GbrainObjects._member_rows` uses it to list sources, and `_registered_named` uses it to find one source by its derived name.

*Call graph*: calls 1 internal fn (sources); called by 2 (_member_rows, _registered_named); 3 external calls (__init__, model_validate, model_validate).


##### `_registered_named`  (lines 199–203)

```
async def _registered_named(ext: ExtensionContext | None, name: str) -> _Registered | None
```

**Purpose**: Finds one registered gbrain source by its object name. It is the common lookup helper for apply, get, status, resync, grant, and delete paths.

**Data flow**: It receives an optional extension context and a name. It first requires a real extension context, then reads all registered gbrain sources and returns the first one whose derived name matches. If none match, it returns nothing.

**Call relations**: This function is called throughout `GbrainObjects`: before applying changes, granting an existing source to an agent, resyncing, showing details or status, and deleting. It keeps all those paths using the same lookup behavior.

*Call graph*: calls 2 internal fn (_registered_from_ext, _require_ext); called by 7 (_apply_owned, _delete_owned, _grant_settled, _member_object, _resync, _status, apply).


##### `GbrainObjects.apply`  (lines 221–252)

```
async def apply(self, ctx: ToolContext, name: str, spec: GbrainSpec, old: GbrainSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Coordinates an apply request for a gbrain source before handing normal changes to the base object system. It separates three cases: resyncing, re-applying the exact same source, and registering or changing a source.

**Data flow**: It receives the tool context, requested object name, requested spec, any old visible spec, and an expected generation. If `resync` is true, it sends the request to `_resync`. If the user re-applies the identical visible spec, it calls `_grant_settled` so the current agent is allowed to use the already-settled source. Otherwise, it checks for a private source already registered by someone else, then delegates the real apply operation to the parent class.

**Call relations**: This is the public apply entry for the object kind. It calls `_resync`, `_grant_settled`, `_identity`, `_origin`, and `_registered_named` to enforce gbrain-specific rules before the inherited object flow calls `_apply_owned` for the actual registration work.

*Call graph*: calls 6 internal fn (speaker_is_admin, _grant_settled, _resync, _identity, _origin, _registered_named); 2 external calls (__init__, subject_shared).


##### `GbrainObjects._grant_settled`  (lines 254–269)

```
async def _grant_settled(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: Grants the current agent access to a source when the user re-applies an identical source that already exists. This avoids a confusing situation where the apply appears successful but the agent still has no feed to read from.

**Data flow**: It reads the speaking member, object owner, and registered source. If there is no speaker, no owner, or the speaker is not allowed to use the source, it does nothing. If everything checks out, it asks the extension context to grant that source to the current turn’s agent.

**Call relations**: It is called only by `GbrainObjects.apply` for identical re-applies. It hands off to the extension context’s grant operation after using `_registered_named` and `_require_ext` to find the stored source safely.

*Call graph*: calls 2 internal fn (_registered_named, _require_ext); called by 1 (apply).


##### `GbrainObjects._resync`  (lines 271–291)

```
async def _resync(self, ctx: ToolContext, name: str, spec: GbrainSpec, old: GbrainSpec | None) -> None
```

**Purpose**: Schedules an immediate sync of an existing source without changing its settings. This is useful when a user wants the system to fetch the latest Markdown right now instead of waiting for the next scheduled poll.

**Data flow**: It receives the context, name, requested spec, and old visible spec. It first checks that the request matches the current source exactly apart from the `resync` action. It then checks that the source exists and that the acting member is either the owner or an admin. If allowed, it asks the extension context to schedule a sync for that source ID.

**Call relations**: It is called from `GbrainObjects.apply` when the spec has `resync: true`. It uses `_identity`, ownership checks inherited from the base class, `_registered_named`, and `_require_ext`, then hands the final scheduling work to the extension context.

*Call graph*: calls 4 internal fn (speaker_is_admin, _identity, _registered_named, _require_ext); called by 1 (apply); 3 external calls (__init__, __init__, __init__).


##### `GbrainObjects._member_rows`  (lines 293–306)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[ObjectOwner], ...]
```

**Purpose**: Builds the list entries for gbrain sources visible through the object system. Each row includes the object name, a short summary, and ownership information.

**Data flow**: It receives an extension context and an optional member ID. It reads all registered gbrain sources, converts each one into an `OwnedRow`, marks whether it is shared, and includes the owner member ID when present. It returns all rows as a tuple.

**Call relations**: This method is used by the inherited member-readable object machinery when listing objects. It depends on `_registered_from_ext` for source data and `_require_ext` to ensure storage access is available.

*Call graph*: calls 2 internal fn (_registered_from_ext, _require_ext); 3 external calls (__init__, __init__, subject_shared).


##### `GbrainObjects._member_object`  (lines 308–323)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: ObjectOwner, *, member_id: UUID | None) -> ObjectDetail[GbrainSpec] | None
```

**Purpose**: Builds the detailed object view for one registered gbrain source. This is what lets a get-style request read back the source spec and timestamps.

**Data flow**: It receives an extension context, name, owner information, and optional member ID. It looks up the registered source by name. If found, it converts it to a `GbrainSpec` and packages that with created and updated timestamps; if not found, it returns nothing.

**Call relations**: This method is called by the inherited object lookup flow after visibility has been considered. It uses `_registered_named` and then relies on `_Registered.spec` to present stored source data in public spec form.

*Call graph*: calls 1 internal fn (_registered_named); 1 external calls (__init__).


##### `GbrainObjects._status`  (lines 325–339)

```
async def _status(self, ctx: ToolContext, name: str, _owner: ObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns live status information for a registered source, such as when it will sync next and whether recent syncs have failed. This gives users operational feedback beyond the static spec.

**Data flow**: It receives the tool context, source name, and owner. It looks up the registered source. If found, it returns a dictionary with shared status, next sync time as text, and consecutive error count; for private sources with a known owner, it also includes the owner member ID.

**Call relations**: This method is used by the object status flow. It calls `_registered_named` to find the current source row and uses the subject value to decide whether owner information should be shown.

*Call graph*: calls 1 internal fn (_registered_named); 1 external calls (subject_shared).


##### `GbrainObjects._apply_owned`  (lines 341–379)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: GbrainSpec, old: GbrainSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: Performs the actual registration or allowed update once the base object system has decided the caller may mutate this source. It registers new GitHub sources, shares existing private sources when allowed, and grants the source to the current agent.

**Data flow**: It receives the context, name, requested spec, old spec, and owner. It requires a speaking member, rejects local directory roots because those must come from operator config, and checks that the requested name matches the derived origin name. If the source is new, it registers it with either a shared subject or a member-private subject. If it already exists, it rejects attempts to make a shared source private, optionally flips a private source to shared, and grants the source to the current agent.

**Call relations**: This is reached through the base class after `GbrainObjects.apply` has done gbrain-specific prechecks. It uses `_origin`, `_registered_named`, and `_require_ext`, then hands storage changes to extension context methods such as registering the source, changing its subject, and granting access.

*Call graph*: calls 3 internal fn (_origin, _registered_named, _require_ext); 4 external calls (__init__, __init__, member_subject, subject_shared).


##### `GbrainObjects._delete_owned`  (lines 381–385)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: Deletes a registered gbrain source after permission checks have already passed. Removing the source also lets the broader sync and page-tombstone pipeline clean up the pages that came from it.

**Data flow**: It receives the context, source name, and owner. It looks up the registered source by name. If the source no longer exists, it raises an unknown-object error. If it exists, it asks the extension context to remove that source by ID.

**Call relations**: This method is called by the inherited delete flow once ownership or admin authority has been checked. It relies on `_registered_named` to find the row and `_require_ext` to reach the extension context that performs the removal.

*Call graph*: calls 2 internal fn (_registered_named, _require_ext); 1 external calls (__init__).


### `extensions/memory/ufo_ext_memory/objects.py`

`domain_logic` · `request handling`

This file is the doorway between the memory database and the system's object API. In human terms, it lets a caller say, "show me the memories I am allowed to see" or "open this memory by its id," and it turns database rows into neat object pages and object details. It defines two object kinds. A `memory` is a stored piece of remembered text, with information about who can see it, what kind of memory it is, when it was written, and links back to the page it came from or the newer memory that replaced it. A `profile` is a short People-band entry for a workspace member: their role and current focus. The important rule throughout the file is visibility. Memories are only shown if their subject, meaning their audience label, matches what the reader is allowed to read. If a memory came from a page, the page must still be readable at the same revision too. Profiles are simpler: because they are built only from workspace-shared facts, a reader either has the shared workspace subject and can see profiles, or they cannot see any. This file also makes both object kinds read-only. New memories come through `memory_update`, old memories are superseded rather than deleted, and profiles are written by the People pass, not by direct object edits.

#### Function details

##### `_require_ext`  (lines 91–94)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Makes sure the memory object code was given its extension context, which is the bundle of database access and workspace information it needs. If that context is missing, it fails loudly instead of doing a misleading partial read.

**Data flow**: It receives an optional extension context. If the value is present, it returns it unchanged; if it is missing, it raises an error explaining that memory objects were dispatched without the needed context.

**Call relations**: The public memory and profile methods call this before doing real work. It acts like checking that you have the right key before trying to open the filing cabinet.

*Call graph*: called by 8 (get, list, member_detail, member_page, get, list, member_detail, member_page).


##### `_stamp`  (lines 97–101)

```
def _stamp(written: datetime) -> str
```

**Purpose**: Turns a stored date and time into one consistent text format for object fields. This matters because different databases can store timezone information differently.

**Data flow**: It receives a `datetime` value, first normalizes it through `_aware` so it has a clear timezone meaning, then returns an ISO-8601 string, the common internet-style spelling for dates and times.

**Call relations**: _row and `_profile_row` use this when building list rows. It keeps memory rows and profile rows from exposing database-specific time quirks to callers.

*Call graph*: called by 2 (_profile_row, _row); 1 external calls (_aware).


##### `_row`  (lines 104–128)

```
def _row(name: str, body: str, subject: str, item_class: str, memory_kind: str, written: datetime | None, page_id: UUID | None, pages: Mapping[UUID, PageState]) -> ObjectRow
```

**Purpose**: Builds one lightweight list row for a memory item. It gives callers a readable summary, useful fields, and page-source information when available.

**Data flow**: It receives the memory id, body text, visibility subject, classification fields, write time, optional source page id, and known page states. It clips long text to safe display lengths, stamps the write time, looks up the source page title and stream if available, and returns an `ObjectRow`.

**Call relations**: Memory listing uses this for each visible memory, and member detail uses it to pair a full memory with the same row shape shown in lists. It hands off time formatting to `_stamp` and text shortening to `clip_to_word`.

*Call graph*: calls 1 internal fn (_stamp); called by 2 (_page, member_detail); 2 external calls (__init__, clip_to_word).


##### `_member_reader`  (lines 131–139)

```
def _member_reader(member_id: UUID) -> SourceReader
```

**Purpose**: Builds the reading identity for a signed-in member when they view memory outside an active conversation turn. It decides what audiences that member can read as.

**Data flow**: It receives a member id. It reads the current agent, builds that member's conversation audience, converts that audience into subject labels, and returns a `SourceReader` containing the agent id, member id, and readable subjects.

**Call relations**: The member-facing memory page and detail methods use this before reading memory. It gives those methods the same visibility rules they would have during a live turn.

*Call graph*: called by 2 (member_detail, member_page); 4 external calls (__init__, audience_subjects, conversation_audience, agent_current).


##### `MemoryObjects.list`  (lines 148–149)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists live memory items visible to the current tool caller. This is the normal object-list path for memories inside a running tool context.

**Data flow**: It receives a tool context and list query. It pulls the extension context and the caller's source reader from the tool context, then asks `_page` to fetch and format the matching memories into an object page.

**Call relations**: This is a public entry into the memory object store. It mainly prepares the right context, then delegates the database and visibility work to `MemoryObjects._page`.

*Call graph*: calls 3 internal fn (source_reader, _page, _require_ext).


##### `MemoryObjects.member_page`  (lines 151–164)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists the memories a particular signed-in member can see from the portal or member view, outside a live tool turn. Admin status does not widen private memory access here.

**Data flow**: It receives an extension context, member id, admin flag, and list query. It creates a reader for that member's own audience, checks the extension context, and passes everything to `_page`, which returns the visible memory page.

**Call relations**: This is the member-facing counterpart to `MemoryObjects.list`. It uses `_member_reader` to recreate the member's reading audience, then relies on `_page` for the actual listing rules.

*Call graph*: calls 3 internal fn (_page, _member_reader, _require_ext).


##### `MemoryObjects.member_detail`  (lines 166–200)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[MemorySpec] | None
```

**Purpose**: Opens one memory item for a signed-in member and returns both the full detail and the row-style summary used in member pages. It can open superseded memories too, so old references still lead somewhere useful.

**Data flow**: It receives an extension context, memory name, member id, and admin flag. It builds the member reader, asks `_item` for the full memory detail, extracts any source-page link, loads readable page state for that page, and returns a `MemberObject` containing a row plus the detail. If the memory is not visible or not found, it returns nothing.

**Call relations**: This method sits between the portal-style member view and the lower-level memory lookup. It calls `_item` to enforce memory visibility, then `_row` to make the detail look consistent with list results.

*Call graph*: calls 4 internal fn (_item, _member_reader, _require_ext, _row); 2 external calls (__init__, UUID).


##### `MemoryObjects.get`  (lines 202–203)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[MemorySpec] | None
```

**Purpose**: Opens one memory item by id for the current tool caller. This is how a reference returned by memory search becomes the full stored memory text and metadata.

**Data flow**: It receives a tool context and object name. It gets the extension context and caller's reader, then asks `_item` to load that memory if it exists and the caller is allowed to read it.

**Call relations**: This is the direct object-get path for memories. It does little itself so that `MemoryObjects._item` can keep all single-memory visibility checks in one place.

*Call graph*: calls 3 internal fn (source_reader, _item, _require_ext).


##### `MemoryObjects._page`  (lines 205–262)

```
async def _page(self, ext: ExtensionContext, reader: SourceReader, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Fetches and formats a page of live, visible memory rows. It filters out superseded or retired memories and double-checks page-derived memories against the current readable page state.

**Data flow**: It receives the extension context, a reader, and a list query. It reads the reader's allowed subjects, queries the database for recent non-superseded, non-retired memories in those subjects, loads the source page states for page-derived memories, drops any memory whose source page is no longer readable in the right form, converts the rest to rows with `_row`, and wraps them in an object page.

**Call relations**: `MemoryObjects.list` and `MemoryObjects.member_page` both call this. It is the main list engine for memories, combining database filtering, page-read checks, row formatting, and final paging.

*Call graph*: calls 3 internal fn (readable_page_states, transaction, _row); called by 2 (list, member_page); 2 external calls (select, object_page).


##### `MemoryObjects._item`  (lines 264–330)

```
async def _item(self, ext: ExtensionContext, reader: SourceReader, name: str) -> ObjectDetail[MemorySpec] | None
```

**Purpose**: Loads one memory item by UUID-like name and checks whether the reader may see it. It also attaches links to the source page and to a replacement memory when the item has been superseded.

**Data flow**: It receives the extension context, reader, and memory name. It first parses the name as a UUID; invalid names return nothing. It queries the memory table for a row in the same workspace and in one of the reader's subjects. If the memory came from a page, it verifies the page is still readable, has the same subject, and is at the recorded revision. It then builds a `MemorySpec`, timestamps, and any object links, and returns an `ObjectDetail`; otherwise it returns nothing.

**Call relations**: `MemoryObjects.get` and `MemoryObjects.member_detail` both rely on this for single-memory reads. It is also where stale references to superseded memories can still resolve and point onward through a `superseded_by` link.

*Call graph*: calls 2 internal fn (readable_page_states, transaction); called by 2 (get, member_detail); 6 external calls (__init__, __init__, __init__, __init__, select, UUID).


##### `MemoryObjects.status`  (lines 332–339)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports no special write status for memory objects. Since this object kind is read-only through this API, there is no pending apply/delete state to return.

**Data flow**: It receives the context, object name, and optional expected generation. It ignores them and returns nothing.

**Call relations**: This satisfies the object-store interface used by the wider system. Other memory methods refuse writes, so status has no extra work to do.


##### `MemoryObjects.apply`  (lines 341–350)

```
async def apply(self, ctx: ToolContext, name: str, spec: MemorySpec, old: MemorySpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create or edit a memory through the generic object apply path. Memories must be recorded through the dedicated `memory_update` route.

**Data flow**: It receives the proposed memory name, new spec, old spec, and generation information. Instead of saving anything, it raises a `VerbNotSupported` error with an explanation.

**Call relations**: The object framework may call this when someone tries to write a memory object. This method protects the memory system's intended write flow by stopping that request immediately.

*Call graph*: 1 external calls (__init__).


##### `MemoryObjects.delete`  (lines 352–359)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to delete a memory through the generic object delete path. Memories are ended by being superseded or ignored by recall, not by direct deletion here.

**Data flow**: It receives the context, memory name, and optional expected generation. It does not touch the database and raises a `VerbNotSupported` error explaining that memories are not deletable here.

**Call relations**: The object framework may call this for deletion requests. It preserves the memory history model, where old ids can still be opened even after a newer memory replaces them.

*Call graph*: 1 external calls (__init__).


##### `ProfileObjects.list`  (lines 410–411)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists member profile rows visible to the current tool caller. These profiles describe what shared workspace facts say about each member's role and focus.

**Data flow**: It receives a tool context and list query. It checks the extension context, reads the caller's allowed subjects from the context, and asks `_page` to fetch and format visible profiles.

**Call relations**: This is the normal object-list path for profiles during tool use. It delegates the shared-subject rule and database work to `ProfileObjects._page`.

*Call graph*: calls 2 internal fn (_page, _require_ext).


##### `ProfileObjects.member_page`  (lines 413–426)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists the People-band profiles visible to a signed-in member outside a live turn. Admin status does not reveal anything extra because profiles are based only on shared facts.

**Data flow**: It receives an extension context, member id, admin flag, and query. It turns that member's conversation audience into subject labels, checks the extension context, and passes the subjects to `_page`, which returns the profile page or an empty page.

**Call relations**: This is the portal/member-view version of `ProfileObjects.list`. It uses the shared audience helpers, then lets `_page` apply the profile visibility rule.

*Call graph*: calls 2 internal fn (_page, _require_ext); 2 external calls (audience_subjects, conversation_audience).


##### `ProfileObjects.get`  (lines 428–430)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ProfileSpec] | None
```

**Purpose**: Opens one member profile for the current tool caller. It returns only the detailed profile, not the wrapper used by member-facing views.

**Data flow**: It receives a tool context and profile name. It checks the extension context, passes the caller's readable subjects and name to `_entry`, and returns the detail part of the result if found.

**Call relations**: This is the direct object-get path for profiles. It relies on `ProfileObjects._entry` to parse the member id, enforce the shared-subject rule, and read the database.

*Call graph*: calls 2 internal fn (_entry, _require_ext).


##### `ProfileObjects.member_detail`  (lines 432–442)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[ProfileSpec] | None
```

**Purpose**: Opens one member profile for a signed-in member view, returning both the row summary and full detail. It follows the same audience rule as member profile listing.

**Data flow**: It receives an extension context, profile name, member id, and admin flag. It builds that member's subject set from their conversation audience, checks the extension context, and asks `_entry` for the matching profile. The result is a `MemberObject` or nothing.

**Call relations**: This is the member-facing counterpart to `ProfileObjects.get`. It hands all lookup and formatting work to `_entry` after choosing the correct member audience.

*Call graph*: calls 2 internal fn (_entry, _require_ext); 2 external calls (audience_subjects, conversation_audience).


##### `ProfileObjects._page`  (lines 444–463)

```
async def _page(self, ext: ExtensionContext, subjects: frozenset[str], query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Fetches and formats a list page of member profiles, but only for readers who can see workspace-shared facts. Without that shared subject, it returns an empty page.

**Data flow**: It receives the extension context, a set of readable subjects, and a list query. If the shared workspace subject is absent, it immediately returns an empty object page. Otherwise it queries recent profile rows for the workspace, converts each row with `_profile_row`, and wraps them in an object page.

**Call relations**: `ProfileObjects.list` and `ProfileObjects.member_page` both call this. It is the central list path for the People band and keeps the shared-facts visibility rule in one place.

*Call graph*: calls 2 internal fn (transaction, _profile_row); called by 2 (list, member_page); 2 external calls (select, object_page).


##### `ProfileObjects._entry`  (lines 465–498)

```
async def _entry(self, ext: ExtensionContext, subjects: frozenset[str], name: str) -> MemberObject[ProfileSpec] | None
```

**Purpose**: Loads one member profile by member id and returns it as both a row and a detail object. It only works for readers who can see workspace-shared facts.

**Data flow**: It receives the extension context, readable subjects, and profile name. If the shared subject is missing, it returns nothing. It parses the name as a UUID member id; invalid names return nothing. It queries the profile table for that member in the workspace, normalizes the written time, builds a row and a `ProfileSpec`, and returns a `MemberObject`.

**Call relations**: `ProfileObjects.get` and `ProfileObjects.member_detail` use this for single-profile reads. It combines permission checking, id parsing, database lookup, and final object shaping.

*Call graph*: calls 2 internal fn (transaction, _profile_row); called by 2 (get, member_detail); 6 external calls (__init__, __init__, __init__, select, _aware, UUID).


##### `ProfileObjects.status`  (lines 500–507)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports no special write status for profile objects. Profiles are read-only through this object API.

**Data flow**: It receives the context, profile name, and optional expected generation. It does not inspect or change anything and returns nothing.

**Call relations**: This exists to satisfy the same object-store interface as writable object kinds. Since profile writes are refused here, there is no status to compute.


##### `ProfileObjects.apply`  (lines 509–518)

```
async def apply(self, ctx: ToolContext, name: str, spec: ProfileSpec, old: ProfileSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create or edit a profile through the generic object apply path. Profiles are written by the People pass from shared workspace facts.

**Data flow**: It receives the proposed profile name, new spec, old spec, and generation information. It does not save anything and raises a `VerbNotSupported` error explaining the proper source of profile writes.

**Call relations**: The object framework may call this for profile write attempts. This method protects the generated People-band data from direct manual changes through the object API.

*Call graph*: 1 external calls (__init__).


##### `ProfileObjects.delete`  (lines 520–527)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to delete a profile through the generic object delete path. The People pass, not direct object deletion, controls which profiles exist.

**Data flow**: It receives the context, profile name, and optional expected generation. It leaves the database untouched and raises a `VerbNotSupported` error.

**Call relations**: The object framework may call this when deletion is requested. It keeps profile lifecycle tied to the shared-facts processing pipeline rather than ad hoc object edits.

*Call graph*: 1 external calls (__init__).


##### `_profile_row`  (lines 530–540)

```
def _profile_row(row: sa.Row) -> ObjectRow
```

**Purpose**: Builds one lightweight list row for a member profile. It gives callers the member id, role, focus, and write time in a consistent object-row shape.

**Data flow**: It receives a database row for a profile. It turns the member id into text, combines role and focus into a clipped summary, stamps the write time, and returns an `ObjectRow` with the profile fields.

**Call relations**: `ProfileObjects._page` uses this for every listed profile, and `ProfileObjects._entry` uses it when returning one profile detail. It shares timestamp formatting with memory rows through `_stamp`.

*Call graph*: calls 1 internal fn (_stamp); called by 2 (_entry, _page); 2 external calls (__init__, clip_to_word).


### Operational extension objects
Operational extensions surface monitors, scheduled report runs, and hosted sites so users can inspect their state and perform allowed stop, visibility, or removal actions.

### `extensions/monitors/ufo_ext_monitors/monitor_kind.py`

`domain_logic` · `request handling for monitor list, get, status, and delete operations`

This file is the bridge between the monitor feature and the general “objects” interface that members and agents can use. In plain terms, it makes active watches visible: you can list them, look at one in detail, check its current status, and delete it to stop watching. Without this file, monitors might still exist in storage, but they would not appear as first-class objects that the rest of the system can read or disarm.

A monitor here means: “run this command every so often in the same conversation sandbox, compare the result with the first result, and fire the agent if something important happens.” The first result is called the baseline, meaning the original snapshot used for comparison.

One important rule is that this file does not create monitors through a normal apply action. Arming a monitor needs a live chat turn, because the system must run the probe command immediately to seed that baseline. So applying a manifest is refused with a clear message telling callers to use the monitor action instead.

The main class, MonitorObjects, adapts MonitorStore records into object rows and object details. It decides what a member can see, builds owner information, exposes status counters such as probes run and failures, and deletes a monitor by disarming it. At the bottom, MONITOR_OBJECT registers all of this with the object framework, including which fields can be listed and which verbs are available.

#### Function details

##### `_owner`  (lines 53–58)

```
def _owner(row: Monitor) -> GeneratedObjectOwner
```

**Purpose**: Builds the ownership record for a monitor. This tells the object system who created the monitor, whether it is shared, and which exact monitor generation the owner record refers to.

**Data flow**: It receives one stored Monitor row. It reads the creator member ID, audience, and monitor ID from that row. It turns the audience into a shared/not-shared ownership fact, then returns a GeneratedObjectOwner that the object system can attach to list and detail results.

**Call relations**: MonitorObjects._member_rows calls this while turning stored monitor rows into rows that members can see. The owner record it returns becomes part of each listed object, so later reads or deletes can confirm they are talking about the same monitor and not a stale one.

*Call graph*: called by 1 (_member_rows); 2 external calls (__init__, subject_shared).


##### `_require_ext`  (lines 61–64)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Checks that the monitor extension context is present before code tries to use monitor storage. The extension context is the bundle of runtime services this extension needs, like access to its store.

**Data flow**: It receives an ExtensionContext or None. If a real context is present, it returns it unchanged. If it is missing, it raises a RuntimeError with a direct explanation, stopping the operation before it can fail later in a confusing way.

**Call relations**: MonitorObjects._member_rows, MonitorObjects._delete_owned, and MonitorObjects._find call this before creating a MonitorStore. It acts like a front-door check: monitor storage operations only proceed when the extension has the runtime context it needs.

*Call graph*: called by 3 (_delete_owned, _find, _member_rows).


##### `MonitorObjects._member_rows`  (lines 78–97)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Builds the list view of all armed monitors visible through this object kind. Each row is a compact summary suitable for listing: name, command/reason summary, owner, timing, conversation, and whether it belongs to the requesting member.

**Data flow**: It receives the extension context and the current member ID. It loads all armed monitors from MonitorStore, looks up creator email addresses, and converts each monitor into an OwnedRow. The result is a tuple of list rows with searchable fields such as conversation, next probe time, deadline, owner email, and mine.

**Call relations**: The object framework calls this when someone lists monitor objects. It calls _require_ext to make sure storage can be used, asks MonitorStore for armed monitors, calls owner_emails for friendly creator labels, and uses _owner to attach the ownership facts needed by the wider object system.

*Call graph*: calls 2 internal fn (_owner, _require_ext); 3 external calls (__init__, __init__, owner_emails).


##### `MonitorObjects._member_object`  (lines 99–125)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[MonitorSpec] | None
```

**Purpose**: Builds the detailed view for one monitor. It shows the monitor's saved settings and links it back to the conversation where its report will land.

**Data flow**: It receives the extension context, monitor name, expected owner record, and current member ID. It looks up the monitor by name, checks that the stored monitor ID still matches the owner generation, and returns None if not. If it matches, it returns an ObjectDetail containing a MonitorSpec with the command, interval, deadline, and reason, plus timestamps and a link to the watched conversation.

**Call relations**: The object framework calls this when someone gets one monitor by name. It relies on _find to locate the active monitor. It then packages the monitor into standard object detail pieces: MonitorSpec for the monitor's settings, ObjectDetail for the full response, and ObjectLink/ObjectRef to point at the related conversation.

*Call graph*: calls 1 internal fn (_find); 4 external calls (__init__, __init__, __init__, __init__).


##### `MonitorObjects._status`  (lines 127–142)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns live status information for one monitor. This is the operational snapshot: when it armed, when it must fire, whether it has probed, and how many quiet, failed, or skipped checks have happened.

**Data flow**: It receives a tool context, monitor name, and expected owner record. It finds the monitor, verifies that the monitor ID matches the owner generation, and returns None if the record is missing or stale. If found, it returns a dictionary with ISO-formatted times, counters, and a shortened excerpt of the baseline output.

**Call relations**: The object/tooling layer calls this when it needs status for a monitor object. It uses _find for lookup and then hands back simple JSON-friendly values, so callers can display monitor health without reading the raw store record directly.

*Call graph*: calls 1 internal fn (_find).


##### `MonitorObjects._apply_owned`  (lines 144–152)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: MonitorSpec, old: MonitorSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: Refuses attempts to create or update a monitor through the generic apply path. This protects an important rule: monitors must be armed through the chat monitor action, because that action runs the first probe and records the baseline.

**Data flow**: It receives the requested name, desired MonitorSpec, any old spec, and owner information, but deliberately does not use them to save anything. Instead, it raises VerbNotSupported with a message explaining that arming must happen in chat.

**Call relations**: The object framework would call this for an apply operation, but this monitor kind does not support that route. By raising VerbNotSupported, it directs callers toward the proper monitor action rather than letting them create an incomplete monitor with no baseline.

*Call graph*: 1 external calls (__init__).


##### `MonitorObjects._delete_owned`  (lines 154–159)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: Stops an armed monitor. Deleting the object is how the system disarms the watch so it will not probe or fire later.

**Data flow**: It receives a tool context, monitor name, and expected owner record. It finds the monitor, checks that the stored monitor ID still matches the owner generation, and raises an error if the record changed or disappeared. If it matches, it asks MonitorStore to disarm the row; if disarming fails because the record changed, it raises the same kind of safety error.

**Call relations**: The object framework calls this when an allowed user deletes a monitor object. It uses _find to locate the current armed monitor, _require_ext before accessing storage, and MonitorStore.disarm to perform the actual stop. The generation check prevents accidentally stopping a different monitor with the same name after a change.

*Call graph*: calls 2 internal fn (_find, _require_ext); 1 external calls (__init__).


##### `MonitorObjects._find`  (lines 161–165)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> Monitor | None
```

**Purpose**: Looks up one armed monitor by name. It is a small helper that keeps the name-search logic in one place for get, status, and delete.

**Data flow**: It receives the extension context and a monitor name. It checks that the context exists, loads all armed monitors from MonitorStore, scans them for the first row with the requested name, and returns that Monitor row. If no armed monitor has that name, it returns None.

**Call relations**: MonitorObjects._member_object, MonitorObjects._status, and MonitorObjects._delete_owned call this whenever they need the current stored row for a named monitor. It centralizes the store access and context check, so those higher-level operations can focus on shaping details, reporting status, or disarming.

*Call graph*: calls 1 internal fn (_require_ext); called by 3 (_delete_owned, _member_object, _status); 1 external calls (__init__).


### `extensions/report_digest/ufo_ext_report_digest/objects.py`

`domain_logic` · `request handling`

A “report” here is not something a user creates by filling out a form. It is the record left behind when a scheduled task runs and either publishes a digest or fails while trying. This file turns those run records into normal workspace objects so the rest of the system can show them in a familiar list/detail shape.

The file is careful about access. It only shows runs that the current member is already allowed to read through their normal audiences. In other words, it is like adding labels and a nicer display case around records the user could already see, not opening a new door to private data.

When listing reports, the code asks the extension context for recent scheduled runs, then enriches each run with two extras: any digest entry written by the report writer job, and the human-readable name of the scheduled task that fired it. It also turns attached artifacts into signed links, which are temporary URLs used to safely download or preview files.

Reports are deliberately read-only. Create, update, and delete requests are refused because reports come from scheduled tasks actually running. If a digest entry needs to be rebuilt, that is done by a separate rebuild tool, not by editing the report object directly.

#### Function details

##### `ReportObjects.list`  (lines 65–74)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists report objects visible to the current acting member. If there is no member attached to the request, it returns an empty page instead of exposing anything.

**Data flow**: It receives a tool context and a list query. It checks who the acting member is, extracts the extension context, then asks for a page of scheduled runs for the current agent and the member’s readable subjects. It returns those runs shaped as an object page.

**Call relations**: This is the public list entry for the report object kind. It delegates the real fetching and formatting work to `ReportObjects._page`, using `_ext` to get the extension context, and falls back to `object_page` for an empty result when no member is present.

*Call graph*: calls 2 internal fn (_page, _ext); 1 external calls (object_page).


##### `ReportObjects.get`  (lines 76–82)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ReportSpec] | None
```

**Purpose**: Fetches one report object by name, where the name is expected to be the scheduled run’s turn ID. It only works for an acting member, so anonymous or memberless contexts see nothing.

**Data flow**: It receives a tool context and a report name. It checks for an acting member, gets the extension context, asks `_one` to find the matching run, and returns the detail part of the found member object. If nothing is found, it returns `None`.

**Call relations**: This is the public detail lookup for normal tool use. It relies on `_ext` for context and `ReportObjects._one` for the actual search, permission-aware run lookup, and detail construction.

*Call graph*: calls 2 internal fn (_one, _ext).


##### `ReportObjects.member_page`  (lines 84–92)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists reports for a particular member in the member-object view of the system. The `admin` flag is accepted because the wider interface expects it, but this code does not use it to widen access.

**Data flow**: It receives an optional extension context, a member ID, an admin flag, and a list query. It converts the carrier into an extension context, uses the general object agent ID, fetches visible runs for that member, and returns a page of report rows.

**Call relations**: This is the member-facing listing path. Like `ReportObjects.list`, it hands the main work to `ReportObjects._page`, but it uses `object_agent_id` rather than the current turn’s agent ID.

*Call graph*: calls 2 internal fn (_page, _ext); 1 external calls (object_agent_id).


##### `ReportObjects.member_detail`  (lines 94–102)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[ReportSpec] | None
```

**Purpose**: Fetches one report for a particular member in the member-object view. As with listing, the admin flag does not grant broader report visibility.

**Data flow**: It receives an optional extension context, a report name, a member ID, and an admin flag. It resolves the extension context and asks `_one` to look up the run by name for that member. The result is either a full member object or `None`.

**Call relations**: This is the member-facing detail path. It is a thin wrapper around `_ext` and `ReportObjects._one`, keeping the same read rules as the regular object detail lookup.

*Call graph*: calls 2 internal fn (_one, _ext).


##### `ReportObjects.status`  (lines 104–111)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports do not have a separate live status operation in this object kind. This function always says there is no status document to return.

**Data flow**: It receives the context, report name, and optional expected generation value. It ignores them and returns `None`, meaning there is no status payload.

**Call relations**: The object framework may ask object kinds for status. For reports, the meaningful status is already stored in the row fields produced elsewhere, so this method does not call any helper or hand off work.


##### `ReportObjects.apply`  (lines 113–122)

```
async def apply(self, ctx: ToolContext, name: str, spec: ReportSpec, old: ReportSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create or update a report object. Reports are produced by scheduled runs, not by direct user edits.

**Data flow**: It receives the requested name, new spec, old spec, and generation check. Instead of changing data, it raises a `VerbNotSupported` error with a message explaining that reports exist only by running scheduled tasks.

**Call relations**: The object framework calls this when someone tries to apply a desired object state. This implementation stops the flow immediately by constructing the standard unsupported-verb error.

*Call graph*: 1 external calls (__init__).


##### `ReportObjects.delete`  (lines 124–131)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to delete a report object through the object interface. Removing or changing report history is not allowed here.

**Data flow**: It receives the context, report name, and optional generation check. It does not look up or remove anything; it raises a `VerbNotSupported` error explaining that reports come from scheduled task runs.

**Call relations**: The object framework calls this for delete requests. This method ends that path at once, using the same read-only rule as `ReportObjects.apply`.

*Call graph*: 1 external calls (__init__).


##### `ReportObjects._page`  (lines 133–146)

```
async def _page(self, ext: ExtensionContext, member_id: UUID, *, agent_id: UUID, query: ObjectListQuery, subjects: frozenset[str] | None=None) -> ObjectPage
```

**Purpose**: Builds a page of report rows from scheduled runs. It is the shared worker used by both normal listing and member listing.

**Data flow**: It receives an extension context, member ID, agent ID, list query, and optional readable subjects. It asks the extension context for the member’s recent scheduled runs, limited to the configured maximum. It enriches those runs into rows with `_rows`, then applies the object-page query rules with `object_page` and returns the page.

**Call relations**: `ReportObjects.list` and `ReportObjects.member_page` call this when a caller wants many reports. It gets raw run data from `ExtensionContext.scheduled_runs`, passes the runs to `ReportObjects._rows` for display shaping, and returns the final page.

*Call graph*: calls 2 internal fn (scheduled_runs, _rows); called by 2 (list, member_page); 1 external calls (object_page).


##### `ReportObjects._one`  (lines 148–180)

```
async def _one(self, ext: ExtensionContext, name: str, *, member_id: UUID, subjects: frozenset[str] | None=None) -> MemberObject[ReportSpec] | None
```

**Purpose**: Finds one report run by its turn ID and builds the full detail object for it. It also rejects names that are not valid UUIDs, because report names are UUID strings.

**Data flow**: It receives an extension context, a report name, member ID, and optional readable subjects. It tries to turn the name into a UUID, asks for the matching scheduled run, and returns `None` if the name is invalid or no visible run exists. If a run is found, it builds one row, wraps it with an empty `ReportSpec`, timestamps, and a link back to the conversation where the run was created.

**Call relations**: `ReportObjects.get` and `ReportObjects.member_detail` call this for single-report lookups. It uses `ExtensionContext.scheduled_runs` to respect visibility, `ReportObjects._rows` to format the row, and object model classes such as `MemberObject`, `ObjectDetail`, `ObjectLink`, and `ObjectRef` to return the standard detail shape.

*Call graph*: calls 2 internal fn (scheduled_runs, _rows); called by 2 (get, member_detail); 6 external calls (__init__, __init__, __init__, __init__, __init__, UUID).


##### `ReportObjects._rows`  (lines 182–197)

```
async def _rows(self, ext: ExtensionContext, runs: tuple[ScheduledRun, ...]) -> tuple[ObjectRow, ...]
```

**Purpose**: Enriches a batch of scheduled runs with digest entries and scheduled task names, then turns them into object rows. This avoids doing separate database lookups for each run one by one.

**Data flow**: It receives a tuple of scheduled runs. It collects their turn IDs to read digest entries, collects task IDs from their idempotency keys to read task names, and then builds one display row per run with `_row`. It returns a tuple of completed object rows.

**Call relations**: `ReportObjects._page` and `ReportObjects._one` call this after they have fetched scheduled runs. It calls `_entries` for digest data, `_task_names` for task labels, uses `scheduled_fire_task_id` to decode task IDs from run keys, and then hands each run to `_row` for final formatting.

*Call graph*: calls 3 internal fn (_entries, _row, _task_names); called by 2 (_one, _page); 1 external calls (scheduled_fire_task_id).


##### `ReportObjects._row`  (lines 199–241)

```
def _row(self, ext: ExtensionContext, run: ScheduledRun, entry: dict[str, JsonValue] | None, tasks: dict[UUID, str]) -> ObjectRow
```

**Purpose**: Turns one scheduled run into the compact row shape used by object lists and details. It chooses a useful summary and includes all fields a reader needs to understand what happened.

**Data flow**: It receives the extension context, one scheduled run, an optional digest entry, and a map of task IDs to task names. It works out the firing task name if available, chooses the digest title or a fallback summary, formats timestamps, hides successful terminal text by replacing it with an empty string, and adds artifact download and preview links. It returns an `ObjectRow`.

**Call relations**: `ReportObjects._rows` calls this once per run. This function uses `scheduled_fire_task_id` to connect a run back to a scheduled task, and uses the extension context’s artifact link helpers to turn stored file records into safe URLs for readers.

*Call graph*: calls 2 internal fn (artifact_link, artifact_preview_link); called by 1 (_rows); 2 external calls (__init__, scheduled_fire_task_id).


##### `ReportObjects._entries`  (lines 243–271)

```
async def _entries(self, ext: ExtensionContext, turn_ids: tuple[UUID, ...]) -> dict[UUID, dict[str, JsonValue]]
```

**Purpose**: Reads the digest entries already written for a set of report runs. These entries contain the report title, summary, and bullet-like points.

**Data flow**: It receives an extension context and a tuple of turn IDs. If there are no IDs, it returns an empty dictionary. Otherwise it opens a database transaction, selects matching rows from the report digest entry table for the current workspace, and returns a dictionary keyed by turn ID with simple JSON-ready digest data.

**Call relations**: `ReportObjects._rows` calls this before formatting rows, so each run can include the digest writer’s output when it exists. It uses `ExtensionContext.transaction` for database access and SQLAlchemy’s `select` builder to form the query.

*Call graph*: calls 1 internal fn (transaction); called by 1 (_rows); 1 external calls (select).


##### `ReportObjects._task_names`  (lines 273–291)

```
async def _task_names(self, ext: ExtensionContext, task_ids: tuple[UUID, ...]) -> dict[UUID, str]
```

**Purpose**: Looks up the human-readable names of scheduled tasks that fired the report runs. If a task was deleted, it simply will not have a name, and the report can still be shown.

**Data flow**: It receives an extension context and a tuple of scheduled task IDs. If the tuple is empty, it returns an empty dictionary. Otherwise it queries the scheduled task table for matching IDs in the current workspace and returns a map from task ID to task name.

**Call relations**: `ReportObjects._rows` calls this after extracting task IDs from runs. It uses the extension context’s transaction support and SQLAlchemy query building, then gives `_row` the task-name map so rows can show friendly task labels.

*Call graph*: calls 1 internal fn (transaction); called by 1 (_rows); 1 external calls (select).


##### `_ext`  (lines 294–299)

```
def _ext(carrier: ToolContext | ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Finds the `ExtensionContext`, which is the object that gives this code access to workspace data, scheduled runs, database transactions, and artifact links. It accepts either the context itself or a tool context that carries it.

**Data flow**: It receives a carrier that may be an `ExtensionContext`, a `ToolContext`, or `None`. If the carrier already is an extension context, it returns it. If the carrier has an `.ext` value, it returns that. If neither is true, it raises a runtime error because the report object cannot work without that context.

**Call relations**: The public methods `ReportObjects.list`, `ReportObjects.get`, `ReportObjects.member_page`, and `ReportObjects.member_detail` call this at the start of their work. It is the small adapter that lets the same report logic run from slightly different framework entry points.

*Call graph*: called by 4 (get, list, member_detail, member_page).


### `extensions/sites/ufo_ext_sites/objects.py`

`domain_logic` · `request handling`

A deployed site is more than a running port: it needs a stable name, a link, an owner, and rules about who may see it. This file teaches the workspace object system how to treat each deployed site as an object named from the site name plus a short fingerprint of the conversation that created it. That fingerprint matters because two different conversations might both deploy a site called “dashboard,” and they must not overwrite each other.

The file builds list rows for sites, including human details such as the creator, visibility, URL, preview image, creation time, and conversation link. It also enforces the sharing rules. The creator can change visibility. Workspace admins can make a site private, but cannot make it more open. Creating a site through the object API is refused, because a real site only exists after a deploy has chosen and served a port. Deleting a site unregisters it so its link stops resolving.

There is one important special case: a site can be bound as an agent’s homepage. Then the site’s own visibility setting is ignored, and the agent’s visibility decides who can see it. In that mode, the page is treated as part of the agent rather than as a browsable shared site.

#### Function details

##### `site_object_name`  (lines 81–85)

```
def site_object_name(conversation_id: UUID, name: str) -> str
```

**Purpose**: Builds the stable object name for a hosted site. It combines the site’s own name with a short digest, meaning a compact fingerprint, of the conversation id so identical site names in different conversations stay separate.

**Data flow**: It receives a conversation id and a site name → turns the conversation id into a short SHA-256 hash prefix → returns a string like `dashboard-9f21c0a4e3b7`.

**Call relations**: Other parts of this file call it whenever they need to turn a stored site row into the object name used by listing, conversation grants, or reverse name checks.

*Call graph*: called by 3 (member_conversation_rows, _named, site_name_from_object); 1 external calls (sha256).


##### `site_name_from_object`  (lines 88–94)

```
def site_name_from_object(conversation_id: UUID, object_name: str) -> str | None
```

**Purpose**: Tries to recover the original site name from an object name, but only if the object name really belongs to the given conversation. This prevents a name from being accepted just because it happens to have a similar suffix.

**Data flow**: It receives a conversation id and an object name → computes the expected digest suffix for that conversation → removes that suffix if present → verifies the rebuilt object name matches exactly → returns the site name or `None`.

**Call relations**: It uses `site_object_name` as a final safety check. It is the inverse helper for code that needs to interpret object names back into site names.

*Call graph*: calls 1 internal fn (site_object_name); 1 external calls (sha256).


##### `_named`  (lines 97–98)

```
def _named(sites: Iterable[HostedSite]) -> dict[str, HostedSite]
```

**Purpose**: Turns a group of hosted site records into a lookup table keyed by their object names. This makes later searches simple and consistent.

**Data flow**: It receives site records → computes each site’s object name from its conversation id and site name → returns a dictionary from object name to the original site record.

**Call relations**: Listing and lookup code rely on this helper so they all use exactly the same naming rule.

*Call graph*: calls 1 internal fn (site_object_name); called by 2 (_find, _member_rows).


##### `_workspace`  (lines 101–104)

```
def _workspace(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Checks that the site object code has an extension context, which is the object that gives access to the current workspace, store, and transaction. Without it, the file cannot safely read site data.

**Data flow**: It receives an optional context → if it is missing, raises an error explaining that this object kind must read through its context → otherwise returns the context unchanged.

**Call relations**: Most operations call this before touching workspace-specific information. `_sites` also uses it before opening the hosted-sites store.

*Call graph*: called by 6 (_apply_owned, _member_object, _member_rows, _status, member_conversation_rows, _sites).


##### `_sites`  (lines 107–109)

```
def _sites(ext: ExtensionContext | None) -> HostedSites
```

**Purpose**: Creates the workspace-scoped access object for the hosted-site registry. This is the doorway to stored site rows for the current workspace.

**Data flow**: It receives an optional extension context → verifies and unwraps it through `_workspace` → uses the workspace id and current transaction → returns a `HostedSites` store helper.

**Call relations**: All code that reads, updates, or unregisters hosted sites goes through this helper, so the correct workspace and transaction are always used.

*Call graph*: calls 1 internal fn (_workspace); called by 5 (_apply_owned, _delete_owned, _find, _member_rows, member_conversation_rows); 1 external calls (__init__).


##### `effective_visibility`  (lines 112–118)

```
def effective_visibility(site: HostedSite, agents: Mapping[UUID, str]) -> Visibility
```

**Purpose**: Decides the visibility that actually applies to a site. A normal site uses its own visibility, but an agent homepage uses the agent’s visibility instead.

**Data flow**: It receives a hosted site and a map of agent ids to visibility levels → if the site is not bound to an agent, returns the site’s own visibility → if it is bound, looks up that agent and converts the agent’s level into site-style visibility.

**Call relations**: Listing, detail reads, and visibility changes all call this so they judge access by the same rule, especially for homepage-bound sites.

*Call graph*: called by 3 (_apply_owned, _member_object, _member_rows); 1 external calls (visibility_level).


##### `_summary`  (lines 121–122)

```
def _summary(site: HostedSite, visibility: Visibility) -> str
```

**Purpose**: Creates a short human-readable summary for a site row. It gives someone scanning a list the site name, visibility, and sandbox port.

**Data flow**: It receives a site and the visibility to display → formats those pieces into one compact text string → returns that string for use in list results.

**Call relations**: The list-building flow calls this when it turns stored hosted sites into object rows.

*Call graph*: called by 1 (_member_rows).


##### `_preview_url`  (lines 125–132)

```
def _preview_url(scoped: ExtensionContext, site: HostedSite) -> str | None
```

**Purpose**: Returns a signed preview-image link for a site, if the last deploy captured one. A signed link is a temporary or permission-bearing URL that lets the portal show the image without adding a special site-preview route.

**Data flow**: It receives the workspace context and a hosted site → checks whether the site has both a preview blob key and a recorded size → if either is missing, returns `None` → otherwise asks the context to build the preview URL.

**Call relations**: Site listing calls this so rows can include a screenshot preview when one exists.

*Call graph*: calls 1 internal fn (image_preview_url); called by 1 (_member_rows).


##### `SiteObjects._admin_can_apply`  (lines 153–154)

```
def _admin_can_apply(self, old: SiteSpec, spec: SiteSpec) -> bool
```

**Purpose**: Defines the one visibility change a workspace admin is allowed to make without being the creator: making a non-private site private. This lets admins reduce exposure but not widen it.

**Data flow**: It receives the old site spec and requested new spec → compares their visibility values → returns `true` only when the old value was not private and the requested value is private.

**Call relations**: The base object system uses this hook while deciding whether an admin may apply a change to an owned site object.


##### `SiteObjects._listed`  (lines 156–157)

```
def _listed(self, row: OwnedRow[GeneratedObjectOwner], query: ObjectListQuery) -> bool
```

**Purpose**: Keeps agent homepage sites out of ordinary site browsing unless the caller explicitly asks for homepage bindings. This avoids presenting an agent’s page as a regular shared website.

**Data flow**: It receives a prepared object row and the list query → checks whether the row has the `homepage_agent` field → returns `true` for ordinary sites, or for homepage sites only when the query filters on that field.

**Call relations**: The inherited listing flow consults this method while deciding which rows should appear in a list response.


##### `SiteObjects.list`  (lines 159–169)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Adds a convenient shorthand for agents listing their own homepage site. The filter value `mine` is replaced with the current turn’s agent id.

**Data flow**: It receives the tool context and list query → if the query asks for `homepage_agent = mine`, copies the query with that value changed to the current agent id → passes the adjusted query to the parent listing behavior → returns the resulting object page.

**Call relations**: This method sits at the start of site listing. After resolving the viewer-relative `mine` shortcut, it hands the work back to the shared member-readable object listing machinery.

*Call graph*: 1 external calls (replace).


##### `SiteObjects._member_rows`  (lines 171–217)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Builds all site rows that the general object system can later filter and show to a member. Each row contains the site’s name, owner, visibility, URL, preview, and other useful fields.

**Data flow**: It receives the extension context and an optional member id → reads all hosted sites for the workspace → names them, reads agent visibilities, and looks up owner emails → for each site, computes effective visibility and builds an owned object row → returns all rows as a tuple.

**Call relations**: The object listing and read permission system depends on this as the raw feed of site objects. It uses helpers such as `_sites`, `_named`, `effective_visibility`, `_summary`, and `_preview_url` to assemble each row.

*Call graph*: calls 6 internal fn (_named, _preview_url, _sites, _summary, _workspace, effective_visibility); 4 external calls (__init__, __init__, owner_emails, site_url).


##### `SiteObjects.member_conversation_rows`  (lines 219–244)

```
async def member_conversation_rows(self, ext: ExtensionContext | None, conversation_id: UUID, *, member_id: UUID, admin: bool, limit: int) -> tuple[ConversationObjectGrant, ...]
```

**Purpose**: Reports which hosted site objects are visible from a particular conversation for a particular member. This lets conversation-level object views include sites created in that conversation.

**Data flow**: It receives the context, conversation id, member id, admin flag, and limit → reads agent visibility settings → asks the site store for sites in that conversation visible to that member, including qualifying homepage-agent sites → converts each site into a conversation object grant → returns those grants.

**Call relations**: Conversation object discovery calls this when it needs site objects attached to a conversation. It uses `site_object_name` so the grants use the same names as normal site listings.

*Call graph*: calls 3 internal fn (_sites, _workspace, site_object_name); 1 external calls (__init__).


##### `SiteObjects._member_object`  (lines 246–269)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[SiteSpec] | None
```

**Purpose**: Builds the detailed object view for one site. It tells the reader the site’s current effective visibility and links the site back to the conversation that created it.

**Data flow**: It receives the context, object name, owner information, and optional member id → finds the matching hosted site → if none exists, returns `None` → otherwise computes effective visibility, builds a `SiteSpec`, adds timestamps and a `created_in` conversation link → returns the object detail.

**Call relations**: The object read path calls this after ownership and visibility have been checked. It relies on `_find` to locate the stored site and on `effective_visibility` so homepage-bound sites show the agent-controlled visibility.

*Call graph*: calls 3 internal fn (_find, _workspace, effective_visibility); 4 external calls (__init__, __init__, __init__, __init__).


##### `SiteObjects._status`  (lines 271–298)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns operational details for a site, such as its URL, port, creator, deploy generation, homepage binding, and source files when available. This is the richer status view someone can use before editing or redeploying a site.

**Data flow**: It receives the tool context, object name, and owner → finds the hosted site → if missing, returns `None` → builds a status dictionary with link and deployment facts → if the site has a stored source manifest, materializes those files into the sandbox and records the path and file list → returns the status dictionary.

**Call relations**: The object status flow calls this for site objects. It hands off to `materialize_source` only when stored source exists, so editable source appears in the sandbox on demand.

*Call graph*: calls 2 internal fn (_find, _workspace); 2 external calls (materialize_source, site_url).


##### `SiteObjects._apply_owned`  (lines 300–336)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SiteSpec, old: SiteSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: Changes who may open an existing hosted site, while refusing fake creation and refusing direct visibility changes on agent homepage sites. It can also create a share card when a site is first made public.

**Data flow**: It receives the tool context, object name, requested spec, old spec, and owner → rejects the request if there is no old object because sites must come from deploys → finds the stored site → compares requested visibility with the effective current visibility → does nothing if unchanged → refuses if the site is an agent homepage → writes the new visibility to the site store → if the site became public and has a screenshot but no share card, asks the share-card code to draw one.

**Call relations**: The object apply/update path calls this after permission checks. It uses `_find`, `_sites`, and `effective_visibility` for the site update, and hands off to `draw_from_stored_shot` when publishing needs a richer social preview.

*Call graph*: calls 4 internal fn (_find, _sites, _workspace, effective_visibility); 2 external calls (__init__, draw_from_stored_shot).


##### `SiteObjects._delete_owned`  (lines 338–342)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: Unhosts a site by removing it from the hosted-site registry. After this, the permanent link no longer resolves through the site registry.

**Data flow**: It receives the tool context, object name, and owner → finds the hosted site → raises an error if it disappeared mid-delete → unregisters the site by conversation id and site name → returns nothing after the registry is updated.

**Call relations**: The object delete path calls this once the caller is allowed to unhost the site. It uses `_find` to translate the object name back to the stored site row, then uses `_sites` to unregister it.

*Call graph*: calls 2 internal fn (_find, _sites).


##### `SiteObjects._find`  (lines 344–345)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> HostedSite | None
```

**Purpose**: Finds one hosted site by its object name. It centralizes the lookup so all read, update, delete, and status paths use the same naming rule.

**Data flow**: It receives the extension context and object name → reads all hosted sites for the workspace → builds the object-name lookup with `_named` → returns the matching hosted site or `None`.

**Call relations**: Detail reads, status reads, visibility updates, and deletes all call this before acting on a specific site.

*Call graph*: calls 2 internal fn (_named, _sites); called by 4 (_apply_owned, _delete_owned, _member_object, _status).


### External source objects
Source extensions expose synced pages, configurable sources, and source triggers as workspace objects for listing, reading, forgetting, and wake-up routing.

### `extensions/sources/ufo_ext_sources/pages.py`

`domain_logic` · `request handling for page object list, get, and delete operations`

A “page” here is not something a user writes directly. It is one document that a sync process brought in from an outside source, such as an issue tracker or another provider. This file turns those synced database rows into workspace objects that tools can browse safely.

The important idea is “read-and-forget.” Users can list pages and get page details, but they cannot create or edit them through this object kind. Creation and updates belong to the content-sync driver. Deleting is also special: it does not simply remove a local object casually. It tombstones, or marks, the synced page as forgotten so the rest of the page-change pipeline can clean up derived search or index state. Only a workspace admin may do that.

The file also protects access. It reads pages through the extension context using the current reader’s source permissions, so callers only see pages they are allowed to see. When reading the body, it pulls bytes from the blob store, but only up to a fixed limit. This is like showing the first pages of a very large document instead of loading the whole book into memory. It also rechecks the page state after reading the blob, so if the page changed while it was being read, it refuses to return stale details.

#### Function details

##### `_require_ext`  (lines 62–65)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This helper makes sure the tool request has an ExtensionContext attached. The ExtensionContext is the gateway this file needs to read source pages and forget pages.

**Data flow**: It receives a ToolContext. If the context contains an extension context, it returns it. If not, it stops the operation with a runtime error, because page objects cannot work without that extension-side access.

**Call relations**: PageObjects._pages uses it before reading sources and pages, PageObjects.get uses it before rechecking readable page state, and PageObjects.delete uses it before forgetting a page. It is the small guard at the doorway to all extension-backed page operations.

*Call graph*: called by 3 (_pages, delete, get).


##### `_page_timestamp`  (lines 68–78)

```
def _page_timestamp(provider_value: str | None, row_value: datetime) -> str
```

**Purpose**: This helper turns a page timestamp into a consistent UTC time string. It accepts either a provider-supplied timestamp or falls back to the local row timestamp.

**Data flow**: It takes a timestamp string from the provider, if one exists, and a datetime from the stored row. If the provider value is missing, it uses the row time and gives it UTC if needed. If the provider value exists, it parses it and rejects it if it is invalid or has no timezone. It returns an ISO-formatted UTC timestamp with microsecond precision.

**Call relations**: _Page.spec and _Page.fields call this when presenting page times to users. It keeps list output and detailed output speaking the same time language.

*Call graph*: called by 2 (fields, spec); 2 external calls (fromisoformat, replace).


##### `_Page.name`  (lines 99–100)

```
def name(self) -> str
```

**Purpose**: This property gives the page its object name. In this system, the object name for a synced page is simply the page row’s UUID written as text.

**Data flow**: It reads the page’s internal UUID and converts it to a string. Nothing else is changed.

**Call relations**: PageObjects.list uses this value when building rows, and PageObjects._find compares it with the requested name when someone asks for a specific page.


##### `_Page.links`  (lines 102–110)

```
def links(self) -> tuple[ObjectLink, ...]
```

**Purpose**: This method describes what source object synced the page, when that source can be named. It creates a link saying the page was “synced_by” a source.

**Data flow**: It checks whether the page has a known source name. If not, it returns no links. If yes, it builds an ObjectLink pointing to the source object with that name.

**Call relations**: PageObjects.get includes these links in the returned page detail. This helps a reader move from a page back to the source subscription that produced it.

*Call graph*: 2 external calls (__init__, __init__).


##### `_Page.spec`  (lines 112–125)

```
def spec(self, body: str, body_truncated: bool) -> PageSpec
```

**Purpose**: This method builds the full public description of a page, including its metadata and the body text that was read from blob storage. The result is shaped as a PageSpec, which is the schema users see.

**Data flow**: It takes body text and a true-or-false flag saying whether the body was cut short. It combines those with the page’s stored source, stream, title, subject, digest, body reference, and normalized timestamps. It returns a PageSpec object.

**Call relations**: PageObjects.get calls this after it has read and bounded the page body. _Page.spec relies on _page_timestamp so detailed page reads use clean UTC timestamps.

*Call graph*: calls 1 internal fn (_page_timestamp); 1 external calls (__init__).


##### `_Page.summary`  (lines 127–128)

```
def summary(self) -> str
```

**Purpose**: This method creates a short one-line label for a page in list results. It helps users recognize the page without opening it.

**Data flow**: It reads the title, source backend, stream, and visibility subject from the page. It joins them into a compact sentence and trims it to the maximum summary length.

**Call relations**: PageObjects.list uses this summary when creating each ObjectRow. It is the list-view equivalent of a label on a folder.


##### `_Page.fields`  (lines 130–138)

```
def fields(self) -> dict[str, JsonValue]
```

**Purpose**: This method prepares the searchable and sortable metadata shown in page list results. It keeps list rows lightweight by leaving out the page body.

**Data flow**: It reads the page’s source id, backend name, stream, title, and timestamps. It normalizes the timestamps through _page_timestamp and returns a dictionary of simple JSON-friendly values.

**Call relations**: PageObjects.list calls this for every visible page. The ObjectKind definition later names these fields as the ones callers may filter or order by.

*Call graph*: calls 1 internal fn (_page_timestamp).


##### `PageObjects.list`  (lines 149–154)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This method returns a paged list of synced pages the caller is allowed to see. It gives names, short summaries, and useful metadata, but not the body text.

**Data flow**: It receives a tool context and a list query, reads the visible pages through PageObjects._pages, turns each page into an ObjectRow, and then passes those rows plus the query to object_page. The result is an ObjectPage containing the requested slice and ordering/filtering behavior.

**Call relations**: This is called when the object system needs to list objects of kind “page.” It delegates the permission-aware page gathering to PageObjects._pages, then hands the finished rows to the SDK paging helper.

*Call graph*: calls 1 internal fn (_pages); 2 external calls (__init__, object_page).


##### `PageObjects.get`  (lines 156–201)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[PageSpec] | None
```

**Purpose**: This method returns the full detail for one page, including a bounded copy of its body text. It is careful not to return a page if it disappeared or changed while being read.

**Data flow**: It receives a context and page name, finds the matching visible page, and returns None if there is no match. It opens the page body from blob storage and reads only up to one byte past the allowed limit so it can tell whether the body was truncated. It decodes the bytes as UTF-8 text, handling the case where the cutoff lands in the middle of a character. Then it rechecks the current readable page state. If the state no longer matches what it started with, it returns None; otherwise it returns an ObjectDetail with the PageSpec, timestamps, and links.

**Call relations**: The object system calls this when someone asks to read one page by name. It uses PageObjects._find to locate the page, ctx.blob to read the stored body, _require_ext to recheck the live page state, and _Page.spec and _Page.links indirectly to assemble the final detail.

*Call graph*: calls 3 internal fn (source_reader, _find, _require_ext); 1 external calls (__init__).


##### `PageObjects.status`  (lines 203–210)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This method reports no status for page objects. Pages are synced records, not user-authored resources with an apply progress state.

**Data flow**: It receives the context, page name, and expected generation value, but does not read or change anything. It always returns None.

**Call relations**: The object interface includes a status hook, so this class must provide one. For pages, there is nothing meaningful to hand off, because sync status belongs elsewhere.


##### `PageObjects.apply`  (lines 212–221)

```
async def apply(self, ctx: ToolContext, name: str, spec: PageSpec, old: PageSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This method rejects attempts to create or update a page through the object API. Pages must come from the content-sync driver, not from direct user edits.

**Data flow**: It receives the requested name, new spec, optional old spec, and generation expectation. Instead of writing anything, it raises VerbNotSupported with a message explaining that pages are synced, not authored.

**Call relations**: The object system would call this for create or update operations. This method deliberately stops that path, keeping PageObjects as a read-and-forget surface only.

*Call graph*: 1 external calls (__init__).


##### `PageObjects.delete`  (lines 223–235)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This method lets a workspace admin forget a synced page. Forgetting marks the page so the existing cleanup pipeline can remove derived index state.

**Data flow**: It receives a context and page name. First it asks whether the current speaker is an admin; if not, it raises AdminRequired. Then it finds the page visible to this request. If no page matches, it raises a value error. If the page exists and the speaker is allowed, it calls the extension context to forget that page id.

**Call relations**: The object system calls this when someone deletes a page object. It uses PageObjects._find to resolve the name and _require_ext to reach forget_page. It also relies on ToolContext.speaker_is_admin to enforce the admin-only gate.

*Call graph*: calls 3 internal fn (speaker_is_admin, _find, _require_ext); 1 external calls (__init__).


##### `PageObjects._find`  (lines 237–238)

```
async def _find(self, ctx: ToolContext, name: str) -> _Page | None
```

**Purpose**: This helper looks up one visible page by its object name. It keeps the get and delete methods from duplicating the same search logic.

**Data flow**: It receives a context and a name string. It loads the visible pages through PageObjects._pages, compares each page’s name with the requested name, and returns the first match. If none match, it returns None.

**Call relations**: PageObjects.get and PageObjects.delete call this before doing their main work. It depends on PageObjects._pages, so the lookup respects the same source visibility rules as listing.

*Call graph*: calls 1 internal fn (_pages); called by 2 (delete, get).


##### `PageObjects._pages`  (lines 240–268)

```
async def _pages(self, ctx: ToolContext) -> tuple[_Page, ...]
```

**Purpose**: This helper gathers all live synced pages the caller may read and wraps them in the local _Page shape. It also attaches source backend names and, when possible, source object names for links.

**Data flow**: It starts with the ToolContext, obtains the ExtensionContext, and reads registered sources. From those sources it builds maps from source id to backend name and source id to user-facing binding name. It then asks the extension context for source pages visible to the current source reader. For each record, it creates a _Page containing the page metadata, body reference, timestamps, and optional source name. It returns all of those _Page objects as a tuple.

**Call relations**: PageObjects.list calls this to produce list rows, and PageObjects._find calls it when get or delete needs one named page. It calls ToolContext.source_reader so the returned pages match the caller’s grants, and it uses ConnectorSourceConfig.model_validate plus binding_name to make links back to known connector sources.

*Call graph*: calls 2 internal fn (source_reader, _require_ext); called by 2 (_find, list); 3 external calls (__init__, model_validate, binding_name).


### `extensions/sources/ufo_ext_sources/tools.py`

`domain_logic` · `object operations and page-change hook`

This file is the control panel for content syncing. A “source” is a saved binding to an outside provider, such as an account plus a tenant URL and a chosen set of streams. A stream is one kind of content from that provider, like messages or tickets. Without this file, users could not register those feeds, change which streams are synced, safely share them with the workspace, or remove them cleanly.

The file also protects identity and permissions. Source names are not arbitrary labels; they are derived from the provider, account, and URL. That avoids duplicate registrations that secretly point at the same outside data. Private sources belong to the member who registered them. Shared sources can be read by the workspace, but only the registrar can make a private source shared. Deleting or forcing a resync is limited to the registrar or an admin.

A second object kind, “source_trigger,” connects a shared source to a conversation. When synced pages change, the page-change hook gathers the relevant changes, checks that the trigger’s agent is allowed to read them, writes a small change log when useful, and wakes the conversation. In everyday terms: sources are subscriptions to outside libraries, and triggers are the reminder notes that tell a specific room when new books arrive.

#### Function details

##### `_Binding.name`  (lines 219–220)

```
def name(self) -> str
```

**Purpose**: Returns the stable object name for a source binding. The name is derived from the provider, account, and tenant URL so the same real-world binding always gets the same name.

**Data flow**: It reads the binding’s provider, account, and base URL → passes them through the shared source-name rule → returns the derived name string.

**Call relations**: Other parts of this file use this property whenever they need to compare, list, watch, or notify about a binding. It delegates the actual naming rule to the shared `binding_name` helper so every caller uses one naming convention.

*Call graph*: 1 external calls (binding_name).


##### `_Binding.created_at`  (lines 223–224)

```
def created_at(self) -> datetime
```

**Purpose**: Reports when the binding first existed. Because one binding is made of several stream rows, it uses the earliest stream creation time.

**Data flow**: It reads all stream creation timestamps → finds the minimum → returns that timestamp as the binding’s creation time.

**Call relations**: Object detail views use this value so a grouped binding can be shown like one object even though it is stored as multiple stream rows.


##### `_Binding.updated_at`  (lines 227–228)

```
def updated_at(self) -> datetime
```

**Purpose**: Reports when the binding was last changed. It uses the latest update time from any stream in the binding.

**Data flow**: It reads all stream update timestamps → finds the maximum → returns that timestamp as the binding’s last update time.

**Call relations**: Object detail views use this value to show the binding’s freshest change across all of its streams.


##### `_Binding.links`  (lines 230–243)

```
def links(self) -> tuple[ObjectLink, ...]
```

**Purpose**: Builds links that explain what credential or connected account this source uses. This helps readers understand how the source authenticates without exposing secret values.

**Data flow**: It reads the binding’s account and sharing status → chooses either a workspace credential link, a private connection link, or no link for shared brokered sources → returns object links for display.

**Call relations**: Source object detail calls this when showing a source. It hands off to object-reference helpers and naming helpers so the link points to the right credential or connection object.

*Call graph*: 4 external calls (__init__, __init__, credential_object_name, account_object_name).


##### `_Binding.spec`  (lines 245–253)

```
def spec(self) -> SourceSpec
```

**Purpose**: Turns the internal binding record back into the public source specification users see. This is how stored stream rows become a clean manifest-like object.

**Data flow**: It reads provider, streams, account, base URL, sharing state, and backfill setting → converts internal values such as the direct-account marker into user-facing fields → returns a `SourceSpec`.

**Call relations**: Source lookup and comparison paths use this when deciding whether a submitted source spec matches what is already stored.

*Call graph*: 2 external calls (__init__, subject_shared).


##### `_Binding.summary`  (lines 255–257)

```
def summary(self) -> str
```

**Purpose**: Creates a short human-readable label for a binding. It names the provider, account, and streams in a compact form.

**Data flow**: It reads provider, account, and stream names → joins the stream names → trims the text to the configured maximum length → returns the summary string.

**Call relations**: Listings and alert messages use this summary so users see recognizable source information instead of only technical IDs.

*Call graph*: called by 1 (_alert_message).


##### `_require_ext`  (lines 266–269)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Ensures an extension context is present before source code tries to use it. The extension context is the object that gives access to stored sources, files, credentials, and runtime actions.

**Data flow**: It receives a possible context → if it is missing, raises an error explaining the dispatch is invalid → otherwise returns the context unchanged.

**Call relations**: Many source and trigger operations call this at their boundary. It prevents later code from failing in obscure ways when the object system was invoked without the required runtime support.

*Call graph*: called by 10 (_apply_owned, _delete_owned, _grant_settled, _member_rows, _resolved_account, _resync, _widen_window, _member_rows, _binding_named, _require_triggers).


##### `_require_connectors`  (lines 272–275)

```
def _require_connectors(ctx: ToolContext) -> ConnectorRegistry
```

**Purpose**: Ensures the current turn has a connector registry. The registry is the catalog that knows how providers can authenticate and which accounts are connected.

**Data flow**: It receives a tool context → reads its connector registry → raises an error if none is available → returns the registry when present.

**Call relations**: `SourceObjects._resolved_account` uses this before deciding whether a source should use a connected account or a direct workspace credential.

*Call graph*: called by 1 (_resolved_account).


##### `_bindings_from_ext`  (lines 278–313)

```
async def _bindings_from_ext(ext: ExtensionContext) -> tuple[_Binding, ...]
```

**Purpose**: Rebuilds high-level source bindings from the lower-level stored source rows. Each stream is stored as its own row, so this function groups related rows back into one user-facing source object.

**Data flow**: It asks the extension context for all source rows → ignores rows from providers not registered in this extension → validates each row’s connector config → groups rows by provider, account, and base URL → returns sorted `_Binding` objects.

**Call relations**: Listing sources, finding a named source, listing triggers with source summaries, and handling page changes all start here so they can reason in terms of whole bindings instead of individual stream rows.

*Call graph*: calls 1 internal fn (sources); called by 4 (_member_rows, _member_rows, _binding_named, on_page_change); 3 external calls (__init__, __init__, model_validate).


##### `_binding_named`  (lines 316–326)

```
async def _binding_named(ext: ExtensionContext | None, name: str) -> _Binding | None
```

**Purpose**: Finds one source binding by its derived name. It is the shared lookup used by both source operations and trigger operations.

**Data flow**: It receives an extension context and a name → rebuilds all bindings → compares each binding’s derived name to the requested name → returns the matching binding or `None`.

**Call relations**: Source get, status, apply, delete, resync, grant, and trigger creation all call this when they need to confirm that a named source still exists.

*Call graph*: calls 2 internal fn (_bindings_from_ext, _require_ext); called by 7 (_apply_owned, _delete_owned, _grant_settled, _member_object, _resync, _status, _apply_owned).


##### `_require_triggers`  (lines 329–330)

```
def _require_triggers(ext: ExtensionContext | None) -> SourceTriggerStore
```

**Purpose**: Creates access to the trigger store after confirming the extension context exists. The trigger store is where standing source watches are saved.

**Data flow**: It receives a possible extension context → checks it with `_require_ext` → wraps it in a `SourceTriggerStore` → returns that store.

**Call relations**: Trigger listing, creation, deletion, lookup, source deletion cleanup, and page-change delivery all use this as the doorway to trigger records.

*Call graph*: calls 1 internal fn (_require_ext); called by 6 (_delete_owned, _apply_owned, _delete_owned, _find, _member_rows, on_page_change); 1 external calls (__init__).


##### `effective_days`  (lines 333–342)

```
def effective_days(request: int | Literal['all'] | None, declared: int | None) -> int | None
```

**Purpose**: Decides how far back a stream should sync. It combines the user’s request with the stream’s own default window.

**Data flow**: It receives the requested backfill value and the stream’s declared default → returns the explicit number if one was requested, the declared default if nothing was requested, or `None` when the result means all available history.

**Call relations**: Source registration and window-widening both call this so initial sync and later resync-window changes follow the same rule.

*Call graph*: called by 2 (_apply_owned, _widen_window).


##### `_binding_identity`  (lines 345–355)

```
def _binding_identity(spec: SourceSpec) -> tuple[str, tuple[str, ...], str, str, bool, int | Literal['all'] | None]
```

**Purpose**: Extracts the fields that define whether two source specs mean the same binding state. It deliberately ignores action-only fields such as `resync`.

**Data flow**: It receives a `SourceSpec` → sorts the stream names and collects provider, account, URL, sharing, and backfill fields → returns a tuple suitable for equality checks.

**Call relations**: `SourceObjects.apply` uses this to detect no-op re-applies, and `_resync` uses it to reject attempts to resync while also changing the source.

*Call graph*: called by 2 (_resync, apply).


##### `SourceObjects.apply`  (lines 384–405)

```
async def apply(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Implements the public apply behavior for source objects. It separates three cases: resync now, identical re-apply, or real create/update.

**Data flow**: It receives the submitted name and spec plus the old visible spec if any → if `resync` is set, schedules sync work → if the spec is identical, grants this agent access to the existing feed → otherwise passes the operation to the base object machinery for normal permission checks and mutation.

**Call relations**: This is the entry point for applying a source object. It calls `_resync`, `_grant_settled`, or the parent class depending on what the submit is trying to do.

*Call graph*: calls 3 internal fn (_grant_settled, _resync, _binding_identity).


##### `SourceObjects._grant_settled`  (lines 407–428)

```
async def _grant_settled(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: Gives the current agent access to an already-existing source when the user re-applies the exact same spec. This matters because no new stream row is created on a no-op, but the agent still needs permission to read the feed it asked for.

**Data flow**: It reads the speaking member, source owner, and binding → if the member is allowed to receive the grant, it grants each stream’s source ID to the current agent → otherwise it changes nothing.

**Call relations**: `SourceObjects.apply` calls this only for identical re-applies. It then uses `_binding_named` and the extension context to perform the actual grants.

*Call graph*: calls 2 internal fn (_binding_named, _require_ext); called by 1 (apply).


##### `SourceObjects._resync`  (lines 430–454)

```
async def _resync(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None) -> None
```

**Purpose**: Schedules an immediate sync for all streams in an existing source. It is an action, not a lasting setting.

**Data flow**: It checks that the submitted spec exactly matches the current source except for `resync` → verifies the caller can see the source and is the owner or an admin → finds the binding → asks the extension context to schedule sync for all stream rows.

**Call relations**: `SourceObjects.apply` calls this when the submitted spec asks for `resync`. It relies on `_binding_identity`, `_binding_named`, and the tool context’s admin check to keep the action safe.

*Call graph*: calls 4 internal fn (speaker_is_admin, _binding_identity, _binding_named, _require_ext); called by 1 (apply); 3 external calls (__init__, __init__, __init__).


##### `SourceObjects._member_rows`  (lines 456–469)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[ObjectOwner], ...]
```

**Purpose**: Builds the rows shown when listing source objects. Each row represents one reconstructed binding, not one stored stream row.

**Data flow**: It rebuilds all bindings → for each binding, creates a row with name, summary, owner member, and shared/private status → returns the rows to the object framework.

**Call relations**: The member-readable object base calls this during source listing. It supplies the raw rows that the base then filters according to member visibility and admin rights.

*Call graph*: calls 2 internal fn (_bindings_from_ext, _require_ext); 3 external calls (__init__, __init__, subject_shared).


##### `SourceObjects._member_object`  (lines 471–487)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: ObjectOwner, *, member_id: UUID | None) -> ObjectDetail[SourceSpec] | None
```

**Purpose**: Builds the detailed view for one source object. It turns the stored binding into the public spec, timestamps, and credential/connection links.

**Data flow**: It receives a source name and owner → looks up the binding → if found, returns an `ObjectDetail` with spec, creation time, update time, and links; otherwise returns `None`.

**Call relations**: The object framework calls this after visibility has been checked. It depends on `_binding_named` and `_Binding` helper methods to present the binding as one object.

*Call graph*: calls 1 internal fn (_binding_named); 1 external calls (__init__).


##### `SourceObjects._status`  (lines 489–515)

```
async def _status(self, ctx: ToolContext, name: str, _owner: ObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Reports operational status for a source, such as when each stream will sync next and whether it has errors. This is the diagnostic view of a source.

**Data flow**: It looks up the binding → builds a status dictionary with sharing state and per-stream timing/error/backfill fields → includes owner member ID for private owned sources → returns the dictionary or `None` if missing.

**Call relations**: The object framework calls this when a source status is requested. It uses `_binding_named` to translate the object name into stream-level runtime information.

*Call graph*: calls 1 internal fn (_binding_named); 1 external calls (subject_shared).


##### `SourceObjects._apply_owned`  (lines 517–621)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: Performs the real create or update of a source after the object framework has accepted that the caller may mutate it. It validates provider, streams, URL, account, sharing, and backfill rules before writing anything.

**Data flow**: It receives the desired source spec → checks there is a speaking member → validates the provider and stream names → validates the tenant URL → resolves the account or credential path → confirms the derived name is correct → widens any allowed backfill window or shares existing rows → registers new stream rows, grants existing ones to the agent, and removes dropped streams.

**Call relations**: The base object apply flow calls this for real mutations. It coordinates `_resolved_account`, `_validated_base_url`, `_binding_named`, `_widen_window`, `effective_days`, and extension-context write operations.

*Call graph*: calls 6 internal fn (_resolved_account, _widen_window, _binding_named, _require_ext, _validated_base_url, effective_days); 8 external calls (__init__, __init__, __init__, now, timedelta, binding_name, member_subject, get).


##### `SourceObjects._widen_window`  (lines 623–697)

```
async def _widen_window(self, ctx: ToolContext, binding: _Binding, *, kept: tuple[_Stream, ...], declared: dict[str, int | None], windowed: frozenset[str], account: str, base_url: str | None, request:
```

**Purpose**: Changes a source’s backfill window only when the new request reaches farther back in time. It refuses narrowing because old synced pages outside the new window would otherwise be left behind incorrectly.

**Data flow**: It receives the current binding, kept streams, provider window defaults, account, URL, and requested window → computes each kept stream’s new cutoff date from the original anchor point → rejects any cutoff that moves later → updates source configs and marks widened streams for refetch.

**Call relations**: `SourceObjects._apply_owned` calls this before making other writes when the backfill setting changes. It uses `effective_days` so widening follows the same window math as initial registration.

*Call graph*: calls 2 internal fn (_require_ext, effective_days); called by 1 (_apply_owned); 3 external calls (__init__, __init__, timedelta).


##### `SourceObjects._delete_owned`  (lines 699–706)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: Deletes a source binding by removing all of its stream rows, then removes triggers that watched it. This is the cleanup path for a whole source object.

**Data flow**: It looks up the binding by name → if missing, raises an unknown-object error → removes each stream source ID → asks the trigger store to remove watches for that binding.

**Call relations**: The object framework calls this after delete permissions are satisfied. It connects source deletion to trigger cleanup so no trigger keeps promising alerts for a gone source.

*Call graph*: calls 3 internal fn (_binding_named, _require_ext, _require_triggers); 1 external calls (__init__).


##### `SourceObjects._resolved_account`  (lines 708–781)

```
async def _resolved_account(self, ctx: ToolContext, spec: SourceSpec) -> _ResolvedAccount
```

**Purpose**: Decides which authentication route a source will use: a connected account broker or a direct workspace credential. This keeps source registration from guessing or accepting contradictory account settings.

**Data flow**: It reads the connector registry, connected accounts, connection details, declared credential slots, and submitted account ID → validates ownership and availability → returns the selected account handle and optional connection ID, or raises a clear error telling the user what to connect or configure.

**Call relations**: `SourceObjects._apply_owned` calls this before registering streams. It is the bridge between the user’s source spec and the runtime credential resolver that will later run the sync.

*Call graph*: calls 4 internal fn (connector_accounts, connector_connection, _require_connectors, _require_ext); called by 1 (_apply_owned); 1 external calls (__init__).


##### `trigger_name`  (lines 784–788)

```
def trigger_name(binding: str, conversation_id: UUID) -> str
```

**Purpose**: Builds the stable name for a source trigger. A trigger is identified by the source it watches and the conversation it belongs to.

**Data flow**: It receives a binding name and conversation ID → joins them into one deterministic string → returns that trigger object name.

**Call relations**: Trigger listing, lookup, and creation all use this helper so a trigger cannot be accidentally filed under a different name.

*Call graph*: called by 3 (_apply_owned, _find, _member_rows).


##### `SourceTriggerObjects._member_rows`  (lines 819–853)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Builds the rows shown when listing source triggers. It includes the watched source, delivery mode, owning conversation, origin, creator email, and whether the trigger belongs to the current member.

**Data flow**: It reads reported triggers from the trigger store → rebuilds source bindings for summaries → looks up creator emails → creates owned rows whose shared/private visibility follows the owning conversation’s audience → returns the rows.

**Call relations**: The member-readable object base calls this for trigger listing. It combines trigger-store data, source summaries, and owner display information into one list view.

*Call graph*: calls 4 internal fn (_bindings_from_ext, _require_ext, _require_triggers, trigger_name); 4 external calls (__init__, __init__, owner_emails, subject_shared).


##### `SourceTriggerObjects._member_object`  (lines 855–890)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[SourceTriggerSpec] | None
```

**Purpose**: Builds the detailed view for one trigger. It shows what source it watches, how it delivers alerts, and links to the watched source and sometimes the reporting conversation.

**Data flow**: It finds the trigger by name → checks that its stored generation still matches the owner data → builds links and a `SourceTriggerSpec` → returns an `ObjectDetail`, or `None` if the trigger changed or disappeared.

**Call relations**: The object framework calls this after visibility checks. It relies on `_find` to protect against stale trigger names or replaced rows.

*Call graph*: calls 1 internal fn (_find); 4 external calls (__init__, __init__, __init__, __init__).


##### `SourceTriggerObjects._status`  (lines 892–906)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Reports readable status information for a trigger. This mirrors the useful list fields for one trigger.

**Data flow**: It finds the trigger and confirms its generation → looks up the creator email → returns conversation ID, source, delivery mode, origin, owner email, and whether it belongs to the acting member.

**Call relations**: The object framework calls this for trigger status requests. It uses `_find` and owner-email lookup to present current trigger metadata.

*Call graph*: calls 1 internal fn (_find); 1 external calls (owner_emails).


##### `SourceTriggerObjects._apply_owned`  (lines 908–945)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SourceTriggerSpec, old: SourceTriggerSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: Creates a trigger for the current conversation to watch a shared source. Existing triggers are immutable: if the same one is re-applied it is a no-op, and changing it requires deletion and recreation.

**Data flow**: It derives the expected trigger name from the requested source and current conversation → rejects a mismatched name → if an existing identical trigger is found, returns without changes → verifies the source is watchable → creates the trigger row → rechecks that the source still exists and removes the trigger if the source vanished during the race.

**Call relations**: The object framework calls this for trigger apply operations. It uses `_watchable`, `_require_triggers`, `trigger_name`, and `_binding_named` to create only valid watches.

*Call graph*: calls 4 internal fn (_watchable, _binding_named, _require_triggers, trigger_name); 1 external calls (__init__).


##### `SourceTriggerObjects._watchable`  (lines 947–960)

```
async def _watchable(self, ctx: ToolContext, source: str) -> None
```

**Purpose**: Checks whether the requested source can be watched by a trigger. Only visible shared sources are watchable.

**Data flow**: It asks the source object store for the named source using the caller’s context → if no visible source is found, raises an unknown-object error → if the source is private, raises a clear refusal → otherwise returns successfully.

**Call relations**: `SourceTriggerObjects._apply_owned` calls this before writing a trigger. By asking the source object store, it reuses the normal source visibility rules instead of duplicating them.

*Call graph*: called by 1 (_apply_owned); 1 external calls (__init__).


##### `SourceTriggerObjects._delete_owned`  (lines 962–966)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: Deletes a source trigger after confirming it is still the same trigger the caller meant to delete. This avoids deleting a changed row by accident.

**Data flow**: It finds the trigger by name → checks that the stored generation matches the owner generation → if it matches, removes the trigger from the trigger store; if not, raises an error.

**Call relations**: The object framework calls this after delete permissions pass. It uses `_find` and `_require_triggers` to perform a safe, current-row deletion.

*Call graph*: calls 2 internal fn (_find, _require_triggers).


##### `SourceTriggerObjects._find`  (lines 968–976)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> ListedTrigger | None
```

**Purpose**: Finds a reported trigger by its derived object name. This is the trigger equivalent of looking up a source by its derived binding name.

**Data flow**: It reads all reported triggers → computes each trigger’s object name from its binding and conversation → returns the matching listed trigger or `None`.

**Call relations**: Trigger detail, status, and delete paths call this whenever they need the current trigger row behind an object name.

*Call graph*: calls 2 internal fn (_require_triggers, trigger_name); called by 3 (_delete_owned, _member_object, _status).


##### `on_page_change`  (lines 979–1029)

```
async def on_page_change(ctx: HookContext) -> HookOutcome
```

**Purpose**: Runs when synced pages change and wakes the conversations that have source triggers. It is the delivery hook between the sync system and agents.

**Data flow**: It receives a page-change hook payload → verifies it is a batch of page changes → maps changed source IDs back to bindings → groups changes by binding → finds triggers for each binding → keeps only shared pages readable by the trigger’s agent → calls `_fire_trigger` for each authorized trigger.

**Call relations**: The manifest hook system calls this after page changes. It uses `_bindings_from_ext` and the trigger store to decide who should be notified, then hands each authorized notification to `_fire_trigger`.

*Call graph*: calls 3 internal fn (_bindings_from_ext, _fire_trigger, _require_triggers); 2 external calls (__init__, suppress).


##### `_fire_trigger`  (lines 1032–1090)

```
async def _fire_trigger(ext: ExtensionContext, binding: _Binding, trigger: SourceTrigger, audience: Audience, authorized: list[PageChange]) -> None
```

**Purpose**: Delivers one trigger notification. Depending on the trigger’s delivery mode, it either wakes the existing conversation or opens one stable conversation per changed page.

**Data flow**: It receives the binding, trigger, conversation audience, and authorized changes → for current delivery, writes one change log and invokes the owning conversation → for per-page delivery, opens or reuses page-specific conversations, writes one-page logs, and invokes each conversation with an idempotency key.

**Call relations**: `on_page_change` calls this after filtering changes for permission. It hands off to `_write_change_log` and `_alert_message`, then uses extension-context conversation methods to wake the agent.

*Call graph*: calls 4 internal fn (invoke, open_conversation, _alert_message, _write_change_log); called by 1 (on_page_change); 1 external calls (conversation_audience).


##### `_write_change_log`  (lines 1093–1128)

```
async def _write_change_log(ext: ExtensionContext, conversation_id: UUID, binding: _Binding, latest: str, changes: list[PageChange]) -> str | None
```

**Purpose**: Writes the list of changed pages to a small runtime file when file storage is available. This keeps long change details out of the chat message while still making them available to the agent.

**Data flow**: It receives a conversation ID, binding, latest/revision label, and changes → if files are unavailable, returns `None` → otherwise writes one JSON line per changed page with page reference, stream, title, change type, and timestamp → prunes older runtime files → returns the written path.

**Call relations**: `_fire_trigger` calls this before sending an alert. The alert can then point the agent to the log file when there are too many changed pages to name directly.

*Call graph*: calls 1 internal fn (_disposition); called by 1 (_fire_trigger); 1 external calls (dumps).


##### `_disposition`  (lines 1131–1137)

```
def _disposition(change: PageChange) -> str
```

**Purpose**: Labels a page change as added, updated, or removed. This turns low-level timestamps and tombstone flags into words a user can understand.

**Data flow**: It reads one page change → returns `removed` if it is a tombstone, `added` if the creation time equals the change time, otherwise `updated`.

**Call relations**: Change-log writing and stream-count summaries both call this so alerts and files use the same labels.

*Call graph*: called by 2 (_stream_counts, _write_change_log).


##### `_stream_counts`  (lines 1140–1154)

```
def _stream_counts(changes: list[PageChange]) -> str
```

**Purpose**: Summarizes how many pages changed in each stream. For example, it can say that one stream had several updates while another had removals.

**Data flow**: It receives a list of page changes → groups them by stream and disposition → formats nonzero counts in a stable order → returns one readable summary string.

**Call relations**: `_alert_message` calls this to include a compact overview of the batch before pointing to exact pages or a change log.

*Call graph*: calls 1 internal fn (_disposition); called by 1 (_alert_message); 1 external calls (defaultdict).


##### `_alert_message`  (lines 1157–1176)

```
def _alert_message(binding: _Binding, changes: list[PageChange], log_path: str | None) -> str
```

**Purpose**: Builds the message sent to an agent when a watched source changes. It gives a short summary and tells the agent how to inspect the changed pages.

**Data flow**: It receives the binding, changed pages, and optional log path → if there are only a few changes, names the pages directly → if there are many and a log exists, points to the log → otherwise tells the agent how to list pages → returns the final alert text.

**Call relations**: `_fire_trigger` calls this immediately before invoking an agent conversation. It uses `_stream_counts`, `_page_reference`, and the binding summary to make the alert useful.

*Call graph*: calls 3 internal fn (summary, _page_reference, _stream_counts); called by 1 (_fire_trigger).


##### `_page_reference`  (lines 1179–1183)

```
def _page_reference(change: PageChange) -> str
```

**Purpose**: Formats one changed page as a readable object reference. It includes the page object name and a short title label.

**Data flow**: It receives one page change → chooses the title or a fallback for untitled pages → trims the label → returns a string the agent can use with object-get style tools.

**Call relations**: `_alert_message` uses this when a batch is small enough to name each changed page directly.

*Call graph*: called by 1 (_alert_message).


##### `_validated_base_url`  (lines 1186–1226)

```
def _validated_base_url(provider: str, base_url: str | None) -> str | None
```

**Purpose**: Validates and normalizes a provider’s tenant API URL. This prevents unsafe or wrongly shaped URLs from being stored in a source binding.

**Data flow**: It receives a provider and optional base URL → checks whether the provider has a fixed API host or requires a tenant URL → parses the submitted URL → requires safe HTTPS with no credentials, port, query, or fragment → checks provider-specific host and path rules → returns a normalized URL or `None` for fixed-host providers.

**Call relations**: `SourceObjects._apply_owned` calls this before resolving accounts or writing source rows. It protects sync registration from accepting arbitrary network destinations.

*Call graph*: called by 1 (_apply_owned); 1 external calls (urlsplit).

# Membership, workspace objects, credentials, and grants  `stage-6`

This stage is the system’s permission desk. It runs behind the scenes whenever a person or agent tries to view workspace data, use an outside account, or change shared settings. The central object system defines a common way to list, read, create, update, and delete workspace records, while agent and object scope keep track of “who is acting” so the wrong agent cannot accidentally use the wrong powers.

Several files turn important workspace things into safe, inspectable objects. Agents, members, conversations, artifacts, credential slots, memories, synced pages, and external sources each get clear rules about what can be seen or changed. Sensitive areas are locked down: secrets are never shown, artifacts cannot be invented directly, conversations and memories are mostly read-only, and membership cannot lose its last admin.

Other parts decide access. Seats control which members an agent may answer. Web audience rules decide who can use agents in the portal. Connector account objects manage connected services, separating the account itself from an agent’s permission to use it. Together, these pieces act like guards, labels, and filing cabinets for workspace power.

## Files in this stage

### Built-in workspace objects
Core object types expose agents, shared files, past conversations, credential slots, and members while enforcing each type's safety rules.

### `core/src/ufo/agents.py`

`domain_logic` · `request handling for object list/get/status/apply/delete operations`

This file is the object interface for workspace agents. In plain terms, it lets the rest of the system say: “show me the agent,” “show me its current status,” or “change its allowed settings.” It does not create new agents, delete agents, or change the system prompt. Those actions are deliberately blocked or routed elsewhere.

The main settings owned here are the agent’s model and whether it may use public internet access. The system prompt is shown as read-only status, along with a digest, which is a short fingerprint used to prove which prompt version a proposal is based on. Prompt edits go through a governance path, like a formal change request, so they do not conflict with direct setting edits here.

The file reads and writes the `agent` database table inside the current workspace. It also enforces permissions. A speaker must be a workspace admin to edit an agent. If editing an agent other than the current one, the running agent must also be the main agent. This is like a building rule where tenants can update their own room only if they are authorized, but changing another room requires the building manager.

One important detail is the `auto` model setting. The stored value may say “auto,” but status and list views report the concrete model actually being used right now.

#### Function details

##### `_effective_model`  (lines 43–48)

```
def _effective_model(ctx: ToolContext, stored: str) -> str
```

**Purpose**: This helper turns the stored model setting into the model name a reader should actually see. If the database says `auto`, it reports the concrete model chosen for the current turn instead of the word `auto`.

**Data flow**: It receives the current tool context and the model value stored in the database. If the stored value is the special `auto` marker, it reads the resolved model from the current agent context; otherwise it keeps the stored value. It returns the model name to display.

**Call relations**: The list and status views call this helper when showing an agent’s model. This keeps display behavior consistent while still leaving the saved spec unchanged, so reading and reapplying an object does not accidentally replace `auto` with today’s concrete model.

*Call graph*: called by 2 (list, status).


##### `AgentObjects.list`  (lines 74–101)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This function returns a paged list of agents in the current workspace. Each row gives a short human-readable summary, including whether the agent is the main agent, which model it is using, and whether public internet is allowed.

**Data flow**: It starts with the current tool context and a list query, then opens a workspace database transaction. It reads agent names, stored models, main-agent flags, and internet-access flags for the current workspace. It turns those database rows into object rows with friendly summaries, then wraps them into an object page result.

**Call relations**: When the object system needs to show available `agent` objects, it calls this method. During that flow, it asks `_effective_model` to present the resolved model name, then hands the finished rows to the object paging helper so callers get the normal object-list shape.

*Call graph*: calls 1 internal fn (_effective_model); 5 external calls (__init__, select, workspace_tx, object_page, ws_current).


##### `AgentObjects.get`  (lines 103–116)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[AgentSpec] | None
```

**Purpose**: This function returns the editable specification for one named agent, or `None` if that agent does not exist. The spec includes only the settings this object path owns: model and public-internet access.

**Data flow**: It receives a context and an agent name. It looks up the database row for that name. If no row is found, it returns nothing. If a row exists, it builds an `AgentSpec` from the stored model and internet-access flag, then packages that with the row’s creation and update times.

**Call relations**: The object system calls this when someone asks for the stored definition of a single agent. It relies on `_row` for the database lookup, then returns an object detail that can be compared with or used by later apply operations.

*Call graph*: calls 1 internal fn (_row); 2 external calls (__init__, __init__).


##### `AgentObjects.status`  (lines 118–133)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This function returns the live read-only status for one named agent. It includes whether the agent is main, the current prompt, a digest of that prompt, and the effective model currently in use.

**Data flow**: It receives the context, agent name, and an optional expected generation value. It looks up the agent row. If the row is missing, it returns nothing. Otherwise it builds a status dictionary from the database row, computes a prompt digest from the prompt text, and converts `auto` into the concrete model visible for this turn.

**Call relations**: The object system calls this when a caller wants the agent’s current state rather than just its editable spec. It uses `_row` to read the database, `prompt_digest` to provide a safe fingerprint for governance proposals, and `_effective_model` to report the model a member should understand as active.

*Call graph*: calls 2 internal fn (_row, _effective_model); 1 external calls (prompt_digest).


##### `AgentObjects.apply`  (lines 135–165)

```
async def apply(self, ctx: ToolContext, name: str, spec: AgentSpec, old: AgentSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This function updates an existing agent’s model and public-internet permission. It refuses creation, checks admin rules, and then writes the approved settings to the database.

**Data flow**: It receives the current context, target agent name, desired spec, old spec, and an optional expected generation value. If there is no old spec or no matching database row, it rejects the request because this path cannot create agents. It then checks whether the speaker is an admin and whether the current agent is allowed to edit the target. If the checks pass, it updates the model, internet-access flag, and update timestamp in the current workspace’s agent table.

**Call relations**: The object system calls this when someone applies a change to an `agent` object. It uses `_row` to confirm the target exists, asks the tool context about admin and main-agent authority, raises clear errors when the operation is not allowed, and finally performs the database update for the fields this file owns.

*Call graph*: calls 3 internal fn (_row, agent_is_main, speaker_is_admin); 5 external calls (__init__, __init__, update, workspace_tx, ws_current).


##### `AgentObjects.delete`  (lines 167–174)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This function always refuses to delete an agent through the object interface. Agents are treated as existing workspace actors, not disposable objects in this path.

**Data flow**: It receives the context, agent name, and optional expected generation value, but does not read or change any database data. It immediately raises an error explaining that agents cannot be deleted through objects.

**Call relations**: The object system calls this if a caller tries to delete an `agent` object. Instead of handing off to any database operation, it stops the flow with a `VerbNotSupported` error so deletion must be handled elsewhere, if it is allowed at all.

*Call graph*: 1 external calls (__init__).


##### `AgentObjects._row`  (lines 176–193)

```
async def _row(self, name: str) -> sa.Row | None
```

**Purpose**: This private helper fetches the database record for one named agent in the current workspace. It centralizes the lookup so get, status, and apply all use the same definition of “this agent.”

**Data flow**: It receives an agent name. It opens a workspace database transaction and selects the prompt, id, model, main-agent flag, internet-access flag, and timestamps for that name within the current workspace. It returns the single matching row or `None` if no agent matches.

**Call relations**: The get, status, and apply methods call this before doing their own work. It is the shared doorway to the `agent` table for single-agent operations, ensuring each higher-level action starts from the same current workspace row.

*Call graph*: called by 3 (apply, get, status); 3 external calls (select, workspace_tx, ws_current).


### `core/src/ufo/artifacts.py`

`domain_logic` · `request handling`

An artifact is a file produced during a conversation and then shared so it can be reused or downloaded later. This file is the bridge between those stored shared-file records and the general object system that users can browse with actions like list, get, status, and delete.

The key idea is identity. A shared file is treated as one object per conversation plus filename. If the same conversation shares report.txt three times, that is one artifact with three versions, and the newest version is what users see. If another conversation shares a file with the same name, it is a different artifact. The visible object name combines a short conversation ID prefix with a cleaned-up filename, like a folder label that keeps one conversation’s files grouped together.

When listing artifacts, the file reads visible shared_artifact database rows, groups them by conversation and filename, and summarizes the latest version. Getting an artifact returns its metadata and a link back to the conversation where it was created. Asking for status does more: if the file is small enough, it fetches the stored bytes and writes them into the current workspace under artifacts/<artifact-name>/<filename>, so a later turn can reuse the file. It can also create a temporary download link. Delete removes every stored version and then deletes their bytes from the blob store. Create and update are deliberately refused, because artifacts must come from sharing an actual workspace file.

#### Function details

##### `artifact_object_names`  (lines 67–87)

```
def artifact_object_names(shares: Iterable[tuple[UUID, str]]) -> dict[tuple[UUID, str], str]
```

**Purpose**: Builds stable, human-readable object names for shared files. It keeps files from different conversations separate even when they have the same filename, and adds a short safety suffix only when names would otherwise collide.

**Data flow**: It receives many pairs of conversation ID and filename. It turns each filename into a safe short slug, prefixes it with the first part of the conversation ID, counts duplicate names, and for any collision adds a short hash made from the full identity. It returns a mapping from each original pair to the final object name.

**Call relations**: ArtifactObjects._groups calls this after it has gathered shared files from the database. artifact_object_names relies on _slug to make filenames safe for object names and on _identity_digest when two different identities still produce the same visible name.

*Call graph*: calls 2 internal fn (_identity_digest, _slug); called by 1 (_groups); 1 external calls (Counter).


##### `_slug`  (lines 90–92)

```
def _slug(filename: str) -> str
```

**Purpose**: Turns a filename into a short, safe piece of an object name. This keeps messy names with spaces, punctuation, or uppercase letters from becoming awkward or unsafe labels.

**Data flow**: It takes one filename, lowercases it, replaces runs of non-letter-or-number characters with dashes, trims extra dashes, and limits the result to a fixed length. If nothing useful remains, it returns the fallback word artifact.

**Call relations**: artifact_object_names calls _slug while constructing the visible artifact name. It is the small cleanup step that makes names predictable before collision checking happens.

*Call graph*: called by 1 (artifact_object_names).


##### `_identity_digest`  (lines 95–97)

```
def _identity_digest(identity: tuple[UUID, str]) -> str
```

**Purpose**: Creates a shortable fingerprint for a specific conversation-and-filename pair. This is used only when two different artifacts would otherwise get the same object name.

**Data flow**: It receives a conversation ID and filename, joins them into a string, and runs that through SHA-256, a standard one-way hashing method that produces a fixed-looking digest. It returns the full hexadecimal digest so the caller can take the needed prefix.

**Call relations**: artifact_object_names calls this when duplicate visible names are detected. The digest lets the system keep names unique without exposing a long or complicated full identity.

*Call graph*: called by 1 (artifact_object_names); 1 external calls (sha256).


##### `ArtifactObjects.list`  (lines 115–127)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows the artifacts that the current user context is allowed to see. It produces a page of object rows with a name, a short summary, and a couple of searchable fields.

**Data flow**: It receives a tool context and a list query. It asks _groups for the visible artifact groups, turns each group into an ObjectRow using the latest share for fields like filename and subject, then passes the rows and query to object_page so paging and filtering rules are applied. It returns an ObjectPage.

**Call relations**: The object system calls this when someone lists artifact objects. It depends on _groups to do the database reading and grouping, and on _summary to make each row readable.

*Call graph*: calls 2 internal fn (_groups, _summary); 2 external calls (__init__, object_page).


##### `ArtifactObjects.get`  (lines 129–148)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ArtifactSpec] | None
```

**Purpose**: Returns the main details for one artifact without copying its bytes into the workspace. It tells the caller what the latest shared file is and which conversation created it.

**Data flow**: It receives a context and artifact name. It uses _find to locate the matching group of versions. If none exists, it returns None. Otherwise it takes the newest share as the current version, builds an ArtifactSpec from its filename, media type, and subject, sets created and updated times from the oldest and newest versions, and adds a link to the creating conversation.

**Call relations**: The object system calls this for object_get-style detail lookup. It hands off name lookup to _find, then packages the result into the standard ObjectDetail shape used by the wider object framework.

*Call graph*: calls 1 internal fn (_find); 4 external calls (__init__, __init__, __init__, __init__).


##### `ArtifactObjects.status`  (lines 150–192)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports live status for an artifact and, when possible, copies the latest file bytes back into the workspace for reuse. It can also create a fresh temporary download link.

**Data flow**: It receives a context, artifact name, and an optional expected generation value. It finds the artifact, fetches the latest blob bytes if the file is below the materialization size limit, verifies in the database that the sharing conversation is still visible to this context, writes the bytes into the sandbox workspace if available, and optionally mints a time-limited download URL. It returns a dictionary containing size, share time, turn ID, version count, download URL, and workspace path.

**Call relations**: The object seam calls status as part of fetching an object when it needs side-effect information. status uses _find to locate the artifact, _unchanged_visible to make sure the object did not disappear from view, the blob store to read bytes, the sandbox to write a workspace copy, and mint_artifact_token to make a download link.

*Call graph*: calls 2 internal fn (_find, _unchanged_visible); 4 external calls (__init__, now, mint_artifact_token, workspace_tx).


##### `ArtifactObjects.apply`  (lines 194–203)

```
async def apply(self, ctx: ToolContext, name: str, spec: ArtifactSpec, old: ArtifactSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create or update artifacts through the object interface. This protects the rule that artifacts only exist after a real workspace file has been shared with share_file.

**Data flow**: It receives the requested artifact name, desired spec, old spec, context, and optional generation check. It does not inspect or change stored data. It immediately raises VerbNotSupported with a message telling the caller to use sharing instead.

**Call relations**: The object framework may call apply for create or update operations, but this artifact store is read/delete only. Its only handoff is to the standard VerbNotSupported error so callers get a clear explanation.

*Call graph*: 1 external calls (__init__).


##### `ArtifactObjects.delete`  (lines 205–229)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Deletes an artifact completely, including every version of that conversation-and-filename share. After this, database records are gone and already-created download links no longer work because the stored bytes are deleted too.

**Data flow**: It receives a context, artifact name, and optional expected generation value. It finds all versions for the artifact, opens a workspace database transaction, locks and rechecks that the latest version is still visible, deletes all matching shared_artifact rows for the current workspace, and verifies the expected number of rows was removed. After the database work, it deletes each corresponding blob from storage.

**Call relations**: The object system calls this when a user deletes an artifact. It relies on _find to gather the versions and _unchanged_visible to avoid deleting something that changed or became invisible during the operation. It then uses the database and blob store together so both the records and bytes are removed.

*Call graph*: calls 2 internal fn (_find, _unchanged_visible); 3 external calls (delete, workspace_tx, ws_current).


##### `ArtifactObjects._unchanged_visible`  (lines 231–238)

```
def _unchanged_visible(self, ctx: ToolContext, latest: sa.Row) -> sa.Select
```

**Purpose**: Builds the database check used to confirm that an artifact’s conversation is still visible to the current request. This prevents acting on an artifact after permissions or audience visibility have changed.

**Data flow**: It receives the current context and the latest shared-artifact row. It creates a database select query that looks for the conversation in the current workspace, owned by the selected agent, matching the artifact’s audience, and included in the caller’s readable subjects. It returns the query for the caller to execute.

**Call relations**: status and delete use this as a safety check after finding an artifact. The helper does not run the query itself; it prepares the exact visibility test so those operations can run it inside their own transaction.

*Call graph*: called by 2 (delete, status); 3 external calls (select, object_agent_id, ws_current).


##### `ArtifactObjects._find`  (lines 240–242)

```
async def _find(self, ctx: ToolContext, name: str) -> tuple[sa.Row, ...] | None
```

**Purpose**: Looks up one artifact by its object name. It hides the details of grouping and naming so higher-level methods can simply ask for the named artifact.

**Data flow**: It receives a context and a name. It calls _groups to get all visible artifact groups, searches for the group whose generated name matches the requested name, and returns that group’s sorted versions. If no group matches, it returns None.

**Call relations**: get, status, and delete call _find before doing their specific work. _find in turn calls _groups, so every named operation uses the same visibility, grouping, and naming rules as list.

*Call graph*: calls 1 internal fn (_groups); called by 3 (delete, get, status).


##### `ArtifactObjects._groups`  (lines 244–285)

```
async def _groups(self, ctx: ToolContext) -> Sequence[tuple[str, tuple[sa.Row, ...]]]
```

**Purpose**: Reads visible shared-file records from the database and organizes them into artifact objects. This is the core collector that turns raw share rows into the versioned objects users see.

**Data flow**: It receives a context. It opens a workspace database transaction, selects shared_artifact rows joined to their turns and conversations, and filters them to the current workspace, selected agent, and readable audiences. It groups rows by conversation ID and filename, asks artifact_object_names to assign each group a visible name, sorts versions newest first inside each group, sorts groups by name, and returns the ordered groups.

**Call relations**: list calls _groups to show every artifact, and _find calls it to locate one artifact by name. _groups is where database rows become the higher-level artifact concept used by the rest of this file.

*Call graph*: calls 1 internal fn (artifact_object_names); called by 2 (_find, list); 4 external calls (select, workspace_tx, object_agent_id, ws_current).


##### `_summary`  (lines 288–294)

```
def _summary(shares: tuple[sa.Row, ...]) -> str
```

**Purpose**: Creates the short text shown for an artifact in listings. It gives a quick human-readable snapshot of the latest version.

**Data flow**: It receives the sorted versions for one artifact. It looks at the newest share, formats the filename, media type, byte size, share date, and version count if there is more than one version, then trims the text to the maximum summary length. It returns that summary string.

**Call relations**: ArtifactObjects.list calls _summary while building each ObjectRow. The summary is only display text; the actual artifact details still come from the grouped rows and ArtifactSpec.

*Call graph*: called by 1 (list).


### `core/src/ufo/conversations.py`

`domain_logic` · `request handling`

A conversation in this system is not something users create through the object API. It is created by a chat surface, like the place where the conversation happened, and later may be removed by retention rules. This file gives the rest of the object system a safe way to look up those conversations without allowing anyone to edit them.

The main idea is similar to a library reference card: you can search for a record, read its catalog information, and request a copy of the text if you are allowed to see it, but you cannot rewrite the original book. The file defines the small public shape of a conversation, `ConversationSpec`, which says which surface it came from and what audience may see it. `ConversationObjects` then implements the read actions: list visible conversations, get one by id, and produce status information from its transcript.

Visibility is important. Every database query is limited to the current workspace, the selected agent, and the audiences the caller is allowed to read. When `status` reads the transcript from blob storage, it checks again that the conversation is still visible before writing a text copy into the workspace. This avoids exposing a transcript if permissions changed while the operation was running. Create, update, and delete are all refused with a clear message because conversations belong to surfaces, not this object API.

#### Function details

##### `ConversationObjects.list`  (lines 54–63)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows the caller a paged list of conversations they are allowed to see. Each item includes the conversation id as its name, a short human-readable summary, and the surface it came from.

**Data flow**: It receives a tool context, which includes the caller's readable audiences, and a list query, which describes paging or filtering needs. It asks `_visible_rows` for matching database rows, turns each row into a lightweight object row, and passes those rows through the shared paging helper. The result is an `ObjectPage` ready for the object API to return.

**Call relations**: This is the public list action for the conversation object kind. It relies on `_visible_rows` to do the permission-aware database lookup, then hands the finished rows to `object_page` so conversation listing behaves like other object listings in the system.

*Call graph*: calls 1 internal fn (_visible_rows); 2 external calls (__init__, object_page).


##### `ConversationObjects.get`  (lines 65–76)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ConversationSpec] | None
```

**Purpose**: Fetches the basic details for one conversation, if the caller is allowed to see it. It returns the surface, audience, and timestamps, but not the transcript text.

**Data flow**: It receives the caller context and a name, where the name should be the conversation's UUID text. It uses `_find` to turn that name into a visible database row. If no row is found, it returns `None`; otherwise it wraps the row's surface and audience in `ConversationSpec` and returns an `ObjectDetail` with creation and update times.

**Call relations**: This is the public get action. It delegates all lookup and visibility checks to `_find`, then formats the answer in the standard object-detail shape used by the broader object system.

*Call graph*: calls 1 internal fn (_find); 2 external calls (__init__, __init__).


##### `ConversationObjects.status`  (lines 78–96)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports transcript-related status for a conversation and, when the transcript is small enough, writes a plain text copy into the workspace. This gives callers a safe way to inspect the visible exchange without changing the conversation.

**Data flow**: It receives the caller context, a conversation name, and an expected generation value that is accepted for API consistency but not used here. It finds the visible conversation, reads and formats its transcript through `_exchange`, checks that the same conversation is still visible through `_unchanged_visible`, and then counts the messages and bytes. If there is text and it is under the materialization size limit, it writes a `.txt` transcript file into the workspace and returns the message count, byte size, and file path; if the conversation is missing it returns `None`.

**Call relations**: This is the public status action. It first calls `_find` for the row, then `_exchange` to read the transcript from blob storage, and finally `_unchanged_visible` as a last permission check before writing anything. If that final check fails, it raises `UnknownObject` so the caller does not receive data they should no longer see.

*Call graph*: calls 3 internal fn (_exchange, _find, _unchanged_visible); 1 external calls (__init__).


##### `ConversationObjects.apply`  (lines 98–107)

```
async def apply(self, ctx: ToolContext, name: str, spec: ConversationSpec, old: ConversationSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to create or change a conversation through the object API. Conversations are made by surfaces, so editing them here would break the ownership rules of the system.

**Data flow**: It receives the usual apply inputs: context, name, desired spec, old spec, and expected generation. It does not read or write any conversation data. It immediately raises `VerbNotSupported` with an explanation that conversations are surface-made.

**Call relations**: This is the mutation path the object system would call for create or update requests. Instead of handing work to storage, it stops the flow right away so all conversation changes must come from the surface layer that owns them.

*Call graph*: 1 external calls (__init__).


##### `ConversationObjects.delete`  (lines 109–116)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to delete a conversation through the object API. Removal is controlled elsewhere, such as by retention policy, not by callers treating conversations as editable objects.

**Data flow**: It receives the caller context, conversation name, and expected generation. It performs no lookup and makes no database or blob changes. It immediately raises `VerbNotSupported` with the shared explanation.

**Call relations**: This is the delete path the object system would call for removal requests. It deliberately ends the request before any lower-level storage code is reached, preserving the rule that conversations are not authored or removed through this object kind.

*Call graph*: 1 external calls (__init__).


##### `ConversationObjects._exchange`  (lines 118–138)

```
async def _exchange(self, ctx: ToolContext, conversation_id: UUID) -> tuple[str, ...]
```

**Purpose**: Reads a conversation transcript and turns it into simple text lines like `role: message`. It is used when `status` needs a human-readable copy of the exchange.

**Data flow**: It receives the caller context and a conversation id. It builds the transcript blob key, reads the blob, and decodes it into transcript messages. If the blob is missing, it returns an empty exchange; if the blob exists but cannot be decoded, it raises an error. For each message, it keeps plain string content or extracts text blocks from richer message content, then returns the collected lines as a tuple.

**Call relations**: This helper is called by `ConversationObjects.status` after the database row has been found. It hands back clean text lines, leaving `status` to decide whether those lines should be written into the workspace and summarized for the caller.

*Call graph*: called by 1 (status); 2 external calls (decode, transcript_key).


##### `ConversationObjects._unchanged_visible`  (lines 140–153)

```
async def _unchanged_visible(self, ctx: ToolContext, row: sa.Row) -> bool
```

**Purpose**: Checks that a conversation is still visible to the caller after its transcript has been read. This is a safety check against permission or audience changes during the status operation.

**Data flow**: It receives the caller context and the database row found earlier. It opens a workspace database transaction and asks whether a row still exists in the visible conversation query with the same id and audience. It returns `true` if the row is still visible and unchanged in the relevant way, otherwise `false`.

**Call relations**: This helper is called by `ConversationObjects.status` just before any transcript text is materialized into the workspace. It uses `_visible` to apply the same workspace, agent, and audience limits as the original lookup, giving `status` a final yes-or-no answer.

*Call graph*: calls 1 internal fn (_visible); called by 1 (status); 3 external calls (exists, select, workspace_tx).


##### `ConversationObjects._find`  (lines 155–165)

```
async def _find(self, ctx: ToolContext, name: str) -> sa.Row | None
```

**Purpose**: Looks up one conversation by name while enforcing visibility rules. The name must be a valid UUID, which is the standard text form used for conversation ids.

**Data flow**: It receives the caller context and a name string. It first tries to parse the name as a UUID; if that fails, it returns `None`. If parsing succeeds, it opens a workspace database transaction, runs the visible-conversations query filtered to that id, and returns the one matching row or `None` if there is no visible match.

**Call relations**: `get` and `status` both call this helper at the start of their work. It centralizes the rule that callers can only address conversations in the current workspace, for the selected agent, and within their readable audiences.

*Call graph*: calls 1 internal fn (_visible); called by 2 (get, status); 2 external calls (workspace_tx, UUID).


##### `ConversationObjects._visible_rows`  (lines 167–170)

```
async def _visible_rows(self, ctx: ToolContext) -> tuple[sa.Row, ...]
```

**Purpose**: Fetches all conversation rows the caller is allowed to list. It is the database-reading helper behind the public list action.

**Data flow**: It receives the caller context, opens a workspace database transaction, and runs the shared `_visible` query. It gathers all returned rows and converts them to an immutable tuple before returning them.

**Call relations**: `ConversationObjects.list` calls this helper before turning rows into list entries. `_visible_rows` depends on `_visible` so listing uses the same permission and workspace rules as individual lookup.

*Call graph*: calls 1 internal fn (_visible); called by 1 (list); 1 external calls (workspace_tx).


##### `ConversationObjects._visible`  (lines 172–183)

```
def _visible(self, ctx: ToolContext) -> sa.Select
```

**Purpose**: Builds the database query that defines which conversations are visible to the current caller. This is the central permission filter for this file.

**Data flow**: It reads the current workspace id, the selected object agent id, and the caller's readable audiences from the context. It builds a SQL query selecting the conversation id, surface, audience, and timestamps, limited to matching workspace, matching agent, and allowed audiences. It returns the query object; it does not run the query itself.

**Call relations**: _find, `_visible_rows`, and `_unchanged_visible` all call this helper before touching conversation rows. Because they all share this one query builder, list, get, and status apply the same visibility rules instead of each inventing their own version.

*Call graph*: called by 3 (_find, _unchanged_visible, _visible_rows); 3 external calls (select, object_agent_id, ws_current).


### `core/src/ufo/credential_kind.py`

`domain_logic` · `request handling`

This file is the public “object view” of BYOK credentials, meaning “bring your own key” or secret values that a workspace supplies for an extension. An extension declares that it needs a credential slot, such as an API key. This file turns those declarations into readable workspace objects: each slot appears in lists and detail views whether or not a secret has been stored for it.

The important safety rule is that the secret itself is never read or returned here. Think of it like a locker label on a wall: you can see that locker number 12 exists and whether it is occupied, but you cannot see what is inside. The database row only proves that a value is stored and records timestamps. If there is no row, the slot is still listed, but marked empty, because the slot comes from the extension manifest rather than from the database.

`CredentialObjects` provides the object operations. Listing builds stable object names for slots and marks them filled or empty. Getting one object returns the slot declaration, description, extension name, possible host information, and timestamps if filled. Status returns a small filled/empty answer and, when possible, the resolved host. Applying changes is blocked because filling or rotating a secret must use `request_credentials`, which is a private handoff. Deleting is allowed only for workspace admins and clears the stored value while leaving the declared slot visible.

#### Function details

##### `_slug`  (lines 67–68)

```
def _slug(raw: str) -> str
```

**Purpose**: Turns a raw credential slot name into a simple, URL-like object name. It makes names lowercase, replaces non-letter-or-number runs with dashes, and trims extra dashes from the ends.

**Data flow**: It receives a raw text name. It lowercases the text, uses a regular expression to replace characters outside `a-z` and `0-9` with `-`, then removes leading and trailing dashes. It returns the cleaned-up name string.

**Call relations**: When `CredentialObjects._named` needs stable object names for declared slots, it calls `_slug` first. `_slug` does only the basic cleanup; `_named` adds extra disambiguation if two slots would otherwise get the same cleaned name.

*Call graph*: called by 1 (_named); 1 external calls (sub).


##### `CredentialObjects.list`  (lines 80–93)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Builds the list view for credential slots in a workspace. It shows every declared slot and says, in the summary, whether that slot currently has a stored secret.

**Data flow**: It receives the tool context and a list query, such as paging information. It asks `_named` for the user-facing names of all declared slots, asks `_filled_slots` which slots have database rows, then creates one object row per slot with a readable summary. It returns an object page containing the matching rows.

**Call relations**: This is called when the object system needs to show credential objects. It relies on `_named` to turn extension declarations into stable names, then on `_filled_slots` to compare those declarations with what is actually stored in the workspace. Finally it hands the rows to `object_page` so the common object-list machinery can format and page the result.

*Call graph*: calls 2 internal fn (_filled_slots, _named); 2 external calls (__init__, object_page).


##### `CredentialObjects.get`  (lines 95–119)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[CredentialSpec] | None
```

**Purpose**: Returns the detail view for one credential slot, without exposing the secret value. It tells the caller what the slot is for, which extension declared it, what host information applies, and when it was filled if it has been filled.

**Data flow**: It receives the tool context and an object name. It looks up that name among the declared slots; if no slot matches, it returns `None`. If the slot exists, it opens a workspace database transaction, searches for a credential row for the current workspace and slot, and reads only the creation and update timestamps. It returns an object detail whose specification describes the declaration and whose timestamps are present only when a stored value exists.

**Call relations**: This is used by the object system when someone reads a single credential object. It starts with `_named` because the outside name may be a cleaned or disambiguated version of the real slot name. It then uses the current workspace and a database query to learn whether a row exists, and wraps the safe, non-secret information in `ObjectDetail`.

*Call graph*: calls 1 internal fn (_named); 5 external calls (__init__, __init__, select, workspace_tx, ws_current).


##### `CredentialObjects.status`  (lines 121–145)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns a small machine-readable status for one credential slot. The main answer is whether the slot is filled, and it may also include the resolved host where the credential will be injected.

**Data flow**: It receives the tool context, an object name, and an optional expected generation value. It resolves the object name to a declared slot; if there is no match, it returns `None`. It checks the workspace database for a row for that slot and sets `filled` to true or false. If the slot has host information and a credential store is available, it asks `credential_host` to resolve the actual host and includes that in the result.

**Call relations**: This is called when the object system needs a lightweight state check rather than full details. Like `get`, it begins with `_named` and checks the current workspace database. If host selection is involved, it hands off to `credential_host`, which knows how to turn the slot’s host declaration into the real host answer.

*Call graph*: calls 1 internal fn (_named); 4 external calls (select, credential_host, workspace_tx, ws_current).


##### `CredentialObjects.apply`  (lines 147–156)

```
async def apply(self, ctx: ToolContext, name: str, spec: CredentialSpec, old: CredentialSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses normal create or update attempts for credential objects. This protects secrets by forcing filling and rotation through `request_credentials`, the dedicated private handoff flow.

**Data flow**: It receives the proposed object name, new credential specification, optional old specification, and optional generation check. It does not inspect or store the proposed data. Instead, it raises `VerbNotSupported` with an explanation that credential filling or rotation must happen elsewhere.

**Call relations**: The object system would call this for create or update-style operations. This implementation is intentionally a dead end: it hands back a clear refusal instead of touching the database, so secret changes cannot accidentally pass through ordinary object editing paths.

*Call graph*: 1 external calls (__init__).


##### `CredentialObjects.delete`  (lines 158–174)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Clears the stored value for a credential slot, while leaving the slot itself declared and visible. Only a workspace admin is allowed to do this.

**Data flow**: It receives the tool context, object name, and optional generation check. It first asks the context whether the speaker is a workspace admin. If not, it raises `AdminRequired`. If allowed, it resolves the object name to its declared slot, opens a workspace database transaction, and deletes the credential row for the current workspace and that slot. The output is no returned value; the lasting effect is that the slot becomes empty.

**Call relations**: This is called when someone deletes a credential object. It uses the tool context for the permission check, `_named` to map the public object name back to the real slot name, and the workspace database to remove the stored row. After this, list and status calls will still show the slot, but as empty.

*Call graph*: calls 2 internal fn (_named, speaker_is_admin); 4 external calls (__init__, delete, workspace_tx, ws_current).


##### `CredentialObjects._named`  (lines 176–188)

```
def _named(self) -> dict[str, DeclaredSlot]
```

**Purpose**: Creates stable public object names for all declared credential slots. It also prevents name collisions when two slots clean up to the same simple name.

**Data flow**: It reads the `slots` stored on the `CredentialObjects` instance. For each slot, it uses `_slug` to make a plain name and groups slots with the same plain name. If a group has one slot, that name is used directly. If several slots collide, it appends a short hash made from the extension and slot name, producing distinct names. It returns a dictionary from public name to declared slot.

**Call relations**: This helper sits underneath list, get, status, and delete. Those operations all receive or display public object names, while the actual credential table uses the original declared slot name. `_named` is the bridge between those two worlds.

*Call graph*: calls 1 internal fn (_slug); called by 4 (delete, get, list, status); 1 external calls (sha256).


##### `CredentialObjects._filled_slots`  (lines 190–199)

```
async def _filled_slots(self) -> frozenset[str]
```

**Purpose**: Finds which credential slots currently have stored values in the current workspace. It does not read the secret values themselves.

**Data flow**: It opens a workspace database transaction and selects only the slot names from credential rows belonging to the current workspace. It collects those names into an immutable set. The returned set lets callers test whether each declared slot is filled.

**Call relations**: This helper is called by `CredentialObjects.list` when building the overview page. The list operation already knows all declared slots from `_named`; `_filled_slots` supplies the database-backed filled/empty information needed for each summary.

*Call graph*: called by 1 (list); 3 external calls (select, workspace_tx, ws_current).


### `core/src/ufo/members.py`

`domain_logic` · `request handling`

A workspace needs a safe way to expose its members to tools and agents. This file provides that “member” object. It is not used for inviting or removing people; members join through verified chat surfaces, and deletion is blocked here. Its main job is to let the main agent list members, inspect one member, check simple status, and change the member’s admin flag when allowed.

The file treats a member like an object with a small editable spec: whether the person is an admin. Listing and reading are visibility-aware. If the speaker is not an admin, or is not using the main agent, they can only see their own member record. Admins using the main agent can see everyone.

Changing admin status is deliberately stricter. The speaker must be a real workspace member, must be using the main agent, and must currently be an admin. The code also locks the workspace row during the change, like putting a “do not touch” sign on the record while checking and updating it, so two changes do not accidentally violate safety rules at the same time. It refuses to remove the final admin, and also refuses to remove the final seated admin, meaning the workspace must always keep at least one active admin who has actually taken a seat.

#### Function details

##### `MemberObjects.list`  (lines 40–55)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows the workspace members that the current speaker is allowed to see. It turns database rows into short object-list entries with each member’s id, email, admin/member role, and whether they are seated.

**Data flow**: It receives the current tool context and a list query, asks for the visible member rows, then converts each row into a simple display item. It returns a paged object list, so callers can browse members in the same style as other object kinds.

**Call relations**: When something asks to list member objects, this function starts by calling MemberObjects._visible_rows to enforce who may see what. It then builds ObjectRow entries and hands them to object_page so the general object system can return a normal paginated result.

*Call graph*: calls 1 internal fn (_visible_rows); 2 external calls (__init__, object_page).


##### `MemberObjects.get`  (lines 57–65)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[MemberSpec] | None
```

**Purpose**: Fetches one member object by its stable member id, if the current speaker is allowed to see it. It returns the editable part of the member record, which is only the admin flag, plus timestamps.

**Data flow**: It receives the current context and a member name, where the name is expected to be the member id as text. It looks for a visible row with that id. If none is found, it returns nothing; if found, it builds a MemberSpec from the row’s admin value and returns it with creation and update times.

**Call relations**: The object system calls this when it needs the detailed view of one member. This function relies on MemberObjects._visible_row for the permission-filtered lookup, then wraps the result in ObjectDetail so it matches the shared object interface.

*Call graph*: calls 1 internal fn (_visible_row); 2 external calls (__init__, __init__).


##### `MemberObjects.status`  (lines 67–82)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns a small live status summary for one visible member. This is separate from the editable spec and tells callers the member’s email and whether they are seated.

**Data flow**: It receives the context, member name, and an optional expected generation value. It looks up the visible member row. If there is no visible matching member, it returns nothing; otherwise it returns a small dictionary containing the email and a true-or-false seated value.

**Call relations**: When the object system asks for status, this function again uses MemberObjects._visible_row so it never reveals a hidden member. It does not pass work on to database code directly; the helper has already gathered the rows it may inspect.

*Call graph*: calls 1 internal fn (_visible_row).


##### `MemberObjects.apply`  (lines 84–156)

```
async def apply(self, ctx: ToolContext, name: str, spec: MemberSpec, old: MemberSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Changes an existing member’s admin role, but only under strict safety rules. It is the gatekeeper that prevents unauthorized role changes and prevents a workspace from being left without an active admin.

**Data flow**: It receives the context, the member id as text, the desired MemberSpec, the old spec if one exists, and an optional expected generation value. First it checks that the request comes from the main agent and from a speaking workspace member. It refuses creation, because new members cannot be made through this object. It converts the name to a UUID, opens a workspace database transaction, locks the workspace record, confirms the speaker is still an admin, loads the target member, checks whether the change is needed, and then applies the update if all rules pass. If the request is invalid, it raises a clear error instead of changing the database.

**Call relations**: This is called when the object system applies a desired member spec. It calls ToolContext.agent_is_main to confirm the trusted route, uses workspace_tx for a database transaction, asks member_is_admin to verify the speaker’s authority, and uses SQL queries and updates to read and change the member table. It raises AdminRequired, VerbNotSupported, or UnknownObject when the request should not continue.

*Call graph*: calls 1 internal fn (agent_is_main); 9 external calls (__init__, __init__, __init__, select, update, workspace_tx, member_is_admin, ws_current, UUID).


##### `MemberObjects.delete`  (lines 158–165)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to delete workspace members through the object interface. This keeps member removal out of this path, because membership is controlled elsewhere.

**Data flow**: It receives the context, member name, and optional expected generation value, but it does not read or change any member data. It immediately raises an error explaining that workspace members cannot be deleted through objects.

**Call relations**: The object system may call this when someone tries a delete action on a member object. Instead of delegating anywhere else, it stops the flow by raising VerbNotSupported with the file’s fixed explanation.

*Call graph*: 1 external calls (__init__).


##### `MemberObjects._visible_rows`  (lines 167–185)

```
async def _visible_rows(self, ctx: ToolContext) -> tuple[sa.Row, ...]
```

**Purpose**: Finds the member rows the current speaker is allowed to see. It is the shared visibility filter used before listing or looking up members.

**Data flow**: It starts with a database query for members in the current workspace, ordered by email. If the speaker is not an admin or is not using the main agent, it narrows the query to only the speaker’s own member id; if there is no speaker member id, it returns an empty result. It then runs the query in a workspace transaction and returns the matching rows.

**Call relations**: MemberObjects.list calls this to build the visible member list, and MemberObjects._visible_row calls it before selecting one member from that list. Inside, it checks ToolContext.speaker_is_admin and ToolContext.agent_is_main, uses ws_current to know which workspace is active, and uses workspace_tx to read from the database.

*Call graph*: calls 2 internal fn (agent_is_main, speaker_is_admin); called by 2 (_visible_row, list); 3 external calls (select, workspace_tx, ws_current).


##### `MemberObjects._visible_row`  (lines 187–191)

```
async def _visible_row(self, ctx: ToolContext, name: str) -> sa.Row | None
```

**Purpose**: Finds one visible member row by id text. It is a small helper that keeps single-member lookups consistent with the same visibility rules used for listing.

**Data flow**: It receives the current context and a member name. It first gets all rows visible to the speaker, then searches that set for a row whose id matches the given name as text. It returns the matching row or nothing.

**Call relations**: MemberObjects.get and MemberObjects.status call this when they need one member. Rather than running its own permission logic, it delegates to MemberObjects._visible_rows, so one rule decides what members can be seen everywhere.

*Call graph*: calls 1 internal fn (_visible_rows); called by 2 (get, status).


### Extension object types
Extension-backed objects add connected accounts, memories, synced pages, and saved content sources to the shared workspace object model.

### `extensions/connectors/ufo_ext_connectors/objects.py`

`domain_logic` · `request handling`

A provider connection, such as an account linked through a third-party service, is not created like an ordinary workspace object. It requires outside consent, so this file refuses normal “create” or “apply” attempts and points users toward the proper connect_account flow. Once a connection exists, this file exposes it in two useful views: a connection object for the member-owned account link, and a connector_grant object for one agent’s access to that link.

Think of the connection as the house key, and a connector grant as permission for a specific helper to use that key. Deleting a connection disconnects the account from every agent. Deleting a grant only removes one agent’s access. Changing a grant can only flip whether it is shared or private; it cannot secretly switch to a different provider or account.

The file also builds stable, human-readable names for these objects from the provider and account ID, with a short hash added to avoid name collisions. It uses summaries from the grants system as the source of truth, then wraps them in the generic workspace object system. Permission rules are written into the object classes: owners and admins can disconnect or revoke, while sharing has stricter limits.

#### Function details

##### `_AccountSummary.provider`  (lines 45–45)

```
def provider(self) -> str
```

**Purpose**: This describes the provider name that any account summary must expose, such as the service or platform the account belongs to. It is part of a lightweight contract used by helper code that can work with both connections and grants.

**Data flow**: The helper code receives a summary-like object → it reads this provider value → it can include the provider in names, labels, and identity checks.

**Call relations**: The naming helper _named relies on summaries having this property so it can create consistent object names without caring whether the summary came from connection_summaries or grant_summaries.


##### `_AccountSummary.account_id`  (lines 48–48)

```
def account_id(self) -> str
```

**Purpose**: This describes the account identifier that any account summary must expose. Together with the provider, it tells the system which outside account is being referred to.

**Data flow**: The helper code receives a summary-like object → it reads this account ID → it combines it with the provider to form a stable object identity and display name.

**Call relations**: The _named helper uses this property alongside provider when naming both connection objects and connector grant objects.


##### `_slug`  (lines 51–52)

```
def _slug(raw: str) -> str
```

**Purpose**: This turns a provider name or account ID into a safer, cleaner piece of an object name. It lowercases the text and replaces groups of non-letter-or-number characters with dashes.

**Data flow**: Raw text goes in → punctuation, spaces, and unusual characters are simplified into dashes → a trimmed lowercase name fragment comes out.

**Call relations**: _named calls this twice for each summary, once for the provider and once for the account ID. It uses the cleaned fragments as the readable part of generated object names.

*Call graph*: called by 1 (_named); 1 external calls (sub).


##### `_named`  (lines 55–61)

```
def _named(rows: tuple[SummaryT, ...]) -> dict[str, SummaryT]
```

**Purpose**: This gives each connection or grant summary a stable object name that humans can read and the system can safely distinguish. The name includes a cleaned provider, a cleaned account ID, and a short fingerprint to avoid collisions.

**Data flow**: A group of account summaries goes in → for each one, the function combines provider and account ID, hashes that identity, and builds a name → a dictionary comes out mapping each generated name to its original summary row.

**Call relations**: ConnectionObjects._owned_rows and ConnectorGrantObjects._owned_rows call this when they need to turn raw grant-system summaries into workspace object rows. It delegates the readable cleanup step to _slug and uses a hash to make the final names unique enough even when cleaned text overlaps.

*Call graph*: calls 1 internal fn (_slug); called by 2 (_owned_rows, _owned_rows); 1 external calls (sha256).


##### `ConnectionObjects._owned_rows`  (lines 72–84)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This lists all connected provider accounts as member-owned workspace objects. It is what lets the object system show existing account connections in a consistent object list.

**Data flow**: The current tool context comes in, though the account data is read from connection_summaries → each summary is given a generated name and wrapped with owner information, a short summary, and its generation ID → a tuple of owned object rows comes out.

**Call relations**: The member-owned object framework calls this when it needs the available connection objects. It asks connection_summaries for the raw connection records, passes them through _named for stable names, and returns rows the broader object system can display and authorize.

*Call graph*: calls 1 internal fn (_named); 3 external calls (__init__, __init__, connection_summaries).


##### `ConnectionObjects._detail`  (lines 86–99)

```
async def _detail(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> ObjectDetail[ConnectionSpec] | None
```

**Purpose**: This returns the editable-looking details for one connection object, mainly its provider and account ID plus timestamps. It does not expose secrets; it gives enough information to understand what account is connected.

**Data flow**: A context, object name, and stored owner marker go in → the function looks up the matching connection summary by its generation ID → if found, it returns an ObjectDetail containing a ConnectionSpec and creation/update times; if missing, it returns nothing.

**Call relations**: The object framework calls this when someone asks to inspect a specific connection. It reads from connection_summaries and packages the result as a ConnectionSpec so the generic object system can present it consistently.

*Call graph*: 3 external calls (__init__, __init__, connection_summaries).


##### `ConnectionObjects._status`  (lines 101–114)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: This provides live status information for a connection, such as who owns it, what host it belongs to, and which agents currently have access. It answers the question, “What is this connection doing right now?”

**Data flow**: A context, name, and owner marker go in → the matching connection summary is found by generation ID → a plain JSON-like dictionary comes out with owner, host, and agent information, or nothing if the record has disappeared.

**Call relations**: The object framework calls this when it needs status beyond the basic spec. It uses connection_summaries as the source of truth and returns simple values that can be shown to users or tools.

*Call graph*: 1 external calls (connection_summaries).


##### `ConnectionObjects._apply_owned`  (lines 116–124)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ConnectionSpec, old: ConnectionSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: This deliberately refuses to create or change a connection through the normal object apply path. Connecting an account requires a third-party consent flow, so users must use connect_account instead.

**Data flow**: A requested connection spec and any existing object information go in → the function ignores the requested change and raises a clear “not supported” error → no connection is created or changed.

**Call relations**: The object framework calls this when someone tries to apply a connection object. Instead of handing off to the grants system, it stops the action immediately with VerbNotSupported so the caller is directed to the proper connection flow.

*Call graph*: 1 external calls (__init__).


##### `ConnectionObjects._delete_owned`  (lines 126–136)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: This disconnects a provider account when an allowed user deletes the connection object. Deleting here is broad: it removes the account connection and therefore cuts off every agent grant attached to it.

**Data flow**: A context, object name, and owner marker go in → the function checks that the grants service exists and that there is a speaking member acting on the request → it asks the grants service to disconnect the stored connection generation → if the record changed underneath it, it raises an error instead of pretending success.

**Call relations**: The object framework calls this after the ownership and admin rules allow a delete. It hands the real work to ctx.grants.disconnect, using the speaker member as the actor, so the grants layer can enforce and record the actual disconnection.


##### `ConnectorGrantObjects._admin_can_apply`  (lines 147–148)

```
def _admin_can_apply(self, old: ConnectorGrantSpec, spec: ConnectorGrantSpec) -> bool
```

**Purpose**: This answers a narrow permission question: may an admin apply this grant change? The only admin edit it allows is turning an already shared grant back to private without changing anything else.

**Data flow**: The old grant spec and requested new spec go in → the function compares them while imagining only the shared flag changed to false → it returns true only for that exact “make private” case.

**Call relations**: The member-owned object framework uses this as part of deciding whether an admin is allowed to apply a connector grant change. It supports the file’s rule that admins may reduce sharing, but not share someone else’s connection or rewrite the account details.

*Call graph*: 1 external calls (model_copy).


##### `ConnectorGrantObjects._owned_rows`  (lines 150–165)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This lists the current agent-access grants as workspace objects. Each row represents one agent’s permission to use a connected provider account.

**Data flow**: The tool context comes in, while grant data is read from grant_summaries → each grant is given a stable generated name and labeled as shared or private → a tuple of owned rows comes out with owner, sharing state, and generation ID attached.

**Call relations**: The object framework calls this when it needs to show connector_grant objects. It gets raw grant summaries from grant_summaries, uses _named to build object names, and returns rows that the generic object system can display and protect with ownership rules.

*Call graph*: calls 1 internal fn (_named); 3 external calls (__init__, __init__, grant_summaries).


##### `ConnectorGrantObjects._detail`  (lines 167–184)

```
async def _detail(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> ObjectDetail[ConnectorGrantSpec] | None
```

**Purpose**: This returns the main details for a single connector grant: which provider account it points to and whether it is shared. It lets users inspect an agent’s access without exposing credentials.

**Data flow**: A context, object name, and owner marker go in → the function searches grant_summaries for the matching generation ID → if found, it builds a ConnectorGrantSpec and wraps it with grant and update timestamps; if not found, it returns nothing.

**Call relations**: The object framework calls this when someone inspects a connector_grant object. It translates grant-system summary data into the object system’s standard detail shape.

*Call graph*: 3 external calls (__init__, __init__, grant_summaries).


##### `ConnectorGrantObjects._status`  (lines 186–200)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: This reports live status for a connector grant, including owner, host, agent, and whether the grant is shared. It gives the operational view of who can currently use the connected account.

**Data flow**: A context, object name, and owner marker go in → the matching grant summary is found by generation ID → a plain dictionary comes out with owner, host, agent, and shared state, or nothing if the grant no longer exists.

**Call relations**: The object framework calls this when it needs status for a connector_grant. It reads from grant_summaries and returns simple JSON-like values that can be shown or consumed by tools.

*Call graph*: 1 external calls (grant_summaries).


##### `ConnectorGrantObjects._apply_owned`  (lines 202–237)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ConnectorGrantSpec, old: ConnectorGrantSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: This changes only the sharing flag on an existing connector grant. It refuses attempts to create a grant here or to alter which provider account the grant refers to, because those actions must go through the account connection flow.

**Data flow**: The context, object name, desired spec, old spec, and owner marker go in → the function rejects missing existing data, missing grants support, or no acting member → it reloads the current grant, checks that provider and account ID have not changed, and compares the desired shared value → if the shared value changed, it asks the grants service to update it; otherwise it does nothing.

**Call relations**: The object framework calls this when someone applies a change to a connector_grant object. It uses grant_summaries to check the latest state, raises VerbNotSupported for changes that belong to connect_account, and hands the allowed shared/private flip to ctx.grants.set_shared.

*Call graph*: 4 external calls (__init__, __init__, model_copy, grant_summaries).


##### `ConnectorGrantObjects._delete_owned`  (lines 239–249)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: This revokes one agent’s access to a connected provider account. Unlike deleting a connection, it leaves the underlying account connection and other agents’ grants intact.

**Data flow**: A context, object name, and owner marker go in → the function checks that the grants service is available and that an acting member is present → it asks the grants service to revoke the grant generation → if the grant changed or disappeared during the operation, it raises an error.

**Call relations**: The object framework calls this after delete permissions are satisfied for a connector_grant. It delegates the actual revocation to ctx.grants.revoke so the grants system can remove just this agent’s access edge.


### `extensions/memory/ufo_ext_memory/objects.py`

`domain_logic` · `request handling`

The memory extension stores remembered facts, events, decisions, and similar notes in database rows. This file is the bridge between those rows and the project’s general object system, so a memory found by search can later be opened like a normal object.

The important safety rule is visibility. A memory belongs to a subject, which is the audience or room allowed to see it. Both listing and reading check the caller’s allowed subjects before returning anything. If a memory was created from a page, the code also checks that the page is still the same revision and still belongs to the same subject. This prevents an old derived memory from leaking across rooms or surviving after its source no longer matches.

Listing shows only live memories: rows that have not been replaced by a newer consolidated memory. Reading by id is more forgiving: it can return a superseded memory too, but includes a link to the replacement. That is like finding an old note in a filing cabinet with a sticky note saying, “use this newer version instead.”

The file also defines the shape of a memory object, including its text, kind, confidence, source note, and date. Writes are deliberately blocked here. New or changed memories must go through the separate memory_update path, and old memories end by being superseded, not deleted.

#### Function details

##### `_require_ext`  (lines 57–60)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This small guard makes sure the memory object code was called with its extension context. The extension context is the object that gives access to the memory store, transactions, workspace id, and page state checks.

**Data flow**: It receives a tool context. If that context contains an extension context, it returns it unchanged. If the extension context is missing, it stops immediately with a runtime error, because the rest of this file cannot safely read memory data without it.

**Call relations**: MemoryObjects.list and MemoryObjects.get call this before touching storage. It acts as the front-door check that ensures those read operations have the extension-specific information they need.

*Call graph*: called by 2 (get, list).


##### `MemoryObjects.list`  (lines 69–122)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns a page of current memory items the caller is allowed to see. It is used for browsing live memories, not for semantic recall; search is still the main way to find relevant memories.

**Data flow**: It takes the caller context and a list query. First it gets the extension context, then reads the caller’s allowed subjects. It queries the memory table for rows in the current workspace, limited to those subjects, and skips rows that have been superseded. For memories derived from pages, it checks that each source page still has the expected subject and revision. It turns the surviving rows into short object rows with an id, a body preview, and simple fields such as subject and memory kind, then wraps them in an object page for the caller.

**Call relations**: The object framework calls this when someone lists memory objects. Inside, it relies on _require_ext for extension access, uses SQLAlchemy to ask the database for matching rows, builds ObjectRow entries for display, and hands the final collection to object_page so it follows the system’s normal paging shape.

*Call graph*: calls 1 internal fn (_require_ext); 3 external calls (__init__, select, object_page).


##### `MemoryObjects.get`  (lines 124–186)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[MemorySpec] | None
```

**Purpose**: This opens one memory by id and returns its full readable details, if the caller is allowed to see it. It can also open an old superseded memory so stale references still have a recovery path to the newer replacement.

**Data flow**: It receives a context and a name string. It first tries to read the name as a UUID, which is the id format used for memory rows; if that fails, it returns nothing. It then queries the memory table for that id in the current workspace and among the caller’s allowed subjects. If the row came from a page, it checks that the page still matches the remembered subject and revision. If everything is visible, it builds a MemorySpec containing the body and recall metadata, adds links to the source page and/or replacement memory when present, and returns an ObjectDetail. If any check fails, it returns nothing.

**Call relations**: The object framework calls this when a memory reference is opened. It starts with _require_ext, uses UUID parsing to validate the requested id, uses SQLAlchemy to fetch the row, then builds ObjectRef and ObjectLink values so callers can follow provenance or supersession links. Finally it wraps the MemorySpec in ObjectDetail for the object system.

*Call graph*: calls 1 internal fn (_require_ext); 6 external calls (__init__, __init__, __init__, __init__, select, UUID).


##### `MemoryObjects.status`  (lines 188–195)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This reports that memory objects have no separate status information through this object interface. It exists to satisfy the object store contract.

**Data flow**: It receives the context, object name, and an optional expected generation value. It does not inspect them or change anything. It always returns null, meaning there is no status payload to show.

**Call relations**: This is part of the same object interface as list, get, apply, and delete. No internal caller is shown here; when the wider object system asks a memory object for status, this method simply declines to provide any extra state.


##### `MemoryObjects.apply`  (lines 197–206)

```
async def apply(self, ctx: ToolContext, name: str, spec: MemorySpec, old: MemorySpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This blocks attempts to create or edit memory objects through the generic object apply operation. The project requires memory writes to go through memory_update instead, so the proper validation and indexing flow is used.

**Data flow**: It receives the context, name, proposed memory spec, optional old spec, and optional expected generation. Instead of saving anything, it raises a clear “not supported” error explaining that memories are recorded through memory_update and never applied here.

**Call relations**: This method is present because the object system may try to apply changes to any object kind. For memory objects, it immediately hands back a VerbNotSupported error rather than passing data to the database, protecting the memory store from the wrong write path.

*Call graph*: 1 external calls (__init__).


##### `MemoryObjects.delete`  (lines 208–215)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This blocks attempts to delete memory objects through the generic object delete operation. Memories are retired by being superseded during consolidation, not erased one at a time.

**Data flow**: It receives the context, memory name, and optional expected generation. It does not look up or remove a row. It raises a “not supported” error explaining that memory deletion is not available and that supersession is the intended ending path.

**Call relations**: This method is invoked through the object interface when deletion is requested. Rather than calling storage code, it returns a VerbNotSupported error, keeping memory lifecycle rules consistent with the rest of the extension.

*Call graph*: 1 external calls (__init__).


### `extensions/sources/ufo_ext_sources/pages.py`

`domain_logic` · `request handling`

A “page” here means one document brought in from an outside content source, such as an issue, pull request, or other provider record. This file turns those synced database rows into workspace objects that tools can browse. Without it, synced pages would exist internally, but users and tools would not have a safe, consistent way to list them, inspect them, or remove one from the workspace.

The file draws a clear boundary: pages are produced by the sync driver, not authored by users. So list and get are allowed, create and update are rejected, and delete means “forget this synced page” rather than editing the original outside service. Delete is guarded so only a workspace admin can do it.

The main public shape is `PageSpec`, which describes what a page looks like to callers: source, stream, title, timestamps, visibility subject, digest, blob reference, and a bounded body. The private `_Page` class is the internal wrapper around a synced row. It knows how to turn that row into display fields, a short summary, links back to the source binding, and the final object spec.

`PageObjects` is the object-store adapter. It asks the extension context for pages the current caller is allowed to see, reads page bodies from the blob store when needed, trims very large bodies to a safe size, and closes the stream afterward. Think of it as a library front desk: it can show the catalog, fetch a readable copy, or let an authorized librarian remove an item from circulation.

#### Function details

##### `_require_ext`  (lines 61–64)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This helper makes sure the tool request has an extension context attached. The extension context is the object that gives this file access to source pages and page-forgetting operations.

**Data flow**: It receives a tool context. If the context contains an extension context, it returns it unchanged. If not, it stops the request with a runtime error, because this page object cannot work without that connection.

**Call relations**: The page listing path uses this before reading synced pages, and the delete path uses it before forgetting a page. It acts like a safety check at the door before any extension-specific work happens.

*Call graph*: called by 2 (_pages, delete).


##### `_page_timestamp`  (lines 67–77)

```
def _page_timestamp(provider_value: str | None, row_value: datetime) -> str
```

**Purpose**: This helper turns page timestamps into one standard UTC text format. It accepts either a timestamp supplied by the original provider or, if that is missing, the workspace row timestamp.

**Data flow**: It takes an optional provider timestamp string and a database timestamp. If the provider value is absent, it uses the row value and assumes UTC when needed. If the provider value is present, it parses it, requires it to include a timezone, converts it to UTC, and returns a precise ISO-formatted string.

**Call relations**: The `_Page.spec` and `_Page.fields` methods call this whenever they expose created or updated times. This keeps list results and detailed reads speaking the same time language.

*Call graph*: called by 2 (fields, spec); 2 external calls (fromisoformat, replace).


##### `_Page.name`  (lines 97–98)

```
def name(self) -> str
```

**Purpose**: This property gives the workspace object name for a page. For synced pages, the name is simply the page row’s unique ID written as text.

**Data flow**: It reads the page’s UUID value and converts it to a string. Nothing else is changed.

**Call relations**: Other methods and object handlers use this name when listing pages or matching a user’s requested page name. It is the bridge between an internal row ID and the public object name.


##### `_Page.links`  (lines 100–108)

```
def links(self) -> tuple[ObjectLink, ...]
```

**Purpose**: This method describes the page’s connection back to the source binding that synced it. If the source binding can be named, the page reports that it was “synced_by” that source.

**Data flow**: It reads the stored source name. If there is no source name, it returns no links. If there is one, it builds a link pointing to the source object with that name.

**Call relations**: Detailed page reads include these links so a caller can move from a page to the source that produced it. This gives useful context without duplicating all source details inside the page itself.

*Call graph*: 2 external calls (__init__, __init__).


##### `_Page.spec`  (lines 110–123)

```
def spec(self, body: str, body_truncated: bool) -> PageSpec
```

**Purpose**: This method builds the full public description of a page, including its metadata and the body text that was read from blob storage. It is used when someone asks for one specific page.

**Data flow**: It receives the already-read body text and a flag saying whether the body had to be shortened. It combines those with the page’s stored metadata, normalizes the timestamps, and returns a `PageSpec` object for the caller.

**Call relations**: The get operation reads the body first, then asks `_Page.spec` to package the body together with the page metadata. `_Page.spec` relies on `_page_timestamp` so the timestamps are consistent and validated.

*Call graph*: calls 1 internal fn (_page_timestamp); 1 external calls (__init__).


##### `_Page.summary`  (lines 125–126)

```
def summary(self) -> str
```

**Purpose**: This method creates a short, human-readable label for a page in list views. It shows the title, the source provider and stream, and who can see the page.

**Data flow**: It reads the page title, backend, stream, and subject, formats them into one sentence-like string, and cuts it to a fixed maximum length. It does not change the page.

**Call relations**: The list operation uses this summary for each row it returns. It helps callers scan many pages without opening every page in detail.


##### `_Page.fields`  (lines 128–136)

```
def fields(self) -> dict[str, JsonValue]
```

**Purpose**: This method picks the searchable and sortable metadata fields that should appear in page lists. These are the fields callers can filter or order by.

**Data flow**: It reads the page’s source ID, provider, stream, title, and timestamps. It normalizes the timestamps and returns the values in a simple dictionary.

**Call relations**: The list operation attaches these fields to each returned row. `_Page.fields` calls `_page_timestamp` so list metadata matches the detailed page representation.

*Call graph*: calls 1 internal fn (_page_timestamp).


##### `PageObjects.list`  (lines 147–152)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This method returns a paged list of synced pages the current caller is allowed to see. It gives each page a name, a short summary, and filterable fields.

**Data flow**: It receives the tool context and a list query. It loads visible pages, turns each one into an object row, and passes those rows plus the query into the paging helper, which applies the requested listing behavior and returns an `ObjectPage`.

**Call relations**: This is the main entry for object-list requests for the `page` kind. It depends on `_pages` to collect page records and on each `_Page` to format display data.

*Call graph*: calls 1 internal fn (_pages); 2 external calls (__init__, object_page).


##### `PageObjects.get`  (lines 154–184)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[PageSpec] | None
```

**Purpose**: This method returns the full details for one synced page, including a safely bounded copy of the page body. It prevents very large bodies from being returned all at once.

**Data flow**: It receives the tool context and requested page name. It finds the matching page, opens the body blob stream, reads up to one byte beyond the maximum limit so it can tell whether truncation happened, decodes the bytes as UTF-8 text, and returns an object detail with the spec, timestamps, and links. If the page does not exist, it returns nothing.

**Call relations**: Object-get requests for pages come here. It asks `_find` for the page metadata, reads the body through the context’s blob capability, then asks `_Page.spec` and `_Page.links` to build the final response.

*Call graph*: calls 1 internal fn (_find); 1 external calls (__init__).


##### `PageObjects.status`  (lines 186–193)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This method reports no separate status for page objects. Pages are read-only synced records, so there is no ongoing apply operation to track here.

**Data flow**: It receives the usual status inputs, including context, name, and expected generation, but ignores them and returns `None`. It changes nothing.

**Call relations**: The object framework may ask any object kind for status. For pages, this method is the simple answer: there is no extra status surface beyond listing and reading the page.


##### `PageObjects.apply`  (lines 195–204)

```
async def apply(self, ctx: ToolContext, name: str, spec: PageSpec, old: PageSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This method rejects attempts to create or update a page. Pages must come from the content sync driver, so user-authored changes are not allowed through this object interface.

**Data flow**: It receives the requested name, new spec, optional old spec, and expected generation. Instead of storing anything, it raises a `VerbNotSupported` error with an explanation that pages are synced, not authored.

**Call relations**: When the object framework routes a create or update request for the `page` kind, it reaches this method. The method deliberately ends the flow so callers register or sync sources instead of trying to edit pages directly.

*Call graph*: 1 external calls (__init__).


##### `PageObjects.delete`  (lines 206–218)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This method forgets one synced page, but only if the speaker is a workspace admin. Forgetting tombstones the page so the existing cleanup pipeline can remove derived index data too.

**Data flow**: It receives the tool context and page name. It first checks whether the speaker is an admin. If not, it raises an admin-required error. If the speaker is allowed, it finds the page, fails if no such page exists, then asks the extension context to forget that page ID.

**Call relations**: Delete requests for page objects come here. The method uses `_find` to turn the public name into an internal page, uses `_require_ext` to reach extension operations, and then hands the actual forgetting work to the extension context.

*Call graph*: calls 3 internal fn (speaker_is_admin, _find, _require_ext); 1 external calls (__init__).


##### `PageObjects._find`  (lines 220–221)

```
async def _find(self, ctx: ToolContext, name: str) -> _Page | None
```

**Purpose**: This helper looks up one visible page by its object name. It keeps get and delete from duplicating the same search logic.

**Data flow**: It receives the context and desired page name. It loads the visible pages, compares each page’s name with the requested name, and returns the first match. If none match, it returns `None`.

**Call relations**: Both `PageObjects.get` and `PageObjects.delete` call this before acting on a single page. It relies on `_pages`, so it only finds pages that the current caller is allowed to see.

*Call graph*: calls 1 internal fn (_pages); called by 2 (delete, get).


##### `PageObjects._pages`  (lines 223–250)

```
async def _pages(self, ctx: ToolContext) -> tuple[_Page, ...]
```

**Purpose**: This helper gathers all live synced pages visible to the current caller and wraps them in `_Page` objects. It also attaches source information so pages can show where they came from.

**Data flow**: It receives the tool context, gets the extension context, loads registered sources, builds lookup tables for provider names and source object names, then asks for source pages matching the caller’s readable subjects. Each raw page record is converted into a `_Page` with metadata, blob reference, timestamps, and optional source link name.

**Call relations**: The list path calls this to build all rows, and `_find` calls it before searching for one page. It is the central gathering step that connects the object interface to the extension’s source and page storage.

*Call graph*: calls 1 internal fn (_require_ext); called by 2 (_find, list); 3 external calls (__init__, model_validate, binding_name).


### `extensions/sources/ufo_ext_sources/tools.py`

`domain_logic` · `object requests and page-change hook`

A source is like a standing order to regularly bring content from an outside system into UFO. This file teaches the object system how to list, inspect, create, update, delete, and subscribe to those source bindings. A binding is identified by the provider, account, and tenant URL, so the object name is derived from those facts rather than chosen freely. That prevents two names from pointing at the same real connection.

When someone applies a source manifest, the code checks that the provider exists, the requested streams are valid, the tenant URL is safe and has the expected shape, and the caller has the right account or workspace credential. It then registers one source row per stream. Sources are private by default, but can be shared with the workspace when created or later by the registering member. Other changes, like changing streams, must be done by deleting and recreating the source.

The special field is subscribers. Any member who can see a source may add or remove only their own conversation id. When synced pages change, the hook groups changes by source, filters out private pages, writes a change log into each subscribed conversation’s files, and invokes that conversation with a clear alert message.

#### Function details

##### `_Binding.name`  (lines 155–156)

```
def name(self) -> str
```

**Purpose**: Builds the official object name for a source binding. The name comes from the provider, account, and base URL, so users cannot accidentally invent a second name for the same connection.

**Data flow**: It reads the binding’s provider, account, and base URL → passes them to the shared name-building helper → returns the derived source object name.

**Call relations**: Other parts of this file use binding names when listing, finding, deleting, subscribing, and sending alerts. It relies on the registry helper so source names stay consistent across the extension.

*Call graph*: 1 external calls (binding_name).


##### `_Binding.created_at`  (lines 159–160)

```
def created_at(self) -> datetime
```

**Purpose**: Reports when the binding first came into existence. Because one binding can contain several streams, it uses the oldest stream creation time.

**Data flow**: It reads all stream creation timestamps in the binding → picks the earliest one → returns that timestamp.

**Call relations**: This is used when object detail is built, so a reader sees one sensible creation time for the whole binding rather than separate times for each stream.


##### `_Binding.updated_at`  (lines 163–164)

```
def updated_at(self) -> datetime
```

**Purpose**: Reports the most recent update time for the whole binding. If any stream changed recently, the binding should look recently updated.

**Data flow**: It reads all stream update timestamps → picks the latest one → returns that timestamp.

**Call relations**: This feeds object detail, giving the object system a single “last changed” time for a multi-stream source.


##### `_Binding.spec`  (lines 166–174)

```
def spec(self, subscribers: tuple[str, ...]=()) -> SourceSpec
```

**Purpose**: Turns the internal binding record back into the public source manifest shape. This is what object_get can show to a user or agent.

**Data flow**: It reads provider, stream names, account, base URL, sharing state, and optional subscribers → converts internal values such as the direct-account marker into user-facing fields → returns a SourceSpec.

**Call relations**: Detail and apply logic use this to compare what already exists with what the caller requested. It calls SourceSpec to produce the same model users submit.

*Call graph*: 1 external calls (__init__).


##### `_Binding.summary`  (lines 176–178)

```
def summary(self) -> str
```

**Purpose**: Creates a short human-readable label for a binding. It helps lists and alerts show what the source is without dumping the full configuration.

**Data flow**: It joins the stream names, combines them with provider and account → trims the text to the summary length limit → returns the short string.

**Call relations**: Object listing uses this through owned rows, and alert messages call it so subscribers can quickly recognize the changed source.

*Call graph*: called by 1 (_alert_message).


##### `_require_ext`  (lines 187–190)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Makes sure the current tool call has the extension context it needs. The extension context is the gateway to registered sources, storage, credentials, and files.

**Data flow**: It receives a tool context → checks whether its extension context is present → returns it, or raises an error if this object was dispatched incorrectly.

**Call relations**: Many SourceObjects methods call this before touching source rows, credentials, or subscriber storage. It is a guardrail that catches wiring mistakes early.

*Call graph*: called by 7 (_apply_owned, _bindings, _delete_owned, _detail, _edit_subscribers, _resolved_account, _status).


##### `_require_connectors`  (lines 193–196)

```
def _require_connectors(ctx: ToolContext) -> ConnectorRegistry
```

**Purpose**: Makes sure the current tool call has the connector registry it needs. The connector registry knows which outside-account connection paths are available.

**Data flow**: It receives a tool context → checks whether the connector registry is present → returns it, or raises an error if the runtime failed to provide it.

**Call relations**: SourceObjects._resolved_account calls this while deciding whether a source should use a connected account or a direct workspace credential.

*Call graph*: called by 1 (_resolved_account).


##### `_bindings_from_ext`  (lines 199–228)

```
async def _bindings_from_ext(ext: ExtensionContext) -> tuple[_Binding, ...]
```

**Purpose**: Reconstructs source objects from the lower-level source rows stored by the extension. Several rows, one per stream, are grouped into one user-visible binding.

**Data flow**: It asks the extension context for all source records → ignores records for providers this extension does not know → validates each record’s saved config → groups rows by provider, account, and base URL → returns sorted _Binding objects with their streams.

**Call relations**: SourceObjects._bindings uses it for normal object operations, and on_page_change uses it to match changed pages back to their source binding.

*Call graph*: calls 1 internal fn (sources); called by 2 (_bindings, on_page_change); 3 external calls (__init__, __init__, model_validate).


##### `_subscribers_map`  (lines 231–243)

```
async def _subscribers_map(ext: ExtensionContext, name: str) -> dict[str, str]
```

**Purpose**: Reads the saved subscriber list for one source. It returns which conversations are subscribed and which agent should be invoked for each conversation.

**Data flow**: It reads a value from the extension store under the source’s subscriber key → accepts a well-formed conversation-to-agent dictionary or an empty value → returns a plain dictionary, or raises an error if the stored data is malformed.

**Call relations**: Object detail and status use it to show subscribers, subscription edits use it before writing changes, and the page-change hook uses it to know who to alert.

*Call graph*: called by 4 (_detail, _edit_subscribers, _status, on_page_change).


##### `_store_subscribers`  (lines 246–252)

```
async def _store_subscribers(ext: ExtensionContext, name: str, mapping: dict[str, str]) -> None
```

**Purpose**: Saves or clears the subscriber list for one source. Empty lists are deleted instead of stored as empty records.

**Data flow**: It receives a source name and a conversation-to-agent mapping → writes the mapping to extension storage if it has entries → otherwise deletes the storage key → returns nothing.

**Call relations**: Subscription edits call it after changing the caller’s entry, and deletion calls it to remove stale subscribers when a source is removed.

*Call graph*: called by 2 (_delete_owned, _edit_subscribers).


##### `_binding_identity`  (lines 255–256)

```
def _binding_identity(spec: SourceSpec) -> tuple[str, tuple[str, ...], str, str, bool]
```

**Purpose**: Extracts the parts of a source spec that define the binding itself. Subscribers are deliberately left out because they are an alert preference, not the source connection.

**Data flow**: It receives a SourceSpec → sorts the stream names and collects provider, account id, base URL, and sharing flag → returns that identity tuple.

**Call relations**: SourceObjects.apply uses it to detect when an apply is only changing subscribers. That lets subscription edits bypass the stricter owner-only mutation path.

*Call graph*: called by 1 (apply).


##### `_self_only_change`  (lines 259–266)

```
def _self_only_change(old: tuple[str, ...], new: tuple[str, ...], caller: str) -> None
```

**Purpose**: Enforces the rule that a conversation may only subscribe or unsubscribe itself. This prevents one conversation from silently changing another conversation’s alerts.

**Data flow**: It receives the old subscriber ids, new subscriber ids, and the caller’s id → compares what changed → returns normally if only the caller changed, or raises a clear error if any other id changed.

**Call relations**: SourceObjects.apply calls this before editing subscribers. It is the small safety check that makes subscriber edits visibility-based without becoming a way to control other users.

*Call graph*: called by 1 (apply).


##### `SourceObjects.apply`  (lines 290–313)

```
async def apply(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Handles object_apply for source objects, with a special fast path for subscription-only changes. It lets visible users toggle their own alerts while leaving real source changes under the normal ownership rules.

**Data flow**: It receives the requested object name, new spec, old visible spec, and generation information → compares binding identity → if only subscribers changed, checks the caller may only edit their own id and saves that change → otherwise hands off to the base object apply flow.

**Call relations**: The object system calls this when a source is applied. It calls _binding_identity, _self_only_change, and _edit_subscribers for subscription edits; for actual create/update work it delegates upward to the member-owned object framework.

*Call graph*: calls 3 internal fn (_edit_subscribers, _binding_identity, _self_only_change); 1 external calls (__init__).


##### `SourceObjects._edit_subscribers`  (lines 315–337)

```
async def _edit_subscribers(self, ctx: ToolContext, name: str, desired: tuple[str, ...], caller: str, agent: UUID) -> None
```

**Purpose**: Adds or removes the current conversation from a source’s subscriber map. It also stores the current agent id so future alerts re-enter the same conversation with the same agent.

**Data flow**: It receives the source name, desired subscriber tuple, caller conversation id, and agent id → reads the existing subscriber map → adds or removes only the caller → stores the updated map → rechecks that the source still exists, clearing the map and raising an unknown-object error if it disappeared.

**Call relations**: SourceObjects.apply calls this after proving the subscriber edit is allowed. It uses _subscribers_map and _store_subscribers for storage, _require_ext for extension access, and _find to guard against writes racing with deletion.

*Call graph*: calls 4 internal fn (_find, _require_ext, _store_subscribers, _subscribers_map); called by 1 (apply); 1 external calls (__init__).


##### `SourceObjects._owned_rows`  (lines 339–350)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[ObjectOwner], ...]
```

**Purpose**: Builds the compact rows used when listing source objects. Each row says the source name, a short summary, and who owns or shares it.

**Data flow**: It loads all reconstructed bindings → for each one, derives its name and summary and builds an owner record from its subject and owner member id → returns the tuple of list rows.

**Call relations**: The member-owned object framework calls this when it needs to list or filter sources by visibility. It depends on _bindings to reconstruct user-visible source bindings first.

*Call graph*: calls 1 internal fn (_bindings); 2 external calls (__init__, __init__).


##### `SourceObjects._detail`  (lines 352–363)

```
async def _detail(self, ctx: ToolContext, name: str, _owner: ObjectOwner) -> ObjectDetail[SourceSpec] | None
```

**Purpose**: Builds the full object detail for one source. This is what lets object_get show the exact manifest, timestamps, and subscriber ids.

**Data flow**: It receives a source name → finds the binding → if missing, returns none → otherwise reads the subscriber map, turns the binding into a SourceSpec, and returns that spec with created and updated times.

**Call relations**: The object framework calls this after visibility has been checked. It uses _find for the source, _subscribers_map for alert subscriptions, and ObjectDetail to package the answer.

*Call graph*: calls 3 internal fn (_find, _require_ext, _subscribers_map); 1 external calls (__init__).


##### `SourceObjects._status`  (lines 365–388)

```
async def _status(self, ctx: ToolContext, name: str, _owner: ObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Builds live status information for one source. It tells the caller whether the source is shared, whether this conversation is subscribed, and how each stream is doing.

**Data flow**: It receives a source name → finds the binding → reads subscribers → creates a dictionary with sharing state, the caller’s subscriber id, subscribed yes/no, and per-stream next sync time and error count → includes the private owner id when relevant → returns the status dictionary.

**Call relations**: The object framework calls this for object status output. It uses the same binding lookup and subscriber storage as detail, but returns operational state rather than the source manifest.

*Call graph*: calls 3 internal fn (_find, _require_ext, _subscribers_map).


##### `SourceObjects._apply_owned`  (lines 390–465)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: Creates a new source binding or performs the one allowed ownership-protected change: making a private source shared. It is the main validation and registration path for real source configuration.

**Data flow**: It receives the requested name, source spec, previous spec, and owner information → checks a speaking member exists → rejects initial subscribers → validates provider, streams, base URL, account, and derived name → if the binding exists, refuses identity changes, allows private-to-shared, or no-ops identical specs → if it does not exist, registers one source row per stream with the right subject and account connection.

**Call relations**: The base apply flow calls this after ownership rules are satisfied. It calls _resolved_account to choose authentication, _validated_base_url to make tenant URLs safe, _find to detect existing bindings, and extension methods to register or share source rows.

*Call graph*: calls 4 internal fn (_find, _resolved_account, _require_ext, _validated_base_url); 6 external calls (__init__, __init__, __init__, member_subject, get, binding_name).


##### `SourceObjects._delete_owned`  (lines 467–474)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: Deletes a whole source binding. Since a binding may contain several stream rows, it removes each stream and then clears the subscriber list.

**Data flow**: It receives a source name and owner information → finds the binding → raises an unknown-object error if it is gone → removes every stream’s source row from the extension → deletes the saved subscriber map.

**Call relations**: The member-owned object framework calls this after checking that the caller is the registrar or an admin. It uses _find, _require_ext, and _store_subscribers to remove both sync rows and alert state.

*Call graph*: calls 3 internal fn (_find, _require_ext, _store_subscribers); 1 external calls (__init__).


##### `SourceObjects._resolved_account`  (lines 476–539)

```
async def _resolved_account(self, ctx: ToolContext, spec: SourceSpec) -> _ResolvedAccount
```

**Purpose**: Decides which credential a source will use to authenticate with the outside provider. It chooses between a connected account and a direct workspace credential, and explains what the user must do if neither is ready.

**Data flow**: It receives the tool context and requested source spec → inspects the connector registry, available connected accounts, declared credentials, and requested account_id → validates ownership of connected accounts → returns the chosen account handle plus connection id, or raises a helpful error.

**Call relations**: SourceObjects._apply_owned calls this before registering source rows. It calls connector account and connection methods on the tool context, and uses extension credentials when falling back to direct authentication.

*Call graph*: calls 4 internal fn (connector_accounts, connector_connection, _require_connectors, _require_ext); called by 1 (_apply_owned); 1 external calls (__init__).


##### `SourceObjects._find`  (lines 541–544)

```
async def _find(self, ctx: ToolContext, name: str) -> _Binding | None
```

**Purpose**: Looks up one source binding by its derived name. It provides a simple single-object view over the reconstructed binding list.

**Data flow**: It receives a source name → loads all bindings → returns the first binding whose name matches, or none if there is no match.

**Call relations**: Detail, status, apply, delete, and subscriber editing all call this when they need to confirm a source exists or inspect its current rows. It delegates the actual reconstruction to _bindings.

*Call graph*: calls 1 internal fn (_bindings); called by 5 (_apply_owned, _delete_owned, _detail, _edit_subscribers, _status).


##### `SourceObjects._bindings`  (lines 546–547)

```
async def _bindings(self, ctx: ToolContext) -> tuple[_Binding, ...]
```

**Purpose**: Loads all source bindings visible to this object handler from the extension context. It is the class-level wrapper around the shared reconstruction helper.

**Data flow**: It receives the tool context → extracts the extension context → asks _bindings_from_ext to group source rows into bindings → returns those bindings.

**Call relations**: SourceObjects._find and _owned_rows call this during object lookup and listing. It centralizes the requirement that source object operations must have an extension context.

*Call graph*: calls 2 internal fn (_bindings_from_ext, _require_ext); called by 2 (_find, _owned_rows).


##### `on_page_change`  (lines 550–593)

```
async def on_page_change(ctx: HookContext) -> HookOutcome
```

**Purpose**: Sends alerts when synced pages change for sources that conversations have subscribed to. It makes sure only shared page changes are announced, so private synced content does not leak.

**Data flow**: It receives a hook context with a batch of page changes → rebuilds source bindings and matches each changed source row to a binding → groups changes by binding → reads subscribers → filters to shared changes → writes a change log per subscribed conversation → invokes that conversation with an alert message and an idempotency key to avoid duplicate alerts → returns no special hook outcome.

**Call relations**: The manifest hook system calls this when page changes are produced by sync. It uses _bindings_from_ext, _subscribers_map, _write_change_log, and _alert_message to turn raw page changes into useful subscriber notifications.

*Call graph*: calls 4 internal fn (_alert_message, _bindings_from_ext, _subscribers_map, _write_change_log); 1 external calls (UUID).


##### `_write_change_log`  (lines 596–629)

```
async def _write_change_log(ext: ExtensionContext, conversation_id: UUID, binding: _Binding, latest: str, changes: list[PageChange]) -> str | None
```

**Purpose**: Writes the full list of changed pages into a subscribed conversation’s workspace files. This keeps alert messages short while still giving the agent a file it can inspect for details.

**Data flow**: It receives the extension context, conversation id, binding, latest timestamp, and changes → if file storage is unavailable, returns none → otherwise writes one JSON line per changed page with page reference, stream, title, change type, and timestamp → prunes older files in that directory → returns the written path.

**Call relations**: on_page_change calls this before invoking each subscriber. It calls _disposition to label each change as added, updated, or removed, and uses JSON encoding so the file is easy for tools like jq or grep to search.

*Call graph*: calls 1 internal fn (_disposition); called by 1 (on_page_change); 1 external calls (dumps).


##### `_disposition`  (lines 632–638)

```
def _disposition(change: PageChange) -> str
```

**Purpose**: Classifies a page change as added, updated, or removed. This gives alerts and logs plain words instead of raw timestamp and tombstone details.

**Data flow**: It receives a PageChange → if it is a tombstone, returns removed → otherwise compares creation time to change time → returns added when they match, or updated when they differ.

**Call relations**: _write_change_log uses this for each JSON log line, and _stream_counts uses it to summarize changes in alert text.

*Call graph*: called by 2 (_stream_counts, _write_change_log).


##### `_stream_counts`  (lines 641–655)

```
def _stream_counts(changes: list[PageChange]) -> str
```

**Purpose**: Summarizes how many pages changed in each stream. This gives subscribers the headline before they inspect individual pages.

**Data flow**: It receives a list of page changes → counts added, updated, and removed items per stream → formats those counts into a readable sentence fragment → returns that text.

**Call relations**: _alert_message calls this while building the notification. It relies on _disposition so the summary uses the same labels as the change log.

*Call graph*: calls 1 internal fn (_disposition); called by 1 (_alert_message); 1 external calls (defaultdict).


##### `_alert_message`  (lines 658–677)

```
def _alert_message(binding: _Binding, changes: list[PageChange], log_path: str | None) -> str
```

**Purpose**: Builds the message sent to a subscribed conversation when a source changes. It gives the agent enough context to decide what to read next and what to tell the member.

**Data flow**: It receives the binding, changed pages, and optional change-log path → chooses a detail style: name every page for small batches, point to the log for larger batches, or suggest listing pages if no log exists → combines that with the source summary and per-stream counts → returns the final alert text.

**Call relations**: on_page_change calls this right before invoking each subscribed conversation. It uses _stream_counts for the headline, _page_reference for small batches, and _Binding.summary to make the source recognizable.

*Call graph*: calls 3 internal fn (summary, _page_reference, _stream_counts); called by 1 (on_page_change).


##### `_page_reference`  (lines 680–684)

```
def _page_reference(change: PageChange) -> str
```

**Purpose**: Formats one changed page as an object reference an agent can fetch. It includes a short title so the reference is understandable to a human.

**Data flow**: It receives a PageChange → trims the title to the alert label limit or uses a fallback for untitled pages → returns text like a page object id plus label.

**Call relations**: _alert_message calls this when there are only a few changed pages, so the alert can name them directly instead of sending the reader to a log file.

*Call graph*: called by 1 (_alert_message).


##### `_validated_base_url`  (lines 687–721)

```
def _validated_base_url(provider: str, base_url: str | None) -> str | None
```

**Purpose**: Checks and normalizes tenant API URLs for providers that need them. This protects the sync system from unsafe or unexpected URLs while giving users provider-specific examples.

**Data flow**: It receives a provider and optional base URL → checks whether that connector has a fixed host or needs a tenant URL → parses the URL → rejects usernames, passwords, ports, queries, fragments, non-HTTPS schemes, and hosts or paths outside the provider’s allowed pattern → returns a normalized HTTPS URL or none.

**Call relations**: SourceObjects._apply_owned calls this before registering a source. It uses the provider rules in this file and urlsplit to make sure tenant-specific connectors only sync from safe, expected API locations.

*Call graph*: called by 1 (_apply_owned); 1 external calls (urlsplit).


### Object system foundation
The shared object registry and scoped ownership helper provide consistent validation, permissions, and audited object handling for all object kinds.

### `core/src/ufo/objects.py`

`domain_logic` · `startup and request handling`

A workspace object is like a labeled card in a shared filing cabinet: it has a kind, a name, and a spec, which is the saved content. Different extensions can add new kinds of cards, but this file enforces the common rules before any extension code runs. Without it, each extension would need its own naming rules, validation, permission checks, list paging, and tool wiring, which would make objects inconsistent and unsafe.

The file has three main jobs. First, it defines the shapes of object identities, links, list rows, pages, ownership records, and registered object kinds. Second, it validates a deployment’s object registry at startup, rejecting duplicate kind names, loose spec models that allow unknown keys, specs that cannot be represented as JSON, and secret fields that would be unsafe to echo back. Third, it exposes five user-facing tool verbs through ObjectVerbs: list, get, explain, apply, and delete.

There is also a reusable MemberOwnedObjects base class for object kinds whose rows belong to workspace members. It centralizes visibility and edit rules so every subclass gets the same guardrails. A generation value can act like a numbered ticket: if the row changes between reading and writing, the write is refused instead of silently overwriting newer data.

#### Function details

##### `ObjectRef.validate_kind`  (lines 80–83)

```
def validate_kind(cls, value: str) -> str
```

**Purpose**: Checks that an object reference uses a valid kind name. This prevents malformed or surprising kind labels from entering the system.

**Data flow**: It receives a kind string, compares it with the allowed snake_case-style pattern, and either returns the same string unchanged or raises an error explaining the rule.

**Call relations**: Pydantic calls this automatically when an ObjectRef is built, so every stored or returned object reference passes the same kind-name gate.


##### `ObjectRef.validate_name`  (lines 87–93)

```
def validate_name(cls, value: str) -> str
```

**Purpose**: Checks that an object reference uses a valid object name. Names must be short, lowercase, and hyphen-friendly so they are stable and easy to read.

**Data flow**: It receives a name string, checks its length and pattern, and returns it unchanged if valid. If not, it raises an error that includes the allowed format.

**Call relations**: Pydantic runs this during ObjectRef creation, making sure links and references cannot carry names that the rest of the object system would reject.


##### `ObjectRef.__str__`  (lines 95–96)

```
def __str__(self) -> str
```

**Purpose**: Turns an object reference into the compact human-readable form `kind/name`.

**Data flow**: It reads the reference’s kind and name fields and joins them with a slash. It does not change the object.

**Call relations**: This is used whenever Python needs a string form of ObjectRef, for example in logs, messages, or debugging output.


##### `_ObjectCursor.validate_rank`  (lines 197–202)

```
def validate_rank(self) -> '_ObjectCursor'
```

**Purpose**: Checks that a list cursor’s saved sort value matches the kind of value its rank claims to be. This keeps paging tokens from being corrupted or mismatched.

**Data flow**: It reads the cursor’s rank and value, verifies that rank 0 means an empty placeholder, rank 1 means a boolean stored as an integer, rank 2 means a number, and rank 3 means a string. It returns the cursor if valid or raises an error if not.

**Call relations**: Pydantic calls this when a cursor is decoded inside object_page, so bad continuation tokens are rejected before they affect list paging.


##### `object_page`  (lines 205–284)

```
def object_page(rows: tuple[ObjectRow, ...], query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Applies the shared rules for searching, filtering, sorting, and paging a list of lightweight object rows. Object kinds can provide simple rows and rely on this function for consistent list behavior.

**Data flow**: It receives rows and a query. It checks that row fields are declared and do not collide with reserved names, filters rows by search text and exact field matches, sorts them, applies any cursor boundary, and returns one page plus an optional next cursor.

**Call relations**: MemberOwnedObjects.list calls this after filtering rows by visibility. Inside, it uses object_page.value to read a field from a row, _sortable to turn values into comparable sort keys, and _ObjectCursor to decode or create paging tokens.

*Call graph*: calls 1 internal fn (_sortable); called by 1 (list); 2 external calls (__init__, __init__).


##### `object_page.value`  (lines 229–234)

```
def value(row: ObjectRow, name: str) -> JsonValue
```

**Purpose**: Reads one named value from a list row in the same way for built-in and custom fields.

**Data flow**: It receives a row and a field name. If the field is `name` or `summary`, it returns that built-in value; otherwise it looks in the row’s extra fields.

**Call relations**: This helper lives inside object_page and is used while searching, filtering, sorting, and building cursor boundaries.


##### `_sortable`  (lines 287–300)

```
def _sortable(value: JsonValue, field_name: str) -> tuple[_SortRank, str | int | float]
```

**Purpose**: Converts a list field value into a safe sort key. It defines one predictable ordering for empty values, booleans, numbers, and strings.

**Data flow**: It receives a JSON-like value and the field name it came from. It returns a pair containing a type rank and the value to sort by, or raises an error if the value is a list, object, or other non-scalar value that cannot be ordered cleanly.

**Call relations**: object_page calls this when ordering rows and when comparing rows against a cursor boundary.

*Call graph*: called by 1 (object_page).


##### `ObjectStore.list`  (lines 315–315)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Defines the required listing operation for every object kind’s storage layer. A concrete store implements it to return one page of lightweight rows.

**Data flow**: It receives a tool context and a list query, then the implementing store reads its own backing data and returns an ObjectPage. This protocol method itself only states the contract.

**Call relations**: ObjectVerbs._list eventually calls a registered kind’s implementation of this method after resolving the kind and preparing the correct context.


##### `ObjectStore.get`  (lines 317–317)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SpecT] | None
```

**Purpose**: Defines how an object kind reads one full object by name. A concrete store implements it to return the saved spec and related metadata.

**Data flow**: It receives a tool context and object name. The implementing store looks up that object and returns ObjectDetail if found, or None if absent.

**Call relations**: ObjectVerbs._get, ObjectVerbs._apply, and ObjectVerbs._delete use this contract to read the current object before showing, updating, or deleting it.


##### `ObjectStore.status`  (lines 319–325)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Defines how an object kind reports live status beside the saved spec. Status is separate because it may be computed from current system state rather than stored YAML.

**Data flow**: It receives a context, name, and optional expected generation. The concrete store checks the object and returns a JSON-like status mapping, or None if the object is gone.

**Call relations**: ObjectVerbs._get calls this after get so the user sees both the saved configuration and the current live state.


##### `ObjectStore.apply`  (lines 327–335)

```
async def apply(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Defines how an object kind creates or updates an object after the common envelope and spec have already been validated.

**Data flow**: It receives a context, object name, validated spec, previous spec if any, and optional expected generation. The concrete store writes the change or raises a clear refusal.

**Call relations**: ObjectVerbs._apply calls this as the final mutation step after parsing YAML, validating the name and spec, and checking cross-agent rules.


##### `ObjectStore.delete`  (lines 337–343)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Defines how an object kind deletes an object by name.

**Data flow**: It receives a context, name, and optional expected generation. The concrete store removes the object or raises a refusal if deletion is not allowed.

**Call relations**: ObjectVerbs._delete calls this after first reading the object so it can verify existence and later echo the deleted spec.


##### `MemberOwnedObjects.list`  (lines 399–407)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists only the member-owned rows that the current actor is allowed to see. It keeps private rows out of list results.

**Data flow**: It reads whether the speaker is an admin, the acting member id, and all rows supplied by the subclass. It keeps rows that are shared, owned by the actor, or admin-visible, converts them to lightweight ObjectRow values, and returns a paged result.

**Call relations**: This is the reusable implementation of ObjectStore.list for member-owned kinds. It asks the subclass for _owned_rows, checks each row with _visible, and hands the visible rows to object_page.

*Call graph*: calls 4 internal fn (_owned_rows, _visible, object_page, speaker_is_admin); 1 external calls (__init__).


##### `MemberOwnedObjects.get`  (lines 409–420)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SpecT] | None
```

**Purpose**: Reads one member-owned object only if the current actor is allowed to see it.

**Data flow**: It looks up the row owner, checks visibility, and returns None if the row is missing or hidden. If visible, it asks the subclass for full detail and attaches the owner generation when the row is generation-tracked.

**Call relations**: This implements the common read gate for member-owned ObjectStore.get methods. It relies on _owner, _visible, speaker_is_admin, and _detail.

*Call graph*: calls 4 internal fn (_detail, _owner, _visible, speaker_is_admin); 1 external calls (replace).


##### `MemberOwnedObjects.status`  (lines 422–442)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns live status for a visible member-owned object while guarding against stale reads. It refuses to show status if the object changed since the earlier read.

**Data flow**: It looks up the owner, checks the expected generation, verifies visibility, asks the subclass for status, then checks the owner and generation again before returning the status. If the object is gone or hidden, it returns None or raises not-found as appropriate.

**Call relations**: ObjectVerbs._get uses this through the ObjectStore.status contract after reading object detail. The method uses _owner, _require_current_generation, _visible, and the subclass’s _status hook.

*Call graph*: calls 5 internal fn (_owner, _require_current_generation, _status, _visible, speaker_is_admin); 1 external calls (__init__).


##### `MemberOwnedObjects.apply`  (lines 444–470)

```
async def apply(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Creates or updates a member-owned object while enforcing ownership, admin, speaker, and stale-change rules. It is the main edit gate for member-owned kinds.

**Data flow**: It receives the validated new spec and the previous spec if one existed. It checks whether the row exists, whether the actor can see and edit it, whether an admin exception is allowed, whether a live speaker is required, and whether the generation still matches, then passes the write to _apply_owned.

**Call relations**: This implements the common ObjectStore.apply path for subclasses. ObjectVerbs._apply reaches it through the store interface after parsing and validating the manifest.

*Call graph*: calls 7 internal fn (_admin_can_apply, _apply_owned, _owned, _owner, _require_current_generation, _visible, speaker_is_admin); 2 external calls (__init__, __init__).


##### `MemberOwnedObjects.delete`  (lines 472–490)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Deletes a member-owned object only when the actor is allowed to delete that row.

**Data flow**: It finds the owner, checks the expected generation, rejects missing or hidden rows as not found, checks owner/admin permission and any live-speaker requirement, then asks the subclass to delete the row.

**Call relations**: ObjectVerbs._delete reaches this through the store interface. The method uses _owner, _require_current_generation, _visible, _owned, and the subclass’s _delete_owned hook.

*Call graph*: calls 6 internal fn (_delete_owned, _owned, _owner, _require_current_generation, _visible, speaker_is_admin); 2 external calls (__init__, __init__).


##### `MemberOwnedObjects._owned`  (lines 492–496)

```
def _owned(self, owner: OwnerT, acting: UUID | None) -> bool
```

**Purpose**: Checks whether the acting member is the actual member-owner of a row.

**Data flow**: It receives an owner record and an acting member id. It returns true only when the row has a member owner and that id matches; admin-only rows with no member id are never treated as member-owned.

**Call relations**: _visible uses this to decide read access, while apply and delete use it to decide whether non-admin edits are allowed.

*Call graph*: called by 3 (_visible, apply, delete).


##### `MemberOwnedObjects._visible`  (lines 498–499)

```
def _visible(self, owner: OwnerT, acting: UUID | None, is_admin: bool) -> bool
```

**Purpose**: Decides whether a row should be visible to the current actor.

**Data flow**: It receives the row owner, acting member id, and admin flag. It returns true if the row is shared, owned by the actor, or the speaker is an admin.

**Call relations**: The list, get, status, apply, and delete paths all call this so hidden rows are consistently omitted or treated as not found.

*Call graph*: calls 1 internal fn (_owned); called by 5 (apply, delete, get, list, status).


##### `MemberOwnedObjects._admin_can_apply`  (lines 501–502)

```
def _admin_can_apply(self, old: SpecT, spec: SpecT) -> bool
```

**Purpose**: Allows a subclass to make a narrow exception for admin edits to someone else’s row. The default is conservative and allows no such update.

**Data flow**: It receives the old and new specs and returns false by default. A subclass can override it to return true for safe admin-approved changes.

**Call relations**: MemberOwnedObjects.apply calls this only when an admin is trying to edit a visible row that they do not own.

*Call graph*: called by 1 (apply).


##### `MemberOwnedObjects._require_current_generation`  (lines 504–521)

```
def _require_current_generation(self, name: str, owner: OwnerT | None, expected_generation: UUID | None, action: str) -> None
```

**Purpose**: Prevents an operation from continuing when a generation-tracked row changed after it was read. This avoids acting on stale information.

**Data flow**: It receives the object name, current owner, expected generation, and action word. It compares the current generation with the expected one and raises an error if they differ.

**Call relations**: MemberOwnedObjects.apply, delete, and status call this before mutating or revealing live state.

*Call graph*: called by 3 (apply, delete, status).


##### `MemberOwnedObjects._owner`  (lines 523–524)

```
async def _owner(self, ctx: ToolContext, name: str) -> OwnerT | None
```

**Purpose**: Finds the owner record for one named row.

**Data flow**: It asks the subclass for all owned rows, searches for the requested name, and returns that row’s owner or None if not found.

**Call relations**: The get, status, apply, and delete gates call this before deciding visibility, permissions, or stale-generation checks.

*Call graph*: calls 1 internal fn (_owned_rows); called by 4 (apply, delete, get, status).


##### `MemberOwnedObjects._owned_rows`  (lines 526–527)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[OwnerT], ...]
```

**Purpose**: Subclasses implement this to provide the list of rows and ownership data for their object kind.

**Data flow**: It receives the tool context and should return owned row summaries. In this base class it raises NotImplementedError, meaning it is a required hook.

**Call relations**: MemberOwnedObjects.list calls it to build visible listings, and _owner calls it to find a specific row’s owner.

*Call graph*: called by 2 (_owner, list).


##### `MemberOwnedObjects._detail`  (lines 529–532)

```
async def _detail(self, ctx: ToolContext, name: str, owner: OwnerT) -> ObjectDetail[SpecT] | None
```

**Purpose**: Subclasses implement this to load the full saved detail for a visible row.

**Data flow**: It receives the context, object name, and already-known owner, then should return ObjectDetail or None. The base class only marks it as required.

**Call relations**: MemberOwnedObjects.get calls this after the shared visibility check succeeds.

*Call graph*: called by 1 (get).


##### `MemberOwnedObjects._status`  (lines 534–537)

```
async def _status(self, ctx: ToolContext, name: str, owner: OwnerT) -> dict[str, JsonValue] | None
```

**Purpose**: Subclasses implement this to compute live status for a visible row.

**Data flow**: It receives the context, object name, and owner, then should return a JSON-like status mapping or None. The base class only marks it as required.

**Call relations**: MemberOwnedObjects.status calls this between two generation and visibility checks.

*Call graph*: called by 1 (status).


##### `MemberOwnedObjects._apply_owned`  (lines 539–547)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, owner: OwnerT | None) -> None
```

**Purpose**: Subclasses implement this to perform the actual create or update after the shared permission gate has approved it.

**Data flow**: It receives the context, name, validated new spec, previous spec if any, and owner if the row already existed. It should write the change; the base class raises NotImplementedError.

**Call relations**: MemberOwnedObjects.apply calls this as the final step once ownership, admin, speaker, and generation checks pass.

*Call graph*: called by 1 (apply).


##### `MemberOwnedObjects._delete_owned`  (lines 549–550)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: OwnerT) -> None
```

**Purpose**: Subclasses implement this to perform the actual deletion after the shared permission gate has approved it.

**Data flow**: It receives the context, name, and owner of the row to delete. It should remove the row; the base class raises NotImplementedError.

**Call relations**: MemberOwnedObjects.delete calls this only after existence, visibility, permission, speaker, and generation checks pass.

*Call graph*: called by 1 (delete).


##### `object_registry`  (lines 581–604)

```
def object_registry(bound: tuple[BoundKind, ...]) -> dict[str, BoundKind]
```

**Purpose**: Builds the lookup table of registered object kinds and rejects unsafe or conflicting registrations at startup.

**Data flow**: It receives bound object kinds from core and extensions. It checks each kind name, detects duplicates, validates cross-agent verb declarations, validates each spec model, and returns a dictionary keyed by kind name.

**Call relations**: This is the boot-time gate before ObjectVerbs can serve requests. It delegates detailed spec checks to _validate_spec_model.

*Call graph*: calls 1 internal fn (_validate_spec_model).


##### `_validate_spec_model`  (lines 607–630)

```
def _validate_spec_model(owner: str, kind: ObjectKind) -> None
```

**Purpose**: Checks that an object kind’s spec model is safe to store, render, and echo back to users.

**Data flow**: It receives the registering owner label and object kind. It verifies list fields are real spec fields, every reachable model forbids unknown keys, no field is a secret type, and Pydantic can produce a JSON schema for the spec.

**Call relations**: object_registry calls this for each registered kind. It uses _reachable_models to inspect nested models and _annotation_types to look through type annotations.

*Call graph*: calls 2 internal fn (_annotation_types, _reachable_models); called by 1 (object_registry).


##### `_reachable_models`  (lines 633–647)

```
def _reachable_models(model: type[BaseModel]) -> tuple[type[BaseModel], ...]
```

**Purpose**: Finds the Pydantic models nested inside a spec model. This lets validation rules apply to the whole spec tree, not just the top level.

**Data flow**: It starts with one model, walks through its field annotations, follows any nested BaseModel types it finds, avoids repeats, and returns all discovered models.

**Call relations**: _validate_spec_model calls this before checking each model’s configuration and fields. It uses _annotation_types to unpack nested type hints.

*Call graph*: calls 1 internal fn (_annotation_types); called by 1 (_validate_spec_model).


##### `_annotation_types`  (lines 650–657)

```
def _annotation_types(annotation: object) -> tuple[object, ...]
```

**Purpose**: Flattens a type annotation into the concrete pieces inside it. This helps the validator notice nested models or secret types even when they are wrapped in containers or unions.

**Data flow**: It receives a type annotation, asks Python for its type arguments, and recursively returns the annotation itself or all nested arguments.

**Call relations**: _reachable_models and _validate_spec_model use this helper while inspecting spec model fields.

*Call graph*: called by 2 (_reachable_models, _validate_spec_model); 1 external calls (get_args).


##### `ObjectVerbs.tools`  (lines 723–791)

```
def tools(self) -> tuple[ToolDef, ...]
```

**Purpose**: Creates the five tool definitions that expose the object system to the rest of the application: list, get, explain, apply, and delete.

**Data flow**: It reads the ObjectVerbs instance and returns ToolDef objects with names, descriptions, input models, handler methods, and safety flags.

**Call relations**: The tool registry calls this when wiring available tools. Each ToolDef points back to one of ObjectVerbs._list, _get, _explain, _apply, or _delete.

*Call graph*: 1 external calls (__init__).


##### `ObjectVerbs._list`  (lines 793–825)

```
async def _list(self, ctx: ToolContext, args: ObjectListInput) -> ToolResult
```

**Purpose**: Implements the object_list tool. It either lists all registered kinds or lists visible instances of one kind.

**Data flow**: It receives a tool context and list input. With no kind, it returns kind names and descriptions. With a kind, it resolves the kind, checks any agent target, builds an ObjectListQuery, calls the store’s list method, and returns JSON containing rows and maybe a next cursor.

**Call relations**: This is called by the object_list ToolDef. It uses _resolve, _target, _bound_ctx, object_agent, and _json_result to route the request correctly.

*Call graph*: calls 4 internal fn (_bound_ctx, _resolve, _target, _json_result); 2 external calls (__init__, object_agent).


##### `ObjectVerbs._get`  (lines 827–863)

```
async def _get(self, ctx: ToolContext, args: ObjectGetInput) -> ToolResult
```

**Purpose**: Implements the object_get tool. It reads one object’s saved spec, live status, links, and timestamps.

**Data flow**: It receives a context and get input, resolves the kind, prepares the extension-bound context, checks any agent target, reads detail from the store, asks for status using the observed generation, formats links and timestamps, and returns YAML text.

**Call relations**: This is called by the object_get ToolDef. It uses _resolve, _bound_ctx, _target, and object_agent before calling the store’s get and status methods.

*Call graph*: calls 3 internal fn (_bound_ctx, _resolve, _target); 5 external calls (__init__, __init__, __init__, object_agent, safe_dump).


##### `ObjectVerbs._explain`  (lines 865–878)

```
async def _explain(self, ctx: ToolContext, args: ObjectExplainInput) -> ToolResult
```

**Purpose**: Implements the object_explain tool. It tells a caller how to author objects of a given kind.

**Data flow**: It receives a kind name, resolves it, and returns JSON containing the kind description, guidance text, allowed cross-agent verbs, name rule, and generated JSON schema for the spec.

**Call relations**: This is called by the object_explain ToolDef. It relies on _resolve and _json_result.

*Call graph*: calls 2 internal fn (_resolve, _json_result).


##### `ObjectVerbs._apply`  (lines 880–922)

```
async def _apply(self, ctx: ToolContext, args: ObjectApplyInput) -> ToolResult
```

**Purpose**: Implements the object_apply tool. It creates or updates an object from a strict YAML manifest.

**Data flow**: It receives the manifest text, parses the required kind/name/spec envelope, resolves the kind, checks agent targeting, validates the object name and spec model, reads any existing object to decide create versus update, enforces cross-agent operation support, calls the store’s apply method, and returns a JSON result.

**Call relations**: This is called by the object_apply ToolDef. It coordinates _parse_envelope, _resolve, _target, _validate_name, _bound_ctx, object_agent, the store’s get/apply methods, and _json_result.

*Call graph*: calls 6 internal fn (_bound_ctx, _resolve, _target, _json_result, _parse_envelope, _validate_name); 3 external calls (__init__, __init__, object_agent).


##### `ObjectVerbs._delete`  (lines 924–945)

```
async def _delete(self, ctx: ToolContext, args: ObjectDeleteInput) -> ToolResult
```

**Purpose**: Implements the object_delete tool. It removes one object and returns enough information to understand or potentially restore what was deleted.

**Data flow**: It receives kind and name, resolves the kind, prepares context, checks any agent target, reads the existing object, refuses if missing, calls the store’s delete method with the observed generation, and returns JSON with the deleted spec when visible.

**Call relations**: This is called by the object_delete ToolDef. It uses _resolve, _bound_ctx, _target, object_agent, the store’s get/delete methods, and _json_result.

*Call graph*: calls 4 internal fn (_bound_ctx, _resolve, _target, _json_result); 2 external calls (__init__, object_agent).


##### `ObjectVerbs._resolve`  (lines 947–952)

```
def _resolve(self, kind: str) -> BoundKind
```

**Purpose**: Looks up a registered object kind by name and gives a clear error if it does not exist.

**Data flow**: It receives a kind string, checks the registry mapping, and returns the matching BoundKind. If missing, it raises UnknownKind with the list of registered kinds.

**Call relations**: All five object verb handlers call this before they can dispatch work to a kind’s store.

*Call graph*: called by 5 (_apply, _delete, _explain, _get, _list); 1 external calls (__init__).


##### `ObjectVerbs._bound_ctx`  (lines 954–955)

```
def _bound_ctx(self, ctx: ToolContext, bound: BoundKind) -> ToolContext
```

**Purpose**: Rebinds a tool context so an object kind’s store runs under the extension context that owns that kind.

**Data flow**: It receives the current ToolContext and BoundKind, copies the context with the bound extension context inserted, and returns the new context.

**Call relations**: The list, get, apply, and delete handlers call this before invoking store methods, so extension-owned objects see the right extension environment.

*Call graph*: called by 4 (_apply, _delete, _get, _list); 1 external calls (replace).


##### `ObjectVerbs._target`  (lines 957–1003)

```
async def _target(self, ctx: ToolContext, bound: BoundKind, name: str, verbs: frozenset[AgentTargetVerb]) -> ObjectAgent | None
```

**Purpose**: Checks and resolves an optional target agent for agent-scoped object operations. It enforces that only the main agent, on a live member-requested call, can target another agent.

**Data flow**: It receives the current context, bound kind, requested agent name, and allowed verbs. If no name is given it returns None. Otherwise it checks the kind’s declared support, reads the current and target agents from the workspace database, enforces main-agent and speaker rules, and returns an ObjectAgent for the target.

**Call relations**: ObjectVerbs._list, _get, _apply, and _delete call this before entering the object_agent scope that tells downstream store code which agent is being targeted.

*Call graph*: called by 4 (_apply, _delete, _get, _list); 3 external calls (__init__, select, workspace_tx).


##### `_parse_envelope`  (lines 1006–1024)

```
def _parse_envelope(manifest: str) -> tuple[str, str, Mapping[str, object]]
```

**Purpose**: Parses and validates the YAML wrapper used by object_apply. It makes sure every create or update starts from exactly `kind`, `name`, and `spec`.

**Data flow**: It receives manifest text, rejects oversized input, parses YAML, checks that the result is a mapping with exactly the three required keys, confirms kind and name are strings and spec is a mapping, and returns those three pieces.

**Call relations**: ObjectVerbs._apply calls this before resolving the kind or validating the spec model.

*Call graph*: called by 1 (_apply); 2 external calls (__init__, safe_load).


##### `_validate_name`  (lines 1027–1032)

```
def _validate_name(name: str) -> None
```

**Purpose**: Checks that a new or updated object name follows the shared object-name rule.

**Data flow**: It receives a name string, checks length and pattern, and either returns nothing on success or raises InvalidName on failure.

**Call relations**: ObjectVerbs._apply calls this after parsing the manifest and before any store mutation can happen.

*Call graph*: called by 1 (_apply); 1 external calls (__init__).


##### `_json_result`  (lines 1035–1036)

```
def _json_result(payload: Mapping[str, object]) -> ToolResult
```

**Purpose**: Wraps a Python mapping as a JSON tool result. It is the small shared formatter for object tools that return JSON.

**Data flow**: It receives a payload mapping, serializes it with json.dumps, places the text in TextContent, and returns a ToolResult.

**Call relations**: ObjectVerbs._list, _explain, _apply, and _delete call this when returning structured JSON responses.

*Call graph*: called by 4 (_apply, _delete, _explain, _list); 3 external calls (__init__, __init__, dumps).


### `core/src/ufo/object_scope.py`

`util` · `cross-cutting during audited object handling`

Some parts of the system need to work on behalf of a very specific agent chosen by object dispatch. This file provides a small private “name tag” mechanism for that. Normally, code can ask for the current agent and get the agent attached to the wider running context. But while an audited object handler is processing a particular object, the system may need to say, “for this block of work, use this exact agent instead.”

It does that with a ContextVar, which is like a per-task storage slot. In an asynchronous program, many tasks can run at the same time, so a normal global variable would be unsafe. A ContextVar keeps each task’s value separate, like each worker carrying their own clipboard.

The ObjectAgent data class is the small record stored in that slot: it contains the agent’s unique UUID and human-readable name. The object_agent context manager temporarily puts an ObjectAgent into the slot for the duration of a with block, then restores the previous value afterward. If no target is given, it leaves things alone. The object_agent_id function is the reader: it returns the temporary object agent’s id if one is set, otherwise it falls back to the regular current agent from agent_scope.

#### Function details

##### `object_agent`  (lines 24–32)

```
def object_agent(target: ObjectAgent | None) -> Iterator[None]
```

**Purpose**: Temporarily marks the current task as acting for a specific object-selected agent. Code uses it around a block of work so anything inside that block can discover the intended agent, and the setting is automatically undone afterward.

**Data flow**: It receives either an ObjectAgent or None. If it gets None, it simply lets the wrapped code run without changing anything. If it gets an ObjectAgent, it stores that agent in the task-local slot, runs the wrapped code, and then restores the old slot value when the block exits, even if an error happens.

**Call relations**: This function is used by object-dispatch or audited-handler code when it needs to set a temporary agent target. Later, code inside that protected block can call object_agent_id to read the chosen agent id without needing the agent passed through every function call.


##### `object_agent_id`  (lines 35–37)

```
def object_agent_id() -> UUID
```

**Purpose**: Returns the agent id that object-handling code should use right now. It prefers the temporary object-specific agent if one was set, and otherwise uses the normal current agent.

**Data flow**: It reads the task-local object-agent slot. If that slot contains an ObjectAgent, it returns that object agent’s UUID. If the slot is empty, it calls ufo.agent_scope.agent_current to get the broader current agent and returns that agent’s id.

**Call relations**: This is the lookup point used by code that needs an agent id during audited object work. It depends on object_agent when a temporary target has been set, and otherwise hands off to ufo.agent_scope.agent_current so normal agent tracking still works outside object-specific dispatch.

*Call graph*: 1 external calls (agent_current).


### Agent and member access
Audience, agent-scope, and seat policies determine which members an agent may serve and which users can access agents through the web portal.

### `extensions/web/ufo_ext_web/audience.py`

`domain_logic` · `request handling and admin tool use`

The web portal needs a simple answer to a sensitive question: when a person signs in, which agents should they be allowed to reach? This file makes the web surface the authority for that decision. Access is stored as small records in the web extension’s own storage, one record for each pair of agent and member email address. Think of it like a guest list for each agent: admins can enter every room, while non-admin members only enter rooms where their email is on the list.

The main flow is `web_audience`. It normalizes the member’s email, checks the workspace seat list to see whether that person is an admin, asks the surface for all agents, and then either returns all agents for admins or only the specifically granted agents for everyone else.

The file also defines two chat tools: `grant_web_access` and `revoke_web_access`. Before either tool changes anything, `_gate` checks that the request comes from a real speaking member, that the speaker is a workspace admin, that an email was provided, and that the email belongs to an existing workspace member. If those checks pass, `_grant` writes a grant record and `_revoke` deletes it. Without this file, the web portal would not have a clear, consistent rule for agent visibility, and admins would not have a safe built-in way to change that visibility.

#### Function details

##### `web_extension`  (lines 25–32)

```
def web_extension() -> ExtensionContext
```

**Purpose**: Builds the web extension’s own access handle so code running from the web surface can read and write the web audience records. This matters because the audience list lives in the extension’s private storage area, not in a global shared table.

**Data flow**: It takes no input. It creates a scoped store for the web extension and an empty credential access object, then combines them into an extension context. The result is an `ExtensionContext` that other web code can use to open transactions and reach the web extension’s stored rows.

**Call relations**: When a surface-side handler needs to consult or change the audience list, this helper gives it the right extension context. Internally it constructs the store and credential objects that the rest of this file expects.

*Call graph*: 3 external calls (__init__, __init__, __init__).


##### `_grant_key`  (lines 35–36)

```
def _grant_key(agent_id: UUID, email: str) -> str
```

**Purpose**: Creates the storage key used for one access grant: one agent plus one member email address. It keeps keys consistent so granting and revoking refer to exactly the same stored record.

**Data flow**: It receives an agent ID and an email address. It trims spaces from the email, lowercases it, and joins it with the audience prefix and agent ID into one string key. The output is the exact key used in the extension store.

**Call relations**: `_grant` calls this before writing a grant, and `_revoke` calls it before deleting a grant. Because both use the same helper, a revoke can find the same record that a grant created.

*Call graph*: called by 2 (_grant, _revoke).


##### `granted_emails`  (lines 39–46)

```
async def granted_emails(store: ScopedStore) -> dict[UUID, tuple[str, ...]]
```

**Purpose**: Builds an administrator-friendly view of all web access grants, grouped by agent. Someone would use this to show which email addresses currently have access to each agent.

**Data flow**: It receives a scoped store. It lists every stored item whose key starts with the audience prefix, pulls the agent ID and email address out of each key, groups emails under their agent, sorts each email list, and returns a dictionary from agent ID to a tuple of emails.

**Call relations**: This reads the same stored rows that `_grant` writes and `_revoke` deletes. It does not change the store; it is the read-side companion to the admin tools.

*Call graph*: calls 1 internal fn (list); 1 external calls (UUID).


##### `_granted_agent_ids`  (lines 49–56)

```
async def _granted_agent_ids(store: ScopedStore, email: str) -> frozenset[UUID]
```

**Purpose**: Finds the set of agents that one email address has been explicitly allowed to reach. This is the core lookup for non-admin web users.

**Data flow**: It receives a scoped store and an email address. It trims and lowercases the email, lists all audience grant records, and keeps only the agent IDs whose stored email matches. It returns a frozen set of matching agent IDs, meaning the caller receives a fixed collection that should not be changed.

**Call relations**: `web_audience` calls this after it has learned that the member is not an admin. The returned agent IDs are then used to filter the full agent list down to only the agents that member may see.

*Call graph*: calls 1 internal fn (list); called by 1 (web_audience); 1 external calls (UUID).


##### `WebAudience.allows`  (lines 67–68)

```
def allows(self, agent_id: UUID) -> bool
```

**Purpose**: Answers the simple question: does this already-computed audience include a particular agent? It is a convenient permission check for later web code.

**Data flow**: It receives an agent ID. It looks through the `agents` stored inside this `WebAudience` object and checks whether any agent has that ID. It returns `true` if the agent is included and `false` otherwise.

**Call relations**: This method is used after `web_audience` has built a member’s visible agent list. Instead of recalculating permissions, other code can ask the `WebAudience` object whether a specific agent is allowed.


##### `web_audience`  (lines 71–84)

```
async def web_audience(surface: SurfaceContext, extension: ExtensionContext, email: str) -> WebAudience
```

**Purpose**: Computes one signed-in member’s view of the web portal: whether they are an admin and which agents they can reach. This is the main decision point for web agent visibility.

**Data flow**: It receives the surface context, the web extension context, and the member’s email address. It normalizes the email, opens a transaction to read the workspace seat snapshot, checks whether the matching member is an admin, and asks the surface for all agents. If the member is an admin, it returns all agents. Otherwise, it reads that email’s grant records and returns only the agents whose IDs are granted.

**Call relations**: Web request code calls this when it needs to know what a member can see. It relies on the seat system to identify admins, the surface to list available agents, and `_granted_agent_ids` to apply the guest-list rule for non-admins. It returns a `WebAudience` object that later checks can reuse.

*Call graph*: calls 3 internal fn (transaction, list_agents, _granted_agent_ids); 2 external calls (__init__, __init__).


##### `_refusal`  (lines 95–96)

```
def _refusal(text: str) -> ToolResult
```

**Purpose**: Creates a standard error result for an admin tool that must refuse a request. It keeps refusal messages in the same tool-result shape as successful tool replies.

**Data flow**: It receives plain text explaining why the request is refused. It wraps that text in a text content object and then in a tool result marked as an error. The output is a `ToolResult` that can be returned directly to the caller.

**Call relations**: `_gate` calls this whenever one of its safety checks fails. `_grant` and `_revoke` then return that refusal without changing any access records.

*Call graph*: called by 1 (_gate); 2 external calls (__init__, __init__).


##### `_gate`  (lines 99–117)

```
async def _gate(ctx: ToolContext, extension: ExtensionContext, args: WebAccessInput) -> ToolResult | None
```

**Purpose**: Performs the safety checks that must pass before web access can be granted or revoked. It prevents non-admins, anonymous/non-speaking contexts, blank emails, and unknown workspace members from changing access.

**Data flow**: It receives the tool context, the extension context, and the requested email input. It first checks that there is a speaking member, then asks whether that speaker is an admin, then checks that the email is not blank. It opens a transaction to read the workspace members and confirms that the email belongs to an existing member. If anything fails, it returns an error tool result; if everything passes, it returns `None` to mean the change may continue.

**Call relations**: Both `_grant` and `_revoke` call `_gate` before touching the store. `_gate` uses `_refusal` to format failure messages and uses the seat snapshot to verify that the target email is a real workspace member.

*Call graph*: calls 3 internal fn (transaction, speaker_is_admin, _refusal); called by 2 (_grant, _revoke); 1 external calls (__init__).


##### `_grant`  (lines 120–136)

```
async def _grant(ctx: ToolContext, args: WebAccessInput) -> ToolResult
```

**Purpose**: Implements the `grant_web_access` tool by adding one member email to the current agent’s web guest list. It is the write operation that lets admins make an agent visible to a non-admin member.

**Data flow**: It receives the tool context and the input containing an email and user-facing description. It first requires an extension context, then calls `_gate` to make sure the request is allowed. If refused, it returns that refusal. If allowed, it writes a grant record under the key for the current agent and target email, storing who granted it, and returns a success message naming the normalized email.

**Call relations**: This function is registered as the handler for the `grant_web_access` tool. It depends on `_gate` for permission checks and `_grant_key` for the exact storage location. The records it writes are later read by `web_audience`, `_granted_agent_ids`, and `granted_emails`.

*Call graph*: calls 2 internal fn (_gate, _grant_key); 2 external calls (__init__, __init__).


##### `_revoke`  (lines 139–152)

```
async def _revoke(ctx: ToolContext, args: WebAccessInput) -> ToolResult
```

**Purpose**: Implements the `revoke_web_access` tool by removing one member email from the current agent’s web guest list. It lets admins take away a non-admin member’s portal access to that agent.

**Data flow**: It receives the tool context and the input containing an email and user-facing description. It first requires an extension context, then calls `_gate` to make sure the request is allowed. If refused, it returns that refusal. If allowed, it deletes the grant record for the current agent and target email, then returns a success message naming the normalized email.

**Call relations**: This function is registered as the handler for the `revoke_web_access` tool. It shares the same safety gate as `_grant` and uses `_grant_key` so it deletes exactly the kind of record that `_grant` creates. Once deleted, future `web_audience` checks will no longer include that agent for the member unless they are an admin.

*Call graph*: calls 2 internal fn (_gate, _grant_key); 2 external calls (__init__, __init__).


### `core/src/ufo/agent_scope.py`

`domain_logic` · `cross-cutting`

Some actions in this system belong to a specific agent, and that agent also belongs inside a specific workspace. This file provides a small “current agent” marker that travels with the running task, much like a name badge worn while entering a restricted room. Code that needs to know “which agent am I acting as?” can ask here, instead of passing the agent ID through every function call.

The key idea is an agent scope: a pair of IDs saying “this agent is bound inside this workspace.” The `agent` context manager creates that scope using the currently active workspace, stores it in a `ContextVar` (task-local storage, so separate async tasks do not overwrite each other), and removes it when the `with` block ends.

The file is deliberately strict. If code tries to switch to a different agent while one is already bound, it raises an error. If code asks for the current agent when none is bound, it raises `AgentUnbound` with a helpful message. It also checks that the current workspace still matches the workspace captured when the agent was bound. That prevents a dangerous mix-up where an agent identity leaks across workspace boundaries.

#### Function details

##### `agent`  (lines 28–38)

```
def agent(agent_id: UUID) -> Iterator[AgentScope]
```

**Purpose**: This function creates a temporary agent boundary for a block of code. Someone uses it as `with agent(agent_id):` when the following work should be treated as being done by that specific agent in the current workspace.

**Data flow**: It takes an agent ID as input and reads the current workspace ID from `ws_current()`. It combines those into an `AgentScope`, stores that scope as the current agent, lets the caller’s block run, and then restores the previous value afterward. If a different agent is already bound, it stops immediately with an error instead of allowing an in-place identity switch.

**Call relations**: This is the entry point for setting the ambient agent identity. It calls `ws_current()` to tie the agent to the workspace that is active at the moment the scope begins, then builds an `AgentScope`. Later, code inside the block can call `agent_current` to retrieve the same scope safely.

*Call graph*: 2 external calls (__init__, ws_current).


##### `agent_current`  (lines 41–48)

```
def agent_current() -> AgentScope
```

**Purpose**: This function returns the agent scope that is currently bound. It is used by agent-scoped code that needs to know which agent and workspace it is allowed to act under.

**Data flow**: It reads the current stored agent scope. If there is none, it raises `AgentUnbound` to explain that the caller forgot to wrap the work in `with agent(agent_id):`. If there is a scope, it also reads the current workspace through `ws_current()` and checks that it still matches the scope’s workspace. When everything is consistent, it returns the `AgentScope`.

**Call relations**: This is the companion to `agent`. After `agent` has placed an agent identity into the task-local context, `agent_current` is how other code retrieves it. It also calls `ws_current()` so it can catch cases where the workspace changed while the agent binding was still active.

*Call graph*: 2 external calls (__init__, ws_current).


### `core/src/ufo/seats.py`

`domain_logic` · `request handling and cross-cutting admission checks`

A “seat” is permission for a workspace member to talk to the agent. This file is the central rulebook for those permissions. Without it, one part of the product might let a person speak while another part refuses them, or billing-related limits could be skipped by accident.

The main idea is simple: a workspace may have no seat limits, in which case everyone is admitted. If limits are set, only members with a recorded seat are admitted. New members are still created even when no seat is available, because the system still needs to remember who they are. They just receive a refusal message until an admin grants them a seat.

The `Seats` class represents the seat state for one workspace. It can check whether the workspace is gated, decide whether a member is admitted, create a snapshot for reporting, grant or revoke seats, set limits, and automatically seat new members while the included allowance is not full. It uses database locks when counting seats, like taking a numbered ticket at a counter, so two simultaneous grants cannot both think the last seat is free.

A few safety rules matter. The last seated admin cannot be unseated, because seat management happens through chat and someone must remain able to fix things. The file also keeps a short-lived memory of workspaces with no seat limit, so the common unlimited case avoids extra database checks.

#### Function details

##### `gate_member`  (lines 44–58)

```
def gate_member(speaker_member_id: UUID | None, admission_source: TurnAdmissionSource, on_behalf_of_member_id: UUID | None) -> UUID | None
```

**Purpose**: Chooses which member a turn should be checked against for seat permission. Usually this is the speaker, but for a scheduled task it is the member the task is acting for.

**Data flow**: It receives a possible speaker member ID, the source of the turn, and a possible “on behalf of” member ID. It returns the speaker when there is one, otherwise returns the creator for scheduled work, otherwise returns nothing for internal turns that are not tied to a member.

**Call relations**: This is the shared definition of “who is being gated.” Admission, scheduled work, resumed turns, and per-round checks can all use the same answer instead of each inventing their own version.


##### `seat_gate_absent`  (lines 61–67)

```
def seat_gate_absent(workspace_id: UUID) -> bool
```

**Purpose**: Quickly answers whether the system recently learned that a workspace has no seat gate at all. This saves a database lookup for the common case where seats are unlimited.

**Data flow**: It takes a workspace ID and looks in a small in-memory cache for an unexpired note saying that this workspace had no seat limit or included-seat rule. It returns true only if that note still has time left.

**Call relations**: Per-round enforcement can ask this before going to the database. The cache entries it reads are written by `_note_absent_limit` after `Seats.gated` or `Seats.admits` confirms the workspace is unlimited.

*Call graph*: 1 external calls (monotonic).


##### `_note_absent_limit`  (lines 70–75)

```
def _note_absent_limit(workspace_id: UUID) -> None
```

**Purpose**: Records, for a few seconds, that a workspace has no seat-related limits. This is a small performance shortcut, not the source of truth.

**Data flow**: It receives a workspace ID, checks the current clock, clears out expired cache entries if the cache is full, and stores a new expiry time for that workspace. It changes only the in-memory cache.

**Call relations**: `Seats.gated` and `Seats.admits` call this after reading the database and finding both seat settings empty. Later, `seat_gate_absent` uses that note to skip a repeated database trip.

*Call graph*: called by 2 (admits, gated); 1 external calls (monotonic).


##### `SeatSnapshot.seated`  (lines 104–105)

```
def seated(self) -> int
```

**Purpose**: Counts how many members in a seat snapshot currently have seats. It is a convenience property for reports or tools that show seat usage.

**Data flow**: It reads the snapshot’s member list, counts entries marked as seated, and returns that number. It does not change the snapshot.

**Call relations**: `Seats.snapshot` builds the `SeatSnapshot`; callers can then ask this property for the total seated count without re-counting by hand.


##### `Seats.gated`  (lines 116–130)

```
async def gated(self, connection: AsyncConnection) -> bool
```

**Purpose**: Checks whether this workspace enforces seats at all. If both the hard limit and included-seat allowance are unset, the workspace is treated as unlimited.

**Data flow**: It takes a database connection, reads the workspace’s seat settings, and returns false when both settings are empty. In that unlimited case it also writes a short-lived cache note; otherwise it returns true.

**Call relations**: This is used when code only needs to know whether seat rules apply. It hands off to `_note_absent_limit` to support the fast path used by later checks.

*Call graph*: calls 1 internal fn (_note_absent_limit); 2 external calls (execute, select).


##### `Seats.admits`  (lines 132–159)

```
async def admits(self, connection: AsyncConnection, member_id: UUID) -> bool
```

**Purpose**: Decides whether a specific member is allowed to be answered by the agent in this workspace. Unlimited workspaces admit everyone who matches a member row; limited workspaces require that the member is seated.

**Data flow**: It receives a database connection and a member ID. It looks up that member together with the workspace’s seat settings. If no such member exists, it returns false. If the workspace is unlimited, it records the cache shortcut and returns true. Otherwise it returns whether the member has a seat timestamp.

**Call relations**: Admission checks call this when a member tries to speak or when a running turn is checked again. It uses `_note_absent_limit` when it discovers the unlimited case.

*Call graph*: calls 1 internal fn (_note_absent_limit); 2 external calls (execute, select).


##### `Seats.snapshot`  (lines 161–191)

```
async def snapshot(self, connection: AsyncConnection) -> SeatSnapshot
```

**Purpose**: Builds a read-only picture of the current seat state for a workspace. This is useful for admin views, billing tools, or reports.

**Data flow**: It reads the workspace’s limit settings, then reads all members in creation order with their email, seat status, and admin flag. It packages that information into `SeatEntry` records inside a `SeatSnapshot`.

**Call relations**: This is the reporting side of the seat system. Unlike grant or revoke, it does not change anything; it gives other code a clear summary to show or inspect.

*Call graph*: 4 external calls (__init__, __init__, execute, select).


##### `Seats.grant`  (lines 193–205)

```
async def grant(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Gives a seat to the member with the given email address. It refuses if the workspace has a hard seat limit and all seats are already taken.

**Data flow**: It receives a database connection and an email. It locks the workspace’s seat settings, finds the matching member, returns immediately if they already have a seat, checks the seated count against the limit, and then writes a seat timestamp if allowed. If there is no room, it raises `SeatLimitReached`.

**Call relations**: Admin tools or billing extensions call this when an explicit seat grant is approved. It relies on `_locked_limits`, `_member_by_email`, `_seated_count`, and `_seat` so the same counting and writing rules are reused.

*Call graph*: calls 4 internal fn (_locked_limits, _member_by_email, _seat, _seated_count); 1 external calls (__init__).


##### `Seats.revoke`  (lines 207–222)

```
async def revoke(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Removes a seat from the member with the given email address. It is careful not to remove the last seated admin, because admins need chat access to manage seats.

**Data flow**: It receives a database connection and an email. It locks the workspace limits, finds the member, returns if they are already unseated, checks whether they are the only seated admin, and either raises `LastAdminSeatRevocation` or clears their seat timestamp in the database.

**Call relations**: Admin tools call this to free a seat. Running conversations are not directly edited here; later admission or per-round enforcement sees the revoked seat and refuses or parks the turn.

*Call graph*: calls 3 internal fn (_locked_limits, _member_by_email, _seated_admin_count); 3 external calls (__init__, execute, update).


##### `Seats.ensure_limit`  (lines 224–236)

```
async def ensure_limit(self, connection: AsyncConnection, limit: int) -> None
```

**Purpose**: Sets the workspace’s hard seat limit once, but only if it has not already been set. This prevents a repeated setup task from overwriting a deliberate operator change.

**Data flow**: It receives a database connection and a positive limit. If the limit is less than one, it raises an error. Otherwise it updates the workspace row only where the existing seat limit is still empty.

**Call relations**: Setup code or a billing extension can call this when a seat limit becomes known. It does not call other local helpers because it is a direct one-time database write.

*Call graph*: 2 external calls (execute, update).


##### `Seats.ensure_included`  (lines 238–250)

```
async def ensure_included(self, connection: AsyncConnection, included: int) -> None
```

**Purpose**: Sets the number of seats that can be handed out automatically, but only if that value is not already set. This represents the plan’s included allowance.

**Data flow**: It receives a database connection and a positive included-seat count. If the count is less than one, it raises an error. Otherwise it writes the value only when the workspace’s included-seat field is still empty.

**Call relations**: Billing or setup code uses this when establishing a plan allowance. `Seats.auto_seat` later uses this value to decide whether a new member can be silently seated.

*Call graph*: 2 external calls (execute, update).


##### `Seats.auto_seat`  (lines 252–261)

```
async def auto_seat(self, connection: AsyncConnection, member_id: UUID) -> None
```

**Purpose**: Automatically gives a new member a seat if the free included allowance is not full. If the allowance is full, it leaves the member unseated instead of blocking member creation.

**Data flow**: It receives a database connection and member ID. It locks and reads the workspace’s limit values, chooses the included allowance if present or the hard limit otherwise, checks how many members are already seated, and seats the new member only if there is room or no bound applies.

**Call relations**: `create_member` calls this right after a new member row is inserted. It uses `_locked_limits`, `_seated_count`, and `_seat` so automatic seating follows the same safe counting rules as manual grants.

*Call graph*: calls 3 internal fn (_locked_limits, _seat, _seated_count).


##### `Seats._locked_limits`  (lines 263–271)

```
async def _locked_limits(self, connection: AsyncConnection) -> tuple[int | None, int | None]
```

**Purpose**: Reads the workspace’s seat limit values while locking the workspace row. The lock makes concurrent seat changes line up instead of racing each other.

**Data flow**: It receives a database connection, selects the workspace’s hard limit and included-seat allowance with a database row lock, and returns those two values.

**Call relations**: `Seats.grant`, `Seats.revoke`, and `Seats.auto_seat` call this before making decisions that depend on current seat counts. It is the guardrail that keeps those decisions consistent under simultaneous requests.

*Call graph*: called by 3 (auto_seat, grant, revoke); 2 external calls (execute, select).


##### `Seats._member_by_email`  (lines 273–290)

```
async def _member_by_email(self, connection: AsyncConnection, email: str) -> tuple[UUID, datetime | None, bool]
```

**Purpose**: Finds a workspace member by email address and returns the details needed for seat changes. It treats email comparison case-insensitively after trimming the input.

**Data flow**: It receives a database connection and email address, searches this workspace’s member table for a matching email, and returns the member ID, current seat timestamp, and admin flag. If no member matches, it raises `UnknownMember`.

**Call relations**: `Seats.grant` and `Seats.revoke` call this before changing a member’s seat. It centralizes the lookup so both actions use the same email matching behavior and error.

*Call graph*: called by 2 (grant, revoke); 3 external calls (__init__, execute, select).


##### `Seats._seated_count`  (lines 292–300)

```
async def _seated_count(self, connection: AsyncConnection) -> int
```

**Purpose**: Counts how many members in this workspace currently have seats. This is used before adding another seat.

**Data flow**: It receives a database connection, counts member rows for this workspace where the seat timestamp is present, and returns that number.

**Call relations**: `Seats.grant` uses this to enforce the hard limit. `Seats.auto_seat` uses it to decide whether the automatic allowance still has space.

*Call graph*: called by 2 (auto_seat, grant); 2 external calls (execute, select).


##### `Seats._seated_admin_count`  (lines 302–311)

```
async def _seated_admin_count(self, connection: AsyncConnection) -> int
```

**Purpose**: Counts how many seated admins remain in a workspace. This protects the system from leaving a workspace with no seated admin.

**Data flow**: It receives a database connection, counts members in this workspace who are both seated and marked as admins, and returns that number.

**Call relations**: `Seats.revoke` calls this only when the target member is an admin. If the count is one, revocation is refused so someone can still manage seats through chat.

*Call graph*: called by 1 (revoke); 2 external calls (execute, select).


##### `Seats._seat`  (lines 313–318)

```
async def _seat(self, connection: AsyncConnection, member_id: UUID) -> None
```

**Purpose**: Marks a member as seated in the database. It is the shared low-level write used by both manual grants and automatic seating.

**Data flow**: It receives a database connection and member ID, then updates that member row with the current time for `seated_at` and `updated_at`. It does not return a value.

**Call relations**: `Seats.grant` calls this after an admin-approved grant passes the limit check. `Seats.auto_seat` calls it when a newly created member fits within the automatic allowance.

*Call graph*: called by 2 (auto_seat, grant); 2 external calls (execute, update).


##### `member_is_admin`  (lines 321–331)

```
async def member_is_admin(connection: AsyncConnection, workspace_id: UUID, member_id: UUID) -> bool
```

**Purpose**: Checks whether a particular member is an admin of a particular workspace. This is a small permission helper.

**Data flow**: It receives a database connection, workspace ID, and member ID. It reads the member’s admin flag for that workspace and returns true or false; if no row is found, the result is false.

**Call relations**: Other permission checks can call this before allowing admin-only seat actions or workspace-level operations. It does not change any data.

*Call graph*: 2 external calls (execute, select).


##### `create_member`  (lines 334–374)

```
async def create_member(connection: AsyncConnection, workspace_id: UUID, email: str, *, is_admin: bool=False) -> UUID
```

**Purpose**: Creates a workspace member row in the one approved way, then applies the seat rule immediately. This ensures every path that adds members uses the same automatic seating behavior.

**Data flow**: It receives a database connection, workspace ID, email, and optional admin flag. It tries to insert a new member with a fresh UUID. If the insert succeeds, it calls `Seats.auto_seat` and returns the new ID. If another request already created the same workspace/email member, it reads and returns the existing member ID instead.

**Call relations**: Onboarding, verified teammate joins, and future member-creation paths should call this instead of inserting directly. It constructs `Seats(workspace_id)` so new members are seated or left unseated according to the shared policy.

*Call graph*: 4 external calls (__init__, execute, select, uuid4).


##### `admin_conversation`  (lines 377–408)

```
async def admin_conversation(connection: AsyncConnection, workspace_id: UUID) -> tuple[UUID, UUID] | None
```

**Purpose**: Finds the best chat conversation to contact a seated workspace admin. This is used when the system needs to ask an admin to grant a seat or handle a workspace-level issue.

**Data flow**: It receives a database connection and workspace ID. It searches private conversations for seated admins on the main agent, orders them by most recently updated, and returns the conversation ID plus agent ID. If no such conversation exists, it returns nothing.

**Call relations**: Seat-refusal or billing-related flows can use this to route an admin request. It only reads data; it does not create a conversation if none exists.

*Call graph*: 2 external calls (execute, select).


##### `member_workspaces`  (lines 411–419)

```
def member_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a candidate list of workspaces that have at least one member. This is useful for background jobs that report or check seat state across workspaces.

**Data flow**: It creates a small query function that selects distinct workspace IDs from the member table, then wraps it as `WorkspaceCandidates`, the project’s way of describing which workspaces a job should run for.

**Call relations**: Extensions or scheduled jobs can declare these candidates without knowing the details of the member table. Inside, it hands the query builder to `owner_candidates`.

*Call graph*: 1 external calls (owner_candidates).


##### `member_workspaces.with_a_member`  (lines 416–417)

```
def with_a_member() -> sa.Select[tuple[UUID]]
```

**Purpose**: Defines the actual database query used by `member_workspaces`: every workspace ID that appears in the member table, with duplicates removed.

**Data flow**: It takes no direct input. When called, it builds and returns a SQL select statement for distinct member workspace IDs; it does not execute the query itself.

**Call relations**: `member_workspaces` passes this inner function to `owner_candidates`, which can later use it as the candidate source for a workspace-level job.

*Call graph*: 1 external calls (select).

## 📊 State Registers Touched

- `reg-database-schema` — The shared database layout and migration version that define which long-term records the system can store.
- `reg-workspace-boundary` — The current workspace or tenant boundary used to keep each customer’s data and actions separate.
- `reg-extension-store` — Per-workspace saved extension data that add-ons use to remember their own small pieces of state.
- `reg-credential-store` — The encrypted store of API keys, service secrets, and owner-provided credentials.
- `reg-auth-session` — The signed login and identity state that proves which member or operator is using the system.
- `reg-workspace-objects` — The shared records for workspaces, agents, members, conversations, artifacts, memories, sources, and other workspace objects.
- `reg-membership-and-seats` — The shared membership, admin role, paid seat, and seat-limit state for a workspace.
- `reg-agent-identity` — The saved identity and settings of each agent, including its main workspace role and whether it may use the internet.
- `reg-audience-policy` — The saved visibility rules that decide which people may see or use a conversation or agent.
- `reg-conversation-state` — The durable conversation record that ties a surface, agent, audience, sandbox handle, and message history together.
- `reg-skill-store` — The shared set of built-in, extension-provided, and user-created skills available to agents.
- `reg-connector-connections` — The saved external accounts, OAuth connections, and agent grants that let tools use outside services safely.
- `reg-source-sync-state` — The saved state of external sources, synced pages, deletion markers, cursors, and retry backoff.
- `reg-memory-store` — The long-term memory store of remembered facts and recall results used to inform later responses.
- `reg-artifact-storage` — The shared file and blob storage for generated artifacts, plus the signed download state used to protect them.
- `reg-onboarding-claims` — The hosted signup state for email claims, invitations, company-domain workspace mapping, and temporary access tokens.
- `reg-acting-principal-scope` — The current acting principal context—member, agent, on-behalf-of member, and object/agent scope—used to authorize actions, attribute turns, choose grants, and keep tool work tied to the right actor.
- `reg-secret-handoff-state` — Short-lived private handoff state used to bind sensitive credential or connection setup payloads to the right actor before secrets are accepted and stored.

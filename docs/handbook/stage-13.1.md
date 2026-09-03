# Built-in workspace, member, agent, conversation, artifact, and credential objects  `stage-13.1`

This stage is shared behind-the-scenes support for the workspace. It defines the built-in objects, meaning records that chat tools can list, inspect, or sometimes change. Together they are the system’s “control panel” for a workspace.

The workspace object shows basic workspace facts, such as members and seats, but cannot be edited directly. Member objects say who belongs, who is an admin, and who has access; admins can also add a person before they first sign in. Agent objects represent the assistants in the workspace and control who may create, edit, archive, or restore them. Prompt governance adds a safety gate: prompt changes become proposals and are applied only after approval, and only if the prompt has not changed meanwhile.

Conversation objects let the system find past chats and show visible transcripts, while artifact objects manage files shared from those chats with access checks. Credential objects show which secret slots extensions need, but never reveal the secret values. Surface objects show connected chat entry points, and extension objects show what loaded extensions contribute without allowing install or removal through these tools.

## Files in this stage

### Extension inventory
Read-only objects describe loaded extensions and what they contribute without allowing install or removal operations.

### `core/src/ufo/host/ext/extension_kind.py`

`domain_logic` · `startup and object request handling`

This file turns the set of active extension manifests into something the workspace object system can show to a user. An extension manifest is the extension’s declaration: its name, version, tools, object kinds, credential slots, chat surfaces, jobs, hooks, sources, and subagent profiles. Think of it like a public menu for each installed extension: it lists what is available, but it does not contain private values such as secrets.

The file first defines how extension names become object names. Manifest names are made lowercase, non-letter characters become hyphens, and duplicate rendered names are rejected early. This matters because two extensions must not accidentally appear under the same object name.

The main class, ExtensionObjects, provides the object operations. Listing extensions returns one row per extension, with a short summary and useful counts. Getting one extension returns its full declaration. Asking for status returns what the extension requires from the deploy, such as sandbox internet access or required seams from other extensions.

All write operations are deliberately refused. Installing or removing an extension is a deploy-level change made through the lockfile and command-line tooling, not a normal object edit. Without this file, users would have no safe, uniform way to inspect the extensions currently loaded into a running deploy.

#### Function details

##### `named_extensions`  (lines 46–61)

```
def named_extensions(manifests: tuple[Manifest, ...]) -> dict[str, Manifest]
```

**Purpose**: Builds the object-name lookup table for the active extension manifests. It turns each extension’s manifest name into the safe name users will type when reading extension objects.

**Data flow**: It receives a tuple of manifests. For each manifest, it lowercases the name, replaces runs of non-letter-or-number characters with hyphens, trims extra hyphens, checks that the result is a valid object name, and stores the manifest under that name. It returns a dictionary from object name to manifest, or raises an error if two manifests would get the same object name.

**Call relations**: This is used when the host has loaded the extension manifests and needs to publish them as workspace objects. It relies on the shared object-name validator so extension names follow the same naming rules as other objects.

*Call graph*: 2 external calls (sub, validate_object_name).


##### `ExtensionObjects.list`  (lines 90–107)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Returns a paged list of all active extensions. Each row gives a human-friendly summary, including the extension version and how many tools and credential slots it declares.

**Data flow**: It reads the stored mapping of extension object names to manifests. It converts each manifest into an ExtensionSpec, builds lightweight rows with summary text and sortable fields, then passes those rows and the user’s list query into the paging helper. The result is an ObjectPage ready for the object system to return.

**Call relations**: This is called when someone lists objects of kind extension. It asks _spec to turn raw manifests into displayable declarations, then hands the finished rows to the common object paging machinery.

*Call graph*: calls 1 internal fn (_spec); 2 external calls (__init__, object_page).


##### `ExtensionObjects.get`  (lines 109–113)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ExtensionSpec] | None
```

**Purpose**: Returns the full declaration for one active extension. It is used when a user wants to inspect exactly what a specific extension contributes.

**Data flow**: It receives an object name and looks it up in the extension mapping. If the name is missing, it returns None. If found, it converts the manifest into an ExtensionSpec and wraps it in an ObjectDetail with no creation or update timestamps, because these objects describe loaded declarations rather than stored database rows.

**Call relations**: This is called when someone reads one extension object by name. Like list, it depends on _spec to translate the manifest into the public shape shown to users.

*Call graph*: calls 1 internal fn (_spec); 1 external calls (__init__).


##### `ExtensionObjects.status`  (lines 115–128)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns the deploy-facing needs of one extension. This separates what the extension contributes from what it asks the deploy to provide.

**Data flow**: It receives an object name and looks up the matching manifest. If no manifest exists, it returns None. If found, it returns a small dictionary containing whether the extension asks for sandbox internet access and which required seams it declares.

**Call relations**: This is called when the object system asks for the status of an extension object. It reads directly from the manifest and does not call other helpers because the status fields are already simple deploy requirements.


##### `ExtensionObjects.apply`  (lines 130–139)

```
async def apply(self, ctx: ToolContext, name: str, spec: ExtensionSpec, old: ExtensionSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to create or update an extension object. This protects the rule that extensions are installed or removed through deploy configuration, not through normal object writes.

**Data flow**: It receives the requested name, new spec, possible old spec, and generation check information, but it does not use them to change anything. Instead, it raises a VerbNotSupported error with a message explaining that extension changes must happen through the deploy lockfile.

**Call relations**: This is called if the object system tries to apply a create or update operation to an extension. Rather than passing work onward, it stops the flow immediately with a clear refusal.

*Call graph*: 1 external calls (__init__).


##### `ExtensionObjects.delete`  (lines 141–148)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to delete an extension object. Removing an extension is treated as a deploy action, not a workspace object action.

**Data flow**: It receives the target name and generation check information, but it does not remove anything from the extension mapping. It raises a VerbNotSupported error that tells the caller to use the deploy lockfile workflow instead.

**Call relations**: This is called if the object system tries to delete an extension. It deliberately ends the operation at this boundary so chat/object tools cannot change which extensions are installed.

*Call graph*: 1 external calls (__init__).


##### `ExtensionObjects._spec`  (lines 150–168)

```
def _spec(self, manifest: Manifest) -> ExtensionSpec
```

**Purpose**: Converts an internal manifest into the public ExtensionSpec shown by list and get. It keeps only names and declarations, so private credential values are never exposed here.

**Data flow**: It receives one manifest. It copies out the manifest’s name and version, gathers tool names from both direct tools and connector tools, and collects the names of object kinds, credential slots, surfaces, jobs, hook events, source backends, and subagent profiles. It returns an ExtensionSpec containing those public declaration fields.

**Call relations**: This helper is used by list when building rows for all extensions and by get when showing one extension in detail. It is the translation step between the loaded manifest format and the object view that users can read.

*Call graph*: called by 2 (get, list); 1 external calls (__init__).


### Shared artifacts and conversations
Artifact and conversation objects expose shared files and past chat context with audience, agent, and member access controls.

### `core/src/ufo/host/kinds/artifacts.py`

`domain_logic` · `request handling`

An artifact is a file produced during a turn and shared through `share_file`. This file is the read-and-delete face of those shared files. It does not create artifacts directly; if someone tries to create or update one here, it tells them to write a workspace file and share it instead.

The main idea is that a file is identified by both the conversation it came from and its filename. If the same filename is shared again in the same conversation, it becomes a newer version of the same artifact. If the same filename appears in another conversation, it is a separate artifact. The code turns that pair into a friendly object name, using a short conversation prefix plus a cleaned-up filename, like a library label that keeps books from different shelves apart.

The `ArtifactObjects` class is the main store. It builds database queries for the visible shared files, groups versions together, makes list rows for the portal, and returns details for one artifact. When an artifact is fetched for status, small files are copied back into the conversation workspace so a later turn can reuse them. It can also mint temporary signed download or preview links. Deleting removes all database rows and stored blobs for every version, so old links stop working.

#### Function details

##### `artifact_media`  (lines 83–97)

```
def artifact_media(media_type: str) -> str
```

**Purpose**: Classifies a file’s media type into a simple bucket: image, document, or other. This gives listings an easy filter instead of asking users to understand every possible MIME type, which is a standard label like `image/png` or `application/pdf`.

**Data flow**: It receives a media type string, lowercases it, checks whether it looks like an image, a text-readable file, an office document, a PDF, or a Word file, and returns one of three plain category names.

**Call relations**: Listing rows call this through `ArtifactObjects._row` so every artifact row includes a simple `media` field that users and filters can rely on.

*Call graph*: called by 1 (_row); 1 external calls (is_text_media).


##### `_document_media`  (lines 100–106)

```
def _document_media() -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database-side test for “is this artifact a document?”. It mirrors the plain Python classification so the database can filter document files before rows are returned.

**Data flow**: It reads the `media_type` column from the shared artifact table, lowercases it inside the SQL query, and returns a SQL condition that matches text, office, PDF, and Word media types.

**Call relations**: `ArtifactObjects._groups` uses this when a listing asks for `media=document`, so the filtering happens in the database rather than after fetching everything.

*Call graph*: called by 1 (_groups); 1 external calls (or_).


##### `_member_participated`  (lines 109–121)

```
def _member_participated() -> sa.ColumnElement[bool]
```

**Purpose**: Builds a database condition that says a signed-in member had already entered the conversation by the time an artifact was shared. This prevents a member from seeing older files from before they joined.

**Data flow**: It compares turns in the same workspace and conversation, looks for a member-admission turn at or before the artifact’s turn, and returns a SQL `exists` condition.

**Call relations**: `ArtifactObjects._member_shares` adds this condition to the normal artifact query for member-facing pages and detail reads.

*Call graph*: called by 1 (_member_shares); 2 external calls (literal, select).


##### `artifact_object_names`  (lines 124–144)

```
def artifact_object_names(shares: Iterable[tuple[UUID, str]]) -> dict[tuple[UUID, str], str]
```

**Purpose**: Creates stable, human-readable object names for artifact identities. The identity is the pair of conversation ID and filename, so files with the same name in different conversations do not get confused.

**Data flow**: It receives many `(conversation_id, filename)` pairs, removes duplicates, creates a base name from the conversation’s first hex characters and a filename slug, counts collisions, and adds a short digest only when two identities would otherwise have the same name.

**Call relations**: `ArtifactObjects._identities` calls this after reading all visible conversation-and-filename pairs, and the resulting names are used by listing, lookup, get, status, and delete flows.

*Call graph*: calls 2 internal fn (_identity_digest, _slug); called by 1 (_identities); 1 external calls (Counter).


##### `_slug`  (lines 147–149)

```
def _slug(filename: str) -> str
```

**Purpose**: Turns a filename into a short, safe name fragment for use in artifact object names. It removes awkward punctuation and spacing so names are readable and URL-like.

**Data flow**: It receives a filename, lowercases it, replaces runs of non-letter-or-number characters with hyphens, trims it to the maximum length, and falls back to `artifact` if nothing usable remains.

**Call relations**: `artifact_object_names` uses this as the filename half of each generated artifact object name.

*Call graph*: called by 1 (artifact_object_names).


##### `_identity_digest`  (lines 152–154)

```
def _identity_digest(identity: tuple[UUID, str]) -> str
```

**Purpose**: Creates a stable hash for one artifact identity so rare name collisions can be broken safely. A hash is a short fingerprint made from the full identity.

**Data flow**: It receives a conversation ID and filename, joins them into a string, hashes that string with SHA-256, and returns the hexadecimal fingerprint.

**Call relations**: `artifact_object_names` uses this only when two distinct artifact identities would otherwise produce the same visible name.

*Call graph*: called by 1 (artifact_object_names); 1 external calls (sha256).


##### `ArtifactObjects.list`  (lines 179–186)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists artifact objects visible to the current tool context. It is the agent-facing list operation for files shared within the current authority and audience rules.

**Data flow**: It reads the caller’s readable subjects and member identity from the context, builds the normal shared-artifact query, asks `_rows` to turn matching shares into object rows, and wraps them into a paged result.

**Call relations**: This is the public list method on the artifact store. It starts the listing flow, then delegates query building to `_shares`, row construction to `_rows`, and final paging to `object_page`.

*Call graph*: calls 2 internal fn (_rows, _shares); 2 external calls (authority_member_id, object_page).


##### `ArtifactObjects.member_page`  (lines 188–207)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Builds the signed-in member’s Artifacts page. It shows only files that belong to the selected agent and are inside the member’s allowed conversation audience.

**Data flow**: It receives a member ID, admin flag, and list query, derives the subjects that member may read, limits shares to ones made after member participation, converts them into rows, and returns a paged object page.

**Call relations**: This is the portal-facing listing path. It uses audience helpers to set the fence, `_member_shares` to enforce the participation rule, `_rows` to make display rows, and `object_page` to page them.

*Call graph*: calls 2 internal fn (_member_shares, _rows); 3 external calls (object_page, audience_subjects, conversation_audience).


##### `ArtifactObjects.get`  (lines 209–211)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ArtifactSpec] | None
```

**Purpose**: Returns the detailed metadata for one named artifact visible to the current tool context. It does not copy bytes into the workspace; that happens in `status`.

**Data flow**: It receives a context and artifact name, searches visible shares for that name, and either returns no result or turns all versions of the artifact into an `ObjectDetail`.

**Call relations**: This is the agent-facing detail read. It uses `_shares` and `_find` to locate the grouped versions, then hands them to `_detail` for the object-detail shape.

*Call graph*: calls 3 internal fn (_find, _shares, _detail).


##### `ArtifactObjects.member_detail`  (lines 213–230)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[ArtifactSpec] | None
```

**Purpose**: Returns one artifact from the member-facing Artifacts index. It uses the same visibility rules as the member listing and includes row information plus detail information.

**Data flow**: It receives a member ID and artifact name, derives the member’s allowed subjects, finds the artifact within member-visible shares, reads the conversation source, builds a row, builds detail, and returns both inside a `MemberObject`.

**Call relations**: This is the portal-facing detail path. It follows the same fence as `member_page`, then combines `_find`, `_sources`, `_row`, and `_detail` so the portal can show both list-style fields and full object detail.

*Call graph*: calls 5 internal fn (_find, _member_shares, _row, _sources, _detail); 3 external calls (__init__, audience_subjects, conversation_audience).


##### `ArtifactObjects.status`  (lines 232–276)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports the current status of an artifact and, for small enough files, copies the latest version back into the workspace. This is how a later turn can reuse a file that an earlier turn produced.

**Data flow**: It receives a context and artifact name, finds the visible artifact, fetches the latest blob if it is below the materialization size limit, rechecks that the artifact is still visible, writes bytes into `artifacts/<name>/<filename>` when available, optionally mints a temporary download URL, and returns size, share time, turn ID, version count, URL, and workspace path.

**Call relations**: Object get flows call status as the place where workspace materialization happens. It relies on `_find` and `_shares` to locate the artifact, `_unchanged_visible` to guard against a visibility change, the blob store for bytes, and URL helpers for signed links.

*Call graph*: calls 3 internal fn (_find, _shares, _unchanged_visible); 6 external calls (__init__, now, workspace_tx, artifact_url_expiry, mint_artifact_url, ws_current).


##### `ArtifactObjects.apply`  (lines 278–287)

```
async def apply(self, ctx: ToolContext, name: str, spec: ArtifactSpec, old: ArtifactSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects create and update attempts for artifacts. Artifacts can only be produced by sharing a file, not by directly writing an object spec.

**Data flow**: It receives the usual apply inputs but does not inspect or save them. It immediately raises a `VerbNotSupported` error with guidance to use `share_file`.

**Call relations**: The object system may call this for create or update verbs, but this artifact store deliberately stops the flow there because `share_file` is the only producer.

*Call graph*: 1 external calls (__init__).


##### `ArtifactObjects.delete`  (lines 289–315)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Deletes an artifact and every version of it. This removes both the database records and the stored file bytes, so already-created download links no longer work.

**Data flow**: It receives a context and artifact name, finds all visible versions, locks and rechecks that the latest visible identity has not changed, deletes all matching shared-artifact rows, verifies the expected number was deleted, then deletes each main blob and any preview blob from blob storage.

**Call relations**: This is the destructive artifact operation. It uses `_find` and `_shares` to decide what artifact is meant, `_unchanged_visible` inside a transaction to avoid deleting the wrong thing after a race, and then hands off to the blob store for physical byte removal.

*Call graph*: calls 3 internal fn (_find, _shares, _unchanged_visible); 3 external calls (delete, workspace_tx, ws_current).


##### `ArtifactObjects._unchanged_visible`  (lines 317–324)

```
def _unchanged_visible(self, ctx: ToolContext, latest: sa.Row) -> sa.Select
```

**Purpose**: Builds a database recheck that the latest artifact still belongs to the same visible conversation. It protects status and delete from acting on an artifact whose visibility or conversation facts changed mid-operation.

**Data flow**: It receives the current context and the latest share row, then returns a SQL query that looks for the same conversation in the current workspace, same selected agent, same audience, and an audience the context may read.

**Call relations**: `status` uses this before writing bytes to the workspace, and `delete` uses it with a row lock before removing records and blobs.

*Call graph*: called by 2 (delete, status); 3 external calls (select, object_agent_id, ws_current).


##### `ArtifactObjects._rows`  (lines 326–337)

```
async def _rows(self, subjects: frozenset[str], viewer: UUID | None, query: ObjectListQuery, shares: sa.Select) -> tuple[ObjectRow, ...]
```

**Purpose**: Turns a share query into the final list of object rows. It is the bridge between raw database shares and the display-ready artifact listing.

**Data flow**: It receives allowed subjects, an optional viewer member ID, a list query, and a base share query. It groups matching shares, reads conversation sources for those groups, converts each group into one row, and returns the rows.

**Call relations**: Both `list` and `member_page` call this after choosing their visibility rules. It delegates filtering and grouping to `_groups`, source lookup to `_sources`, and row formatting to `_row`.

*Call graph*: calls 3 internal fn (_groups, _row, _sources); called by 2 (list, member_page).


##### `ArtifactObjects._find`  (lines 339–359)

```
async def _find(self, subjects: frozenset[str], name: str, shares: sa.Select) -> tuple[sa.Row, ...] | None
```

**Purpose**: Finds all versions of one named artifact inside a particular visibility projection. It resolves the public object name back to its real identity: conversation ID plus filename.

**Data flow**: It receives allowed subjects, a name, and a share query. It loads all visible identities and names, finds the identity matching the requested name, filters the share query to that conversation and filename, reads all rows, sorts newest first, and returns them or nothing.

**Call relations**: `get`, `member_detail`, `status`, and `delete` all use this whenever a user supplies an artifact name. It relies on `_identities` so name resolution is stable across listing windows.

*Call graph*: calls 1 internal fn (_identities); called by 4 (delete, get, member_detail, status); 2 external calls (where, workspace_tx).


##### `ArtifactObjects._groups`  (lines 361–426)

```
async def _groups(self, subjects: frozenset[str], viewer: UUID | None, query: ObjectListQuery, shares: sa.Select) -> Sequence[tuple[str, tuple[sa.Row, ...]]]
```

**Purpose**: Applies listing filters and groups recent share rows into artifacts. Each group represents one conversation-and-filename identity, with its versions sorted newest first.

**Data flow**: It receives allowed subjects, an optional viewer, the list query, and a base share query. It narrows by text search, conversation, ownership, surface, and media category, scans a bounded number of newest rows, groups them by identity, looks up stable names, and returns name-plus-version groups sorted by name.

**Call relations**: `_rows` calls this as the main listing engine. It uses `_document_media` for document filtering and `_identities` to attach the same names that direct lookups will use.

*Call graph*: calls 2 internal fn (_identities, _document_media); called by 1 (_rows); 5 external calls (false, not_, or_, workspace_tx, UUID).


##### `ArtifactObjects._identities`  (lines 428–451)

```
async def _identities(self, subjects: frozenset[str]) -> dict[tuple[UUID, str], str]
```

**Purpose**: Reads every distinct artifact identity visible to a set of subjects and assigns stable object names. This keeps names consistent even if the listing only scans the newest shares.

**Data flow**: It receives allowed subjects, queries the database for distinct conversation IDs and filenames in the current workspace for the selected agent and allowed audiences, then passes those identities to `artifact_object_names`.

**Call relations**: `_find` uses this to turn a requested name back into an identity, and `_groups` uses it to label grouped listing results.

*Call graph*: calls 1 internal fn (artifact_object_names); called by 2 (_find, _groups); 4 external calls (select, workspace_tx, object_agent_id, ws_current).


##### `ArtifactObjects._shares`  (lines 453–488)

```
def _shares(self, subjects: frozenset[str]) -> sa.Select
```

**Purpose**: Builds the base database query for artifact shares visible to an agent and a set of audience subjects. It includes the file facts plus conversation and owner information needed for rows and details.

**Data flow**: It receives allowed subjects and returns a SQL select over shared artifacts joined to turns, conversations, and optionally members. The query is limited to the current workspace, selected agent, and conversations whose audience is readable.

**Call relations**: `list`, `get`, `status`, `delete`, and `_member_shares` start from this shared query, then add their own filtering or grouping rules.

*Call graph*: called by 5 (_member_shares, delete, get, list, status); 3 external calls (select, object_agent_id, ws_current).


##### `ArtifactObjects._member_shares`  (lines 490–491)

```
def _member_shares(self, subjects: frozenset[str]) -> sa.Select
```

**Purpose**: Builds the member-facing share query. It starts with the normal visible shares and adds the rule that the member must have participated before the share happened.

**Data flow**: It receives allowed subjects, calls `_shares` to build the base query, adds the `_member_participated` condition, and returns the narrowed SQL query.

**Call relations**: `member_page` and `member_detail` use this so the portal never shows a member files from before they entered that conversation.

*Call graph*: calls 2 internal fn (_shares, _member_participated); called by 2 (member_detail, member_page).


##### `ArtifactObjects._sources`  (lines 493–530)

```
async def _sources(self, conversation_ids: Sequence[UUID]) -> dict[UUID, str | None]
```

**Purpose**: Finds the opening source for conversations, such as the permalink or surface source from the first turn. This lets artifact rows explain where the conversation began.

**Data flow**: It receives conversation IDs, finds the earliest turn number for each conversation in the current workspace, reads that turn’s stored context, validates it as a `TurnContext`, and returns a map from conversation ID to source or `None`.

**Call relations**: `_rows` uses this for list rows, and `member_detail` uses it for one member-facing detail row, so the displayed artifact can include its origin source.

*Call graph*: called by 2 (_rows, member_detail); 5 external calls (model_validate, and_, select, workspace_tx, ws_current).


##### `ArtifactObjects._row`  (lines 532–560)

```
def _row(self, name: str, shares: tuple[sa.Row, ...], viewer: UUID | None, sources: dict[UUID, str | None]) -> ObjectRow
```

**Purpose**: Builds one display row for an artifact group. It summarizes the latest version while also including useful listing fields like owner, origin, media category, and links.

**Data flow**: It receives a generated name, all versions of that artifact, the optional viewer ID, and conversation sources. It takes the newest share, builds a short summary, fills row fields, marks whether it is the viewer’s own file, and adds download and preview URLs when available.

**Call relations**: `_rows` calls this for normal listings, and `member_detail` calls it when wrapping a single portal detail. It uses `_summary`, `artifact_media`, `_download_url`, and `_preview_url` to fill the row.

*Call graph*: calls 4 internal fn (_download_url, _preview_url, _summary, artifact_media); called by 2 (_rows, member_detail); 1 external calls (__init__).


##### `ArtifactObjects._download_url`  (lines 562–571)

```
def _download_url(self, latest: sa.Row) -> str | None
```

**Purpose**: Creates a temporary signed download link for the latest artifact blob when this deployment is configured to publish links. Signed means the URL carries proof that it is allowed for a limited time.

**Data flow**: It receives the latest share row, checks that both a token secret and public base URL are configured, mints a path for the blob with an expiry and workspace ID, prefixes it with the public base URL, and returns the full URL or `None`.

**Call relations**: `_row` calls this while building listing and member-detail rows so the portal can offer a download button when link minting is enabled.

*Call graph*: called by 1 (_row); 4 external calls (now, artifact_url_expiry, mint_artifact_url, ws_current).


##### `ArtifactObjects._preview_url`  (lines 573–593)

```
def _preview_url(self, latest: sa.Row) -> str | None
```

**Purpose**: Creates a signed image preview link when the artifact has a valid raster image preview. Raster images are pixel-based images such as PNG or JPEG.

**Data flow**: It receives the latest share row, chooses the preview blob if one exists or the original blob if the file is already an image, checks that the blob key’s image type matches the declared media type, and then mints a preview URL with the blob size and workspace ID. If anything is not eligible, it returns `None`.

**Call relations**: `_row` calls this so artifact listings can show thumbnails or previews only when the stored bytes and declared type agree.

*Call graph*: called by 1 (_row); 3 external calls (mint_image_preview_url, raster_image_media_type, ws_current).


##### `_detail`  (lines 596–612)

```
def _detail(shares: tuple[sa.Row, ...]) -> ObjectDetail[ArtifactSpec]
```

**Purpose**: Builds the full object detail for an artifact. It describes the current file spec and links the artifact back to the conversation where it was created.

**Data flow**: It receives all versions of one artifact sorted newest first, takes the latest version for filename, media type, and subject, uses the oldest share time as creation time and the latest share time as update time, and returns an `ObjectDetail` with a `created_in` conversation link.

**Call relations**: `ArtifactObjects.get` and `ArtifactObjects.member_detail` call this after `_find` has collected all versions for the requested artifact.

*Call graph*: called by 2 (get, member_detail); 4 external calls (__init__, __init__, __init__, __init__).


##### `_summary`  (lines 615–621)

```
def _summary(shares: tuple[sa.Row, ...]) -> str
```

**Purpose**: Creates a short human-readable summary for an artifact row. It gives a quick glance at the filename, type, size, share date, and whether there are multiple versions.

**Data flow**: It receives all versions of an artifact, reads the latest one, formats a sentence-like summary, adds a version count when needed, and cuts it to the maximum summary length.

**Call relations**: `ArtifactObjects._row` uses this when building each listing row.

*Call graph*: called by 1 (_row).


##### `artifact_object`  (lines 624–685)

```
def artifact_object(*, public_base_url: str | None=None, artifact_token_secret: str='') -> ObjectKind
```

**Purpose**: Registers the artifact object kind with the object system. This tells the wider system what artifacts are called, what fields they expose, what verbs they support, and which store object implements them.

**Data flow**: It receives optional public URL and token-secret settings, constructs an `ArtifactObjects` store with those settings, and returns an `ObjectKind` describing artifact behavior, guidance, spec model, list fields, and allowed agent verbs.

**Call relations**: Startup or object-kind registration code calls this to make artifacts available. The returned `ObjectKind` points list, get, and delete requests to `ArtifactObjects` and advertises that create and update are not supported.

*Call graph*: 2 external calls (__init__, __init__).


### `core/src/ufo/host/kinds/conversations.py`

`domain_logic` · `request handling`

A conversation in this system is a record of a chat that happened on some surface, such as the portal, Slack, or an extension-provided chat. Other objects, like artifacts and scheduled tasks, can point back to the conversation they came from. This file is what makes those links resolvable.

The important rule is that conversations are read-only here. A surface creates them, and retention policy may later close or remove them, but tools cannot create, edit, or delete them through the object API. That keeps the conversation history tied to the real chat transport that produced it.

The file exposes conversations differently depending on who is asking. During a turn, it lists and gets only conversations whose audience matches the caller’s readable subjects. A workspace admin can deliberately ask for private metadata rows from other members, but only as metadata: no transcript is exposed. Outside a turn, the portal can show a member their own conversation rail, plus readable shared conversations, with fields such as title, whether it is “mine,” speaker, surface, and last activity time.

For status, the file reads the stored transcript blob, turns the messages into plain text lines, and writes a small enough transcript into the workspace for inspection. It rechecks visibility after reading, like checking that a library card is still valid before handing over the book.

#### Function details

##### `ConversationObjects.list`  (lines 83–90)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists conversation rows that the current tool turn is allowed to see. If the caller explicitly asks for private rows and is a qualifying workspace admin, it also includes private conversation metadata from other members.

**Data flow**: It receives the tool context and a list query. It reads visible conversation rows for the caller’s subjects, turns each database row into a simple object row, optionally adds private metadata rows, and then passes the rows through the normal paging and filtering helper. The output is an ObjectPage for display or further processing.

**Call relations**: This is the main list entry used by the object system for the conversation kind. It relies on _rows for normal visibility, _widens_for_admin to decide whether private metadata may be shown, _private_rows for that extra admin-only set, _row to format rows, and object_page to apply the requested page shape.

*Call graph*: calls 4 internal fn (_private_rows, _rows, _widens_for_admin, _row); 1 external calls (object_page).


##### `ConversationObjects.get`  (lines 92–96)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ConversationSpec] | None
```

**Purpose**: Looks up one conversation by its id and returns its full object detail if the caller may see it. For admins, it can fall back to private metadata access when normal visibility does not allow the row.

**Data flow**: It receives the tool context and a name string. It first tries to find a visible row using that name as a UUID; if none is found, it tries the stricter private admin lookup. If a row is found, it turns it into an ObjectDetail; otherwise it returns nothing.

**Call relations**: This is the single-object read path for conversation objects. It delegates normal lookup to _find, private admin lookup to _private_find, and final formatting to _detail.

*Call graph*: calls 3 internal fn (_find, _private_find, _detail).


##### `ConversationObjects.member_page`  (lines 98–139)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Builds the conversation list shown to a signed-in member in the portal, outside of an active tool turn. It shows the member’s own conversations and some readable conversations involving others, while deliberately not widening just because the member is an admin.

**Data flow**: It receives portal context, member id, admin flag, and a list query. It opens the conversation directory for the current workspace and selected agent, asks for two bounded groups, “mine” and “others,” optionally narrows by the portal filter, skips entries without titles, formats each remaining entry, and returns a paged result.

**Call relations**: This is the portal rail listing path. It uses ws_current and object_agent_id to stay scoped to the current workspace and agent, ConversationDirectory to read prepared conversation summaries, _member_row to shape each visible item, and object_page to finish the page.

*Call graph*: calls 1 internal fn (_member_row); 4 external calls (__init__, object_agent_id, object_page, ws_current).


##### `ConversationObjects.member_detail`  (lines 141–156)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[ConversationSpec] | None
```

**Purpose**: Returns one conversation detail for a signed-in portal member outside a turn, but only when the conversation’s audience matches what that member is allowed to read. It does not let admins browse everyone’s private conversation rows through this route.

**Data flow**: It receives portal context, a conversation name, member id, and admin flag. It computes the subjects carried by that member’s own conversation audience, finds a matching row, and if present wraps both the row summary and full detail into a MemberObject. If no permitted row exists, it returns nothing.

**Call relations**: This is the portal detail companion to member_page. It uses conversation_audience and audience_subjects to derive the member’s allowed subjects, _find to read within that boundary, _row for the compact row view, _detail for the full spec, and MemberObject to return both together.

*Call graph*: calls 3 internal fn (_find, _detail, _row); 3 external calls (__init__, audience_subjects, conversation_audience).


##### `ConversationObjects.status`  (lines 158–176)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports transcript status for a visible conversation and, when possible, writes the readable text exchange into the caller’s workspace. It is careful not to expose transcript text for private admin-only metadata rows.

**Data flow**: It receives the tool context, conversation name, and an expected generation value that this implementation does not use. It finds a visible row, reads and flattens the transcript messages, rechecks that the same row is still visible, writes a text file if there is content and it is not too large, and returns message count, byte size, and the workspace path if one was written.

**Call relations**: This is the object status path for conversations. It calls _find to enforce normal visibility, _exchange to read transcript content, _unchanged_visible to guard against a visibility-changing race, and raises UnknownObject if the row stopped being safely visible before the transcript is returned.

*Call graph*: calls 3 internal fn (_exchange, _find, _unchanged_visible); 1 external calls (__init__).


##### `ConversationObjects.apply`  (lines 178–187)

```
async def apply(self, ctx: ToolContext, name: str, spec: ConversationSpec, old: ConversationSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to create or change a conversation through the object API. This protects the rule that conversations come from chat surfaces, not from object edits.

**Data flow**: It receives the requested name, new spec, possible old spec, context, and expected generation. It does not inspect or store them; it immediately raises a VerbNotSupported error explaining that conversations are surface-made.

**Call relations**: This is called when the object system tries to apply a desired conversation state. It hands off only to VerbNotSupported, making mutation refusal an explicit part of the conversation kind’s behavior.

*Call graph*: 1 external calls (__init__).


##### `ConversationObjects.delete`  (lines 189–196)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to delete a conversation through the object API. Conversation lifetime is controlled elsewhere, such as by the surface or retention process.

**Data flow**: It receives the tool context, conversation name, and expected generation. It does not remove any row; it raises a VerbNotSupported error with the standard explanation.

**Call relations**: This is the delete path for the conversation object kind. Instead of touching the database, it immediately uses VerbNotSupported to tell the caller that this operation is not available.

*Call graph*: 1 external calls (__init__).


##### `ConversationObjects._exchange`  (lines 198–218)

```
async def _exchange(self, ctx: ToolContext, conversation_id: UUID) -> tuple[str, ...]
```

**Purpose**: Reads a conversation transcript and turns it into plain text lines such as “user: hello” or “assistant: ...”. It hides missing transcripts by treating them as an empty exchange, but reports corrupted transcripts as an error.

**Data flow**: It receives the tool context and conversation id. It builds the transcript blob key, fetches and decodes the blob, walks each message, extracts text from either a plain string or text blocks, prefixes each line with the message role, and returns a tuple of lines.

**Call relations**: This helper is used by status after the conversation row has been found. It depends on transcript_key to locate the stored blob and decode to turn stored transcript data back into message objects before status writes a plain text workspace file.

*Call graph*: called by 1 (status); 2 external calls (decode, transcript_key).


##### `ConversationObjects._unchanged_visible`  (lines 220–233)

```
async def _unchanged_visible(self, subjects: frozenset[str], row: sa.Row) -> bool
```

**Purpose**: Checks that a conversation row is still visible to the same subjects and still has the same audience after the transcript was read. This prevents a timing mistake where access changes halfway through a status request.

**Data flow**: It receives the allowed subjects and the database row originally found. It opens a workspace database transaction, builds a visibility query for the same conversation id and audience, asks whether such a row still exists, and returns true or false.

**Call relations**: This helper is called only by status. It uses _visible to rebuild the same visibility boundary, then SQL exists/select through workspace_tx to verify that returning transcript information is still safe.

*Call graph*: calls 1 internal fn (_visible); called by 1 (status); 3 external calls (exists, select, workspace_tx).


##### `ConversationObjects._find`  (lines 235–241)

```
async def _find(self, subjects: frozenset[str], name: str) -> sa.Row | None
```

**Purpose**: Finds one visible conversation by name, where the name must be a valid UUID. It keeps invalid names from becoming database lookups.

**Data flow**: It receives a set of readable subjects and a name string. It tries to parse the name as a UUID; if parsing fails, it returns nothing. If parsing succeeds, it asks _rows for that specific id and returns the first row if present.

**Call relations**: This is the shared normal lookup helper for get, member_detail, and status. It delegates the actual database read to _rows so all normal conversation reads use the same visibility query.

*Call graph*: calls 1 internal fn (_rows); called by 3 (get, member_detail, status); 1 external calls (UUID).


##### `ConversationObjects._rows`  (lines 243–250)

```
async def _rows(self, subjects: frozenset[str], *, conversation_id: UUID | None) -> tuple[sa.Row, ...]
```

**Purpose**: Reads conversation rows for the selected agent that are visible to a given set of subjects. It can read all visible rows or just one conversation id.

**Data flow**: It receives readable subjects and an optional conversation id. It starts with the common visible-conversations query, adds an id filter if one was supplied, runs the query inside the current workspace transaction, and returns the resulting rows as a tuple.

**Call relations**: This helper supplies normal visible rows to list and _find. It builds on _visible for the access rule and workspace_tx for the database connection tied to the current workspace.

*Call graph*: calls 1 internal fn (_visible); called by 2 (_find, list); 1 external calls (workspace_tx).


##### `ConversationObjects._widens_for_admin`  (lines 252–255)

```
async def _widens_for_admin(self, ctx: ToolContext) -> bool
```

**Purpose**: Decides whether the current turn may widen from ordinary visibility to private metadata visibility. It allows this only for a workspace admin and never for a foreign-audience conversation.

**Data flow**: It receives the tool context. If the turn audience starts with the foreign-audience prefix, it returns false immediately; otherwise it asks the context whether the speaker is an admin and returns that answer.

**Call relations**: This guard is used by list and _private_find before they read private metadata rows. It calls ToolContext.speaker_is_admin only after ruling out the foreign-audience case.

*Call graph*: calls 1 internal fn (speaker_is_admin); called by 2 (_private_find, list).


##### `ConversationObjects._private_find`  (lines 257–265)

```
async def _private_find(self, ctx: ToolContext, name: str) -> sa.Row | None
```

**Purpose**: Finds one private conversation metadata row for an admin when ordinary visibility did not find it. It never returns transcript content, only the row used by get.

**Data flow**: It receives the tool context and name. It first checks whether admin widening is allowed, then parses the name as a UUID, then asks _private_rows for that id. It returns the first matching row or nothing.

**Call relations**: This is the private fallback used by get. It relies on _widens_for_admin for permission, UUID parsing for name validation, and _private_rows for the actual database read.

*Call graph*: calls 2 internal fn (_private_rows, _widens_for_admin); called by 1 (get); 1 external calls (UUID).


##### `ConversationObjects._private_rows`  (lines 267–277)

```
async def _private_rows(self, ctx: ToolContext, *, conversation_id: UUID | None) -> tuple[sa.Row, ...]
```

**Purpose**: Reads private conversation metadata rows for the selected agent that are not already visible to the caller. This is for the limited admin view of other members’ private conversations.

**Data flow**: It receives the tool context and an optional conversation id. It starts from all conversations for the current agent, keeps rows whose audience looks like a member-private subject and is not in the caller’s readable subjects, optionally filters by id, runs the query, and returns the rows.

**Call relations**: This helper is used by list for explicit private listings and by _private_find for one private row. It builds its base query with _agent_conversations and runs it through workspace_tx.

*Call graph*: calls 1 internal fn (_agent_conversations); called by 2 (_private_find, list); 1 external calls (workspace_tx).


##### `_agent_conversations`  (lines 280–301)

```
def _agent_conversations() -> sa.Select
```

**Purpose**: Builds the base database query for conversations belonging to the current workspace and the currently selected agent. It also includes the agent name used later in object links.

**Data flow**: It reads the current workspace id and selected agent id, constructs a SQL query joining conversation rows to their agent row, chooses the archived agent name when present or the current name otherwise, and returns the unfinished query for other helpers to add filters.

**Call relations**: This is the foundation query for _visible and _private_rows. It uses SQLAlchemy’s select builder, ws_current for workspace scope, and object_agent_id for agent scope, so callers do not repeat those rules.

*Call graph*: called by 2 (_private_rows, _visible); 3 external calls (select, object_agent_id, ws_current).


##### `_visible`  (lines 304–305)

```
def _visible(subjects: frozenset[str]) -> sa.Select
```

**Purpose**: Adds the normal audience visibility rule to the base conversation query. A conversation is visible when its audience is one of the caller’s readable subjects.

**Data flow**: It receives a frozen set of subject strings. It builds the current-agent conversation query, adds an audience-in-subjects filter, and returns the resulting SQL query for execution elsewhere.

**Call relations**: This helper is used by _rows for normal reads and by _unchanged_visible for the final safety check in status. It depends on _agent_conversations so visibility is always applied within the current workspace and agent.

*Call graph*: calls 1 internal fn (_agent_conversations); called by 2 (_rows, _unchanged_visible).


##### `_member_row`  (lines 308–326)

```
def _member_row(entry: ListedConversation, *, mine: bool) -> ObjectRow
```

**Purpose**: Turns a portal directory conversation entry into a compact row for the member’s conversation list. It includes user-facing fields like title, whether it is mine, speaker, surface, portal availability, and last activity time.

**Data flow**: It receives a ListedConversation and a boolean saying whether it belongs to the current member. It chooses speaker names, chooses the latest useful timestamp, computes whether the surface is portal-compatible, and returns an ObjectRow with those fields.

**Call relations**: This formatter is called by member_page for each directory entry that has a title. It packages the directory data into the common ObjectRow shape that object_page can paginate and return.

*Call graph*: called by 1 (member_page); 1 external calls (__init__).


##### `_row`  (lines 329–344)

```
def _row(row: sa.Row, *, private: bool=False) -> ObjectRow
```

**Purpose**: Turns a raw database conversation row into a compact object row for ordinary object listings. It summarizes where the conversation came from and when it was created.

**Data flow**: It receives a database row and an optional private flag. It builds a readable origin phrase from the surface and surface label, adds fields such as surface and surface_label, marks private rows when requested, and returns an ObjectRow named by the conversation id.

**Call relations**: This formatter is used by list for normal and private metadata rows, and by member_detail for the row half of the returned MemberObject. It hands the shaped data to ObjectRow.

*Call graph*: called by 2 (list, member_detail); 1 external calls (__init__).


##### `_detail`  (lines 347–359)

```
def _detail(row: sa.Row) -> ObjectDetail[ConversationSpec]
```

**Purpose**: Turns a database conversation row into the full detail object, including the formal conversation spec and a link back to the agent it belongs to. This is what a caller sees when asking for one conversation.

**Data flow**: It receives a database row. It copies surface, surface label, and audience into a ConversationSpec, carries over created and updated timestamps, creates a scoped_to link pointing at the agent, and returns an ObjectDetail.

**Call relations**: This formatter is used by get and member_detail after a permitted row has been found. It creates ConversationSpec, ObjectRef, ObjectLink, and ObjectDetail objects so the rest of the object system receives a standard detail shape.

*Call graph*: called by 2 (get, member_detail); 4 external calls (__init__, __init__, __init__, __init__).


### Credential slots
Credential objects let members inspect and clear extension-declared credential slots while never returning secret values.

### `core/src/ufo/host/kinds/credential_kind.py`

`domain_logic` · `request handling for workspace credential object reads and admin credential clearing`

Some extensions need outside secrets, such as API keys. This file represents those needs as “credential” objects: not the secrets themselves, but the empty-or-filled slots where secrets may be stored. Think of it like a labeled safe deposit box list: you can see that a box exists and whether it has something inside, but you cannot see the contents.

The slot declarations come from installed extension manifests. A database row only exists when a slot has been filled with a sealed credential value. If there is no row, the slot is still shown, but marked empty. Reads return a `CredentialSpec`, which contains safe information such as the slot name, description, extension name, and possible host choices. They never return the credential value or even a digest of it.

The public object behavior is deliberately narrow. Listing and detail views are available to workspace members. Creating or updating a credential through this object kind is refused, because filling or rotating a secret must go through a separate private `request_credentials` flow. Deleting is allowed only for workspace admins, and it clears the stored value while leaving the declared slot visible as empty.

#### Function details

##### `CredentialObjects.list`  (lines 77–78)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Builds the normal workspace list view for credential slots. It shows every declared slot and whether it is filled, without showing any secret value.

**Data flow**: It receives a tool context and a list query with paging, sorting, or filtering instructions. It asks `_rows` to build safe row summaries for all declared slots, then passes those rows and the query to `object_page`, which returns the requested page of results.

**Call relations**: When the object system needs to list credential objects, it calls this method. This method does not inspect secrets itself; it delegates row building to `_rows` and hands the final rows to the shared paging helper.

*Call graph*: calls 1 internal fn (_rows); 1 external calls (object_page).


##### `CredentialObjects.member_page`  (lines 80–90)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Builds the portal-style list of credential slots for a signed-in member. It gives members the same safe slot index: declarations plus filled-or-empty state, never credential values.

**Data flow**: It receives optional extension context, member identity, admin status, and a list query. It ignores member-specific scoping because credential declarations are workspace-wide, gets the safe rows from `_rows`, and returns a paged result through `object_page`.

**Call relations**: The member-facing portal calls this when a member views credential slots. It follows the same path as the workspace list view by relying on `_rows`, then uses the common paging helper to shape the response.

*Call graph*: calls 1 internal fn (_rows); 1 external calls (object_page).


##### `CredentialObjects.get`  (lines 92–93)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[CredentialSpec] | None
```

**Purpose**: Returns the safe detail view for one credential slot by name. It shows the slot declaration and timestamps if the slot has been filled, but not the secret.

**Data flow**: It receives a tool context and a slot name. It passes the name to `_detail`; the result is either a safe object detail or `None` if no installed extension declares that slot.

**Call relations**: The object system calls this when someone opens one credential object. This method is a thin doorway into `_detail`, which does the actual lookup and safe rendering.

*Call graph*: calls 1 internal fn (_detail).


##### `CredentialObjects.member_detail`  (lines 95–110)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[CredentialSpec] | None
```

**Purpose**: Returns the member-facing detail view for one credential slot. It combines the list-row summary with the safe detailed declaration so the portal can show both at once.

**Data flow**: It receives optional extension context, a slot name, member identity, and admin status. It first asks `_detail` for the safe declaration; if the slot is unknown, it returns `None`. Otherwise it asks `_rows` for the current filled-or-empty rows, picks the matching row, and wraps the row and detail into a `MemberObject`.

**Call relations**: The member portal calls this when a signed-in member opens a single credential slot. It reuses `_detail` for the declaration and `_rows` for the summary, so the member view stays consistent with the normal list and get views.

*Call graph*: calls 2 internal fn (_detail, _rows); 1 external calls (__init__).


##### `CredentialObjects.status`  (lines 112–136)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports lightweight status for one credential slot, mainly whether it is filled. If the slot has a host setting and a credential store is available, it also reports the resolved host information.

**Data flow**: It receives a context, a slot name, and an optional expected generation value. It looks up the declared slot by name. If the slot is not declared, it returns `None`. If it is declared, it opens a workspace database transaction, checks whether a row exists for that slot in the current workspace, and returns a dictionary such as `filled: true` or `filled: false`. For host-aware slots, it asks the credential store for the current host and adds it to the status.

**Call relations**: Status checks call this when they need a compact answer rather than a full object detail. It uses `_named` to confirm the slot is real, reads the current workspace through the workspace context, and may hand off to `credential_host` to resolve host details safely.

*Call graph*: calls 1 internal fn (_named); 4 external calls (select, workspace_tx, credential_host, ws_current).


##### `CredentialObjects.apply`  (lines 138–147)

```
async def apply(self, ctx: ToolContext, name: str, spec: CredentialSpec, old: CredentialSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to create, fill, or update a credential through the normal object apply path. This keeps secret handoff inside the safer `request_credentials` flow.

**Data flow**: It receives the proposed credential spec, the old spec if any, and an optional expected generation value. Instead of storing anything, it immediately raises a `VerbNotSupported` error explaining that filling or rotating credentials must use the private credential request action.

**Call relations**: The object system would call this for create or update operations. This file deliberately stops that path here, so no caller can accidentally send a secret through an ordinary object write.

*Call graph*: 1 external calls (__init__).


##### `CredentialObjects.delete`  (lines 149–165)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Clears the stored value for a credential slot. It does not remove the slot declaration, so the slot will still appear afterward as empty.

**Data flow**: It receives a context, slot name, and optional expected generation value. It first asks the tool context whether the current speaker is a workspace admin. If not, it raises an admin-required error. If yes, it finds the declared slot, opens a workspace database transaction, and deletes the credential row for that slot in the current workspace.

**Call relations**: The object system calls this when someone deletes a credential object. It uses `_named` to map the requested name to a declared slot, checks admin permission through the tool context, then performs the database delete inside the current workspace.

*Call graph*: calls 2 internal fn (_named, speaker_is_admin); 4 external calls (__init__, delete, workspace_tx, ws_current).


##### `CredentialObjects._rows`  (lines 167–183)

```
async def _rows(self) -> tuple[ObjectRow, ...]
```

**Purpose**: Builds the safe list rows for all declared credential slots. Each row says which extension declared the slot and whether it is filled.

**Data flow**: It first asks `_filled_slots` for the set of slot names that currently have database rows. It asks `_named` for the declared slots by display name. For each declared slot, it creates an `ObjectRow` with a human-readable summary and safe fields: slot name, extension name, and filled status.

**Call relations**: This is the shared row-building helper behind both workspace and member list views, and it is also used by `member_detail` to attach the matching row to a detail response. It gathers database fill state once, combines it with manifest declarations, and returns rows ready for paging or display.

*Call graph*: calls 2 internal fn (_filled_slots, _named); called by 3 (list, member_detail, member_page); 1 external calls (__init__).


##### `CredentialObjects._detail`  (lines 185–209)

```
async def _detail(self, name: str) -> ObjectDetail[CredentialSpec] | None
```

**Purpose**: Builds the safe detail record for one declared credential slot. It includes the declaration and fill timestamps, but never reads or returns the credential value.

**Data flow**: It receives a slot name and looks it up in `_named`. If no declaration exists, it returns `None`. If the slot exists, it opens a workspace database transaction and checks for the row holding timestamps for that slot. It then creates a `CredentialSpec` from the declaration, including host information or host choices, and wraps it in an `ObjectDetail` with timestamps set to `None` when the slot is empty.

**Call relations**: The normal `get` path and the member `member_detail` path both call this. It is the central place where a declared slot becomes a safe detailed object view, while the database is used only to learn whether a stored row exists and when it was created or updated.

*Call graph*: calls 1 internal fn (_named); called by 2 (get, member_detail); 5 external calls (__init__, __init__, select, workspace_tx, ws_current).


##### `CredentialObjects._named`  (lines 211–212)

```
def _named(self) -> dict[str, DeclaredSlot]
```

**Purpose**: Turns the stored list of declared credential slots into a lookup table by name. This lets other methods quickly find the declaration for a requested slot.

**Data flow**: It reads the `slots` held by the `CredentialObjects` instance. It passes them to `named_slots`, which returns a dictionary-like mapping from slot names to declared slot records.

**Call relations**: Several methods call this before working with a slot: `_detail` and `status` use it to reject unknown names, `_rows` uses it to iterate through all declarations, and `delete` uses it to find the exact slot to clear.

*Call graph*: called by 4 (_detail, _rows, delete, status); 1 external calls (named_slots).


##### `CredentialObjects._filled_slots`  (lines 214–223)

```
async def _filled_slots(self) -> frozenset[str]
```

**Purpose**: Finds which credential slots currently have stored values in the current workspace. It returns only slot names, not the values themselves.

**Data flow**: It opens a workspace database transaction and selects the slot column from credential rows belonging to the current workspace. It turns the returned rows into a frozen set of slot names, which is safe to compare against declarations.

**Call relations**: `_rows` calls this when it needs to mark declared slots as filled or empty. This helper is the only list-building step that checks the credential table, and it reads only the slot names needed for status display.

*Call graph*: called by 1 (_rows); 3 external calls (select, workspace_tx, ws_current).


### Workspace membership and surfaces
Member, surface, and workspace objects describe who has access, which chat surfaces are connected, and read-only workspace facts.

### `core/src/ufo/host/kinds/members.py`

`domain_logic` · `request handling`

This file is the rulebook for workspace membership. It decides who can see the roster, who can change someone’s role or access, and how a new person can be added by email. Without it, the system would not have a safe, consistent way to show or edit members, and an agent might leak the workspace roster in a shared channel or let the wrong person change access.

A member has two important switches: `admin`, meaning they can administer the workspace, and `seated`, meaning they are allowed through the door when they try to speak. Think of a seat like an active badge: removing the seat does not erase the person, but it stops them from entering.

The file is careful about privacy. In an internal conversation with the main agent, the roster can be shown. In a child agent, ordinary members usually see only themselves, while admins may see more during a turn. In a channel shared with another organization, everyone is narrowed to their own row.

Changing a member is locked down. Only a signed-in workspace admin using the main agent can change admin status or seating. The code also prevents removing the last admin, and prevents leaving the workspace without a seated admin. Members cannot be deleted through this object. New members are created through `add_member`, which validates the email, checks admin permission, avoids duplicates, creates the member, and optionally sends an invitation email.

#### Function details

##### `MemberObjects.list`  (lines 68–72)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows a page of member rows that the current speaker is allowed to see. It is used when the agent or object system needs to list workspace members safely, without exposing more of the roster than the situation allows.

**Data flow**: It receives the current tool context and a paging/filtering query. It asks `_visible_rows` for the database rows this speaker may see, turns each row into a simple display row with `_row`, and passes those rows into `object_page` to produce the final page.

**Call relations**: This is the public list operation for the member object. It relies on `_visible_rows` to enforce the privacy rules first, then hands the allowed rows to `_row` and `object_page` so the rest of the system gets a normal object-list response.

*Call graph*: calls 2 internal fn (_visible_rows, _row); 1 external calls (object_page).


##### `MemberObjects.member_page`  (lines 74–91)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows a page of members for portal-style reads outside an agent turn. It follows a stricter portal rule: the full roster is visible only through the main agent; otherwise the reader sees only their own row.

**Data flow**: It receives the signed-in member’s ID, their admin flag, and a page query. It asks `_member_rows` what rows that member may read in the current agent, converts each row with `_row`, and wraps them into an object page.

**Call relations**: This is the portal counterpart to `list`. Instead of using the full live tool context, it calls `_member_rows`, which checks whether the named agent is the main agent and then delegates to `_roster` for the actual database read.

*Call graph*: calls 2 internal fn (_member_rows, _row); 1 external calls (object_page).


##### `MemberObjects.get`  (lines 93–95)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[MemberSpec] | None
```

**Purpose**: Fetches the detailed view of one visible member during a tool/object read. If the named member is not visible to the current speaker, it returns nothing rather than revealing that row.

**Data flow**: It receives the current context and the object name, which is expected to be a member ID as text. It asks `_visible_row` to find a matching allowed row, then turns that row into detailed member data with `_detail`; if no row is allowed or found, it returns `None`.

**Call relations**: This is the single-object read used during agent turns. It depends on `_visible_row`, which applies the same visibility rules as list, and then uses `_detail` to return the editable member fields and timestamps.

*Call graph*: calls 2 internal fn (_visible_row, _detail).


##### `MemberObjects.member_detail`  (lines 97–113)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[MemberSpec] | None
```

**Purpose**: Fetches one member’s row and detail for portal-style reads. Like `member_page`, it uses the portal visibility rule rather than widening access just because the reader is an admin.

**Data flow**: It receives a member object name plus the signed-in member’s ID and admin flag. It asks `_member_rows` for the rows visible in the portal, looks for the requested ID, and if found combines `_row` and `_detail` into a `MemberObject`; otherwise it returns `None`.

**Call relations**: This is the portal counterpart to `get`. It calls `_member_rows` to keep portal reads aligned with the main-agent rule, then packages the same summary and detail shapes used elsewhere.

*Call graph*: calls 3 internal fn (_member_rows, _detail, _row); 1 external calls (__init__).


##### `MemberObjects.status`  (lines 115–130)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns a small status snapshot for one visible member. It is useful when the system needs just basic facts, such as the email and whether the person currently has a seat.

**Data flow**: It receives the current context, the member name, and an optional expected generation value. It looks up the matching visible row with `_visible_row`; if found, it returns a dictionary containing the email and seated state, and if not found it returns `None`.

**Call relations**: This function uses the same visibility gate as `get`, through `_visible_row`, but returns a smaller plain data shape instead of a full object detail.

*Call graph*: calls 1 internal fn (_visible_row).


##### `MemberObjects.apply`  (lines 132–211)

```
async def apply(self, ctx: ToolContext, name: str, spec: MemberSpec, old: MemberSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Changes an existing member’s admin role and/or seat. It protects workspace access by allowing only a speaking admin on the main agent to make changes, and by refusing changes that would leave the workspace without an admin or without a seated admin.

**Data flow**: It receives the current context, the target member name, the desired member settings, the old settings, and an optional expected generation. It checks that the speaker is present and using the main agent, rejects creation through this path, parses the member ID, opens a workspace database transaction, locks the workspace row, verifies the speaker is an admin, loads the target member, grants or revokes a seat if needed, checks last-admin safety rules if admin status is being removed, and finally updates the member’s admin flag when necessary. It returns nothing when the change succeeds, but it may raise an error if the request is not allowed or unsafe.

**Call relations**: This is the write path for existing member objects. It calls the context permission checks, uses `workspace_tx` and SQL queries for a safe database update, delegates seat changes to `Seats.grant` or `Seats.revoke`, and raises object-system errors such as `AdminRequired`, `UnknownObject`, or `VerbNotSupported` when the request should not proceed.

*Call graph*: calls 1 internal fn (agent_is_main); 10 external calls (__init__, __init__, __init__, __init__, select, update, workspace_tx, member_is_admin, ws_current, UUID).


##### `MemberObjects.delete`  (lines 213–220)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses deletion of member objects. The system keeps membership records and uses seating to remove access instead of deleting the member through this object interface.

**Data flow**: It receives the current context, member name, and optional expected generation. It does not inspect or change any data; it immediately raises a “verb not supported” error explaining that members cannot be deleted this way.

**Call relations**: This is the delete operation required by the object interface, but for members it is intentionally closed. It hands control back to the caller by raising `VerbNotSupported`.

*Call graph*: 1 external calls (__init__).


##### `MemberObjects._visible_rows`  (lines 222–228)

```
async def _visible_rows(self, ctx: ToolContext) -> tuple[sa.Row, ...]
```

**Purpose**: Decides which member rows the current speaker is allowed to see during an agent turn. This is the main privacy gate for listing and reading members in live conversations.

**Data flow**: It receives a tool context. If there is no signed-in speaker, it returns an empty tuple. If the audience is a channel shared with another organization, it returns only the speaker’s own row. Otherwise it checks whether the current agent is the main agent or whether the speaker is an admin, and asks `_roster` either for the whole workspace roster or just the speaker’s row.

**Call relations**: `MemberObjects.list` and `_visible_row` call this before exposing member data. It delegates the actual database fetch to `_roster` after it has decided whether the caller may see the whole roster.

*Call graph*: calls 3 internal fn (_roster, agent_is_main, speaker_is_admin); called by 2 (_visible_row, list).


##### `MemberObjects._visible_row`  (lines 230–234)

```
async def _visible_row(self, ctx: ToolContext, name: str) -> sa.Row | None
```

**Purpose**: Finds one visible member row by its text ID. It is a small helper that keeps single-member reads using the same privacy rules as member lists.

**Data flow**: It receives the current context and a member name. It asks `_visible_rows` for all rows the speaker may see, scans them for one whose ID matches the name, and returns that row or `None`.

**Call relations**: `MemberObjects.get` and `MemberObjects.status` call this when they need one member. It depends on `_visible_rows`, so a hidden member is treated the same as a missing member from the caller’s point of view.

*Call graph*: calls 1 internal fn (_visible_rows); called by 2 (get, status).


##### `MemberObjects._member_rows`  (lines 236–248)

```
async def _member_rows(self, member_id: UUID) -> tuple[sa.Row, ...]
```

**Purpose**: Decides which member rows are visible for portal reads outside a normal agent turn. It checks whether the portal request is aimed at the main agent, because only the main agent gives portal access to the full roster.

**Data flow**: It receives the signed-in member’s ID. It opens a workspace transaction, reads whether the current agent is marked as the main agent, then asks `_roster` for either the whole roster or only that member’s row.

**Call relations**: `member_page` and `member_detail` call this for portal reads. It uses `agent_current` and `ws_current` to identify the current agent and workspace, then hands off to `_roster` for the actual member query.

*Call graph*: calls 1 internal fn (_roster); called by 2 (member_detail, member_page); 4 external calls (select, workspace_tx, agent_current, ws_current).


##### `MemberObjects._roster`  (lines 250–270)

```
async def _roster(self, member_id: UUID, *, whole: bool) -> tuple[sa.Row, ...]
```

**Purpose**: Reads member rows from the database, either for the entire workspace or for one member only. It is the shared database reader behind both live-turn and portal membership views.

**Data flow**: It receives a member ID and a `whole` flag. It builds a database query for members in the current workspace, ordered by email; if `whole` is false, it adds a filter for just the given member ID. It runs the query inside a workspace transaction and returns the resulting rows as a tuple.

**Call relations**: Both `_visible_rows` and `_member_rows` call this after they have made the access-control decision. This keeps the raw roster query in one place while letting different entry paths decide how much of the roster is allowed.

*Call graph*: called by 2 (_member_rows, _visible_rows); 3 external calls (select, workspace_tx, ws_current).


##### `_row`  (lines 273–286)

```
def _row(row: sa.Row) -> ObjectRow
```

**Purpose**: Turns a database member row into a short display row for object lists. It gives the object system a stable name, a human-readable summary, and simple fields for email, admin status, and seated status.

**Data flow**: It receives a database row with member facts. It converts the member ID to text for the object name, builds a sentence-like summary, and returns an `ObjectRow` containing the visible fields.

**Call relations**: `MemberObjects.list`, `member_page`, and `member_detail` call this whenever they need the list-style representation of a member. It is the formatting step between database rows and object responses.

*Call graph*: called by 3 (list, member_detail, member_page); 1 external calls (__init__).


##### `_detail`  (lines 289–294)

```
def _detail(row: sa.Row) -> ObjectDetail[MemberSpec]
```

**Purpose**: Turns a database member row into the detailed object data used when reading one member. It captures the editable settings and the creation/update timestamps.

**Data flow**: It receives a database row. It builds a `MemberSpec` from the row’s admin and seated values, then wraps that spec with the row’s created and updated times in an `ObjectDetail`.

**Call relations**: `MemberObjects.get` and `member_detail` call this after a row has already passed visibility checks. It pairs with `_row`: `_row` is the short card, while `_detail` is the full settings view.

*Call graph*: called by 2 (get, member_detail); 2 external calls (__init__, __init__).


##### `AddMember.add`  (lines 331–363)

```
async def add(self, ctx: ToolContext, args: AddMemberInput) -> ToolResult
```

**Purpose**: Adds a new person to the workspace by email before they have contacted the agent. It is meant for admins who need to invite colleagues, contractors, or advisors directly.

**Data flow**: It receives the tool context and input containing an email, an admin flag, and a notify flag. It verifies that the speaker is a signed-in admin using the main agent, rejects externally shared channels, normalizes and validates the email, opens a workspace transaction, locks the workspace row, rechecks admin permission, confirms the email is not already a member, creates the member, and then returns a message saying what happened and whether an email invitation will be sent.

**Call relations**: This is the handler wired into the `add_member` tool definition. It calls `_absent` to prevent duplicates, calls `create_member` to insert the new member, and uses `TextContent` and `ToolResult` to send a clear result back to the tool caller.

*Call graph*: calls 2 internal fn (_absent, agent_is_main); 9 external calls (__init__, __init__, __init__, select, workspace_tx, create_member, email_domain, member_is_admin, ws_current).


##### `AddMember._absent`  (lines 365–378)

```
async def _absent(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Checks that an email address is not already a member of the current workspace. This prevents accidentally creating duplicate memberships for the same person.

**Data flow**: It receives an open database connection and a normalized email address. It searches the current workspace for a member whose email matches case-insensitively; if none is found, it returns normally, and if one is found, it raises an error telling the caller to edit that existing member instead.

**Call relations**: `AddMember.add` calls this inside the same transaction before creating a member. It acts as the duplicate-checking gate just before `create_member` is allowed to run.

*Call graph*: called by 1 (add); 3 external calls (execute, select, ws_current).


### `core/src/ufo/host/kinds/surface_kind.py`

`domain_logic` · `startup registration and request handling for surface object reads`

A “surface” is a place where conversations can happen, such as a chat provider, browser entry point, or other extension-declared channel. Extensions declare these surfaces in their manifests, but the core system needs one shared view because no single extension can see every extension’s manifest or every workspace’s installation records.

This file turns those declarations into read-only objects. Think of it like a notice board: it shows every surface the system knows about, plus a note saying whether this workspace has actually bound that surface to an agent. The declaration itself comes from the extension. The binding comes from the database table that records surface installations for the current workspace.

The main setup step is `registered_surfaces`, which checks all active manifests, rejects bad names, and refuses duplicate surface names. `SurfaceObjects` then provides the object-style operations: list surfaces, read one surface, read status, and serve admin-facing member views. All write operations are refused, because a surface is not created or deleted through this object API. It appears when an extension declares it, and it becomes connected through that surface’s own connect flow.

An important safety rule is that foreign shared audiences see nothing. Also, normal non-admin member views do not show these rows, because surface transport state is considered an admin concern.

#### Function details

##### `registered_surfaces`  (lines 54–79)

```
def registered_surfaces(manifests: tuple[Manifest, ...]) -> dict[str, RegisteredSurface]
```

**Purpose**: Builds the system’s master list of surface declarations from all active extension manifests. It also protects the system from confusing or unsafe registrations by rejecting invalid object names and duplicate surface names.

**Data flow**: It receives a tuple of extension manifests. For each declared surface, it checks that the surface name follows the object-name rules, then records which extension declared it and whether it is addressed, durable, or a home surface. It returns a dictionary keyed by surface name; if a name is invalid or reused by two extensions, it raises an error instead of returning a partial or ambiguous result.

**Call relations**: This is the boot-time gatekeeper for the rest of the file. Later, `SurfaceObjects` relies on the clean mapping produced here, so object reads can assume every surface has one clear owner and a valid object name.

*Call graph*: 3 external calls (__init__, __init__, validate_object_name).


##### `SurfaceObjects.list`  (lines 114–117)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Returns a page of surface rows for an internal tool request. It deliberately returns nothing for foreign shared audiences, so externally shared channels cannot inspect workspace surface setup.

**Data flow**: It receives a tool context and a list query. If the request comes from a foreign audience, it produces an empty page. Otherwise, it reads the workspace’s current surface installations from the database, turns the registered surfaces into rows, and wraps those rows in a paged result according to the query.

**Call relations**: This is the normal list path for tool-based object reads. It asks `_installations` for binding state, asks `_rows` to combine that state with the registered declarations, and hands the result to the shared object paging helper.

*Call graph*: calls 2 internal fn (_installations, _rows); 1 external calls (object_page).


##### `SurfaceObjects.member_page`  (lines 119–131)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Returns the admin portal’s page of surface rows for a signed-in member. Non-admin members see no rows because surface connection state is treated as workspace administration information.

**Data flow**: It receives member context, the member id, an admin flag, and a list query. If the member is not an admin, it returns an empty page. If the member is an admin, it reads current installations, builds rows for all registered surfaces, and returns the requested page.

**Call relations**: This is the member-facing counterpart to `SurfaceObjects.list`. When allowed, it follows the same path: `_installations` reads database bindings, `_rows` creates display rows, and the object paging helper formats the page.

*Call graph*: calls 2 internal fn (_installations, _rows); 1 external calls (object_page).


##### `SurfaceObjects.get`  (lines 133–138)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SurfaceObjectSpec] | None
```

**Purpose**: Reads the detailed declaration for one surface in an internal tool request. It refuses foreign audiences and unknown surface names.

**Data flow**: It receives a tool context and a surface name. If the audience is foreign or the name is not registered, it returns nothing. Otherwise, it reads the current workspace installations and builds an object detail containing the declared surface properties plus installation timestamps if the surface is bound.

**Call relations**: This is the single-object read path for tools. It checks access and existence first, then passes the installation snapshot to `_detail` so the returned detail combines manifest data with workspace binding dates.

*Call graph*: calls 2 internal fn (_detail, _installations).


##### `SurfaceObjects.member_detail`  (lines 140–156)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[SurfaceObjectSpec] | None
```

**Purpose**: Returns one surface for the admin portal, including both the list-style row and the full detail. It is only available to admins.

**Data flow**: It receives optional extension context, a surface name, the member id, and an admin flag. If the requester is not an admin or the surface is unknown, it returns nothing. Otherwise, it reads installations once, builds the row summary, builds the detailed spec, and packages both into a member object.

**Call relations**: This is the member-facing single-object read path. It reuses `_row` for the compact display information and `_detail` for the full declaration, both based on the same installation snapshot from `_installations`.

*Call graph*: calls 3 internal fn (_detail, _installations, _row); 1 external calls (__init__).


##### `SurfaceObjects.status`  (lines 158–177)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns the live binding status for one surface. This tells callers whether the workspace has a surface installation, and if so which agent it runs as and whether ingress routing is enabled.

**Data flow**: It receives a tool context, a surface name, and an optional expected generation value. Foreign audiences and unknown surfaces get no result. For a known internal request, it reads installations; if there is no binding, it returns `bound: false`. If there is a binding, it returns `bound: true` along with the agent name, whether that agent is archived, and whether this installation routes inbound traffic.

**Call relations**: This is the status endpoint for tool reads. It depends on `_installations` for the database view and then trims that information down to the status fields callers need.

*Call graph*: calls 1 internal fn (_installations).


##### `SurfaceObjects.apply`  (lines 179–188)

```
async def apply(self, ctx: ToolContext, name: str, spec: SurfaceObjectSpec, old: SurfaceObjectSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to create or update a surface object. Surfaces are declared by extensions and connected by their own setup flow, not edited through this object kind.

**Data flow**: It receives the proposed surface spec, the old spec if any, a tool context, a name, and an optional expected generation value. It does not inspect or save the spec. It immediately raises a “verb not supported” error with an explanation.

**Call relations**: This protects the read-only contract of the surface object kind. When the wider object system tries to apply a change, this function stops the write and explains that setup belongs to the surface’s own connect flow.

*Call graph*: 1 external calls (__init__).


##### `SurfaceObjects.delete`  (lines 190–197)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to delete a surface object. A surface disappears only when its declaring extension is removed or changed, not through an object deletion request.

**Data flow**: It receives a tool context, the surface name, and an optional expected generation value. It does not delete anything. It raises a “verb not supported” error explaining that registration leaves with the extension.

**Call relations**: This is the deletion guard for the object system. If a caller tries to delete a surface object, this function preserves the rule that manifests own surface registration.

*Call graph*: 1 external calls (__init__).


##### `SurfaceObjects._rows`  (lines 199–203)

```
def _rows(self, installed: Mapping[str, _BoundInstallation]) -> tuple[ObjectRow, ...]
```

**Purpose**: Builds the complete list of display rows for all registered surfaces. It keeps the list stable and easy to browse by sorting surfaces by name.

**Data flow**: It receives a mapping of installed surface bindings. It walks through the registered surfaces in sorted order, combines each declaration with any matching installation record, and returns a tuple of object rows.

**Call relations**: `SurfaceObjects.list` and `SurfaceObjects.member_page` call this after reading installations. It delegates the row-building details for each individual surface to `_row`.

*Call graph*: calls 1 internal fn (_row); called by 2 (list, member_page).


##### `SurfaceObjects._row`  (lines 205–228)

```
def _row(self, name: str, registered: RegisteredSurface, installed: Mapping[str, _BoundInstallation]) -> ObjectRow
```

**Purpose**: Creates the compact list entry for one surface. The row says which extension declared it, whether it is bound, and, if bound, which agent it is bound to.

**Data flow**: It receives a surface name, that surface’s registered declaration, and the current installation map. It checks whether this surface has a binding. It then builds a human-readable summary and structured fields such as extension, addressed, durable, home, and bound, and returns an object row.

**Call relations**: `_rows` uses this for every surface in a list, and `SurfaceObjects.member_detail` uses it when a member admin opens one surface. It is the small formatter that turns raw declaration plus binding state into a list-friendly row.

*Call graph*: called by 2 (_rows, member_detail); 1 external calls (__init__).


##### `SurfaceObjects._detail`  (lines 230–245)

```
def _detail(self, name: str, installed: Mapping[str, _BoundInstallation]) -> ObjectDetail[SurfaceObjectSpec]
```

**Purpose**: Creates the full read view for one surface. It shows the extension-declared properties and includes creation/update timestamps only if this workspace has an installation binding.

**Data flow**: It receives a surface name and the current installation map. It looks up the registered declaration, checks for a matching binding, and returns an object detail containing a `SurfaceObjectSpec` plus optional created and updated times.

**Call relations**: `SurfaceObjects.get` and `SurfaceObjects.member_detail` call this after access checks and installation lookup. It is the formatter for the detailed object view, while `_row` formats the compact list view.

*Call graph*: called by 2 (get, member_detail); 2 external calls (__init__, __init__).


##### `SurfaceObjects._installations`  (lines 247–278)

```
async def _installations(self) -> dict[str, _BoundInstallation]
```

**Purpose**: Reads the database bindings between registered surfaces and agents for the current workspace. This is how the read views know whether a surface is connected and which agent it uses.

**Data flow**: It opens a workspace database transaction, selects surface installation rows for the current workspace, joins them to their agent rows, and reads fields such as surface name, routing flag, timestamps, agent name, and archived state. It returns a dictionary keyed by surface name, where each value is a small `_BoundInstallation` record.

**Call relations**: All read operations that need binding state call this: list, member page, get, member detail, and status. It is the bridge between the object views in this file and the workspace database tables.

*Call graph*: called by 5 (get, list, member_detail, member_page, status); 4 external calls (__init__, select, workspace_tx, ws_current).


### `core/src/ufo/host/kinds/workspace_kind.py`

`domain_logic` · `request handling`

This file is the project’s doorway for asking, “What workspace am I in, and who belongs to it?” A workspace here is not something users create, edit, or delete through this object kind. It is more like the building that already exists; this file only lets people look at the building’s directory.

The workspace object has an empty spec, meaning there are no user-written settings to fill in. The useful information is all status: the total member count, the number of seated members, and a roster showing each member’s email, whether they have a seat, and whether they are an admin. A “seat” means the member currently has access.

The file protects that roster depending on who is asking. External or foreign audiences get nothing. In an internal conversation, a member talking to the main agent can see the whole roster. A child agent only answers with the speaker’s own member row. This mirrors the member object’s privacy rules.

The private helper `_shape` gathers the actual facts from the database and the seating system. The public methods then turn that shared snapshot into a list row, a detail view, or a status response. Attempts to apply changes or delete the workspace are refused with clear messages, because seating changes belong on member objects and the workspace itself is permanent.

#### Function details

##### `WorkspaceObjects.list`  (lines 73–86)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows the current workspace as a single list entry, unless the request comes from a foreign audience. The entry summarizes how many members exist and how many currently have seats.

**Data flow**: It receives the tool context and a list query. If the audience is external, it returns an empty page. Otherwise it reads the current workspace id, asks `_shape` for the latest member and seat counts, builds one row named with the workspace id, and wraps that row in a paged result using the caller’s query options.

**Call relations**: This is used when someone lists objects of kind `workspace`. It relies on `_shape` to collect the real workspace facts, uses `ws_current` to name the one workspace being served, and hands the finished row to `object_page` so it behaves like other object listings.

*Call graph*: calls 1 internal fn (_shape); 3 external calls (__init__, object_page, ws_current).


##### `WorkspaceObjects.get`  (lines 88–98)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[WorkspaceSpec] | None
```

**Purpose**: Returns the detail view for the current workspace when the caller asks for it by its exact workspace id. The detail contains timestamps and an empty spec, because there is nothing editable on the workspace object.

**Data flow**: It receives the tool context and the requested object name. If the audience is foreign, or the name is not the current workspace id, it returns nothing. Otherwise it fetches the workspace shape, creates an empty `WorkspaceSpec`, attaches the workspace creation and update times, and returns that detail object.

**Call relations**: This is called when a client reads one workspace object directly. Like `list`, it asks `_shape` for database-backed facts and uses `ws_current` to make sure the requested name is really the bound workspace.

*Call graph*: calls 1 internal fn (_shape); 3 external calls (__init__, __init__, ws_current).


##### `WorkspaceObjects.status`  (lines 100–121)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns the live status for the workspace: member count, seated count, and a roster filtered to what the caller is allowed to see. This is the main way callers learn who has access to the workspace.

**Data flow**: It receives the tool context, workspace name, and an optional expected generation value. It rejects foreign audiences and wrong workspace names by returning nothing. For a valid internal request, it reads the workspace shape, checks whether the speaker is a member using the main agent, and then returns counts plus roster entries. The roster is either the whole roster or only the speaker’s own row, depending on that permission check.

**Call relations**: This is called when a client asks for status on the workspace object. It combines `_shape` for the raw roster with `ToolContext.agent_is_main` for the visibility rule, so the final answer exposes only the appropriate people.

*Call graph*: calls 2 internal fn (_shape, agent_is_main); 1 external calls (ws_current).


##### `WorkspaceObjects.apply`  (lines 123–132)

```
async def apply(self, ctx: ToolContext, name: str, spec: WorkspaceSpec, old: WorkspaceSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create or update the workspace object. It points callers to the correct place for seat changes: applying `seated` on a member object.

**Data flow**: It receives the context, workspace name, proposed workspace spec, old spec, and optional generation check. It does not inspect or save those values. Instead, it raises a `VerbNotSupported` error with an explanation that workspace seating is controlled through member objects.

**Call relations**: This is reached when the object system tries to apply a change to a `workspace` object. Rather than handing off to storage, it stops the flow immediately so callers do not accidentally think the workspace has editable fields.

*Call graph*: 1 external calls (__init__).


##### `WorkspaceObjects.delete`  (lines 134–141)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to delete the workspace. In this system, a workspace is created automatically and is meant to remain permanent.

**Data flow**: It receives the context, workspace name, and optional generation check. It does not delete anything. It raises a `VerbNotSupported` error explaining that the workspace cannot be removed through this object kind.

**Call relations**: This is reached when the object system tries to delete a `workspace` object. It ends that path with a clear refusal, preserving the rule that workspace lifetime is not controlled by this read-only object.

*Call graph*: 1 external calls (__init__).


##### `WorkspaceObjects._shape`  (lines 143–161)

```
async def _shape(self) -> WorkspaceShape
```

**Purpose**: Collects the shared snapshot of workspace facts used by list, get, and status. It is the one place in this file that reads the database and seating information.

**Data flow**: It reads the current workspace id, opens a workspace database transaction, fetches the workspace row’s creation and update timestamps, and asks the seating system for a snapshot of members. It then packages the member count, seated count, roster, and timestamps into a `WorkspaceShape` value that the public methods can reuse.

**Call relations**: This helper sits behind `list`, `get`, and `status`. Those methods decide what the caller should receive; `_shape` supplies the accurate underlying facts by using the database transaction, the workspace table, and the `Seats` snapshot service.

*Call graph*: called by 3 (get, list, status); 5 external calls (__init__, __init__, select, workspace_tx, ws_current).


### Agent lifecycle and prompt governance
Agent objects define creation and settings changes, while governance objects route prompt edits through approval and stale-change checks.

### `core/src/ufo/runtime/kinds/agents.py`

`domain_logic` · `object and tool request handling`

An agent here is not just a running chat bot. It is a saved workspace object with a name, prompt, model choice, internet policy, skill settings, portal visibility, icon, and optional input/output rules for spawned work. This file is the rulebook and storage adapter for that object.

The main job is to protect the workspace from unsafe or confusing changes. Any speaking member can create an agent, but only the owner or a workspace admin can edit or archive it. The main agent is special: every member can use it, its visibility must stay workspace-wide, and it cannot be archived. Archiving is deliberately not a hard delete. Like moving a file to a records room, the agent disappears from normal use and releases its old name, but its conversations, grants, connected accounts, and spending history stay attached to the same database row. A restore action can bring that same row back later.

The file also checks that model names and declared JSON schemas are valid before they are saved. At the bottom, it registers the whole agent object kind and the restore action so the wider object/tool system knows how to expose them.

#### Function details

##### `_effective_model`  (lines 70–75)

```
def _effective_model(ctx: ToolContext, stored: str) -> str
```

**Purpose**: Reports the model an agent is actually using. If the saved setting says "auto", it shows the concrete model chosen for the current turn instead of exposing the placeholder.

**Data flow**: It receives the current tool context and the model value stored on the agent row. If the stored value is the special auto marker, it reads the already-resolved model from the current agent in the context; otherwise it keeps the stored value. It returns one model name for display or status output and does not change anything.

**Call relations**: Agent summaries and status reports call this when they need to show a member what model is really in use. This keeps read-only views helpful without rewriting the saved setting from "auto" into today’s chosen model.

*Call graph*: called by 2 (_status, _agent_summary).


##### `AgentSpec._declared_schema`  (lines 166–171)

```
def _declared_schema(cls, value: dict[str, JsonValue] | None, info: ValidationInfo) -> dict[str, JsonValue] | None
```

**Purpose**: Checks custom input and output schemas before they are accepted into an agent specification. A schema is a machine-readable description of the shape of data an agent spawn expects or returns.

**Data flow**: It receives a proposed schema value and information about which field is being validated. If the value is present, it passes it to the shared schema checker; if the checker accepts it, the same schema value comes back. If the value is missing, it stays missing.

**Call relations**: This runs as part of Pydantic model validation when an AgentSpec is built from user input. It hands the detailed schema rules to check_declared_schema so invalid contracts are rejected before create or update code writes them to storage.

*Call graph*: 1 external calls (check_declared_schema).


##### `_known_model`  (lines 174–191)

```
def _known_model(ctx: ToolContext, model: str, reasoning: ReasoningEffort) -> None
```

**Purpose**: Refuses to save an agent configuration that names a model this deployment cannot use, or a reasoning setting the model does not allow. This prevents a bad setting from breaking every future turn of that agent.

**Data flow**: It receives the current context, a requested model name, and a reasoning setting. It resolves the special "auto" model to the deployment’s actual automatic model, checks the available model registry, and checks whether reasoning can be turned off for that model. It returns nothing when the settings are acceptable, or raises an error with a clear message when they are not.

**Call relations**: Agent creation and agent editing both call this just before saving model-related settings. It is placed on the write path because once a bad model reaches the database, later turns would fail before the member had a chance to repair it.

*Call graph*: called by 2 (_create, _mutate).


##### `_agent_summary`  (lines 194–199)

```
def _agent_summary(ctx: ToolContext, row: sa.Row) -> str
```

**Purpose**: Builds the short human-readable line shown when agents are listed. It tells the reader whether the agent is live or archived, what model it uses, and whether public internet access is allowed.

**Data flow**: It receives the current context and one database row for an agent. It first turns the stored model into the effective display model, then chooses wording based on whether the row is archived and whether it is the main agent. It returns a short text summary and does not alter the row.

**Call relations**: The agent listing code calls this while turning database rows into object-list entries. It relies on _effective_model so list output shows the resolved model for auto-configured agents.

*Call graph*: calls 1 internal fn (_effective_model); called by 1 (_owned_rows).


##### `AgentObjects._admin_can_apply`  (lines 214–215)

```
def _admin_can_apply(self, old: AgentSpec, spec: AgentSpec) -> bool
```

**Purpose**: Says that a workspace admin is allowed to apply any agent specification change once the shared permission gate has decided the caller is an admin. In other words, this hook adds no extra admin-only restriction.

**Data flow**: It receives the old and proposed agent specifications. It does not inspect them and simply returns true, meaning the admin path is allowed to continue.

**Call relations**: This is an override used by the broader member-owned object framework when deciding whether an apply operation may proceed. The detailed validation still happens later in create or mutate logic.


##### `AgentObjects.list`  (lines 217–223)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists agents, with live agents as the default view. Archived agents only appear when the caller explicitly asks for them with an archived filter.

**Data flow**: It receives a tool context and a list query. If the query does not mention archived status, it adds a filter for non-archived agents; then it passes the adjusted query to the parent listing behavior. It returns an object page of matching agents.

**Call relations**: The object system calls this when someone lists agent objects. It lightly edits the query before handing off to the shared MemberOwnedObjects listing flow, so archived apps do not clutter normal lists.

*Call graph*: 1 external calls (replace).


##### `AgentObjects._owned_rows`  (lines 225–257)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[ObjectOwner], ...]
```

**Purpose**: Fetches the agent rows for the current workspace and turns them into ownership-aware list entries. These entries tell the shared object system who can see or act on each agent.

**Data flow**: It reads the current workspace id, queries the agent table for names, ownership, visibility, archive state, and display settings, then builds one OwnedRow per database row. Each result includes the name, summary text, owner information, and extra list fields such as id and archive timestamps.

**Call relations**: The shared list machinery uses this as the file’s database-backed source of agent objects. While building each row, it calls _agent_summary for readable text and constructs ObjectOwner values so the broader object framework can apply visibility and ownership rules.

*Call graph*: calls 1 internal fn (_agent_summary); 5 external calls (__init__, __init__, select, workspace_tx, ws_current).


##### `AgentObjects._detail`  (lines 259–292)

```
async def _detail(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> ObjectDetail[AgentSpec] | None
```

**Purpose**: Returns the full editable description of one agent, including its saved prompt and settings. This is what a caller needs before understanding or updating a specific agent.

**Data flow**: It receives a context, an agent name, and ownership information. It looks up the database row by name; if none exists, it returns nothing. Otherwise it copies the row’s saved settings into an AgentSpec, adds creation and update times, and, for non-main agents, adds a link showing they are scoped under the main agent.

**Call relations**: The object get/detail flow calls this after access has been considered. It depends on _row for the database lookup and then packages the result into ObjectDetail so the wider object system can return a consistent shape.

*Call graph*: calls 1 internal fn (_row); 4 external calls (__init__, __init__, __init__, __init__).


##### `AgentObjects._status`  (lines 294–313)

```
async def _status(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns operational facts about one agent that are useful for status displays but are not the editable spec itself. This includes whether it is main, archived, ownerless, or provisioned by another system.

**Data flow**: It receives a context, an agent name, and ownership information. It looks up the row; if it is missing, it returns nothing. If found, it builds a plain dictionary with status fields, converting ids and timestamps into JSON-friendly strings and resolving the effective model for display.

**Call relations**: The object status path calls this when a caller asks for current state. It shares the _row lookup with detail and uses _effective_model so auto model settings are reported in the same member-friendly way everywhere.

*Call graph*: calls 2 internal fn (_row, _effective_model).


##### `AgentObjects._apply_owned`  (lines 315–326)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: AgentSpec, old: AgentSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: Chooses whether an apply request should create a new agent or update an existing one. It is the single write doorway after ownership checks have passed.

**Data flow**: It receives the target name, proposed spec, any old spec, and owner information. If there is no old spec, it treats the apply as a create and sends the data to _create. If an old spec exists, it treats the apply as an update and sends the data to _mutate. It returns nothing after the chosen write path finishes.

**Call relations**: The shared object apply flow calls this once it has decided the caller is allowed to write. This method then dispatches to the file’s two concrete write routines: _create for a new row or _mutate for an existing row.

*Call graph*: calls 2 internal fn (_create, _mutate).


##### `AgentObjects._mutate`  (lines 328–406)

```
async def _mutate(self, ctx: ToolContext, name: str, spec: AgentSpec) -> None
```

**Purpose**: Updates the settings of an existing agent while preserving fields the caller intentionally omitted. It also enforces special rules, such as the main agent always staying visible to the whole workspace.

**Data flow**: It receives the context, agent name, and proposed spec. It loads the current row, rejects unknown agents and invalid model settings, fills in omitted optional fields from the existing row, checks that the prompt will not be empty, and detects whether anything actually changed. If there is a real change, it writes the new values and update time to the database.

**Call relations**: _apply_owned calls this for updates after the ownership framework has allowed the operation. It relies on _row to get current saved values and _known_model to catch model problems before writing. The actual write is done through the workspace database transaction.

*Call graph*: calls 2 internal fn (_row, _known_model); called by 1 (_apply_owned); 4 external calls (__init__, update, workspace_tx, ws_current).


##### `AgentObjects._create`  (lines 408–454)

```
async def _create(self, ctx: ToolContext, name: str, spec: AgentSpec) -> None
```

**Purpose**: Creates a brand-new non-main agent owned by the member who asked for it. It saves only the submitted configuration and does not copy grants, credentials, sources, or remembered state from anywhere else.

**Data flow**: It receives the context, requested name, and full agent spec. It requires a speaking member, requires a non-empty prompt, validates the model settings, reads existing icons so it can choose an automatic icon if needed, and inserts a new database row with a fresh id. If another agent already has the name, it turns the database conflict into a clear name-taken error.

**Call relations**: _apply_owned calls this when an apply request targets a name that has no existing agent. It uses _known_model for model safety, auto_agent_icon for a portal-friendly default icon, and the workspace transaction to commit the new row.

*Call graph*: calls 1 internal fn (_known_model); called by 1 (_apply_owned); 7 external calls (__init__, insert, select, workspace_tx, ws_current, auto_agent_icon, uuid4).


##### `AgentObjects.delete`  (lines 456–466)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Starts deletion for an agent, but first blocks attempts to delete the main agent. In this system, deleting an agent means archiving it, not erasing its history.

**Data flow**: It receives the context, agent name, and an optional expected generation value used by the broader object system for change checks. It looks up the row; if the row is the main agent, it raises a not-supported error. Otherwise it delegates the rest of the delete flow to the parent class.

**Call relations**: The object delete command reaches this method first. It performs the agent-specific main-agent guard before handing control back to the shared deletion machinery, which later calls the owned delete implementation when permissions allow.

*Call graph*: calls 1 internal fn (_row); 1 external calls (__init__).


##### `AgentObjects._delete_owned`  (lines 468–498)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: Archives an agent that the caller is allowed to delete. The agent stops accepting new turns, leaves normal portal views, and frees its old name, while its record and related history remain recoverable.

**Data flow**: It receives the context, agent name, and owner information. It loads the row, rejects missing agents, the main agent, and already archived agents, then updates the row: the live name becomes a durable archived name based on the id, the old name is saved separately, archive time is set, and update time is refreshed.

**Call relations**: The shared delete flow calls this after ownership or admin permission has been checked. It depends on _row for the current state and writes the archive marker through a workspace database transaction.

*Call graph*: calls 1 internal fn (_row); 5 external calls (__init__, __init__, update, workspace_tx, ws_current).


##### `AgentObjects._row`  (lines 500–541)

```
async def _row(self, name: str) -> sa.Row | None
```

**Purpose**: Looks up one agent row by name inside the current workspace. It is the common database reader used by detail, status, edit, delete, and archive operations.

**Data flow**: It receives an agent name. It reads the current workspace id, queries the agent table for all fields this file needs, and also includes the main agent’s name as a helper value for links. It returns one database row when found, or nothing when no matching agent exists.

**Call relations**: Most per-agent operations call this before they can do anything meaningful: detail and status use it for read views, mutate uses it for old values, delete uses it to protect the main agent, and archive uses it to confirm current state.

*Call graph*: called by 5 (_delete_owned, _detail, _mutate, _status, delete); 3 external calls (select, workspace_tx, ws_current).


##### `RestoreApplication.restore`  (lines 556–613)

```
async def restore(self, ctx: ToolContext, args: RestoreApplicationInput) -> ToolResult
```

**Purpose**: Brings an archived agent back to life under a requested name. It keeps the same underlying row, so conversations, scheduled work, connected accounts, and grants remain attached.

**Data flow**: It receives the tool context and input containing the new live name. It confirms the restore action is bound to an archived agent target, requires a speaking member, validates the requested name, extracts the archived agent id from the durable archived name, and reads the row. It only allows the owner or a workspace admin to proceed. If the row is still archived, it updates the name, clears archive fields, and returns a tool result saying the app is live again; if the requested name is already taken, it raises a clear error.

**Call relations**: This is the handler for the restore_application tool registered in this file. The tool system calls it when a user invokes the Restore action on an archived agent object; it uses the database to verify and update the row, and asks the ToolContext whether a non-owner speaker is an admin.

*Call graph*: calls 1 internal fn (speaker_is_admin); 10 external calls (__init__, __init__, __init__, __init__, select, update, workspace_tx, validate_object_name, ws_current, UUID).


### `core/src/ufo/runtime/kinds/governance.py`

`domain_logic` · `request handling`

This file is a safety gate for changing an agent’s configuration, specifically its prompt. Instead of letting code overwrite an agent prompt immediately, it records a proposed change with a fingerprint of the current prompt. Later, when someone approves the proposal, the file checks that the prompt has not changed in the meantime. This is like signing a contract for a specific draft: if someone edits the draft before approval, the old approval no longer applies.

The fingerprint is a SHA-256 digest, which is a stable short-looking string made from the full prompt text. It is used to compare prompt contents without relying on the full text everywhere.

The `Governance` class is tied to one workspace and one proposing extension. `propose_change` first checks that the target agent exists in that workspace, then stores a pending proposal containing the old prompt digest, the new prompt, and the digest of that new prompt. `approve_proposal` reads the proposal, confirms it is still pending, locks the agent row so two approvals cannot race each other, and compares the saved old digest with the agent’s current prompt digest. If they differ, the proposal is rejected. If they match, the prompt is updated and the proposal is marked approved. Important events are logged so the system can later explain what happened.

#### Function details

##### `prompt_digest`  (lines 16–17)

```
def prompt_digest(prompt: str) -> str
```

**Purpose**: This function turns a prompt into a fixed fingerprint. The system uses that fingerprint to tell whether a prompt is still exactly the same as it was earlier.

**Data flow**: It takes prompt text in, encodes it as bytes, runs it through SHA-256 hashing, and returns the hash as a hexadecimal string. It does not change anything outside itself.

**Call relations**: When a change is proposed, `Governance.propose_change` uses this to record the fingerprint of the new prompt. When a proposal is approved, `Governance.approve_proposal` uses it again to compare the agent’s current prompt with the fingerprint saved when the proposal was opened.

*Call graph*: called by 2 (approve_proposal, propose_change); 1 external calls (sha256).


##### `Governance.propose_change`  (lines 28–55)

```
async def propose_change(self, change: AgentChange) -> ProposalRef
```

**Purpose**: This function opens a new proposal to change an agent’s prompt. It records the proposed new prompt, but deliberately does not apply it yet.

**Data flow**: It receives an `AgentChange`, which includes the target agent, the expected old prompt fingerprint, and the proposed new prompt. Inside a workspace database transaction, it checks that the agent belongs to the current workspace. If the agent is missing, it raises an error. If the agent exists, it creates a new proposal ID, computes the fingerprint of the proposed prompt, inserts a pending proposal row, and returns a `ProposalRef` containing the proposal ID.

**Call relations**: This is the first half of the governed-change flow. A caller uses it when an extension or core code wants to request a prompt update. It calls `prompt_digest` to fingerprint the new prompt, uses the workspace transaction to write the proposal safely, and hands back a reference that can later be passed to `Governance.approve_proposal`.

*Call graph*: calls 1 internal fn (prompt_digest); 5 external calls (__init__, insert, select, workspace_tx, uuid4).


##### `Governance.approve_proposal`  (lines 57–114)

```
async def approve_proposal(self, proposal_id: UUID) -> None
```

**Purpose**: This function tries to apply a pending proposal. It only changes the agent prompt if the prompt is still the same as when the proposal was made.

**Data flow**: It takes a proposal ID, opens a workspace database transaction, and looks up the matching proposal in the current workspace. If the proposal does not exist or is not pending, it raises an error. It then reads and locks the target agent’s prompt. If the current prompt fingerprint does not match the proposal’s saved `from_digest`, it marks the proposal rejected and logs that rejection. If the fingerprint matches, it writes the proposed prompt into the agent row, marks the proposal approved, and logs the approval after the transaction completes.

**Call relations**: This is the second half of the governed-change flow. It consumes proposals created by `Governance.propose_change`. It calls `prompt_digest` to detect stale proposals, uses database updates to either reject or apply the proposal, and calls the logging helper so approvals and rejections are visible to observers.

*Call graph*: calls 1 internal fn (prompt_digest); 4 external calls (select, update, workspace_tx, log).

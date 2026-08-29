# Workspace object system and portal mutation APIs  `stage-7`

This stage is the system’s “object counter” after a request has been logged in and sent to the right place. A workspace object is a named record, like an agent, member, file, site, or connected account. core objects.py is the safety gate: it checks shape, visibility, ownership, and which extension owns each kind before listing, reading, changing, deleting, or running actions.

Built-in kinds cover agents, members, workspaces, conversations, artifacts, credentials, installed extensions, chat surfaces, and governed prompt-change proposals. They expose what users may inspect or edit, while blocking unsafe writes, such as editing conversations or secrets directly. Extension kinds add connectors, gbrain sources, memory records, monitors, reports, sites, user-created skills, synced pages and sources, plus scheduled-task visibility rules.

Support files make the machine smooth: listings gives stable next/previous pages, object_scope tracks which agent an action is acting for, and object_views turns internal actions into safe button-like descriptions. Finally, web panels connect portal forms and buttons to the normal conversation-based write path, so portal mutations follow the same safety rules instead of bypassing them.

## Files in this stage

### Built-in workspace kinds
Core built-in object kinds expose extensions, files, conversations, credentials, members, surfaces, and workspace facts with the appropriate read-only or administrative constraints.

### `core/src/ufo/ext/extension_kind.py`

`domain_logic` · `startup and object request handling`

This file turns the set of extension manifests loaded at startup into an object kind called `extension`. A manifest is the extension's declaration: its name, version, tools, object kinds, credential slots, chat surfaces, jobs, hooks, sources, and subagent profiles. The important point is that this view is informational. It shows the names of things an extension declares, but it never exposes secret values, and it does not treat extensions as editable database rows.

The file first defines how extension names become object names. For example, a manifest named `scheduled_tasks` becomes `scheduled-tasks`, because object names are lowercase and hyphenated. It also checks for collisions, so two extensions cannot accidentally appear under the same object name.

`ExtensionObjects` is the read-only object handler. Listing extensions returns one row per active extension, with a short summary and useful counts. Getting one extension returns a fuller declaration. Asking for status returns what the extension needs from the deploy, such as sandbox internet access or required seams from other extensions. Any attempt to create, update, or delete an extension is rejected with a clear message: installation and removal happen through the deploy lockfile and command-line tooling, not through chat or object mutation. In short, this file is the inspection window for installed extensions, not the installer.

#### Function details

##### `named_extensions`  (lines 46–61)

```
def named_extensions(manifests: tuple[Manifest, ...]) -> dict[str, Manifest]
```

**Purpose**: This function converts loaded extension manifests into the names used for `extension` objects. It also catches bad or duplicate names early, during boot, so one extension cannot silently hide another.

**Data flow**: It receives a tuple of manifests. For each manifest, it lowercases the manifest name, replaces non-letter-or-number runs with hyphens, trims extra hyphens, and checks that the result is a valid object name. It builds and returns a dictionary from that object name to the manifest; if two manifests produce the same name, it raises an error instead.

**Call relations**: The extension loader uses this kind of preparation when it turns active manifests into workspace objects. Inside the function, `re.sub` does the name cleanup, and `validate_object_name` enforces the project's object-name rules before the names are used anywhere else.

*Call graph*: 2 external calls (sub, validate_object_name).


##### `ExtensionObjects.list`  (lines 90–107)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This function lists all active extensions as simple rows that are easy to scan. It shows each extension's version and counts for tools and credential slots, which helps a user quickly understand what each extension adds.

**Data flow**: It receives a tool context and a list query, reads the stored mapping of extension object names to manifests, and turns each manifest into an `ExtensionSpec`. From each spec it creates an `ObjectRow` with a name, a human-readable summary, and sortable or filterable fields. It then passes those rows and the query into `object_page`, which returns the requested page of results.

**Call relations**: This is called when someone lists objects of kind `extension`. It relies on `ExtensionObjects._spec` to translate each raw manifest into the public shape, then hands the rows to the shared object paging helper so listing behaves like other object kinds.

*Call graph*: calls 1 internal fn (_spec); 2 external calls (__init__, object_page).


##### `ExtensionObjects.get`  (lines 109–113)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ExtensionSpec] | None
```

**Purpose**: This function returns the full public declaration for one installed extension. It is used when a user wants to inspect a specific extension rather than scan the list.

**Data flow**: It receives a tool context and an extension object name. It looks up that name in the active extension mapping. If no manifest exists, it returns `None`; otherwise it converts the manifest into an `ExtensionSpec` and wraps it in an `ObjectDetail` with no creation or update timestamps, because extensions here are declarations loaded from manifests, not editable stored rows.

**Call relations**: This is called when someone reads one `extension` object by name. It uses `ExtensionObjects._spec` for the manifest-to-public-view conversion, then returns that view in the standard object detail wrapper used by the object system.

*Call graph*: calls 1 internal fn (_spec); 1 external calls (__init__).


##### `ExtensionObjects.status`  (lines 115–128)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This function shows what an extension asks from the deploy environment. It answers questions like whether the extension wants sandbox internet access and what other extension-provided seams it depends on.

**Data flow**: It receives a tool context, an extension object name, and an optional expected generation value. It looks up the manifest by name. If the extension is not present, it returns `None`; otherwise it returns a small dictionary containing `sandbox_internet` and `requires` from the manifest.

**Call relations**: This is used when the object system asks for the status side of an `extension` object. Unlike `get`, which describes what the extension contributes, this reports what the extension requires from the deploy.


##### `ExtensionObjects.apply`  (lines 130–139)

```
async def apply(self, ctx: ToolContext, name: str, spec: ExtensionSpec, old: ExtensionSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This function deliberately refuses attempts to create or update an extension through the object interface. It protects the rule that extensions are installed and removed only through deploy configuration and lockfile changes.

**Data flow**: It receives the proposed extension name, new spec, optional old spec, context, and optional expected generation value. It does not inspect or save the proposed change. Instead, it immediately raises `VerbNotSupported` with a message explaining that extension installation is a deploy act.

**Call relations**: This is reached if someone tries to apply a change to an `extension` object. Rather than passing work onward, it stops the mutation at the boundary and returns the same policy message used for unsupported extension edits.

*Call graph*: 1 external calls (__init__).


##### `ExtensionObjects.delete`  (lines 141–148)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This function deliberately refuses attempts to delete an extension through the object interface. It prevents chat or object edits from changing the deploy's installed extension set.

**Data flow**: It receives the context, extension name, and optional expected generation value. It does not remove anything from the active manifest mapping. It immediately raises `VerbNotSupported` with the message that extensions are removed through the deploy lockfile process.

**Call relations**: This is reached when someone tries to delete an `extension` object. Like `ExtensionObjects.apply`, it enforces the read-only nature of this object kind and points users toward the deploy-level installation and removal flow.

*Call graph*: 1 external calls (__init__).


##### `ExtensionObjects._spec`  (lines 150–168)

```
def _spec(self, manifest: Manifest) -> ExtensionSpec
```

**Purpose**: This helper converts an internal manifest into the public `ExtensionSpec` shown to users. It gathers only names and declarations, not secret values or runtime data.

**Data flow**: It receives one manifest. It reads the manifest's name, version, tools, connector tools, object kinds, credential slots, surfaces, jobs, hook events, source backends, and subagent profiles. It returns an `ExtensionSpec` containing those values as simple tuples of strings.

**Call relations**: Both `ExtensionObjects.list` and `ExtensionObjects.get` call this helper so the list and detail views describe extensions in the same way. It hands the collected fields into `ExtensionSpec`, which is the public data shape for an extension declaration.

*Call graph*: called by 2 (get, list); 1 external calls (__init__).


### `core/src/ufo/kinds/artifacts.py`

`domain_logic` · `request handling for artifact object list/get/status/delete operations`

An artifact is a shared file, such as a report, image, or document produced during a turn. This file is the bridge between those raw stored files and the higher-level object system. Without it, shared files would still exist in the database and blob store, but users and agents would not have a consistent way to find them, name them, copy them back into a workspace, or remove them.

The main idea is simple: each artifact is identified by the conversation it came from plus its filename. If the same conversation shares the same filename again, that becomes a newer version of the same artifact. If another conversation shares the same filename, it is a different artifact. The file builds readable object names from the conversation id and filename, like a labeled folder on a shelf.

The `ArtifactObjects` class supplies the object operations. Listing gathers visible share records, groups versions together, adds helpful fields like size, media type, owner, source, and signed links, then returns a page. Getting returns the latest file details. Status can also copy the latest bytes back into the current workspace so a later turn can reuse the file. Deleting removes every stored version and its blobs. Creating or updating is deliberately refused because artifacts must be produced by writing a workspace file and calling `share_file`.

#### Function details

##### `artifact_media`  (lines 79–87)

```
def artifact_media(media_type: str) -> str
```

**Purpose**: Classifies a file’s declared media type into a broad bucket: image, document, or other. This gives listings a simple filter instead of forcing callers to know every possible MIME type, which is a standard text label like `image/png` or `application/pdf`.

**Data flow**: It receives a media type string, lowercases it, checks whether it looks like an image or a known document type, and returns one of three short labels. It does not change any stored data.

**Call relations**: When `ArtifactObjects._row` builds a listing row, it asks this helper for the friendly media category that appears in the row’s fields.

*Call graph*: called by 1 (_row).


##### `_document_media`  (lines 90–95)

```
def _document_media() -> sa.ColumnElement[bool]
```

**Purpose**: Builds a database condition that matches artifact rows whose media type counts as a document. It exists so document filtering can happen in the database before rows are fetched.

**Data flow**: It reads the shared artifact table’s `media_type` column, describes lower-case prefix and exact-value checks, and returns a SQL expression. The expression is later added to a query; this function itself does not run the query.

**Call relations**: `ArtifactObjects._groups` uses this when a caller filters a listing with `media=document`, and also to exclude documents when filtering for `media=other`.

*Call graph*: called by 1 (_groups); 1 external calls (or_).


##### `_member_participated`  (lines 98–110)

```
def _member_participated() -> sa.ColumnElement[bool]
```

**Purpose**: Builds a database test for whether a signed-in member had already entered the conversation before an artifact was shared. This protects members from seeing files that were shared before they were part of that conversation.

**Data flow**: It compares turns in the same workspace and conversation, looking for a member-admission turn whose sequence number is no later than the artifact’s turn. It returns a SQL `exists` condition, meaning “there is at least one matching row.”

**Call relations**: `ArtifactObjects._member_shares` adds this condition to the normal share query when building member-facing artifact pages and details.

*Call graph*: called by 1 (_member_shares); 2 external calls (literal, select).


##### `artifact_object_names`  (lines 113–133)

```
def artifact_object_names(shares: Iterable[tuple[UUID, str]]) -> dict[tuple[UUID, str], str]
```

**Purpose**: Creates stable object names for artifacts from their true identity: conversation id plus filename. This keeps files from different conversations separate even when the filenames match.

**Data flow**: It receives many `(conversation_id, filename)` pairs, removes duplicates, turns each filename into a safe slug, prefixes it with part of the conversation id, and checks for name collisions. If two identities still produce the same name, it adds a short hash suffix, then returns a dictionary from identity to object name.

**Call relations**: `ArtifactObjects._identities` calls this after reading all visible artifact identities from the database. It relies on `_slug` for readable filename pieces and `_identity_digest` only when a collision needs extra disambiguation.

*Call graph*: calls 2 internal fn (_identity_digest, _slug); called by 1 (_identities); 1 external calls (Counter).


##### `_slug`  (lines 136–138)

```
def _slug(filename: str) -> str
```

**Purpose**: Turns a filename into a short, safe, readable piece of an object name. It removes punctuation-like runs and normalizes the text so names are easier to use.

**Data flow**: It receives a filename, lowercases it, replaces non-letter-or-number runs with hyphens, trims the result, limits its length, and falls back to `artifact` if nothing usable remains.

**Call relations**: `artifact_object_names` calls this while building the base name for each artifact identity.

*Call graph*: called by 1 (artifact_object_names).


##### `_identity_digest`  (lines 141–143)

```
def _identity_digest(identity: tuple[UUID, str]) -> str
```

**Purpose**: Creates a deterministic fingerprint for a conversation-and-filename pair. It is used only as a tie-breaker when two different artifacts would otherwise get the same object name.

**Data flow**: It receives a `(conversation_id, filename)` pair, converts it to text, hashes it with SHA-256, and returns the full hexadecimal digest. The caller usually keeps only the first few characters.

**Call relations**: `artifact_object_names` calls this when its collision count shows that a plain conversation-prefix-plus-slug name is not unique enough.

*Call graph*: called by 1 (artifact_object_names); 1 external calls (sha256).


##### `ArtifactObjects.list`  (lines 168–175)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists artifact objects visible to the current tool or agent context. It is the normal agent-facing way to browse shared files.

**Data flow**: It takes the current context and list query, builds a share query limited to the context’s readable subjects, turns matching shares into grouped object rows, and wraps them into a paged response.

**Call relations**: The object system calls this for an artifact list operation. It hands the database work to `_shares` and `_rows`, then lets `object_page` apply the standard object paging format.

*Call graph*: calls 2 internal fn (_rows, _shares); 1 external calls (object_page).


##### `ArtifactObjects.member_page`  (lines 177–196)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Builds the signed-in member’s artifact index. It shows only files in conversations and audiences that member is allowed to see, even if the member is an admin.

**Data flow**: It receives a member id, admin flag, and list query. It derives the member’s audience subjects, builds a member-safe share query, converts matching shares into rows, and returns a paged object list.

**Call relations**: The member-facing portal path calls this for an artifacts page. It uses `conversation_audience` and `audience_subjects` to define the visibility fence, `_member_shares` to enforce member participation, `_rows` to shape rows, and `object_page` to package the result.

*Call graph*: calls 2 internal fn (_member_shares, _rows); 3 external calls (object_page, audience_subjects, conversation_audience).


##### `ArtifactObjects.get`  (lines 198–200)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ArtifactSpec] | None
```

**Purpose**: Fetches the details for one artifact by object name for the current tool or agent context. It describes the latest version without copying bytes into the workspace.

**Data flow**: It receives a context and object name, searches visible shares for that name, and returns `None` if no matching artifact exists. If found, it converts the versions into an object detail record.

**Call relations**: The object system calls this for an artifact get operation. It uses `_shares` to define what is visible, `_find` to resolve the name to all versions, and `_detail` to build the returned detail.

*Call graph*: calls 3 internal fn (_find, _shares, _detail).


##### `ArtifactObjects.member_detail`  (lines 202–219)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[ArtifactSpec] | None
```

**Purpose**: Fetches one artifact detail for a signed-in member’s portal view. It applies the same member visibility rules as the member artifact listing.

**Data flow**: It receives the artifact name and member information, derives the member’s allowed audience subjects, finds the matching member-visible shares, and returns `None` if absent. If present, it adds source information, builds the listing row and detail, and returns them together as a member object.

**Call relations**: The portal detail view calls this after a member selects an artifact. It uses the audience helpers, `_member_shares`, `_find`, `_sources`, `_row`, and `_detail` so the detail page matches what `member_page` would list.

*Call graph*: calls 5 internal fn (_find, _member_shares, _row, _sources, _detail); 3 external calls (__init__, audience_subjects, conversation_audience).


##### `ArtifactObjects.status`  (lines 221–265)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports the current status of an artifact and, when small enough, copies its latest bytes back into the conversation workspace. This is what lets a later turn reuse a file that an earlier turn shared.

**Data flow**: It receives a context, artifact name, and optional expected generation. It finds the visible artifact, reads the latest blob if it is below the materialization size limit, double-checks that the conversation is still visible, writes the file under `artifacts/<name>/<filename>` when bytes were loaded, optionally mints a fresh download link, and returns size, share time, turn id, version count, link, and workspace path.

**Call relations**: The object get flow calls status when it needs workspace materialization. This function uses `_find` and `_shares` to locate the artifact, `_unchanged_visible` inside a database transaction to avoid acting on a stale or newly hidden object, the blob store to read bytes, the sandbox to write the workspace file, and artifact URL helpers to create a temporary download link.

*Call graph*: calls 3 internal fn (_find, _shares, _unchanged_visible); 6 external calls (__init__, now, workspace_tx, artifact_url_expiry, mint_artifact_url, ws_current).


##### `ArtifactObjects.apply`  (lines 267–276)

```
async def apply(self, ctx: ToolContext, name: str, spec: ArtifactSpec, old: ArtifactSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects create and update attempts for artifacts. Artifacts are not edited through the object API; they are made by sharing a file with `share_file`.

**Data flow**: It receives the requested name, new spec, old spec, and expected generation, but does not use them to change anything. It immediately raises a clear “verb not supported” error telling the caller how artifacts are supposed to be produced.

**Call relations**: The object system may call this for create or update-like operations. Instead of handing off to storage, it stops the flow with `VerbNotSupported`.

*Call graph*: 1 external calls (__init__).


##### `ArtifactObjects.delete`  (lines 278–304)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Deletes an artifact and all of its versions. This removes both the database records and the stored blob bytes, so previously issued download links stop working.

**Data flow**: It receives a context, artifact name, and optional expected generation. It finds all visible versions, locks and rechecks the current conversation visibility, deletes matching shared-artifact rows from the database, verifies the expected number of rows disappeared, then deletes each file blob and preview blob from blob storage.

**Call relations**: The object system calls this for artifact deletion. It uses `_shares` and `_find` to identify the object, `_unchanged_visible` during the database transaction to guard against races, SQL deletion to remove rows, and the blob service to remove stored bytes afterward.

*Call graph*: calls 3 internal fn (_find, _shares, _unchanged_visible); 3 external calls (delete, workspace_tx, ws_current).


##### `ArtifactObjects._unchanged_visible`  (lines 306–313)

```
def _unchanged_visible(self, ctx: ToolContext, latest: sa.Row) -> sa.Select
```

**Purpose**: Builds a database query that confirms a latest artifact row still belongs to the same visible conversation. It is a safety check before copying bytes or deleting records.

**Data flow**: It receives the current context and the latest share row, then creates a query matching the current workspace, selected agent, conversation id, audience value, and readable subjects. The result is a SQL select that callers execute.

**Call relations**: `ArtifactObjects.status` uses this before writing a workspace copy, and `ArtifactObjects.delete` uses it while deleting. Both callers rely on it to catch cases where the artifact changed or became invisible between lookup and action.

*Call graph*: called by 2 (delete, status); 3 external calls (select, object_agent_id, ws_current).


##### `ArtifactObjects._rows`  (lines 315–326)

```
async def _rows(self, subjects: frozenset[str], viewer: UUID | None, query: ObjectListQuery, shares: sa.Select) -> tuple[ObjectRow, ...]
```

**Purpose**: Turns visible artifact shares into listing rows. It is the shared path used by both agent listings and member listings.

**Data flow**: It receives allowed subjects, an optional viewer id, a list query, and a base share query. It groups matching share rows by artifact, reads conversation source links for those groups, and converts each group into an `ObjectRow`.

**Call relations**: `ArtifactObjects.list` and `ArtifactObjects.member_page` call this after choosing the right visibility query. It delegates grouping to `_groups`, source lookup to `_sources`, and row formatting to `_row`.

*Call graph*: calls 3 internal fn (_groups, _row, _sources); called by 2 (list, member_page).


##### `ArtifactObjects._find`  (lines 328–348)

```
async def _find(self, subjects: frozenset[str], name: str, shares: sa.Select) -> tuple[sa.Row, ...] | None
```

**Purpose**: Resolves one artifact object name to all of its stored versions within a given visibility fence. It deliberately searches the full visible identity set, not just the latest listing window.

**Data flow**: It receives allowed subjects, a name, and a share query. It builds the visible identity-to-name map, finds the identity matching the requested name, fetches all share rows for that conversation and filename, sorts newest first, and returns the tuple or `None`.

**Call relations**: `get`, `member_detail`, `status`, and `delete` all call this when a specific artifact name must be acted on. It depends on `_identities` for stable name resolution and then uses the supplied share query so each caller’s visibility rules stay in force.

*Call graph*: calls 1 internal fn (_identities); called by 4 (delete, get, member_detail, status); 2 external calls (where, workspace_tx).


##### `ArtifactObjects._groups`  (lines 350–418)

```
async def _groups(self, subjects: frozenset[str], viewer: UUID | None, query: ObjectListQuery, shares: sa.Select) -> Sequence[tuple[str, tuple[sa.Row, ...]]]
```

**Purpose**: Builds grouped artifact versions for a listing request. It applies search and supported filters before limiting the scan, so common browsing stays fast while filtered listings still find matching files.

**Data flow**: It receives subjects, an optional viewer id, a list query, and a base share query. It adds search text and filters such as conversation, mine, and media type, fetches up to the scan limit of newest matching share rows, groups them by conversation and filename, attaches stable object names, sorts versions newest first, and returns groups sorted by name.

**Call relations**: `_rows` calls this as the first step in building listing rows. It uses `_document_media` for document filtering, `_identities` for stable names, and database access to fetch the narrowed share rows.

*Call graph*: calls 2 internal fn (_identities, _document_media); called by 1 (_rows); 5 external calls (false, not_, or_, workspace_tx, UUID).


##### `ArtifactObjects._identities`  (lines 420–443)

```
async def _identities(self, subjects: frozenset[str]) -> dict[tuple[UUID, str], str]
```

**Purpose**: Finds every distinct artifact identity visible to a set of audience subjects and assigns each one its stable object name. This keeps a file’s name the same whether it appears in a listing or is fetched directly.

**Data flow**: It receives allowed subjects, queries the database for distinct conversation-and-filename pairs in the current workspace, selected agent, and visible audiences, then passes those pairs to `artifact_object_names`. It returns a dictionary from identity to object name.

**Call relations**: `_find` uses this to resolve a requested name, and `_groups` uses it to name listed groups. It combines database selection with the naming helper `artifact_object_names`.

*Call graph*: calls 1 internal fn (artifact_object_names); called by 2 (_find, _groups); 4 external calls (select, workspace_tx, object_agent_id, ws_current).


##### `ArtifactObjects._shares`  (lines 445–480)

```
def _shares(self, subjects: frozenset[str]) -> sa.Select
```

**Purpose**: Builds the base database query for artifact share rows visible to the current agent and audience subjects. It is the common starting point for listing, getting, status, deleting, and member-specific queries.

**Data flow**: It receives a set of subjects and returns a SQL select that joins shared artifacts to their turns, conversations, and optional member owner. The query selects file metadata, conversation metadata, owner information, and preview information, limited to the current workspace, selected agent, and allowed audiences.

**Call relations**: `list`, `get`, `status`, and `delete` call this directly for agent-visible operations. `_member_shares` calls it and adds the extra member-participation rule.

*Call graph*: called by 5 (_member_shares, delete, get, list, status); 3 external calls (select, object_agent_id, ws_current).


##### `ArtifactObjects._member_shares`  (lines 482–483)

```
def _member_shares(self, subjects: frozenset[str]) -> sa.Select
```

**Purpose**: Builds the member-safe version of the artifact share query. It starts with normal visibility and then keeps only shares made after a member had entered the conversation.

**Data flow**: It receives allowed subjects, creates the base share query through `_shares`, adds the `_member_participated` condition, and returns the narrowed SQL query.

**Call relations**: `member_page` and `member_detail` use this so portal readers see only artifacts that satisfy both audience rules and member-entry timing.

*Call graph*: calls 2 internal fn (_shares, _member_participated); called by 2 (member_detail, member_page).


##### `ArtifactObjects._sources`  (lines 485–522)

```
async def _sources(self, conversation_ids: Sequence[UUID]) -> dict[UUID, str | None]
```

**Purpose**: Looks up where each conversation originally came from, such as the source link recorded on its first turn. This adds helpful origin context to artifact listings.

**Data flow**: It receives conversation ids. If there are none, it returns an empty dictionary. Otherwise it finds the earliest turn for each conversation, reads its stored context, validates that context into a `TurnContext`, extracts the source field, and returns a mapping from conversation id to source or `None`.

**Call relations**: `_rows` calls this for all conversations in a listing, and `member_detail` calls it for the selected artifact’s conversation. `_row` then uses the returned mapping when filling the `source` field.

*Call graph*: called by 2 (_rows, member_detail); 5 external calls (model_validate, and_, select, workspace_tx, ws_current).


##### `ArtifactObjects._row`  (lines 524–552)

```
def _row(self, name: str, shares: tuple[sa.Row, ...], viewer: UUID | None, sources: dict[UUID, str | None]) -> ObjectRow
```

**Purpose**: Formats one grouped artifact into a single listing row. It chooses the latest version as the current display version while still summarizing the whole group.

**Data flow**: It receives the object name, all share rows for that artifact, an optional viewer id, and source information. It reads the newest share, creates a summary, fills fields such as filename, subject, conversation id, media, size, owner, origin, source, whether it is mine, and optional download and preview links, then returns an `ObjectRow`.

**Call relations**: `_rows` uses this for each listed artifact group, and `member_detail` uses it for the selected member object. It calls `_summary`, `artifact_media`, `_download_url`, and `_preview_url` to fill user-facing fields.

*Call graph*: calls 4 internal fn (_download_url, _preview_url, _summary, artifact_media); called by 2 (_rows, member_detail); 1 external calls (__init__).


##### `ArtifactObjects._download_url`  (lines 554–563)

```
def _download_url(self, latest: sa.Row) -> str | None
```

**Purpose**: Creates a temporary signed download URL for an artifact blob when this deployment is configured to publish such links. A signed URL is a link with a built-in token that proves the holder may download the file for a limited time.

**Data flow**: It receives the latest share row. If either the token secret or public base URL is missing, it returns `None`. Otherwise it computes an expiry time, mints a signed artifact path for the blob in the current workspace, prefixes it with the public base URL, and returns the full URL.

**Call relations**: `_row` calls this while building listing fields. It uses the artifact URL helpers and current workspace id so the link points to the right stored blob.

*Call graph*: called by 1 (_row); 4 external calls (now, artifact_url_expiry, mint_artifact_url, ws_current).


##### `ArtifactObjects._preview_url`  (lines 565–585)

```
def _preview_url(self, latest: sa.Row) -> str | None
```

**Purpose**: Creates a signed image-preview URL when an artifact has previewable image bytes. It supports either a separate rasterized preview blob or the original file if it is already an image.

**Data flow**: It receives the latest share row, chooses the preview blob if present or the original blob otherwise, checks that the blob key’s image type matches the declared media type, and returns `None` if it is not eligible. If eligible, it mints and returns a preview URL for the current workspace.

**Call relations**: `_row` calls this to fill the `preview_url` field in listings and member details. It relies on `raster_image_media_type` to avoid offering previews for mismatched or non-image blobs, then hands off to `mint_image_preview_url`.

*Call graph*: called by 1 (_row); 3 external calls (mint_image_preview_url, raster_image_media_type, ws_current).


##### `_detail`  (lines 588–604)

```
def _detail(shares: tuple[sa.Row, ...]) -> ObjectDetail[ArtifactSpec]
```

**Purpose**: Builds the full object detail record for an artifact. It describes the latest version’s spec and links the artifact back to the conversation where it was created.

**Data flow**: It receives all share rows for one artifact, newest first. It uses the newest row for filename, media type, subject, and update time, uses the oldest row for creation time, adds a `created_in` link to the conversation, and returns an `ObjectDetail`.

**Call relations**: `ArtifactObjects.get` and `ArtifactObjects.member_detail` call this after `_find` has gathered the artifact’s versions.

*Call graph*: called by 2 (get, member_detail); 4 external calls (__init__, __init__, __init__, __init__).


##### `_summary`  (lines 607–613)

```
def _summary(shares: tuple[sa.Row, ...]) -> str
```

**Purpose**: Creates a short human-readable summary for an artifact listing row. It gives the filename, media type, size, share date, and version count when there is more than one version.

**Data flow**: It receives all share rows for one artifact, reads the latest row and the number of versions, builds a sentence-like summary, and trims it to the maximum summary length.

**Call relations**: `ArtifactObjects._row` calls this while formatting an artifact group into an `ObjectRow`.

*Call graph*: called by 1 (_row).


##### `artifact_object`  (lines 616–679)

```
def artifact_object(*, public_base_url: str | None=None, artifact_token_secret: str='') -> ObjectKind
```

**Purpose**: Constructs the registered artifact object kind for the object system. This is the package that tells the rest of the system what artifacts are, what fields they list, which actions are allowed, and which store object performs those actions.

**Data flow**: It receives optional deployment settings for public URLs and artifact link signing. It creates an `ArtifactObjects` store with those settings, then returns an `ObjectKind` named `artifact` with its description, guidance, spec model, list fields, and allowed agent verbs.

**Call relations**: Startup or object-kind registration code calls this to make artifacts available. The returned `ObjectKind` points list/get/delete operations to an `ArtifactObjects` instance and deliberately omits create/update as supported agent actions.

*Call graph*: 2 external calls (__init__, __init__).


### `core/src/ufo/kinds/conversations.py`

`domain_logic` · `request handling`

A conversation in this system is the record of a chat that happened on some surface, such as the web portal, Slack, or an extension. Other objects, like artifacts or scheduled tasks, can point back to the conversation they came from. This file is the bridge that makes those links useful: given a conversation id, it can show the conversation’s surface, label, audience, timestamps, and related agent.

The file is careful about privacy. Most reads are limited to conversations whose audience matches the subjects the current caller is allowed to read. A subject is a label meaning “this person or group is allowed in.” A workspace admin can see limited metadata for another member’s private conversation, but only when explicitly asking for private rows, and not for foreign-audience conversations. Even then, transcript text is not exposed.

For portal member views, the file builds a chat list from `ConversationDirectory`, separating “mine” from “others,” skipping empty or untitled conversations, and adding display fields such as title, speaker, surface, and last activity time.

The `status` path can materialize the visible text exchange into a workspace file, like printing a readable copy of the chat into a folder. But create, update, and delete all refuse: conversations belong to the surfaces that created them.

#### Function details

##### `ConversationObjects.list`  (lines 83–90)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists conversation rows visible to the current tool call. If the caller is a qualifying admin and explicitly asks for private rows, it also includes limited metadata for other members’ private conversations.

**Data flow**: It receives a tool context with readable subjects and a list query with filters. It reads matching conversation rows from the database, converts each row into a simple object-list row, optionally adds private metadata rows, and then returns a paged result shaped by the query.

**Call relations**: This is the main list entry for the object kind. It asks `_rows` for normally visible conversations, uses `_widens_for_admin` and `_private_rows` only for the explicit private case, turns database rows into display rows with `_row`, and hands the final set to `object_page` for filtering, sorting, and paging.

*Call graph*: calls 4 internal fn (_private_rows, _rows, _widens_for_admin, _row); 1 external calls (object_page).


##### `ConversationObjects.get`  (lines 92–96)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ConversationSpec] | None
```

**Purpose**: Looks up one conversation by name, where the name is expected to be its UUID string. It returns the full object detail if the caller may see it, including the surface, audience, timestamps, and link to the agent.

**Data flow**: It receives the current tool context and a name. It first searches among normally visible conversations, then falls back to the admin-only private metadata path, and finally converts a found row into an object detail or returns nothing.

**Call relations**: This is the single-object read path. It delegates ordinary visibility to `_find`, private admin lookup to `_private_find`, and formatting to `_detail`.

*Call graph*: calls 3 internal fn (_find, _private_find, _detail).


##### `ConversationObjects.member_page`  (lines 98–135)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Builds the portal’s conversation list for a signed-in member outside an active turn. It shows the member’s own conversations and a smaller set of readable conversations involving others, but it does not expand just because the member is an admin.

**Data flow**: It receives portal context, member id, admin flag, and a query. It asks `ConversationDirectory` for two groups, “mine” and “others,” keeps only conversations with titles, converts each entry into a portal-friendly row, and returns a paged list.

**Call relations**: This is used by the member-facing portal rail rather than the in-turn tool object API. It gets the current workspace and selected agent, reads from `ConversationDirectory`, formats each listed conversation with `_member_row`, and sends the combined rows to `object_page`.

*Call graph*: calls 1 internal fn (_member_row); 4 external calls (__init__, object_agent_id, object_page, ws_current).


##### `ConversationObjects.member_detail`  (lines 137–152)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[ConversationSpec] | None
```

**Purpose**: Returns one conversation detail for a signed-in portal member outside a turn, but only when that member’s own allowed audience subjects can see it. It deliberately does not let admins browse every private conversation through this path.

**Data flow**: It receives portal context, conversation name, member id, and admin flag. It builds the audience subjects for that member, searches for a matching visible row, and returns both a list-style row and detailed metadata if found.

**Call relations**: This is the portal companion to `member_page`. It uses `conversation_audience` and `audience_subjects` to decide what the member may read, `_find` to locate the row, `_row` for the compact row, `_detail` for full detail, and wraps both in `MemberObject`.

*Call graph*: calls 3 internal fn (_find, _detail, _row); 3 external calls (__init__, audience_subjects, conversation_audience).


##### `ConversationObjects.status`  (lines 154–172)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports transcript status for a visible conversation and, when small enough, writes a readable transcript file into the caller’s workspace. This gives tools a safe way to inspect the text exchange without editing the conversation.

**Data flow**: It receives the tool context, conversation name, and an expected generation value that is not used here. It finds the visible conversation, reads and formats its transcript, double-checks that the row is still visible and unchanged, writes a text file if there is content within the size limit, and returns message count, byte size, and optional workspace path.

**Call relations**: This is the object status path. It locates the row through `_find`, gets transcript lines through `_exchange`, protects against a visibility race with `_unchanged_visible`, and raises `UnknownObject` if the conversation is no longer safely readable.

*Call graph*: calls 3 internal fn (_exchange, _find, _unchanged_visible); 1 external calls (__init__).


##### `ConversationObjects.apply`  (lines 174–183)

```
async def apply(self, ctx: ToolContext, name: str, spec: ConversationSpec, old: ConversationSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create or update a conversation through the object system. This protects the rule that only chat surfaces create and close conversations.

**Data flow**: It receives the proposed conversation spec and related context, but does not inspect or save them. It immediately raises a “verb not supported” error explaining that conversations are surface-made.

**Call relations**: This is called when the object framework tries to apply a change. Instead of handing off to storage, it stops the flow with `VerbNotSupported`.

*Call graph*: 1 external calls (__init__).


##### `ConversationObjects.delete`  (lines 185–192)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to delete a conversation through the object system. Conversation lifetime is controlled elsewhere, such as by retention rules, not by callers editing objects.

**Data flow**: It receives the tool context, name, and expected generation value, but makes no database change. It immediately raises a “verb not supported” error.

**Call relations**: This is the delete counterpart to `apply`. When the object framework asks to remove a conversation, it ends the request by constructing `VerbNotSupported`.

*Call graph*: 1 external calls (__init__).


##### `ConversationObjects._exchange`  (lines 194–214)

```
async def _exchange(self, ctx: ToolContext, conversation_id: UUID) -> tuple[str, ...]
```

**Purpose**: Reads a stored conversation transcript and turns it into plain text lines like “user: hello” or “assistant: hi.” It hides storage details from the status code.

**Data flow**: It receives a tool context and conversation id. It builds the blob key for that transcript, fetches and decodes the blob, extracts text from string content or text blocks, and returns a tuple of role-prefixed lines. If the blob is missing, it returns an empty exchange; if the transcript is unreadable, it raises an error.

**Call relations**: `status` calls this when it needs the transcript text. This helper hands off to `transcript_key` to find the blob and `decode` to understand the transcript format.

*Call graph*: called by 1 (status); 2 external calls (decode, transcript_key).


##### `ConversationObjects._unchanged_visible`  (lines 216–229)

```
async def _unchanged_visible(self, subjects: frozenset[str], row: sa.Row) -> bool
```

**Purpose**: Checks that a conversation row is still visible to the same subjects and still has the same audience after its transcript was read. This avoids exposing text if permissions changed mid-request.

**Data flow**: It receives the allowed subjects and the earlier database row. It opens a workspace database transaction, builds a visibility query for the same conversation id and audience, and returns true or false depending on whether that row still exists under those rules.

**Call relations**: `status` calls this after `_exchange`. It uses `_visible` to reuse the normal visibility rule, then SQL `exists` and `select` inside `workspace_tx` to make the safety check.

*Call graph*: calls 1 internal fn (_visible); called by 1 (status); 3 external calls (exists, select, workspace_tx).


##### `ConversationObjects._find`  (lines 231–237)

```
async def _find(self, subjects: frozenset[str], name: str) -> sa.Row | None
```

**Purpose**: Finds one normally visible conversation by UUID name. It rejects names that are not valid UUIDs instead of treating them as database ids.

**Data flow**: It receives readable subjects and a name string. It tries to parse the name as a UUID, asks `_rows` for that exact conversation if parsing succeeds, and returns the first matching row or nothing.

**Call relations**: `get`, `member_detail`, and `status` use this as their standard lookup path. It relies on `_rows` to apply the actual database and visibility rules.

*Call graph*: calls 1 internal fn (_rows); called by 3 (get, member_detail, status); 1 external calls (UUID).


##### `ConversationObjects._rows`  (lines 239–246)

```
async def _rows(self, subjects: frozenset[str], *, conversation_id: UUID | None) -> tuple[sa.Row, ...]
```

**Purpose**: Fetches database rows for conversations of the selected agent that are visible to a given set of subjects. It can return all visible rows or just one conversation id.

**Data flow**: It receives readable subjects and an optional conversation id. It starts from the shared visible-conversation query, narrows it to one id if provided, runs it in the current workspace database transaction, and returns the resulting rows.

**Call relations**: `list` uses this for the normal listing, and `_find` uses it for single-object lookup. It builds on `_visible`, which in turn builds on the selected-agent conversation query.

*Call graph*: calls 1 internal fn (_visible); called by 2 (_find, list); 1 external calls (workspace_tx).


##### `ConversationObjects._widens_for_admin`  (lines 248–251)

```
async def _widens_for_admin(self, ctx: ToolContext) -> bool
```

**Purpose**: Decides whether the current tool call is allowed to widen its view to private conversation metadata. It blocks that widening for foreign-audience contexts even if the speaker is an admin.

**Data flow**: It receives the tool context. It checks whether the context audience starts with the foreign-audience prefix; if so, it returns false. Otherwise it asks whether the speaker is a workspace admin and returns that answer.

**Call relations**: `list` uses this before including explicit private rows, and `_private_find` uses it before searching private metadata. It delegates the actual admin check to `ToolContext.speaker_is_admin`.

*Call graph*: calls 1 internal fn (speaker_is_admin); called by 2 (_private_find, list).


##### `ConversationObjects._private_find`  (lines 253–261)

```
async def _private_find(self, ctx: ToolContext, name: str) -> sa.Row | None
```

**Purpose**: Finds one private conversation metadata row for admins who are allowed to see such metadata. It does not expose transcript content.

**Data flow**: It receives the tool context and a name. It first checks whether admin widening is allowed, then parses the name as a UUID, asks `_private_rows` for that exact id, and returns the first match or nothing.

**Call relations**: `get` calls this only after normal lookup fails. It uses `_widens_for_admin` as the gate and `_private_rows` as the database search.

*Call graph*: calls 2 internal fn (_private_rows, _widens_for_admin); called by 1 (get); 1 external calls (UUID).


##### `ConversationObjects._private_rows`  (lines 263–273)

```
async def _private_rows(self, ctx: ToolContext, *, conversation_id: UUID | None) -> tuple[sa.Row, ...]
```

**Purpose**: Fetches private conversation metadata for the selected agent where the audience belongs to a member but is not among the caller’s readable subjects. This is the restricted admin-only view used for metadata, not content.

**Data flow**: It receives the tool context and an optional conversation id. It starts from all conversations for the selected agent, filters to member-private audiences outside the caller’s subjects, optionally narrows to one id, runs the query in a workspace transaction, and returns the rows.

**Call relations**: `list` calls this when an admin explicitly asks for private rows, and `_private_find` calls it for one private metadata lookup. It builds its base query through `_agent_conversations`.

*Call graph*: calls 1 internal fn (_agent_conversations); called by 2 (_private_find, list); 1 external calls (workspace_tx).


##### `_agent_conversations`  (lines 276–297)

```
def _agent_conversations() -> sa.Select
```

**Purpose**: Builds the common database query for conversations belonging to the current workspace and selected agent. This keeps all conversation reads anchored to the same workspace and agent boundary.

**Data flow**: It reads the current workspace id and selected agent id, then creates a SQL query joining conversations to agents. The query selects conversation fields plus the agent’s current or archived name, and returns the unfinished query for other helpers to refine.

**Call relations**: _visible uses this for ordinary visibility, and `_private_rows` uses it for admin metadata reads. It relies on `ws_current` and `object_agent_id` to know the active workspace and agent.

*Call graph*: called by 2 (_private_rows, _visible); 3 external calls (select, object_agent_id, ws_current).


##### `_visible`  (lines 300–301)

```
def _visible(subjects: frozenset[str]) -> sa.Select
```

**Purpose**: Adds the normal audience visibility rule to the selected-agent conversation query. A conversation is visible here only if its audience is one of the caller’s allowed subjects.

**Data flow**: It receives a frozen set of subject strings. It starts with `_agent_conversations`, adds a database condition requiring the conversation audience to be in that set, and returns the refined query.

**Call relations**: `_rows` uses this to fetch visible conversations, and `_unchanged_visible` uses it to re-check safety during status generation. It is the central shared visibility filter for ordinary reads.

*Call graph*: calls 1 internal fn (_agent_conversations); called by 2 (_rows, _unchanged_visible).


##### `_member_row`  (lines 304–322)

```
def _member_row(entry: ListedConversation, *, mine: bool) -> ObjectRow
```

**Purpose**: Turns a portal directory conversation entry into an object-list row tailored for the member chat list. It adds user-friendly fields such as title, whether it is mine, speaker, surface, portal availability, and last activity time.

**Data flow**: It receives a listed conversation and a flag saying whether it belongs to the member. It chooses speaker display text, picks the latest useful timestamp, computes whether the portal transport can carry the conversation, and returns an `ObjectRow`.

**Call relations**: `member_page` calls this for each conversation returned by `ConversationDirectory`. The resulting rows are then passed to `object_page` for query filtering and paging.

*Call graph*: called by 1 (member_page); 1 external calls (__init__).


##### `_row`  (lines 325–340)

```
def _row(row: sa.Row, *, private: bool=False) -> ObjectRow
```

**Purpose**: Turns a database conversation row into a compact object-list row for the general object API. It creates a readable summary like where the conversation happened and when it was created.

**Data flow**: It receives a database row and an optional private flag. It builds an origin phrase from the surface and surface label, fills the row fields with surface information and possibly `private: true`, and returns an `ObjectRow` named by the conversation id.

**Call relations**: `list` uses this for ordinary and private list rows, and `member_detail` uses it for the row part of a member object. It is the display formatter for raw conversation rows.

*Call graph*: called by 2 (list, member_detail); 1 external calls (__init__).


##### `_detail`  (lines 343–355)

```
def _detail(row: sa.Row) -> ObjectDetail[ConversationSpec]
```

**Purpose**: Turns a database conversation row into full object detail. The detail contains the conversation spec, timestamps, and a link showing which agent the conversation is scoped to.

**Data flow**: It receives a database row. It copies surface, surface label, and audience into a `ConversationSpec`, copies created and updated timestamps, creates a `scoped_to` link to the agent, and returns an `ObjectDetail`.

**Call relations**: `get` uses this when returning a conversation object detail, and `member_detail` uses it for portal detail. It constructs `ConversationSpec`, `ObjectRef`, `ObjectLink`, and `ObjectDetail` to fit the object framework’s expected shape.

*Call graph*: called by 2 (get, member_detail); 4 external calls (__init__, __init__, __init__, __init__).


### `core/src/ufo/kinds/credential_kind.py`

`domain_logic` · `request handling`

Some extensions need outside credentials, such as an API key, but those secrets must not be exposed through normal object reads. This file solves that by treating each declared credential slot like a labeled lockbox: everyone can see the label and whether the box is empty, but nobody can see what is inside.

The declarations come from active extension manifests. A database row exists only when a slot has been filled with a sealed value. If there is no row, the slot is still listed because the slot comes from the extension declaration, not from the stored secret table.

The main class, CredentialObjects, provides the object-kind behavior. It can list all slots, show detail for one slot, report status, and clear a stored value. Listing and detail include safe information such as the slot name, description, extension name, fill state, and possible host choices. They do not read or return the encrypted credential column.

Creating or updating a credential object is refused on purpose. Filling or rotating a secret must go through a separate private handoff called request_credentials, where permissions and secret handling are tighter. Deleting is allowed only for a workspace admin, and it clears the stored credential while leaving the declared slot visible as empty.

#### Function details

##### `CredentialObjects.list`  (lines 77–78)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Returns a paged list of all declared credential slots for the current workspace. It shows safe summary information, including whether each slot is filled, but never the credential value.

**Data flow**: It receives a tool context and a list query with paging or filtering instructions. It asks _rows to build the safe row list, then passes those rows and the query to object_page, which returns the requested page. Nothing secret is read or returned.

**Call relations**: This is the normal workspace object-list path. It relies on _rows to collect the slot summaries, then hands the finished list to the shared object paging helper so credential slots behave like other object lists.

*Call graph*: calls 1 internal fn (_rows); 1 external calls (object_page).


##### `CredentialObjects.member_page`  (lines 80–90)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Returns the same kind of credential-slot list for a signed-in member portal view. Because credential declarations are workspace-wide and reveal no secret values, members see the same safe slot index.

**Data flow**: It receives member information, admin status, and a list query. It builds the safe rows with _rows and turns them into a page with object_page. The member_id and admin flag do not change the rows here because no member-specific secret data is exposed.

**Call relations**: This is the member-facing list path. Like list, it delegates row construction to _rows and paging to object_page, keeping the portal view aligned with the regular object view.

*Call graph*: calls 1 internal fn (_rows); 1 external calls (object_page).


##### `CredentialObjects.get`  (lines 92–93)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[CredentialSpec] | None
```

**Purpose**: Returns safe detail for one credential slot by name. The detail describes the declared slot and its timestamps if it has been filled, but it does not include the secret.

**Data flow**: It receives a tool context and a slot name. It passes the name to _detail, which looks up the declaration and any matching stored row timestamps. The result is either an ObjectDetail or None if no extension declared that slot.

**Call relations**: This is the normal workspace object-detail path. It is a thin wrapper around _detail, which centralizes the safe detail-building logic also used by member_detail.

*Call graph*: calls 1 internal fn (_detail).


##### `CredentialObjects.member_detail`  (lines 95–110)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[CredentialSpec] | None
```

**Purpose**: Returns the member-portal view of a single credential slot. It combines the list-row summary with the detailed declaration so the portal can show both the slot’s status and its safe metadata.

**Data flow**: It receives an optional extension context, the slot name, and member/admin information. It asks _detail for the safe detail; if the slot is unknown, it returns None. If the slot exists, it also gets the current rows from _rows, finds the matching row, and wraps the row and detail in a MemberObject.

**Call relations**: This is the member-facing detail path. It reuses _detail for the declaration and _rows for the fill-state row, then hands both to MemberObject so the portal gets the same safe information as the object system.

*Call graph*: calls 2 internal fn (_detail, _rows); 1 external calls (__init__).


##### `CredentialObjects.status`  (lines 112–136)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports whether a declared credential slot is currently filled, and may include the resolved host tied to that slot. This is a safe status check, not a way to read the credential.

**Data flow**: It receives a context, slot name, and optional expected generation value. It first maps known slot names through _named; if the slot is not declared, it returns None. For a known slot, it opens a workspace database transaction, checks whether a row exists for the current workspace and slot, and returns a dictionary with filled set to true or false. If the slot has host information and a credential store is available, it also asks credential_host for the safe host value and includes it.

**Call relations**: This supports callers that need the current fill state of one slot. It uses _named to reject undeclared slots, workspace_tx and ws_current to read only the active workspace’s rows, and credential_host when host information must be resolved from the credential system.

*Call graph*: calls 1 internal fn (_named); 4 external calls (select, credential_host, workspace_tx, ws_current).


##### `CredentialObjects.apply`  (lines 138–147)

```
async def apply(self, ctx: ToolContext, name: str, spec: CredentialSpec, old: CredentialSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses create and update attempts for credential objects. This protects secrets by forcing fills and rotations through the separate request_credentials flow, which is designed for private secret handoff.

**Data flow**: It receives the proposed credential spec, any old spec, and an optional expected generation value. Instead of storing anything, it immediately raises a VerbNotSupported error with an explanation. No database data is read or changed.

**Call relations**: This is called when the object framework tries to create or update a credential object. Rather than handing off to storage, it stops the operation and points the caller toward the safer credential-request mechanism.

*Call graph*: 1 external calls (__init__).


##### `CredentialObjects.delete`  (lines 149–165)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Clears the stored value for a credential slot. Only a workspace admin can do this, and the slot remains visible afterward because the declaration still comes from the extension manifest.

**Data flow**: It receives a context, slot name, and optional expected generation value. It asks the context whether the current speaker is an admin; if not, it raises AdminRequired. If allowed, it finds the declared slot by name, opens a workspace database transaction, and deletes the matching credential row for the current workspace. The output is no returned value, but the stored credential is removed if it existed.

**Call relations**: This is the object delete path for credentials. It uses ToolContext.speaker_is_admin as the permission gate, _named to translate the public name to the declared slot, and workspace_tx with ws_current to delete only the active workspace’s stored value.

*Call graph*: calls 2 internal fn (_named, speaker_is_admin); 4 external calls (__init__, delete, workspace_tx, ws_current).


##### `CredentialObjects._rows`  (lines 167–179)

```
async def _rows(self) -> tuple[ObjectRow, ...]
```

**Purpose**: Builds the safe list rows for all declared credential slots. Each row says which extension declared the slot and whether it is filled or empty.

**Data flow**: It asks _filled_slots for the set of slot names that currently have stored rows. It asks _named for the declared slots keyed by name. Then it creates one ObjectRow per declared slot, with a human-readable summary and safe fields such as extension and filled. It returns all rows as a tuple sorted by slot name.

**Call relations**: This is the shared row builder used by list, member_page, and member_detail. It joins two safe sources of information: declarations from _named and fill state from _filled_slots, without touching the secret values.

*Call graph*: calls 2 internal fn (_filled_slots, _named); called by 3 (list, member_detail, member_page); 1 external calls (__init__).


##### `CredentialObjects._detail`  (lines 181–205)

```
async def _detail(self, name: str) -> ObjectDetail[CredentialSpec] | None
```

**Purpose**: Builds the safe detail view for one declared credential slot. It describes what the slot is for, which extension declared it, and any host options, plus timestamps if the slot has been filled.

**Data flow**: It receives a slot name and looks it up with _named. If no declaration exists, it returns None. If the slot exists, it opens a workspace database transaction and checks for the stored row’s created and updated timestamps in the current workspace. It then returns an ObjectDetail containing a CredentialSpec made from the declaration and timestamps from the row, or null timestamps if no row exists.

**Call relations**: This is the shared detail builder used by get and member_detail. It combines extension-declared metadata with database timestamps through workspace_tx and ws_current, while deliberately leaving out the credential value.

*Call graph*: calls 1 internal fn (_named); called by 2 (get, member_detail); 5 external calls (__init__, __init__, select, workspace_tx, ws_current).


##### `CredentialObjects._named`  (lines 207–208)

```
def _named(self) -> dict[str, DeclaredSlot]
```

**Purpose**: Turns the stored collection of declared credential slots into a lookup table by slot name. This lets other methods quickly find the declaration for a requested slot.

**Data flow**: It reads this CredentialObjects instance’s slots tuple. It passes those declarations to named_slots, which returns a dictionary keyed by slot name. The result is used to validate names and retrieve slot metadata.

**Call relations**: This is a small helper used by _detail, _rows, delete, and status. It keeps name lookup consistent across reads, status checks, and deletion.

*Call graph*: called by 4 (_detail, _rows, delete, status); 1 external calls (named_slots).


##### `CredentialObjects._filled_slots`  (lines 210–219)

```
async def _filled_slots(self) -> frozenset[str]
```

**Purpose**: Finds which credential slots currently have stored values in the active workspace. It returns only slot names, not the secret values.

**Data flow**: It opens a workspace database transaction and selects credential slot names for the current workspace. It turns the returned rows into a frozen set of slot names. The output is a safe yes-or-no map source: if a name is in the set, that slot is filled.

**Call relations**: This helper is called by _rows when building list summaries. It uses workspace_tx and ws_current so the fill state comes only from the active workspace, then gives _rows the information needed to label each declared slot as filled or empty.

*Call graph*: called by 1 (_rows); 3 external calls (select, workspace_tx, ws_current).


### `core/src/ufo/kinds/members.py`

`domain_logic` · `request handling`

A workspace needs a reliable roster: who is allowed in, who can administer it, and whose access has been paused. This file is that roster’s rulebook. It exposes members as objects that can be listed, inspected, and updated, but not deleted. Updating means changing two important flags: admin status and “seated” status. A seat is the on/off switch for access; an unseated member is still known to the workspace, but their messages are refused when they try to enter.

The file is careful about privacy and authority. In an internal conversation with the main agent, the roster can be visible broadly. In a child agent or a channel shared with another organization, the view narrows, often to only the speaking member’s own row. Think of it like a company directory that is open inside the office, but not shown in a meeting with outsiders.

Changing roles or seats is locked down. Only a signed-in admin using the main agent can do it, and the code prevents removing the last admin or the last seated admin. The add_member tool follows the same authority rule, and can optionally email the newly added person a sign-in link.

#### Function details

##### `MemberObjects.list`  (lines 68–72)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows the members that the current speaker is allowed to see. It is used when the member object collection is listed inside a tool or agent turn.

**Data flow**: It receives a tool context and a list query. It asks `_visible_rows` for the database rows the speaker may see, turns each row into a simple display row with `_row`, then passes those rows through `object_page` so paging and query options are applied. The result is an `ObjectPage` ready to show to the caller.

**Call relations**: This is the public list entry point for `MemberObjects`. It relies on `_visible_rows` to enforce the visibility rules first, then uses `_row` to make each database row readable before handing the collection to the shared object paging helper.

*Call graph*: calls 2 internal fn (_visible_rows, _row); 1 external calls (object_page).


##### `MemberObjects.member_page`  (lines 74–91)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows a page of members for portal-style reads, outside a normal agent turn. It uses the member named in the request as the reader and applies the portal’s stricter main-agent visibility rule.

**Data flow**: It receives the extension context, the signed-in member id, their admin flag, and a list query. It gets the rows visible to that member through `_member_rows`, converts them with `_row`, and packages them with `object_page`. The output is a paged list of member summaries.

**Call relations**: This mirrors `list`, but for portal reads instead of live tool turns. It calls `_member_rows`, which decides whether the current agent is the main one, then formats rows with `_row` and delegates paging to `object_page`.

*Call graph*: calls 2 internal fn (_member_rows, _row); 1 external calls (object_page).


##### `MemberObjects.get`  (lines 93–95)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[MemberSpec] | None
```

**Purpose**: Fetches one visible member object by name during a tool or agent turn. If the speaker is not allowed to see that member, it behaves as if the object is absent.

**Data flow**: It receives the tool context and the requested member name. It asks `_visible_row` to find a matching row within the speaker’s allowed view. If no row is found it returns `None`; otherwise it turns the database row into detailed member data with `_detail`.

**Call relations**: This is the single-object companion to `list`. It depends on `_visible_row`, which in turn uses the same visibility path as listing, so a caller cannot use detail lookup to bypass roster privacy.

*Call graph*: calls 2 internal fn (_visible_row, _detail).


##### `MemberObjects.member_detail`  (lines 97–113)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[MemberSpec] | None
```

**Purpose**: Fetches one member’s detail for a portal read. It follows the portal rule that non-main agents only reveal the signed-in member’s own row, even for admins.

**Data flow**: It receives the requested object name plus the signed-in member id and admin flag. It asks `_member_rows` for the rows visible in this portal context, searches for the row whose id matches the name, and returns `None` if there is no match. If found, it builds both the summary row and the detailed spec and wraps them in a `MemberObject`.

**Call relations**: This is the portal version of `get`. It calls `_member_rows` for the allowed set, then uses `_row` and `_detail` so the portal detail view is consistent with the list view.

*Call graph*: calls 3 internal fn (_member_rows, _detail, _row); 1 external calls (__init__).


##### `MemberObjects.status`  (lines 115–130)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns a small status snapshot for one visible member. It reports only the email and whether the member currently has a seat.

**Data flow**: It receives the tool context, a member name, and an expected generation value that this implementation does not use. It looks up the member through `_visible_row`. If the row is not visible it returns `None`; otherwise it returns a small dictionary containing the email address and seated flag.

**Call relations**: This is a lightweight read path that shares the same privacy check as `get`. It calls `_visible_row` so status cannot reveal a hidden roster entry.

*Call graph*: calls 1 internal fn (_visible_row).


##### `MemberObjects.apply`  (lines 132–211)

```
async def apply(self, ctx: ToolContext, name: str, spec: MemberSpec, old: MemberSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Changes an existing member’s admin role or seat. It is the protected path for granting or removing workspace administration and for turning access on or off.

**Data flow**: It receives the tool context, the member object name, the desired `MemberSpec`, the previous spec, and an expected generation value. It first confirms the request comes from a signed-in member using the main agent, refuses creation through this path, and parses the object name as a member id. Inside a workspace database transaction, it locks the workspace row, checks that the speaker is an admin, loads the target member, grants or revokes their seat if needed, and updates their admin flag if allowed. It changes the database and returns no value.

**Call relations**: This is the write path behind applying changes to a member object. It calls the tool context to check the main-agent rule, uses database helpers and SQL queries for locking and updates, relies on `member_is_admin` for authority, and uses `Seats` when access needs to be granted or revoked.

*Call graph*: calls 1 internal fn (agent_is_main); 10 external calls (__init__, __init__, __init__, __init__, select, update, workspace_tx, member_is_admin, ws_current, UUID).


##### `MemberObjects.delete`  (lines 213–220)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses deletion of member objects. The system keeps members as records and uses seating to remove access instead of deleting them here.

**Data flow**: It receives the tool context, member name, and expected generation value. It does not inspect or change the database. It immediately raises `VerbNotSupported` with the explanation that workspace members cannot be deleted through objects.

**Call relations**: This is the delete entry point required by the object interface, but this member kind deliberately blocks it. Callers are expected to use `apply` to unseat a member rather than delete them.

*Call graph*: 1 external calls (__init__).


##### `MemberObjects._visible_rows`  (lines 222–228)

```
async def _visible_rows(self, ctx: ToolContext) -> tuple[sa.Row, ...]
```

**Purpose**: Decides which member rows the current speaker is allowed to see during a live tool or agent turn. This is the main privacy gate for listing and lookup.

**Data flow**: It receives the tool context. If there is no signed-in speaker, it returns an empty tuple. If the conversation audience is foreign, meaning a channel shared with another organization, it returns only the speaker’s own roster row. Otherwise it checks whether the agent is the main agent or whether the speaker is an admin, and asks `_roster` for either the whole workspace roster or only the speaker’s row.

**Call relations**: `list` calls this to build visible pages, and `_visible_row` calls it before searching for one member. It hands off to `_roster` after it has decided whether the view should be whole-workspace or self-only.

*Call graph*: calls 3 internal fn (_roster, agent_is_main, speaker_is_admin); called by 2 (_visible_row, list).


##### `MemberObjects._visible_row`  (lines 230–234)

```
async def _visible_row(self, ctx: ToolContext, name: str) -> sa.Row | None
```

**Purpose**: Finds one member row within the set the speaker is allowed to see. It prevents direct lookup from revealing hidden members.

**Data flow**: It receives the tool context and a member name. It calls `_visible_rows` to get the allowed rows, compares each row’s id to the requested name, and returns the matching row if present. If no visible row matches, it returns `None`.

**Call relations**: `get` and `status` use this helper before returning any single-member information. Because it is built on `_visible_rows`, it inherits the same rules used by listing.

*Call graph*: calls 1 internal fn (_visible_rows); called by 2 (get, status).


##### `MemberObjects._member_rows`  (lines 236–248)

```
async def _member_rows(self, member_id: UUID) -> tuple[sa.Row, ...]
```

**Purpose**: Computes the member rows visible to a signed-in member during a portal read. It bases portal visibility on whether the current agent is the main agent.

**Data flow**: It receives a member id. It opens a workspace transaction, checks the current agent record to see whether that agent is marked as the main agent, then asks `_roster` for either the full roster or just that member’s row. It returns the resulting tuple of database rows.

**Call relations**: `member_page` and `member_detail` call this for portal reads. It uses the current workspace and agent from scoped context, then delegates the actual roster query to `_roster`.

*Call graph*: calls 1 internal fn (_roster); called by 2 (member_detail, member_page); 4 external calls (select, agent_current, workspace_tx, ws_current).


##### `MemberObjects._roster`  (lines 250–270)

```
async def _roster(self, member_id: UUID, *, whole: bool) -> tuple[sa.Row, ...]
```

**Purpose**: Reads the workspace member roster from the database, either as the whole roster or as one member’s own row. It is the shared database query behind both turn-time and portal-time member views.

**Data flow**: It receives a member id and a `whole` flag. It builds a database query for member email, id, admin flag, seat timestamp, creation time, and update time in the current workspace, ordered by email. If `whole` is false, it adds a filter for the given member id. It runs the query in a workspace transaction and returns all matching rows.

**Call relations**: `_visible_rows` calls this after deciding live-turn visibility, and `_member_rows` calls it after deciding portal visibility. This keeps the actual roster data shape consistent across list, detail, and portal views.

*Call graph*: called by 2 (_member_rows, _visible_rows); 3 external calls (select, workspace_tx, ws_current).


##### `_row`  (lines 273–286)

```
def _row(row: sa.Row) -> ObjectRow
```

**Purpose**: Turns a raw member database row into a short object-list row that is easy to display. It creates the summary shown in member lists.

**Data flow**: It receives a database row containing member fields. It builds an `ObjectRow` whose name is the member id, whose summary says the email, role, and seated state, and whose fields expose email, admin, and seated values. The output is a display-friendly object row.

**Call relations**: `list`, `member_page`, and `member_detail` use this whenever they need the compact member representation. It is the formatting bridge between database rows and the object system’s list format.

*Call graph*: called by 3 (list, member_detail, member_page); 1 external calls (__init__).


##### `_detail`  (lines 289–294)

```
def _detail(row: sa.Row) -> ObjectDetail[MemberSpec]
```

**Purpose**: Turns a raw member database row into the detailed object data used for inspection. It captures the editable member settings and timestamps.

**Data flow**: It receives a database row. It creates a `MemberSpec` from the row’s admin flag and whether a seat timestamp exists, then wraps that spec with the row’s creation and update times in an `ObjectDetail`. The result is the detail payload for a member object.

**Call relations**: `get` and `member_detail` call this when a visible row needs to become a full detail response. It pairs with `_row`: `_row` is the list card, `_detail` is the full record.

*Call graph*: called by 2 (get, member_detail); 2 external calls (__init__, __init__).


##### `AddMember.add`  (lines 331–363)

```
async def add(self, ctx: ToolContext, args: AddMemberInput) -> ToolResult
```

**Purpose**: Adds a new person to the workspace by email before they have contacted the agent. It can make them an admin and can send, or skip, an invitation email.

**Data flow**: It receives the tool context and `AddMemberInput`. It checks that the speaker is signed in, using the main agent, and not speaking in an externally shared channel. It normalizes and validates the email, opens a workspace transaction, locks the workspace row, confirms the speaker is an admin, checks through `_absent` that the email is not already a member, and calls `create_member` to insert the member. It returns a `ToolResult` with a human-readable confirmation message.

**Call relations**: This is the handler wired into the `add_member` tool definition. It uses context checks for the room and agent rules, database transaction helpers for safe writes, `_absent` to avoid duplicates, and `create_member` to do the actual member creation.

*Call graph*: calls 2 internal fn (_absent, agent_is_main); 9 external calls (__init__, __init__, __init__, select, workspace_tx, create_member, email_domain, member_is_admin, ws_current).


##### `AddMember._absent`  (lines 365–378)

```
async def _absent(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Checks that an email address is not already a member of the current workspace. It gives a clear error telling the admin to edit the existing member instead of adding a duplicate.

**Data flow**: It receives an async database connection and a normalized email address. It queries the current workspace for an existing member whose email matches case-insensitively. If none is found, it returns normally. If a member already exists, it raises a `ValueError` naming the existing member id.

**Call relations**: `AddMember.add` calls this just before creating a member. It acts like a duplicate-address checkpoint so the add flow does not accidentally create two roster entries for the same person.

*Call graph*: called by 1 (add); 3 external calls (execute, select, ws_current).


### `core/src/ufo/kinds/surface_kind.py`

`domain_logic` · `startup and request handling`

This file gives the rest of the system a safe, consistent way to see which chat surfaces exist and whether this workspace has connected any of them. The important idea is that surfaces are not created by users through the normal object system. They are declared by extensions in their manifests, like items printed on a menu, and a separate connect flow binds one of those menu items to a real installation in a workspace.

At startup, registered_surfaces reads all active extension manifests, checks that every surface name is valid, and refuses duplicate names. That prevents two extensions from quietly claiming the same address.

SurfaceObjects is the read-only object-kind implementation. It can list surfaces, show one surface in detail, and report status such as whether it is bound to an agent, whether that agent is archived, and whether the binding routes incoming traffic. It combines two sources of truth: extension declarations from manifests, and database rows in surface_installation that say what this workspace has connected.

Mutation is deliberately blocked. Applying or deleting a surface through this object interface raises a clear “not supported” error. Without that rule, someone could try to edit a surface record that is really owned by an extension manifest or its connect flow, creating confusing state that would not match reality.

#### Function details

##### `registered_surfaces`  (lines 54–79)

```
def registered_surfaces(manifests: tuple[Manifest, ...]) -> dict[str, RegisteredSurface]
```

**Purpose**: Builds the complete set of surface declarations from the active extension manifests. It also protects the system from bad or conflicting surface names before normal operation begins.

**Data flow**: It receives a tuple of manifests. For each surface declared by each manifest, it checks that the surface name follows the object-name rules, then records which extension declared it and whether it is addressed, durable, or home. It returns a dictionary keyed by surface name. If a name is invalid or two extensions use the same name, it raises an error instead of returning a partial or ambiguous result.

**Call relations**: This is the setup step that turns extension manifest data into the map later stored inside SurfaceObjects. It calls the shared name validator so surface objects use the same naming rules as other objects, and it creates RegisteredSurface records that the read methods later render.

*Call graph*: 3 external calls (__init__, __init__, validate_object_name).


##### `SurfaceObjects.list`  (lines 114–117)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Returns a page of all registered surfaces for an internal tool request, including whether each one is bound in the current workspace. It hides surfaces from foreign shared audiences.

**Data flow**: It reads the tool context and the paging or filtering query. If the request comes from a foreign audience, it returns an empty page. Otherwise it reads workspace installation rows, turns the registered surfaces into display rows, and wraps them into an object page.

**Call relations**: This is the main list path for tool-facing reads. It asks _installations for the current database state, asks _rows to combine that state with manifest declarations, and then hands the result to object_page so callers receive the standard paged object response.

*Call graph*: calls 2 internal fn (_installations, _rows); 1 external calls (object_page).


##### `SurfaceObjects.member_page`  (lines 119–131)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Returns the surface list as seen by a signed-in workspace member. Only admins can see it, because surface bindings reveal workspace transport setup.

**Data flow**: It receives member information, an admin flag, and a list query. If the member is not an admin, it returns an empty page. If the member is an admin, it reads installation rows, builds display rows, and returns them as a page.

**Call relations**: This is the portal/member version of list. Like SurfaceObjects.list, it relies on _installations and _rows, but it first applies the member-facing permission rule: non-admins see no surface transport state.

*Call graph*: calls 2 internal fn (_installations, _rows); 1 external calls (object_page).


##### `SurfaceObjects.get`  (lines 133–138)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SurfaceObjectSpec] | None
```

**Purpose**: Returns the detailed declaration for one surface to an internal tool request. It only answers for known registered surfaces and only for internal audiences.

**Data flow**: It receives a context and a surface name. If the audience is foreign, or the name is not registered, it returns nothing. Otherwise it reads the current workspace installations and builds an ObjectDetail containing the manifest-declared surface properties plus installation timestamps when a binding exists.

**Call relations**: This is the single-object read path for tools. It uses _installations to get binding facts and _detail to combine those facts with the registered surface declaration.

*Call graph*: calls 2 internal fn (_detail, _installations).


##### `SurfaceObjects.member_detail`  (lines 140–156)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[SurfaceObjectSpec] | None
```

**Purpose**: Returns both the list-style row and the detailed declaration for one surface in the member portal. It only answers for admins and known registered surfaces.

**Data flow**: It receives the surface name, member identity, and admin flag. If the member is not an admin or the surface name is unknown, it returns nothing. Otherwise it reads installation rows once, builds a row summary and a detailed view from the same data, and packages both into a MemberObject.

**Call relations**: This is the member-facing single-surface read path. It combines _row and _detail so the portal can show both the compact listing information and the full object detail without doing two separate reads.

*Call graph*: calls 3 internal fn (_detail, _installations, _row); 1 external calls (__init__).


##### `SurfaceObjects.status`  (lines 158–177)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports the live binding status for one surface. This is the quick answer to “is this surface connected here, and if so to which agent?”

**Data flow**: It receives a context, surface name, and an expected generation value that this read-only implementation does not use. It rejects foreign audiences and unknown surface names by returning nothing. For a known surface, it reads installation rows. If there is no binding, it returns bound: false. If there is a binding, it returns bound: true plus the agent name, whether the agent is archived, and whether the installation routes ingress traffic.

**Call relations**: This status endpoint sits beside list and get as a focused read. It only needs _installations, because it returns binding state rather than the full manifest declaration.

*Call graph*: calls 1 internal fn (_installations).


##### `SurfaceObjects.apply`  (lines 179–188)

```
async def apply(self, ctx: ToolContext, name: str, spec: SurfaceObjectSpec, old: SurfaceObjectSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create or update a surface through the object API. Surfaces must come from extension manifests and be connected through their own setup flow.

**Data flow**: It receives the requested name, new spec, previous spec, context, and expected generation. Instead of changing anything, it raises a VerbNotSupported error with an explanation that setup belongs to the surface’s own flow.

**Call relations**: This is the write path guardrail. When the object framework tries to apply a change to a surface object, this method stops the operation immediately and does not call any lower-level storage code.

*Call graph*: 1 external calls (__init__).


##### `SurfaceObjects.delete`  (lines 190–197)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to delete a surface through the object API. A surface disappears only when the extension that registered it is removed or no longer declares it.

**Data flow**: It receives the context, surface name, and expected generation. It makes no database changes and raises a VerbNotSupported error explaining that registration belongs to the extension, not to a mutable object row.

**Call relations**: This is the delete-path counterpart to apply. If the object framework asks this kind to delete a surface, it blocks the request so the manifest remains the single source of truth for registration.

*Call graph*: 1 external calls (__init__).


##### `SurfaceObjects._rows`  (lines 199–203)

```
def _rows(self, installed: Mapping[str, _BoundInstallation]) -> tuple[ObjectRow, ...]
```

**Purpose**: Turns all registered surfaces into compact list rows. It is the bridge between raw declarations and the table-like listing shown to callers.

**Data flow**: It receives a map of installed surface bindings. It walks through the registered surfaces in sorted name order, passes each one to _row together with the installation map, and returns a tuple of ObjectRow values.

**Call relations**: This helper is used by both SurfaceObjects.list and SurfaceObjects.member_page after they have loaded installation data. It delegates the per-surface wording and fields to _row.

*Call graph*: calls 1 internal fn (_row); called by 2 (list, member_page).


##### `SurfaceObjects._row`  (lines 205–228)

```
def _row(self, name: str, registered: RegisteredSurface, installed: Mapping[str, _BoundInstallation]) -> ObjectRow
```

**Purpose**: Builds one compact row for a surface list. The row tells readers what extension declared the surface and whether this workspace has bound it to an agent.

**Data flow**: It receives the surface name, its registered declaration, and the installation map. It checks whether that surface has a binding. From that it creates a summary such as “not bound” or “bound to agent,” adding an archived note when needed. It returns an ObjectRow with searchable fields like extension, addressed, durable, home, and bound.

**Call relations**: This helper is called by _rows for full listings and by member_detail when a member page needs the row for just one surface. It creates the standard ObjectRow shape expected by the object system.

*Call graph*: called by 2 (_rows, member_detail); 1 external calls (__init__).


##### `SurfaceObjects._detail`  (lines 230–245)

```
def _detail(self, name: str, installed: Mapping[str, _BoundInstallation]) -> ObjectDetail[SurfaceObjectSpec]
```

**Purpose**: Builds the full detail view for one surface. It shows the extension-declared specification and, when present, the timestamps from this workspace’s binding.

**Data flow**: It receives a surface name and the installation map. It looks up the registered declaration, checks whether there is a bound installation, then creates a SurfaceObjectSpec from the declaration. It returns an ObjectDetail with that spec and created/updated times if bound, or no timestamps if unbound.

**Call relations**: This helper is called by SurfaceObjects.get and SurfaceObjects.member_detail. It packages manifest facts into the standard ObjectDetail container used by the rest of the object API.

*Call graph*: called by 2 (get, member_detail); 2 external calls (__init__, __init__).


##### `SurfaceObjects._installations`  (lines 247–278)

```
async def _installations(self) -> dict[str, _BoundInstallation]
```

**Purpose**: Reads the current workspace’s surface bindings from the database. This is how the read-only manifest view learns which registered surfaces are actually connected here.

**Data flow**: It opens a workspace database transaction, selects rows from surface_installation joined to agent, and filters them to the current workspace. For each row, it records the surface name, agent display name, whether the agent is archived, whether it routes ingress, and creation/update timestamps. It returns a dictionary keyed by surface name.

**Call relations**: All read methods that need binding state call this helper: list, member_page, get, member_detail, and status. It is the only place in this file that touches the database, and it returns small _BoundInstallation records so the rest of the file can work with plain in-memory data.

*Call graph*: called by 5 (get, list, member_detail, member_page, status); 4 external calls (__init__, select, workspace_tx, ws_current).


### `core/src/ufo/kinds/workspace_kind.py`

`domain_logic` · `request handling`

This file is like the front desk for a workspace. If someone asks, “What workspace am I in, and who has access?”, this code knows how to answer. It does not let anyone change the workspace, because the workspace itself has no editable settings here. Its visible information is calculated from member records: total members, seated members, and a roster.

The main type, WorkspaceObjects, provides the standard object actions for this kind. Listing returns one row for the current workspace, named by the workspace id. Getting the object returns its empty spec plus timestamps. Asking for status returns the useful live information: member count, seated count, and roster entries showing email, seat state, and admin state.

There are important privacy rules. If the request comes from an externally shared audience, the workspace appears invisible. In an internal conversation, a member talking to the main agent can see the whole roster. A child agent only shows the speaker their own row. This mirrors the member visibility rules.

The file also refuses writes on purpose. Applying changes raises an error explaining that seats are changed on member objects. Deleting raises an error because the workspace is permanent. Without this file, callers would not have a consistent, safe way to inspect the current workspace, and might try to mutate something that is meant to be derived and read-only.

#### Function details

##### `WorkspaceObjects.list`  (lines 73–86)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Returns the workspace as a one-item list, with a short summary of how many members exist and how many are seated. It hides the workspace completely from foreign or externally shared audiences.

**Data flow**: It receives a tool context, which includes who is asking, and a list query, which may ask for paging or ordering. If the audience is foreign, it returns an empty page. Otherwise it reads the current workspace id, asks _shape for the latest counts, builds one row with the workspace id as the name, and wraps that row in an object page.

**Call relations**: This is called when the object system needs to list objects of kind workspace. It relies on _shape to collect the real database-backed facts, uses ws_current to name the current workspace, and hands the finished row to object_page so the response follows the same paging format as other object kinds.

*Call graph*: calls 1 internal fn (_shape); 3 external calls (__init__, object_page, ws_current).


##### `WorkspaceObjects.get`  (lines 88–98)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[WorkspaceSpec] | None
```

**Purpose**: Returns the basic detail record for the current workspace, but only when the caller asks for exactly this workspace by id. The returned spec is empty because there are no user-editable workspace fields in this object kind.

**Data flow**: It receives the tool context and a requested object name. If the audience is foreign, or if the name does not match the current workspace id, it returns nothing. If the name matches, it reads the workspace shape, creates an empty WorkspaceSpec, and returns an ObjectDetail containing that spec plus the workspace creation and update timestamps.

**Call relations**: This is used when a caller asks to read one specific workspace object. It calls _shape for the stored timestamps and current member-derived data, and uses ws_current to check that the requested name is the current workspace id.

*Call graph*: calls 1 internal fn (_shape); 3 external calls (__init__, __init__, ws_current).


##### `WorkspaceObjects.status`  (lines 100–121)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns the live status for the workspace: member count, seated count, and the roster the caller is allowed to see. This is where the file applies the privacy rule about who may see the whole roster.

**Data flow**: It receives the tool context, workspace name, and an optional expected generation value. It first rejects foreign audiences and wrong workspace names by returning nothing. Then it reads the workspace shape. If the speaker is a member and is talking to the main agent, it includes every roster entry. Otherwise it filters the roster down to the speaker’s own member entry. The result is a plain dictionary with counts and visible roster rows.

**Call relations**: This is called when the object system wants the status, meaning the live, derived information rather than an authored spec. It calls _shape to get the full roster and counts, asks ToolContext.agent_is_main whether this is the main agent conversation, and then trims the roster before returning it.

*Call graph*: calls 2 internal fn (_shape, agent_is_main); 1 external calls (ws_current).


##### `WorkspaceObjects.apply`  (lines 123–132)

```
async def apply(self, ctx: ToolContext, name: str, spec: WorkspaceSpec, old: WorkspaceSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create or update the workspace object. This protects the rule that workspace facts here are read-only and that seating is changed on member objects instead.

**Data flow**: It receives the context, workspace name, proposed spec, previous spec, and optional generation check. It does not read or change any stored workspace data. It immediately raises a VerbNotSupported error with a message telling the caller to grant or revoke seats through member objects.

**Call relations**: This is invoked by the object system when something tries to apply changes to a workspace object. Instead of handing off to database writes, it stops the flow with a clear error, preserving the design that this object is only for reading.

*Call graph*: 1 external calls (__init__).


##### `WorkspaceObjects.delete`  (lines 134–141)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to delete the workspace. A workspace is treated as permanent once created, so this operation is not allowed through the object interface.

**Data flow**: It receives the context, workspace name, and optional generation check. It does not inspect or modify the database. It raises a VerbNotSupported error explaining that the workspace is permanent and cannot be deleted.

**Call relations**: This is called when the object system receives a delete request for a workspace object. It ends that request immediately with an error instead of passing anything to storage, making the permanence rule explicit.

*Call graph*: 1 external calls (__init__).


##### `WorkspaceObjects._shape`  (lines 143–161)

```
async def _shape(self) -> WorkspaceShape
```

**Purpose**: Collects all the raw information needed to describe the workspace in one small internal package. It reads the workspace timestamps from the database and combines them with a current snapshot of member seating.

**Data flow**: It starts by reading the current workspace id. Inside a workspace database transaction, it fetches the workspace row’s creation and update times, then asks Seats for a snapshot of that workspace’s members and seated count. It returns a WorkspaceShape containing total members, seated count, roster entries, and timestamps.

**Call relations**: This is the shared helper behind list, get, and status. Those public methods decide what the caller is allowed to see and how to format the answer, while _shape does the common work of gathering the current workspace facts from the database and seat snapshot.

*Call graph*: called by 3 (get, list, status); 5 external calls (__init__, __init__, select, workspace_tx, ws_current).


### Account and gbrain sources
Connector and gbrain object kinds represent third-party accounts, permissions, and markdown source roots under owner and admin rules.

### `extensions/connectors/ufo_ext_connectors/objects.py`

`domain_logic` · `workspace object listing, inspection, and object apply/delete operations`

This file is the bridge between the connector system and the workspace object system. In plain terms, it lets the workspace talk about “Alice’s Google account” and “this agent’s access to Alice’s Google account” as inspectable objects. Without it, users and agents would not have a consistent object-based way to see connected accounts, attach an existing account to an agent, make access shared or private, revoke one agent’s access, or disconnect an account entirely.

There are two related object kinds. A `connection` is the real member-owned account connection, created only through the separate `connect_account` flow because that involves consent with an outside provider. A `connector_grant` is one agent’s access to a connection. Think of the connection as a locked filing cabinet owned by a member, and a grant as one key given to one agent.

The file defines small specification models for both objects, then store classes that list existing rows, return detailed object views, expose status information, and perform allowed changes. It deliberately refuses to create a real connection through normal object editing. For grants, it can attach an already available connection to an agent, flip whether that grant is shared, or revoke the grant. It also records links: grants point to the agent they are for, and private grants point back to the connection they open.

#### Function details

##### `_AccountSummary.provider`  (lines 51–51)

```
def provider(self) -> str
```

**Purpose**: This is part of a small shape definition for account summary objects. It says that any summary used here must provide the name of the outside account provider, such as a service or integration name.

**Data flow**: A summary-like object is expected to already contain provider information → code reads its `provider` value → that provider name can be used to build object names and display account details.

**Call relations**: The helper `_named` depends on this property when it turns account summaries into a name-to-summary lookup. The concrete summaries come from the grant and connection summary functions elsewhere.


##### `_AccountSummary.account_id`  (lines 54–54)

```
def account_id(self) -> str
```

**Purpose**: This is the other required part of the account summary shape. It says that any summary used here must provide the account’s identifier at the provider.

**Data flow**: A summary-like object is expected to already contain an account identifier → code reads its `account_id` value → that identifier is combined with the provider to form a stable object name.

**Call relations**: The helper `_named` reads this property together with `provider`. That lets both connection listings and grant listings use the same naming rule.


##### `_named`  (lines 57–58)

```
def _named(rows: tuple[SummaryT, ...]) -> dict[str, SummaryT]
```

**Purpose**: This helper gives account summaries their workspace object names. It prevents the connection and grant code from each inventing their own naming scheme.

**Data flow**: It receives a tuple of account summary rows → for each row, it combines the row’s provider and account id using the shared account naming helper → it returns a dictionary where each object name points to its original summary row.

**Call relations**: Both `ConnectionObjects._member_rows` and `ConnectorGrantObjects._member_rows` call this before building visible object rows. It hands them a consistent name for each account, produced by `ufo.sdk.grants.account_object_name`.

*Call graph*: called by 2 (_member_rows, _member_rows); 1 external calls (account_object_name).


##### `ConnectionObjects._member_rows`  (lines 69–86)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This builds the list view for member-owned connection objects. It shows each connected account with a human-readable summary and ownership information.

**Data flow**: It asks the grants layer for all connection summaries → names them with `_named` → wraps each one as an `OwnedRow` that includes the display name, summary text, owner member id, and connection generation id → returns the complete tuple of visible rows.

**Call relations**: The workspace object system calls this when it needs to list `connection` objects a member can read. It depends on `connection_summaries` for the real connection data and uses `GeneratedObjectOwner` and `OwnedRow` to hand that data back in the object system’s expected form.

*Call graph*: calls 1 internal fn (_named); 3 external calls (__init__, __init__, connection_summaries).


##### `ConnectionObjects._member_object`  (lines 88–106)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[ConnectionSpec] | None
```

**Purpose**: This returns the detailed object record for one connection. It gives callers the exact provider/account pair and timestamps for the selected connected account.

**Data flow**: It receives an object name and owner record → looks up current connection summaries and finds the row whose generation id matches the owner → if found, it builds a `ConnectionSpec` and an `ObjectDetail`; if not found, it returns nothing.

**Call relations**: The workspace object system calls this when someone opens or reads a specific `connection` object. It uses `connection_summaries` as the source of truth and returns details in the standard object format.

*Call graph*: 3 external calls (__init__, __init__, connection_summaries).


##### `ConnectionObjects._status`  (lines 108–123)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: This produces extra live status information for a connection, such as who owns it, whether it is shared, where it is hosted, and which agents use it.

**Data flow**: It receives the tool context, object name, and owner record → finds the matching connection summary by generation id → converts selected fields into a simple JSON-friendly dictionary → returns that dictionary, or returns nothing if the row no longer exists.

**Call relations**: The object/tool layer calls this when it needs current status beyond the basic object spec. It reads from `connection_summaries` and does not change anything.

*Call graph*: 1 external calls (connection_summaries).


##### `ConnectionObjects._apply_owned`  (lines 125–133)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ConnectionSpec, old: ConnectionSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: This intentionally refuses to create or edit real account connections through object apply. Connecting an account must go through `connect_account`, because it involves a third-party consent flow.

**Data flow**: It receives the requested connection spec and any existing object state → ignores the proposed edit as unsupported → raises a clear `VerbNotSupported` error explaining that `connect_account` must be used instead.

**Call relations**: The object system calls this if someone tries to apply changes to a `connection` object. Instead of handing off to the grants layer, it stops the flow immediately with the connector-specific refusal message.

*Call graph*: 1 external calls (__init__).


##### `ConnectionObjects._delete_owned`  (lines 135–145)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: This disconnects a connected account. Deleting a `connection` is a strong action because it removes that account from every agent that was using it.

**Data flow**: It receives the tool context, object name, and owner record → checks that connector grants support is available and that there is a speaking member to act as the person making the request → asks the grants layer to disconnect the connection by generation id → finishes silently if successful, or raises an error if the connection changed during the operation.

**Call relations**: The object system calls this when an allowed owner or admin deletes a `connection`. It hands the real work to `ctx.grants.disconnect`, including the acting member id for permission and audit purposes.


##### `ConnectorGrantObjects._admin_can_apply`  (lines 156–157)

```
def _admin_can_apply(self, old: ConnectorGrantSpec, spec: ConnectorGrantSpec) -> bool
```

**Purpose**: This checks the one grant edit that an admin is allowed to make without being the owner: turning a shared grant back to private. It exists so admins can reduce exposure, but not broaden access.

**Data flow**: It receives the old grant spec and the proposed new spec → compares them while changing only the `shared` flag to false → returns true only when the old grant was shared and the requested change is exactly making it private.

**Call relations**: This is used by the member-readable object permission machinery when deciding whether an admin may apply an update. It relies on `ConnectorGrantSpec.model_copy` to compare the proposed change cleanly.

*Call graph*: 1 external calls (model_copy).


##### `ConnectorGrantObjects._member_rows`  (lines 159–176)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This builds the list view for connector grant objects. Each row represents one agent’s access to a connected account.

**Data flow**: It asks the grants layer for grant summaries → names them with `_named` → turns each grant into an `OwnedRow` with summary text, owner member id, whether it is shared, and the grant generation id → returns all rows as a tuple.

**Call relations**: The workspace object system calls this when it needs to list `connector_grant` objects a member can read. It mirrors the connection listing flow, but reads from `grant_summaries` because grants are per-agent access records.

*Call graph*: calls 1 internal fn (_named); 3 external calls (__init__, __init__, grant_summaries).


##### `ConnectorGrantObjects._member_object`  (lines 178–219)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[ConnectorGrantSpec] | None
```

**Purpose**: This returns the detailed object record for one connector grant. It shows which provider account the grant opens, whether it is shared, and which agent it belongs to.

**Data flow**: It receives an object name and owner record → looks up the matching grant summary by generation id → builds a `ConnectorGrantSpec` from the row → adds timestamps and object links, including a link to the agent and, for private grants, a link back to the underlying connection → returns the completed detail, or nothing if the row is gone.

**Call relations**: The object system calls this when someone reads a specific `connector_grant`. It uses `grant_summaries` for current data, `ObjectRef` and `ObjectLink` to describe relationships, and `account_object_name` to point private grants back to their connection object.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, account_object_name, grant_summaries).


##### `ConnectorGrantObjects._status`  (lines 221–236)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: This produces extra live status information for a connector grant. It is a quick view of who owns the connection, which agent has access, where it is hosted, and whether the grant is shared.

**Data flow**: It receives the tool context, object name, and owner record → finds the matching grant summary by generation id → returns selected fields as a JSON-friendly dictionary → returns nothing if the grant no longer exists.

**Call relations**: The object/tool layer calls this when it needs current status for a grant. It reads from `grant_summaries` and does not make changes.

*Call graph*: 1 external calls (grant_summaries).


##### `ConnectorGrantObjects._apply_owned`  (lines 238–286)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ConnectorGrantSpec, old: ConnectorGrantSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: This is the main edit path for connector grants. It can attach an existing connection to an agent, or change a grant between shared and private, while refusing attempts to secretly create or switch the underlying account.

**Data flow**: It receives the requested grant spec plus any old spec and owner record → if this is a brand-new grant, it checks for connector access and a speaking member, then asks the grants layer to attach the provider account to the current conversation’s agent → if editing an existing grant, it reloads the current row, verifies the provider and account id have not been changed, then updates only the `shared` flag when needed → it returns nothing on success and raises clear errors when the request is unavailable, unsupported, or races with a changed grant.

**Call relations**: The object system calls this when applying a `connector_grant` create or update. It reads `grant_summaries` to confirm the current state, uses `ConnectorGrantSpec.model_copy` to ensure only sharing changed, and hands real state changes to `ctx.grants.attach` or `ctx.grants.set_shared`.

*Call graph*: 4 external calls (__init__, __init__, model_copy, grant_summaries).


##### `ConnectorGrantObjects._delete_owned`  (lines 288–298)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: This revokes one agent’s access to a connected account. It removes the grant without disconnecting the account itself or affecting other agents’ grants.

**Data flow**: It receives the tool context, object name, and owner record → checks that the grants service is available and that a speaking member is present → asks the grants layer to revoke the grant by generation id → finishes silently if successful, or raises an error if the grant changed during revocation.

**Call relations**: The object system calls this when an allowed owner or admin deletes a `connector_grant`. It delegates the actual removal to `ctx.grants.revoke`, passing the acting member id so the grants layer can enforce and record the action.


### `extensions/gbrain/ufo_ext_gbrain/objects.py`

`domain_logic` · `request handling and startup-visible source listing`

This file is the rulebook for `gbrain_source` objects. Think of a gbrain source like a subscription to a pile of markdown pages: the system needs to know where the pages come from, who is allowed to see them, and when to refresh them. Without this file, users could not safely register repositories as knowledge sources, list the sources they can see, trigger a fresh sync, or remove a source and its pages.

The file gives every source a stable name based on its real origin: repository, branch, or local folder path. That means the same GitHub repo always maps to the same object name, and changing the repo or branch creates a different source rather than silently editing the old one.

It also enforces ownership rules. A source is private to the member who registered it unless it is explicitly shared with the workspace. Only the original registering member can turn a private source into a shared one. Once shared, it cannot be made private again except by deleting and recreating it. Local folder sources are even stricter: they come only from operator configuration at startup, because they read the server’s filesystem.

The main class, `GbrainObjects`, connects the project’s generic object verbs — apply, list, get, status, delete — to the gbrain source registry held by the extension context.

#### Function details

##### `gbrain_source_name`  (lines 57–64)

```
def gbrain_source_name(repo: str | None, branch: str | None, root: str | None) -> str
```

**Purpose**: Builds the official object name for a gbrain source from its origin. This prevents users from inventing arbitrary names for the same repository or folder.

**Data flow**: It receives a repository name, branch name, and folder root, any of which may be absent depending on the source type. It turns those values into a stable JSON string, hashes that string, keeps the first few hexadecimal characters, and returns a name like `gbrain-1a2b3c4d`.

**Call relations**: This is the naming rule used whenever the code needs to compare a requested object name with the source’s true identity. `_origin` uses it while preparing a new source, and `_Registered.name` uses it when showing or finding sources that already exist.

*Call graph*: called by 2 (name, _origin); 2 external calls (sha256, dumps).


##### `GbrainSpec.validate_origin`  (lines 97–102)

```
def validate_origin(self) -> 'GbrainSpec'
```

**Purpose**: Checks that a requested source points to exactly one kind of origin: either a GitHub repository or a local folder, not both and not neither. It also makes sure a branch is only used with a repository.

**Data flow**: It reads the fields of a `GbrainSpec` after they have been filled in. If the origin fields are inconsistent, it raises a clear validation error; if they make sense, it returns the same spec unchanged.

**Call relations**: This validator runs as part of creating or checking a `GbrainSpec`. It protects later code, such as `_origin` and `GbrainObjects._apply_owned`, from having to guess what kind of source the user meant.


##### `_origin`  (lines 112–126)

```
def _origin(spec: GbrainSpec) -> _Origin
```

**Purpose**: Turns a validated user-facing source spec into the internal backend choice and backend-specific configuration. In plain terms, it decides whether this is a Git source or a folder source and packages it for the sync system.

**Data flow**: It receives a `GbrainSpec`. If the spec names a folder root, it creates a folder backend config and a derived source name. If the spec names a repository, it creates a Git backend config and a derived source name. It returns an `_Origin` object containing the backend name, config, and official object name.

**Call relations**: The apply path calls this when checking whether a requested name matches the source’s real identity and when registering a new source. `GbrainObjects.apply` also uses it to detect whether another member has already privately registered the same origin.

*Call graph*: calls 1 internal fn (gbrain_source_name); called by 2 (_apply_owned, apply); 3 external calls (__init__, __init__, __init__).


##### `_identity`  (lines 129–130)

```
def _identity(spec: GbrainSpec) -> tuple[str | None, str | None, str | None, bool]
```

**Purpose**: Extracts the parts of a spec that define what source it is and whether it is shared. This gives the code a simple way to tell whether a new apply request is actually changing anything.

**Data flow**: It receives a `GbrainSpec` and returns a tuple containing repository, branch, root, and shared flag. It does not change anything.

**Call relations**: `GbrainObjects.apply` uses this to spot a no-op reapply of the same source. `GbrainObjects._resync` uses it to enforce that a resync request is only a refresh request, not a hidden edit to the source.

*Call graph*: called by 2 (_resync, apply).


##### `_Registered.name`  (lines 147–148)

```
def name(self) -> str
```

**Purpose**: Returns the official object name for a source that is already registered in the system.

**Data flow**: It reads the registered source’s repository, branch, and folder root fields. It passes them through the same naming rule used for new sources and returns the resulting `gbrain-...` name.

**Call relations**: This property keeps stored sources and newly requested sources using the exact same naming rule. It is used indirectly when registered sources are listed, looked up, or compared by name.

*Call graph*: calls 1 internal fn (gbrain_source_name).


##### `_Registered.spec`  (lines 150–156)

```
def spec(self) -> GbrainSpec
```

**Purpose**: Converts a stored source row back into the public spec shape that users and tools understand.

**Data flow**: It reads the stored repository, branch, root, and subject. The subject is checked to decide whether the source is shared. It returns a `GbrainSpec` with `resync` left at its normal false value because resync is an action, not saved state.

**Call relations**: This is used when `GbrainObjects._member_object` builds the detail view for a source. It translates internal storage fields into the object format returned by the object system.

*Call graph*: 2 external calls (__init__, subject_shared).


##### `_Registered.summary`  (lines 158–162)

```
def summary(self) -> str
```

**Purpose**: Creates a short human-readable label for a registered source, such as a server directory path or GitHub repository name.

**Data flow**: It reads either the folder root or the repository and branch. It formats that into a short sentence and trims it to the configured maximum length.

**Call relations**: This summary appears in object listings and in some error messages. It helps users recognize a source without seeing all of its stored details.


##### `_require_ext`  (lines 165–168)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Makes sure the gbrain object code has an extension context, which is the connection to the source registry and sync operations. Without that context, the object cannot read or change sources.

**Data flow**: It receives an optional extension context. If one is present, it returns it; if not, it raises a runtime error explaining that gbrain sources were dispatched without the needed context.

**Call relations**: Most operations that touch real source records call this before proceeding. It is a guardrail used by lookup, listing, registering, resyncing, granting, and deleting paths.

*Call graph*: called by 6 (_apply_owned, _delete_owned, _grant_settled, _member_rows, _resync, _registered_named).


##### `_registered_from_ext`  (lines 171–196)

```
async def _registered_from_ext(ext: ExtensionContext) -> tuple[_Registered, ...]
```

**Purpose**: Reads all known gbrain source records from the extension context and converts them into a small internal form used by this file.

**Data flow**: It asks the extension context for source records. For each record, it keeps only the gbrain Git and folder backends, validates the stored backend configuration, extracts repository or root information, and returns a tuple of `_Registered` entries.

**Call relations**: This is the main bridge from the extension’s source registry into the object layer. `_member_rows` uses it to list sources, while `_registered_named` uses it as the starting point for finding one source by name.

*Call graph*: calls 1 internal fn (sources); called by 2 (_member_rows, _registered_named); 3 external calls (__init__, model_validate, model_validate).


##### `_registered_named`  (lines 199–203)

```
async def _registered_named(ext: ExtensionContext | None, name: str) -> _Registered | None
```

**Purpose**: Finds one registered gbrain source by its official object name.

**Data flow**: It receives an optional extension context and a name. It first requires a real context, loads all registered gbrain sources, compares each source’s derived name to the requested name, and returns the matching `_Registered` entry or `None`.

**Call relations**: This lookup helper is used throughout the object operations: apply, get detail, status, no-op grant, resync, registration updates, and delete all rely on it to connect an object name to the stored source row.

*Call graph*: calls 2 internal fn (_registered_from_ext, _require_ext); called by 7 (_apply_owned, _delete_owned, _grant_settled, _member_object, _resync, _status, apply).


##### `GbrainObjects.apply`  (lines 221–252)

```
async def apply(self, ctx: ToolContext, name: str, spec: GbrainSpec, old: GbrainSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Implements the high-level apply behavior for gbrain sources. It decides whether the request is a resync, a harmless repeat of an existing source, a new registration, or a change that needs ownership checks.

**Data flow**: It receives the tool context, requested object name, desired spec, any visible old spec, and an expected generation marker. If `resync` is set, it sends the request to `_resync`. If the spec is identical to an already visible source, it grants the current agent access without re-registering. If the source appears to be privately registered by someone else, it refuses. Otherwise it lets the base object flow continue to the owned apply logic.

**Call relations**: This is the front door for apply requests on `gbrain_source` objects. It coordinates helper functions like `_identity`, `_origin`, `_registered_named`, `_grant_settled`, and `_resync` before the lower-level registration work happens.

*Call graph*: calls 6 internal fn (speaker_is_admin, _grant_settled, _resync, _identity, _origin, _registered_named); 2 external calls (__init__, subject_shared).


##### `GbrainObjects._grant_settled`  (lines 254–269)

```
async def _grant_settled(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: Gives the current agent access to a source when the user reapplies an identical spec that is already settled. This avoids a confusing case where nothing changes but the agent still lacks the feed.

**Data flow**: It reads the speaker member, the object owner, and the registered source row. If there is no live speaker, no owner, or the speaker is not allowed to use the source, it does nothing. Otherwise it grants the source to the current agent through the extension context.

**Call relations**: `GbrainObjects.apply` calls this for no-op reapplies. It hands off to the extension context’s source grant operation once it has confirmed the source is shared or owned by the speaking member.

*Call graph*: calls 2 internal fn (_registered_named, _require_ext); called by 1 (apply).


##### `GbrainObjects._resync`  (lines 271–291)

```
async def _resync(self, ctx: ToolContext, name: str, spec: GbrainSpec, old: GbrainSpec | None) -> None
```

**Purpose**: Schedules an immediate refresh of an existing source without changing its repository, branch, folder, or sharing state.

**Data flow**: It receives the context, name, requested spec, and old visible spec. It first checks that the request is only a resync and not an edit. It then confirms the source exists, is visible to the acting member or admin, and that the actor is either the owner or an admin. If all checks pass, it asks the extension context to schedule a sync for that source.

**Call relations**: This is called only from `GbrainObjects.apply` when `resync` is true. It uses `_identity` to reject mixed edit-and-resync requests, `_registered_named` to find the stored source, and the extension context to queue the actual sync.

*Call graph*: calls 4 internal fn (speaker_is_admin, _identity, _registered_named, _require_ext); called by 1 (apply); 3 external calls (__init__, __init__, __init__).


##### `GbrainObjects._member_rows`  (lines 293–306)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[ObjectOwner], ...]
```

**Purpose**: Builds the list entries for gbrain sources that the object system can later filter and show to a member.

**Data flow**: It receives the extension context and an optional member id. It loads all registered gbrain sources, turns each into an `OwnedRow` with a name, short summary, and owner information, and returns those rows.

**Call relations**: The generic member-readable object machinery calls this when listing objects. It relies on `_registered_from_ext` for the raw registered sources and packages them into the common object-listing shape.

*Call graph*: calls 2 internal fn (_registered_from_ext, _require_ext); 3 external calls (__init__, __init__, subject_shared).


##### `GbrainObjects._member_object`  (lines 308–323)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: ObjectOwner, *, member_id: UUID | None) -> ObjectDetail[GbrainSpec] | None
```

**Purpose**: Builds the detailed object view for one named gbrain source.

**Data flow**: It receives the extension context, object name, owner information, and optional member id. It looks up the registered source by name. If found, it returns the public spec plus created and updated timestamps; if not found, it returns `None`.

**Call relations**: The object system calls this after it has decided a member may inspect a specific object. It uses `_registered_named` to find the source and `_Registered.spec` to present it in user-facing form.

*Call graph*: calls 1 internal fn (_registered_named); 1 external calls (__init__).


##### `GbrainObjects._status`  (lines 325–339)

```
async def _status(self, ctx: ToolContext, name: str, _owner: ObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns live operational status for a gbrain source, such as when it will sync next and how many errors have happened in a row.

**Data flow**: It receives the tool context, name, and owner. It looks up the registered source. If the source exists, it returns a dictionary with sharing state, next sync time, and consecutive error count. For private sources with an owner, it also includes the owner member id.

**Call relations**: The object status flow calls this when a user asks for more than the saved spec. It uses `_registered_named` to read current source metadata and converts it into simple JSON-friendly values.

*Call graph*: calls 1 internal fn (_registered_named); 1 external calls (subject_shared).


##### `GbrainObjects._apply_owned`  (lines 341–379)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: GbrainSpec, old: GbrainSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: Performs the actual create-or-update work once the higher-level object system has accepted that the actor is allowed to mutate the source. It registers a new GitHub source, shares an existing private one, or grants the current agent access.

**Data flow**: It receives the context, requested name, desired spec, old spec, and owner. It requires a speaking member, refuses local folder roots because those must come from server config, checks that the requested name matches the origin-derived name, and looks for an existing registration. If none exists, it registers the source with a private or shared subject. If it exists, it refuses unsharing, optionally changes a private source to shared, and grants the source to the current agent.

**Call relations**: This is reached from the generic apply flow after `GbrainObjects.apply` has done special gbrain checks. It uses `_origin` for backend setup, `_registered_named` for existing rows, and the extension context for registering, sharing, and granting sources.

*Call graph*: calls 3 internal fn (_origin, _registered_named, _require_ext); 3 external calls (__init__, member_subject, subject_shared).


##### `GbrainObjects._delete_owned`  (lines 381–385)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: Deletes a registered gbrain source after the object system has confirmed the actor is allowed to remove it.

**Data flow**: It receives the context, object name, and owner. It looks up the registered source. If none is found, it raises an unknown-object error; otherwise it asks the extension context to remove the source by id.

**Call relations**: The generic delete flow calls this for `gbrain_source` objects. It uses `_registered_named` to connect the object name to the stored source row and then hands deletion to the extension context, where the synced pages can follow the tombstone cleanup path.

*Call graph*: calls 2 internal fn (_registered_named, _require_ext); 1 external calls (__init__).


### Memory and automation records
Memory, monitor, and report objects expose generated or scheduled records for safe listing, inspection, and limited lifecycle control.

### `extensions/memory/ufo_ext_memory/objects.py`

`domain_logic` · `request handling`

This file is the public “object shelf” for the memory extension. One shelf holds individual memories: short stored statements with an id, text, visibility audience, source information, and links back to the page they came from or the newer memory that replaced them. The other shelf holds profiles: one row per workspace member, saying their role and current focus based on shared workspace facts.

The important job here is safety. A memory may be private to a person, shared with the workspace, or tied to a source page. Before this file shows anything, it checks the reader’s allowed subjects, which are like labels saying what rooms of information they may enter. For memories derived from pages, it also checks that the source page is still readable and still matches the revision the memory came from. This prevents stale or newly restricted page content from leaking through old memory records.

Listings show only “live” memories, meaning ones not superseded and not retired. Opening by id can still show a superseded memory, but includes a link to the replacement, like a forwarding address. Profiles are simpler: because they are made only from shared facts, anyone who can read the workspace-shared subject can read all profiles; anyone outside cannot read any. Both object kinds are read-only here. Memory updates and profile generation happen through separate tools or background passes.

#### Function details

##### `_require_ext`  (lines 91–94)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: This small guard makes sure an extension context is present before any memory or profile object tries to read the database. Without that context, the object code would not know which workspace store to use.

**Data flow**: It receives a possible extension context. If the value is missing, it stops with an error; if it is present, it returns the same context unchanged.

**Call relations**: The public memory and profile methods call this at their boundary, before handing work to the lower-level page or detail readers. It is the doorway check that prevents the rest of the file from running without the environment it needs.

*Call graph*: called by 8 (get, list, member_detail, member_page, get, list, member_detail, member_page).


##### `_stamp`  (lines 97–101)

```
def _stamp(written: datetime) -> str
```

**Purpose**: This turns a stored date and time into one consistent text format. It matters because different databases can store timezone information differently, and readers need a stable spelling for sorting and display.

**Data flow**: It takes a datetime value from storage, first normalizes it with the memory store’s timezone helper, then returns an ISO-formatted string. The output is safe to place in object fields shown to callers.

**Call relations**: Row-building helpers call this when they add a written time to memory and profile listing rows. It delegates the timezone cleanup to the shared store helper before producing the final text.

*Call graph*: called by 2 (_profile_row, _row); 1 external calls (_aware).


##### `_row`  (lines 104–128)

```
def _row(name: str, body: str, subject: str, item_class: str, memory_kind: str, written: datetime | None, page_id: UUID | None, pages: Mapping[UUID, PageState]) -> ObjectRow
```

**Purpose**: This builds the lightweight listing row for one memory item. It gives callers a preview of the memory plus key facts such as visibility, kind, written time, and source page information.

**Data flow**: It receives the memory id, body text, audience subject, class, kind, creation time, optional source page id, and any readable page states already fetched. It trims the text for summary and listing fields, adds page title and stream when available, and returns an ObjectRow.

**Call relations**: Memory listing and member detail use this after they have already checked which memories and pages are readable. It calls the timestamp formatter and word-clipping helper so the returned row is compact and display-friendly.

*Call graph*: calls 1 internal fn (_stamp); called by 2 (_page, member_detail); 2 external calls (__init__, clip_to_word).


##### `_member_reader`  (lines 131–139)

```
def _member_reader(member_id: UUID) -> SourceReader
```

**Purpose**: This constructs the reading identity for a signed-in member when they are viewing memory outside a live conversation turn. It captures what that member is allowed to see: their own subject plus the shared workspace subject.

**Data flow**: It receives a member id. It looks up the current agent, derives the audience for that member’s conversation, converts that audience into readable subject labels, and returns a SourceReader containing all of that.

**Call relations**: The member-facing memory page and detail methods use this before reading memories. It connects portal-style reads to the same audience rules used during normal conversation.

*Call graph*: called by 2 (member_detail, member_page); 4 external calls (__init__, audience_subjects, conversation_audience, agent_current).


##### `MemoryObjects.list`  (lines 148–149)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This is the standard list operation for memory objects during a tool request. It returns visible, live memories for the current caller.

**Data flow**: It receives the tool context and list query. It pulls the extension context and the caller’s source reader from the tool context, then asks the shared memory paging helper to fetch and shape the results.

**Call relations**: This is a public entry into the memory object store. It performs only setup and then hands the real database and visibility work to MemoryObjects._page.

*Call graph*: calls 3 internal fn (source_reader, _page, _require_ext).


##### `MemoryObjects.member_page`  (lines 151–164)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This lists memories as a signed-in member would see them from the member portal, outside a tool turn. It deliberately does not let an admin see another person’s private memories through this path.

**Data flow**: It receives the extension context, member id, admin flag, and query. It builds a reader for that member’s own audience, requires the extension context, and returns a page of visible live memories.

**Call relations**: The member portal calls this kind of method when showing a memory list. It uses _member_reader to match normal audience rules, then relies on MemoryObjects._page for the actual read.

*Call graph*: calls 3 internal fn (_page, _member_reader, _require_ext).


##### `MemoryObjects.member_detail`  (lines 166–200)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[MemorySpec] | None
```

**Purpose**: This opens one memory item for a signed-in member and returns both its full detail and the listing row that would represent it. It also supports old references by allowing readable superseded memories to be opened.

**Data flow**: It receives an extension context, memory id text, member id, and admin flag. It builds that member’s reader, fetches the full memory detail, finds any source page link, fetches readable page state for that source, and returns a MemberObject combining row and detail; if anything is not readable or not found, it returns nothing.

**Call relations**: This is the member-portal detail path. It asks MemoryObjects._item for the authoritative detail, then uses _row to make the matching preview row with source-page information.

*Call graph*: calls 4 internal fn (_item, _member_reader, _require_ext, _row); 2 external calls (__init__, UUID).


##### `MemoryObjects.get`  (lines 202–203)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[MemorySpec] | None
```

**Purpose**: This opens one memory item by id during a tool request. It is how a reference returned by memory search can be turned into the full memory text and links.

**Data flow**: It receives the tool context and memory name. It takes the caller’s reader and extension context from the tool context, then asks the item helper to fetch the visible record or return nothing.

**Call relations**: This is the public detail operation for memory objects. It does the request-level setup and delegates database lookup, permission checks, and link building to MemoryObjects._item.

*Call graph*: calls 3 internal fn (source_reader, _item, _require_ext).


##### `MemoryObjects._page`  (lines 205–262)

```
async def _page(self, ext: ExtensionContext, reader: SourceReader, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This does the real work of building a memory listing. It finds live memory rows that match the reader’s allowed subjects, then removes any page-derived memory whose source page is no longer safely readable.

**Data flow**: It receives an extension context, a reader, and a list query. It reads matching non-superseded and non-retired rows from the memory table, fetches readable states for any cited pages, filters out rows whose page state no longer matches, turns the survivors into ObjectRows, and wraps them in an ObjectPage.

**Call relations**: Both the normal list method and member portal page method rely on this helper. It talks to the database through the extension transaction, asks the extension context for page visibility, and uses _row plus object_page to shape the final result.

*Call graph*: calls 3 internal fn (readable_page_states, transaction, _row); called by 2 (list, member_page); 2 external calls (select, object_page).


##### `MemoryObjects._item`  (lines 264–330)

```
async def _item(self, ext: ExtensionContext, reader: SourceReader, name: str) -> ObjectDetail[MemorySpec] | None
```

**Purpose**: This fetches the full detail for one memory item, if the reader is allowed to see it. It also builds provenance links, such as the source page and the newer memory that superseded it.

**Data flow**: It receives an extension context, reader, and memory name. It parses the name as a UUID, queries the memory table in the current workspace and allowed subjects, checks source-page readability when needed, then returns an ObjectDetail containing the full MemorySpec, timestamps, and links; invalid, missing, or hidden items return nothing.

**Call relations**: MemoryObjects.get and MemoryObjects.member_detail use this as their shared detail reader. It calls the database, page-readability service, and object link/detail constructors to produce the opened memory object.

*Call graph*: calls 2 internal fn (readable_page_states, transaction); called by 2 (get, member_detail); 6 external calls (__init__, __init__, __init__, __init__, select, UUID).


##### `MemoryObjects.status`  (lines 332–339)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This reports no special status for memory objects. It exists to satisfy the object store interface, where some object kinds may expose status information.

**Data flow**: It receives the request context, object name, and optional expected generation. It does not inspect or change anything and always returns nothing.

**Call relations**: Callers can ask for object status through the common object interface. For memory items, this method is the simple answer that there is no status channel here.


##### `MemoryObjects.apply`  (lines 341–350)

```
async def apply(self, ctx: ToolContext, name: str, spec: MemorySpec, old: MemorySpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This refuses direct creation or editing of memory objects. Memories must be recorded through the memory_update path so the extension can keep recall and consolidation behavior consistent.

**Data flow**: It receives the context, name, proposed memory spec, old spec, and optional generation check. Instead of writing anything, it raises a VerbNotSupported error explaining that apply is not the write path.

**Call relations**: The common object system may try to route edits through apply. This method blocks that route and points callers back to the proper memory-writing tool.

*Call graph*: 1 external calls (__init__).


##### `MemoryObjects.delete`  (lines 352–359)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This refuses direct deletion of memory objects. A memory ends by being superseded or ignored by recall, not by being removed through this object interface.

**Data flow**: It receives the context, memory name, and optional generation check. It changes no storage and raises a VerbNotSupported error explaining that memories are not deleted here.

**Call relations**: When the object system offers a delete verb, this method protects memory storage from ad hoc removal. Consolidation and recall logic elsewhere decide what is current.

*Call graph*: 1 external calls (__init__).


##### `ProfileObjects.list`  (lines 410–411)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This lists member profiles for the current tool caller, if the caller can read the workspace-shared facts. Profiles summarize each known member’s role and current focus.

**Data flow**: It receives a tool context and list query. It requires the extension context, takes the caller’s readable subject set from the context, and asks the profile paging helper to return the matching page.

**Call relations**: This is the public list operation for profile objects. It does boundary setup, then lets ProfileObjects._page enforce the shared-subject rule and read the database.

*Call graph*: calls 2 internal fn (_page, _require_ext).


##### `ProfileObjects.member_page`  (lines 413–426)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This lists member profiles for a signed-in member outside a live tool turn. Admin status does not widen the view because profiles are based only on shared workspace facts.

**Data flow**: It receives an extension context, member id, admin flag, and query. It derives the subject labels for that member’s conversation, requires the extension context, and returns a page of profiles if the shared subject is included.

**Call relations**: The member portal uses this to show the People band. It builds the same audience subjects used elsewhere and delegates the actual read to ProfileObjects._page.

*Call graph*: calls 2 internal fn (_page, _require_ext); 2 external calls (audience_subjects, conversation_audience).


##### `ProfileObjects.get`  (lines 428–430)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ProfileSpec] | None
```

**Purpose**: This opens one profile by member id during a tool request. It returns just the detailed profile object when the caller is allowed to read shared workspace facts.

**Data flow**: It receives the tool context and profile name. It requires the extension context, uses the context’s readable subjects, asks the entry helper for the matching member profile, and returns the detail part or nothing.

**Call relations**: This is the public detail operation for profiles in the object system. It reuses ProfileObjects._entry, which performs the shared-subject check and database lookup.

*Call graph*: calls 2 internal fn (_entry, _require_ext).


##### `ProfileObjects.member_detail`  (lines 432–442)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[ProfileSpec] | None
```

**Purpose**: This opens one member profile for the member portal and returns both its row and detail. It uses the signed-in member’s normal conversation audience rather than any admin override.

**Data flow**: It receives an extension context, profile name, member id, and admin flag. It derives the member’s readable subjects, requires the extension context, and returns the matching MemberObject or nothing.

**Call relations**: The member-facing detail view calls this path. It hands off to ProfileObjects._entry after translating the member id into the subject set used for visibility.

*Call graph*: calls 2 internal fn (_entry, _require_ext); 2 external calls (audience_subjects, conversation_audience).


##### `ProfileObjects._page`  (lines 444–463)

```
async def _page(self, ext: ExtensionContext, subjects: frozenset[str], query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This does the real work of listing profiles. It only shows profiles to readers who carry the workspace-shared subject, because every profile is written from shared facts.

**Data flow**: It receives an extension context, a set of readable subjects, and a query. If the shared subject is absent, it returns an empty page; otherwise it reads recent profile rows for the workspace, converts each to an ObjectRow, and wraps them in an ObjectPage.

**Call relations**: Both normal and member-facing profile list methods call this helper. It is the central place where profile visibility and profile listing shape are enforced.

*Call graph*: calls 2 internal fn (transaction, _profile_row); called by 2 (list, member_page); 2 external calls (select, object_page).


##### `ProfileObjects._entry`  (lines 465–498)

```
async def _entry(self, ext: ExtensionContext, subjects: frozenset[str], name: str) -> MemberObject[ProfileSpec] | None
```

**Purpose**: This fetches one member profile if it exists and the reader may see shared workspace facts. It returns the profile in both row form and full detail form.

**Data flow**: It receives an extension context, readable subjects, and the profile name. It first checks for the shared subject, parses the name as a member UUID, queries the profile table, normalizes the written time, and returns a MemberObject with ProfileSpec detail; missing, invalid, or hidden profiles return nothing.

**Call relations**: ProfileObjects.get and ProfileObjects.member_detail use this shared detail reader. It talks to the database, uses _profile_row for the preview row, and builds ObjectDetail for the full profile.

*Call graph*: calls 2 internal fn (transaction, _profile_row); called by 2 (get, member_detail); 6 external calls (__init__, __init__, __init__, select, _aware, UUID).


##### `ProfileObjects.status`  (lines 500–507)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This reports no special status for profile objects. It is present because profile objects implement the same object store interface as other kinds.

**Data flow**: It receives the context, profile name, and optional expected generation. It reads and changes nothing, and always returns nothing.

**Call relations**: If the wider object system asks profiles for status, this method supplies the neutral answer. Profiles have no extra status state in this file.


##### `ProfileObjects.apply`  (lines 509–518)

```
async def apply(self, ctx: ToolContext, name: str, spec: ProfileSpec, old: ProfileSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This refuses direct profile edits. Profiles are written by the People pass, which derives them from shared workspace facts, so manual object apply would bypass the intended source of truth.

**Data flow**: It receives the context, name, proposed profile spec, old spec, and optional generation check. It does not write anything and raises a VerbNotSupported error with the refusal message.

**Call relations**: The common object editing route may call apply. This method closes that route for profiles and keeps profile updates owned by the background People pass.

*Call graph*: 1 external calls (__init__).


##### `ProfileObjects.delete`  (lines 520–527)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This refuses direct profile deletion for the same reason edits are refused. Profiles disappear or change only when the People pass rewrites what shared facts support.

**Data flow**: It receives the context, profile name, and optional expected generation. It makes no database change and raises a VerbNotSupported error.

**Call relations**: When callers try to use the common object delete verb on a profile, this method blocks it. It protects the derived People band from manual changes.

*Call graph*: 1 external calls (__init__).


##### `_profile_row`  (lines 530–540)

```
def _profile_row(row: sa.Row) -> ObjectRow
```

**Purpose**: This builds the lightweight listing row for one member profile. It gives callers a compact preview and the fields needed to display or sort the People band.

**Data flow**: It receives a database row containing member id, role, focus, and written time. It creates a summary from role and focus, formats the written time, and returns an ObjectRow with profile fields.

**Call relations**: Profile listing and profile detail both use this helper after reading database rows. It calls the timestamp formatter and word-clipping helper so profile rows stay consistent with the rest of the object interface.

*Call graph*: calls 1 internal fn (_stamp); called by 2 (_entry, _page); 2 external calls (__init__, clip_to_word).


### `extensions/monitors/ufo_ext_monitors/monitor_kind.py`

`domain_logic` · `request handling`

A monitor is like a reminder with a test attached: it runs a command every so often, compares the result to an initial baseline, and wakes the agent if the result changes, fails repeatedly, or reaches a final deadline. This file does not run the monitor itself. Instead, it describes how armed monitors appear as objects inside the wider UFO system.

The key idea is that monitors must be created during a live chat turn, because the system needs to run the command once immediately to record the first result. That first result is the baseline, like taking a photo before watching for changes. Because of that, this file refuses normal "apply" creation and tells callers to use the special monitor action instead.

Once monitors exist, this file provides the object-facing behavior. It can list all armed monitors a member is allowed to see, show the details and current status of one monitor, and delete one to disarm it. Visibility is based on the conversation being watched: if someone can read that conversation, they can see the monitor. Stopping a monitor is stricter: only the creator or a workspace admin may do it. The file also registers the monitor object kind with names, descriptions, searchable list fields, and supported actions.

#### Function details

##### `_owner`  (lines 53–58)

```
def _owner(row: Monitor) -> GeneratedObjectOwner
```

**Purpose**: Builds the ownership record for one monitor. This tells the object system who created the monitor, what audience it is shared with, and which exact monitor record it represents.

**Data flow**: It receives a monitor database row. It reads the creator's member ID, the monitor's audience, and the monitor's unique ID, turns the audience into a shared-access flag, and returns a GeneratedObjectOwner object that the rest of the object system can use for access checks and identity matching.

**Call relations**: When MonitorObjects._member_rows is preparing the list of visible monitors, it calls _owner for each stored monitor. The returned owner record is placed into each listed row so later get, status, or delete requests can confirm they are talking about the same monitor.

*Call graph*: called by 1 (_member_rows); 2 external calls (__init__, subject_shared).


##### `_require_ext`  (lines 61–64)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Makes sure the monitor extension context is present before code tries to read or change monitor storage. The extension context is the bundle of runtime services this extension needs.

**Data flow**: It receives either an ExtensionContext or nothing. If the context is missing, it stops immediately with a clear runtime error. If it is present, it returns the same context unchanged so the caller can safely create a MonitorStore.

**Call relations**: MonitorObjects._member_rows, MonitorObjects._delete_owned, and MonitorObjects._find call this before touching MonitorStore. It acts as a small guardrail so storage operations fail early and clearly if the monitor extension was not wired in.

*Call graph*: called by 3 (_delete_owned, _find, _member_rows).


##### `MonitorObjects._member_rows`  (lines 78–97)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Builds the list entries shown when a member lists monitor objects. Each entry gives a short, useful summary of an armed monitor, including what it watches, when it will run next, who owns it, and whether it belongs to the current member.

**Data flow**: It receives the extension context and the requesting member's ID. It loads all currently armed monitors from MonitorStore, looks up owner email addresses, and turns each monitor into an OwnedRow with a name, a short summary, ownership information, and listable fields such as conversation ID, next probe time, deadline, owner email, and a "mine" flag. It returns all those rows as a tuple.

**Call relations**: This is called by the object framework when someone asks to list monitors they can read. It relies on _require_ext to ensure storage is available, MonitorStore to fetch armed watches, owner_emails to make creator information human-readable, and _owner to attach the access-and-identity record for each monitor.

*Call graph*: calls 2 internal fn (_owner, _require_ext); 3 external calls (__init__, __init__, owner_emails).


##### `MonitorObjects._member_object`  (lines 99–125)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[MonitorSpec] | None
```

**Purpose**: Builds the detailed view for one monitor. It shows the monitor's command, interval, deadline, reason, timestamps, and the conversation where any report will be sent.

**Data flow**: It receives the extension context, the monitor name, the owner identity supplied by the object system, and the requesting member's ID. It looks up the named armed monitor. If no matching monitor exists, or if the stored monitor's unique ID no longer matches the requested owner generation, it returns nothing. Otherwise, it creates a MonitorSpec from the stored command, interval, deadline, and reason, wraps it in an ObjectDetail, and adds a link back to the watched conversation.

**Call relations**: The object framework calls this when someone gets a specific monitor. It hands off the search to MonitorObjects._find, then packages the result using MonitorSpec, ObjectDetail, ObjectLink, and ObjectRef so the wider object system can display the monitor as a normal readable object.

*Call graph*: calls 1 internal fn (_find); 4 external calls (__init__, __init__, __init__, __init__).


##### `MonitorObjects._status`  (lines 127–142)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Provides live status information for one monitor, such as when it was armed, when it last probed, how many probes ran, and what baseline output it is comparing against.

**Data flow**: It receives a tool context, monitor name, and owner identity. It finds the armed monitor by name and checks that its unique ID still matches the owner identity. If the monitor is missing or changed, it returns nothing. Otherwise, it returns a dictionary of simple JSON-ready values: timestamps, probe counters, skipped count, failure and quiet streaks, and a shortened baseline excerpt.

**Call relations**: This is used when the object system wants the operational status beside the monitor's saved specification. It calls MonitorObjects._find to locate the current row, then converts the monitor's internal fields into values that tools or users can read.

*Call graph*: calls 1 internal fn (_find).


##### `MonitorObjects._apply_owned`  (lines 144–152)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: MonitorSpec, old: MonitorSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: Refuses to create or update monitors through the generic object "apply" route. This protects an important rule: arming a monitor must happen through the chat action that runs the first probe and records the baseline.

**Data flow**: It receives the usual apply inputs: tool context, object name, desired monitor specification, previous specification if any, and owner information. It does not use them to write anything. Instead, it raises VerbNotSupported with an explanation telling the caller to arm the monitor through the monitor action.

**Call relations**: The object framework would call this for an apply request, but this monitor kind intentionally stops that flow. It hands back a clear refusal through VerbNotSupported instead of touching MonitorStore, because creation without a live baseline would produce a broken watch.

*Call graph*: 1 external calls (__init__).


##### `MonitorObjects._delete_owned`  (lines 154–159)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: Stops an armed monitor when an allowed user deletes the monitor object. In this system, deleting a monitor means disarming it so it will not probe or fire later.

**Data flow**: It receives a tool context, monitor name, and owner identity. It finds the named armed monitor and verifies that the stored unique ID still matches the requested owner generation. If the monitor is missing or changed, it raises an error. If it matches, it asks MonitorStore to disarm the row. If disarming fails because the row changed, it raises the same kind of error.

**Call relations**: The object framework calls this after its access rules have allowed a delete request. The function uses MonitorObjects._find to locate the row, _require_ext to safely open MonitorStore, and MonitorStore.disarm to perform the actual stop. This is the object-level path from "delete this monitor" to "the watch is no longer armed."

*Call graph*: calls 2 internal fn (_find, _require_ext); 1 external calls (__init__).


##### `MonitorObjects._find`  (lines 161–165)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> Monitor | None
```

**Purpose**: Looks up one currently armed monitor by name. It is a small shared helper used by get, status, and delete operations.

**Data flow**: It receives the extension context and a monitor name. It checks that the context exists, loads all armed monitors from MonitorStore, scans them for the first row whose name matches, and returns that monitor row. If none match, it returns nothing.

**Call relations**: MonitorObjects._member_object, MonitorObjects._status, and MonitorObjects._delete_owned all call this instead of repeating the same lookup logic. It is the common doorway from object requests into the current set of armed monitors stored by MonitorStore.

*Call graph*: calls 1 internal fn (_require_ext); called by 3 (_delete_owned, _member_object, _status); 1 external calls (__init__).


### `extensions/report_digest/ufo_ext_report_digest/objects.py`

`domain_logic` · `request handling`

A report in this extension is not something a user writes directly. It is the record left behind when a scheduled task fires, publishes a report, or fails while trying. This file turns those records into normal workspace objects so the rest of the system can show them, search them, and open their details in the same way it does for other object kinds.

The main class, `ReportObjects`, is the read-only doorway. When asked for a page of reports, it first checks who the reader is, then asks the extension context for only the scheduled runs that person is allowed to read. This is important: the file does not widen access for admins or curious callers. It uses the same audience fence as the original runs.

For each run, it adds helpful surrounding information. It looks up the digest entry written by the report digest job, finds the scheduled task name when possible, and turns any attached files into signed links. A signed link is a temporary URL that proves the reader is allowed to download or preview the file.

Create, update, delete, and status-changing operations are deliberately refused. Like a receipt from a vending machine, a report object exists because something happened; you cannot manufacture the receipt without running the machine.

#### Function details

##### `ReportObjects.list`  (lines 65–74)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows a page of report objects for the currently acting member. It is used when a tool or workspace view asks, “What reports can this person see?”

**Data flow**: It receives a tool context and a list query. If there is no acting member, it returns an empty page. Otherwise it pulls the extension context out of the tool context, uses the current agent and the member’s read permissions, and asks `_page` to build the report rows. The result is an `ObjectPage` ready for the object system to display or filter.

**Call relations**: This is the normal list entry point for report objects. It hands the real fetching work to `_page`, after `_ext` finds the extension context needed to read scheduled runs.

*Call graph*: calls 2 internal fn (_page, _ext); 1 external calls (object_page).


##### `ReportObjects.get`  (lines 76–82)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ReportSpec] | None
```

**Purpose**: Fetches the detail view for one report by name. The name is expected to be the scheduled turn id, written as a UUID string.

**Data flow**: It receives a tool context and a report name. If no member is acting, it returns nothing. Otherwise it resolves the extension context, asks `_one` to find the matching scheduled run within the member’s allowed subjects, and returns only the detail part of the found member object.

**Call relations**: This is called when the object system wants one report rather than a list. It depends on `_one` to validate the name, read the run, and assemble the full object detail.

*Call graph*: calls 2 internal fn (_one, _ext).


##### `ReportObjects.member_page`  (lines 84–92)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Builds a page of report objects for a member-level view, outside the usual tool context. It is useful when the system is browsing objects for a member rather than from an active tool turn.

**Data flow**: It receives an extension context or compatible carrier, a member id, an admin flag, and a list query. It resolves the extension context, uses the general object agent id, and asks `_page` to gather visible scheduled runs and turn them into rows. The admin flag does not expand what is read here.

**Call relations**: This is another public doorway into `_page`. Unlike `list`, it is used by member object browsing flows and gets its agent id from `object_agent_id` rather than the current turn.

*Call graph*: calls 2 internal fn (_page, _ext); 1 external calls (object_agent_id).


##### `ReportObjects.member_detail`  (lines 94–102)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[ReportSpec] | None
```

**Purpose**: Builds the member-level detail view for one report. It returns the report as a `MemberObject`, which includes both its row summary and its detailed information.

**Data flow**: It receives a context carrier, report name, member id, and admin flag. It resolves the extension context and asks `_one` to find the scheduled run for that member. The result is either a complete member object or nothing if the name is invalid or unreadable.

**Call relations**: This mirrors `get` for member browsing flows. It delegates the actual lookup and object construction to `_one`.

*Call graph*: calls 2 internal fn (_one, _ext).


##### `ReportObjects.status`  (lines 104–111)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports no live status for report objects. Reports are historical records of scheduled runs, not objects with an ongoing apply operation to monitor.

**Data flow**: It receives the tool context, report name, and optional expected generation, but does not read or change anything. It always returns `None`, meaning there is no separate status payload.

**Call relations**: This stands in the object-kind interface so callers can ask for status uniformly. For reports, the meaningful state is already in the row fields, especially the run status.


##### `ReportObjects.apply`  (lines 113–122)

```
async def apply(self, ctx: ToolContext, name: str, spec: ReportSpec, old: ReportSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create or update a report object. Reports must be produced by scheduled tasks running, not by direct edits.

**Data flow**: It receives the requested name, desired spec, previous spec, and generation check. Instead of saving anything, it raises `VerbNotSupported` with a message explaining that reports exist only through scheduled runs.

**Call relations**: This protects the object kind from mutation through the generic object API. Callers who want reports to exist must schedule and run tasks; this function deliberately hands off to an error rather than storage.

*Call graph*: 1 external calls (__init__).


##### `ReportObjects.delete`  (lines 124–131)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to delete a report object through the object API. The report list reflects scheduled run history rather than user-maintained records.

**Data flow**: It receives the tool context, report name, and optional generation check. It does not remove anything. It raises `VerbNotSupported` with the same explanation used for create and update attempts.

**Call relations**: This completes the read-only contract for the report object kind. Generic object deletion calls stop here instead of touching scheduled run or digest data.

*Call graph*: 1 external calls (__init__).


##### `ReportObjects._page`  (lines 133–146)

```
async def _page(self, ext: ExtensionContext, member_id: UUID, *, agent_id: UUID, query: ObjectListQuery, subjects: frozenset[str] | None=None) -> ObjectPage
```

**Purpose**: Collects the scheduled runs for a member and turns them into a page of report rows. It is the shared helper behind both normal listing and member-level listing.

**Data flow**: It receives an extension context, member id, agent id, list query, and optional read subjects. It asks the extension context for that member’s newest scheduled runs, capped at the report list limit. It then asks `_rows` to enrich those runs and passes the rows through `object_page`, which applies the object-list query shape.

**Call relations**: `list` and `member_page` both call this after deciding which member and agent to use. `_page` does the broad read, then hands run-by-run formatting to `_rows`.

*Call graph*: calls 2 internal fn (scheduled_runs, _rows); called by 2 (list, member_page); 1 external calls (object_page).


##### `ReportObjects._one`  (lines 148–180)

```
async def _one(self, ext: ExtensionContext, name: str, *, member_id: UUID, subjects: frozenset[str] | None=None) -> MemberObject[ReportSpec] | None
```

**Purpose**: Finds and builds one report object from a report name. It also adds detail metadata such as creation time, update time, and a link back to the conversation where the run happened.

**Data flow**: It receives an extension context, a name, a member id, and optional read subjects. It first tries to parse the name as a UUID. If that fails, it returns nothing. If parsing works, it asks for the matching scheduled run visible to the member. When found, it turns the run into a row with `_rows`, then wraps it in a `MemberObject` with an empty `ReportSpec`, timestamps from the firing time, and a `created_in` link to the conversation.

**Call relations**: `get` and `member_detail` use this for single-report lookups. It relies on the extension context for the permission-aware scheduled run read, and on `_rows` for the same enrichment used by list pages.

*Call graph*: calls 2 internal fn (scheduled_runs, _rows); called by 2 (get, member_detail); 6 external calls (__init__, __init__, __init__, __init__, __init__, UUID).


##### `ReportObjects._rows`  (lines 182–197)

```
async def _rows(self, ext: ExtensionContext, runs: tuple[ScheduledRun, ...]) -> tuple[ObjectRow, ...]
```

**Purpose**: Adds digest entries and task names to a batch of scheduled runs, then converts each run into an object row. This avoids doing separate database lookups for every single run.

**Data flow**: It receives a tuple of scheduled runs. It collects their turn ids and asks `_entries` for digest text keyed by turn id. It also extracts scheduled task ids from the runs’ idempotency keys and asks `_task_names` for human-readable task names. Finally it calls `_row` once per run, passing the matching digest entry and task-name map, and returns all the finished rows.

**Call relations**: `_page` and `_one` call this whenever they need scheduled runs turned into report rows. It coordinates `_entries`, `_task_names`, and `_row` like an assembly line: fetch supporting data first, then build each display row.

*Call graph*: calls 3 internal fn (_entries, _row, _task_names); called by 2 (_one, _page); 1 external calls (scheduled_fire_task_id).


##### `ReportObjects._row`  (lines 199–241)

```
def _row(self, ext: ExtensionContext, run: ScheduledRun, entry: dict[str, JsonValue] | None, tasks: dict[UUID, str]) -> ObjectRow
```

**Purpose**: Turns one scheduled run into the row shape used by the workspace object system. This is where the report’s visible fields are chosen and formatted.

**Data flow**: It receives the extension context, one scheduled run, an optional digest entry, and known task names. It derives the firing task id when possible, looks up the task name, chooses a summary title, formats the firing time, and builds fields such as conversation, agent, status, surface, source, failure text, digest entry, and artifacts. For each artifact, it asks the extension context for signed download and preview links. The output is one `ObjectRow` named by the run’s turn id.

**Call relations**: `_rows` calls this for each run after gathering batch-level supporting data. It uses extension context link helpers so file attachments can be safely shown to the reader.

*Call graph*: calls 2 internal fn (artifact_link, artifact_preview_link); called by 1 (_rows); 2 external calls (__init__, scheduled_fire_task_id).


##### `ReportObjects._entries`  (lines 243–271)

```
async def _entries(self, ext: ExtensionContext, turn_ids: tuple[UUID, ...]) -> dict[UUID, dict[str, JsonValue]]
```

**Purpose**: Reads digest entries for a set of report runs. These entries contain the human-written or generated digest material: title, summary, and key points.

**Data flow**: It receives an extension context and turn ids. If there are no turn ids, it returns an empty dictionary. Otherwise it opens a database transaction, selects digest rows for the current workspace and those turn ids, and reshapes each row into a plain dictionary keyed by turn id. Each point is reduced to its text and actor.

**Call relations**: `_rows` calls this before building rows, so each report can include the digest entry if the writer job has already produced one. It reads from the `report_digest_entry` table and gives the result back to row construction.

*Call graph*: calls 1 internal fn (transaction); called by 1 (_rows); 1 external calls (select).


##### `ReportObjects._task_names`  (lines 273–291)

```
async def _task_names(self, ext: ExtensionContext, task_ids: tuple[UUID, ...]) -> dict[UUID, str]
```

**Purpose**: Looks up the object names of scheduled tasks that fired the report runs. This lets a report say which scheduled task produced it, when that task still exists.

**Data flow**: It receives an extension context and task ids. If there are no ids, it returns an empty dictionary. Otherwise it opens a database transaction, selects matching scheduled task ids and names in the current workspace, and returns a map from task id to task name. Deleted tasks simply will not appear in the result.

**Call relations**: `_rows` calls this after extracting task ids from scheduled runs. `_row` later uses the returned map to place the task name into each report row, or leaves it blank when no name is available.

*Call graph*: calls 1 internal fn (transaction); called by 1 (_rows); 1 external calls (select).


##### `_ext`  (lines 294–299)

```
def _ext(carrier: ToolContext | ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Finds the `ExtensionContext`, which is the object that knows how to read scheduled runs, open database transactions, and make artifact links. It accepts either the context directly or a tool context that contains it.

**Data flow**: It receives a carrier that may be an extension context, a tool context, or `None`. If the carrier already is an extension context, it returns it. If the carrier has an attached extension context, it returns that. If neither is true, it raises a runtime error because the report object code cannot function without that context.

**Call relations**: The public methods `list`, `get`, `member_page`, and `member_detail` call this before doing real work. It is the small adapter that lets the same report object code run from both tool flows and member object flows.

*Call graph*: called by 4 (get, list, member_detail, member_page).


### Publishing and synced content
Site, skill, page, and source handlers make deployed content and user-authored or synced knowledge manageable as workspace objects.

### `extensions/sites/ufo_ext_sites/objects.py`

`domain_logic` · `request handling`

A deployed site is not just a running web page; it also needs a clear identity inside the workspace. This file gives each hosted site an object name made from the site name plus a short digest of the conversation that created it, so two conversations can both have a site called `dashboard` without colliding. Think of it like adding the apartment number to a street address.

The file also enforces the sharing rules. A site can be private, workspace-visible, or public. The creator and workspace admins have special access. Other members only see it once it is shared beyond private. Admins may make a site more private, but they may not widen access. If a site is bound as an agent's homepage, the site no longer uses its own visibility setting; it follows the agent's visibility instead, and attempts to change the site directly are refused.

The main class, `SiteObjects`, plugs into the broader object framework. It turns stored `HostedSite` rows into object list rows, detailed object records, status payloads, and delete/apply actions. It also creates preview links, public site URLs, ownership fields, and conversation links. Without this file, deployed websites might still run, but chat and object tools would not have a consistent, permission-aware way to find or control them.

#### Function details

##### `site_object_name`  (lines 81–85)

```
def site_object_name(conversation_id: UUID, name: str) -> str
```

**Purpose**: Builds the stable object name for a hosted site. It combines the human site name with a short fingerprint of the conversation, so identical names from different conversations stay separate.

**Data flow**: It receives a conversation ID and a site name. It hashes the conversation ID, keeps a short prefix of that hash, and appends it to the site name after a dash. The result is a name like `dashboard-9f21c0a4e3b7`.

**Call relations**: This is the shared naming rule used when sites are collected into lookup tables, when conversation-visible grants are created, and when another helper checks whether an object name belongs to a conversation.

*Call graph*: called by 3 (member_conversation_rows, _named, site_name_from_object); 1 external calls (sha256).


##### `site_name_from_object`  (lines 88–94)

```
def site_name_from_object(conversation_id: UUID, object_name: str) -> str | None
```

**Purpose**: Tries to recover the original site name from an object name, but only if that object name matches the expected conversation digest. This prevents a name from being accepted under the wrong conversation.

**Data flow**: It receives a conversation ID and an object name. It computes the expected digest suffix for that conversation, checks whether the object name ends with it, removes the suffix, and verifies the name by rebuilding it. It returns the plain site name when valid, or `None` when it does not match.

**Call relations**: It uses the same naming rule as `site_object_name`, so parsing and creation stay in sync. It is a validation helper for code that needs to translate an object-style name back into a site registry name.

*Call graph*: calls 1 internal fn (site_object_name); 1 external calls (sha256).


##### `_named`  (lines 97–98)

```
def _named(sites: Iterable[HostedSite]) -> dict[str, HostedSite]
```

**Purpose**: Turns a collection of hosted site records into a dictionary keyed by their workspace object names. This makes later lookups fast and consistent.

**Data flow**: It receives many `HostedSite` records. For each one, it calculates the object name from the site's conversation and name, then stores the site under that key. It returns a map from object name to site record.

**Call relations**: The listing and lookup paths both rely on this helper. It keeps `_member_rows` and `_find` from each inventing their own naming behavior.

*Call graph*: calls 1 internal fn (site_object_name); called by 2 (_find, _member_rows).


##### `_workspace`  (lines 101–104)

```
def _workspace(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Checks that the site object code has an extension context to work with. The extension context is the bundle of workspace-specific services and data access needed here.

**Data flow**: It receives an optional extension context. If one is present, it returns it unchanged. If it is missing, it raises an error because this file cannot read site data without workspace scope.

**Call relations**: Most operations call this before reading workspace settings, transactions, agent visibility, or public URL data. `_sites` also uses it before opening the hosted-site registry.

*Call graph*: called by 6 (_apply_owned, _member_object, _member_rows, _status, member_conversation_rows, _sites).


##### `_sites`  (lines 107–109)

```
def _sites(ext: ExtensionContext | None) -> HostedSites
```

**Purpose**: Creates the workspace-scoped interface to the hosted-site registry. That registry is where deployed sites are stored, updated, and unregistered.

**Data flow**: It receives an optional extension context, confirms it is present, then uses the workspace ID and current transaction from that context to create a `HostedSites` accessor. It returns that accessor for database-style operations.

**Call relations**: Read, update, delete, and find flows all call this when they need the authoritative list of hosted sites. It sits between `SiteObjects` and the underlying store.

*Call graph*: calls 1 internal fn (_workspace); called by 5 (_apply_owned, _delete_owned, _find, _member_rows, member_conversation_rows); 1 external calls (__init__).


##### `effective_visibility`  (lines 112–118)

```
def effective_visibility(site: HostedSite, agents: Mapping[UUID, str]) -> Visibility
```

**Purpose**: Decides which visibility level actually controls access to a site. For normal sites this is the site's own setting; for an agent homepage it is the agent's setting.

**Data flow**: It receives a site record and a map of agent IDs to their visibility levels. If the site is not bound to an agent homepage, it returns the site's visibility. If it is bound, it looks up that agent and converts the agent's level into a site visibility value.

**Call relations**: Listing, object detail, and visibility-change code call this so they all agree on the rule. This is especially important because homepage-bound sites deliberately ignore their own stored visibility column.

*Call graph*: called by 3 (_apply_owned, _member_object, _member_rows); 1 external calls (visibility_level).


##### `_summary`  (lines 121–122)

```
def _summary(site: HostedSite, visibility: Visibility) -> str
```

**Purpose**: Creates a short human-readable summary for a site row. The summary names the site, shows who can see it, and shows the sandbox port serving it.

**Data flow**: It receives a site record and the visibility level to display. It formats those pieces into one compact string and returns it.

**Call relations**: `_member_rows` uses this when turning stored site records into rows for object listings.

*Call graph*: called by 1 (_member_rows).


##### `_preview_url`  (lines 125–132)

```
def _preview_url(scoped: ExtensionContext, site: HostedSite) -> str | None
```

**Purpose**: Builds a temporary image link for the screenshot captured during the site's last deploy, when such a screenshot exists. This lets listings show a preview without inventing a separate image route.

**Data flow**: It receives the workspace context and a site record. If the site has no stored preview blob or no recorded size, it returns `None`. Otherwise it asks the context to create a signed image preview URL and returns that link.

**Call relations**: `_member_rows` calls this while building listing fields, adding `preview_url` only when there is a real captured image to show.

*Call graph*: calls 1 internal fn (image_preview_url); called by 1 (_member_rows).


##### `SiteObjects._admin_can_apply`  (lines 153–154)

```
def _admin_can_apply(self, old: SiteSpec, spec: SiteSpec) -> bool
```

**Purpose**: Defines the one visibility change a workspace admin is allowed to make without being the creator: making a shared site private. This protects creators from admins broadening access on their behalf.

**Data flow**: It receives the old site spec and the requested new spec. It returns `true` only when the old visibility is not already private and the requested visibility is private. It does not change anything itself.

**Call relations**: The parent object framework consults this during an apply operation. If an admin tries to widen access, this method does not approve that path.


##### `SiteObjects._listed`  (lines 156–157)

```
def _listed(self, row: OwnedRow[GeneratedObjectOwner], query: ObjectListQuery) -> bool
```

**Purpose**: Decides whether a site row should appear in a normal listing. Homepage-bound sites are hidden unless the caller specifically asks for homepage bindings.

**Data flow**: It receives a prepared object row and the list query. If the row has no `homepage_agent` field, it can be listed normally. If it does have that field, it is listed only when the query includes a `homepage_agent` filter.

**Call relations**: The object listing framework uses this as a final row filter. It keeps agent homepages from appearing as ordinary shared sites unless the read is intentionally about that binding.


##### `SiteObjects.list`  (lines 159–169)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Adds a convenient shortcut for agents listing their own homepage site. The caller can filter `homepage_agent` as `mine` instead of knowing its own agent ID.

**Data flow**: It receives a tool context and an object-list query. If the filter says `homepage_agent=mine`, it replaces `mine` with the current turn's agent ID. Then it passes the revised query to the inherited listing behavior and returns that result.

**Call relations**: This method runs before the general member-readable listing machinery. It translates a viewer-relative word into a concrete ID, then lets the base class continue the normal list flow.

*Call graph*: 1 external calls (replace).


##### `SiteObjects._member_rows`  (lines 171–217)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Builds the object-list rows a member is allowed to read. Each row is a workspace object view of one registered hosted site, with ownership, visibility, URLs, and useful metadata.

**Data flow**: It receives the extension context and, when known, the member ID of the viewer. It reads all hosted sites, workspace public URL settings, agent visibility levels, and creator email addresses. It then creates one `OwnedRow` per site, including fields such as conversation ID, creation time, effective visibility, owner email, whether it is mine, deploy generation, site URL, homepage agent, and preview URL. It returns all rows as a tuple.

**Call relations**: The broader object framework calls this when producing site listings. It gathers data from the site registry, workspace context, agent visibility map, and preview helper, then hands normalized rows back to the object system.

*Call graph*: calls 6 internal fn (_named, _preview_url, _sites, _summary, _workspace, effective_visibility); 4 external calls (__init__, __init__, owner_emails, site_url).


##### `SiteObjects.member_conversation_rows`  (lines 219–244)

```
async def member_conversation_rows(self, ext: ExtensionContext | None, conversation_id: UUID, *, member_id: UUID, admin: bool, limit: int) -> tuple[ConversationObjectGrant, ...]
```

**Purpose**: Reports which site objects should be visible inside a particular conversation for a particular member. This lets conversation views discover hosted sites tied to that conversation.

**Data flow**: It receives the extension context, conversation ID, member ID, admin flag, and a limit. It reads agent visibility levels, asks the hosted-site store for sites visible in that conversation, and marks workspace-visible agent homepages as eligible. It returns conversation object grants naming each visible site and its generation.

**Call relations**: Conversation object discovery calls this when it needs site objects related to one conversation. The method asks the store for permission-filtered sites, names them with `site_object_name`, and returns grants to the conversation object layer.

*Call graph*: calls 3 internal fn (_sites, _workspace, site_object_name); 1 external calls (__init__).


##### `SiteObjects._member_object`  (lines 246–269)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[SiteSpec] | None
```

**Purpose**: Builds the detailed object record for one site when a member reads it by name. The detail includes the current effective visibility and a link back to the conversation that created it.

**Data flow**: It receives the extension context, object name, owner information, and optional member ID. It looks up the hosted site. If none exists, it returns `None`. If found, it reads agent visibility, creates a `SiteSpec` using the effective visibility, adds timestamps and a `created_in` conversation link, and returns an `ObjectDetail`.

**Call relations**: The object framework calls this for object-get style reads. It depends on `_find` for locating the site and `effective_visibility` so homepage-bound rows show the agent-controlled visibility rather than the dormant site column.

*Call graph*: calls 3 internal fn (_find, _workspace, effective_visibility); 4 external calls (__init__, __init__, __init__, __init__).


##### `SiteObjects._status`  (lines 271–298)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Builds an operational status payload for a site. This includes the live URL, port, creator, deploy generation, homepage binding, and sometimes the stored source files.

**Data flow**: It receives the tool context, object name, and owner information. It finds the site and returns `None` if it is gone. Otherwise it builds a dictionary with site details and a generated public URL. If the site has a source manifest, it materializes the stored source into the sandbox and adds the destination path plus file list.

**Call relations**: Status reads call this after the object has been identified. It hands off URL construction to `site_url` and source restoration to `materialize_source`, so callers can inspect or edit a deployed static site.

*Call graph*: calls 2 internal fn (_find, _workspace); 2 external calls (materialize_source, site_url).


##### `SiteObjects._apply_owned`  (lines 300–336)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SiteSpec, old: SiteSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: Applies an allowed visibility change to an existing deployed site. It refuses creation, refuses direct changes to agent-homepage visibility, and creates a share card when an older site first becomes public and has a screenshot.

**Data flow**: It receives the tool context, object name, requested spec, old spec, and owner. If there is no old object or owner, it raises an error because sites must be created by deployment, not by object apply. It finds the site, compares the requested visibility to the effective one, and returns early if nothing changes. If the site is an agent homepage, it raises an error telling the caller to change the agent instead. Otherwise it updates the stored site visibility. If the site is becoming public and has a stored preview but no share card yet, it draws a card from the stored screenshot.

**Call relations**: The object framework calls this during an apply operation after ownership and gate checks. It uses `_find`, `_sites`, and `effective_visibility` for the main update path, then hands off to `draw_from_stored_shot` only for the public-share-card side effect.

*Call graph*: calls 4 internal fn (_find, _sites, _workspace, effective_visibility); 2 external calls (__init__, draw_from_stored_shot).


##### `SiteObjects._delete_owned`  (lines 338–342)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: Unhosts an existing site by removing it from the hosted-site registry. After this, the permanent link stops resolving through the site system.

**Data flow**: It receives the tool context, object name, and owner information. It looks up the site. If it cannot find it, it raises an error because the site disappeared during deletion. If found, it asks the hosted-site store to unregister the site by conversation ID and site name.

**Call relations**: The object framework calls this for delete operations once the caller has passed the delete gate. It uses `_find` to translate the object name into a stored site row, then `_sites` to perform the unregister action.

*Call graph*: calls 2 internal fn (_find, _sites).


##### `SiteObjects._find`  (lines 344–345)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> HostedSite | None
```

**Purpose**: Finds one hosted site by its object name. It is the local lookup helper used by reads, status checks, visibility changes, and deletes.

**Data flow**: It receives the extension context and an object name. It reads all hosted sites for the workspace, builds the object-name map, and returns the matching site record if present. If no row matches, it returns `None`.

**Call relations**: Several higher-level methods call this at the start of their work. It centralizes the object-name-to-site-row translation so get, status, apply, and delete all search the same way.

*Call graph*: calls 2 internal fn (_named, _sites); called by 4 (_apply_owned, _delete_owned, _member_object, _status).


### `extensions/skill_create/ufo_ext_skill_create/manifest.py`

`orchestration` · `startup registration, object requests, skill loading, scheduled indexing`

This file is the public face of the “create skill” extension. A skill here is a bundle of text files, with a required SKILL.md file, saved for the whole workspace rather than for one person or one agent. Think of it like a shared recipe card box: members can add or update recipe cards, agents can later pull from the box when they need one, and the system keeps an index so cards can be found by keyword.

The file defines the shape of a saved skill, including whether files are new text, copied from the workspace, or kept unchanged by referring to their stored fingerprint. It checks that skill files stay inside the skill’s own folder, that they are readable text, and that the total number and size of files stay within limits. This prevents a skill save from accidentally grabbing unrelated files or storing too much data.

It also exposes skills as workspace objects: list them, get their details without dumping file bodies, apply changes, check status, and delete them with generation checks so one person does not overwrite another person’s newer edit. Separately, it provides a search tool that ranks all loadable skills by keywords. Finally, a scheduled indexing job keeps saved skill descriptions searchable by embedding them into the project’s search index.

#### Function details

##### `_require_ext`  (lines 119–122)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: This small guard makes sure the code has an extension context, which is the object that gives access to the current workspace, database, sandbox, index, and related services. It fails early with a clear error if that context is missing.

**Data flow**: It receives either an extension context or nothing. If the context is present, it returns it unchanged; if it is missing, it raises an error before any database or workspace work can happen.

**Call relations**: The object methods call this before reading, saving, resolving, or deleting skills. It acts like checking that you have the right keys before trying to open the workspace storage room.

*Call graph*: called by 8 (_resolve, apply, delete, get, list, member_detail, member_page, status).


##### `_contained_keys`  (lines 125–132)

```
def _contained_keys(name: str, spec: UserSkillSpec) -> None
```

**Purpose**: This validates that every file path in a skill really belongs inside that skill’s own folder. It protects the system from paths that try to escape elsewhere in the workspace, such as with “../”.

**Data flow**: It receives the skill name and the proposed skill specification. It computes the skill’s allowed root folder, checks each file key against that root, and either finishes silently or raises a clear validation error.

**Call relations**: SkillObjects.apply calls this before saving anything. It relies on the sandbox path checker and the skill-root helper so later file resolution can assume the file names are safe.

*Call graph*: called by 1 (apply); 2 external calls (contained_relative, skill_root).


##### `_text`  (lines 135–141)

```
def _text(path: str, content: bytes) -> str
```

**Purpose**: This confirms that a skill file is plain UTF-8 text. Skills in this system may bundle text files, not arbitrary binary files such as images or compiled data.

**Data flow**: It receives a skill-relative path and the file’s raw bytes. It tries to decode those bytes as text; on success it returns the decoded string, and on failure it raises a validation error naming the bad file.

**Call relations**: SkillObjects._resolve calls this after it has gathered each file’s bytes. It is the final text-only gate before those bytes become the saved skill contents.

*Call graph*: called by 1 (_resolve).


##### `SkillObjects.list`  (lines 148–149)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns a paged list of saved workspace skills for use inside a tool turn. It gives callers a concise table-style view rather than full file contents.

**Data flow**: It receives the tool context and a list query such as paging or filtering options. It checks the extension context, builds rows from the skill store, applies paging through object_page, and returns an ObjectPage.

**Call relations**: The object system calls this when someone lists skill objects. It delegates the actual row-building to SkillObjects._rows and uses the shared paging helper to format the result.

*Call graph*: calls 2 internal fn (_rows, _require_ext); 1 external calls (object_page).


##### `SkillObjects.member_page`  (lines 151–162)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns the same kind of saved-skill list for a signed-in member viewing skills outside an agent turn, such as in a portal. The skills are workspace-owned, so the member and admin flags do not narrow the saved set here.

**Data flow**: It receives an extension context, member information, admin information, and a list query. It verifies the context, reads the workspace skill rows, applies paging, and returns the page.

**Call relations**: Portal-style member views use this path, while turn-time tool views use SkillObjects.list. Both share SkillObjects._rows so both views describe the same saved skills.

*Call graph*: calls 2 internal fn (_rows, _require_ext); 1 external calls (object_page).


##### `SkillObjects.get`  (lines 164–165)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[UserSkillSpec] | None
```

**Purpose**: This fetches one saved skill’s object details during a tool turn. It returns metadata and file fingerprints, not the actual file bodies, so large or sensitive content is not echoed into the caller’s context.

**Data flow**: It receives the tool context and skill name. It checks the extension context, asks SkillObjects._skill for the stored detail, and returns either that detail or null if the skill does not exist.

**Call relations**: The object system calls this for object_get-style reads. It hands the real lookup and digest-building work to SkillObjects._skill.

*Call graph*: calls 2 internal fn (_skill, _require_ext).


##### `SkillObjects.member_detail`  (lines 167–185)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[UserSkillSpec] | None
```

**Purpose**: This returns a signed-in member’s detailed view of one saved workspace skill. It combines the list-row summary with the detail record that contains file digests and timestamps.

**Data flow**: It receives an extension context, a skill name, member information, and admin information. It verifies the context, finds the matching row, loads the detail, and returns a MemberObject; if either part is missing, it returns null.

**Call relations**: Member-facing pages use this when showing one saved skill. It calls SkillObjects._rows for the visible row and SkillObjects._skill for the stored detail, then wraps both together.

*Call graph*: calls 3 internal fn (_rows, _skill, _require_ext); 1 external calls (__init__).


##### `SkillObjects._rows`  (lines 187–195)

```
async def _rows(self, ext: ExtensionContext) -> tuple[ObjectRow, ...]
```

**Purpose**: This builds the lightweight rows used when listing saved skills. Each row includes the skill name, a shortened description, and whether it is pinned.

**Data flow**: It receives an extension context. It asks UserSkillStore for the workspace’s skill listing, turns each stored listing item into an ObjectRow, and returns all rows as a tuple.

**Call relations**: SkillObjects.list, SkillObjects.member_page, and SkillObjects.member_detail all use this shared helper so every listing view is consistent.

*Call graph*: called by 3 (list, member_detail, member_page); 2 external calls (__init__, __init__).


##### `SkillObjects._skill`  (lines 197–212)

```
async def _skill(self, ext: ExtensionContext, name: str) -> ObjectDetail[UserSkillSpec] | None
```

**Purpose**: This builds the detailed object view for one saved skill without exposing the file contents. Instead of returning each file body, it returns a SHA-256 digest, which is a fingerprint used to prove unchanged content.

**Data flow**: It receives an extension context and a skill name. It reads the stored record, turns each file’s bytes into a digest and size, places those into a UserSkillSpec, and returns an ObjectDetail with timestamps and generation; if no record exists, it returns null.

**Call relations**: SkillObjects.get and SkillObjects.member_detail call this when they need one skill’s details. Later, SkillObjects.apply can accept those digests back as FileRef values to keep unchanged files without resending their contents.

*Call graph*: called by 2 (get, member_detail); 5 external calls (__init__, __init__, __init__, __init__, sha256).


##### `SkillObjects.status`  (lines 214–231)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This gives a compact status summary for a saved skill, such as description, file count, byte count, and pinned state. It also checks that the caller is looking at the expected version.

**Data flow**: It receives the tool context, skill name, and an expected generation value. It loads the stored record; if missing, it returns null. If the generation does not match, it raises an error. Otherwise it returns a small dictionary of status fields.

**Call relations**: The object workflow can call this after reading or changing a skill to confirm the current state. Its generation check supports safe editing by detecting when someone else saved a newer version first.

*Call graph*: calls 1 internal fn (_require_ext); 1 external calls (__init__).


##### `SkillObjects.apply`  (lines 233–256)

```
async def apply(self, ctx: ToolContext, name: str, spec: UserSkillSpec, old: UserSkillSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This saves a new or updated workspace skill after checking that it is safe and small enough. It is the main write path for member-authored skills.

**Data flow**: It receives the tool context, skill name, proposed spec, previous spec, and expected generation. It checks the context, enforces the maximum file count, verifies file paths, resolves all file bodies, checks total byte size, and saves the result into UserSkillStore with the pinned flag and generation guard.

**Call relations**: The object system calls this when a manifest is applied. It uses _contained_keys for path safety and SkillObjects._resolve for turning inline text, workspace file references, and stored file references into actual bytes before handing them to the store.

*Call graph*: calls 3 internal fn (_resolve, _contained_keys, _require_ext); 1 external calls (__init__).


##### `SkillObjects.delete`  (lines 258–269)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This removes a saved workspace skill, but only if the caller is not working from a stale version. That prevents accidental deletion after another edit has happened.

**Data flow**: It receives the tool context, skill name, and expected generation. It loads the current record; if the record exists and its generation differs from the expected one, it raises an error. Otherwise it asks the store to delete the skill.

**Call relations**: The object system calls this for delete operations. It uses UserSkillStore for both the version check and the final removal.

*Call graph*: calls 1 internal fn (_require_ext); 1 external calls (__init__).


##### `SkillObjects._resolve`  (lines 271–319)

```
async def _resolve(self, ctx: ToolContext, name: str, spec: UserSkillSpec) -> dict[str, bytes]
```

**Purpose**: This turns the mixed file values in a skill spec into the exact bytes that will be saved. It supports three cases: new inline text, a workspace file copied in at apply time, or an unchanged stored file kept by digest.

**Data flow**: It receives the tool context, skill name, and spec. It loads existing stored files for digest-based keeps, verifies those digests, reads any referenced workspace files through the sandbox, decodes the sandbox’s base64 output back into bytes, converts inline strings to bytes, checks every file is UTF-8 text, and returns a dictionary of final file bytes.

**Call relations**: SkillObjects.apply calls this before saving. This helper is where file references become durable stored content, and it calls _text so invalid binary files are rejected before reaching UserSkillStore.

*Call graph*: calls 2 internal fn (_require_ext, _text); called by 1 (apply); 7 external calls (__init__, b64decode, sha256, dumps, loads, quote, workspace_path).


##### `skill_search`  (lines 371–388)

```
async def skill_search(ctx: ToolContext, args: SkillSearchInput) -> ToolResult
```

**Purpose**: This searches all currently loadable skill cards by keyword and returns matching “name: description” lines. It helps a caller discover a skill even when it is not already visible in a short list.

**Data flow**: It receives the tool context and a query with a result limit. It reads all skill cards, scores each card against the query, sorts best matches first, keeps positive matches up to the limit, and returns text lines; if nothing matches, it returns a message saying how many skills were searched.

**Call relations**: SKILL_SEARCH_TOOL uses this as its handler. It depends on the same lexical scoring used elsewhere for skill routing, and it returns only summaries so callers can decide whether to load a skill by name.

*Call graph*: 3 external calls (__init__, __init__, lexical_score).


##### `_member_cards`  (lines 405–406)

```
async def _member_cards(ctx: ExtensionContext) -> tuple[SkillCard, ...]
```

**Purpose**: This returns the routing cards for member-authored workspace skills. A routing card is the short name-and-description summary used to decide whether a skill is relevant.

**Data flow**: It receives an extension context, opens the UserSkillStore for that workspace, and returns the stored skill cards.

**Call relations**: The manifest registers this as the member-skills card provider. The runtime calls it when it needs to know which workspace skills can be considered for loading.

*Call graph*: 1 external calls (__init__).


##### `_materialize_skill`  (lines 409–410)

```
async def _materialize_skill(ctx: ExtensionContext, name: str) -> RuntimeSkill | None
```

**Purpose**: This loads one saved workspace skill into the runtime form an agent can actually use. “Materialize” here means turning the stored record back into a RuntimeSkill object.

**Data flow**: It receives an extension context and a skill name. It asks UserSkillStore to materialize that named skill and returns the runtime skill, or null if it cannot be loaded.

**Call relations**: The manifest registers this as the single-skill loader for member skills. It is used when the runtime has chosen a specific workspace skill to load.

*Call graph*: 1 external calls (__init__).


##### `_materialize_all_skills`  (lines 413–414)

```
async def _materialize_all_skills(ctx: ExtensionContext) -> tuple[RuntimeSkill, ...]
```

**Purpose**: This loads all saved workspace skills into runtime skill objects. It is useful when the system needs the full workspace skill set rather than one named skill.

**Data flow**: It receives an extension context. It asks UserSkillStore to materialize every saved skill and returns them as a tuple.

**Call relations**: The manifest registers this as the all-skills loader for member skills. The runtime uses it when it needs the complete saved skill collection for a workspace.

*Call graph*: 1 external calls (__init__).


##### `index_skills`  (lines 417–466)

```
async def index_skills(ctx: ExtensionContext) -> None
```

**Purpose**: This scheduled job keeps saved skill descriptions searchable. It finds skills whose stored search index is missing or stale, embeds their routing text, and marks them as indexed only if the skill has not changed mid-work.

**Data flow**: It receives an extension context. It checks that both the index backend and embedding client are available, queries the database for stale skill cards, and then tries to index each one. It logs individual failures and continues; if every attempted card fails, it raises the last error so the job is visibly unhealthy.

**Call relations**: The manifest registers this as the skill_index job. For each stale row, it calls _index_card, which performs the actual index update and digest-settle step.

*Call graph*: calls 2 internal fn (transaction, _index_card); 3 external calls (__init__, or_, select).


##### `_index_card`  (lines 469–506)

```
async def _index_card(ctx: ExtensionContext, index: IndexBackend, embed: EmbedClient, chunker: TextChunker, row: sa.Row) -> None
```

**Purpose**: This indexes one skill’s routing card and then records that the current digest has been indexed. If the skill was deleted while indexing, it cleans up the stale index entry instead.

**Data flow**: It receives the extension context, index backend, embedding client, text chunker, and a database row for one stale skill. It sends the skill name and shortened description through chunking, embedding, and upsert into the index. Then it updates the database only if the row still has the same digest; if no skill with that name remains, it deletes that skill’s index scope.

**Call relations**: index_skills calls this for each stale skill. It hands off the search-text work to chunk_embed_upsert and uses database transactions to avoid marking changed content as safely indexed.

*Call graph*: calls 2 internal fn (transaction, delete); called by 1 (index_skills); 4 external calls (__init__, select, update, chunk_embed_upsert).


##### `_skills_awaiting_index`  (lines 509–519)

```
def _skills_awaiting_index() -> sa.Select[tuple[UUID]]
```

**Purpose**: This builds the database query used to find workspaces that have skills needing search indexing. It identifies any workspace with at least one skill whose indexed digest is missing or out of date.

**Data flow**: It takes no runtime data. It returns a SQL select statement that points at distinct workspace IDs with stale skill index state.

**Call relations**: manifest passes this query builder to owner_candidates for the scheduled job. That lets the job runner choose only workspaces where indexing work is actually needed.

*Call graph*: 2 external calls (or_, select).


##### `manifest`  (lines 522–542)

```
def manifest() -> Manifest
```

**Purpose**: This declares the extension to the larger system: its name, version, tool, object kind, built-in authoring skill, member-skill loaders, and scheduled indexing job. It is the hook that makes everything in this file discoverable.

**Data flow**: It takes no input. It constructs and returns a Manifest containing the skill search tool, the skill object store, the bundled create-skill skill, member-skill callbacks, and the periodic indexing job specification.

**Call relations**: The extension loader calls this at startup. The returned Manifest wires the rest of the file into the platform so object operations, skill loading, searching, and indexing can happen later.

*Call graph*: 5 external calls (__init__, __init__, __init__, __init__, owner_candidates).


### `extensions/sources/ufo_ext_sources/pages.py`

`domain_logic` · `request handling`

A “page” here means one document brought in by the content sync system from a registered source, such as an issue tracker or another provider stream. This file turns those internal synced records into workspace objects that tools can browse safely. Without it, synced content might exist in storage, but users and agents would not have a standard way to list pages, read their metadata, fetch their body text, or forget a page through the object system.

The file separates the page into two layers. `PageSpec` is the public shape of a page: source details, title, timestamps, visibility subject, digest, blob reference, and a bounded copy of the body. `_Page` is the internal wrapper around a stored page row. It knows how to turn that row into summaries, searchable fields, links back to the source that synced it, and the public `PageSpec`.

`PageObjects` is the object-store adapter. Listing gathers all pages the current reader is allowed to see and turns them into rows. Getting a page also reads its body from blob storage, but only up to 65,536 UTF-8 bytes, like opening just the first chunk of a large document. It then rechecks that the page has not changed while the body was being read. Create and update are refused because pages are produced only by sync. Delete is really “forget”: only an admin can tombstone the page so downstream indexes can be cleaned up.

#### Function details

##### `_require_ext`  (lines 62–65)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This helper makes sure a tool request has the extension context needed to talk to source-page storage. It fails loudly if page object code was called without that context, because the rest of this file cannot work without it.

**Data flow**: It receives a `ToolContext`, looks for its `ext` field, and returns that extension context if present. If the field is missing, it raises a runtime error instead of letting later code fail in a more confusing way.

**Call relations**: The page operations call this before using extension-only abilities. `_pages` uses it to read sources and pages, `get` uses it to recheck page state, and `delete` uses it to forget a page.

*Call graph*: called by 3 (_pages, delete, get).


##### `_page_timestamp`  (lines 68–78)

```
def _page_timestamp(provider_value: str | None, row_value: datetime) -> str
```

**Purpose**: This function turns a page timestamp into a consistent UTC timestamp string. It accepts either a provider-supplied time or, if that is missing, the workspace row time.

**Data flow**: It takes an optional timestamp string from the original provider and a fallback `datetime` from the local row. If the provider string exists, it parses it and requires a timezone; if not, it uses the row time and assumes UTC when needed. It returns an ISO-formatted UTC timestamp with microseconds.

**Call relations**: `_Page.spec` and `_Page.fields` use this when exposing page times. That keeps the detailed page view and the list/search fields speaking the same timestamp language.

*Call graph*: called by 2 (fields, spec); 2 external calls (fromisoformat, replace).


##### `_Page.name`  (lines 99–100)

```
def name(self) -> str
```

**Purpose**: This property gives the page its object name. The name is simply the page row’s UUID written as text.

**Data flow**: It reads the page’s internal `id` value and converts it to a string. Nothing else is changed.

**Call relations**: The listing and lookup code rely on this name as the user-facing identifier. `PageObjects.list` shows it, and `_find` compares requested names against it.


##### `_Page.links`  (lines 102–110)

```
def links(self) -> tuple[ObjectLink, ...]
```

**Purpose**: This function builds a link from a page back to the source object that synced it, when that source has a known object name. The link helps readers understand where the page came from.

**Data flow**: It checks whether `_Page.source_name` is present. If not, it returns no links. If present, it creates an `ObjectLink` whose relation is `synced_by` and whose target points to the matching source object.

**Call relations**: `PageObjects.get` includes these links in the returned object detail. The link is made only after `_pages` has joined page records to source records and calculated the source object name.

*Call graph*: 2 external calls (__init__, __init__).


##### `_Page.spec`  (lines 112–125)

```
def spec(self, body: str, body_truncated: bool) -> PageSpec
```

**Purpose**: This function turns the internal page wrapper into the public page data shape returned by object reads. It combines stored metadata with the body text that was read separately from blob storage.

**Data flow**: It receives the already-read body text and a flag saying whether the body was cut short. It reads the page’s source, stream, title, timestamps, visibility subject, digest, and blob reference, normalizes timestamps through `_page_timestamp`, and returns a `PageSpec` object.

**Call relations**: `PageObjects.get` calls this after it has found the page, read a bounded body, and confirmed the page did not change during the read. `_page_timestamp` is used here so the public detail view has valid UTC times.

*Call graph*: calls 1 internal fn (_page_timestamp); 1 external calls (__init__).


##### `_Page.summary`  (lines 127–128)

```
def summary(self) -> str
```

**Purpose**: This function creates a short human-readable line for a page in list results. It gives enough context to recognize the page without opening it.

**Data flow**: It combines the title, source backend, stream, and visibility subject into one string, then trims it to the configured maximum length. It returns that short summary and changes nothing.

**Call relations**: `PageObjects.list` uses this to fill each list row. It is the quick label users see while browsing many pages.


##### `_Page.fields`  (lines 130–138)

```
def fields(self) -> dict[str, JsonValue]
```

**Purpose**: This function produces the searchable and sortable metadata fields shown in page lists. These are the compact facts about a page, not the full body.

**Data flow**: It reads the page’s source id, backend, stream, title, and timestamps. It normalizes the timestamps through `_page_timestamp` and returns a dictionary of JSON-friendly values.

**Call relations**: `PageObjects.list` places these fields into each `ObjectRow`. The object kind declaration later marks these same fields as available for filtering and ordering.

*Call graph*: calls 1 internal fn (_page_timestamp).


##### `PageObjects.list`  (lines 149–154)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This is the list operation for page objects. It returns the synced pages the current caller is allowed to see, as compact rows suitable for browsing and filtering.

**Data flow**: It takes the tool context and list query, asks `_pages` for visible page wrappers, turns each wrapper into an `ObjectRow` with a name, summary, and fields, then passes those rows and the query to `object_page` to produce a paged result.

**Call relations**: This is called by the object system when someone lists `page` objects. It depends on `_pages` to do the permission-aware read and source joining, then hands the rows to the SDK’s paging helper.

*Call graph*: calls 1 internal fn (_pages); 2 external calls (__init__, object_page).


##### `PageObjects.get`  (lines 156–201)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[PageSpec] | None
```

**Purpose**: This is the read operation for one page object. It returns page metadata plus a safe, size-limited slice of the page body.

**Data flow**: It receives a requested page name, finds the matching visible page, and returns `None` if there is no match. If found, it streams the body bytes from blob storage, keeps only up to 65,536 UTF-8 bytes plus one extra byte to detect truncation, decodes safely, then rereads the page state to make sure the subject, revision, digest, and body reference still match. If the page changed during the read, it returns `None`; otherwise it returns an `ObjectDetail` with the spec, timestamps, and links.

**Call relations**: The object system calls this when a user or agent opens a page by name. It uses `_find` for lookup, the blob capability on the context for body bytes, `_require_ext` and `source_reader` to recheck current readable state, and `_Page.spec` and `_Page.links` to build the final detail.

*Call graph*: calls 3 internal fn (source_reader, _find, _require_ext); 1 external calls (__init__).


##### `PageObjects.status`  (lines 203–210)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This function reports that page objects have no separate apply/status workflow. For synced pages, there is no pending operation status to return.

**Data flow**: It receives the context, page name, and optional expected generation, but does not read or change anything. It always returns `None`.

**Call relations**: The object interface includes a status hook, so this method fills that slot for pages. Since pages are read-only projections of sync data, it does not hand off to other page logic.


##### `PageObjects.apply`  (lines 212–221)

```
async def apply(self, ctx: ToolContext, name: str, spec: PageSpec, old: PageSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This function blocks manual creation or update of page objects. Pages must come from the content sync driver, not from direct user edits.

**Data flow**: It receives the requested name, new spec, optional old spec, and optional expected generation. Instead of saving anything, it raises `VerbNotSupported` with an explanation that synced pages are not authored directly.

**Call relations**: The object system calls this for create or update attempts. Rather than calling storage code, it stops the request immediately, preserving the rule that only registered sources and the sync driver produce pages.

*Call graph*: 1 external calls (__init__).


##### `PageObjects.delete`  (lines 223–235)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This function lets an admin forget a synced page. “Delete” here means tombstoning the page so the existing page-change pipeline can clean up derived index data.

**Data flow**: It receives the page name and context. First it asks whether the speaker is a workspace admin; if not, it raises `AdminRequired`. Then it finds the page by name; if missing, it raises a value error. If present, it asks the extension context to forget that page id.

**Call relations**: The object system calls this for page deletion requests. It uses `speaker_is_admin` for the permission gate, `_find` to locate only a visible page, and `_require_ext` to call `forget_page` in the extension storage layer.

*Call graph*: calls 3 internal fn (speaker_is_admin, _find, _require_ext); 1 external calls (__init__).


##### `PageObjects._find`  (lines 237–238)

```
async def _find(self, ctx: ToolContext, name: str) -> _Page | None
```

**Purpose**: This helper finds one visible page wrapper by its object name. It keeps get and delete from duplicating the same lookup logic.

**Data flow**: It receives the context and requested name, asks `_pages` for the visible pages, scans for the first page whose `name` matches, and returns that page. If none match, it returns `None`.

**Call relations**: `PageObjects.get` uses this before reading a body, and `PageObjects.delete` uses it before forgetting a page. It delegates the real page collection and permission filtering to `_pages`.

*Call graph*: calls 1 internal fn (_pages); called by 2 (delete, get).


##### `PageObjects._pages`  (lines 240–268)

```
async def _pages(self, ctx: ToolContext) -> tuple[_Page, ...]
```

**Purpose**: This helper gathers all live synced pages the current reader may see and enriches them with source information. It is the common source of page rows for listing and name lookup.

**Data flow**: It starts with the tool context, gets the extension context, reads the registered sources, and builds lookup tables from source id to backend name and source object name. It then asks for source pages visible to the current `source_reader`, converts each stored record into a `_Page` wrapper, and returns them as a tuple.

**Call relations**: `PageObjects.list` calls this to build list rows, and `_find` calls it when get or delete needs one page. It uses connector configuration validation and `binding_name` to create links back to source objects where possible.

*Call graph*: calls 2 internal fn (source_reader, _require_ext); called by 2 (_find, list); 3 external calls (__init__, model_validate, binding_name).


### `extensions/sources/ufo_ext_sources/tools.py`

`domain_logic` · `object operations and page-change hook handling`

A “source” here is a saved connection to an outside service, such as a CRM or helpdesk, plus the selected streams of content to sync from it. This file turns those saved sync rows into friendly objects that agents and members can list, inspect, apply, resync, or delete. Without it, the system might still have low-level source rows, but users would not have a safe object-level way to register a provider, choose streams, share it with the workspace, or remove it cleanly.

The file also defines “source triggers.” A trigger is like a standing alarm: when a shared source changes, it wakes a conversation. The code makes sure only shared sources can be watched, because private source pages are not visible to other conversations.

A lot of the work is safety checking. Source names are derived from the provider, account, and tenant URL, so the same binding cannot be accidentally registered twice under two names. Stream changes are treated as the full desired set: omitted streams are removed, along with the pages they synced. Backfill windows, meaning “how far back in time the first sync should reach,” can only widen in place; narrowing requires deleting and recreating the source so old pages are not left behind incorrectly.

When page changes arrive, this file groups them by source, filters out anything the watching agent is not allowed to read, writes a small change log when possible, and invokes the right conversation with a human-readable alert.

#### Function details

##### `_Binding.name`  (lines 218–219)

```
def name(self) -> str
```

**Purpose**: Builds the official object name for a source binding. The name comes from the provider, account, and tenant URL, so identity is stable and not chosen freely by the caller.

**Data flow**: It reads the binding’s provider, account, and base URL, passes them to the shared name-building rule, and returns the resulting string. It does not change anything.

**Call relations**: Other source and trigger operations use this name when listing bindings, finding one binding, grouping page changes, and validating that a caller applied the object under the correct name.

*Call graph*: 1 external calls (binding_name).


##### `_Binding.created_at`  (lines 222–223)

```
def created_at(self) -> datetime
```

**Purpose**: Reports when the whole binding effectively began. Because one binding may contain several stream rows, it uses the oldest stream creation time.

**Data flow**: It reads the creation time of every stream in the binding and returns the earliest one. Nothing is written or changed.

**Call relations**: The object detail view uses this value so a caller sees one creation time for the binding instead of separate times for each stream row.


##### `_Binding.updated_at`  (lines 226–227)

```
def updated_at(self) -> datetime
```

**Purpose**: Reports when anything in the binding was most recently updated. For a binding with several streams, the newest stream update is the binding’s update time.

**Data flow**: It reads the update time of every stream and returns the latest one. It has no side effects.

**Call relations**: The source object detail view uses this to show a single last-updated time for the reconstructed source object.


##### `_Binding.links`  (lines 229–242)

```
def links(self) -> tuple[ObjectLink, ...]
```

**Purpose**: Describes what credential or connected account the source uses for access. This gives readers a safe pointer to the authentication object behind the source when that pointer is allowed to be shown.

**Data flow**: It looks at whether the binding uses a direct workspace credential, a connected account, or a shared binding whose connection should not be exposed. It returns object links for visible access relationships, or an empty set when no link should be shown.

**Call relations**: Source object detail calls this when building the object’s visible metadata. It hands off to object-reference helpers and credential/account naming helpers to create links in the same format as the rest of the object system.

*Call graph*: 4 external calls (__init__, __init__, credential_object_name, account_object_name).


##### `_Binding.spec`  (lines 244–252)

```
def spec(self) -> SourceSpec
```

**Purpose**: Turns the internal binding back into the public source specification that users and agents see. This is the readable form of the saved source configuration.

**Data flow**: It reads the binding’s provider, streams, account, base URL, sharing state, and backfill request. It returns a SourceSpec with direct-account details hidden as an empty account ID and the internal subject translated into a shared/private flag.

**Call relations**: Source get, resync checks, and apply comparisons use this public spec to decide whether a requested operation matches what already exists.

*Call graph*: 2 external calls (__init__, subject_shared).


##### `_Binding.summary`  (lines 254–256)

```
def summary(self) -> str
```

**Purpose**: Creates a short human-readable label for a binding. It says which provider and account are involved and names the streams.

**Data flow**: It joins the stream names, combines them with the provider and account, trims the result to the configured maximum length, and returns the text.

**Call relations**: Listing source objects and alert messages use this summary so people can quickly recognize what changed without reading the full specification.

*Call graph*: called by 1 (_alert_message).


##### `_require_ext`  (lines 265–268)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Makes sure an extension context is present before code tries to read or write extension data. The extension context is the system doorway to source rows, files, credentials, and similar services.

**Data flow**: It receives a possible extension context. If it is present, it returns it; if it is missing, it raises an error immediately instead of letting later code fail in a confusing way.

**Call relations**: Most source and trigger operations call this near the start of their work. It acts like a guardrail before they touch source storage, trigger storage, credentials, or sync scheduling.

*Call graph*: called by 10 (_apply_owned, _delete_owned, _grant_settled, _member_rows, _resolved_account, _resync, _widen_window, _member_rows, _binding_named, _require_triggers).


##### `_require_connectors`  (lines 271–274)

```
def _require_connectors(ctx: ToolContext) -> ConnectorRegistry
```

**Purpose**: Makes sure the current tool turn has a connector registry. The registry is the catalog that knows which external providers can be connected and how.

**Data flow**: It reads the connector registry from the tool context. If the registry exists, it returns it; if not, it raises a clear runtime error.

**Call relations**: Account resolution calls this before deciding whether a provider should use a connected account, a broker, or a direct workspace credential.

*Call graph*: called by 1 (_resolved_account).


##### `_bindings_from_ext`  (lines 277–312)

```
async def _bindings_from_ext(ext: ExtensionContext) -> tuple[_Binding, ...]
```

**Purpose**: Rebuilds user-facing source bindings from the lower-level source rows stored by the sync system. One binding may be made of several stream rows.

**Data flow**: It reads all stored source records from the extension context, ignores records for providers this extension does not know, validates each row’s connector config, groups rows by provider, account, and base URL, and returns sorted _Binding objects containing their streams and sharing details.

**Call relations**: Listing sources, finding a source by name, listing triggers with source summaries, and reacting to page changes all start here because they need the higher-level binding view rather than raw rows.

*Call graph*: calls 1 internal fn (sources); called by 4 (_member_rows, _member_rows, _binding_named, on_page_change); 3 external calls (__init__, __init__, model_validate).


##### `_binding_named`  (lines 315–325)

```
async def _binding_named(ext: ExtensionContext | None, name: str) -> _Binding | None
```

**Purpose**: Finds one source binding by its derived object name. This is the common lookup used whenever a source or trigger refers to a binding.

**Data flow**: It requires a valid extension context, rebuilds all bindings from stored source rows, compares each binding’s derived name with the requested name, and returns the match or nothing.

**Call relations**: Source get, status, apply, resync, delete, settled grants, and trigger creation all call this to confirm that the named source really exists and to retrieve its stream rows.

*Call graph*: calls 2 internal fn (_bindings_from_ext, _require_ext); called by 7 (_apply_owned, _delete_owned, _grant_settled, _member_object, _resync, _status, _apply_owned).


##### `_require_triggers`  (lines 328–329)

```
def _require_triggers(ext: ExtensionContext | None) -> SourceTriggerStore
```

**Purpose**: Creates access to the source trigger store after confirming the extension context exists. The trigger store is where standing source-change alarms are saved.

**Data flow**: It takes a possible extension context, checks it with _require_ext, and returns a SourceTriggerStore connected to that context.

**Call relations**: Source deletion, trigger listing, trigger creation, trigger deletion, trigger lookup, and page-change handling use this helper before reading or changing trigger rows.

*Call graph*: calls 1 internal fn (_require_ext); called by 6 (_delete_owned, _apply_owned, _delete_owned, _find, _member_rows, on_page_change); 1 external calls (__init__).


##### `effective_days`  (lines 332–341)

```
def effective_days(request: int | Literal['all'] | None, declared: int | None) -> int | None
```

**Purpose**: Decides how far back a stream should sync when a source is registered or widened. It combines the user’s request with the stream’s own default window.

**Data flow**: It receives the requested backfill setting and the stream’s declared default. A number wins, no request falls back to the declared default, and “all” becomes no cutoff. It returns the number of days to pin, or nothing for all history/no cutoff.

**Call relations**: Source apply uses this when creating new stream rows, and window widening uses it when comparing the old and new backfill reach.

*Call graph*: called by 2 (_apply_owned, _widen_window).


##### `_binding_identity`  (lines 344–354)

```
def _binding_identity(spec: SourceSpec) -> tuple[str, tuple[str, ...], str, str, bool, int | Literal['all'] | None]
```

**Purpose**: Extracts the parts of a source spec that define whether it is the same binding request. This lets the code tell a no-op reapply from a real change.

**Data flow**: It reads provider, sorted streams, account ID, base URL, sharing flag, and backfill setting from the spec and returns them as a comparable tuple.

**Call relations**: The top-level source apply path uses this to skip unnecessary writes for identical specs. The resync path also uses it to make sure a resync request is not trying to edit the source at the same time.

*Call graph*: called by 2 (_resync, apply).


##### `SourceObjects.apply`  (lines 383–404)

```
async def apply(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Receives an apply request for a source object and chooses the right path: resync, harmless reapply, or real mutation. It is the front door for source apply behavior.

**Data flow**: It takes the tool context, object name, requested spec, old visible spec, and expected generation. If resync is requested, it schedules sync work. If the spec is identical to the visible old one, it grants the current agent access without rewriting rows. Otherwise it passes the request to the base object machinery for normal permission checks and mutation.

**Call relations**: The object system calls this when someone applies a source manifest. It hands off to _resync, _grant_settled, or the inherited apply flow, which eventually reaches _apply_owned for the actual source-row changes.

*Call graph*: calls 3 internal fn (_grant_settled, _resync, _binding_identity).


##### `SourceObjects._grant_settled`  (lines 406–427)

```
async def _grant_settled(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: Grants the current agent access to an already-existing source when an identical apply does not create any new stream rows. This prevents a successful-looking no-op from leaving the agent unable to read the feed.

**Data flow**: It reads the speaking member, the source owner, and the named binding. If the speaker is allowed to receive the feed, it grants each stream source ID to the current agent. If not, it quietly does nothing.

**Call relations**: SourceObjects.apply calls this only for identical reapplications. It uses the binding lookup and extension context, then hands each stream to the extension’s grant operation.

*Call graph*: calls 2 internal fn (_binding_named, _require_ext); called by 1 (apply).


##### `SourceObjects._resync`  (lines 429–453)

```
async def _resync(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None) -> None
```

**Purpose**: Schedules an immediate sync of an existing source without changing its configuration. It is for “refresh this now,” not for editing provider, streams, sharing, or backfill settings.

**Data flow**: It receives the requested spec and the old spec. It refuses the request if the source is missing or the spec differs except for the resync flag, checks that the actor owns the source or is an admin, finds the binding, and asks the extension context to schedule all its stream rows for sync.

**Call relations**: SourceObjects.apply calls this when the resync flag is set. It uses permission checks from the context and binding lookup before handing the stream IDs to the sync scheduler.

*Call graph*: calls 4 internal fn (speaker_is_admin, _binding_identity, _binding_named, _require_ext); called by 1 (apply); 3 external calls (__init__, __init__, __init__).


##### `SourceObjects._member_rows`  (lines 455–468)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[ObjectOwner], ...]
```

**Purpose**: Builds the rows shown when source objects are listed. Each row represents one reconstructed binding, not one raw stream row.

**Data flow**: It reads all bindings from the extension context and turns each into an OwnedRow with its name, short summary, owner member, and shared/private flag. It returns the collection to the object listing layer.

**Call relations**: The member-readable object base calls this during source listing. The base then applies visibility rules so members see shared sources and their own private sources, while admins can see more.

*Call graph*: calls 2 internal fn (_bindings_from_ext, _require_ext); 3 external calls (__init__, __init__, subject_shared).


##### `SourceObjects._member_object`  (lines 470–486)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: ObjectOwner, *, member_id: UUID | None) -> ObjectDetail[SourceSpec] | None
```

**Purpose**: Builds the detailed view for one source object. It returns the public spec, timestamps, and allowed access links for the named binding.

**Data flow**: It looks up the binding by name. If found, it converts the binding to a SourceSpec, reads its created and updated times, adds its links, and returns an ObjectDetail. If not found, it returns nothing.

**Call relations**: The object system calls this after it has decided the requester may see the source. It relies on _binding_named and the _Binding helper methods to assemble the detail.

*Call graph*: calls 1 internal fn (_binding_named); 1 external calls (__init__).


##### `SourceObjects._status`  (lines 488–514)

```
async def _status(self, ctx: ToolContext, name: str, _owner: ObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Reports operational status for a source, such as when each stream will sync next and whether it has errors. This is the health view of the binding.

**Data flow**: It finds the binding, then builds a dictionary containing whether it is shared and one status block per stream: next sync time, error count, parked state, parked reason, and backfill cutoff. For private sources it may include the owner member ID.

**Call relations**: The object status flow calls this when someone asks for source status. It reads from the reconstructed binding and returns plain JSON-style data.

*Call graph*: calls 1 internal fn (_binding_named); 1 external calls (subject_shared).


##### `SourceObjects._apply_owned`  (lines 516–620)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: Performs the real work of creating or changing a source after ownership and permission checks have passed. It validates the provider, streams, tenant URL, account, sharing change, backfill window, and then adds, grants, removes, or rewrites stream rows.

**Data flow**: It receives the desired source spec and current owner information. It checks that a speaking member exists, validates the provider and stream names, validates whether backfill_days is meaningful, normalizes the base URL, resolves the account or credential to use, enforces the derived source name, compares with any existing binding, widens windows or shares when allowed, registers new streams, grants existing streams to the agent, and removes dropped streams.

**Call relations**: The inherited apply flow calls this for actual mutations. It coordinates many helpers: account resolution, URL validation, binding lookup, effective-days calculation, and window widening, then uses the extension context to write source rows and grants.

*Call graph*: calls 6 internal fn (_resolved_account, _widen_window, _binding_named, _require_ext, _validated_base_url, effective_days); 7 external calls (__init__, __init__, now, timedelta, binding_name, member_subject, get).


##### `SourceObjects._widen_window`  (lines 622–696)

```
async def _widen_window(self, ctx: ToolContext, binding: _Binding, *, kept: tuple[_Stream, ...], declared: dict[str, int | None], windowed: frozenset[str], account: str, base_url: str | None, request:
```

**Purpose**: Changes a source’s backfill window only when the new request reaches further back in time. This protects the system from leaving already-synced pages outside the new window but still present.

**Data flow**: It receives the current binding, the streams the apply request is keeping, each stream’s declared default window, and the new request. For each kept windowed stream, it reconstructs the original anchor date, calculates the new cutoff, refuses any narrowing, and builds updated connector configs. It then asks the extension context to save the new window and refetch streams whose cutoff moved earlier.

**Call relations**: SourceObjects._apply_owned calls this before writing any stream changes when the backfill request differs. It uses effective_days for the old and new meanings of the request and hands the final configs to the extension context.

*Call graph*: calls 2 internal fn (_require_ext, effective_days); called by 1 (_apply_owned); 3 external calls (__init__, __init__, timedelta).


##### `SourceObjects._delete_owned`  (lines 698–705)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: Deletes a source binding by removing all of its stream rows and clearing triggers that watched it. Removing the stream rows also causes their synced pages to be removed through the wider page-tombstone process.

**Data flow**: It looks up the binding by name, raises an unknown-object error if it is gone, removes each stream source ID through the extension context, and then removes triggers tied to that binding name.

**Call relations**: The object system calls this after delete permission is confirmed. It combines source-row deletion with trigger cleanup so no trigger is left watching a removed source.

*Call graph*: calls 3 internal fn (_binding_named, _require_ext, _require_triggers); 1 external calls (__init__).


##### `SourceObjects._resolved_account`  (lines 707–780)

```
async def _resolved_account(self, ctx: ToolContext, spec: SourceSpec) -> _ResolvedAccount
```

**Purpose**: Decides which account or credential a source will use to authenticate with the outside provider. It hides the complexity of brokered connected accounts versus direct workspace credentials.

**Data flow**: It reads the connector registry, available connector accounts, connection details, declared credentials, and the requested account ID. It returns an account handle and optional connection ID, or raises a clear error telling the user to connect an account, choose among accounts, use the workspace credential, or add a missing credential.

**Call relations**: SourceObjects._apply_owned calls this before registration. The returned account handle is stored in each stream’s connector config, so future sync runs replay the same authentication choice.

*Call graph*: calls 4 internal fn (connector_accounts, connector_connection, _require_connectors, _require_ext); called by 1 (_apply_owned); 1 external calls (__init__).


##### `trigger_name`  (lines 783–787)

```
def trigger_name(binding: str, conversation_id: UUID) -> str
```

**Purpose**: Builds the official object name for a source trigger. A trigger is identified by the source it watches and the conversation it belongs to.

**Data flow**: It receives a binding name and conversation ID, joins them into one deterministic string, and returns it. It does not read or write storage.

**Call relations**: Trigger listing, trigger lookup, and trigger creation all use this same rule so the system refuses incorrectly named trigger applies instead of creating duplicate or ambiguous trigger objects.

*Call graph*: called by 3 (_apply_owned, _find, _member_rows).


##### `SourceTriggerObjects._member_rows`  (lines 818–852)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Builds the rows shown when source triggers are listed. Each row says which source wakes which conversation, how it delivers alerts, and who created it.

**Data flow**: It reads reported trigger rows from the trigger store, rebuilds source bindings for friendly summaries, looks up creator emails, and returns OwnedRow entries with generated ownership information and fields such as conversation, source, delivery, origin, owner email, and whether it belongs to the current member.

**Call relations**: The member-readable object base calls this for trigger listing. It combines trigger-store data with source-binding summaries and owner email lookup to make the list understandable.

*Call graph*: calls 4 internal fn (_bindings_from_ext, _require_ext, _require_triggers, trigger_name); 4 external calls (__init__, __init__, owner_emails, subject_shared).


##### `SourceTriggerObjects._member_object`  (lines 854–889)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[SourceTriggerSpec] | None
```

**Purpose**: Builds the detailed view for one source trigger. It shows the source being watched, the delivery mode, timestamps, and links to related objects.

**Data flow**: It finds the listed trigger by name and checks that its generation still matches the owner record. If valid, it creates links to the watched source and, for current-conversation delivery, to the reporting conversation. It returns an ObjectDetail with a SourceTriggerSpec.

**Call relations**: The object system calls this after visibility checks. It relies on _find for the trigger lookup and object-link helpers to describe relationships.

*Call graph*: calls 1 internal fn (_find); 4 external calls (__init__, __init__, __init__, __init__).


##### `SourceTriggerObjects._status`  (lines 891–905)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Reports current readable status for a trigger, such as its source, conversation, delivery type, origin, creator email, and whether it is mine.

**Data flow**: It finds the trigger by name and generation. If it still matches, it looks up the creator’s email and returns a JSON-style dictionary of status fields; otherwise it returns nothing.

**Call relations**: The object status flow calls this for source_trigger objects. It uses the same _find path as get and delete, keeping status tied to the current trigger row.

*Call graph*: calls 1 internal fn (_find); 1 external calls (owner_emails).


##### `SourceTriggerObjects._apply_owned`  (lines 907–944)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SourceTriggerSpec, old: SourceTriggerSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: Creates a source trigger for the current conversation, or accepts an identical reapply. It refuses attempts to rename or edit an existing trigger because the trigger’s identity is exactly its source plus conversation.

**Data flow**: It computes the expected name from the requested source and current conversation. If the supplied name is wrong, it raises an error. If an identical trigger already exists, it returns without changing anything; if a different one exists under that name, it refuses. For a new trigger, it checks that the source is watchable, writes the trigger row, then rechecks that the source still exists and cleans up if it disappeared mid-operation.

**Call relations**: The object apply flow calls this for source_trigger mutations. It hands validation to _watchable, writes through the trigger store, and uses _binding_named as a final race-condition check.

*Call graph*: calls 4 internal fn (_watchable, _binding_named, _require_triggers, trigger_name); 1 external calls (__init__).


##### `SourceTriggerObjects._watchable`  (lines 946–959)

```
async def _watchable(self, ctx: ToolContext, source: str) -> None
```

**Purpose**: Checks whether the requested source can be watched by a trigger. Only visible shared sources qualify.

**Data flow**: It asks the source object store for the named source using the current context. If the source is not visible, it raises unknown-object. If it is private, it raises a clear error explaining that private changes cannot reach conversations. Shared sources pass without returning data.

**Call relations**: SourceTriggerObjects._apply_owned calls this before creating a trigger. It deliberately relies on the source object’s own visibility rules so guessing a private or hidden source name does not reveal extra information.

*Call graph*: called by 1 (_apply_owned); 1 external calls (__init__).


##### `SourceTriggerObjects._delete_owned`  (lines 961–965)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: Deletes a source trigger after confirming it is still the same trigger the object layer approved for deletion. This avoids deleting a row that changed underneath the caller.

**Data flow**: It finds the trigger by name, compares its stored generation ID with the owner generation, raises an error if they differ or it is missing, and otherwise removes the trigger from the trigger store.

**Call relations**: The object system calls this after delete permission checks. It uses _find for the current row and the trigger store to perform the removal.

*Call graph*: calls 2 internal fn (_find, _require_triggers).


##### `SourceTriggerObjects._find`  (lines 967–975)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> ListedTrigger | None
```

**Purpose**: Finds one reported trigger by its derived object name. It is the shared lookup helper for trigger get, status, and delete.

**Data flow**: It reads all reported triggers from the trigger store, derives each trigger’s object name from its binding and conversation, and returns the matching row or nothing.

**Call relations**: Trigger detail, status, and deletion all call this so they agree on how trigger names map to stored trigger rows.

*Call graph*: calls 2 internal fn (_require_triggers, trigger_name); called by 3 (_delete_owned, _member_object, _status).


##### `on_page_change`  (lines 978–1019)

```
async def on_page_change(ctx: HookContext) -> HookOutcome
```

**Purpose**: Responds when synced pages change and wakes any source triggers that should hear about those changes. It is the bridge between the sync engine and conversations.

**Data flow**: It receives a hook payload, confirms it is a page-change batch, maps changed source IDs back to bindings, groups changes by binding, reads triggers waiting on each binding, filters to shared pages, checks what each trigger’s agent is allowed to read, and fires the trigger for authorized changes. It returns no special outcome.

**Call relations**: The manifest hook system calls this on page_change events. It uses binding reconstruction, trigger lookup, source-read authorization, and then hands each permitted batch to _fire_trigger while ignoring archived agents.

*Call graph*: calls 3 internal fn (_bindings_from_ext, _fire_trigger, _require_triggers); 2 external calls (__init__, suppress).


##### `_fire_trigger`  (lines 1022–1057)

```
async def _fire_trigger(ext: ExtensionContext, binding: _Binding, trigger: SourceTrigger, authorized: list[PageChange]) -> None
```

**Purpose**: Delivers authorized page changes to one trigger. Depending on the trigger’s delivery mode, it either wakes the existing conversation once or opens one stable conversation per changed page.

**Data flow**: It receives the extension context, binding, trigger, and authorized page changes. For current delivery, it writes one change log, builds one alert message, and invokes the trigger’s conversation. For per-page delivery, it opens or reuses a page-specific conversation for each change, writes a one-page log, and invokes that conversation with an idempotency key.

**Call relations**: on_page_change calls this after filtering changes for visibility. It delegates change-log writing to _write_change_log and alert text construction to _alert_message, then uses the extension context to invoke agents.

*Call graph*: calls 4 internal fn (invoke, open_conversation, _alert_message, _write_change_log); called by 1 (on_page_change).


##### `_write_change_log`  (lines 1060–1095)

```
async def _write_change_log(ext: ExtensionContext, conversation_id: UUID, binding: _Binding, latest: str, changes: list[PageChange]) -> str | None
```

**Purpose**: Writes the changed-page list into the conversation’s runtime files as JSON Lines, which means one small JSON record per line. This keeps large change details out of the alert message while still making them available to the agent.

**Data flow**: It receives the conversation, binding, latest batch marker, and changes. If file storage is unavailable, it returns nothing. Otherwise it writes one line per change with page reference, stream, title, disposition, and timestamp, prunes older runtime files in that source directory, and returns the written file path.

**Call relations**: _fire_trigger calls this before invoking the agent. The alert message can then point to the file path when there are too many changed pages to name inline.

*Call graph*: calls 1 internal fn (_disposition); called by 1 (_fire_trigger); 1 external calls (dumps).


##### `_disposition`  (lines 1098–1104)

```
def _disposition(change: PageChange) -> str
```

**Purpose**: Labels a page change as added, updated, or removed. This gives alerts and logs simple words instead of raw timestamp and tombstone details.

**Data flow**: It reads one PageChange. If it is a tombstone, it returns removed. Otherwise, if the page was created in this change, it returns added; if not, it returns updated.

**Call relations**: The change-log writer and stream-count summary both call this so logs and alert summaries use the same labels.

*Call graph*: called by 2 (_stream_counts, _write_change_log).


##### `_stream_counts`  (lines 1107–1121)

```
def _stream_counts(changes: list[PageChange]) -> str
```

**Purpose**: Builds a compact summary of how many pages changed in each stream. For example, it can say that one stream had three additions and another had two removals.

**Data flow**: It receives a list of page changes, groups them by stream, counts each change disposition, and returns a readable string with the nonzero counts in a stable order.

**Call relations**: _alert_message calls this to include a short overview of the batch before explaining where to read the details.

*Call graph*: calls 1 internal fn (_disposition); called by 1 (_alert_message); 1 external calls (defaultdict).


##### `_alert_message`  (lines 1124–1143)

```
def _alert_message(binding: _Binding, changes: list[PageChange], log_path: str | None) -> str
```

**Purpose**: Creates the message sent to an agent when a watched source changes. It balances being helpful with not stuffing too much data into the conversation.

**Data flow**: It receives the binding, changed pages, and optional change-log path. It always includes the source name, summary, and stream counts. If there are only a few changes, it names the pages directly; if there are many and a log exists, it points to the log; otherwise it tells the agent how to list pages.

**Call relations**: _fire_trigger calls this immediately before invoking a conversation. It uses binding summaries, page references, and stream counts to make the wake-up actionable.

*Call graph*: calls 3 internal fn (summary, _page_reference, _stream_counts); called by 1 (_fire_trigger).


##### `_page_reference`  (lines 1146–1150)

```
def _page_reference(change: PageChange) -> str
```

**Purpose**: Formats one changed page as an object reference with a readable title snippet. This helps the alerted agent know exactly what to fetch.

**Data flow**: It reads the page ID and title from a change. It trims the title to a safe length, substitutes a fallback for untitled pages, and returns a string like a page object reference plus label.

**Call relations**: _alert_message uses this when the batch is small enough to list individual changed pages inside the alert.

*Call graph*: called by 1 (_alert_message).


##### `_validated_base_url`  (lines 1153–1193)

```
def _validated_base_url(provider: str, base_url: str | None) -> str | None
```

**Purpose**: Validates and normalizes a provider’s tenant API URL. This prevents unsafe or wrongly shaped URLs from being stored in source configs.

**Data flow**: It reads the provider’s connector definition and the submitted base URL. Providers with a fixed API host must not receive an override. Providers that need tenant-specific URLs must match a strict HTTPS host and path rule, with no username, password, port, query, or fragment. It returns a normalized URL or nothing for fixed-host providers.

**Call relations**: SourceObjects._apply_owned calls this before resolving the account or registering rows. Its errors tell the caller the expected URL shape, which keeps source registration safe and discoverable.

*Call graph*: called by 1 (_apply_owned); 1 external calls (urlsplit).


### Portal and agent objects
The web panel bridge routes portal mutations into the conversation-based write path, while agent objects define the editable settings being changed.

### `extensions/web/ufo_ext_web/panels.py`

`orchestration` · `request handling`

The portal has panels where a member can press buttons or submit forms, such as saving an agent setting, deleting a connection, or starting an account connection flow. This file defines what those submissions are allowed to look like, turns them into typed tool calls, sends them into the member’s dedicated “Portal actions” conversation, and waits for the final result so the web page can show a clear answer.

The important design choice is that portal writes are treated like conversation turns. That means they are ordered, audited, and run through the same permission checks as other agent actions. Without this file, the web UI would either need many separate mutation endpoints, or it could accidentally bypass the normal turn log and safety rules.

The file also contains the data used by the first-run connection catalog and “unlock” suggestions, which tell a user what useful app behaviors become possible after connecting certain providers. Finally, it exposes the agent settings projection: a read-only snapshot of the selected agent’s current configuration, available models, schema for editable settings, and admin-only audience information.

#### Function details

##### `ApplyIntent.kinds`  (lines 97–101)

```
def kinds(cls) -> frozenset[str]
```

**Purpose**: Returns the complete set of object types that a portal panel is allowed to submit changes for. This keeps the UI and the validation rule tied to the same closed list instead of maintaining two separate lists.

**Data flow**: It reads the type annotation on the ApplyIntent.kind field, extracts the literal allowed values from that annotation, and returns them as an immutable set. Nothing outside the class is changed.

**Call relations**: This is the source list used by nearby helper methods that decide which object kinds can be applied or deleted. It relies on Python’s type information rather than a hand-written duplicate list.

*Call graph*: 1 external calls (get_args).


##### `ApplyIntent.applying_kinds`  (lines 104–106)

```
def applying_kinds(cls) -> frozenset[str]
```

**Purpose**: Returns the object types that may be created or updated from a panel using the apply verb. It excludes kinds that are intentionally delete-only or connect-only.

**Data flow**: It starts with all allowed kinds from ApplyIntent.kinds, removes credential and source_trigger because panels may only delete those, and removes connection because connections are created through provider connect flows. The result is an immutable set for the caller to use.

**Call relations**: The web surface code calls this when building kind-specific payloads, so it can show create or edit controls only where this intent lane will actually accept them.

*Call graph*: called by 1 (_kind_payload).


##### `ApplyIntent.deleting_kinds`  (lines 109–115)

```
def deleting_kinds(cls) -> frozenset[str]
```

**Purpose**: Returns the object types that may be deleted from a panel. In this contract, every named kind can be deleted, including connections, where delete means disconnect.

**Data flow**: It reads the same allowed kind list used by validation and returns it unchanged. It does not inspect any database state or user permission by itself.

**Call relations**: The web surface code calls this when deciding whether to draw delete controls for object pages. The actual permission and existence checks still happen later in the tool/action path.

*Call graph*: called by 1 (_kind_payload).


##### `ApplyIntent._verb_pairs_with_its_kind`  (lines 118–139)

```
def _verb_pairs_with_its_kind(self) -> 'ApplyIntent'
```

**Purpose**: Checks that a submitted panel intent uses a verb that makes sense for its object kind. For example, a connection can be connected or deleted, but not edited like a normal settings object.

**Data flow**: It receives the already-parsed ApplyIntent object, looks at its verb, kind, spec, and create_only flag, and either returns the same object as valid or raises a validation error explaining the mismatch. It does not perform the change itself.

**Call relations**: Pydantic, the data validation library, runs this automatically when a PanelIntent is parsed in submit_intent. If this check fails, the web request is rejected before any conversation turn is created.


##### `Unlock._names_offered_tiles_and_a_drawn_mark`  (lines 328–337)

```
def _names_offered_tiles_and_a_drawn_mark(self) -> 'Unlock'
```

**Purpose**: Checks that an unlock suggestion can actually be shown in the first-run UI. It makes sure the icon exists and every required provider name matches one of the provider tiles the portal knows how to draw.

**Data flow**: It receives an Unlock object, compares its mark against the known agent icons, then checks each provider requirement group against the offered provider catalog. It returns the same object if all names are valid, or raises a validation error if the UI would not be able to display it correctly.

**Call relations**: This runs automatically when the module creates the Unlock and AppUnlock constants. It catches catalog mistakes at import time instead of letting a broken suggestion appear later in the portal.


##### `Unlock.missing`  (lines 339–343)

```
def missing(self, held: frozenset[str]) -> tuple[str, ...]
```

**Purpose**: Tells the portal which provider accounts a member still needs to connect before an unlock suggestion is ready. It treats each requirement group as “any one of these providers is enough.”

**Data flow**: It takes the set of provider names the member already has, checks each requirement group, and returns the first preferred provider from any group that is not yet satisfied. The result is an ordered tuple of missing provider names.

**Call relations**: This helper is used by UI logic that presents unlocks as either ready-to-build applications or recommendations that need one or more account connections first.


##### `_action_intent`  (lines 516–527)

```
def _action_intent(kind: str, name: str | None, action: str, body: dict[str, JsonValue]) -> ToolIntent
```

**Purpose**: Builds the standard tool-call object for a portal action that was already presented by the system. It locks the target kind, optional object name, and action name into the intent so the browser body cannot redirect the action elsewhere.

**Data flow**: It receives the target kind, optional target name, action name, and body data. It places the target fields and the body under a single object_action input, then returns a ToolIntent ready to be admitted as a conversation turn.

**Call relations**: submit_action calls this after it has checked the request body and confirmed the action is available. The returned ToolIntent is then passed to SurfaceContext.admit so the engine can run the action.

*Call graph*: called by 1 (submit_action); 1 external calls (__init__).


##### `_tool_intent`  (lines 530–565)

```
def _tool_intent(submitted: ApplyIntent) -> ToolIntent
```

**Purpose**: Converts a validated ApplyIntent from a panel form into the exact tool call the agent system should run. It covers connect, delete/detach, and apply-style updates.

**Data flow**: It receives an ApplyIntent. Connect becomes a connect_account tool call, delete and detach become object_delete calls, and apply-style changes become an object_apply call with a YAML manifest describing the object spec. It returns a ToolIntent and does not execute it.

**Call relations**: submit_intent calls this after request validation and any extra checks. The result is the prepared intent that gets written into the portal action conversation and dispatched by the engine.

*Call graph*: called by 1 (submit_intent); 2 external calls (__init__, safe_dump).


##### `_outcome`  (lines 568–584)

```
def _outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Turns the final frame from an intent run into a simple JSON response for the web page. It gives the user either a success message, credential follow-up information, or a readable refusal/error.

**Data flow**: It receives a terminal frame and the turn ID. If the frame says the run finished successfully, it returns applied=true and includes credentials when the tool requested them; otherwise it strips a leading technical error class from the message and returns applied=false. The output is an HTTP JSON response.

**Call relations**: submit_intent uses this for ordinary panel intents. The specialized outcome readers for Slack, GitHub, iMessage, billing, and rebuild actions fall back to it whenever their action did not finish normally.

*Call graph*: called by 7 (_action_outcome, _github_outcome, _imessage_outcome, _portal_outcome, _rebuild_outcome, _slack_outcome, submit_intent); 1 external calls (JSONResponse).


##### `_slack_outcome`  (lines 587–602)

```
def _slack_outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Reads the special result of the Slack connection action and returns the Slack install URL when one was created. If Slack refused or no install is needed, it returns the tool’s own explanation.

**Data flow**: It receives a terminal frame and turn ID. If the frame is not successful, it delegates to _outcome; otherwise it extracts a JSON object from the tool’s text, reads the authorize_url and hint fields, and returns a JSON response with applied=true, a possible URL, and the turn ID.

**Call relations**: This function is registered in ACTION_OUTCOMES for the Slack connect action. When submit_action finishes a matching action, _action_outcome chooses this reader so the portal gets a usable install link instead of raw tool text.

*Call graph*: calls 1 internal fn (_outcome); 2 external calls (loads, JSONResponse).


##### `_github_outcome`  (lines 605–613)

```
def _github_outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Reads the special result of the GitHub connection action and pulls out the install link if the tool produced one. If there is no link, the tool’s sentence becomes the message shown to the user.

**Data flow**: It receives a terminal frame and turn ID. On failure it delegates to _outcome; on success it searches the frame text for a URL and returns a JSON response containing that URL when found, otherwise the original text as the message.

**Call relations**: This function is registered for the GitHub connect action. _action_outcome uses it after submit_action has admitted the action and received the terminal frame.

*Call graph*: calls 1 internal fn (_outcome); 1 external calls (JSONResponse).


##### `_imessage_outcome`  (lines 616–637)

```
def _imessage_outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Reads the special result of the iMessage connection action and reports whether the connection is pending or complete, along with user instructions and an optional opt-in link.

**Data flow**: It receives a terminal frame and turn ID. If the action failed, it delegates to _outcome; otherwise it extracts a JSON object from the frame text, validates the connection state, instruction, and optional link, then returns a JSON response for the portal.

**Call relations**: This function is registered for the iMessage connect action. _action_outcome chooses it for that action so the web page can show connection instructions rather than raw JSON-like text.

*Call graph*: calls 1 internal fn (_outcome); 2 external calls (loads, JSONResponse).


##### `_portal_outcome`  (lines 640–654)

```
def _portal_outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Reads the special result of the billing portal action and returns the provider’s billing portal URL. This is used when an admin asks to manage billing.

**Data flow**: It receives a terminal frame and turn ID. If the frame is not successful, it delegates to _outcome; otherwise it extracts a JSON object from the tool text, reads portal_url, verifies it is a string, and returns it in a JSON response.

**Call relations**: _action_outcome calls this only for the workspace manage_billing action when the posted operation asks for the billing portal. Other billing operations use the normal _outcome path.

*Call graph*: calls 1 internal fn (_outcome); called by 1 (_action_outcome); 2 external calls (loads, JSONResponse).


##### `_rebuild_outcome`  (lines 657–664)

```
def _rebuild_outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Returns the tool’s own message after a rebuild action, because pressing rebuild usually queues background work rather than changing the page immediately. The message tells the user what was queued or skipped.

**Data flow**: It receives a terminal frame and turn ID. If the run failed, it delegates to _outcome; if it succeeded, it returns applied=true with the frame text as the message. The response includes the turn ID.

**Call relations**: This function is registered for report digest and page facts rebuild actions. _action_outcome uses it so those actions can explain the queued work in their own words.

*Call graph*: calls 1 internal fn (_outcome); 1 external calls (JSONResponse).


##### `_action_outcome`  (lines 679–686)

```
def _action_outcome(kind: str, action: str, body: dict[str, JsonValue], frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Chooses the right response reader for a completed portal action. Most actions use the standard success/error format, but a few actions need to extract links or detailed queue messages.

**Data flow**: It receives the action target kind, action name, posted body, terminal frame, and turn ID. For the billing portal operation it routes to _portal_outcome; otherwise it looks up a specialized reader in ACTION_OUTCOMES and falls back to _outcome. It returns the chosen HTTP response.

**Call relations**: submit_action calls this after the action turn reaches a terminal frame. It is the small dispatcher that connects action identity to the proper user-facing result format.

*Call graph*: calls 2 internal fn (_outcome, _portal_outcome); called by 1 (submit_action).


##### `submit_intent`  (lines 717–842)

```
async def submit_intent(ctx: SurfaceContext, request: Request, agent_id: UUID, member_id: UUID, email: str) -> Response
```

**Purpose**: Accepts one prepared panel form submission, validates it, turns it into a tool intent, admits it into the member’s portal action conversation, and waits for the final result. This is the main write path for ordinary panel changes.

**Data flow**: It reads the HTTP request body, rejects oversized or malformed JSON, validates the submitted ApplyIntent, applies extra checks for frame access, agent specs, model names, sandbox sizes, and credential slot names, then converts the submission with _tool_intent. It opens or finds the member’s portal intent conversation, gives it a friendly title, admits the intent as a turn, watches the turn stream until it finishes, parks, or times out, and returns a JSON response to the browser.

**Call relations**: Web routes call this when a panel submits a normal intent. It calls SurfaceContext methods to read current agent details, find conversations, admit turns, and follow turn output; it calls _tool_intent to build the tool call and _outcome to translate the terminal frame back into a web response.

*Call graph*: calls 9 internal fn (admit, agent_detail, conversation_for, frame_admits, list_credential_slots, retitle_conversation, tail, _outcome, _tool_intent); 5 external calls (timeout, loads, conversation_audience, JSONResponse, body).


##### `submit_action`  (lines 845–934)

```
async def submit_action(ctx: SurfaceContext, request: Request, agent_id: UUID, member_id: UUID, email: str, *, kind: str, name: str | None, action: str) -> Response
```

**Purpose**: Accepts a posted portal action, verifies that the action really exists for the target, runs it through the normal conversation turn system, and returns the action’s final result. It is used for named actions presented by object pages rather than generic form saves.

**Data flow**: It reads and size-checks the request body, parses it as a dictionary, rejects any body fields that try to name the target envelope, confirms the requested action is present for the target kind and object, and checks whether framed app pages are allowed to call it. It then builds an object_action intent, admits it into the portal action conversation, waits for terminal/parked/timeout status, and returns a JSON response.

**Call relations**: Web routes call this for presented actions such as connect flows, rebuild buttons, or billing actions. It uses _action_intent to build the intent and _action_outcome to choose the correct final response format.

*Call graph*: calls 8 internal fn (admit, conversation_for, frame_admits, object_actions, retitle_conversation, tail, _action_intent, _action_outcome); 5 external calls (timeout, loads, conversation_audience, JSONResponse, body).


##### `_update_schema`  (lines 937–949)

```
def _update_schema(sandbox_sizes: tuple[str, ...]) -> dict[str, JsonValue]
```

**Purpose**: Builds the JSON schema used by the settings page to render editable agent settings. It removes fields that the page shows with custom controls or that the current deployment does not support.

**Data flow**: It asks AgentSpec for its full schema, removes prompt, icon, purpose, input/output schema fields, and sandbox_size when sandbox sizes are unavailable, then returns the trimmed schema dictionary. It does not read any specific agent instance.

**Call relations**: agent_settings calls this while building the settings response. The front end can then render regular settings from the schema while separately handling custom fields like the prompt editor and icon grid.

*Call graph*: called by 1 (agent_settings); 1 external calls (model_json_schema).


##### `agent_settings`  (lines 952–998)

```
async def agent_settings(ctx: SurfaceContext, agent_id: UUID, member_id: UUID, *, admin: bool, archivable: bool) -> Response
```

**Purpose**: Returns the web portal’s settings view for one agent: current configuration, prompt details, available models, editable schema, deployment capabilities, and optional admin audience information.

**Data flow**: It asks SurfaceContext for the agent details visible to the member. If there is no such agent, it returns a 404 response; otherwise it optionally loads granted audience emails for admins, builds a current AgentSpec snapshot, trims fields depending on deployment support, and returns everything as JSON.

**Call relations**: The settings page calls this to populate itself before the user edits anything. It depends on SurfaceContext for agent state, _update_schema for the form schema, and the web audience helpers for admin-only sharing information.

*Call graph*: calls 2 internal fn (agent_detail, _update_schema); 5 external calls (__init__, JSONResponse, Response, granted_emails, web_extension).


### `core/src/ufo/kinds/agents.py`

`domain_logic` · `request handling`

This file is the rulebook for the workspace's “agent” objects. An agent is the app-like assistant a member can talk to or run: it has a system prompt, a model choice, safety and sandbox settings, portal visibility, and optional JSON-shaped input and output rules. Without this file, the system would not know who is allowed to create or change agents, how to show them in object listings, or how to safely retire one without losing its history.

The main idea is that agents are durable records. Creating an agent writes a new database row owned by the member who created it. Updating an agent changes only the settings that are explicitly supplied, so leaving out the prompt or icon means “keep the current one.” The main workspace agent is special: every member can reach it, its visibility must stay workspace-wide, and it cannot be archived.

Deleting an agent does not erase it. Instead, the file “archives” it: the agent stops accepting new turns, disappears from normal portal listings, and gives up its old name so another agent can use it. The old row remains under a stable archived name, like putting a folder into long-term storage with its label changed. That keeps conversations, grants, spending records, scheduled tasks, and connected accounts attached to what the agent actually did. A separate restore action can bring the same row back under an available name.

#### Function details

##### `_effective_model`  (lines 71–76)

```
def _effective_model(ctx: ToolContext, stored: str) -> str
```

**Purpose**: Reports the concrete model an agent is actually using when the stored value says “auto.” This matters because readers need to see the real current model, while the saved setting must still remain “auto” so it can follow future deployment defaults.

**Data flow**: It receives the current tool context and the model value stored on the agent row. If the stored value is the special auto marker, it reads the already-resolved model from the current agent in the context; otherwise it returns the stored model unchanged.

**Call relations**: Agent summaries and status reports call this when they need to show a human-readable model. It feeds those read paths without changing the saved agent configuration.

*Call graph*: called by 2 (_status, _agent_summary).


##### `AgentSpec._declared_schema`  (lines 167–172)

```
def _declared_schema(cls, value: dict[str, JsonValue] | None, info: ValidationInfo) -> dict[str, JsonValue] | None
```

**Purpose**: Checks that a custom input or output schema for spawned agents is acceptable. A schema here means a JSON description of what shape data must have, and the system requires it to describe an object at the top level.

**Data flow**: It receives a proposed schema value during AgentSpec validation. If there is no schema, it leaves it alone; if there is one, it passes it to the shared contract checker and returns it only after that checker accepts it.

**Call relations**: This runs automatically when an AgentSpec is built. It hands schema validation to ufo.turns.contracts.check_declared_schema so bad spawn contracts are rejected before they are stored on an agent.

*Call graph*: 1 external calls (check_declared_schema).


##### `_known_model`  (lines 175–192)

```
def _known_model(ctx: ToolContext, model: str, reasoning: ReasoningEffort) -> None
```

**Purpose**: Refuses agent settings that would save an unknown or unusable model choice. This prevents an agent from being written into a state where all later turns fail before anyone can repair it.

**Data flow**: It takes the tool context, a requested model name, and the requested reasoning setting. It resolves “auto” to the deployment's current auto model, checks that the named model exists if a model registry is present, and checks whether the chosen model allows reasoning to be turned off. It returns nothing when the settings are valid, or raises an error when they are not.

**Call relations**: Agent creation and mutation call this just before writing model settings. It acts as a gatekeeper in the write path, catching mistakes while the member is still making the change.

*Call graph*: called by 2 (_create, _mutate).


##### `_agent_summary`  (lines 195–200)

```
def _agent_summary(ctx: ToolContext, row: sa.Row) -> str
```

**Purpose**: Builds the short one-line description shown for an agent in listings. It tells the reader whether the agent is archived or live, what model it uses, and whether public internet is allowed.

**Data flow**: It receives the current context and a database row. It first converts the stored model into the effective readable model, then formats either an archived message with the archive date or a live message with main-agent status and internet policy.

**Call relations**: AgentObjects._owned_rows calls this while assembling the rows used by list and ownership checks. It relies on _effective_model so listings show the real model for auto-configured agents.

*Call graph*: calls 1 internal fn (_effective_model); called by 1 (_owned_rows).


##### `AgentObjects._admin_can_apply`  (lines 215–216)

```
def _admin_can_apply(self, old: AgentSpec, spec: AgentSpec) -> bool
```

**Purpose**: States that a workspace admin may apply any agent change once the normal admin permission gate has been passed. It does not add extra limits for admins inside this file.

**Data flow**: It receives the old and new agent specs and always answers true. Nothing is changed directly.

**Call relations**: This method is part of the shared member-owned object framework. The broader object system consults it when deciding whether an admin is allowed to apply an update.


##### `AgentObjects.list`  (lines 218–224)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists agents, defaulting to live agents unless the caller explicitly asks about archived ones. This keeps archived apps out of normal views while still making them discoverable with a filter.

**Data flow**: It receives a tool context and a listing query. If the query does not mention the archived filter, it copies the query and adds archived=false, then passes the query to the shared listing behavior and returns the resulting page.

**Call relations**: This is the agent-specific front door for object listing. It adjusts the query first, then hands off to the base MemberOwnedObjects listing machinery.

*Call graph*: 1 external calls (replace).


##### `AgentObjects.get`  (lines 226–243)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[AgentSpec] | None
```

**Purpose**: Reads one agent by name, with a shortcut: an empty name means “this turn's own agent.” That shortcut lets actions bound to the current agent work even when the model was never told the agent's actual name.

**Data flow**: It receives a context and a name. If a name is provided, it delegates to the normal get behavior. If the name is empty, it looks through owned rows for the row whose id matches the current turn's agent id, then gets that real agent and returns its detail with the real name filled in.

**Call relations**: This method sits on the object-get path. For the empty-name case it calls AgentObjects._owned_rows to find the current agent, then uses the base get behavior to fetch the full detail.

*Call graph*: calls 1 internal fn (_owned_rows); 1 external calls (replace).


##### `AgentObjects._owned_rows`  (lines 245–277)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[ObjectOwner], ...]
```

**Purpose**: Builds the internal list of all agent rows in the current workspace, including ownership and sharing information. The object framework uses this to decide what a member can see or change.

**Data flow**: It reads the current workspace id, queries the agent table for agent identity, name, archive state, model, owner, and visibility, then turns each database row into an OwnedRow. Each row includes a short summary, owner member id, whether it is shared, and extra fields like id and archived name.

**Call relations**: AgentObjects.get calls this when resolving the empty-name shortcut. The broader inherited object behavior also relies on this style of owned-row data for listing and permission checks, and this method uses _agent_summary to make each row readable.

*Call graph*: calls 1 internal fn (_agent_summary); called by 1 (get); 5 external calls (__init__, __init__, select, workspace_tx, ws_current).


##### `AgentObjects._detail`  (lines 279–312)

```
async def _detail(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> ObjectDetail[AgentSpec] | None
```

**Purpose**: Returns the full editable description of one agent. This is what a caller needs when they want to inspect the agent's saved settings, not just see it in a list.

**Data flow**: It receives a context, an agent name, and owner information. It loads the database row; if none exists, it returns nothing. Otherwise it copies row fields into an AgentSpec, adds creation and update times, and, for non-main agents, includes a link showing which main agent they are scoped under.

**Call relations**: This is called by the object framework when an agent detail is requested. It depends on AgentObjects._row for the database lookup and then packages the result into ObjectDetail for the rest of the object system.

*Call graph*: calls 1 internal fn (_row); 4 external calls (__init__, __init__, __init__, __init__).


##### `AgentObjects._status`  (lines 314–333)

```
async def _status(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns machine-readable status facts about an agent, such as whether it is main, archived, ownerless, or provisioned. This is separate from the editable spec and is useful for status displays or tools.

**Data flow**: It receives the context, agent name, and owner information. It loads the row; if no row exists, it returns nothing. Otherwise it returns a dictionary with status fields, converting dates and ids to plain string-like values and using the effective model for display.

**Call relations**: The object framework calls this when status is requested for an agent. It uses AgentObjects._row for the data and _effective_model so an auto model is reported as the real current model.

*Call graph*: calls 2 internal fn (_row, _effective_model).


##### `AgentObjects._apply_owned`  (lines 335–346)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: AgentSpec, old: AgentSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: Chooses whether applying a spec means creating a new agent or updating an existing one. It is the fork in the write path after ownership checks have already happened.

**Data flow**: It receives the target name, proposed spec, any old spec, and owner information. If there is no old spec, it treats the apply as creation; otherwise it treats it as mutation. It returns nothing after the write path completes.

**Call relations**: The shared object-apply machinery calls this once it has decided the caller is allowed to write. This method then hands off to AgentObjects._create or AgentObjects._mutate.

*Call graph*: calls 2 internal fn (_create, _mutate).


##### `AgentObjects._mutate`  (lines 348–426)

```
async def _mutate(self, ctx: ToolContext, name: str, spec: AgentSpec) -> None
```

**Purpose**: Updates an existing agent's settings while preserving fields the caller intentionally omitted. It also protects important rules, such as “the main agent must stay workspace-visible” and “an agent prompt cannot be empty.”

**Data flow**: It receives the context, agent name, and new spec. It loads the current row, validates the requested model and reasoning, computes the next values by mixing supplied fields with existing fields, checks whether anything actually changed, and writes the new values to the database when needed. If the agent does not exist or the requested settings are invalid, it raises an error instead.

**Call relations**: AgentObjects._apply_owned calls this for existing agents. It uses AgentObjects._row to read current settings, _known_model to validate model choices, and then writes through a workspace transaction.

*Call graph*: calls 2 internal fn (_row, _known_model); called by 1 (_apply_owned); 4 external calls (__init__, update, workspace_tx, ws_current).


##### `AgentObjects._create`  (lines 428–474)

```
async def _create(self, ctx: ToolContext, name: str, spec: AgentSpec) -> None
```

**Purpose**: Creates a new non-main agent owned by the speaking member. It stores only the submitted configuration and deliberately does not copy grants, credentials, sources, memory, or other derived data from anywhere else.

**Data flow**: It receives the context, requested name, and full agent spec. It checks that there is a speaking member, that a non-empty prompt was provided, and that the model settings are valid. It reads existing icons so it can choose an automatic icon if needed, then inserts a new agent row with a fresh id and the speaker as owner. If the name is already taken, it reports that as a clear error.

**Call relations**: AgentObjects._apply_owned calls this when applying a spec to a name that has no current agent. It relies on _known_model for safety, auto_agent_icon for a default portal icon, and the database's unique-name rule to settle race conditions.

*Call graph*: calls 1 internal fn (_known_model); called by 1 (_apply_owned); 6 external calls (insert, select, workspace_tx, auto_agent_icon, ws_current, uuid4).


##### `AgentObjects.delete`  (lines 476–486)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Starts deletion of an agent, but first blocks deletion of the main agent. In this system, deleting an ordinary agent means archiving it, not erasing it.

**Data flow**: It receives the context, agent name, and an optional expected generation id used by the object system for safe writes. It loads the row; if it is the main agent, it raises a not-supported error. Otherwise it delegates to the inherited delete flow, which will enforce ownership and call the owned delete step.

**Call relations**: This is the public delete entry for agent objects. It checks the agent-specific main-agent rule with AgentObjects._row, then hands off to the base object deletion machinery.

*Call graph*: calls 1 internal fn (_row); 1 external calls (__init__).


##### `AgentObjects._delete_owned`  (lines 488–518)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: Archives an agent after the caller has passed the ownership or admin gate. Archiving stops new turns, removes the app from normal live use, and frees its old name while preserving its record.

**Data flow**: It receives the context, agent name, and owner information. It loads the row, rejects missing agents, the main agent, and already archived agents, then updates the row: the live name becomes a durable archived name, the previous name is stored separately, and archive/update timestamps are set.

**Call relations**: The inherited delete flow calls this after permission checks. It uses AgentObjects._row to inspect the target and then writes the archive change inside a workspace transaction.

*Call graph*: calls 1 internal fn (_row); 5 external calls (__init__, __init__, update, workspace_tx, ws_current).


##### `AgentObjects._row`  (lines 520–561)

```
async def _row(self, name: str) -> sa.Row | None
```

**Purpose**: Fetches the complete database row for one agent name in the current workspace. Many other methods use it as their single source of truth before reading, editing, deleting, or reporting status.

**Data flow**: It receives an agent name. It queries the agent table for that name within the current workspace and also includes the workspace's main agent name as an extra field. It returns one row if found, or nothing if no matching agent exists.

**Call relations**: This helper is used by detail, status, mutation, deletion, and archive logic. By centralizing the lookup, the rest of the file can work from the same complete view of an agent.

*Call graph*: called by 5 (_delete_owned, _detail, _mutate, _status, delete); 3 external calls (select, workspace_tx, ws_current).


##### `RestoreApplication.restore`  (lines 576–633)

```
async def restore(self, ctx: ToolContext, args: RestoreApplicationInput) -> ToolResult
```

**Purpose**: Brings an archived agent back to life under a requested name. It restores the same underlying record, so the agent's old conversations, tasks, connected accounts, and grants remain attached.

**Data flow**: It receives the tool context and a new-name argument. It confirms the action is bound to an archived agent target, requires a speaking member, validates the new name, extracts the archived agent id from the archived name, and reads the row. It then checks that the speaker is the owner or a workspace admin. If the row is already live with that name, it returns a success message; otherwise it clears the archive fields and writes the new live name. If the new name is taken or the archived row cannot be found, it raises an error.

**Call relations**: This is the handler behind the restore_application tool registered at the bottom of the file. It uses ToolContext.speaker_is_admin for the admin check, database select/update calls for the restore, and returns a ToolResult message for the user-facing action.

*Call graph*: calls 1 internal fn (speaker_is_admin); 10 external calls (__init__, __init__, __init__, __init__, select, update, workspace_tx, validate_object_name, ws_current, UUID).


### Object infrastructure and controls
The central object system validates and dispatches object operations, with supporting controls for prompt governance, pagination, action scope, and action views.

### `core/src/ufo/objects.py`

`domain_logic` · `startup validation and request handling`

A workspace object is like a labeled card in a shared filing cabinet: it has a kind, a name, and a structured spec. Extensions can add new kinds of cards, but this file makes sure every card follows the same rules before it reaches extension code. Without this layer, different extensions could disagree about names, leak secret fields into chat transcripts, expose private member data, or overwrite each other’s changes without warning.

The file has three main jobs. First, it defines the common data shapes: object rows for lists, full object details, links between objects, ownership records, and store protocols that each object kind must implement. Second, it provides shared behavior for common patterns, especially member-owned objects where a row may be private, shared, or admin-only. Third, it exposes the tool-facing verbs: list, get, explain, apply, delete, and object action dispatch support.

Before serving, registries validate every kind and action. They reject duplicate names, unsafe schemas, unknown target kinds, and reserved input fields. During a request, ObjectVerbs resolves the requested kind, switches to the owning extension context, optionally checks cross-agent access, validates input, records mutations in an object-change journal, and then calls the kind’s store. The store remains responsible for the real domain change, but core keeps the envelope safe and consistent.

#### Function details

##### `_ObjectCursor.validate_rank`  (lines 195–200)

```
def validate_rank(self) -> '_ObjectCursor'
```

**Purpose**: Checks that a list pagination cursor contains the right kind of value for its recorded sort type. This prevents a cursor from saying, for example, that it sorted by a number while carrying text.

**Data flow**: It reads the cursor’s rank and value after Pydantic has built the cursor object. If the pair matches one of the allowed combinations, the cursor is kept; otherwise validation fails with a clear error.

**Call relations**: This is used automatically when object_page rebuilds a cursor from the caller’s cursor token. It protects the later sorting comparison from malformed cursor data.


##### `object_page`  (lines 203–286)

```
def object_page(rows: tuple[ObjectRow, ...], query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Applies the shared listing rules for object rows: searching, exact filters, sorting, and pagination. Object kinds can hand it their lightweight rows and get back a consistent page result.

**Data flow**: It receives rows plus an ObjectListQuery. It checks that row fields are declared and do not collide with reserved fields, filters rows by search text and exact field matches, sorts them using _sortable, trims the result to the fixed page size, and returns an ObjectPage with an optional cursor for the next page.

**Call relations**: MemberOwnedObjects.list and MemberReadableObjects.member_page call this after their visibility gates have selected rows the caller may see. It calls _sortable to make field values comparable and creates _ObjectCursor values when another page exists.

*Call graph*: calls 1 internal fn (_sortable); called by 2 (list, member_page); 2 external calls (__init__, __init__).


##### `object_page.value`  (lines 231–236)

```
def value(row: ObjectRow, name: str) -> JsonValue
```

**Purpose**: Looks up one sortable or filterable value from a row by field name. It gives object_page a single way to read built-in fields and kind-specific fields.

**Data flow**: It takes an ObjectRow and a field name. For name and summary it returns the row’s built-in values; for any other field it reads from the row’s fields mapping, returning missing fields as null.

**Call relations**: This helper lives inside object_page because only the paging algorithm needs it. It feeds search, filter, and sort decisions inside that one listing pass.


##### `_sortable`  (lines 289–302)

```
def _sortable(value: JsonValue, field_name: str) -> tuple[_SortRank, str | int | float]
```

**Purpose**: Turns a simple JSON value into a stable sort key. It makes nulls, booleans, numbers, and strings comparable in a predictable order.

**Data flow**: It receives a field value and the field’s name. It returns a rank plus a normalized value, or raises an error if the value is a complex shape such as an object or list that cannot be safely ordered.

**Call relations**: object_page calls this while sorting rows and while interpreting pagination cursors. It is the small rulebook that keeps all object listings ordered the same way.

*Call graph*: called by 1 (object_page).


##### `ObjectStore.list`  (lines 317–317)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Defines the method every object kind must provide to list its visible lightweight rows. It is a protocol method, meaning it describes the expected shape rather than implementing storage itself.

**Data flow**: A ToolContext and ObjectListQuery go in. The implementing store reads its own data source, applies any kind-specific row creation, and returns an ObjectPage.

**Call relations**: ObjectVerbs._list calls the concrete implementation after resolving the kind and binding the right extension context. Member-owned base classes also implement this contract for subclasses.


##### `ObjectStore.get`  (lines 319–319)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SpecT] | None
```

**Purpose**: Defines how an object kind reads one full object by name. The result includes the validated spec and related metadata, or nothing if the object is absent or hidden.

**Data flow**: A ToolContext and object name go in. The implementing store looks up the object and returns ObjectDetail, or null if there is no readable object.

**Call relations**: ObjectVerbs._get, ObjectVerbs._apply, ObjectVerbs._delete, and ObjectVerbs.action_target rely on this to learn whether a named object exists before reading, changing, deleting, or acting on it.


##### `ObjectStore.status`  (lines 321–327)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Defines how an object kind reports live state beside the stored spec. Status is separate from spec because it may change over time without being part of the authored object document.

**Data flow**: A context, name, and optional expected generation go in. The store reads current live state, checks generation if it supports that safety fence, and returns a JSON-like dictionary or null.

**Call relations**: ObjectVerbs._get calls this after get so object reads include both saved configuration and current state. ObjectVerbs.action_target also calls it to make the kind’s visibility and freshness checks run before an action handler proceeds.


##### `ObjectStore.apply`  (lines 329–337)

```
async def apply(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Defines how an object kind creates or updates one object after core has validated the outer envelope and spec. The actual domain write belongs to the kind’s store.

**Data flow**: A context, name, validated spec, old spec if any, and optional expected generation go in. The store performs the create or update, or raises a domain-specific refusal.

**Call relations**: ObjectVerbs._apply calls this after parsing YAML, checking names, validating the Pydantic model, and journaling the intended change. MemberOwnedObjects.apply is one reusable implementation for member-owned kinds.


##### `ObjectStore.delete`  (lines 339–345)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Defines how an object kind deletes one named object. Core coordinates the request, but the kind’s store decides what deletion means in its own tables.

**Data flow**: A context, name, and optional expected generation go in. The store removes or deactivates the object, or raises an error if deletion is not allowed.

**Call relations**: ObjectVerbs._delete calls this after it has read the old object and written a journal entry. MemberOwnedObjects.delete provides the shared ownership-gated version for member-owned kinds.


##### `owner_emails`  (lines 364–379)

```
async def owner_emails(owners: Iterable[UUID | None]) -> dict[UUID | None, str]
```

**Purpose**: Resolves member IDs to email addresses in one database query. Object listings use this when they want to show who owns each row.

**Data flow**: It receives a collection of member IDs, ignoring null owners. If there are IDs, it opens a workspace database transaction, selects matching member emails, and returns a mapping from member ID to email.

**Call relations**: Stores for member-owned object kinds can call this while building list rows. It uses workspace_tx for database access and SQLAlchemy to build the query.

*Call graph*: 2 external calls (select, workspace_tx).


##### `MemberOwnedObjects.list`  (lines 421–429)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists member-owned objects while enforcing who is allowed to see them. It keeps private, shared, and admin-only rows from leaking into ordinary listings.

**Data flow**: It reads the acting member and whether the speaker is an admin from the ToolContext. It asks the subclass for owned rows, keeps only rows visible to this caller and allowed by _listed, converts them to ObjectRow values, and passes them to object_page.

**Call relations**: ObjectVerbs._list reaches this when a member-owned store is registered for a kind. The method delegates storage details to _owned_rows and common paging to object_page.

*Call graph*: calls 5 internal fn (_listed, _owned_rows, _visible, object_page, speaker_is_admin); 1 external calls (__init__).


##### `MemberOwnedObjects.get`  (lines 431–442)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SpecT] | None
```

**Purpose**: Reads one member-owned object only if the caller is allowed to see it. It turns hidden objects into not-found results rather than revealing that private data exists.

**Data flow**: It finds the owner for the name, checks visibility against the acting member and admin status, then asks the subclass for full detail. If the owner carries a generation, it copies that generation onto the returned detail.

**Call relations**: ObjectVerbs._get and other verbs call this through the ObjectStore interface. It relies on _owner, _visible, and _detail so subclasses only supply the data-specific pieces.

*Call graph*: calls 4 internal fn (_detail, _owner, _visible, speaker_is_admin); 1 external calls (replace).


##### `MemberOwnedObjects.status`  (lines 444–464)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reads live status for a member-owned object with visibility and freshness checks. It avoids showing status for an object that changed or became invisible during the read.

**Data flow**: It looks up the owner, checks the expected generation, checks visibility, reads status from the subclass, then looks up the owner again and repeats the generation and visibility checks. It returns the status, null for a disappeared object, or raises not-found/stale errors.

**Call relations**: ObjectVerbs._get calls store.status after reading detail. This method calls _owner, _require_current_generation, _visible, and _status to combine safety checks with subclass-specific status reading.

*Call graph*: calls 5 internal fn (_owner, _require_current_generation, _status, _visible, speaker_is_admin); 1 external calls (__init__).


##### `MemberOwnedObjects.apply`  (lines 466–492)

```
async def apply(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Creates or updates a member-owned object while enforcing ownership, admin rules, optional live-speaker requirements, and generation freshness. It is the shared write gate for many object kinds.

**Data flow**: It receives a validated spec plus the old spec if one was read. It finds the current owner, checks visibility and generation, decides whether the actor owns the row or needs admin permission, optionally requires a live speaker, and then calls the subclass’s _apply_owned to perform the real write.

**Call relations**: ObjectVerbs._apply reaches this through the store interface after validation and journaling. The method coordinates helper checks such as _owned, _visible, _admin_can_apply, and _require_current_generation before handing off to _apply_owned.

*Call graph*: calls 7 internal fn (_admin_can_apply, _apply_owned, _owned, _owner, _require_current_generation, _visible, speaker_is_admin); 2 external calls (__init__, __init__).


##### `MemberOwnedObjects.delete`  (lines 494–512)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Deletes a member-owned object only when the caller can see it and has authority to remove it. It treats invisible objects as not found.

**Data flow**: It looks up the owner, checks the generation fence, refuses if the object is missing or hidden, verifies the caller is either the owner or an admin, optionally requires a live speaker, and then calls _delete_owned.

**Call relations**: ObjectVerbs._delete calls this through ObjectStore.delete. It uses the shared ownership helpers before allowing the subclass-specific deletion code to run.

*Call graph*: calls 6 internal fn (_delete_owned, _owned, _owner, _require_current_generation, _visible, speaker_is_admin); 2 external calls (__init__, __init__).


##### `MemberOwnedObjects._owned`  (lines 514–518)

```
def _owned(self, owner: OwnerT, acting: UUID | None) -> bool
```

**Purpose**: Answers whether the acting member is the actual owner of a row. Admin-only rows with no member owner are deliberately owned by nobody.

**Data flow**: It receives an owner record and the acting member ID. It returns true only when the row has a non-null member_id and that ID matches the acting member.

**Call relations**: _visible uses this to decide read access, while apply and delete use it to decide whether admin permission is needed.

*Call graph*: called by 3 (_visible, apply, delete).


##### `MemberOwnedObjects._visible`  (lines 520–521)

```
def _visible(self, owner: OwnerT, acting: UUID | None, is_admin: bool) -> bool
```

**Purpose**: Answers whether a member-owned row should be visible to the current caller. Visibility is granted when the row is shared, owned by the actor, or the caller is an admin.

**Data flow**: It receives the owner record, acting member ID, and admin flag. It combines shared status, _owned, and admin status into one boolean result.

**Call relations**: list, get, status, apply, and delete all call this so every member-owned verb uses the same privacy rule.

*Call graph*: calls 1 internal fn (_owned); called by 5 (apply, delete, get, list, status).


##### `MemberOwnedObjects._listed`  (lines 523–531)

```
def _listed(self, row: OwnedRow[OwnerT], query: ObjectListQuery) -> bool
```

**Purpose**: Lets a subclass hide certain addressable rows from browse-style listings. By default, anything visible is also listed.

**Data flow**: It receives an owned row and the listing query. The base implementation ignores the query and returns true, but subclasses may override it to apply special listing-only rules.

**Call relations**: MemberOwnedObjects.list calls this after visibility checks and before object_page. It exists because the base class owns the common query flow.

*Call graph*: called by 1 (list).


##### `MemberOwnedObjects._admin_can_apply`  (lines 533–534)

```
def _admin_can_apply(self, old: SpecT, spec: SpecT) -> bool
```

**Purpose**: Lets a subclass allow a workspace admin to make a narrow kind of update to someone else’s row. The safe default is no.

**Data flow**: It receives the old spec and proposed new spec. The base implementation always returns false, meaning admin updates to another member’s row are refused unless a subclass opts in.

**Call relations**: MemberOwnedObjects.apply calls this when the actor is not the owner but is an admin. It is a policy hook for object kinds with special admin-edit rules.

*Call graph*: called by 1 (apply).


##### `MemberOwnedObjects._require_current_generation`  (lines 536–553)

```
def _require_current_generation(self, name: str, owner: OwnerT | None, expected_generation: UUID | None, action: str) -> None
```

**Purpose**: Stops an operation when the row under a name has changed since it was read. This is a safety fence against acting on stale information.

**Data flow**: It receives the name, current owner, expected generation, and action wording. If the owner has a generation, that generation must match; if the kind is ungenerated, no generation should have been supplied. On mismatch it raises an error.

**Call relations**: status, apply, and delete call this before exposing status or changing data. GeneratedObjectOwner supplies the generation value that makes the fence possible.

*Call graph*: called by 3 (apply, delete, status).


##### `MemberOwnedObjects._owner`  (lines 555–556)

```
async def _owner(self, ctx: ToolContext, name: str) -> OwnerT | None
```

**Purpose**: Finds the owner record for a named member-owned object. It is the common lookup used before visibility and permission checks.

**Data flow**: It asks _owned_rows for all candidate rows, scans for the requested name, and returns that row’s owner or null if no row matches.

**Call relations**: get, status, apply, and delete call this. The actual row source comes from the subclass’s _owned_rows implementation.

*Call graph*: calls 1 internal fn (_owned_rows); called by 4 (apply, delete, get, status).


##### `MemberOwnedObjects._owned_rows`  (lines 558–559)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[OwnerT], ...]
```

**Purpose**: Declares that subclasses must provide the lightweight rows and owners for their object kind. The base class cannot know where each extension stores its rows.

**Data flow**: A ToolContext goes in. A subclass should return all rows relevant to the current context, including owner information and lightweight fields.

**Call relations**: MemberOwnedObjects.list and _owner depend on this. The base method raises NotImplementedError to force each concrete kind to supply it.

*Call graph*: called by 2 (_owner, list).


##### `MemberOwnedObjects._detail`  (lines 561–564)

```
async def _detail(self, ctx: ToolContext, name: str, owner: OwnerT) -> ObjectDetail[SpecT] | None
```

**Purpose**: Declares that subclasses must provide the full detail for one already-visible owned object. This keeps storage-specific reading outside the shared gate.

**Data flow**: The context, object name, and owner go in. A subclass should return ObjectDetail or null if the row no longer exists.

**Call relations**: MemberOwnedObjects.get calls this after owner and visibility checks. The base method is only a required hook.

*Call graph*: called by 1 (get).


##### `MemberOwnedObjects._status`  (lines 566–569)

```
async def _status(self, ctx: ToolContext, name: str, owner: OwnerT) -> dict[str, JsonValue] | None
```

**Purpose**: Declares that subclasses must provide live status for one owned object. Status is kind-specific, so the shared base class cannot compute it.

**Data flow**: The context, object name, and owner go in. A subclass should return a JSON-like status dictionary or null.

**Call relations**: MemberOwnedObjects.status calls this between its before-and-after safety checks. The base method raises NotImplementedError.

*Call graph*: called by 1 (status).


##### `MemberOwnedObjects._apply_owned`  (lines 571–579)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, owner: OwnerT | None) -> None
```

**Purpose**: Declares that subclasses must perform the actual create or update after the shared ownership gate has passed. This is where the object kind changes its own storage.

**Data flow**: It receives the context, name, new spec, old spec if any, and current owner if any. A subclass writes the change or raises a domain error.

**Call relations**: MemberOwnedObjects.apply calls this only after visibility, authority, live-speaker, and generation checks. The base method is a required storage hook.

*Call graph*: called by 1 (apply).


##### `MemberOwnedObjects._delete_owned`  (lines 581–582)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: OwnerT) -> None
```

**Purpose**: Declares that subclasses must perform the actual deletion after the shared ownership gate has passed. The base class handles permission; the subclass handles storage.

**Data flow**: It receives the context, name, and owner. A subclass removes or otherwise deletes the row in its own data store.

**Call relations**: MemberOwnedObjects.delete calls this after not-found, visibility, admin, speaker, and generation checks. The base method raises NotImplementedError.

*Call graph*: called by 1 (delete).


##### `MemberReadable.member_detail`  (lines 604–611)

```
async def member_detail(self, ext: 'ExtensionContext | None', name: str, *, member_id: UUID, admin: bool) -> MemberObject | None
```

**Purpose**: Defines the portal-facing way to read one object as a signed-in member outside a chat turn. Implementing this is a kind’s opt-in to member portal detail pages.

**Data flow**: An extension context, object name, member ID, and admin flag go in. The implementation returns a MemberObject if visible, or null if absent or hidden.

**Call relations**: Portal routes can rely on this protocol when a kind supports member reads. Kinds that cannot answer outside a turn simply do not implement it.


##### `MemberListable.member_page`  (lines 619–626)

```
async def member_page(self, ext: 'ExtensionContext | None', *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Defines the portal-facing way to list a page of objects for one signed-in member. It extends member detail support with searchable, filterable indexes.

**Data flow**: An extension context, member ID, admin flag, and ObjectListQuery go in. The implementation returns an ObjectPage containing only rows the member may see.

**Call relations**: MemberReadableObjects implements this protocol for member-owned kinds. Portal listing routes can call it when the object kind is listable.


##### `ConversationMemberListable.member_conversation_rows`  (lines 638–646)

```
async def member_conversation_rows(self, ext: 'ExtensionContext | None', conversation_id: UUID, *, member_id: UUID, admin: bool, limit: int) -> tuple[ConversationObjectGrant, ...]
```

**Purpose**: Defines how a kind can expose object grants related to a conversation for one member. This supports conversation views that need to show which objects were granted or visible there.

**Data flow**: An extension context, conversation ID, member ID, admin flag, and limit go in. The implementation returns grant records containing object names, generations, and whether content is visible.

**Call relations**: This is a protocol hook for kinds that participate in conversation object listings. The concrete implementations live elsewhere.


##### `MemberReadableObjects.member_page`  (lines 659–672)

```
async def member_page(self, ext: 'ExtensionContext | None', *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists member-readable objects for the portal while applying the same visibility rule as turn-time object listing. It keeps portal and chat behavior aligned.

**Data flow**: It receives an extension context, member ID, admin flag, and query. It asks _member_rows for rows, filters them through _visible and _listed, converts them to ObjectRow values, and sends them to object_page.

**Call relations**: This implements MemberListable. It shares object_page with MemberOwnedObjects.list so portal and tool listings behave the same way.

*Call graph*: calls 2 internal fn (_member_rows, object_page); 1 external calls (__init__).


##### `MemberReadableObjects.member_detail`  (lines 674–692)

```
async def member_detail(self, ext: 'ExtensionContext | None', name: str, *, member_id: UUID, admin: bool) -> MemberObject[SpecT] | None
```

**Purpose**: Reads one portal-visible object and returns both its list row and full detail. It uses the same ownership gate as the turn-side methods.

**Data flow**: It loads member rows, finds the requested name, checks visibility, asks _member_object for full detail, and wraps the row plus detail in a MemberObject. Missing or hidden rows return null.

**Call relations**: This implements MemberReadable. It calls subclass hooks _member_rows and _member_object, then packages the result for portal detail pages.

*Call graph*: calls 2 internal fn (_member_object, _member_rows); 2 external calls (__init__, __init__).


##### `MemberReadableObjects._owned_rows`  (lines 694–695)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[OwnerT], ...]
```

**Purpose**: Adapts portal-style member row loading to the ObjectStore list/get path used during turns. This avoids having separate row logic for portal and chat.

**Data flow**: It takes the ToolContext, extracts the extension context and acting member ID, and returns _member_rows for that member.

**Call relations**: MemberOwnedObjects._owner and list call this through inheritance. It delegates directly to _member_rows.

*Call graph*: calls 1 internal fn (_member_rows).


##### `MemberReadableObjects._detail`  (lines 697–700)

```
async def _detail(self, ctx: ToolContext, name: str, owner: OwnerT) -> ObjectDetail[SpecT] | None
```

**Purpose**: Adapts portal-style object detail loading to the ObjectStore get path used during turns. This keeps detail rendering consistent across entry points.

**Data flow**: It takes the ToolContext, name, and owner, then calls _member_object with the context’s extension and acting member ID. The resulting ObjectDetail is returned.

**Call relations**: MemberOwnedObjects.get calls this through inheritance. It is a bridge from turn-time reads to the subclass’s member-readable implementation.

*Call graph*: calls 1 internal fn (_member_object).


##### `MemberReadableObjects._member_rows`  (lines 702–705)

```
async def _member_rows(self, ext: 'ExtensionContext | None', *, member_id: UUID | None) -> tuple[OwnedRow[OwnerT], ...]
```

**Purpose**: Declares that subclasses must provide rows readable for a given member. This is the common source for both portal and turn-side listings.

**Data flow**: An extension context and optional member ID go in. A subclass should return owned rows with names, summaries, owners, and lightweight fields.

**Call relations**: member_page, member_detail, and _owned_rows all call this. The base method is a required hook for concrete member-readable kinds.

*Call graph*: called by 3 (_owned_rows, member_detail, member_page).


##### `MemberReadableObjects._member_object`  (lines 707–715)

```
async def _member_object(self, ext: 'ExtensionContext | None', name: str, owner: OwnerT, *, member_id: UUID | None) -> ObjectDetail[SpecT] | None
```

**Purpose**: Declares that subclasses must provide full detail for one member-readable object. This is the common source for portal and turn-side detail reads.

**Data flow**: An extension context, name, owner, and optional member ID go in. A subclass should return ObjectDetail or null.

**Call relations**: member_detail and _detail call this. The base method raises NotImplementedError to force storage-specific implementation.

*Call graph*: called by 2 (_detail, member_detail).


##### `action_registry`  (lines 763–808)

```
def action_registry(bound: tuple[BoundAction, ...], kinds: Mapping[str, BoundKind]) -> dict[str, dict[str, BoundAction]]
```

**Purpose**: Validates and indexes all object actions for a deployment. An object action is an extra operation attached to a kind or instance when create/read/update/delete is not enough.

**Data flow**: It receives bound actions and the registered kinds. It checks bindings, action and kind names, target kind existence, duplicate action names, reserved input fields, tool declaration safety, and input schema safety, then returns a nested lookup by kind and action name.

**Call relations**: This runs after manifests are collected at startup. It calls validate_tool_declaration and _validate_spec_model so unsafe action inputs fail before the system serves requests.

*Call graph*: calls 1 internal fn (_validate_spec_model); 2 external calls (fullmatch, validate_tool_declaration).


##### `object_registry`  (lines 811–834)

```
def object_registry(bound: tuple[BoundKind, ...]) -> dict[str, BoundKind]
```

**Purpose**: Validates and indexes all object kinds for a deployment. It is the boot-time gate that prevents ambiguous or unsafe kind declarations.

**Data flow**: It receives bound object kinds. For each one it checks the kind name grammar, duplicate names, valid cross-agent verb declarations, and spec model safety, then returns a mapping from kind name to BoundKind.

**Call relations**: ObjectVerbs uses this registry to resolve every object request. It calls _validate_spec_model so bad object specs are rejected during startup rather than during a user request.

*Call graph*: calls 1 internal fn (_validate_spec_model); 1 external calls (fullmatch).


##### `_validate_spec_model`  (lines 837–856)

```
def _validate_spec_model(label: str, spec_model: type[BaseModel]) -> None
```

**Purpose**: Checks that an object spec or action input model is safe to store, display, and echo back. It rejects models that allow unknown keys, contain secret fields, or cannot be represented as JSON.

**Data flow**: It walks the provided Pydantic model and reachable nested models. It inspects model configuration and field annotations, then asks Pydantic to produce a JSON schema. Any unsafe condition becomes a clear ValueError.

**Call relations**: object_registry calls this for object specs, and action_registry calls it for action inputs. It uses _reachable_models and _annotation_types to inspect nested models too.

*Call graph*: calls 2 internal fn (_annotation_types, _reachable_models); called by 2 (action_registry, object_registry).


##### `_reachable_models`  (lines 859–873)

```
def _reachable_models(model: type[BaseModel]) -> tuple[type[BaseModel], ...]
```

**Purpose**: Finds all nested Pydantic models reachable from a root model’s field types. This lets validation cover not only the top-level spec but embedded structures too.

**Data flow**: It starts with one model, repeatedly inspects field annotations, adds nested BaseModel subclasses to a frontier, skips models already seen, and returns the discovered models.

**Call relations**: _validate_spec_model calls this before checking model settings and fields. It uses _annotation_types to unwrap compound type annotations.

*Call graph*: calls 1 internal fn (_annotation_types); called by 1 (_validate_spec_model).


##### `_annotation_types`  (lines 876–883)

```
def _annotation_types(annotation: object) -> tuple[object, ...]
```

**Purpose**: Flattens a type annotation into the concrete pieces inside it. For example, it can look through container or union types to find nested model or secret types.

**Data flow**: It receives an annotation. If the annotation has no type arguments, it returns it as a single item; otherwise it recursively gathers the argument types and returns them as a tuple.

**Call relations**: _reachable_models and _validate_spec_model call this while inspecting schemas. It uses typing.get_args to see inside parameterized type hints.

*Call graph*: called by 2 (_reachable_models, _validate_spec_model); 1 external calls (get_args).


##### `ObjectVerbs.tools`  (lines 967–1050)

```
def tools(self) -> tuple[ToolDef, ...]
```

**Purpose**: Builds the tool definitions exposed to the model for object operations. These are the public commands for listing, reading, explaining, applying, deleting, and invoking object actions.

**Data flow**: It creates ToolDef objects with names, plain descriptions, input models, handlers, and safety flags such as whether a tool has side effects. It returns them as a tuple.

**Call relations**: The tool registry consumes these definitions elsewhere. Each ToolDef points back to one ObjectVerbs handler such as _list, _get, _apply, or _delete.

*Call graph*: 1 external calls (__init__).


##### `ObjectVerbs._list`  (lines 1052–1089)

```
async def _list(self, ctx: ToolContext, args: ObjectListInput) -> ToolResult
```

**Purpose**: Implements the object_list tool. With no kind it lists available object kinds; with a kind it lists instances of that kind.

**Data flow**: It reads the input arguments and context. If no kind is given, it returns kind descriptions and whether granted actions exist. If a kind is given, it resolves the kind, checks any agent target, binds the extension context, calls the store’s list method with an ObjectListQuery, adds action templates and cursor data, and returns JSON.

**Call relations**: This is the handler declared by ObjectVerbs.tools. It uses _resolve, _target, _bound_ctx, _granted_actions, _action_views, object_agent, and _json_result to move from tool input to a safe listing response.

*Call graph*: calls 6 internal fn (_action_views, _bound_ctx, _granted_actions, _resolve, _target, _json_result); 2 external calls (__init__, object_agent).


##### `ObjectVerbs._get`  (lines 1091–1138)

```
async def _get(self, ctx: ToolContext, args: ObjectGetInput) -> ToolResult
```

**Purpose**: Implements the object_get tool. It reads one object’s spec, status, links, timestamps, generation, and available instance actions.

**Data flow**: It resolves the kind, binds the owning extension context, checks any agent target, reads detail from the store, raises not-found if absent, reads status using the detail’s generation, rewrites applicable links for agent-scoped reads, and returns a YAML-rendered result.

**Call relations**: This is the read handler declared by ObjectVerbs.tools. It calls _resolve, _target, _bound_ctx, _action_views, and the kind store’s get/status methods under object_agent scope.

*Call graph*: calls 4 internal fn (_action_views, _bound_ctx, _resolve, _target); 5 external calls (__init__, __init__, __init__, object_agent, safe_dump).


##### `ObjectVerbs._explain`  (lines 1140–1155)

```
async def _explain(self, ctx: ToolContext, args: ObjectExplainInput) -> ToolResult
```

**Purpose**: Implements the object_explain tool. It tells a caller how to author or use a kind before creating or updating an object.

**Data flow**: It resolves the kind and returns JSON containing the kind description, guidance, allowed cross-agent verbs, object name rule, JSON schema for the spec, and available collection and instance actions.

**Call relations**: This handler is declared by ObjectVerbs.tools. It uses _resolve and _action_views, then formats the response with _json_result.

*Call graph*: calls 3 internal fn (_action_views, _resolve, _json_result).


##### `ObjectVerbs._apply`  (lines 1157–1222)

```
async def _apply(self, ctx: ToolContext, args: ObjectApplyInput) -> ToolResult
```

**Purpose**: Implements the object_apply tool for creating or updating objects from a YAML manifest. It validates the request, journals the intended change, and then asks the owning store to write.

**Data flow**: It parses the YAML envelope, resolves the kind, checks cross-agent permission, validates the object name and spec, reads any existing object, enforces create_only, records an object_change journal row, calls the store’s apply method with the correct expected generation, withdraws the journal row if this attempt fails, and returns created or updated JSON.

**Call relations**: This is the write handler declared by ObjectVerbs.tools. It coordinates _parse_envelope, _resolve, _target, _bound_ctx, _journal_object_change, _withdraw_object_change, object_agent, and _json_result around the concrete store write.

*Call graph*: calls 7 internal fn (_bound_ctx, _resolve, _target, _journal_object_change, _json_result, _parse_envelope, _withdraw_object_change); 4 external calls (__init__, __init__, validate_object_name, object_agent).


##### `ObjectVerbs._delete`  (lines 1224–1259)

```
async def _delete(self, ctx: ToolContext, args: ObjectDeleteInput) -> ToolResult
```

**Purpose**: Implements the object_delete tool. It deletes a named object and echoes the old spec when visible so a mistaken delete can be recreated.

**Data flow**: It resolves the kind, binds context, checks any agent target, reads the old object, raises not-found if missing, records a delete journal row, calls the store’s delete method with the old generation, withdraws this attempt’s journal row on failure, and returns JSON with deleted status and the old spec if allowed.

**Call relations**: This is the delete handler declared by ObjectVerbs.tools. It uses the same journaling helpers as _apply and calls the concrete store under object_agent scope.

*Call graph*: calls 6 internal fn (_bound_ctx, _resolve, _target, _journal_object_change, _json_result, _withdraw_object_change); 2 external calls (__init__, object_agent).


##### `ObjectVerbs._object_action`  (lines 1261–1262)

```
async def _object_action(self, ctx: ToolContext, args: ObjectActionInput) -> ToolResult
```

**Purpose**: Exists only so the object_action tool has a schema in the tool list. Actual action dispatch is performed by the engine before this handler would run.

**Data flow**: If called directly, it ignores normal action execution and raises a RuntimeError. No useful result is produced.

**Call relations**: ObjectVerbs.tools registers this as the handler for the public object action tool, but the surrounding engine resolves and dispatches the bound action instead.


##### `ObjectVerbs._granted_actions`  (lines 1264–1272)

```
def _granted_actions(self, ctx: ToolContext, kind: str) -> dict[str, BoundAction]
```

**Purpose**: Filters a kind’s registered actions down to the actions this turn is allowed to see. This prevents advertising actions the current context has not been granted.

**Data flow**: It looks up actions for the kind, compares each action’s canonical ID against ctx.granted_actions, and returns only the allowed BoundAction entries.

**Call relations**: _list uses this to mark kinds that have visible actions. _action_views uses it before building callable action templates.

*Call graph*: called by 2 (_action_views, _list).


##### `ObjectVerbs._action_views`  (lines 1274–1304)

```
def _action_views(self, ctx: ToolContext, kind: str, binding: str, *, name: str | None=None, agent: str | None=None, generation: UUID | None=None) -> list[JsonValue]
```

**Purpose**: Builds the action templates shown in object_list, object_get, and object_explain. A template tells the caller exactly how to invoke an available action.

**Data flow**: It filters granted actions by collection versus instance binding, fixed instance name if any, and whether an agent-targeted read may show the action. It then calls action_view and serializes each view to JSON-like data.

**Call relations**: _list, _get, and _explain call this when including actions in their responses. It depends on _granted_actions and the shared action_view formatter.

*Call graph*: calls 1 internal fn (_granted_actions); called by 3 (_explain, _get, _list); 1 external calls (action_view).


##### `ObjectVerbs.action_target`  (lines 1306–1350)

```
async def action_target(self, ctx: ToolContext, action: ToolDef, wire: ObjectActionInput) -> ObjectActionTarget
```

**Purpose**: Resolves the object or collection an object action will act on before the action handler runs. It makes the target kind, object name, agent, and generation explicit.

**Data flow**: It checks that the ToolDef is an object action, verifies any agent target, returns a collection target immediately for collection actions, validates fixed instance bindings, reads the target object through the kind owner’s store, calls status to repeat visibility/freshness checks, and returns an ObjectActionTarget containing live and caller-supplied generation data.

**Call relations**: The engine calls this during object action dispatch. It uses _agent_gate, _resolve, _bound_ctx, and object_agent so target checks belong to the target kind, not the action contributor.

*Call graph*: calls 3 internal fn (_agent_gate, _bound_ctx, _resolve); 3 external calls (__init__, __init__, object_agent).


##### `ObjectVerbs._resolve`  (lines 1352–1357)

```
def _resolve(self, kind: str) -> BoundKind
```

**Purpose**: Looks up a kind name in the registry and gives a clear error if it is unknown. This is the common first step for kind-specific operations.

**Data flow**: It receives a kind string, checks the registry mapping, and returns the BoundKind. If missing, it raises UnknownKind with the list of registered kinds.

**Call relations**: _list, _get, _explain, _apply, _delete, and action_target all call this before touching a kind’s store.

*Call graph*: called by 6 (_apply, _delete, _explain, _get, _list, action_target); 1 external calls (__init__).


##### `ObjectVerbs._bound_ctx`  (lines 1359–1360)

```
def _bound_ctx(self, ctx: ToolContext, bound: BoundKind) -> ToolContext
```

**Purpose**: Rebinds a tool context to the extension context that owns a kind. This ensures store code runs with its own extension’s workspace resources.

**Data flow**: It receives the current ToolContext and a BoundKind. It returns a copy of the context with ext replaced by the bound kind’s context.

**Call relations**: _list, _get, _apply, _delete, and action_target call this before invoking a kind store. It uses dataclasses.replace to avoid mutating the original context.

*Call graph*: called by 5 (_apply, _delete, _get, _list, action_target); 1 external calls (replace).


##### `ObjectVerbs._target`  (lines 1362–1375)

```
async def _target(self, ctx: ToolContext, bound: BoundKind, name: str, verbs: frozenset[AgentTargetVerb]) -> ObjectAgent | None
```

**Purpose**: Checks and resolves an optional agent target for ordinary object verbs. Agent targeting lets the main workspace agent operate in another agent’s object namespace when a kind allows it.

**Data flow**: It receives the context, bound kind, requested agent name, and verbs being attempted. Empty agent names return null. Non-empty names are rejected unless the kind declares at least one matching targetable verb, then _agent_gate resolves the named agent.

**Call relations**: _list, _get, _apply, and _delete call this before entering object_agent scope. It delegates the detailed permission and database lookup to _agent_gate.

*Call graph*: calls 1 internal fn (_agent_gate); called by 4 (_apply, _delete, _get, _list).


##### `ObjectVerbs._agent_gate`  (lines 1377–1426)

```
async def _agent_gate(self, ctx: ToolContext, name: str) -> ObjectAgent | None
```

**Purpose**: Enforces the rules for targeting another agent’s object namespace. It allows this only from the main agent, for a live member-requested call, and only to an agent the member may access.

**Data flow**: It reads the current agent from the database. If the requested name is the current agent, it returns null. Otherwise it checks main-agent status, subagent status, live speaker presence, admin status, and target agent visibility, then returns an ObjectAgent or raises a clear error.

**Call relations**: _target and action_target call this whenever a request names another agent. It uses workspace_tx, SQLAlchemy queries, and member_is_admin to make the access decision.

*Call graph*: called by 2 (_target, action_target); 5 external calls (__init__, or_, select, workspace_tx, member_is_admin).


##### `_journal_object_change`  (lines 1429–1483)

```
async def _journal_object_change(ctx: ToolContext, kind: str, name: str, verb: Literal['create', 'update', 'delete'], before: BaseModel | None, after: BaseModel | None, agent_id: UUID) -> UUID | None
```

**Purpose**: Records a create, update, or delete before the actual mutation happens. This gives the system an audit trail and supports safe retry after crashes or duplicate dispatches.

**Data flow**: It builds a change ID from the idempotency key when available, identifies the caller, checks whether the journal row already exists, and if not inserts kind, name, verb, caller, agent, before spec, after spec, and timestamp into object_change. It returns the inserted row ID, or null if the row already existed.

**Call relations**: ObjectVerbs._apply and ObjectVerbs._delete call this before store writes. If the later write fails and this attempt created the journal row, they call _withdraw_object_change.

*Call graph*: called by 2 (_apply, _delete); 7 external calls (dumps, model_dump, insert, select, workspace_tx, uuid4, uuid5).


##### `_withdraw_object_change`  (lines 1486–1493)

```
async def _withdraw_object_change(ctx: ToolContext, change_id: UUID) -> None
```

**Purpose**: Removes a journal row that was written for a mutation attempt that failed before committing. This keeps failed writes from looking like successful changes.

**Data flow**: It receives the context and change ID, opens a workspace transaction, and deletes the matching object_change row for the current workspace.

**Call relations**: ObjectVerbs._apply and ObjectVerbs._delete call this in exception paths after _journal_object_change returned a newly inserted row ID.

*Call graph*: called by 2 (_apply, _delete); 2 external calls (delete, workspace_tx).


##### `_parse_envelope`  (lines 1496–1522)

```
def _parse_envelope(manifest: str) -> tuple[str, str, Mapping[str, object], UUID | None]
```

**Purpose**: Parses and validates the YAML manifest used by object_apply. It makes sure the request has exactly the expected outer shape before spec validation begins.

**Data flow**: It checks the byte size, safely loads YAML, requires a mapping with kind, name, spec, and optional generation, verifies kind and name are strings and spec is a mapping, parses generation as a UUID if present, and returns kind, name, spec mapping, and generation.

**Call relations**: ObjectVerbs._apply calls this as its first input gate. Later steps validate the object name and the kind-specific spec model.

*Call graph*: called by 1 (_apply); 3 external calls (__init__, UUID, safe_load).


##### `_json_result`  (lines 1525–1526)

```
def _json_result(payload: Mapping[str, object]) -> ToolResult
```

**Purpose**: Wraps a JSON-serializable payload as a ToolResult. It is a small helper for tool handlers that return compact JSON text.

**Data flow**: It receives a mapping, serializes it with json.dumps, puts the text in TextContent, and returns a ToolResult containing that content.

**Call relations**: ObjectVerbs._list, _explain, _apply, and _delete call this to format their responses consistently.

*Call graph*: called by 4 (_apply, _delete, _explain, _list); 3 external calls (__init__, __init__, dumps).


### `core/src/ufo/kinds/governance.py`

`domain_logic` · `request handling`

This file is a safety gate for changing an agent's configuration, specifically its prompt. Instead of letting code overwrite an agent prompt immediately, it creates a proposal that says: “change this prompt from the version I saw to this new text.” The “version I saw” is stored as a digest, which is a short fingerprint made from the prompt text.

The important idea is compare-and-swap: like checking that a document is still on the same draft before replacing it. When a proposal is approved, the code reads the current agent prompt again. If its fingerprint no longer matches the proposal’s original fingerprint, that means someone or something changed the prompt in the meantime. In that case, the proposal is rejected rather than accidentally overwriting newer work.

The Governance class is tied to one workspace, so proposals cannot cross workspace boundaries. It also records which extension or component opened the proposal. Database work happens inside a transaction, meaning the related reads and writes are treated as one safe unit. During approval, the agent row is locked while checking and updating, so two approvals cannot race each other and silently conflict.

#### Function details

##### `prompt_digest`  (lines 16–17)

```
def prompt_digest(prompt: str) -> str
```

**Purpose**: This function turns a prompt into a stable fingerprint using SHA-256, a common hashing method that produces a fixed-length string from text. The code uses that fingerprint to tell whether a prompt has changed without comparing long prompt text everywhere.

**Data flow**: It takes one prompt string as input. It encodes the text, runs it through SHA-256, and returns the hexadecimal digest string. It does not change anything outside itself.

**Call relations**: When a change is proposed, Governance.propose_change uses this to record the fingerprint of the new prompt. When a proposal is approved, Governance.approve_proposal uses it to compare the proposal’s expected old prompt with the agent’s current prompt.

*Call graph*: called by 2 (approve_proposal, propose_change); 1 external calls (sha256).


##### `Governance.propose_change`  (lines 28–55)

```
async def propose_change(self, change: AgentChange) -> ProposalRef
```

**Purpose**: This function opens a new proposal to change an agent’s prompt. It records the requested change but does not apply it yet, so approval can happen later with a safety check.

**Data flow**: It receives an AgentChange, which includes the target agent, the prompt fingerprint the proposer started from, and the new prompt text. It creates a new proposal ID, opens a workspace database transaction, checks that the agent exists in this workspace, and inserts a pending proposal containing the old fingerprint, the new prompt fingerprint, the new prompt body, and the proposer extension. It returns a ProposalRef pointing to the new proposal. If the agent is not found in this workspace, it raises an error instead of creating anything.

**Call relations**: Higher-level code calls this when it wants to request a prompt change rather than write it directly. Inside the transaction it uses SQLAlchemy to read the agent and insert the proposal, calls prompt_digest to fingerprint the new prompt, and returns a ProposalRef so later code can refer to the proposal.

*Call graph*: calls 1 internal fn (prompt_digest); 5 external calls (__init__, insert, select, workspace_tx, uuid4).


##### `Governance.approve_proposal`  (lines 57–114)

```
async def approve_proposal(self, proposal_id: UUID) -> None
```

**Purpose**: This function tries to approve and apply a pending proposal. It only updates the agent prompt if the current prompt still matches the fingerprint recorded when the proposal was created.

**Data flow**: It receives a proposal ID. It opens a workspace database transaction, loads the proposal for this workspace, and checks that it exists and is still pending. It then reads and locks the target agent’s prompt, meaning the database prevents another transaction from changing that same row at the same moment. If the current prompt fingerprint does not match the proposal’s original fingerprint, it marks the proposal rejected and logs that rejection. If the fingerprint matches, it writes the new prompt into the agent row, marks the proposal approved, and logs the approval after the transaction completes.

**Call relations**: Higher-level approval code calls this when a pending proposal should be accepted. It uses prompt_digest for the conflict check, SQLAlchemy to read and update the proposal and agent rows, workspace_tx to keep the database work atomic, and the logging helper to record whether the proposal was approved or rejected.

*Call graph*: calls 1 internal fn (prompt_digest); 4 external calls (select, update, workspace_tx, log).


### `core/src/ufo/listings.py`

`domain_logic` · `request handling`

This file solves a common listing problem: people may be reading page 2 while new items are being created. If the system used simple page numbers or offsets, a new row at the top could make an item appear twice or disappear between pages. Instead, this file uses keyset paging, which means each page is anchored to the actual values of the last-seen row: its creation time and its unique id.

All listings are ordered the same way: newest first. The creation time is the main sort key, and the row id breaks ties when two rows have the same timestamp. A cursor is like a bookmark that says, “continue from this exact row, going older” or “continue from this exact row, going newer.”

The file has two main jobs. First, `page_query` shapes a database query so it asks for the right slice of rows, plus one extra row to detect whether there is another page. Second, `page_of` turns the returned rows into a `ListingPage`: the visible rows plus optional cursors for the older and newer directions.

If a cursor token is badly formed, the code raises `MalformedCursor` instead of silently showing the first page. That matters because a broken or stale link should be reported clearly, not quietly move the reader to a different place.

#### Function details

##### `ListingCursor.encode`  (lines 42–45)

```
def encode(self) -> str
```

**Purpose**: Turns a listing position into a single text token that can be put into a URL or API response. This token records both where the page boundary is and whether the next request should look for newer or older rows.

**Data flow**: It starts with a `ListingCursor` containing a creation time, an item id, and a direction flag. It chooses the word `newer` or `older`, joins that with the timestamp and id using a separator, and returns the finished string token. It does not change anything else.

**Call relations**: This is used when a page needs to hand a client a bookmark for another page. The cursors created by `page_of` can later be encoded and sent outward; when the client sends the token back, `ListingCursor.decode` reads it.


##### `ListingCursor.decode`  (lines 48–60)

```
def decode(cls, token: str) -> 'ListingCursor'
```

**Purpose**: Reads a cursor token from outside the system and turns it back into a safe, structured listing position. It rejects tokens that do not name a real-looking position.

**Data flow**: It receives a text token, splits it into direction, timestamp, and item id, then checks that the direction is either `newer` or `older`. It parses the timestamp and verifies the id is a valid UUID, which is a standard unique identifier format. If anything is missing or invalid, it raises `MalformedCursor`; otherwise it returns a `ListingCursor` with the parsed values.

**Call relations**: The web workspace memory surface calls this when a request includes a paging cursor. After decoding, the cursor can be passed into `page_query` so the database request starts from the correct place instead of guessing.

*Call graph*: called by 1 (workspace_memory); 3 external calls (__init__, fromisoformat, UUID).


##### `page_query`  (lines 74–96)

```
def page_query(query: sa.Select[Any], cursor: ListingCursor | None, limit: int, *, created_at: sa.ColumnElement[datetime], ident: sa.ColumnElement[Any]) -> sa.Select[Any]
```

**Purpose**: Prepares a database query to fetch exactly one page of a listing using cursor-based paging. It applies the shared newest-first ordering and adds the right boundary condition when the user is continuing from a cursor.

**Data flow**: It receives a database query, an optional cursor, a page size limit, and the two database columns that define the listing position: creation time and id. It orders the query newest-first when moving older, or temporarily oldest-first when moving newer, asks for one more row than the visible limit, and, if a cursor exists, filters to rows on the correct side of that cursor. It returns the modified query; it does not run the query itself.

**Call relations**: Listing code calls this before reading from the database. The extra row it requests is important because `page_of` later uses that extra row to decide whether an older or newer page link should exist.

*Call graph*: 2 external calls (tuple_, UUID).


##### `page_of`  (lines 99–128)

```
def page_of(rows: Sequence[SourceT], cursor: ListingCursor | None, limit: int, *, render: Callable[[SourceT], RowT], position: Callable[[SourceT], tuple[datetime, str]]) -> ListingPage[RowT]
```

**Purpose**: Turns the raw rows returned for a listing page into a clean page object for callers to return to clients. It chooses the visible rows, restores the display order when needed, and creates the next-page cursors.

**Data flow**: It receives the rows fetched by `page_query`, the cursor that led here if any, the visible limit, a `render` function that converts each source row into the public row shape, and a `position` function that extracts each row’s timestamp and id. It checks whether there was an extra row beyond the limit, trims to the visible rows, reverses the rows if the query had walked toward newer items, builds older and newer boundary cursors when those directions exist, and returns a `ListingPage` containing the rendered rows and optional cursors.

**Call relations**: This function is the partner to `page_query`: the query gets the right raw slice, and `page_of` packages that slice into the page envelope. Inside it, the helper `page_of.at` creates cursor objects for the first or last visible row so clients can continue browsing.

*Call graph*: 1 external calls (__init__).


##### `page_of.at`  (lines 118–120)

```
def at(source: SourceT, *, newer: bool) -> ListingCursor
```

**Purpose**: Creates a cursor for one row inside `page_of`. It is a small helper used to mark a page boundary in either the older or newer direction.

**Data flow**: It receives one source row and a direction flag. It calls the supplied `position` function to pull out the row’s creation time and item id, then returns a `ListingCursor` containing those values and the requested direction. It only builds this cursor; it does not alter the page rows.

**Call relations**: This helper is called by `page_of` when constructing the final `ListingPage`. It turns the first visible row into a `newer` cursor when there are newer rows to reach, and the last visible row into an `older` cursor when there are older rows to reach.

*Call graph*: 1 external calls (__init__).


### `core/src/ufo/object_scope.py`

`data_model` · `object action dispatch and handler execution`

Object actions in this system can sometimes run on behalf of a specific agent. This file provides the small pieces needed to record that selected agent for the current task only. Think of it like putting a temporary sticky note on the current work item: while the handler is running, code can read the note; when the handler finishes, the note is removed.

It defines two frozen Pydantic models. Pydantic is a library that checks and structures data. `ObjectAgent` names one exact agent by ID and name. `ObjectActionTarget` describes the object action target that the engine has already worked out before the handler runs: what kind of object it is, which instance name is involved, which agent is targeted, and which object generations were seen or expected.

The active object agent is stored in a `ContextVar`, which is task-local storage. That means separate concurrent tasks do not accidentally overwrite each other’s agent target. The `object_agent` context manager temporarily sets this task-local value. The `object_agent_id` helper then returns the scoped object agent ID if one was set; otherwise it falls back to the currently active agent from the broader agent scope. Without this file, audited object handlers could easily record or use the wrong agent when object dispatch crosses agent boundaries.

#### Function details

##### `object_agent`  (lines 44–52)

```
def object_agent(target: ObjectAgent | None) -> Iterator[None]
```

**Purpose**: Temporarily marks the current task as acting on a particular object agent. Code would use this around the work of running an object handler so that anything inside can ask for the correct agent target.

**Data flow**: It receives either an `ObjectAgent` or `None`. If the input is `None`, it simply runs the enclosed block without changing anything. If an agent is provided, it stores that agent in task-local storage before the enclosed block runs, then restores the previous value afterward, even if the block exits early because of an error.

**Call relations**: This is meant to wrap the period when object dispatch has resolved a target and is about to run handler code. It does not call other project functions itself; instead, it prepares the task-local value that later calls to `object_agent_id` can read.


##### `object_agent_id`  (lines 55–57)

```
def object_agent_id() -> UUID
```

**Purpose**: Returns the agent ID that should be used for the current object action. It prefers the object-specific target when one has been set, and otherwise uses the normal current agent.

**Data flow**: It reads the task-local object agent value. If that value exists, it returns that agent’s UUID. If no object-specific target is active, it asks `agent_current()` for the broader current agent and returns that agent’s ID.

**Call relations**: Handler or audit code can call this when it needs to know which agent the current object action belongs to. Its fallback path calls `ufo.agent_scope.agent_current`, so object-scoped dispatch can layer on top of the general agent scope instead of replacing it.

*Call graph*: 1 external calls (agent_current).


### `core/src/ufo/object_views.py`

`domain_logic` · `model discovery and portal rendering`

The system has “actions” that can be attached to objects, like buttons or commands a user or model may invoke. This file creates the public view of those actions: their name, description, input shape, and a pre-filled call template that says what object and action should be called later. Without this layer, other parts of the system would have to understand the full internal action objects and repeat the same filtering rules, which would make it easier to expose the wrong command or build calls incorrectly.

The central data shape is ActionView, a Pydantic model. Pydantic is a library that checks and packages data into predictable Python objects. ActionView is frozen, meaning it should not be changed after creation, and it rejects unexpected fields. That makes these views reliable to pass around.

The helper functions work like a display case in a shop: they choose which actions are allowed to be shown, arrange them in a stable order, and include enough information for someone to press the “button” later. Some actions are only for model profiles and should not appear as portal controls. Some are allowed inside embedded app pages, and the file can collect their callable IDs too.

#### Function details

##### `action_view`  (lines 27–53)

```
def action_view(kind: str, bound: 'BoundAction', *, name: str | None=None, agent: str | None=None, generation: UUID | None=None, presented: bool=False) -> ActionView
```

**Purpose**: Builds one public-facing ActionView from an internal bound action. It includes the action’s description, its expected input format, and a pre-filled call object that later code can use to invoke the right action on the right target.

**Data flow**: It receives the kind of target, a bound action, and optional details such as object name, agent name, generation ID, and whether portal presentation details should be included. It copies the action name and description, asks the action’s input model for its JSON schema, and creates a call dictionary with the target kind, action name, optional identifiers, and an empty input placeholder. If presentation details are requested, it also copies the user-facing label and confirmation text. It returns a new immutable ActionView.

**Call relations**: This is the builder used when a higher-level function has already decided an action should be shown. presented_action_views calls it for each eligible action so the rest of the system receives clean, ready-to-use action descriptions instead of raw internal action objects.

*Call graph*: called by 1 (presented_action_views); 1 external calls (__init__).


##### `presented`  (lines 56–58)

```
def presented(bound: 'BoundAction') -> bool
```

**Purpose**: Answers the simple question: should this bound action appear as a portal control? It is true only when the action has presentation information and is not marked as profile-only.

**Data flow**: It receives a bound action and reads two pieces of information from the underlying action: whether presentation settings exist, and whether the action is limited to profile use only. It combines those checks into one true-or-false result. It does not change anything.

**Call relations**: This is the shared visibility test for portal-facing actions. presented_action_views uses it before building visible action views, and frame_admissible_ids uses it before allowing bound actions to be callable from an embedded app page.

*Call graph*: called by 2 (frame_admissible_ids, presented_action_views).


##### `presented_action_views`  (lines 61–77)

```
def presented_action_views(actions: 'Mapping[str, Mapping[str, BoundAction]]', kind: str, binding: 'ActionBinding', *, name: str | None=None, generation: UUID | None=None) -> tuple[ActionView, ...]
```

**Purpose**: Selects the actions for one target that should be shown in the portal and turns them into ActionView objects. It also keeps the output in a stable order by sorting the actions by their short name.

**Data flow**: It receives a nested collection of actions, the target kind to look under, the required binding type, and optional target name and generation ID. It looks only at actions for that kind, sorts them, filters out actions that are not bound correctly, do not match the requested target name, or are not meant to be presented. For each remaining action, it calls action_view with presentation enabled. It returns a tuple of ActionView objects.

**Call relations**: This function is the main projection step for portal controls. It uses presented as the gatekeeper for visibility and action_view as the formatter that turns each accepted internal action into the public shape other code can display or offer for invocation.

*Call graph*: calls 2 internal fn (action_view, presented).


##### `frame_admissible_ids`  (lines 80–97)

```
def frame_admissible_ids(tools: 'Iterable[ToolDef]', actions: 'Mapping[str, Mapping[str, BoundAction]]') -> tuple[str, ...]
```

**Purpose**: Collects the action IDs that an embedded app page is allowed to call. This is a safety boundary: only tools and presented actions explicitly marked for frame use are included.

**Data flow**: It receives global tool definitions and bound object actions. From tools, it keeps unbound tools that have presentation settings and are marked as frame-capable. From object actions, it keeps actions that are presented and whose presentation settings allow frame use, then reads their canonical IDs. It removes duplicates, sorts the result, and returns the IDs as a tuple of strings.

**Call relations**: This function prepares the allow-list for embedded pages. It calls presented so bound actions follow the same portal visibility rule used elsewhere, then adds the extra requirement that the action or tool must be allowed inside a frame.

*Call graph*: calls 1 internal fn (presented).


### Scheduled task visibility
Scheduled task visibility logic decides which members may read protected task prompts and descriptions.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/visibility.py`

`domain_logic` · `request handling`

Scheduled tasks can contain private instructions or descriptions, so the system needs a simple rule for who may read them. This file provides that rule. The key idea is that a task is only as private as the conversation or audience it reports into. If the task reports into a shared workspace place, everyone may read its content. If it reports into one member’s private place, only that member may read it. If the caller is not known to be any member, they only get access to tasks aimed at shared places.

There is one important extra rule: when a task is not reporting to a shared audience and not directly aimed at the current member, the creator can still read it. This means a member can see a task they created, even if the audience comparison does not already grant access. However, a missing creator does not make the task public. The file deliberately bases the decision first on the reporting audience, not on assumptions about who created the task.

In everyday terms, this is like checking whether a note is posted on the office bulletin board, placed in someone’s personal mailbox, or written by the person asking to read it.

#### Function details

##### `task_content_visible`  (lines 7–20)

```
def task_content_visible(listed: ListedTask, member_id: UUID | None) -> bool
```

**Purpose**: Decides whether the given member may read a scheduled task’s prompt and description. It is used wherever the system needs to show or hide task content safely.

**Data flow**: It receives a listed scheduled task and either a member ID or no member ID. It first checks whether the task’s audience is shared by the workspace; if so, it returns true. If there is no member ID and the task is not shared, it returns false. Otherwise, it checks whether the task’s audience is exactly that member’s own subject, and if not, it finally checks whether that member created the task. The output is a simple yes-or-no answer, and the function does not change any data.

**Call relations**: When another part of the scheduled-task system needs to decide whether to reveal task content, it calls this function. This function asks `ufo.sdk.subjects.subject_shared` whether the audience is shared, and uses `ufo.sdk.subjects.member_subject` to build the private audience value for the current member so it can compare it with the task’s audience.

*Call graph*: 2 external calls (member_subject, subject_shared).

## 📊 State Registers Touched

- `reg-extension-catalog` — The installed extension and pack catalog that says which extra tools, routes, agents, skills, jobs, and backends are available.
- `reg-workspace-records` — The saved workspace records that identify each customer space and hold its limits, setup state, balance settings, and routing boundaries.
- `reg-member-identity` — The shared record of who each user is, how they logged in, what workspace they belong to, and what timezone or invitation state is known.
- `reg-auth-tokens` — The signed login, surface, artifact, and SDK tokens used to prove that a caller or link is allowed to act.
- `reg-agent-registry` — The durable list of agents, including their names, visibility, owners, purposes, model behavior, provisioning source, and tool policy.
- `reg-credentials-and-grants` — The encrypted secrets, account connections, and grants that say which member or agent may use an outside service.
- `reg-tool-catalog-policy` — The current tool catalog and allowlist rules that say which built-in, extension, connector, MCP, and sandbox tools may be called.
- `reg-feature-flags` — The workspace feature switches that let the system turn capabilities on or off without changing the code.
- `reg-object-store` — The workspace object records and change journal for agents, members, files, credentials, sites, connectors, memory records, reports, and extension objects.
- `reg-audience-visibility` — The saved visibility and audience rules that decide who may see a conversation, transcript, agent, source, artifact, or object.
- `reg-artifact-blob-store` — The shared file, blob, attachment, artifact, signed download, and media-preview storage used to publish and recover produced work.
- `reg-source-page-sync-state` — The source and page records that remember connected feeds, cursors, backoff, deletes, ownership, grants, and the latest synced content.
- `reg-hosted-site-registry` — The hosted-site records that remember who owns each site, which conversation created it, where it runs, and how previews or sharing are allowed.
- `reg-prompt-and-delivery-policy` — The prompt, delivery-rule, compaction, and prompt-change proposal state that controls what instructions are rendered and how replies should be shaped.
- `reg-extension-data-store` — The per-workspace extension storage area where optional features save their own small durable JSON state.
- `reg-request-actor-scope` — Context-local current workspace, member, acting agent, and object/action scope carried through authorization, database boundaries, object APIs, tools, and egress checks.
- `reg-agent-setup-state` — Durable setup checklist and progress state for provisioned agents, including required account links, credentials, schedules, and extension-specific onboarding needs.
- `reg-membership-access-policy` — Durable workspace membership, owner/admin role, seat, invitation, and mutation-permission state used to decide what a member may manage beyond simple object visibility.
- `reg-security-audit-log` — Durable audit records for sensitive reads and administrative/object changes, such as transcript access and object-change journaling.
- `reg-object-kind-action-registry` — Process-local registry of built-in and extension object kinds, schemas, actions, visibility rules, and handlers used by the portal object APIs.
- `reg-conversation-slot-provider-registry` — Registered providers that summarize and read extension conversation slots such as artifacts, sources, sites, automations, and task panels.

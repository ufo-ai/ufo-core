# Artifacts, Media Rendering, and Shared Outputs  `stage-15`

This stage is shared behind-the-scenes support for anything the system produces as a file or preview. When a conversation turn creates a file, the artifact object defines how that file can be listed, inspected, downloaded, copied back into a workspace, or deleted. It also blocks direct creation, so shared files must come through the approved sharing path.

Download safety is handled by signed links, which are web links with a built-in tamper check and expiry time. The artifact URL code verifies that each link still points to a real, allowed artifact. Media rendering turns files and sites into useful previews. The document renderer sends document bytes to an outside preview service and gets back text and page images, while checking file size, response shape, and bundle safety. If a preview was missed, the preview renderer retries later. Image preview checks make sure accepted preview images are the promised type and safe to process, and the preview data model records where previews are stored. Site previewing captures sandboxed website screenshots, and share cards combine those screenshots with branding for public links. Package marker files simply make these modules importable.

## Files in this stage

### Signed artifact links
Creates and verifies expiring artifact download URLs before handing off preview validation to media helpers.

### `core/src/ufo/media/artifact_url.py`

`domain_logic` · `artifact link minting and download request verification`

Artifacts are files saved under a workspace, such as shared documents, patches, or images. This file is the gatekeeper for the special download URLs that let someone fetch those files. The URL itself contains the artifact address, an expiry time, the workspace it belongs to, and a signature. The signature is like a tamper-evident seal: if someone changes the artifact id, expiry, workspace, or preview permission, the check fails and no file is served.

The file also keeps URLs cache-friendly. Instead of giving every new link a different expiry second, it rounds expiry times into one-hour buckets. That means repeated links to the same artifact can be identical for a while, so browsers and edge caches can reuse them.

For images, the file can add a preview claim. That claim says the file may be shown inline, and records the expected image type and byte size. Later, the download route can compare the claim with the actual bytes before serving it as a preview.

Older links without a workspace claim are treated carefully. Even if their signature is valid, they are considered expired for direct serving. A signed-in workspace member must refresh them first, so anonymous old links do not bypass workspace scoping.

#### Function details

##### `artifact_url_expiry`  (lines 59–66)

```
def artifact_url_expiry(now: datetime) -> int
```

**Purpose**: Chooses the expiry time that new artifact URLs should carry. It rounds time up into a shared bucket so repeated links can stay byte-for-byte the same and caches can reuse them.

**Data flow**: It receives the current time. It converts that time to seconds, adds the configured lifetime, then rounds up to the next bucket boundary. It returns that future expiry as an integer timestamp.

**Call relations**: When an image preview URL is being made, mint_image_preview_url asks this function for the expiry to place in the signed link. The result is then passed into mint_artifact_url so the same expiry is covered by the signature.

*Call graph*: called by 1 (mint_image_preview_url); 1 external calls (timestamp).


##### `ArtifactUrlExpired.__init__`  (lines 88–90)

```
def __init__(self, claims: ArtifactClaims) -> None
```

**Purpose**: Creates the special error used when a URL is authentic but no longer directly usable. It keeps the already-verified claims so another trusted path, such as a logged-in refresh, can decide what to do next.

**Data flow**: It receives verified artifact claims. It builds an error message saying the URL is expired, then stores those claims on the exception object. Nothing is returned, but the exception now carries the useful artifact details.

**Call relations**: verify_artifact_url calls this when the signature checks out but the URL is expired, or when the URL is an older form without a workspace claim. That lets callers distinguish 'bad or tampered link' from 'valid link that needs member refresh.'

*Call graph*: called by 1 (verify_artifact_url).


##### `artifact_media_type`  (lines 93–105)

```
def artifact_media_type(filename: str) -> str
```

**Purpose**: Decides what media type, also called a MIME type, should be used when serving an artifact file. This tells browsers whether bytes are a patch, spreadsheet, image, generic download, and so on.

**Data flow**: It receives a filename. It first checks a project-owned table for important suffixes whose type must be stable across machines. If no table entry exists, it asks Python's mimetype helper, but only accepts the answer when the filename is not also marked as compressed. It returns a media type string, or a safe generic fallback.

**Call relations**: This function stands apart as the file-type decision helper for artifact serving. It uses standard filename parsing and mimetype guessing, but shields the rest of the system from host-specific or misleading guesses.

*Call graph*: 2 external calls (guess_type, PurePosixPath).


##### `mint_artifact_url`  (lines 108–131)

```
def mint_artifact_url(secret: str, blob_key: str, expires_at: int, *, workspace_id: UUID, preview: ImagePreviewGrant | None=None) -> str
```

**Purpose**: Builds a signed relative download URL for one stored artifact. Someone uses it when they want to give a browser a link that can fetch a file without exposing broader storage access.

**Data flow**: It receives the signing secret, the stored blob key, an expiry timestamp, the owning workspace id, and optionally an image preview grant. It checks that the secret exists, splits and validates the artifact key, formats any preview claim, signs the workspace, artifact id, expiry, and preview text, then returns a URL path with query parameters for the grant and signature.

**Call relations**: mint_image_preview_url calls this after it has decided an image is eligible for inline preview. Internally this function relies on _split_key to make sure the blob key is really an artifact address, _parsed_preview to reject invalid preview claims, _signed_message to build the exact signed text, and sign_detached to create the tamper-proof seal.

*Call graph*: calls 3 internal fn (_parsed_preview, _signed_message, _split_key); called by 1 (mint_image_preview_url); 3 external calls (__init__, sign_detached, quote).


##### `mint_image_preview_url`  (lines 134–162)

```
def mint_image_preview_url(secret: str, public_base_url: str | None, blob_key: str, size_bytes: int | None, *, workspace_id: UUID) -> str | None
```

**Purpose**: Creates an absolute signed URL for showing a stored raster image inline, or returns nothing if the image should not be previewed. A raster image is a pixel-based image such as PNG, JPEG, or GIF.

**Data flow**: It receives the signing secret, the public base URL, the blob key, the file size, and the workspace id. It first refuses to continue if signing or public delivery is not configured. It then infers the image type from the blob key, checks that the size is known and small enough, chooses a rounded expiry time, asks mint_artifact_url to sign the preview permission, and prefixes the public base URL. The output is a full URL string or None.

**Call relations**: This is the convenience path for preview producers. It calls raster_image_media_type to see whether the artifact is a supported image, artifact_url_expiry to choose a cache-friendly expiry, creates an ImagePreviewGrant, and hands the final signing work to mint_artifact_url.

*Call graph*: calls 2 internal fn (artifact_url_expiry, mint_artifact_url); 3 external calls (__init__, now, raster_image_media_type).


##### `verify_artifact_url`  (lines 165–206)

```
def verify_artifact_url(secret: str, artifact_id: str, filename: str, expires_at: str, signature: str, preview: str, workspace: str, now: datetime) -> ArtifactClaims
```

**Purpose**: Checks whether an incoming artifact download URL is valid and returns the claims it proves. It rejects malformed, tampered, expired, or unsafe links before any file bytes are served.

**Data flow**: It receives the secret, URL pieces such as artifact id, filename, expiry, signature, preview claim, workspace claim, and the current time. It validates the shapes of the id, filename, expiry, and workspace; parses the optional preview claim; rebuilds the exact signed message; and checks the signature. If everything is authentic, it creates ArtifactClaims containing the workspace, blob key, filename, expiry, and preview data. If the link is expired or lacks a workspace claim, it raises ArtifactUrlExpired with those claims; otherwise it returns the claims.

**Call relations**: The artifact download route would call this before reading storage. It uses _is_canonical_uuid and _is_filename for safe URL shape checks, _parsed_preview for preview permissions, _signed_message so verification signs the same text that minting used, and ArtifactUrlExpired.__init__ when a valid link must take the refresh path instead of direct serving.

*Call graph*: calls 5 internal fn (__init__, _is_canonical_uuid, _is_filename, _parsed_preview, _signed_message); 5 external calls (__init__, __init__, timestamp, verify_detached, UUID).


##### `_signed_message`  (lines 209–211)

```
def _signed_message(workspace: str, artifact_id: str, expires_at: str, preview_value: str) -> bytes
```

**Purpose**: Builds the exact byte string that is signed when a URL is minted and checked when a URL is verified. Its job is to make both sides agree on what the tamper-evident seal covers.

**Data flow**: It receives the workspace text, artifact id, expiry text, and preview text. If a workspace is present, it includes it at the front; otherwise it leaves that part out for older claim-less links. It joins the fields with colons and returns the encoded bytes.

**Call relations**: mint_artifact_url calls this before creating a signature, and verify_artifact_url calls it before checking a signature. Because both paths use the same helper, they cannot accidentally sign slightly different message formats.

*Call graph*: called by 2 (mint_artifact_url, verify_artifact_url).


##### `_parsed_preview`  (lines 214–225)

```
def _parsed_preview(value: str) -> ImagePreviewGrant | None
```

**Purpose**: Checks and decodes the preview permission carried in a URL. It ensures the claim describes a supported raster image type and a safe byte size.

**Data flow**: It receives preview text in the form 'media-type:size'. It splits the text at the last colon, checks that the media type is one of the allowed raster image types, checks that the size is numeric and below the configured maximum, and returns an ImagePreviewGrant. If anything is wrong, it returns None.

**Call relations**: mint_artifact_url uses this as a safety check before signing a preview claim. verify_artifact_url uses it when reading a request, so a forged or malformed preview instruction is rejected even before storage bytes are considered.

*Call graph*: called by 2 (mint_artifact_url, verify_artifact_url); 3 external calls (__init__, cast, values).


##### `_split_key`  (lines 228–237)

```
def _split_key(blob_key: str) -> tuple[str, str]
```

**Purpose**: Validates and separates an artifact blob key into its artifact id and filename. It prevents callers from signing links to paths outside the artifact storage area.

**Data flow**: It receives a blob key string. It removes the expected 'artifacts/' prefix, splits the remaining text into an artifact id and filename, then checks that the prefix was present, the id is a canonical UUID, and the filename is a simple single path segment. It returns the artifact id and filename, or raises an artifact URL error if the key is unsafe.

**Call relations**: mint_artifact_url calls this before signing any link. It depends on _is_canonical_uuid and _is_filename for the detailed checks, so only well-formed artifact addresses can become signed download URLs.

*Call graph*: calls 2 internal fn (_is_canonical_uuid, _is_filename); called by 1 (mint_artifact_url); 1 external calls (__init__).


##### `_is_canonical_uuid`  (lines 240–244)

```
def _is_canonical_uuid(value: str) -> bool
```

**Purpose**: Checks whether a string is exactly a normal UUID spelling. A UUID is a standard unique identifier, and this check avoids accepting unusual or ambiguous forms.

**Data flow**: It receives a string. It tries to parse it as a UUID and then converts it back to the standard string form. It returns true only if that standard form exactly matches the input; invalid UUID text returns false.

**Call relations**: _split_key uses this to validate artifact ids before signing links. verify_artifact_url uses it to validate artifact and workspace ids before trusting a request.

*Call graph*: called by 2 (_split_key, verify_artifact_url); 1 external calls (UUID).


##### `_is_filename`  (lines 247–248)

```
def _is_filename(value: str) -> bool
```

**Purpose**: Checks whether a filename is safe to use as the final part of an artifact path. It blocks empty names, nested paths, and special directory names.

**Data flow**: It receives a filename string. It verifies that the name is not empty, does not contain a slash, and is not '.' or '..'. It returns true for a plain filename and false for anything that could behave like a path trick.

**Call relations**: _split_key uses this before a stored blob key can be signed. verify_artifact_url uses it on incoming URL filenames so a request cannot construct a path outside the intended artifact file.

*Call graph*: called by 2 (_split_key, verify_artifact_url).


### Sandbox site screenshots
Captures a running sandbox website through the preview service and stores the resulting PNG as a reusable artifact.

### `core/src/ufo/media/site_previewer.py`

`domain_logic` · `request handling`

This file is the bridge between a live sandboxed website and a saved preview image. Imagine a user has built or opened a small web app inside an isolated workspace. The rest of the system needs a thumbnail-like picture of that site, but it should not take the screenshot itself. Instead, SitePreviewer prepares a safe public viewing URL for the sandbox, asks an external service called ufo-preview to render that URL, and then records where the image was stored.

The file is careful about trust and size. It checks that the requested output name is only a simple filename, that the requested image dimensions are reasonable, and that responses do not grow beyond fixed byte limits. If the blob storage backend is S3, the preview service can upload the image directly using a temporary upload link. Otherwise, the service sends the PNG bytes back inline and this code writes them into workspace storage.

It also verifies the result before accepting it: direct uploads must return valid metadata, inline responses must be PNG images, and the width and height must match what was requested. If anything goes wrong, it logs a short failure record and returns no preview instead of crashing the larger flow.

#### Function details

##### `SitePreviewer.render`  (lines 47–124)

```
async def render(self, conversation_id: UUID, port: int, name: str, width: int, height: int) -> StoredPreview | None
```

**Purpose**: This asynchronous function tries to make one PNG preview of a sandboxed website port. It returns a StoredPreview pointing to the saved image when successful, or None when the preview cannot be drawn safely.

**Data flow**: It receives a conversation ID, a port number, a desired filename, and target width and height. It validates the filename and dimensions, builds a public sandbox viewing URL from the current workspace and conversation, chooses whether the preview service should upload directly to blob storage or return image bytes, then sends a POST request to the preview service. If the service uploads directly, it reads and checks returned metadata before producing a StoredPreview. If the image comes back inline, it checks the content type, PNG signature, and dimensions, writes the bytes into blob storage, and then returns a StoredPreview. If the HTTP request fails, the response is too large, the data is invalid, or the service reports an error, it logs the problem and returns None.

**Call relations**: When some higher-level part of the system needs a site preview, it calls this method as the main worker. The method asks ws_current for the active workspace, uses mint_ingress_view_url to create a browser-accessible sandbox URL, uses json.dumps to package the render request, and uses httpx.AsyncClient with an httpx.Timeout to talk to the preview service. On success it hands back StoredPreview so later code can refer to the stored artifact; on failure it calls ufo.o11y.log so operators have a short record of why no preview was produced.

*Call graph*: 9 external calls (__init__, AsyncClient, Timeout, dumps, PurePosixPath, log, mint_ingress_view_url, ws_current, uuid4).


### Artifact object APIs
Defines the built-in artifact object surface for listing, inspecting, downloading, copying, and deleting shared turn outputs.

### `core/src/ufo/kinds/artifacts.py`

`domain_logic` · `request handling for artifact object list/get/status/delete operations`

An artifact is like a file placed on a shared shelf after a conversation turn finishes. This file is the shelf's rulebook. It decides which files are visible, what each file is called, how versions work, and how the bytes can be fetched again.

The key idea is that one artifact is identified by both the conversation that shared it and the filename. If the same conversation shares the same filename again, that becomes a new version of the same artifact. If another conversation shares a file with the same filename, it is a separate artifact. To make this readable, the file builds names from a short conversation prefix plus a cleaned-up filename, adding a short digest only when names would collide.

Most work happens through ArtifactObjects. Listing gathers recent visible shares from the database, groups them by conversation and filename, adds human-friendly fields, and may attach signed download or image-preview links. Getting an artifact returns its latest description. Status is more active: it can fetch the stored bytes from the blob store and write them back into the current workspace so a later turn can reuse the file. Delete removes all versions from the database and then deletes their stored bytes. Direct create and update are rejected so there is only one way to produce artifacts: write a workspace file and call share_file.

#### Function details

##### `artifact_media`  (lines 79–87)

```
def artifact_media(media_type: str) -> str
```

**Purpose**: Sorts a file's detailed media type into a broad category: image, document, or other. This gives listings a simple field people can filter on without knowing every possible MIME type, which is the standard label browsers and systems use for file formats.

**Data flow**: It receives a media type string such as image/png or application/pdf. It lowercases it, checks whether it looks like an image or a known document type, and returns one short category string.

**Call relations**: ArtifactObjects._row uses this when building each listing row, so the portal and object filters can show and filter artifacts by a simple media category.

*Call graph*: called by 1 (_row).


##### `_document_media`  (lines 90–95)

```
def _document_media() -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database test for deciding whether a shared artifact should count as a document. It exists so document filtering can happen inside the database query instead of after loading unnecessary rows.

**Data flow**: It reads the shared_artifact media_type column through SQLAlchemy, a library for building database queries in Python. It produces a yes-or-no database expression that matches text files, common Office document types, PDFs, and Word documents.

**Call relations**: ArtifactObjects._groups calls this when a listing asks for media=document, or when it needs to exclude documents while finding media=other.

*Call graph*: called by 1 (_groups); 1 external calls (or_).


##### `_member_participated`  (lines 98–110)

```
def _member_participated() -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database rule that says a member should only see artifacts shared after a member had entered that conversation. This protects files from earlier hidden parts of a conversation.

**Data flow**: It compares turns in the same workspace and conversation. It returns a database existence check that is true when there is a member-admission turn at or before the artifact's turn.

**Call relations**: ArtifactObjects._member_shares adds this rule to the normal artifact query before member-facing pages and details are read.

*Call graph*: called by 1 (_member_shares); 2 external calls (literal, select).


##### `artifact_object_names`  (lines 113–133)

```
def artifact_object_names(shares: Iterable[tuple[UUID, str]]) -> dict[tuple[UUID, str], str]
```

**Purpose**: Creates stable, readable object names for artifact identities. The name combines a short conversation identifier with a cleaned filename, so files from the same session cluster together and same-named files from different sessions stay separate.

**Data flow**: It receives pairs of conversation ID and filename. It turns each filename into a slug, prefixes it with the conversation ID's first hex characters, counts duplicate names, and adds a short digest only where two different identities would otherwise get the same name.

**Call relations**: ArtifactObjects._identities calls this after reading all visible conversation-and-filename pairs from the database. It relies on _slug for readable filename cleanup and _identity_digest for collision-proof suffixes.

*Call graph*: calls 2 internal fn (_identity_digest, _slug); called by 1 (_identities); 1 external calls (Counter).


##### `_slug`  (lines 136–138)

```
def _slug(filename: str) -> str
```

**Purpose**: Turns a filename into a short safe name piece. It removes awkward characters so artifact object names are readable and URL-friendly.

**Data flow**: It receives a filename, lowercases it, replaces runs of non-letter-or-number characters with hyphens, trims the result to a maximum length, and falls back to artifact if nothing usable remains.

**Call relations**: artifact_object_names calls this while building the base name for every artifact identity.

*Call graph*: called by 1 (artifact_object_names).


##### `_identity_digest`  (lines 141–143)

```
def _identity_digest(identity: tuple[UUID, str]) -> str
```

**Purpose**: Creates a stable fingerprint for a conversation-and-filename identity. This is used only when two different artifacts would otherwise receive the same readable name.

**Data flow**: It receives a conversation ID and filename, joins them into a string, hashes that string with SHA-256, and returns the hexadecimal hash text.

**Call relations**: artifact_object_names calls this to append a short collision suffix when readable names are not unique.

*Call graph*: called by 1 (artifact_object_names); 1 external calls (sha256).


##### `ArtifactObjects.list`  (lines 168–175)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists artifact objects visible to the current tool context. This is the normal agent-facing listing path.

**Data flow**: It reads the caller's allowed subjects and member identity from the ToolContext, builds the base share query with _shares, gathers formatted rows through _rows, and returns a paged result using object_page.

**Call relations**: The object system calls this when an agent lists artifacts. It delegates the real query and row-building work to _shares and _rows, then hands the rows to the shared pagination helper.

*Call graph*: calls 2 internal fn (_rows, _shares); 1 external calls (object_page).


##### `ArtifactObjects.member_page`  (lines 177–196)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Builds the signed-in member's Artifacts index. It shows only files inside that member's audience reach and only after member participation rules allow them.

**Data flow**: It receives a member ID, admin flag, and list query. It derives the subjects that member is allowed to see, narrows shares with _member_shares, converts them into rows with _rows, and returns a paged object page.

**Call relations**: The member-facing surface calls this for the Artifacts page. It uses conversation_audience and audience_subjects to build the visibility fence, then follows the same row and pagination path as list.

*Call graph*: calls 2 internal fn (_member_shares, _rows); 3 external calls (object_page, audience_subjects, conversation_audience).


##### `ArtifactObjects.get`  (lines 198–200)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ArtifactSpec] | None
```

**Purpose**: Finds one artifact by name for the current agent context and returns its detailed metadata. It does not copy bytes into the workspace; status is the operation that does that.

**Data flow**: It receives a ToolContext and artifact name. It searches visible shares with _find, and if found, turns the full version group into an ObjectDetail with _detail; otherwise it returns null.

**Call relations**: The object system calls this for object_get detail lookup. It uses _shares as the visibility-aware source and hands found rows to _detail.

*Call graph*: calls 3 internal fn (_find, _shares, _detail).


##### `ArtifactObjects.member_detail`  (lines 202–219)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[ArtifactSpec] | None
```

**Purpose**: Returns one member-visible artifact with both listing-row information and detailed metadata. It is the detail version of the member Artifacts index.

**Data flow**: It receives an artifact name and member identity. It derives that member's audience subjects, searches member-visible shares with _find and _member_shares, reads source links with _sources, builds a row with _row, builds detail with _detail, and wraps both in a MemberObject.

**Call relations**: The member-facing surface calls this when opening a single artifact. It follows the same audience rules as member_page, then combines _row and _detail so the UI has both summary fields and exact spec information.

*Call graph*: calls 5 internal fn (_find, _member_shares, _row, _sources, _detail); 3 external calls (__init__, audience_subjects, conversation_audience).


##### `ArtifactObjects.status`  (lines 221–265)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports the current state of an artifact and, when small enough, copies its latest bytes back into the workspace. This is how a later turn can reuse a file produced by an earlier turn.

**Data flow**: It receives the current context, artifact name, and optional expected generation. It finds the latest visible share, downloads bytes from the blob store if the file is within the materialization size limit, checks in the database that the conversation is still visible and unchanged, writes the file under artifacts/<name>/<filename> when bytes were fetched, optionally mints a fresh signed download link, and returns size, share time, turn ID, version count, URL, and workspace path.

**Call relations**: The object seam calls status during object_get. It uses _find and _shares to locate the artifact, _unchanged_visible to guard against visibility changes, the blob store to fetch bytes, and artifact URL helpers to create a temporary download link.

*Call graph*: calls 3 internal fn (_find, _shares, _unchanged_visible); 6 external calls (__init__, now, workspace_tx, artifact_url_expiry, mint_artifact_url, ws_current).


##### `ArtifactObjects.apply`  (lines 267–276)

```
async def apply(self, ctx: ToolContext, name: str, spec: ArtifactSpec, old: ArtifactSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects create and update attempts for artifacts. Artifacts are intentionally produced only by sharing a workspace file, not by editing the object record directly.

**Data flow**: It receives the proposed artifact spec and related object-write inputs, ignores them for creation purposes, and raises VerbNotSupported with guidance to use share_file instead.

**Call relations**: The object system would call this for create or update verbs. Instead of handing off work, it stops the flow immediately so share_file remains the only producer.

*Call graph*: 1 external calls (__init__).


##### `ArtifactObjects.delete`  (lines 278–304)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Deletes an artifact and every stored version of it. After this, old signed links stop working because the underlying blobs are gone.

**Data flow**: It receives a context and artifact name. It finds all visible versions, locks and rechecks that the latest artifact is still visible and unchanged, deletes the matching database rows, verifies the expected number of versions disappeared, and then deletes each version's main blob and any preview blob from storage.

**Call relations**: The object system calls this for artifact deletion. It uses _find and _shares to identify the version group, _unchanged_visible to protect against races, the database transaction to remove rows, and the blob store to remove bytes afterward.

*Call graph*: calls 3 internal fn (_find, _shares, _unchanged_visible); 3 external calls (delete, workspace_tx, ws_current).


##### `ArtifactObjects._unchanged_visible`  (lines 306–313)

```
def _unchanged_visible(self, ctx: ToolContext, latest: sa.Row) -> sa.Select
```

**Purpose**: Builds a database check that the artifact's conversation still matches the visibility facts used when it was found. This helps avoid acting on a file after permissions or ownership have changed.

**Data flow**: It receives the current context and the latest share row. It creates a query that looks for the same conversation in the current workspace, same selected agent, same audience, and an audience still allowed by the caller.

**Call relations**: ArtifactObjects.status and ArtifactObjects.delete run this check before copying or deleting. It is a safety gate between finding an artifact and taking an action based on it.

*Call graph*: called by 2 (delete, status); 3 external calls (select, object_agent_id, ws_current).


##### `ArtifactObjects._rows`  (lines 315–326)

```
async def _rows(self, subjects: frozenset[str], viewer: UUID | None, query: ObjectListQuery, shares: sa.Select) -> tuple[ObjectRow, ...]
```

**Purpose**: Turns visible artifact shares into listing rows. It is the shared path used by both agent listings and member-facing pages.

**Data flow**: It receives allowed subjects, an optional viewer ID, a list query, and a base share query. It asks _groups to collect shares by artifact identity, reads conversation source information with _sources, then converts each group into an ObjectRow with _row.

**Call relations**: ArtifactObjects.list and ArtifactObjects.member_page call this after choosing the right share query. It coordinates grouping, source lookup, and row formatting.

*Call graph*: calls 3 internal fn (_groups, _row, _sources); called by 2 (list, member_page).


##### `ArtifactObjects._find`  (lines 328–348)

```
async def _find(self, subjects: frozenset[str], name: str, shares: sa.Select) -> tuple[sa.Row, ...] | None
```

**Purpose**: Resolves one artifact name to all of its visible share versions. Unlike listing, it is not limited to the most recent scan window, so a known name can still be opened even if it is old.

**Data flow**: It receives allowed subjects, an object name, and a share query. It reads all visible identities and their generated names, finds the identity matching the requested name, queries rows for that conversation and filename, and returns them sorted newest first, or null if none match.

**Call relations**: ArtifactObjects.get, member_detail, status, and delete all use this before doing their specific work. It calls _identities so the same naming rules apply everywhere.

*Call graph*: calls 1 internal fn (_identities); called by 4 (delete, get, member_detail, status); 2 external calls (where, workspace_tx).


##### `ArtifactObjects._groups`  (lines 350–418)

```
async def _groups(self, subjects: frozenset[str], viewer: UUID | None, query: ObjectListQuery, shares: sa.Select) -> Sequence[tuple[str, tuple[sa.Row, ...]]]
```

**Purpose**: Builds artifact groups for a listing. It applies search and filters, reads a bounded set of matching shares, and groups versions of the same conversation-and-filename artifact together.

**Data flow**: It receives allowed subjects, optional viewer ID, the user's list query, and a base share query. It narrows the database query for text search, conversation ID, mine=true, and media category; reads up to the configured scan limit ordered by newest share; groups rows by conversation and filename; looks up stable names through _identities; and returns sorted name-and-version groups.

**Call relations**: ArtifactObjects._rows calls this as the first step in listing. It uses _document_media for document filtering and _identities so displayed names match names used by direct lookup.

*Call graph*: calls 2 internal fn (_identities, _document_media); called by 1 (_rows); 5 external calls (false, not_, or_, workspace_tx, UUID).


##### `ArtifactObjects._identities`  (lines 420–443)

```
async def _identities(self, subjects: frozenset[str]) -> dict[tuple[UUID, str], str]
```

**Purpose**: Finds every distinct visible artifact identity and assigns each its stable object name. This keeps names consistent whether an artifact appears in a listing or is opened directly.

**Data flow**: It receives allowed audience subjects. It queries the database for distinct conversation ID and filename pairs in the current workspace, selected agent, and permitted audiences, then passes those pairs to artifact_object_names.

**Call relations**: ArtifactObjects._find uses this to translate a requested name back to an identity. ArtifactObjects._groups uses it to label grouped listing results.

*Call graph*: calls 1 internal fn (artifact_object_names); called by 2 (_find, _groups); 4 external calls (select, workspace_tx, object_agent_id, ws_current).


##### `ArtifactObjects._shares`  (lines 445–480)

```
def _shares(self, subjects: frozenset[str]) -> sa.Select
```

**Purpose**: Builds the base database query for artifact shares visible to the current agent and audience. It includes both file facts and conversation/member facts needed for listings and details.

**Data flow**: It receives allowed subjects. It creates a SQL query joining shared artifacts to their turn, conversation, and optional owning member, limited to the current workspace, selected agent, and permitted conversation audiences.

**Call relations**: ArtifactObjects.list, get, status, delete, and _member_shares all start from this query. Later helpers add filters, grouping, or member-specific rules on top of it.

*Call graph*: called by 5 (_member_shares, delete, get, list, status); 3 external calls (select, object_agent_id, ws_current).


##### `ArtifactObjects._member_shares`  (lines 482–483)

```
def _member_shares(self, subjects: frozenset[str]) -> sa.Select
```

**Purpose**: Creates the member-safe version of the artifact share query. It adds the rule that the member must have participated by the time the artifact was shared.

**Data flow**: It receives allowed subjects, builds the normal visible-share query with _shares, adds the _member_participated database condition, and returns the narrowed query.

**Call relations**: ArtifactObjects.member_page and member_detail call this for member-facing reads, so those paths inherit the normal visibility rules plus the member participation fence.

*Call graph*: calls 2 internal fn (_shares, _member_participated); called by 2 (member_detail, member_page).


##### `ArtifactObjects._sources`  (lines 485–522)

```
async def _sources(self, conversation_ids: Sequence[UUID]) -> dict[UUID, str | None]
```

**Purpose**: Looks up the opening source for conversations, such as the permalink or origin recorded on the first turn. This lets artifact rows show where the conversation came from.

**Data flow**: It receives conversation IDs. It finds the lowest-sequence turn for each conversation in the current workspace, reads that turn's stored context, validates it as a TurnContext, and returns a map from conversation ID to source string or null.

**Call relations**: ArtifactObjects._rows calls this while building listings, and member_detail calls it for a single artifact detail row. The resulting source values are passed into _row.

*Call graph*: called by 2 (_rows, member_detail); 5 external calls (model_validate, and_, select, workspace_tx, ws_current).


##### `ArtifactObjects._row`  (lines 524–552)

```
def _row(self, name: str, shares: tuple[sa.Row, ...], viewer: UUID | None, sources: dict[UUID, str | None]) -> ObjectRow
```

**Purpose**: Builds one listing row for an artifact group. It turns the latest version plus supporting facts into the fields the object listing and portal display.

**Data flow**: It receives the artifact name, all share versions sorted newest first, optional viewer ID, and conversation source map. It picks the latest share, creates a short summary, fills fields such as filename, subject, conversation, media category, owner email, origin, mine flag, source, and signed URLs, then returns an ObjectRow.

**Call relations**: ArtifactObjects._rows uses this for listing rows, and member_detail uses it for a member detail wrapper. It calls _summary, artifact_media, _download_url, and _preview_url to fill specific fields.

*Call graph*: calls 4 internal fn (_download_url, _preview_url, _summary, artifact_media); called by 2 (_rows, member_detail); 1 external calls (__init__).


##### `ArtifactObjects._download_url`  (lines 554–563)

```
def _download_url(self, latest: sa.Row) -> str | None
```

**Purpose**: Creates a temporary signed download URL for an artifact, when this deployment is configured to publish such links. A signed URL is a link containing proof that the download is allowed for a limited time.

**Data flow**: It receives the latest share row. If either the public base URL or token secret is missing, it returns null; otherwise it creates an expiring artifact path for the blob key and joins it to the public base URL.

**Call relations**: ArtifactObjects._row calls this while building listing and member rows so the UI can offer a download link when link minting is enabled.

*Call graph*: called by 1 (_row); 4 external calls (now, artifact_url_expiry, mint_artifact_url, ws_current).


##### `ArtifactObjects._preview_url`  (lines 565–585)

```
def _preview_url(self, latest: sa.Row) -> str | None
```

**Purpose**: Creates a signed image-preview URL when a safe raster image preview is available. Raster means a pixel-based image such as PNG or JPEG.

**Data flow**: It receives the latest share row. It chooses the preview blob if one exists, otherwise the artifact's own blob, checks that the blob key's image type agrees with the declared media type, and returns a signed preview URL; if the type check fails, it returns null.

**Call relations**: ArtifactObjects._row calls this to add preview_url to listing rows. It relies on raster_image_media_type to confirm eligibility and mint_image_preview_url to produce the final link.

*Call graph*: called by 1 (_row); 3 external calls (mint_image_preview_url, raster_image_media_type, ws_current).


##### `_detail`  (lines 588–604)

```
def _detail(shares: tuple[sa.Row, ...]) -> ObjectDetail[ArtifactSpec]
```

**Purpose**: Builds the detailed object view for an artifact. It reports the latest spec, creation and update times, and the conversation where the artifact was created.

**Data flow**: It receives all versions of one artifact, newest first. It takes filename, media type, and subject from the latest version, uses the oldest version as created_at, uses the latest version as updated_at, and adds a created_in link to the sharing conversation.

**Call relations**: ArtifactObjects.get and member_detail call this after _find has gathered the version group. It packages database rows into the standard ObjectDetail shape.

*Call graph*: called by 2 (get, member_detail); 4 external calls (__init__, __init__, __init__, __init__).


##### `_summary`  (lines 607–613)

```
def _summary(shares: tuple[sa.Row, ...]) -> str
```

**Purpose**: Creates a short human-readable sentence for a listing row. It gives enough information to recognize the file at a glance.

**Data flow**: It receives all versions of an artifact, newest first. It uses the latest filename, media type, size, and share date, adds a version count when there is more than one version, and trims the text to the summary length limit.

**Call relations**: ArtifactObjects._row calls this whenever it builds an ObjectRow for listings or member detail rows.

*Call graph*: called by 1 (_row).


##### `artifact_object`  (lines 616–679)

```
def artifact_object(*, public_base_url: str | None=None, artifact_token_secret: str='') -> ObjectKind
```

**Purpose**: Constructs and registers the artifact object kind definition. This tells the wider object system what artifacts are, which fields they expose, and which actions are allowed.

**Data flow**: It receives optional public URL and token secret settings. It creates an ArtifactObjects store configured for link minting, describes the artifact kind, names its spec model and list fields, and returns an ObjectKind that supports list, get, and delete for agents.

**Call relations**: This is called during object-kind setup by code outside this file. It ties the ArtifactObjects behavior to the object framework so artifact operations become available through the standard object interface.

*Call graph*: 2 external calls (__init__, __init__).


### Media preview rendering
Provides the media package, document rendering pipeline, image preview validation, retry rendering, and preview metadata shape.

### `core/src/ufo/media/__init__.py`

`other` · `import/package discovery`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may contain useful tools, but the label itself does not do the work. Here, the drawer is `ufo.media`, which likely groups code related to media features elsewhere in the project. Because this file is empty, importing `ufo.media` does not set up variables, run startup code, or expose helper functions directly. Its main value is structural: it gives the project a clean place to organize media-related modules and lets other files refer to them using normal Python import paths. Without this file, depending on the Python version and packaging setup, imports from this directory could be less reliable or fail in some environments.


### `core/src/ufo/media/document_renderer.py`

`io_transport` · `request handling`

This file is the bridge between a document file and the system’s file-viewing experience. A document such as a PDF or office file is not directly useful to many tools unless it can be shown page by page. `DocumentRenderer` sends the raw document to a separate rendering service called `ufo-preview`, then checks and repackages the answer into a simple result: page numbers, PNG images, page sizes, extracted text, and a reminder to inspect visual quality.

The file is careful about limits. It refuses input documents that are too large, asks for only a bounded number of pages, and rejects render results that are too big. This matters because rendering can create large images, and without these caps a single document could consume too much memory or bandwidth.

The service returns a zip file, like a small folder bundled into one file. Inside it must be a `manifest.json` describing the pages, plus one PNG image for each page. `_unpack` acts like a strict customs inspector: it checks that the manifest matches the request, that page numbers are sensible and consecutive, that every expected file is present and no surprise files are included, and that the images are really PNG files. Only then does it base64-encode the images, which turns binary image data into text that can be carried safely in JSON-like structures.

#### Function details

##### `DocumentRenderer.render`  (lines 60–107)

```
async def render(self, path: str, kind: str, content: bytes, start_page: int, limit: int) -> dict[str, object]
```

**Purpose**: Sends a bounded document to the preview service and asks it to render a selected page range. It is the public entry point for turning raw document bytes into a structured, page-by-page result.

**Data flow**: It receives a file path, document kind, raw bytes, a requested starting page, and a page limit. First it checks the input size, cleans up the requested page range, and builds an HTTP request with those settings. It streams the document to the rendering service, collects the returned zip bundle if the service succeeds, and stops with a clear error if the service fails or sends too much data. Finally it passes the downloaded bundle to `_unpack` in a worker thread and returns the cleaned result dictionary.

**Call relations**: This method is called when the system needs a document preview. It uses `httpx.AsyncClient` to talk to the external renderer, `json.dumps` to attach the render instructions, and `httpx.Timeout` to avoid waiting forever. Once the network part is done, it hands the zip bundle to `DocumentRenderer._unpack`, using `asyncio.to_thread` so the CPU and zip-file work does not block the async event loop.

*Call graph*: 4 external calls (to_thread, AsyncClient, Timeout, dumps).


##### `DocumentRenderer._unpack`  (lines 109–196)

```
def _unpack(self, path: str, kind: str, start_page: int, limit: int, bundle: bytes) -> dict[str, object]
```

**Purpose**: Checks and converts the render service’s zip bundle into the final page-preview data. It makes sure the service returned exactly the pages and files requested before exposing them to the rest of the system.

**Data flow**: It receives the original path and document kind, the requested page range, and the zip bundle bytes returned by the renderer. It opens the bundle, reads and validates `manifest.json`, checks page counts, page order, image sizes, file names, and PNG headers, then reads each page image. Each valid image is converted to base64 text, and any page text is gathered into one combined text string. It returns a dictionary containing document metadata, extracted text, page images, pagination information, and a quality reminder.

**Call relations**: This method is used by `DocumentRenderer.render` after the external service has returned a successful response. It relies on `io.BytesIO` and `zipfile.ZipFile` to read the zip bundle from memory, and on `base64.b64encode` to make each PNG image safe to include in the returned data structure. Its job is to be the safety and consistency checkpoint before the rendered pages are handed back to callers.

*Call graph*: 3 external calls (b64encode, BytesIO, ZipFile).


### `core/src/ufo/media/image_previews.py`

`domain_logic` · `request handling`

Image previews are convenient, but they are also a place where broken or hostile files can enter the system. This file acts like a careful doorman for raster images such as JPEG, PNG, GIF, and WebP. It first recognizes likely image types from file names, then validates uploaded preview bytes against a signed claim called an ImagePreviewGrant, which says what media type and byte size the preview is supposed to have.

The validation happens in layers. First, the streamed bytes are counted as they arrive, so the file cannot quietly be larger than promised. Then the full image is checked with Pillow, the Python image library. The code confirms the outer container looks complete, opens the image, verifies that Pillow agrees with the claimed type, and then actually walks through image frames. While doing that, it limits width, height, number of animation frames, and total decoded pixels. This matters because a tiny-looking image file can sometimes expand into a huge amount of memory when decoded, like a compressed suitcase that unfolds into a room full of furniture.

If anything is suspicious, incomplete, too large, or mislabeled, the file raises InvalidImagePreview instead of letting the preview continue through the system.

#### Function details

##### `raster_image_media_type`  (lines 43–44)

```
def raster_image_media_type(path: str) -> RasterImageMediaType | None
```

**Purpose**: This function guesses the raster image media type from a path or file name. It is useful when the system wants to know whether a file name looks like a supported image preview, such as .jpg or .png.

**Data flow**: It receives a path string. It looks only at the final file extension, lowercases it, and checks it against the known image suffixes. It returns the matching media type, such as image/jpeg, or returns nothing if the suffix is not supported.

**Call relations**: This is the lightweight first check before deeper image validation. It uses PurePosixPath to read the suffix in a consistent path-like way, but it does not open or inspect the file contents.

*Call graph*: 1 external calls (PurePosixPath).


##### `validated_image_preview`  (lines 47–61)

```
async def validated_image_preview(stream: AsyncIterator[bytes], grant: ImagePreviewGrant) -> bytes
```

**Purpose**: This async function reads an incoming image preview stream and accepts it only if it exactly matches its signed size claim and passes image safety checks. Someone would use it at the boundary where untrusted preview bytes enter the system.

**Data flow**: It receives an asynchronous stream of byte chunks and an ImagePreviewGrant containing the promised media type and byte count. It counts chunks as they arrive, rejects the preview if it is too large or does not match the promised size, joins the chunks into one byte string, and sends those bytes to the image validator in a worker thread. If all checks pass, it returns the original image bytes; if not, it raises InvalidImagePreview.

**Call relations**: This is the public validation path for streamed preview data. It raises InvalidImagePreview when the size claim is broken, then hands the fully collected bytes to _ImagePreviewValidator.validate through asyncio.to_thread so the blocking image work does not stall the async event loop.

*Call graph*: 2 external calls (__init__, to_thread).


##### `_ImagePreviewValidator.validate`  (lines 66–109)

```
def validate(data: bytes, media_type: RasterImageMediaType) -> None
```

**Purpose**: This function performs the serious image inspection. It checks that the bytes form a real, complete, supported image, that the claimed media type is truthful, and that decoding the image stays within safety limits.

**Data flow**: It receives raw image bytes and the media type the sender claimed. It first checks basic container endings or headers, then opens the bytes with Pillow, verifies the image structure, reopens it, walks through each frame, and counts dimensions and decoded pixels. If the image is valid and within limits, it returns nothing; if the image is malformed, dangerous, too large, or not actually the claimed type, it raises InvalidImagePreview.

**Call relations**: validated_image_preview calls this after the byte stream has been fully collected and size-checked. Inside, it delegates quick format-specific completeness checks to _ImagePreviewValidator._validate_container, uses Pillow to inspect and decode the image, and converts low-level image-library errors into the project’s own InvalidImagePreview error.

*Call graph*: 5 external calls (__init__, open, BytesIO, catch_warnings, simplefilter).


##### `_ImagePreviewValidator._validate_container`  (lines 112–125)

```
def _validate_container(data: bytes, media_type: RasterImageMediaType) -> None
```

**Purpose**: This function does quick, format-specific checks that an image file looks complete at the container level. It catches simple truncation cases before the more expensive image decoding step.

**Data flow**: It receives the raw bytes and the claimed media type. For JPEG, GIF, and PNG, it checks for the expected ending marker; for WebP, it checks the RIFF/WEBP header and stored length. If the container looks complete, it returns nothing; otherwise it raises InvalidImagePreview.

**Call relations**: _ImagePreviewValidator.validate calls this as its first line of defense. It does not replace full Pillow validation; it simply catches obvious incomplete files early so the later image checks start from a more trustworthy byte sequence.

*Call graph*: 1 external calls (__init__).


### `core/src/ufo/media/preview_renderer.py`

`domain_logic` · `scheduled background retry`

When someone shares a document, the system tries to make a small preview image right away. That first attempt is best-effort: if the separate preview service is briefly down, the file is still shared, but the database row is left without preview information. This file fills that gap.

Think of it like a follow-up clerk checking recent forms that are missing a thumbnail stamp. The `PreviewRenderer` looks for recent shared artifacts whose preview fields are still empty and whose filenames have a supported document suffix. It only looks back for a limited time, so a permanently broken file is not retried forever.

For each candidate, it creates two temporary signed links: one link lets the preview service read the original file, and another lets that service upload the generated PNG. These are presigned URLs, meaning short-lived web addresses that grant limited access without sending file bytes through this core service. The preview service downloads the source, renders the image, uploads it, and returns only the preview size. If that succeeds, this file updates the shared artifact row with the preview key, media type, and size. If the service is unreachable or refuses the file, the row is left alone for a later scheduled run.

#### Function details

##### `_eligible`  (lines 44–45)

```
def _eligible(filename_column: sa.Column) -> sa.ColumnElement[bool]
```

**Purpose**: This helper decides whether a shared file has a filename that the preview system knows how to render. It keeps the retry job focused on supported document types instead of asking the preview service to try every file.

**Data flow**: It receives a database filename column → builds a database condition that checks whether the filename ends with any supported preview suffix → returns that condition so a larger database query can filter rows.

**Call relations**: Both `PreviewRenderer.run` and `PreviewRenderer.candidate_workspaces` call this before searching the database. In both cases, it supplies the same rule: only rows with preview-friendly filenames should be considered.

*Call graph*: called by 2 (candidate_workspaces, run); 2 external calls (ilike, or_).


##### `PreviewRenderer.run`  (lines 59–81)

```
async def run(self) -> None
```

**Purpose**: This is the main work loop for one workspace. It finds a small batch of recent shared files that are missing previews, then tries to render each one.

**Data flow**: It computes a cutoff time one hour in the past → opens a workspace-scoped database transaction → selects recent shared artifacts with no preview and eligible filenames → if there are any, opens an HTTP client → sends each row's blob key and filename to `_render_one`. It does not return preview data directly; its effect is that successful rows may be updated in the database by `_render_one`.

**Call relations**: A scheduler or job runner calls `run` after constructing a `PreviewRenderer` for a workspace. `run` uses `_eligible` to narrow the database search, then hands each chosen artifact to `_render_one`, which does the actual preview-service request and database update.

*Call graph*: calls 2 internal fn (_render_one, _eligible); 4 external calls (now, AsyncClient, select, workspace_tx).


##### `PreviewRenderer._render_one`  (lines 83–119)

```
async def _render_one(self, client: httpx.AsyncClient, blob_key: str, filename: str) -> None
```

**Purpose**: This function tries to create and record the preview for one shared file. It is careful to keep file contents out of the core service by giving the preview service temporary read and write URLs instead of moving bytes itself.

**Data flow**: It receives an HTTP client, the stored file's blob key, and the original filename → creates a new preview blob key ending in `.png` → asks the blob store for a short-lived download URL for the source and a short-lived upload URL for the preview → sends a render request to the preview service with the file type, size limits, source URL, and upload URL. If the service cannot be reached or returns an error, it logs the problem and leaves the database unchanged. If the service succeeds, it reads the returned preview size and updates the matching shared artifact row with the preview blob key, PNG media type, and byte size.

**Call relations**: `PreviewRenderer.run` calls this once for each candidate row in its batch. `_render_one` talks outward to the preview service through the HTTP client, uses the blob store through presigned URLs, logs failures for observability, and finally writes the preview metadata back under the workspace database transaction.

*Call graph*: called by 1 (run); 7 external calls (post, dumps, PurePosixPath, update, workspace_tx, log, uuid4).


##### `PreviewRenderer.candidate_workspaces`  (lines 121–135)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: This function finds which workspaces currently have recent shared files that still need preview retries. It lets a higher-level job decide which workspaces should run the renderer.

**Data flow**: It computes the same retry cutoff time used by `run` → opens an owner-level database transaction that can see workspace identifiers → selects distinct workspace IDs from shared artifacts that are recent, missing previews, and have eligible filenames → returns those workspace IDs as a tuple.

**Call relations**: A scheduler or coordinating job can call `candidate_workspaces` before running per-workspace rendering. It uses `_eligible` to apply the same supported-file rule as `run`, but instead of rendering anything itself, it returns the workspace IDs that need attention.

*Call graph*: calls 1 internal fn (_eligible); 3 external calls (now, select, owner_tx).


### `core/src/ufo/media/previews.py`

`data_model` · `cross-cutting`

This file is a simple data model. It describes one piece of information the media system needs after a picture preview has been saved: the preview's storage key and its exact size in bytes.

The main item is `StoredPreview`, a frozen dataclass. A dataclass is a Python shortcut for plain objects that mostly just carry data. “Frozen” means that once a `StoredPreview` is created, its fields cannot be changed. That matters because storage records should behave like receipts: once you say “this preview is stored at this key and is this many bytes,” other code can safely pass that record around without worrying that someone silently rewrote it.

The `blob_key` is a workspace-relative name or path used to find the stored preview in blob storage. Blob storage means a place for saving raw file-like chunks of data, such as images. The `size_bytes` field records the preview’s exact byte size, which can be useful for validation, display, limits, or bookkeeping.

Without this file, different parts of the project might pass preview details around as loose dictionaries or separate strings and numbers, making mistakes easier. This small class gives those details a shared name and structure.


### Extension share outputs
Marks the app artifacts extension as importable and generates branded preview cards for public hosted site links.

### `extensions/app_artifacts/ufo_ext_app_artifacts/__init__.py`

`other` · `import/package discovery`

This is an empty Python package marker file. In Python, a folder can act like a named bundle of code, called a package, when it contains an `__init__.py` file. That lets other files import modules from `ufo_ext_app_artifacts` using normal Python import paths. Think of it like putting a label on a drawer: the drawer may contain useful tools elsewhere, but this label is what lets the rest of the workshop find it by name. Because the file is empty, it does not run setup code, create shortcuts, or expose any public functions. Its value is structural: without it, some Python environments or packaging tools might not recognize this directory as an importable package, which could make the extension fail to load.


### `extensions/sites/ufo_ext_sites/share_card.py`

`domain_logic` · `site deploy / share-card generation`

Link preview services, often called “unfurlers,” look for an image to show beside a shared URL. This file makes that image for hosted sites. The result is a fixed-size card: a dark UFO brand panel on the left and the site’s own front page on the right. Without this file, public sites would either keep using a generic image or keep an older card if a new one could not be drawn.

The work happens inside the site’s sandbox, which is the isolated environment where the site is being built or served. That matters because the site is already reachable there on local loopback, and the sandbox already has Chrome or Chromium available. The file asks a headless browser, meaning a browser without a visible window, to take a careful screenshot. It does not simply capture at page load; it waits briefly for the page to stop changing, so animations or late content have a chance to appear.

Then it builds a small HTML page for the finished card. That page contains the left brand panel, embeds the site screenshot as a data URI, and is itself photographed by the browser. Finally, Pillow, an image library, converts the drawn PNG into a progressive JPEG and computes a digest, which is a fingerprint used to identify the card. Failures are deliberately non-fatal: if drawing, encoding, or storing fails, the problem is logged and the hosted site keeps whatever card it already had.

#### Function details

##### `card_page`  (lines 486–511)

```
def card_page(name: str, drawn: str) -> str
```

**Purpose**: Builds the HTML page that the browser will draw into the final share card. It puts together the brand panel, the site name, the UFO logo, the embedded font, and a placeholder where the site screenshot will later be inserted.

**Data flow**: It receives the site name and a CSS sizing rule for how the screenshot should appear. It reads local asset files for the font and logo, escapes the site name so member-provided text cannot break the HTML, fills the card template, and returns a complete HTML string with a screenshot token still inside it.

**Call relations**: When _compose is ready to build the final card, it asks card_page for the card markup. card_page calls _lockup to get the SVG logo markup, and it uses base64 encoding and HTML escaping so the browser can safely draw the page.

*Call graph*: calls 1 internal fn (_lockup); called by 1 (_compose); 2 external calls (b64encode, escape).


##### `_lockup`  (lines 514–518)

```
def _lockup() -> str
```

**Purpose**: Extracts the usable SVG logo markup from the stored UFO logo file. It removes the document wrapper before the SVG so the logo can be placed inside another HTML page.

**Data flow**: It reads the logo asset from disk as text, finds where the actual <svg> element starts, and returns the SVG markup from that point onward.

**Call relations**: card_page calls _lockup while assembling the left brand panel. The returned SVG becomes the visible UFO lockup in the card.

*Call graph*: called by 1 (card_page).


##### `shot_command`  (lines 521–541)

```
def shot_command(*, url: str, width: int, height: int, scale: int, shot: str, root: str) -> str
```

**Purpose**: Creates the shell command that will run Chromium in the sandbox and save a screenshot. This is the bridge between Python code and the browser-driving script that captures either the site page or the finished card page.

**Data flow**: It receives a URL or file path, image dimensions, scale factor, output screenshot path, and sandbox root. It quotes paths and URLs safely, inserts the browser-driver Python program and browser flags into a shell script, and returns that script as a string ready to run.

**Call relations**: _shoot calls shot_command whenever it needs a browser screenshot. shot_command prepares the exact command, while _shoot is responsible for running it and checking whether it succeeded.

*Call graph*: called by 1 (_shoot); 2 external calls (quote, shell_path).


##### `draw_from_page`  (lines 544–562)

```
async def draw_from_page(ctx: ToolContext, sites: HostedSites, conversation_id: UUID, name: str, port: int) -> None
```

**Purpose**: Creates a share card for a newly deployed site by taking a fresh screenshot of the site’s front page. This is the normal path when the site is currently running in the sandbox.

**Data flow**: It receives the tool context, site storage helper, conversation ID, site name, and local port where the site is serving. It chooses a runtime path for the screenshot, asks _shoot to photograph the live site at the card’s own right-side size, and if that succeeds passes the screenshot to _compose to finish and store the card.

**Call relations**: This is one of the public entry points for this file’s feature. It first relies on _shoot to capture the page, then hands control to _compose so the final branded card can be drawn, encoded, stored, and recorded for the site.

*Call graph*: calls 2 internal fn (_compose, _shoot).


##### `draw_from_stored_shot`  (lines 565–580)

```
async def draw_from_stored_shot(ctx: ToolContext, sites: HostedSites, conversation_id: UUID, name: str, blob_key: str) -> None
```

**Purpose**: Creates a share card for an older site that already has a stored page preview but did not yet have a dedicated share card. It reuses that older screenshot rather than requiring the site to be live.

**Data flow**: It receives the tool context, site storage helper, conversation ID, site name, and blob key for an existing screenshot. It downloads the screenshot from blob storage, writes it into the sandbox, and then asks _compose to build the card from that image. If the write fails, it logs the problem and stops.

**Call relations**: This is the backfill path for sites deployed before share cards existed. It skips _shoot for the first screenshot because the screenshot already exists, then calls _compose to do the same final card-building work used by draw_from_page.

*Call graph*: calls 2 internal fn (_compose, _undrawn).


##### `_shoot`  (lines 583–612)

```
async def _shoot(ctx: ToolContext, name: str, shot: str, *, url: str, width: int, height: int, scale: int) -> bool
```

**Purpose**: Runs the sandbox browser screenshot command and reports whether a usable image was written. It is used both for photographing the site page and for photographing the assembled card HTML.

**Data flow**: It receives the tool context, site name, output path, URL or file path to capture, dimensions, and scale. It first empties the output file so success can be judged by a fresh non-empty result, builds the browser command with shot_command, runs it with a timeout, and returns true if the command succeeds. If setup or capture fails, it logs the failure and returns false.

**Call relations**: draw_from_page calls _shoot to capture the live site. _compose calls _shoot again to capture the completed card page. When anything goes wrong, _shoot reports through _undrawn rather than throwing the whole deploy off course.

*Call graph*: calls 2 internal fn (_undrawn, shot_command); called by 2 (_compose, draw_from_page).


##### `_compose`  (lines 615–665)

```
async def _compose(ctx: ToolContext, sites: HostedSites, conversation_id: UUID, name: str, shot: str, drawn: str) -> None
```

**Purpose**: Turns an existing screenshot into the finished share card, stores it, and updates the hosted site record. This is the main assembly line for the final image.

**Data flow**: It receives the tool context, site storage helper, conversation ID, site name, screenshot path, and sizing rule for the screenshot. It writes the card HTML page into the sandbox, inserts the screenshot bytes into that page, photographs the page into a PNG, converts the PNG to a progressive JPEG, stores the JPEG as a preview artifact, and finally records the blob key and digest on the site. If any step fails, it logs the issue and leaves the site’s existing card unchanged.

**Call relations**: draw_from_page and draw_from_stored_shot both hand their screenshot to _compose. Inside, _compose asks card_page for markup, uses _shoot to draw the card page, calls ToolContext.store_preview to save the finished file, and calls HostedSites.set_share_card only after the new card is safely stored.

*Call graph*: calls 5 internal fn (store_preview, _shoot, _undrawn, card_page, set_share_card); called by 2 (draw_from_page, draw_from_stored_shot).


##### `_undrawn`  (lines 668–669)

```
def _undrawn(name: str, detail: object) -> None
```

**Purpose**: Logs that a share card could not be drawn or stored. It keeps failures visible to operators without stopping the site from being hosted.

**Data flow**: It receives the site name and an error detail. It turns the detail into text, trims it to a safe length, and writes a structured log event named site_card.undrawn.

**Call relations**: _shoot, _compose, and draw_from_stored_shot call _undrawn whenever a recoverable failure happens. It is the shared failure-reporting path for this file’s “best effort” behavior.

*Call graph*: called by 3 (_compose, _shoot, draw_from_stored_shot); 1 external calls (log).

## 📊 State Registers Touched

- `reg-conversation-records` — The durable conversation list, including titles, audience, surface labels, sandbox links, and visibility rules.
- `reg-sandbox-handles` — The remembered execution workspaces, browser workbenches, terminal sessions, and sandbox IDs used across a conversation or turn.
- `reg-artifact-blob-store` — The shared file storage for generated artifacts, downloads, document previews, screenshots, and other saved output bytes.
- `reg-hosted-site-store` — Saved hosted-site records, published bindings, homepage mappings, build metadata, and site preview state used by public routes and site tools.
- `reg-turn-created-reference-store` — Saved references created by a turn, linking its work to newly produced artifacts, objects, sources, sites, or other records for later display, replay, and cleanup.
- `reg-signed-token-keyring` — Shared signing secrets, key IDs, expiry rules, and validation parameters used to mint and verify login, public-route, artifact-download, and sandbox-access tokens.

# Surface ingress and external request routing  `stage-5`

This stage is the system’s set of front doors while it is running. Requests arrive from browsers, Slack, terminals, operators, file links, and sandbox-hosted sites. Each surface speaks its own outside language, then uses a shared bridge to turn that traffic into core actions such as finding a member, opening a conversation, sending a message, reading data, or streaming replies.

The web portal surface serves the browser app and handles chat, settings, admin views, objects, live updates, and community skill browsing. The Slack surface verifies Slack events, maps messages and buttons into conversations, formats mentions, and sends replies back. The terminal surface does the same for command-line users, including live progress, credential prompts, OAuth callbacks, and cross-server terminal streams. Operator and debugger surfaces are read-only control room windows for trusted staff to inspect conversations, turns, files, and memory.

Shared support files keep these doors safe. Signed tokens route early public requests. Signed artifact links and artifact routes protect downloads and refresh expired links for valid members. Image preview checks prevent harmful oversized inputs. The sandbox ingress server exposes sandbox sites only through valid signed host links.

## Sub-stages

- [Web portal surface](stage-5.1.md) `stage-5.1` — 4 files
- [Slack surface](stage-5.2.md) `stage-5.2` — 6 files
- [Terminal and command-line surface](stage-5.3.md) `stage-5.3` — 6 files
- [Operator and debugger surfaces](stage-5.4.md) `stage-5.4` — 4 files

## Files in this stage

### Artifact and preview safeguards
Shared validators protect signed artifact access and externally supplied image previews before public surfaces use them.

### `core/src/ufo/artifact_url.py`

`domain_logic` · `link creation and artifact request handling`

This file is the gatekeeper for anonymous artifact downloads. An artifact is stored under a key like `artifacts/<artifact-id>/<filename>`, and this code turns that key into a URL that can be shared for a limited time. The URL contains an expiry time and a signature. A signature is a tamper-proof stamp made with the server's secret; if anyone changes the artifact id, expiry, or preview permission, the stamp no longer matches.

The main idea is simple: `mint_artifact_url` makes the link, and `verify_artifact_url` later proves whether the link is valid. The filename is kept in the path, but the signed part is the artifact id, expiry time, and optional preview claim. That means the URL can only reach files inside the artifact area, not arbitrary storage paths.

The file also supports safe image previews. A preview claim says, “this raster image may be shown inline, and it must have this media type and exact byte size.” The download route can then double-check the actual bytes before serving them as an image.

If a link is expired but otherwise genuine, the code raises a special `ArtifactUrlExpired` error that still carries the verified claims. This lets another part of the system refresh access for a signed-in workspace member instead of treating the URL as fake.

#### Function details

##### `ArtifactUrlExpired.__init__`  (lines 55–57)

```
def __init__(self, claims: ArtifactClaims) -> None
```

**Purpose**: This builds the special error used when a download URL is real but too old. It keeps the verified artifact details so another part of the system can decide whether a logged-in user is allowed to recover or refresh the download.

**Data flow**: It receives already-checked artifact claims. It creates an error message saying the URL is expired, then stores those claims on the error object so they are not lost.

**Call relations**: During URL checking, `verify_artifact_url` calls this when the signature is valid but the expiry time has passed. That lets the route distinguish “expired but authentic” from “malformed or forged.”

*Call graph*: called by 1 (verify_artifact_url).


##### `artifact_media_type`  (lines 60–65)

```
def artifact_media_type(filename: str) -> str
```

**Purpose**: This guesses what kind of file an artifact is, such as PNG image or PDF, based on its filename. If the name suggests compressed bytes or the type is unknown, it falls back to a safe generic download type.

**Data flow**: It takes a filename, asks Python's MIME type table what content type the name suggests, and checks whether the name also implies a compression encoding. It returns the guessed type only when it is safe and direct; otherwise it returns `application/octet-stream`, meaning generic binary data.

**Call relations**: This helper is used when serving artifact bytes so the response can tell the browser what kind of file it is. It relies on `mimetypes.guess_type` for the filename lookup but deliberately refuses misleading cases like a gzipped image being labeled as a plain image.

*Call graph*: 1 external calls (guess_type).


##### `mint_artifact_url`  (lines 68–84)

```
def mint_artifact_url(secret: str, blob_key: str, expires_at: int, *, preview: ImagePreviewGrant | None=None) -> str
```

**Purpose**: This creates a signed download path for one stored artifact. It is used when the system wants to give someone temporary access to a file without requiring the file route itself to know who they are.

**Data flow**: It receives the server secret, a storage key, an expiry timestamp, and optionally an image preview grant. It checks that the secret exists, splits and validates the artifact storage key, formats the optional preview claim, signs the artifact id plus expiry plus preview value, and returns a URL path with query parameters for the expiry and signature. If the preview claim is present, it adds that too.

**Call relations**: This is the minting half of the signed-link pair. It uses `_split_key` to make sure the key really names an artifact, `_parsed_preview` to reject invalid preview grants, `_signed_message` to build the exact bytes being signed, `sign_detached` to create the tamper-proof stamp, and URL quoting so filenames and preview values are safe inside a URL. Later, `verify_artifact_url` checks the same pieces.

*Call graph*: calls 3 internal fn (_parsed_preview, _signed_message, _split_key); 3 external calls (__init__, sign_detached, quote).


##### `verify_artifact_url`  (lines 87–117)

```
def verify_artifact_url(secret: str, artifact_id: str, filename: str, expires_at: str, signature: str, preview: str, now: datetime) -> ArtifactClaims
```

**Purpose**: This checks whether an incoming artifact download URL is trustworthy and still current. If it passes, it returns the exact storage key, filename, expiry, and any preview permission that the URL proves.

**Data flow**: It receives the server secret, URL path pieces, query-string values, and the current time. It first rejects missing secrets, bad artifact ids, unsafe filenames, non-numeric expiry values, and invalid preview claims. It then rebuilds the signed message and checks the signature. If the signature matches, it creates verified claims; if the expiry time has passed, it raises `ArtifactUrlExpired` carrying those claims. Otherwise it returns the claims for the caller to serve.

**Call relations**: This is the checking half of the signed-link pair. It is called when an artifact request comes in. It uses `_is_artifact_id` and `_is_filename` to prevent unsafe paths, `_parsed_preview` to understand preview permission, `_signed_message` and `verify_detached` to detect tampering, and `ArtifactUrlExpired.__init__` when the link is genuine but too old.

*Call graph*: calls 5 internal fn (__init__, _is_artifact_id, _is_filename, _parsed_preview, _signed_message); 4 external calls (__init__, __init__, timestamp, verify_detached).


##### `_signed_message`  (lines 120–121)

```
def _signed_message(artifact_id: str, expires_at: str, preview_value: str) -> bytes
```

**Purpose**: This builds the exact text that is signed and later verified. Keeping this in one helper makes the minting and checking sides agree byte-for-byte.

**Data flow**: It receives an artifact id, an expiry string, and a preview value. It joins them with colons in a fixed order and returns the result as bytes, which are the form needed by the signing functions.

**Call relations**: `mint_artifact_url` calls this before signing a new link. `verify_artifact_url` calls it again when checking a link, so any changed value produces a different message and the signature check fails.

*Call graph*: called by 2 (mint_artifact_url, verify_artifact_url).


##### `_parsed_preview`  (lines 124–135)

```
def _parsed_preview(value: str) -> ImagePreviewGrant | None
```

**Purpose**: This turns the preview part of a URL into a trusted preview grant, but only if it follows the allowed format and limits. It prevents a link from claiming unsafe or oversized inline image previews.

**Data flow**: It receives a text value shaped like `media-type:size`. It splits off the media type and size, checks that the media type is one of the allowed raster image types, checks that the size is numeric, and rejects sizes above the configured maximum. If everything is valid, it returns an `ImagePreviewGrant`; otherwise it returns nothing.

**Call relations**: `mint_artifact_url` uses this as a self-check before putting a preview claim into a signed URL. `verify_artifact_url` uses it when reading a request, so only the same narrow set of preview claims can pass.

*Call graph*: called by 2 (mint_artifact_url, verify_artifact_url); 3 external calls (__init__, cast, values).


##### `_split_key`  (lines 138–147)

```
def _split_key(blob_key: str) -> tuple[str, str]
```

**Purpose**: This breaks a stored artifact key into its artifact id and filename, while proving that the key is inside the artifact namespace. It stops callers from minting links for unrelated storage paths.

**Data flow**: It receives a blob key string. It removes the expected `artifacts/` prefix, separates the artifact id from the filename, then checks that the prefix existed, the separator was present, the artifact id is a proper UUID, and the filename is safe. It returns the artifact id and filename, or raises an artifact URL error if the key is not valid.

**Call relations**: `mint_artifact_url` calls this before signing anything. It relies on `_is_artifact_id` and `_is_filename` for the two detailed checks, so only well-formed artifact keys can become downloadable URLs.

*Call graph*: calls 2 internal fn (_is_artifact_id, _is_filename); called by 1 (mint_artifact_url); 1 external calls (__init__).


##### `_is_artifact_id`  (lines 150–154)

```
def _is_artifact_id(value: str) -> bool
```

**Purpose**: This checks whether a string is exactly a standard UUID artifact id. A UUID is a long, standardized identifier used here to name one artifact without ambiguity.

**Data flow**: It receives a string. It tries to parse it as a UUID and then compares the normalized UUID text back to the original. It returns true only when the value is already in the expected canonical form; if parsing fails, it returns false.

**Call relations**: `_split_key` uses this when minting a URL from a storage key, and `verify_artifact_url` uses it when checking a request path. This keeps both creation and verification using the same idea of a valid artifact id.

*Call graph*: called by 2 (_split_key, verify_artifact_url); 1 external calls (UUID).


##### `_is_filename`  (lines 157–158)

```
def _is_filename(value: str) -> bool
```

**Purpose**: This checks whether a filename is safe to use as a single path segment. It rejects empty names, nested paths, and the special directory names `.` and `..`.

**Data flow**: It receives a filename string. It returns true only if the name is not empty, contains no slash, and is not one of the directory navigation markers. It does not change anything.

**Call relations**: `_split_key` uses this before minting a link, and `verify_artifact_url` uses it before trusting a requested filename. Together, they prevent a signed artifact URL from being stretched into a path traversal trick.

*Call graph*: called by 2 (_split_key, verify_artifact_url).


### `core/src/ufo/image_previews.py`

`domain_logic` · `request handling`

Image previews are useful because they let the system show a quick picture without fetching or processing a full file. But image files can also be malformed, misleading, or deliberately dangerous. This file acts like a security checkpoint at the door: it only lets through previews that match their signed promise and stay within strict safety limits.

It recognizes common raster image formats: GIF, JPEG, PNG, and WebP. A raster image is a normal pixel-based picture, unlike a vector drawing. The file can first guess the media type from a path suffix, such as “.png”. More importantly, it can validate a streamed preview. It reads the incoming bytes, confirms the byte count matches the trusted grant, then asks Pillow, the image library, to inspect the actual image.

The validator checks several layers. It rejects files that look incomplete at the container level, such as a JPEG missing its ending marker. It then opens the image, verifies that the real format matches the claimed media type, and walks through frames for animated formats. Along the way it limits dimensions, number of frames, and total decoded pixels. This matters because a tiny-looking image file can sometimes expand into a huge amount of memory, like a folded map that covers the whole room when opened.

#### Function details

##### `raster_image_media_type`  (lines 43–44)

```
def raster_image_media_type(path: str) -> RasterImageMediaType | None
```

**Purpose**: This function guesses the image media type from a file path, using the file extension. It is useful as a quick first pass when the system sees a name like “photo.jpg” or “preview.webp”.

**Data flow**: It takes a path string as input. It extracts the final suffix, lowercases it, and looks it up in the known raster image suffix table. It returns the matching media type, such as “image/png”, or returns nothing if the suffix is not one of the supported image types.

**Call relations**: This is a lightweight helper used before deeper validation. It relies on path parsing to read the suffix, but it does not inspect the file bytes; the stricter checks happen later in the preview validation flow.

*Call graph*: 1 external calls (PurePosixPath).


##### `validated_image_preview`  (lines 47–61)

```
async def validated_image_preview(stream: AsyncIterator[bytes], grant: ImagePreviewGrant) -> bytes
```

**Purpose**: This function reads an incoming image preview and proves that it matches the trusted size and media type claim before returning the bytes. It is the main safe entry point for accepting preview data.

**Data flow**: It receives an asynchronous stream of byte chunks and an ImagePreviewGrant, which says what media type and byte size the preview is supposed to have. It counts the bytes as they arrive, rejects the preview if it is too large or does not match the promised size, joins the chunks into one byte string, and then runs the image validator in a background thread so CPU-heavy image decoding does not block the main async work. If everything passes, it returns the original bytes unchanged; if not, it raises InvalidImagePreview.

**Call relations**: This function sits between outside image data and the rest of the system. It performs the streaming size checks itself, then hands the completed byte string to _ImagePreviewValidator.validate for format and decoding checks.

*Call graph*: 2 external calls (__init__, to_thread).


##### `_ImagePreviewValidator.validate`  (lines 66–109)

```
def validate(data: bytes, media_type: RasterImageMediaType) -> None
```

**Purpose**: This function performs the deep safety inspection of the image bytes. It makes sure the file is complete, readable, truly the claimed format, and small enough after decoding to be safe.

**Data flow**: It takes raw image bytes and the media type they are supposed to be. First it performs simple container-ending checks. Then it opens the bytes with Pillow, treats decompression-bomb warnings as hard errors, verifies the image structure, and reopens it to walk through each frame. For every frame, it checks width, height, frame count, and total decoded pixels, and forces the frame to load so hidden decode problems are caught. It returns nothing on success. On any invalid, suspicious, or mismatched image, it raises InvalidImagePreview.

**Call relations**: validated_image_preview calls this after it has finished reading and counting the stream. This validator delegates the format-specific completeness checks to _ImagePreviewValidator._validate_container, then uses Pillow to do the deeper image parsing and decoding checks.

*Call graph*: 5 external calls (__init__, open, BytesIO, catch_warnings, simplefilter).


##### `_ImagePreviewValidator._validate_container`  (lines 112–125)

```
def _validate_container(data: bytes, media_type: RasterImageMediaType) -> None
```

**Purpose**: This function performs quick format-specific checks that the image file appears complete. It catches simple truncation problems before the heavier image parsing begins.

**Data flow**: It receives the image bytes and the claimed media type. For JPEG, GIF, and PNG, it checks for the expected ending bytes. For WebP, it checks the RIFF/WebP header and confirms the declared file length matches the actual byte length. It returns nothing if the container looks complete, or raises InvalidImagePreview if the bytes are missing required structure.

**Call relations**: _ImagePreviewValidator.validate calls this as its first line of defense. If this quick check passes, validation continues into the more expensive Pillow-based image inspection.

*Call graph*: 1 external calls (__init__).


### Surface orchestration bridge
The trusted external-surface bridge translates Slack, portal, and related requests into core member, conversation, turn, and reply operations.

### `core/src/ufo/ext/surface.py`

`orchestration` · `request handling, live streaming, and background writeback delivery`

A surface is the place where a human talks to the system: a Slack thread, a browser chat, a CLI-like live channel, or a future integration. This file defines the special powers those surfaces need and keeps them in one controlled seam. Without it, each surface would have to reach directly into core tables, queues, credentials, files, and live streams, which would make identity, privacy, and delivery rules easy to get wrong.

The central object is SurfaceContext. A route handler receives it after core has already resolved the workspace. Through it, the surface can link an outside user id to a workspace member, find or create a conversation, admit a message into the durable turn queue, tail live frames, stop turns, fetch transcripts, list portal pages, sign artifact links, and read declared credentials.

The file supports two delivery styles. A live surface keeps a connection open and streams turn frames as they happen. A durable surface, like Slack, cannot rely on an open connection, so the WritebackPoller later picks up completed turns from the database and posts the final answer plus attachments. Think of live delivery like watching food made at the counter, and durable delivery like leaving a ticket for a courier to bring the order when it is ready.

The file also contains small data shapes used by portal screens, safety helpers for inbound text and filenames, authorization checks for private transcripts, and installation lookup for shared surfaces.

#### Function details

##### `mint_marker`  (lines 179–189)

```
def mint_marker() -> str
```

**Purpose**: Creates a short random marker used to wrap one member message in unique tags. This makes it possible to later separate the member’s own words from surrounding context without trusting the message text.

**Data flow**: It takes no input, asks the secure random generator for a few bytes, and returns them as hexadecimal text. Nothing else is changed.

**Call relations**: It is used as the first step before fencing inbound member text; the marker is then passed to the message-wrapping helper.

*Call graph*: 1 external calls (token_hex).


##### `fence_member_message`  (lines 192–205)

```
def fence_member_message(marker: str, ambient: str, body: str, attachments: str) -> str
```

**Purpose**: Builds the exact text that becomes a member’s inbound message, with separate sections for ambient context, the member’s words, and attachment text. The unique marker keeps those sections from being confused with text the member typed.

**Data flow**: It receives a marker, ambient text, the message body, and attachment text. It returns one combined string with tagged sections, adding the attachment section only when attachments exist.

**Call relations**: Surfaces use this before admitting a member message so later transcript views can recover what the member actually said.


##### `inbox_name`  (lines 208–237)

```
def inbox_name(raw: str, used: set[str]) -> str
```

**Purpose**: Turns an unsafe attachment filename from the outside world into a safe workspace filename. It prevents path tricks, overly long names, and duplicate names in one batch.

**Data flow**: It receives the raw filename and the set of names already used. It strips path parts, replaces unsafe characters, keeps the extension when possible, adds a number if needed, updates the used set, and returns the safe name.

**Call relations**: Attachment-ingesting surfaces call this before writing uploaded files into the conversation workspace.

*Call graph*: 1 external calls (contained_leaf).


##### `member_message_text`  (lines 240–250)

```
def member_message_text(inbound: str) -> str
```

**Purpose**: Extracts the member’s own words from a fenced inbound message. If the message was not fenced, it treats the whole input as the member text.

**Data flow**: It receives an inbound string, looks for the uniquely marked member-message section, and returns that section’s content or the original string.

**Call relations**: conversation_name calls this so conversation titles are based on what the member said, not on surrounding channel context.

*Call graph*: called by 1 (conversation_name).


##### `MemberAdmitter.admit`  (lines 287–296)

```
async def admit(self, conversation_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None, intent: ToolIntent | None=None) -> Adm
```

**Purpose**: Defines the required shape of an object that can admit a member message into the core turn queue. It is a protocol, meaning concrete implementations must provide this method.

**Data flow**: It receives the conversation id, message body, optional idempotency key, optional context, speaker member id, and optional prepared tool intent. It must return an Admitted result describing the turn created or joined.

**Call relations**: SurfaceContext.admit delegates to an implementation of this protocol whenever a surface sends a member message into core.


##### `TurnTailer.tail`  (lines 308–310)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Defines how live surfaces subscribe to the stream of frames for one turn. A frame is a live update such as model output, tool progress, or a terminal state.

**Data flow**: It receives a turn id and an optional cursor telling where to resume. It returns an async context manager that yields cursor-and-frame pairs until the turn ends.

**Call relations**: SurfaceContext.tail exposes this to web, debugger, sample, and UFO live surfaces so they do not touch the internal hub directly.


##### `TurnStopper.stop`  (lines 320–320)

```
async def stop(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID) -> 'Stopped'
```

**Purpose**: Defines how a surface asks core to stop a running turn for a member. It also reports whether a waiting follow-up message started a new turn.

**Data flow**: It receives workspace, conversation, and turn ids. It returns a Stopped result saying whether the turn was ended and whether a new turn was founded.

**Call relations**: SurfaceContext.stop_turn delegates to this protocol when web or live-channel clients press stop.


##### `_media_predicate`  (lines 352–368)

```
def _media_predicate(column: sa.ColumnElement[str], media: str) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a database filter for artifact media categories such as image, data, document, or other. It lets the portal filter shared files without hard-coding SQL in the route.

**Data flow**: It receives a database column and a media category. It returns a SQL condition that matches the right content types, or raises an error for an unknown category.

**Call relations**: SurfaceContext.list_artifacts uses it when the workspace artifacts page asks for a media filter.

*Call graph*: called by 1 (list_artifacts); 3 external calls (and_, not_, or_).


##### `conversation_name`  (lines 447–452)

```
def conversation_name(inbound: str) -> str
```

**Purpose**: Creates an initial conversation title from the text that opened the conversation. It deliberately ignores ambient channel context.

**Data flow**: It receives inbound text, extracts the member’s own words, trims whitespace, caps the length, and returns the title string.

**Call relations**: Conversation-creation paths use this convention so surface-created and agent-created conversations are titled consistently.

*Call graph*: calls 1 internal fn (member_message_text).


##### `retitle_conversation`  (lines 455–472)

```
async def retitle_conversation(workspace_id: UUID, conversation_id: UUID, title: str) -> None
```

**Purpose**: Updates a conversation’s title after a surface has a better name for it. Blank titles are ignored.

**Data flow**: It receives workspace id, conversation id, and title. It trims and caps the title, then updates the matching conversation row if it exists.

**Call relations**: SurfaceContext.retitle_conversation is the surface-facing wrapper around this shared helper.

*Call graph*: called by 1 (retitle_conversation); 2 external calls (update, workspace_tx).


##### `AgentDetail._aware_utc`  (lines 514–515)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Ensures an agent detail timestamp has timezone information. This avoids ambiguous times in API responses.

**Data flow**: It receives a datetime and returns it unchanged if already timezone-aware, otherwise marks it as UTC.

**Call relations**: Pydantic calls it while building AgentDetail objects for portal agent overview reads.

*Call graph*: 1 external calls (replace).


##### `ConnectionView._aware_utc`  (lines 559–560)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Ensures a connector connection timestamp is timezone-aware. This keeps portal data consistent.

**Data flow**: It receives a datetime and returns an equivalent UTC-aware datetime when no timezone was attached.

**Call relations**: Pydantic invokes it when SurfaceContext.list_agent_connections builds ConnectionView rows.

*Call graph*: 1 external calls (replace).


##### `ConnectionPoolView._aware_utc`  (lines 580–581)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Normalizes connection-pool timestamps to timezone-aware UTC. It protects clients from mixed naive and aware datetime values.

**Data flow**: It receives connected_at and returns it with UTC timezone if needed.

**Call relations**: Pydantic runs it while SurfaceContext.list_connections builds pooled connection views.

*Call graph*: 1 external calls (replace).


##### `SubagentDetail.summary`  (lines 640–641)

```
def summary(self) -> SubagentSummary
```

**Purpose**: Returns the smaller list-row version of a subagent profile. It is used when a page needs only the subagent name and model.

**Data flow**: It reads the detail object’s name and model and returns a SubagentSummary with those fields.

**Call relations**: Portal code can use this to show a roster without exposing the full prompt and limits.

*Call graph*: 1 external calls (__init__).


##### `_binding_fields`  (lines 672–700)

```
def _binding_fields(backend: str, config: dict[str, JsonValue]) -> _BindingFields
```

**Purpose**: Extracts the source-binding identity fields the portal must echo back when editing a connector-backed source. This prevents a form action from accidentally changing hidden identity fields to defaults.

**Data flow**: It receives a backend name and stored JSON config. It validates connector config, returns display and identity fields, or returns all None values when the config is not a connector source.

**Call relations**: SurfaceContext.list_sources calls it while building SourceView records for the workspace sources page.

*Call graph*: called by 1 (list_sources); 2 external calls (model_validate, binding_name).


##### `SourceView._aware_utc`  (lines 726–727)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Makes a source sync timestamp timezone-aware. This gives clients a reliable time format.

**Data flow**: It receives next_sync_at and returns it unchanged if aware or marked as UTC if not.

**Call relations**: Pydantic calls it as SourceView objects are created by SurfaceContext.list_sources.

*Call graph*: 1 external calls (replace).


##### `ConversationSummary._aware_utc`  (lines 745–748)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Normalizes conversation timestamps for created-at and last-turn-at fields. It accepts that last-turn-at may be absent.

**Data flow**: It receives a datetime or None. None stays None; a naive datetime is marked UTC; an aware one is returned as-is.

**Call relations**: Pydantic applies it for conversation listings across debug and web portal views.

*Call graph*: 1 external calls (replace).


##### `record_transcript_access`  (lines 761–822)

```
async def record_transcript_access(workspace_id: UUID, conversation_id: UUID, agent_id: UUID, member_id: UUID) -> TranscriptAccess | None
```

**Purpose**: Records that an admin acknowledged they are reading another member’s private transcript. This creates a short-lived permission and an audit log entry.

**Data flow**: It receives workspace, conversation, agent, and reader member ids. It checks that the conversation belongs to the agent, finds the private subject member, inserts an access row, logs the disclosure, and returns the reader and subject emails, or None if disclosure is not applicable.

**Call relations**: Prepared portal actions call this before private content is served; SurfaceContext.readable_conversation later checks the recorded row.

*Call graph*: 9 external calls (__init__, now, insert, select, audience_member, parse_audience, workspace_tx, log, uuid4).


##### `LedgerEntry._aware_utc`  (lines 897–898)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Ensures accounting timestamps include a timezone. This makes billing rows predictable for clients.

**Data flow**: It receives created_at and returns a UTC-aware datetime if the original had no timezone.

**Call relations**: Pydantic runs it when SurfaceContext.turn_detail builds ledger entries.

*Call graph*: 1 external calls (replace).


##### `_fulfilled_marker_key`  (lines 941–946)

```
def _fulfilled_marker_key(workspace_id: UUID, sealed: str, slot: str) -> str
```

**Purpose**: Builds the blob-store key used to remember that one credential prompt slot has been fulfilled. This stops the same prompt from being shown again.

**Data flow**: It receives workspace id, sealed request text, and slot name. It hashes the sealed request, combines it with the slot, and returns a blob key string.

**Call relations**: SurfaceContext.credential_prompt_pending checks for this marker, and SurfaceContext.fulfill_credential_request writes it.

*Call graph*: called by 2 (credential_prompt_pending, fulfill_credential_request); 1 external calls (sha256).


##### `_main_agent`  (lines 949–962)

```
async def _main_agent(workspace_id: UUID) -> UUID
```

**Purpose**: Finds the workspace’s main agent. It is the fallback when a surface installation does not explicitly bind to an agent.

**Data flow**: It receives a workspace id, reads the agent table, and returns the id of the main agent or raises if none exists.

**Call relations**: _bind_surface_installation and SurfaceContext._surface_agent call it when they need a default agent.

*Call graph*: called by 2 (_surface_agent, _bind_surface_installation); 2 external calls (select, workspace_tx).


##### `_bind_surface_installation`  (lines 965–997)

```
async def _bind_surface_installation(workspace_id: UUID, surface: str, installation_id: str) -> None
```

**Purpose**: Creates or updates the binding between a workspace and an external surface installation, such as a Slack team. It keeps installation ownership unique across the whole fleet.

**Data flow**: It receives workspace id, surface name, and installation id. It validates the id, chooses the main agent for new bindings, upserts the row, and raises SurfaceInstallationConflict if another workspace already owns that installation.

**Call relations**: SurfaceContext.bind_installation and SurfaceInstallationAccess.bind both use this single writer for installation records.

*Call graph*: calls 1 internal fn (_main_agent); called by 2 (bind_installation, bind); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.conversation_slots`  (lines 1046–1048)

```
def conversation_slots(self) -> tuple['BoundConversationSlot', ...]
```

**Purpose**: Returns the extension-provided conversation slots available in this deployment. Slots are fixed at boot.

**Data flow**: It reads the context’s stored tuple of bound slots and returns it unchanged.

**Call relations**: Web surface code reads this when rendering conversation slot data.


##### `SurfaceContext.read_conversation_slot`  (lines 1050–1055)

```
async def read_conversation_slot(self, bound: 'BoundConversationSlot', context: 'ConversationSlotContext') -> 'ConversationSlotPayload'
```

**Purpose**: Reads one conversation slot in the correct agent scope. Agent scope means the provider sees the same agent namespace the conversation belongs to.

**Data flow**: It receives a bound slot and slot context, temporarily binds the agent id from the context, calls the provider’s read method, and returns the payload.

**Call relations**: The web surface calls this when a page needs a slot’s current content.

*Call graph*: called by 1 (conversation_slot); 1 external calls (agent).


##### `SurfaceContext.summarize_conversation_slot`  (lines 1057–1062)

```
async def summarize_conversation_slot(self, bound: 'BoundConversationSlot', context: 'ConversationSlotContext') -> int | None
```

**Purpose**: Asks a slot provider for a compact summary value in the correct agent scope. This supports portal overviews without loading full slot content.

**Data flow**: It receives a bound slot and context, binds the context’s agent id, calls the provider’s summarize method, and returns an integer summary or None.

**Call relations**: The web surface calls it while building conversation slot summaries.

*Call graph*: called by 1 (conversation_slots); 1 external calls (agent).


##### `SurfaceContext.deploy_extensions`  (lines 1065–1068)

```
def deploy_extensions(self) -> tuple[DeployExtensionView, ...]
```

**Purpose**: Returns the installed deploy-level extensions for administration screens. It exposes manifest-level status, not secrets or runtime internals.

**Data flow**: It reads the stored tuple of DeployExtensionView objects and returns it.

**Call relations**: Portal administration views use it to show what this deployment loaded.


##### `SurfaceContext.deploy_sandbox_internet`  (lines 1071–1074)

```
def deploy_sandbox_internet(self) -> bool
```

**Purpose**: Reports whether this deployment can allow sandbox internet access at all. Agent settings can only narrow this capability.

**Data flow**: It returns the stored boolean value from the context.

**Call relations**: Agent overview screens use it when explaining an agent’s internet-access setting.


##### `SurfaceContext.subagents`  (lines 1077–1081)

```
def subagents(self) -> tuple[SubagentDetail, ...]
```

**Purpose**: Returns the subagent profiles registered by this deployment. Subagents are fixed deploy features, not member-owned data.

**Data flow**: It returns the stored tuple of SubagentDetail objects.

**Call relations**: Portal pages list these profiles beside normal workspace agents.


##### `SurfaceContext.subagent`  (lines 1083–1086)

```
def subagent(self, name: str) -> SubagentDetail | None
```

**Purpose**: Looks up one subagent profile by name. It returns None when the deployment has no such profile.

**Data flow**: It receives a name, scans the stored subagent tuple, and returns the matching profile or None.

**Call relations**: The web surface uses it in its subagent route gate before rendering a profile page.

*Call graph*: called by 1 (_subagent_gate).


##### `SurfaceContext.deploy_skills`  (lines 1089–1094)

```
def deploy_skills(self) -> tuple[tuple[str, str], ...]
```

**Purpose**: Returns the deploy-provided loadable skill index. This is the shared base skill list available to agents and subagents.

**Data flow**: It asks the skill registry for its index and returns that tuple.

**Call relations**: Portal and subagent detail views use it to show the deploy-level skill floor.


##### `SurfaceContext.models`  (lines 1097–1101)

```
def models(self) -> tuple[str, ...]
```

**Purpose**: Returns the model ids this deployment supports. The portal uses this as the closed list of model choices.

**Data flow**: It reads and returns the stored tuple of model names.

**Call relations**: Agent configuration screens use it when offering model options.


##### `SurfaceContext.sandbox_sizes`  (lines 1104–1107)

```
def sandbox_sizes(self) -> tuple[str, ...]
```

**Purpose**: Returns the sandbox sizes supported by this deployment. An empty list means there is no user-visible size choice.

**Data flow**: It returns the stored tuple of sandbox-size names.

**Call relations**: Portal agent settings read it to decide whether to show sandbox-size controls.


##### `SurfaceContext.credential`  (lines 1109–1112)

```
async def credential(self, slot: str) -> str
```

**Purpose**: Reads a declared workspace credential value for a trusted surface. This is privileged because normal scoped extensions do not get raw credential access.

**Data flow**: It receives a slot name, checks that a credential store exists, and returns the stored value for this workspace and slot.

**Call relations**: Slack and other surface code call it for signing secrets, tokens, and other surface-owned credentials.

*Call graph*: called by 10 (_channel_origin, _ctx_signing_secret, _identity, _post_ephemeral, _run_identity_proof, _to_inbound, attach, ingest, interactive, post).


##### `SurfaceContext.credential_prompt_pending`  (lines 1114–1128)

```
async def credential_prompt_pending(self, sealed: str, slot: str) -> bool
```

**Purpose**: Checks whether a sealed credential request still needs a value for one slot. It keeps fulfilled, expired, or foreign prompts from being shown again.

**Data flow**: It receives a sealed request and slot. It opens and validates the sealed request, checks workspace and slot membership, then returns whether the fulfillment marker blob is absent.

**Call relations**: The web surface uses it while deciding which credential prompts to render.

*Call graph*: calls 1 internal fn (_fulfilled_marker_key); called by 1 (_pending_prompts); 1 external calls (open_credential_request).


##### `SurfaceContext.open_credential_authorization`  (lines 1130–1140)

```
def open_credential_authorization(self, sealed: str) -> CredentialRequestState
```

**Purpose**: Opens a sealed credential-authorization handoff after the workspace is known. It recovers the trusted claims inside the seal.

**Data flow**: It receives sealed text, requires a credential store, verifies and opens the seal, and returns the CredentialRequestState or raises if invalid.

**Call relations**: Slack OAuth callback code uses it to learn which member and slot a credential handoff belongs to.

*Call graph*: called by 1 (oauth_callback); 1 external calls (open_credential_request).


##### `SurfaceContext.fulfill_credential_request`  (lines 1142–1167)

```
async def fulfill_credential_request(self, sealed: str, slot: str, value: str, member_id: UUID | None) -> None
```

**Purpose**: Stores a credential value for a sealed request after proving the right member is fulfilling the right slot in the right workspace.

**Data flow**: It receives sealed request text, slot, value, and member id. It validates the seal, workspace, member, and slot, writes the encrypted credential, then writes the fulfillment marker blob.

**Call relations**: Slack OAuth, UFO surface secret fulfillment, and web credential forms call it to complete credential prompts.

*Call graph*: calls 1 internal fn (_fulfilled_marker_key); called by 3 (oauth_callback, _fulfill_secret, fulfill_credential); 4 external calls (__init__, now, dumps, open_credential_request).


##### `SurfaceContext.bind_installation`  (lines 1169–1175)

```
async def bind_installation(self, installation_id: str) -> None
```

**Purpose**: Binds this surface’s external installation id to the current workspace. This is how later shared ingress requests find the right workspace.

**Data flow**: It receives an installation id and passes the workspace id and this surface name to the shared binding helper.

**Call relations**: Slack calls it after an OAuth installation callback succeeds.

*Call graph*: calls 1 internal fn (_bind_surface_installation); called by 1 (oauth_callback).


##### `SurfaceContext.public_base_url`  (lines 1178–1181)

```
def public_base_url(self) -> str | None
```

**Purpose**: Returns the deployment’s public base URL, if configured. Surfaces need it to build callback and deep-link URLs.

**Data flow**: It returns the stored public base URL or None.

**Call relations**: Surface code reads this when it needs externally reachable links.


##### `SurfaceContext.home_url`  (lines 1183–1193)

```
def home_url(self, fragment: str='') -> str | None
```

**Purpose**: Builds a link into the configured browser-home surface. It lets one surface point a member to the web portal without knowing that surface’s internals.

**Data flow**: It receives an optional URL fragment. If public base URL and home surface are configured, it returns /surface/<home> plus the fragment; otherwise it returns None.

**Call relations**: Slack uses it when it must send members to the browser portal, such as for oversize replies.

*Call graph*: called by 1 (_reply_with_oversize_links).


##### `SurfaceContext.shared_artifacts`  (lines 1195–1227)

```
async def shared_artifacts(self, turn_id: UUID) -> tuple[SharedArtifact, ...]
```

**Purpose**: Returns the files a turn shared, in stable share order. Live surfaces use this to render downloads; durable surfaces get similar data through writeback.

**Data flow**: It receives a turn id, reads shared_artifact rows for this workspace and turn, and returns SharedArtifact objects.

**Call relations**: UFO and web surface code call it when listing shared files for a turn.

*Call graph*: called by 2 (shared_files, _turn_files); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.artifact_link`  (lines 1229–1239)

```
def artifact_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a temporary signed download link for a shared artifact. If link delivery is not configured, it returns None.

**Data flow**: It receives a SharedArtifact, checks for a token secret and public base URL, signs a blob URL with an expiry time, and returns the full URL.

**Call relations**: Slack, UFO, and web surfaces call it when presenting file downloads to members.

*Call graph*: called by 6 (_oversize_link_line, shared_files, _project_slot_context, _radar_run, _turn_files, workspace_artifacts); 2 external calls (now, mint_artifact_url).


##### `SurfaceContext.artifact_preview_link`  (lines 1241–1273)

```
def artifact_preview_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a temporary signed inline preview link for eligible image-like artifacts. It refuses previews when the file type, declared size, or configuration is unsafe.

**Data flow**: It receives a SharedArtifact, chooses either the preview blob or original blob, verifies it is a raster image within size limits, signs a preview URL, and returns it or None.

**Call relations**: Web portal views call it when showing artifact previews in workspace and slot-context pages.

*Call graph*: called by 3 (_project_slot_context, _radar_run, workspace_artifacts); 4 external calls (__init__, now, mint_artifact_url, raster_image_media_type).


##### `SurfaceContext.ingress_url`  (lines 1275–1304)

```
def ingress_url(self, conversation_id: UUID, port: int, entry_path: str) -> str | None
```

**Purpose**: Builds a signed browser URL for opening a conversation sandbox port. This lets a member view a site running inside the sandbox without giving the surface ingress secrets.

**Data flow**: It receives conversation id, port, and entry path. If ingress is configured, it mints a short-lived view token, creates the port-specific hostname, quotes the path, and returns the URL.

**Call relations**: The sites extension calls it when rendering an iframe or site frame for a sandboxed app.

*Call graph*: called by 1 (frame); 6 external calls (__init__, now, site_label, mint_ingress_token, quote, urlsplit).


##### `SurfaceContext._identity_member`  (lines 1306–1319)

```
async def _identity_member(self, surface: str, external_id: str) -> UUID | None
```

**Purpose**: Looks up which member an external surface id is linked to. It is the common private helper for same-surface and peer-surface identity reads.

**Data flow**: It receives a surface name and external id, queries surface_identity for this workspace, and returns the member id or None.

**Call relations**: SurfaceContext.linked_member and SurfaceContext.adopt_identity call it.

*Call graph*: called by 2 (adopt_identity, linked_member); 2 external calls (select, workspace_tx).


##### `SurfaceContext.linked_member`  (lines 1321–1322)

```
async def linked_member(self, external_id: str) -> UUID | None
```

**Purpose**: Finds the member linked to this surface’s external id. It answers without creating a new link.

**Data flow**: It receives an external id and delegates to the identity lookup helper using this context’s surface name.

**Call relations**: Many surfaces call it during authentication or request handling before deciding whether to admit a message.

*Call graph*: calls 1 internal fn (_identity_member); called by 8 (_surface_ingest, _surface_live_admit, _viewer, _resolve_member, interactive, channel, op_body, _authenticate).


##### `SurfaceContext.is_operator_workspace`  (lines 1324–1331)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Checks whether the workspace appears to be the operator’s own workspace. It gates operator-only display details, not tenant powers.

**Data flow**: It reads the workspace email domain and compares it to the fixed operator domain.

**Call relations**: Surface renderers can use this before showing internal debugging or accounting extras.

*Call graph*: calls 1 internal fn (workspace_domain).


##### `SurfaceContext.adopt_identity`  (lines 1333–1356)

```
async def adopt_identity(self, peer_surface: str, external_id: str) -> UUID | None
```

**Purpose**: Links this surface’s external id to the same member already known by another surface. This lets one human keep one identity across surfaces.

**Data flow**: It receives a peer surface and external id, looks up the peer member, inserts a surface_identity row for this surface if found, logs races, and returns the member id or None.

**Call relations**: The sample live surface uses it when adopting an identity from a peer surface.

*Call graph*: calls 1 internal fn (_identity_member); called by 1 (_surface_live_admit); 3 external calls (insert, workspace_tx, log).


##### `SurfaceContext.link_member`  (lines 1358–1394)

```
async def link_member(self, external_id: str, email: str) -> UUID | None
```

**Purpose**: Links this surface’s external id to an existing workspace member by email. It does not create new members.

**Data flow**: It receives an external id and email, finds the oldest case-insensitive member match, inserts the identity link, logs races, and returns the member id or None.

**Call relations**: SurfaceContext.join_member builds on it, and several surfaces call it when they already trust an email address.

*Call graph*: called by 5 (join_member, _surface_ingest, _viewer, channel, _authenticate); 4 external calls (insert, select, workspace_tx, log).


##### `SurfaceContext.join_member`  (lines 1396–1413)

```
async def join_member(self, external_id: str, email: str) -> UUID | None
```

**Purpose**: Links an external id to a member, creating a new member when the verified email belongs to the workspace’s own domain. This supports first-contact teammate join for trusted channels.

**Data flow**: It receives external id and email. It first tries to link an existing member, then compares the email domain to the workspace domain, creates a member if allowed, and links again.

**Call relations**: Slack member resolution uses it for channel-verified emails.

*Call graph*: calls 2 internal fn (link_member, workspace_domain); called by 1 (_resolve_member); 3 external calls (workspace_tx, create_member, email_domain).


##### `SurfaceContext._conversation_lookup`  (lines 1415–1425)

```
def _conversation_lookup(self, queue_key: str) -> sa.Select
```

**Purpose**: Builds the standard database query for finding a conversation by this surface’s queue key. Keeping this in one helper avoids inconsistent lookup rules.

**Data flow**: It receives a queue key and returns a SQL select for the conversation id, member id, audience, and label within this workspace and surface.

**Call relations**: find_conversation, conversation_for, and terminal_op_body reuse this query.

*Call graph*: called by 3 (conversation_for, find_conversation, terminal_op_body); 1 external calls (select).


##### `SurfaceContext.find_conversation`  (lines 1427–1433)

```
async def find_conversation(self, queue_key: str) -> UUID | None
```

**Purpose**: Finds an existing conversation for this surface key without creating one. This is important for ambient replies that should only join an existing thread.

**Data flow**: It receives a queue key, runs the standard lookup, and returns the conversation id or None.

**Call relations**: Slack uses it to decide whether a thread is already participating.

*Call graph*: calls 1 internal fn (_conversation_lookup); called by 2 (_participating_conversation, interactive); 1 external calls (workspace_tx).


##### `SurfaceContext.conversation_agent`  (lines 1435–1448)

```
async def conversation_agent(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Finds the agent permanently bound to a conversation. This lets a surface enforce the agent wall before reading content.

**Data flow**: It receives a conversation id, queries this workspace’s conversation row, and returns the agent id or None.

**Call relations**: The web surface calls it while resolving chat URLs.

*Call graph*: called by 1 (_resolve_chat); 2 external calls (select, workspace_tx).


##### `SurfaceContext.retitle_conversation`  (lines 1450–1453)

```
async def retitle_conversation(self, conversation_id: UUID, title: str) -> None
```

**Purpose**: Surface-facing wrapper for changing a conversation title. It keeps the workspace id fixed to this context.

**Data flow**: It receives a conversation id and title and passes them with this workspace id to the shared retitle helper.

**Call relations**: The web surface calls it when opening or renaming a conversation.

*Call graph*: calls 1 internal fn (retitle_conversation); called by 1 (_open_conversation).


##### `SurfaceContext.conversation_for`  (lines 1455–1549)

```
async def conversation_for(self, queue_key: str, audience: Audience, agent_id: UUID | None=None, conversation_id: UUID | None=None, label: str | None=None) -> UUID
```

**Purpose**: Gets or creates the conversation for this surface and queue key. It also narrows audience information and binds new conversations to an agent.

**Data flow**: It receives queue key, audience, optional agent id, optional requested conversation id, and optional label. It reuses an existing row when present, narrows audience or updates labels as needed, otherwise validates or chooses an agent, inserts the row, and handles creation races.

**Call relations**: All admitting surfaces call this before SurfaceContext.admit so messages land in the correct durable conversation.

*Call graph*: calls 2 internal fn (_conversation_lookup, _surface_agent); called by 7 (_surface_ingest, _surface_live_admit, _admit_inbound, interactive, channel, submit_intent, _open_conversation); 9 external calls (insert, select, update, audience_member, narrow_audience, parse_audience, workspace_tx, log, uuid4).


##### `SurfaceContext._surface_agent`  (lines 1551–1563)

```
async def _surface_agent(self) -> UUID
```

**Purpose**: Finds the agent this surface installation is bound to, falling back to the workspace main agent. This decides where new surface conversations run.

**Data flow**: It reads the surface_installation row for this workspace and surface. If present it returns the bound agent id; otherwise it returns the main agent id.

**Call relations**: SurfaceContext.conversation_for calls it when creating a conversation without an explicit agent.

*Call graph*: calls 1 internal fn (_main_agent); called by 1 (conversation_for); 2 external calls (select, workspace_tx).


##### `SurfaceContext.ambient_reply_wanted`  (lines 1565–1596)

```
async def ambient_reply_wanted(self, message: AmbientMessage, history: tuple[AmbientMessage, ...]) -> bool
```

**Purpose**: Asks the ambient-reply classifier whether an unaddressed message in a thread should start a turn. It fails open so uncertain decisions do not silently drop a member request.

**Data flow**: It receives the new ambient message and recent history. It runs the classifier with a timeout, logs the decision or warning, and returns True unless a definite no-reply decision is received.

**Call relations**: Slack uses it before admitting ambient channel traffic.

*Call graph*: called by 1 (_ambient_reply_wanted); 3 external calls (wait_for, log, warn).


##### `SurfaceContext.admit`  (lines 1598–1629)

```
async def admit(self, conversation_id: UUID, body: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None, intent: ToolIntent | None=None) -> Admitt
```

**Purpose**: Admits an inbound member message or prepared intent into the durable turn system. This is the main write path from surfaces into core.

**Data flow**: It receives conversation id, body, optional idempotency key, context, speaker member id, and optional tool intent. It delegates to the injected MemberAdmitter and returns the Admitted result.

**Call relations**: Sample, Slack, UFO, web chat, and web panel intent submissions call it after resolving the conversation and speaker.

*Call graph*: called by 8 (_surface_ingest, _surface_live_admit, _admit_inbound, interactive, _send, channel, submit_intent, chat).


##### `SurfaceContext.connect_url`  (lines 1631–1637)

```
async def connect_url(self, turn_id: UUID, member_id: UUID) -> str
```

**Purpose**: Starts an authenticated connector handoff for a terminal connect request. It returns the URL the member should visit.

**Data flow**: It receives a turn id and member id, loads the installed connect flow, and asks ConnectHandoff to authorize it for this workspace.

**Call relations**: Slack interactive actions and web event handling call it when a turn asks the member to connect an account.

*Call graph*: called by 2 (interactive, _events); 3 external calls (__init__, __init__, installed_connect_flow).


##### `SurfaceContext.admitted_body`  (lines 1639–1664)

```
async def admitted_body(self, idempotency_key: str) -> str | None
```

**Purpose**: Looks up what body was stored for an idempotency key. This lets a surface tell which duplicate click or delivery actually won.

**Data flow**: It receives an idempotency key, first checks founding turn rows, then queued inbound messages, and returns the stored body or None.

**Call relations**: Slack and web code use it to reconcile races around answer buttons and chat submissions.

*Call graph*: called by 3 (_unseen_tail, interactive, chat); 2 external calls (select, workspace_tx).


##### `SurfaceContext.turn_owner`  (lines 1666–1680)

```
async def turn_owner(self, turn_id: UUID) -> UUID | None
```

**Purpose**: Finds the member who owns the conversation containing a turn. Live surfaces use this to stop members from tailing someone else’s private turn.

**Data flow**: It receives a turn id, joins turn to conversation inside this workspace, and returns the conversation member id or None.

**Call relations**: Sample and web surfaces call it when authorizing live turn streams.

*Call graph*: called by 2 (_surface_live_admit, _member_turn); 2 external calls (select, workspace_tx).


##### `SurfaceContext.stop_turn`  (lines 1682–1688)

```
async def stop_turn(self, conversation_id: UUID, turn_id: UUID) -> Stopped
```

**Purpose**: Stops a running turn after the surface has authorized the acting member. It returns whether stopping happened and whether a pending follow-up began.

**Data flow**: It receives conversation and turn ids, passes workspace, conversation, and turn ids to the injected stopper, and returns its Stopped result.

**Call relations**: UFO and web chat call it when a member requests cancellation.

*Call graph*: called by 2 (channel, chat).


##### `SurfaceContext.retract_arrival`  (lines 1690–1708)

```
async def retract_arrival(self, conversation_id: UUID, arrival_id: UUID, member_id: UUID) -> bool
```

**Purpose**: Deletes a pending message that the member sent but no turn has consumed yet. It only lets a member retract their own waiting message.

**Data flow**: It receives conversation id, arrival id, and member id. It deletes the matching unconsumed inbound_message row and returns whether one row was deleted.

**Call relations**: The UFO surface calls it for an unsend action.

*Call graph*: called by 1 (_unsend); 2 external calls (delete, workspace_tx).


##### `SurfaceContext.turn_is_terminal`  (lines 1710–1727)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Checks from durable storage whether a turn has ended. This avoids posting progress updates after the final answer has already been delivered.

**Data flow**: It receives a turn id, reads the turn status, and returns True if the row is missing or in a terminal status.

**Call relations**: Side-channel reporting code can call it before sending late updates.

*Call graph*: 2 external calls (select, workspace_tx).


##### `SurfaceContext.latest_turn`  (lines 1729–1746)

```
async def latest_turn(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Finds the newest turn in a conversation. This helps a live surface resume the right stream or re-render pending handoffs after reload.

**Data flow**: It receives a conversation id, orders that conversation’s turns by sequence descending, and returns the newest turn id or None.

**Call relations**: Slack, UFO, and web surfaces call it while reopening conversations or resuming live views.

*Call graph*: called by 4 (_participating_conversation, channel, _conversation_messages, _resolve_chat); 2 external calls (select, workspace_tx).


##### `SurfaceContext.tail`  (lines 1748–1754)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Subscribes to live frames for a turn through the injected tailer. This is the read half of live surface delivery.

**Data flow**: It receives a turn id and optional cursor and returns the tailer’s async stream context.

**Call relations**: Debugger, sample, UFO, web events, and web panel code call it to stream turn updates.

*Call graph*: called by 5 (_events, _surface_frames, channel, submit_intent, _events).


##### `SurfaceContext.spend_rollup`  (lines 1756–1759)

```
async def spend_rollup(self, window_seconds: int | None) -> SpendReport
```

**Purpose**: Reads workspace-wide usage and cost for a time window or all time. This feeds usage dashboards.

**Data flow**: It receives an optional window length, opens a workspace transaction, and returns the SpendRollup report.

**Call relations**: Sample and web workspace usage views call it.

*Call graph*: called by 2 (_surface_live_admit, workspace_usage); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.write_workspace_file`  (lines 1761–1776)

```
async def write_workspace_file(self, conversation_id: UUID, rel: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes an uploaded file into a conversation’s sandbox workspace before a turn runs. It enforces a maximum size while reading the stream.

**Data flow**: It receives conversation id, relative path, and byte chunks. It accumulates chunks up to the limit, raises if too large, then writes the bytes through the sandbox carrier.

**Call relations**: Sample, Slack attachment download, and web upload handling call it before admitting or running turns.

*Call graph*: called by 3 (_surface_ingest, _download_files, _deliver_uploads).


##### `SurfaceContext.list_agents`  (lines 1778–1804)

```
async def list_agents(self) -> tuple[AgentSummary, ...]
```

**Purpose**: Lists all agents in the workspace, with the main one first. Surfaces can then apply their own audience checks before showing choices.

**Data flow**: It reads agent rows for this workspace, orders them, and returns AgentSummary objects.

**Call relations**: The web audience code calls it when deciding which agents a signed-in member may access.

*Call graph*: called by 1 (web_audience); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.agent_detail`  (lines 1806–1855)

```
async def agent_detail(self, agent_id: UUID) -> AgentDetail | None
```

**Purpose**: Reads one agent’s full portal overview information. It includes configuration, prompt digest, and surfaces bound to the agent.

**Data flow**: It receives an agent id, reads the agent row and bound installations, returns AgentDetail, or returns None if the agent is absent.

**Call relations**: Web panels use it for agent overview pages and intent submission context.

*Call graph*: called by 2 (agent_overview, submit_intent); 4 external calls (__init__, select, workspace_tx, prompt_digest).


##### `SurfaceContext.object_kind`  (lines 1857–1869)

```
def object_kind(self, kind: str) -> 'PortalKind | None'
```

**Purpose**: Returns portal metadata for one registered object kind. This tells the portal what fields can be filtered or ordered and what schema to render.

**Data flow**: It receives a kind name, looks it up in the bound object registry, and returns PortalKind or None.

**Call relations**: The web surface calls it in its object route gate.

*Call graph*: called by 1 (_object_gate); 1 external calls (__init__).


##### `SurfaceContext.agent_skills`  (lines 1871–1889)

```
async def agent_skills(self, agent_id: UUID) -> tuple[PortalSkill, ...]
```

**Purpose**: Lists the deploy and member-authored skills available to one agent. It mirrors the skill composition a turn would actually load.

**Data flow**: It receives an agent id, binds that agent scope, merges deploy skills with user skills, labels each top-level skill by origin, and returns PortalSkill objects.

**Call relations**: The web skills page calls it.

*Call graph*: called by 1 (skills); 2 external calls (__init__, agent).


##### `SurfaceContext.memory_available`  (lines 1892–1896)

```
def memory_available(self) -> bool
```

**Purpose**: Reports whether a memory-search provider is installed. This lets the portal hide memory UI when unsupported.

**Data flow**: It checks whether the context has a memory provider and returns a boolean.

**Call relations**: Web memory routes should gate calls to search_memory and recent_memory on this value.


##### `SurfaceContext.search_memory`  (lines 1898–1908)

```
async def search_memory(self, reader: 'SourceReader', queries: tuple[str, ...]) -> 'tuple[MemoryMatch, ...]'
```

**Purpose**: Searches readable memory using the same reader shape the agent tools use. It refuses to run if no memory provider exists.

**Data flow**: It receives a SourceReader and search queries, checks a provider is installed, delegates to the provider, and returns memory matches.

**Call relations**: The web workspace memory page calls it for query searches.

*Call graph*: called by 1 (workspace_memory).


##### `SurfaceContext.recent_memory`  (lines 1910–1923)

```
async def recent_memory(self, subjects: frozenset[str], limit: int, kinds: 'frozenset[str] | None'=None, cursor: 'ListingCursor | None'=None) -> 'ListingPage[MemoryMatch]'
```

**Purpose**: Lists recent readable memory items without a search query. It supports paging and optional kind filtering.

**Data flow**: It receives readable subjects, limit, optional kinds, and cursor. It checks for a memory provider and delegates to list_recent.

**Call relations**: The web workspace memory page calls it for browsing memory.

*Call graph*: called by 1 (workspace_memory).


##### `SurfaceContext.memory_kinds`  (lines 1926–1931)

```
def memory_kinds(self) -> tuple[str, ...]
```

**Purpose**: Returns the memory item classes the installed provider can list. This feeds portal filters.

**Data flow**: It checks that a memory provider exists and returns its listable kinds.

**Call relations**: Memory UI can call it after checking memory_available.


##### `SurfaceContext.agent_spend`  (lines 1933–1938)

```
async def agent_spend(self, agent_id: UUID, window_seconds: int | None) -> AgentSpendReport
```

**Purpose**: Reads usage and spending for one agent, including caps. This supports per-agent billing views.

**Data flow**: It receives agent id and optional window seconds, opens a transaction, and asks SpendRollup for the agent report.

**Call relations**: The web usage page calls it.

*Call graph*: called by 1 (usage); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.member_spend`  (lines 1940–1945)

```
async def member_spend(self, member_id: UUID, window_seconds: int | None) -> MemberSpendReport
```

**Purpose**: Reads usage and spending for one member, including caps. This supports member or workspace usage views.

**Data flow**: It receives member id and optional window seconds, opens a transaction, and asks SpendRollup for the member report.

**Call relations**: The web workspace usage page calls it.

*Call graph*: called by 1 (workspace_usage); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.list_agent_connections`  (lines 1947–1999)

```
async def list_agent_connections(self, agent_id: UUID, member_id: UUID, *, admin: bool) -> tuple[ConnectionView, ...]
```

**Purpose**: Lists connector accounts granted to one agent that the requesting member may see. Admins see all; non-admins see shared or own grants.

**Data flow**: It receives agent id, member id, and admin flag. It builds a visibility-limited query and returns ConnectionView records with owner, sharing, and grant names.

**Call relations**: The web connections page calls it for an agent’s connection panel.

*Call graph*: called by 1 (connections); 5 external calls (__init__, or_, select, workspace_tx, account_object_name).


##### `SurfaceContext.list_connections`  (lines 2001–2070)

```
async def list_connections(self, member_id: UUID, *, admin: bool) -> tuple[ConnectionPoolView, ...]
```

**Purpose**: Lists the workspace connection pool visible to the requester, grouped with the agents each connection is attached to. It protects private owner names for non-visible private connections.

**Data flow**: It receives member id and admin flag, queries connections and optional grants, groups rows by provider and account, attaches agent summaries, and returns ConnectionPoolView records.

**Call relations**: The web connection pool page calls it.

*Call graph*: called by 1 (connection_pool); 6 external calls (__init__, __init__, or_, select, workspace_tx, account_object_name).


##### `SurfaceContext.github_coverage`  (lines 2072–2120)

```
async def github_coverage(self, member_id: UUID, *, admin: bool) -> GithubCoverageView
```

**Purpose**: Reports whether GitHub is configured through API connections, Git push credentials, and sources. It gives a compact setup-health view.

**Data flow**: It receives member id and admin flag, applies visibility rules, checks for GitHub connection rows, source rows, and credential slots, and returns GithubCoverageView.

**Call relations**: The web GitHub coverage route calls it.

*Call graph*: called by 1 (github_coverage); 6 external calls (__init__, exists, or_, select, true, workspace_tx).


##### `SurfaceContext.list_artifacts`  (lines 2122–2214)

```
async def list_artifacts(self, member_id: UUID, *, admin: bool, limit: int, cursor: 'ListingCursor | None'=None, q: str | None=None, media: str | None=None) -> 'ListingPage[ListedArtifact]'
```

**Purpose**: Returns a paged list of shared files visible to the requester. It supports search text and media-type filtering.

**Data flow**: It receives member/admin information, limit, cursor, optional query text, and optional media category. It applies visibility, search, and media filters, keyset-pages the database rows, and returns ListedArtifact entries.

**Call relations**: The web workspace artifacts page calls it, and it uses _media_predicate for media filtering.

*Call graph*: calls 1 internal fn (_media_predicate); called by 1 (workspace_artifacts); 6 external calls (or_, select, readable_audiences, workspace_tx, page_of, page_query).


##### `SurfaceContext.list_conversation_artifacts`  (lines 2216–2276)

```
async def list_conversation_artifacts(self, conversation_id: UUID, *, limit: int) -> tuple[ListedArtifact, ...]
```

**Purpose**: Lists recent shared files for a single conversation. The caller is responsible for authorizing conversation access before calling.

**Data flow**: It receives conversation id and limit, queries artifacts joined through turns in this workspace and conversation, and returns ListedArtifact rows.

**Call relations**: The web surface uses it while projecting conversation slot context.

*Call graph*: called by 1 (_project_slot_context); 4 external calls (__init__, __init__, select, workspace_tx).


##### `SurfaceContext.list_scheduled_runs`  (lines 2278–2381)

```
async def list_scheduled_runs(self, member_id: UUID, *, limit: int, cursor: 'ListingCursor | None'=None, agent_id: UUID | None=None) -> 'ListingPage[ScheduledRun]'
```

**Purpose**: Lists completed turns that fired automatically, such as scheduled tasks, where the member may read the resulting conversation. It includes final text and shared artifacts.

**Data flow**: It receives member id, limit, optional cursor, and optional agent id. It pages terminal scheduled turns, loads their files and conversation sources, and returns ScheduledRun rows.

**Call relations**: The web workspace radar page calls it; it uses _conversation_sources to include the origin link.

*Call graph*: calls 1 internal fn (_conversation_sources); called by 1 (workspace_radar); 6 external calls (__init__, select, readable_audiences, workspace_tx, page_of, page_query).


##### `SurfaceContext.list_member_objects`  (lines 2383–2409)

```
async def list_member_objects(self, kind: str, agent_id: UUID, member_id: UUID, *, admin: bool, query: 'ObjectListQuery') -> 'ObjectPage | None'
```

**Purpose**: Lists portal-visible objects of one registered kind for a signed-in member. The object kind’s own store enforces detailed visibility.

**Data flow**: It receives kind, agent id, member id, admin flag, and query. It finds a listable kind, binds the agent scope, stamps supported fields onto the query, and returns an object page or None.

**Call relations**: The web object index and radar task-name helpers call it.

*Call graph*: called by 2 (_radar_task_names, object_index); 2 external calls (replace, agent).


##### `SurfaceContext.member_object`  (lines 2411–2426)

```
async def member_object(self, kind: str, name: str, agent_id: UUID, member_id: UUID, *, admin: bool) -> 'MemberObject | None'
```

**Purpose**: Reads one portal-visible object detail for a signed-in member. Hidden and absent objects both return None.

**Data flow**: It receives kind, object name, agent id, member id, and admin flag. It finds a readable kind, binds agent scope, delegates to member_detail, and returns the object or None.

**Call relations**: The web object detail route calls it.

*Call graph*: called by 1 (object_detail); 1 external calls (agent).


##### `SurfaceContext.list_conversation_member_objects`  (lines 2428–2450)

```
async def list_conversation_member_objects(self, kind: str, agent_id: UUID, conversation_id: UUID, member_id: UUID, *, admin: bool, limit: int) -> tuple['ConversationObjectGrant', ...] | None
```

**Purpose**: Lists object grants related to a conversation for a member-visible object kind. It is used to enrich conversation context in the portal.

**Data flow**: It receives kind, agent id, conversation id, member id, admin flag, and limit. It finds a compatible kind, binds agent scope, and delegates to the store.

**Call relations**: The web surface calls it while projecting slot context.

*Call graph*: called by 1 (_project_slot_context); 1 external calls (agent).


##### `SurfaceContext.list_credential_slots`  (lines 2452–2483)

```
async def list_credential_slots(self) -> tuple[CredentialSlotView, ...]
```

**Purpose**: Lists member-fillable credential slots and whether each is filled, never the secret value. It hides deploy-written slots members cannot act on.

**Data flow**: It reads filled credential slot names from the database, maps declared slots to object names, filters to member-fillable declarations, and returns CredentialSlotView rows.

**Call relations**: Web credential panels and intent submissions call it.

*Call graph*: called by 2 (submit_intent, workspace_credentials); 4 external calls (__init__, select, named_slots, workspace_tx).


##### `SurfaceContext.workspace_domain`  (lines 2485–2491)

```
async def workspace_domain(self) -> str | None
```

**Purpose**: Returns the workspace’s own email domain. This is used for trusted self-join decisions.

**Data flow**: It opens a workspace transaction and asks the seats helper for the workspace domain, returning a string or None.

**Call relations**: SurfaceContext.join_member and is_operator_workspace call it.

*Call graph*: called by 2 (is_operator_workspace, join_member); 2 external calls (workspace_tx, workspace_domain).


##### `SurfaceContext.list_members`  (lines 2493–2501)

```
async def list_members(self) -> tuple[SeatEntry, ...]
```

**Purpose**: Returns the workspace roster sorted by email. This gives stable rows for the team panel.

**Data flow**: It takes a seats snapshot in a transaction, sorts member entries by email, and returns them.

**Call relations**: The web workspace team page calls it.

*Call graph*: called by 1 (workspace_team); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.list_sources`  (lines 2503–2548)

```
async def list_sources(self, member_id: UUID, *, admin: bool) -> tuple[SourceView, ...]
```

**Purpose**: Lists live source bindings visible to the requester. It includes owner, sharing, sync health, and connector identity fields.

**Data flow**: It receives member id and admin flag, applies source visibility rules, queries non-removed sources, adds _binding_fields from each config, and returns SourceView rows.

**Call relations**: The web workspace sources page calls it.

*Call graph*: calls 1 internal fn (_binding_fields); called by 1 (workspace_sources); 4 external calls (__init__, or_, select, workspace_tx).


##### `SurfaceContext.spend_caps`  (lines 2550–2598)

```
async def spend_caps(self) -> tuple[SpendCapView, ...]
```

**Purpose**: Lists all spend caps in the workspace with human-readable subject names. It supports the administration billing view.

**Data flow**: It queries cap rows joined to agents or members as appropriate, orders them, and returns SpendCapView objects.

**Call relations**: The web admin index calls it.

*Call graph*: called by 1 (admin_index); 4 external calls (__init__, and_, select, workspace_tx).


##### `SurfaceContext.list_installations`  (lines 2600–2616)

```
async def list_installations(self) -> tuple[InstallationSummary, ...]
```

**Purpose**: Lists surface installations bound to the workspace and their agents. This shows which chat surfaces are connected.

**Data flow**: It queries surface_installation rows for the workspace, orders by surface, and returns InstallationSummary objects.

**Call relations**: The web admin index calls it.

*Call graph*: called by 1 (admin_index); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_conversations`  (lines 2618–2667)

```
async def list_conversations(self, limit: int=LIST_CONVERSATIONS_LIMIT) -> tuple[ConversationSummary, ...]
```

**Purpose**: Lists workspace conversations across surfaces for debug-style views. It includes basic activity counts but not transcript content.

**Data flow**: It builds an activity subquery from turns, joins conversations and members, orders by latest activity, applies the limit, and returns ConversationSummary rows.

**Call relations**: The debugger surface calls it for its conversation list.

*Call graph*: called by 1 (conversations); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_agent_conversations`  (lines 2669–2782)

```
async def list_agent_conversations(self, agent_id: UUID, member_id: UUID, *, admin: bool, limit: int, surface: str | None=None, conversation_id: UUID | None=None, participation: Literal['mine', 'other
```

**Purpose**: Lists one agent’s member-facing conversations for the portal, with privacy-aware content fields. It separates readable content from administrative metadata.

**Data flow**: It receives agent, member, admin, limit, and optional filters. It queries conversations, applies participation/search/visibility filters, then separately loads source links and speakers only for readable rows, returning ListedConversation objects.

**Call relations**: Web chat index, conversation, named-chat, and resolve-chat flows call it; it uses helper predicates and source/speaker loaders.

*Call graph*: calls 5 internal fn (_conversation_sources, _conversation_speakers, _matches, _others, _participated); called by 4 (_named, _resolve_chat, chats_index, conversations); 8 external calls (__init__, __init__, select, audience_member, conversation_audience, parse_audience, readable_audiences, workspace_tx).


##### `SurfaceContext._spoken`  (lines 2784–2805)

```
def _spoken(self, member_id: UUID | None) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a database condition saying whether a conversation contains member speech. It can check for one member or for any member.

**Data flow**: It receives an optional member id and returns an EXISTS SQL condition over turns for the current conversation row.

**Call relations**: _participated and _others use it while filtering conversation listings.

*Call graph*: called by 2 (_others, _participated); 2 external calls (literal, select).


##### `SurfaceContext._participated`  (lines 2807–2815)

```
def _participated(self, member_id: UUID) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a condition for conversations the member participated in. A member participated if the conversation is bound to them or they spoke in it.

**Data flow**: It receives member id and returns a SQL OR condition combining conversation ownership and the _spoken check.

**Call relations**: list_agent_conversations uses it for the participation='mine' filter.

*Call graph*: calls 1 internal fn (_spoken); called by 1 (list_agent_conversations); 1 external calls (or_).


##### `SurfaceContext._others`  (lines 2817–2829)

```
def _others(self, member_id: UUID) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a condition for readable conversations where someone else spoke and this member did not. This powers the “others” side of portal rails.

**Data flow**: It receives member id and returns a SQL condition excluding this member’s ownership and speech while requiring some member speech.

**Call relations**: list_agent_conversations uses it for the participation='others' filter.

*Call graph*: calls 1 internal fn (_spoken); called by 1 (list_agent_conversations); 2 external calls (and_, not_).


##### `SurfaceContext._matches`  (lines 2831–2860)

```
def _matches(self, search: str, member_id: UUID) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a privacy-aware search condition for conversations. It only searches transcript-derived fields when the requester can read that content.

**Data flow**: It receives search text and member id, returns a SQL condition matching surface label, owning email, and, for readable audiences only, title or speaker email.

**Call relations**: Agent and subagent conversation listings use it when a search term is supplied.

*Call graph*: called by 2 (list_agent_conversations, list_subagent_conversations); 5 external calls (and_, literal, or_, select, readable_audiences).


##### `SurfaceContext._conversation_sources`  (lines 2862–2897)

```
async def _conversation_sources(self, listed: Sequence[UUID]) -> dict[UUID, str | None]
```

**Purpose**: Loads the source link or origin string from each listed conversation’s opening turn. This is content, so callers pass only conversations the viewer may read.

**Data flow**: It receives conversation ids, finds each conversation’s first turn, validates stored turn context, and returns a map from conversation id to source string or None.

**Call relations**: Conversation listings, scheduled-run listings, and subagent detail reads use it to display where a conversation began.

*Call graph*: called by 4 (list_agent_conversations, list_scheduled_runs, list_subagent_conversations, readable_subagent_conversation); 4 external calls (model_validate, and_, select, workspace_tx).


##### `SurfaceContext._conversation_speakers`  (lines 2899–2955)

```
async def _conversation_speakers(self, listed: Sequence[UUID]) -> dict[UUID, tuple[ConversationSpeaker, ...]]
```

**Purpose**: Loads the first few distinct member speakers for each listed conversation. It preserves first-speaking order.

**Data flow**: It receives conversation ids, queries turn speakers with window ranking, parses context for display sender names, and returns a map to ConversationSpeaker tuples.

**Call relations**: Conversation and subagent listings call it only for readable conversations.

*Call graph*: called by 3 (list_agent_conversations, list_subagent_conversations, readable_subagent_conversation); 4 external calls (__init__, model_validate, select, workspace_tx).


##### `SurfaceContext.readable_conversation`  (lines 2957–2998)

```
async def readable_conversation(self, conversation_id: UUID, agent_id: UUID, member_id: UUID, *, admin: bool=False) -> bool
```

**Purpose**: Answers whether a member may read a conversation’s content. It enforces member/shared access and short-lived admin disclosure grants.

**Data flow**: It receives conversation id, agent id, member id, and admin flag. It checks the conversation exists under the agent, accepts normal readable audiences, otherwise checks admin status, private-member audience, and recent transcript_access row.

**Call relations**: Web route gates and readable_subagent_conversation rely on it before serving transcripts, files, or subagent content.

*Call graph*: called by 2 (readable_subagent_conversation, _readable_conversation); 6 external calls (now, select, audience_member, parse_audience, readable_audiences, workspace_tx).


##### `SurfaceContext.conversation_audience`  (lines 3000–3010)

```
async def conversation_audience(self, conversation_id: UUID, agent_id: UUID) -> Audience | None
```

**Purpose**: Reads the audience bound to a conversation for a given agent. It returns None if the row is absent.

**Data flow**: It receives conversation and agent ids, reads the audience string in this workspace, parses it, and returns an Audience object or None.

**Call relations**: The web surface calls it while building conversation slot context.

*Call graph*: called by 1 (_slot_context); 3 external calls (select, parse_audience, workspace_tx).


##### `SurfaceContext.list_subagent_conversations`  (lines 3012–3110)

```
async def list_subagent_conversations(self, profile: str, member_id: UUID, agent_ids: frozenset[UUID], *, admin: bool, limit: int, search: str | None=None) -> tuple[SubagentRun, ...]
```

**Purpose**: Lists conversations created by one subagent profile, across the agents the requester may access. It keeps subagent work separate from normal member conversations.

**Data flow**: It receives profile, member, allowed agent ids, admin flag, limit, and optional search. It queries subagent conversations with activity, applies visibility and search, loads sources and speakers for readable rows, and returns SubagentRun entries.

**Call relations**: The web subagent conversations page calls it.

*Call graph*: calls 3 internal fn (_conversation_sources, _conversation_speakers, _matches); called by 1 (subagent_conversations); 6 external calls (__init__, __init__, __init__, select, readable_audiences, workspace_tx).


##### `SurfaceContext.readable_subagent_conversation`  (lines 3112–3230)

```
async def readable_subagent_conversation(self, conversation_id: UUID, profile: str, member_id: UUID, agent_ids: frozenset[UUID], *, root_conversation_id: UUID | None, admin: bool) -> SubagentRun | Non
```

**Purpose**: Gates and describes one subagent conversation for a detail page. It can authorize through the root conversation that spawned the child.

**Data flow**: It receives conversation id, profile, member, allowed agents, optional root conversation id, and admin flag. It verifies the child profile and agent, optionally verifies it was spawned from the root, checks readability, loads source and speakers, and returns SubagentRun or None.

**Call relations**: The web subagent conversation page calls it before showing a transcript.

*Call graph*: calls 3 internal fn (_conversation_sources, _conversation_speakers, readable_conversation); called by 1 (subagent_conversation); 5 external calls (__init__, __init__, __init__, select, workspace_tx).


##### `SurfaceContext.conversation_subagent_turns`  (lines 3232–3269)

```
async def conversation_subagent_turns(self, conversation_id: UUID, limit: int=LIST_TURNS_LIMIT) -> tuple[Turn, ...]
```

**Purpose**: Lists all subagent turns spawned under a conversation, including nested children. It gives the portal the tree of delegated work.

**Data flow**: It receives a conversation id and limit, builds a recursive database query over parent_turn_id, orders parents before children, and returns Turn records.

**Call relations**: Web conversation message, event, and slot-target flows use it to nest subagent activity under the main conversation.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 3 (_conversation_messages, _events, _slot_target); 3 external calls (literal, select, workspace_tx).


##### `SurfaceContext.list_turns`  (lines 3271–3287)

```
async def list_turns(self, conversation_id: UUID, limit: int=LIST_TURNS_LIMIT) -> tuple[Turn, ...]
```

**Purpose**: Lists the recent turns of a conversation in admission order. It returns full durable turn records.

**Data flow**: It receives conversation id and limit, queries the latest matching turns descending, reverses them to oldest-first, and converts rows to Turn objects.

**Call relations**: Debugger and web conversation message views call it.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 2 (conversation_turns, _conversation_messages); 1 external calls (workspace_tx).


##### `SurfaceContext.agent_origin_refs`  (lines 3289–3324)

```
async def agent_origin_refs(self, conversation_id: UUID) -> frozenset[str]
```

**Purpose**: Finds message references that are machine-origin envelopes rather than member prose, such as scheduled fires or subagent results. This helps transcript renderers avoid showing machine envelopes as chat bubbles.

**Data flow**: It receives a conversation id, unions matching turn ids and inbound-message ids, and returns them as strings in a frozen set.

**Call relations**: The web conversation message projection calls it while deciding how to render transcript entries.

*Call graph*: called by 1 (_conversation_messages); 4 external calls (or_, select, union_all, workspace_tx).


##### `SurfaceContext.turn_detail`  (lines 3326–3376)

```
async def turn_detail(self, turn_id: UUID) -> TurnDetail | None
```

**Purpose**: Reads one turn with its accounting rows and direct subagent children. It is the detailed inspection view for a turn.

**Data flow**: It receives a turn id, loads the turn row, child turn rows, and ledger entries in one transaction, converts them to models, and returns TurnDetail or None.

**Call relations**: Debugger and web event/message/resolve flows call it when they need durable turn details.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 6 (stream, turn, _conversation_messages, _events, _member_turn, _resolve_chat); 4 external calls (__init__, __init__, select, workspace_tx).


##### `SurfaceContext.queued_arrivals`  (lines 3378–3419)

```
async def queued_arrivals(self, conversation_id: UUID, draining_turn_id: UUID | None) -> tuple[QueuedArrival, ...]
```

**Purpose**: Lists admitted inbound messages that are not yet written into the transcript. This keeps reloads from hiding messages while a turn is still running.

**Data flow**: It receives conversation id and optional draining turn id, first checks the conversation belongs to this workspace, then returns unconsumed rows plus rows consumed by the named running turn.

**Call relations**: The web conversation message projection calls it after listing turns.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (_conversation_messages); 5 external calls (__init__, false, or_, select, workspace_tx).


##### `SurfaceContext.arrival_speakers`  (lines 3421–3449)

```
async def arrival_speakers(self, conversation_id: UUID) -> tuple[SpokenArrival, ...]
```

**Purpose**: Returns speaker attribution for member-admitted queued messages, including drained ones. This lets folded messages be labelled correctly in projections.

**Data flow**: It receives conversation id, verifies ownership, reads member-admitted inbound_message rows, parses sender from context, and returns SpokenArrival records.

**Call relations**: The web conversation message projection uses it beside turn rows.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (_conversation_messages); 4 external calls (__init__, __init__, select, workspace_tx).


##### `SurfaceContext.read_transcript`  (lines 3451–3462)

```
async def read_transcript(self, conversation_id: UUID) -> Conversation | None
```

**Purpose**: Reads the durable transcript blob for a conversation after verifying workspace ownership. This prevents cross-workspace blob access.

**Data flow**: It receives conversation id, checks ownership, fetches the transcript blob by key, decodes it, and returns the Conversation or None if absent.

**Call relations**: Debugger, UFO, and web transcript and slot-context views call it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 5 (conversation_transcript, channel, _conversation_messages, _slot_context, _subagent_nodes); 2 external calls (decode, transcript_key).


##### `SurfaceContext.list_compactions`  (lines 3464–3475)

```
async def list_compactions(self, conversation_id: UUID) -> tuple[int, ...]
```

**Purpose**: Lists available compaction record indices for a conversation. Compaction records describe transcript summarization events.

**Data flow**: It receives conversation id, checks ownership, lists matching blob keys, extracts numeric indices, sorts them, and returns the tuple.

**Call relations**: The debugger surface calls it for compaction listings.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (conversation_compactions).


##### `SurfaceContext.read_compaction`  (lines 3477–3483)

```
async def read_compaction(self, conversation_id: UUID, index: int) -> CompactionRecord | None
```

**Purpose**: Reads one persisted compaction record after verifying the conversation belongs to this workspace.

**Data flow**: It receives conversation id and index, checks ownership, and returns the compaction record from blob storage or None.

**Call relations**: The debugger surface calls it when opening a compaction record.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (compaction_record); 1 external calls (read_compaction_record).


##### `SurfaceContext.list_workspace_files`  (lines 3485–3491)

```
async def list_workspace_files(self, conversation_id: UUID) -> tuple[WorkspaceFile, ...]
```

**Purpose**: Lists member-visible files in a conversation’s sandbox workspace. It returns empty when the conversation is not owned or no sandbox exists.

**Data flow**: It receives conversation id, checks ownership, and delegates to the sandbox entries call.

**Call relations**: The debugger workspace-files route calls it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (workspace_files).


##### `SurfaceContext.conversation_changes`  (lines 3493–3499)

```
async def conversation_changes(self, conversation_id: UUID) -> WorkspaceChanges
```

**Purpose**: Returns recorded workspace file changes for a conversation. This powers portal views that show what changed after a turn.

**Data flow**: It receives conversation id, checks ownership, and returns recorded changes or NOTHING_CHANGED.

**Call relations**: The web surface calls it while projecting slot context.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (_project_slot_context); 1 external calls (recorded_workspace_changes).


##### `SurfaceContext.read_workspace_file`  (lines 3501–3510)

```
async def read_workspace_file(self, conversation_id: UUID, rel: str) -> AsyncIterator[bytes] | None
```

**Purpose**: Streams one file from a conversation’s sandbox workspace after checking ownership. It returns None for missing or unauthorized files.

**Data flow**: It receives conversation id and relative path, checks ownership, and returns an async byte stream from the sandbox or None.

**Call relations**: The debugger workspace-file route calls it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (workspace_file).


##### `SurfaceContext.terminal_connect`  (lines 3512–3516)

```
def terminal_connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None) -> None
```

**Purpose**: Registers that a member’s held connection is acting as the terminal for a conversation. This lets sandbox terminal operations rendezvous with the client.

**Data flow**: It receives conversation id, current directory, and member id, and records the connection in the sandbox terminal registry.

**Call relations**: Live terminal-capable surfaces call it when a terminal connection opens.


##### `SurfaceContext.terminal_disconnect`  (lines 3518–3519)

```
def terminal_disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: Removes the terminal connection for a conversation when the held connection closes.

**Data flow**: It receives a conversation id and tells the sandbox terminal registry to disconnect it.

**Call relations**: Live terminal-capable surfaces pair this with terminal_connect during connection teardown.


##### `SurfaceContext.claim_terminal`  (lines 3521–3527)

```
async def claim_terminal(self, conversation_id: UUID, cwd: str) -> bool
```

**Purpose**: Binds a new conversation to the currently connected terminal if it has no sandbox binding yet. This prevents the first sandbox use from silently opening elsewhere.

**Data flow**: It receives conversation id and current directory, delegates to the sandbox claim operation, and returns whether this call made the claim.

**Call relations**: The UFO live surface calls it around message send and channel setup.

*Call graph*: called by 2 (_send, channel).


##### `SurfaceContext.next_terminal_op`  (lines 3529–3536)

```
async def next_terminal_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: Waits for the next terminal operation requested by a turn. The optional excluded op prevents re-sending the one just answered.

**Data flow**: It receives conversation id and optional op id to exclude, delegates to the terminal registry, and returns a TerminalOp.

**Call relations**: Live terminal streams use it while racing terminal operations with normal turn frames.


##### `SurfaceContext.terminal_resolve`  (lines 3538–3552)

```
def terminal_resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None, member_id: UUID | None) -> bool
```

**Purpose**: Answers an in-flight terminal operation with bytes or a failure message. The terminal registry enforces the single-use op and member binding.

**Data flow**: It receives conversation id, op id, reply bytes, optional failure text, and member id. It delegates to terminal resolution and returns whether resolution succeeded.

**Call relations**: The UFO channel route calls it when a client posts terminal results.

*Call graph*: called by 1 (channel).


##### `SurfaceContext.terminal_op_body`  (lines 3554–3566)

```
async def terminal_op_body(self, queue_key: str, op_id: str, member_id: UUID | None) -> bytes | None
```

**Purpose**: Reads staged bytes for an in-flight terminal operation without creating a conversation. It is used by clients that fetch operation bodies separately.

**Data flow**: It receives queue key, op id, and member id, looks up the existing conversation by queue key, then asks the terminal registry for staged bytes.

**Call relations**: The UFO op_body route calls it.

*Call graph*: calls 1 internal fn (_conversation_lookup); called by 1 (op_body); 1 external calls (workspace_tx).


##### `SurfaceContext.installation`  (lines 3568–3581)

```
async def installation(self, peer_surface: str) -> str | None
```

**Purpose**: Reads this workspace’s installation id for another surface. This supports deep links or metadata that mention where a conversation lives.

**Data flow**: It receives a peer surface name, queries surface_installation, and returns the installation id or None.

**Call relations**: The debugger workspace metadata route calls it.

*Call graph*: called by 1 (workspace_meta); 2 external calls (select, workspace_tx).


##### `SurfaceContext.transaction`  (lines 3584–3593)

```
async def transaction(self) -> AsyncIterator[AsyncConnection]
```

**Purpose**: Gives a surface a raw workspace database transaction for extension-owned tables. The surface must still scope its own SQL correctly.

**Data flow**: It opens a workspace transaction, yields the async connection, commits on normal exit, and rolls back on error through the transaction context.

**Call relations**: Surface extensions can use it when they need to render their own migrated data outside a turn.

*Call graph*: 1 external calls (workspace_tx).


##### `SurfaceContext._owned_conversation`  (lines 3595–3605)

```
async def _owned_conversation(self, conversation_id: UUID) -> bool
```

**Purpose**: Checks whether a conversation id belongs to this workspace. It is the safety gate before unscoped blob or sandbox reads.

**Data flow**: It receives a conversation id, queries the conversation table for this workspace, and returns a boolean.

**Call relations**: Transcript, compaction, queued-arrival, workspace-file, and change-reading helpers call it before accessing external storage.

*Call graph*: called by 8 (arrival_speakers, conversation_changes, list_compactions, list_workspace_files, queued_arrivals, read_compaction, read_transcript, read_workspace_file); 2 external calls (select, workspace_tx).


##### `SurfaceContext._turn_query`  (lines 3607–3625)

```
def _turn_query(self) -> sa.Select
```

**Purpose**: Builds the standard SELECT for durable turn rows. This keeps all turn projections selecting the same fields.

**Data flow**: It takes no input and returns a SQL select object with turn columns needed to build a Turn model.

**Call relations**: list_turns, conversation_subagent_turns, and turn_detail use it.

*Call graph*: called by 3 (conversation_subagent_turns, list_turns, turn_detail); 1 external calls (select).


##### `SurfaceContext._turn_record`  (lines 3627–3645)

```
def _turn_record(self, row: sa.Row) -> Turn
```

**Purpose**: Converts a database row into the Turn data model. It validates nested context and terminal JSON into typed objects.

**Data flow**: It receives a SQL row, copies scalar fields, parses optional context and terminal fields, and returns a Turn.

**Call relations**: Turn-listing and turn-detail methods call it after executing _turn_query.

*Call graph*: called by 3 (conversation_subagent_turns, list_turns, turn_detail); 3 external calls (__init__, model_validate, model_validate).


##### `SurfaceInstallationAccess.bind`  (lines 3666–3671)

```
async def bind(self, surface: str, installation_id: str) -> None
```

**Purpose**: Lets a tool bind an installation only for surfaces declared in its manifest. This prevents tools from registering arbitrary surface names.

**Data flow**: It receives a surface name and installation id, checks the surface is declared, reads the ambient workspace id, and calls the shared binding helper.

**Call relations**: Tool code uses this installation registry path instead of writing surface_installation rows directly.

*Call graph*: calls 1 internal fn (_bind_surface_installation); 2 external calls (__init__, ws_current).


##### `SurfaceAuth.workspace`  (lines 3683–3693)

```
async def workspace(self, installation_id: str) -> UUID | None
```

**Purpose**: Resolves an external installation id to the workspace that owns it before a request has a SurfaceContext. This is the shared-surface pre-binding lookup.

**Data flow**: It receives an installation id, queries owner-scoped storage for this surface and installation, and returns the workspace id or None.

**Call relations**: Slack workspace resolution calls it when handling incoming shared requests.

*Call graph*: called by 1 (resolve_workspace); 2 external calls (select, owner_tx).


##### `SurfaceAuth.open_credential_authorization`  (lines 3695–3707)

```
def open_credential_authorization(self, sealed: str) -> CredentialRequestState | None
```

**Purpose**: Opens a sealed credential handoff before the workspace has been resolved. It returns None instead of raising for invalid seals.

**Data flow**: It receives sealed text, checks a credential store exists, tries to verify and open the seal, and returns the state or None.

**Call relations**: Slack workspace resolution uses it during OAuth-style pre-binding flows.

*Call graph*: called by 1 (resolve_workspace); 1 external calls (open_credential_request).


##### `SurfaceAuth.credential`  (lines 3709–3725)

```
async def credential(self, workspace_id: UUID, slot: str) -> str
```

**Purpose**: Reads a declared credential slot during pre-binding surface authentication. It verifies the slot is declared and that the workspace exists.

**Data flow**: It receives workspace id and slot, checks the declared set and credential store, binds workspace context, verifies the workspace row, then returns the credential value.

**Call relations**: Slack authentication reads its signing secret through this method.

*Call graph*: called by 1 (_auth_signing_secret); 4 external calls (__init__, select, workspace_tx, ws).


##### `SurfaceDeliveryError.__init__`  (lines 3751–3755)

```
def __init__(self, message: str, *, retry_after_seconds: int | None=None) -> None
```

**Purpose**: Creates a delivery error that can carry a provider-requested retry delay. Negative retry delays are rejected.

**Data flow**: It receives a message and optional retry_after_seconds, validates the delay, stores it, and initializes the runtime error.

**Call relations**: Slack posting code raises it so WritebackPoller._fail_or_retry can schedule the next attempt appropriately.

*Call graph*: called by 1 (_chat_post).


##### `_writeback_due`  (lines 3801–3817)

```
def _writeback_due(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for writebacks that are ready to be claimed. A writeback is due when its turn is terminal and it is pending or its claim expired.

**Data flow**: It receives the current time and returns a SQL condition combining turn terminal status, writeback status, and claim expiry.

**Call relations**: writeback_workspaces.due and WritebackPoller._claim use it to find deliverable work.

*Call graph*: called by 2 (_claim, due); 2 external calls (and_, or_).


##### `writeback_workspaces`  (lines 3820–3855)

```
def writeback_workspaces() -> WorkspaceCandidates
```

**Purpose**: Creates a rotating workspace candidate reader for the writeback poller. It keeps the poller scanning bounded batches instead of the whole fleet at once.

**Data flow**: It initializes a cursor and returns an async candidates function that reads due workspace ids through owner_candidates and advances or wraps the cursor.

**Call relations**: A WritebackPoller receives this candidates function and calls it from run or drain.

*Call graph*: 1 external calls (owner_candidates).


##### `writeback_workspaces.due`  (lines 3827–3841)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the owner-scoped query for the next page of workspaces with due writebacks. It applies the rotating cursor when set.

**Data flow**: It reads the current time, selects workspace ids from due writebacks joined to turns, groups and orders them, limits the batch, and returns the SQL query.

**Call relations**: owner_candidates wraps it so writeback_workspaces.candidates can execute it safely.

*Call graph*: calls 1 internal fn (_writeback_due); 2 external calls (now, select).


##### `writeback_workspaces.candidates`  (lines 3845–3853)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: Returns the next batch of workspace ids with due writebacks, wrapping to the beginning when it reaches the end.

**Data flow**: It calls the wrapped due reader, resets the cursor if needed, updates the cursor to the last returned workspace id, and returns the ids.

**Call relations**: WritebackPoller.run and drain call this to know which workspaces to drain.


##### `_WritebackDeliveryFailed.__init__`  (lines 3863–3866)

```
def __init__(self, phase: Literal['post', 'attach'], error: Exception) -> None
```

**Purpose**: Wraps a delivery failure with the phase where it happened: posting the reply or attaching files. This lets retry logs and scheduling explain what failed.

**Data flow**: It receives the phase and original exception, stores both, and initializes the runtime error message from the original exception.

**Call relations**: WritebackPoller._deliver_claimed raises it when surface post or attach code fails.

*Call graph*: called by 1 (_deliver_claimed).


##### `WritebackPoller.run`  (lines 3889–3922)

```
async def run(self) -> None
```

**Purpose**: Runs the continuous background loop that delivers durable surface replies. It keeps several workspace drains in flight and cleans them up on shutdown.

**Data flow**: It repeatedly checks finished tasks, asks for candidate workspaces, starts drain tasks under concurrency limits, logs failures, sleeps between polls, and cancels outstanding tasks when leaving.

**Call relations**: The process background runner calls it for normal durable writeback delivery.

*Call graph*: calls 1 internal fn (_drain_workspace); 5 external calls (Semaphore, create_task, gather, sleep, log).


##### `WritebackPoller.drain`  (lines 3924–3933)

```
async def drain(self) -> None
```

**Purpose**: Runs one bounded writeback drain pass instead of an infinite loop. This is useful for one-shot maintenance or tests.

**Data flow**: It gets candidate workspace ids, drains them concurrently with a semaphore, gathers results, and raises an ExceptionGroup if any drain failed.

**Call relations**: It uses the same _drain_workspace path as the continuous run loop.

*Call graph*: calls 1 internal fn (_drain_workspace); 2 external calls (Semaphore, gather).


##### `WritebackPoller._drain_workspace`  (lines 3935–3953)

```
async def _drain_workspace(self, workspace_id: UUID, semaphore: asyncio.Semaphore) -> None
```

**Purpose**: Claims and delivers due writebacks for one workspace under the global workspace concurrency limit. It also starts claim-renewal tasks while delivery is in progress.

**Data flow**: It receives workspace id and semaphore, binds workspace context, claims rows, creates renewal tasks, delivers each row, logs retries, then cancels and awaits renewals.

**Call relations**: WritebackPoller.run and drain call it for each candidate workspace.

*Call graph*: calls 3 internal fn (_claim, _deliver, _renew_claim); called by 2 (drain, run); 4 external calls (create_task, gather, log, ws).


##### `WritebackPoller._claim`  (lines 3955–3988)

```
async def _claim(self, workspace_id: UUID) -> Sequence[sa.Row]
```

**Purpose**: Claims a batch of due writebacks for this worker. Claiming prevents another poller from delivering the same row at the same time.

**Data flow**: It receives workspace id, finds due rows, updates them to claimed with this worker id and an expiry time, and returns turn ids, reply refs, and last errors.

**Call relations**: _drain_workspace calls it before delivery; _writeback_due defines which rows are eligible.

*Call graph*: calls 1 internal fn (_writeback_due); called by 1 (_drain_workspace); 5 external calls (now, timedelta, select, update, workspace_tx).


##### `WritebackPoller._deliver`  (lines 3990–4022)

```
async def _deliver(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None, renewal: asyncio.Task[None]) -> None
```

**Purpose**: Delivers one claimed writeback and records success or retry/failure. It measures elapsed time and logs the outcome.

**Data flow**: It receives workspace id, turn id, existing reply reference, and renewal task. It calls delivery-with-lease, handles lost claims and delivery failures, updates retry state when needed, and logs.

**Call relations**: _drain_workspace calls it for each claimed row.

*Call graph*: calls 2 internal fn (_deliver_with_lease, _fail_or_retry); called by 1 (_drain_workspace); 2 external calls (now, log).


##### `WritebackPoller._deliver_with_lease`  (lines 4024–4053)

```
async def _deliver_with_lease(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None, renewal: asyncio.Task[None]) -> None
```

**Purpose**: Runs external delivery while a claim renewal task keeps the lease alive. It stops renewal before marking the row delivered to avoid racing its own lock.

**Data flow**: It receives workspace id, turn id, reply ref, and renewal task. It races delivery against renewal failure, cancels unfinished tasks, waits for cleanup, then marks delivered.

**Call relations**: _deliver calls it; it hands the actual post/attach work to _deliver_claimed.

*Call graph*: calls 2 internal fn (_deliver_claimed, _mark_delivered); called by 1 (_deliver); 3 external calls (create_task, gather, wait).


##### `WritebackPoller._deliver_claimed`  (lines 4055–4077)

```
async def _deliver_claimed(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None) -> None
```

**Purpose**: Builds the writeback payload and calls the target surface’s post and attach handlers. It records the reply reference after the first successful post.

**Data flow**: It receives workspace id, turn id, and optional reply ref. It builds the Writeback, finds the surface spec, skips if no durable delivery exists, creates a context, posts if needed, records the reply ref, then attaches files.

**Call relations**: _deliver_with_lease calls it; surface-specific post and attach functions do the outside network delivery.

*Call graph*: calls 3 internal fn (_build, _record_ref, __init__); called by 1 (_deliver_with_lease); 1 external calls (log).


##### `WritebackPoller._renew_claim`  (lines 4079–4082)

```
async def _renew_claim(self, turn_id: UUID) -> None
```

**Purpose**: Keeps extending a writeback claim while a potentially slow external delivery is running.

**Data flow**: It receives a turn id, sleeps for the refresh interval in a loop, and calls _refresh_claim each time.

**Call relations**: _drain_workspace starts it as a task for each claimed writeback.

*Call graph*: calls 1 internal fn (_refresh_claim); called by 1 (_drain_workspace); 1 external calls (sleep).


##### `WritebackPoller._refresh_claim`  (lines 4084–4100)

```
async def _refresh_claim(self, turn_id: UUID) -> None
```

**Purpose**: Extends this worker’s claim expiry for one writeback. If the row is no longer claimed by this worker, it reports a lost claim.

**Data flow**: It receives a turn id, updates the writeback row’s claim expiry where status and worker match, and raises _WritebackClaimLost if no row was updated.

**Call relations**: _renew_claim calls it repeatedly during delivery.

*Call graph*: called by 1 (_renew_claim); 5 external calls (__init__, now, timedelta, update, workspace_tx).


##### `WritebackPoller._build`  (lines 4102–4154)

```
async def _build(self, turn_id: UUID) -> tuple[Writeback, str]
```

**Purpose**: Builds the Writeback object for a terminal turn, including conversation routing data and shared artifacts. It also returns the surface name to dispatch to.

**Data flow**: It receives a turn id, reads the turn terminal frame, conversation queue key, surface, agent id, and artifact rows, validates the terminal frame, and returns the Writeback plus surface name.

**Call relations**: _deliver_claimed calls it before invoking a surface’s post or attach handlers.

*Call graph*: called by 1 (_deliver_claimed); 5 external calls (__init__, __init__, model_validate, select, workspace_tx).


##### `WritebackPoller._record_ref`  (lines 4156–4168)

```
async def _record_ref(self, turn_id: UUID, reply_ref: str) -> None
```

**Purpose**: Stores the external reply reference after a successful post. This lets recovery attach files without posting the reply again.

**Data flow**: It receives turn id and reply reference, updates the claimed writeback row for this worker, and raises _WritebackClaimLost if the claim no longer belongs to this worker.

**Call relations**: _deliver_claimed calls it between post and attach.

*Call graph*: called by 1 (_deliver_claimed); 3 external calls (__init__, update, workspace_tx).


##### `WritebackPoller._mark_delivered`  (lines 4170–4187)

```
async def _mark_delivered(self, turn_id: UUID) -> None
```

**Purpose**: Marks a claimed writeback as delivered and clears its claim. This is the durable close of the delivery process.

**Data flow**: It receives a turn id, updates the row to delivered where claimed by this worker, clears claimed_by and claim_expires_at, and raises _WritebackClaimLost if the update fails.

**Call relations**: _deliver_with_lease calls it after post and attach complete.

*Call graph*: called by 1 (_deliver_with_lease); 3 external calls (__init__, update, workspace_tx).


##### `WritebackPoller._fail_or_retry`  (lines 4189–4238)

```
async def _fail_or_retry(self, turn_id: UUID, error: _WritebackDeliveryFailed) -> tuple[str, str, datetime | None]
```

**Purpose**: Releases a failed writeback for retry or marks it permanently failed if it is too old. It honors provider retry-after delays but caps them.

**Data flow**: It receives turn id and wrapped delivery failure, computes last-error text and next retry time, updates the writeback row from claimed to pending or failed, and returns the outcome, error text, and next attempt time.

**Call relations**: _deliver calls it after _deliver_with_lease reports a post or attach failure.

*Call graph*: called by 1 (_deliver); 5 external calls (now, timedelta, case, update, workspace_tx).


### Signed public ingress
Public browser ingress uses signed routing claims to admit sandbox-hosted site traffic on the correct host before deeper checks run.

### `core/src/ufo/ingress_serve.py`

`entrypoint` · `startup and request handling`

A sandbox may run a small web server, such as a preview app or dev server. This file is the gatekeeper that exposes those sandbox ports to a browser without making them public to everyone. It reads the requested hostname to learn which conversation and port the browser is trying to reach, checks a signed session cookie, finds the live sandbox, and then relays HTTP or WebSocket traffic to it.

The flow is like a hotel front desk. A visitor first arrives with a temporary signed invitation link. The server trades that invitation for a short-lived room key cookie that only works for that exact site hostname. Later requests must present that cookie. If the hostname, token, workspace, conversation, or port do not match, the server refuses the request with a clear message.

When a request is allowed, the file carefully forwards it to the sandbox without buffering whole bodies in memory. It also protects the rest of the system: it strips private UFO cookies before the sandbox sees them, prevents sandbox pages from setting cookies in UFO’s reserved namespace, removes cache headers so private site content is never stored by a shared cache, and controls framing rules so only the intended app page can embed the site. WebSockets get the same authorization gate, plus an origin check so one hosted site cannot open a live socket into another.

#### Function details

##### `IngressServe.app`  (lines 220–252)

```
def app(self) -> FastAPI
```

**Purpose**: Builds the FastAPI web application that receives all incoming HTTP and WebSocket traffic for hosted sandbox sites. It defines which paths open signed site links, which paths are proxied to the sandbox, and which WebSocket paths are refused or relayed.

**Data flow**: It starts with the configured IngressServe object. It creates a FastAPI application, attaches routes for the special view-token path, a catch-all HTTP proxy route, and matching WebSocket routes, then returns the ready application for the server to run.

**Call relations**: At startup, run creates an IngressServe and hands the application from this method to uvicorn. The routes it installs later send normal site traffic to _proxy, WebSocket traffic to _socket, and token-opening traffic to _open or _no_view_token.

*Call graph*: 1 external calls (FastAPI).


##### `IngressServe._no_view_token`  (lines 254–260)

```
async def _no_view_token(self, request: Request) -> Response
```

**Purpose**: Answers requests to the special site-opening path when no token was provided. This prevents an empty or malformed link from falling through to the sandbox as an ordinary path.

**Data flow**: It receives an HTTP request. If the method is not GET or HEAD, it returns a 405 response saying only those methods are allowed; otherwise it returns a plain 403 message saying the link is not valid.

**Call relations**: IngressServe.app routes the bare view path here. It is a guard before any sandbox proxying can happen, so missing tokens never reach _proxy.

*Call graph*: 1 external calls (Response).


##### `IngressServe._open`  (lines 262–304)

```
async def _open(self, request: Request, view_path: str) -> Response
```

**Purpose**: Turns a signed view token from a site link into a short-lived session cookie for that exact site origin. This is what lets an embedded browser frame enter a hosted sandbox site safely.

**Data flow**: It receives the browser request and the path after the special view prefix. It splits out the token and optional entry path, checks that the request host names a valid site, verifies the token, confirms the token matches that host’s conversation and port, mints a session token, stores it in a cookie, and returns a redirect to the requested site path.

**Call relations**: IngressServe.app sends view-link requests here. It uses _site to understand the hostname, uses token verification and minting helpers to exchange one kind of token for another, and hands the browser back to the catch-all route by redirecting to a normal site path.

*Call graph*: calls 1 internal fn (_site); 8 external calls (replace, now, RedirectResponse, Response, mint_ingress_token, verify_ingress_token, set_session_cookie, quote).


##### `IngressServe._site`  (lines 306–317)

```
def _site(self, request: HTTPConnection) -> tuple[UUID, int] | None
```

**Purpose**: Figures out which sandbox site a request hostname is meant to address. It returns the conversation ID and port encoded in the subdomain, or nothing if the host is not one of this ingress’s signed site names.

**Data flow**: It reads the connection’s hostname and compares it with the configured base host. If the hostname has the right suffix, it parses the leading label into a conversation and port; if parsing fails or the suffix is wrong, it returns None.

**Call relations**: _open uses this to make sure a view token is being opened on the right site. _dial_site uses it as the first step of the shared HTTP and WebSocket authorization gate.

*Call graph*: called by 2 (_dial_site, _open); 1 external calls (parse_site_label).


##### `IngressServe._dial_site`  (lines 319–365)

```
async def _dial_site(self, connection: HTTPConnection) -> DialedSite | SiteRefusal
```

**Purpose**: Performs the main access check before any traffic reaches a sandbox. It confirms the request is for a valid site, verifies the session cookie, finds the sandbox for the conversation, and asks the carrier how to reach the requested port.

**Data flow**: It receives an HTTP or WebSocket connection. It extracts the site from the host, verifies the session cookie, checks that the cookie claims match the host, reads the stored sandbox handle from the database, converts that into a live sandbox handle, and dials the target port. It returns either a DialedSite with claims and connection target, or a SiteRefusal with a status code and message.

**Call relations**: Both _proxy and _socket call this before contacting a sandbox, so HTTP and WebSocket traffic share one security gate. It delegates hostname parsing to _site, database lookup to _stored_handle, and carrier-specific dialing to the configured carrier.

*Call graph*: calls 2 internal fn (_site, _stored_handle); called by 2 (_proxy, _socket); 8 external calls (__init__, __init__, __init__, now, warn, verify_ingress_token, sandbox_handle_id, ws).


##### `IngressServe._proxy`  (lines 367–419)

```
async def _proxy(self, request: Request, path: str) -> Response
```

**Purpose**: Relays an authorized HTTP request to the matching sandbox web server and streams the response back to the browser. It also rewrites sensitive headers so private hosted-site content stays private and safe to embed.

**Data flow**: It receives a browser request and path. It asks _dial_site whether the request may reach a sandbox; on refusal, it returns the refusal message. On success, it builds the upstream URL and headers, streams the request body if there is one, sends the request with the shared HTTP client, then streams the upstream response back while dropping cache, hop-by-hop, framing, and unsafe cookie headers.

**Call relations**: This is the catch-all HTTP route installed by app. It relies on _dial_site for authorization, _upstream_url and _upstream_headers to prepare the outbound request, _body to stream response bytes safely, _unframed_policy to adjust content security policy headers, and _confined_cookie to limit cookies set by the sandbox.

*Call graph*: calls 6 internal fn (_body, _confined_cookie, _dial_site, _unframed_policy, _upstream_headers, _upstream_url); 7 external calls (stream, Response, StreamingResponse, Request, BackgroundTask, log_error, ws).


##### `IngressServe._upstream_url`  (lines 421–424)

```
def _upstream_url(self, scheme: str, host: str, path: str, query_string: bytes) -> str
```

**Purpose**: Builds the exact URL used to contact the sandbox server. It preserves the browser’s path and query string while pointing them at the internal target host.

**Data flow**: It takes a scheme such as http or ws, a target host, a path, and raw query-string bytes. It safely quotes the path, decodes the query string, and returns a complete URL with the query appended when present.

**Call relations**: _proxy uses this for HTTP and HTTPS upstream requests. _socket uses the same helper for WebSocket and secure WebSocket upstream connections, keeping path handling consistent across both protocols.

*Call graph*: called by 2 (_proxy, _socket); 1 external calls (quote).


##### `IngressServe._stored_handle`  (lines 426–436)

```
async def _stored_handle(self, workspace_id: UUID, conversation_id: UUID) -> str | None
```

**Purpose**: Looks up the saved sandbox handle for a conversation in a workspace. This tells the ingress which sandbox instance should currently serve the requested site.

**Data flow**: It receives a workspace ID and conversation ID. Inside a workspace database transaction, it selects the conversation row matching both IDs and reads its sandbox_handle field. It returns that handle string, or None if no matching row exists.

**Call relations**: _dial_site calls this after the session cookie has been verified. If it cannot find a usable handle, _dial_site refuses the request because there is no live site to reach.

*Call graph*: called by 1 (_dial_site); 2 external calls (select, workspace_tx).


##### `IngressServe._upstream_headers`  (lines 438–472)

```
def _upstream_headers(self, request: HTTPConnection, dial_headers: Mapping[str, str]) -> list[tuple[str, str]]
```

**Purpose**: Creates the header list that the sandbox server is allowed to see. It forwards the browser’s useful headers but removes headers that belong to the proxy connection, WebSocket handshake internals, the original Host, UFO’s own session cookie, and anything the dial target must override.

**Data flow**: It reads all request headers and the extra headers supplied by the dial target. It filters out unsafe or inappropriate names, removes the ingress session cookie from Cookie headers while leaving site cookies, lowercases forwarded names, then appends the dial target’s required headers. The result is a clean list of header pairs for the upstream request.

**Call relations**: _proxy uses this before sending HTTP traffic to the sandbox. _socket uses it before opening the upstream WebSocket, so both paths hide UFO’s private cookie and apply carrier-provided headers the same way.

*Call graph*: called by 2 (_proxy, _socket).


##### `IngressServe._unframed_policy`  (lines 474–486)

```
def _unframed_policy(self, policy: str) -> str
```

**Purpose**: Removes only the frame-ancestors rule from a Content-Security-Policy header. That lets UFO decide where hosted sites may be embedded without throwing away the site’s other browser safety rules.

**Data flow**: It receives one policy header string. It splits it into semicolon-separated directives, drops any directive named frame-ancestors, joins the rest back together, and returns the rewritten policy or an empty string if nothing remains.

**Call relations**: _proxy calls this while copying response headers from the sandbox. The proxy then adds its own frame-ancestors rule separately, so the browser gets UFO’s embedding rule plus the site’s remaining protections.

*Call graph*: called by 1 (_proxy).


##### `IngressServe._confined_cookie`  (lines 488–515)

```
def _confined_cookie(self, header: str) -> str | None
```

**Purpose**: Rewrites one Set-Cookie response header so a sandbox site cannot set cookies outside its own hostname or inside UFO’s reserved cookie namespace. This prevents a hosted site from planting or replacing session-like cookies for UFO or sibling hosts.

**Data flow**: It receives a raw Set-Cookie header. It reads the cookie name, drops the whole header if the name is missing, malformed, or starts with the reserved ufo_ prefix, removes any Domain attribute, and returns the remaining cookie header. If the cookie is not safe to relay, it returns None.

**Call relations**: _proxy calls this for every Set-Cookie header from the sandbox. Safe cookies are appended to the browser response; unsafe ones are silently left out.

*Call graph*: called by 1 (_proxy).


##### `IngressServe._body`  (lines 517–527)

```
async def _body(self, upstream: httpx.Response) -> AsyncIterator[bytes]
```

**Purpose**: Streams raw response bytes from the sandbox to the browser and makes sure the upstream response is closed afterward. This avoids loading whole files or responses into memory.

**Data flow**: It receives an httpx response object from the upstream sandbox. It yields each raw chunk as it arrives. Whether the stream finishes normally or fails, it closes the upstream response so the connection is released.

**Call relations**: _proxy gives this generator to StreamingResponse. It works with the background close task as a safety net, ensuring upstream resources are cleaned up even when streaming ends unexpectedly.

*Call graph*: called by 1 (_proxy); 2 external calls (aclose, aiter_raw).


##### `IngressServe._no_socket_view`  (lines 529–535)

```
async def _no_socket_view(self, websocket: WebSocket) -> None
```

**Purpose**: Refuses WebSocket attempts to the special view-token path. That path is only for exchanging a token over normal HTTP, not for opening a live socket.

**Data flow**: It receives a WebSocket handshake. It creates a refusal saying the link is not valid and sends that refusal response without accepting the WebSocket.

**Call relations**: IngressServe.app routes WebSocket handshakes on the view path here. It uses _refuse to send the same style of denial response that _socket uses for unauthorized WebSockets.

*Call graph*: calls 1 internal fn (_refuse); 1 external calls (__init__).


##### `IngressServe._socket`  (lines 537–588)

```
async def _socket(self, websocket: WebSocket, path: str) -> None
```

**Purpose**: Relays an authorized WebSocket between the browser and the sandbox site. This supports hosted apps that need two-way live communication, such as live reload or push updates.

**Data flow**: It receives a WebSocket handshake and path. It first checks that the browser Origin matches the requested host, then runs the same site/session/dial gate used for HTTP. If allowed, it builds the upstream WebSocket URL, forwards safe headers and offered subprotocols, connects to the sandbox, accepts the viewer socket with the subprotocol chosen upstream, and relays messages until one side ends or fails.

**Call relations**: This is the catch-all WebSocket route installed by app. It calls _same_origin for a WebSocket-specific browser safety check, _dial_site for shared authorization, _upstream_url and _upstream_headers to connect upstream, _relay to move messages both ways, and _refuse or _end when the connection cannot continue.

*Call graph*: calls 7 internal fn (_dial_site, _end, _refuse, _relay, _same_origin, _upstream_headers, _upstream_url); 6 external calls (__init__, accept, log_error, ws, connect, Subprotocol).


##### `IngressServe._same_origin`  (lines 590–602)

```
def _same_origin(self, websocket: WebSocket) -> bool
```

**Purpose**: Checks that a WebSocket was opened by the same hosted site hostname it is trying to reach. This blocks one sandbox-hosted site from using the browser’s cookies to open a socket into another hosted site.

**Data flow**: It reads the Origin header from the WebSocket handshake and the hostname of the requested URL. It parses the Origin and returns true only when its hostname exactly matches the request hostname.

**Call relations**: _socket calls this before any session or sandbox dialing. If it fails, _socket refuses the handshake immediately with a foreign-origin message.

*Call graph*: called by 1 (_socket); 1 external calls (urlsplit).


##### `IngressServe._refuse`  (lines 604–611)

```
async def _refuse(self, websocket: WebSocket, refusal: SiteRefusal) -> None
```

**Purpose**: Rejects a WebSocket handshake with a normal HTTP-style error response. This gives the viewer the same clear status code and message they would get for an HTTP request.

**Data flow**: It receives a WebSocket object and a SiteRefusal. It builds a plain text response from the refusal’s message and status, then sends it as a denial response instead of accepting the socket.

**Call relations**: _no_socket_view uses this for token-path WebSocket attempts. _socket uses it whenever origin checks, authorization, dialing, or upstream connection setup fails before the viewer socket is accepted.

*Call graph*: called by 2 (_no_socket_view, _socket); 2 external calls (send_denial_response, Response).


##### `IngressServe._relay`  (lines 613–630)

```
async def _relay(self, viewer: WebSocket, upstream: ClientConnection) -> None
```

**Purpose**: Runs both halves of a WebSocket relay at the same time. It stops the whole relay as soon as either the browser side or sandbox side finishes.

**Data flow**: It receives the accepted viewer WebSocket and the connected upstream WebSocket. It starts one task to copy viewer messages to the site and another to copy site messages to the viewer, waits for the first to finish, cancels the other, and raises any real failure from the completed task.

**Call relations**: _socket calls this after both WebSocket connections are open. It coordinates _viewer_to_site and _site_to_viewer so neither side is left waiting forever after the other side has gone away.

*Call graph*: calls 2 internal fn (_site_to_viewer, _viewer_to_site); called by 1 (_socket); 3 external calls (create_task, gather, wait).


##### `IngressServe._viewer_to_site`  (lines 632–641)

```
async def _viewer_to_site(self, viewer: WebSocket, upstream: ClientConnection) -> None
```

**Purpose**: Copies WebSocket messages from the browser to the sandbox server. It preserves whether each message is text or binary, because WebSocket applications often treat those as different message types.

**Data flow**: It repeatedly receives messages from the viewer. If the viewer disconnects, it returns; otherwise it sends either the text value or the byte value to the upstream WebSocket.

**Call relations**: _relay starts this as one direction of the two-way pipe. When it finishes, _relay cancels the opposite direction and lets _socket handle any resulting cleanup.

*Call graph*: called by 1 (_relay); 2 external calls (receive, send).


##### `IngressServe._site_to_viewer`  (lines 643–656)

```
async def _site_to_viewer(self, upstream: ClientConnection, viewer: WebSocket) -> None
```

**Purpose**: Copies WebSocket messages from the sandbox server back to the browser and then closes the browser socket with an appropriate close code. This lets the site’s client understand why the connection ended when possible.

**Data flow**: It reads messages from the upstream WebSocket. Text messages are sent as text to the viewer, and byte messages are sent as bytes. When upstream ends, it chooses the upstream close code or a normal close code, replaces codes that are not legal to send, and asks _end to close the viewer socket.

**Call relations**: _relay starts this as the sandbox-to-browser direction. It calls _end for the final close, while _relay watches it alongside _viewer_to_site and stops the paired task when either direction finishes.

*Call graph*: calls 1 internal fn (_end); called by 1 (_relay); 3 external calls (suppress, send_bytes, send_text).


##### `IngressServe._end`  (lines 658–668)

```
async def _end(self, viewer: WebSocket, code: int, reason: str) -> None
```

**Purpose**: Attempts to close the viewer WebSocket without letting close-time errors create a second failure. This is useful because the browser may already have gone away.

**Data flow**: It receives a viewer WebSocket, close code, and reason. It tries to send a close frame with those values, but suppresses any exception because the connection is already ending either way.

**Call relations**: _site_to_viewer calls this when the upstream site closes. _socket also calls it after relay failures so the viewer gets a clear terminal state when possible.

*Call graph*: called by 2 (_site_to_viewer, _socket); 2 external calls (suppress, close).


##### `ingress_base_host`  (lines 671–682)

```
def ingress_base_host(configured: str | None) -> str
```

**Purpose**: Extracts the wildcard base hostname used for all hosted sandbox sites from configuration. It refuses to start if that hostname is missing, because the ingress would not know how to decode site hostnames.

**Data flow**: It receives the configured ingress public URL or None. It parses out the hostname and returns it; if no hostname is present, it raises a RuntimeError with an explanation of the required setting.

**Call relations**: run calls this during startup while constructing IngressServe. The returned base host is later used by _site to decide whether an incoming Host belongs to this ingress.

*Call graph*: called by 1 (run); 1 external calls (urlsplit).


##### `ingress_frame_ancestor`  (lines 685–693)

```
def ingress_frame_ancestor(configured: str | None) -> str
```

**Purpose**: Builds the browser framing rule that says which app origin is allowed to embed hosted sandbox sites. If the app public URL is not configured, it returns 'none' so no embedding is allowed by default.

**Data flow**: It receives the configured public app URL or None. It parses the scheme, hostname, and optional port; when they are present, it returns an origin string such as https://example.com, otherwise it returns the Content Security Policy value 'none'.

**Call relations**: run calls this at startup and stores the result on IngressServe. _proxy later uses that value in every proxied response’s Content-Security-Policy header.

*Call graph*: called by 1 (run); 1 external calls (urlsplit).


##### `upstream_client`  (lines 696–719)

```
def upstream_client() -> httpx.AsyncClient
```

**Purpose**: Creates the shared HTTP client used to contact sandbox web servers. It is deliberately cookie-blind so cookies from one sandbox site are never stored and replayed to another.

**Data flow**: It creates an httpx asynchronous client with fixed timeouts, connection limits, and a cookie jar policy that accepts no domains. The returned client can send upstream requests but will not keep cross-request cookies of its own.

**Call relations**: run calls this once during startup and passes the client into IngressServe. _proxy later uses that client for all HTTP traffic to sandbox origins.

*Call graph*: called by 1 (run); 4 external calls (CookieJar, DefaultCookiePolicy, AsyncClient, Limits).


##### `run`  (lines 722–743)

```
def run() -> None
```

**Purpose**: Starts the ingress service process. It loads configuration, initializes observability and database access, prepares the sandbox carrier and HTTP client, builds the ingress application, and runs the web server.

**Data flow**: It reads configuration and environment settings, initializes logging/tracing and the database, verifies the database is reachable, ensures the ingress signing secret is available, constructs an IngressServe with the base host, carrier, upstream client, and frame rule, then starts uvicorn listening on the configured ingress port.

**Call relations**: This is the top-level startup path for the file. It calls ingress_base_host, ingress_frame_ancestor, and upstream_client to prepare pieces that IngressServe needs, then hands IngressServe.app to uvicorn so the request-handling methods can serve traffic.

*Call graph*: calls 3 internal fn (ingress_base_host, ingress_frame_ancestor, upstream_client); 12 external calls (__init__, run, load_config, init_db, verify_db_reachable, load_manifests, init_o11y, log, owner_dsn, ingress_secret (+2 more)).


### `core/src/ufo/surface_token.py`

`domain_logic` · `request handling`

Some routes can be reached just by following a link. At that early point, the system may not have a login cookie or any other normal way to know which workspace the request belongs to. This file solves that by putting the needed routing claims into a signed token that can travel in a URL.

The token is like a sealed envelope. Anyone can carry it around, but only this deployment can make a valid seal because it uses a shared secret from the environment. The code uses HMAC-style signing, which means the payload is paired with a signature that changes if anyone edits the contents. If the signature does not match, the token is rejected.

A token also records the name of the surface that created it. When another surface tries to verify it, the surface name must match. This prevents a token made for one public route from being reused as an address for another route.

Importantly, this is not a login ticket and it never expires here. It only answers, “What claims did this surface safely put in the URL?” After that, the request must still pass the route’s normal access rules. The payload is deliberately limited to string keys and string values, because it is meant to cross a URL cleanly.

#### Function details

##### `mint_surface_token`  (lines 24–35)

```
def mint_surface_token(surface: str, payload: Mapping[str, str]) -> str
```

**Purpose**: This function makes a signed surface token from a surface name and a set of string claims. It is used when the system needs to create a URL-safe address that can later prove what surface made it and what routing information it carried.

**Data flow**: It receives a surface name and a mapping of claim names to claim values. It first refuses an empty surface name, and it also refuses any payload that tries to set the reserved "surface" claim itself. It then adds the real surface name, turns the combined data into compact JSON text, reads the signing secret, signs the bytes, and returns the finished token string.

**Call relations**: When a surface needs to hand out a durable link, this function packages the claims and calls _secret to get the deployment’s signing key. It then hands the JSON body to ufo.token_signing.sign_token, which produces the tamper-evident token that can later be checked by verify_surface_token.

*Call graph*: calls 1 internal fn (_secret); 2 external calls (dumps, sign_token).


##### `verify_surface_token`  (lines 38–51)

```
def verify_surface_token(surface: str, token: str) -> dict[str, str] | None
```

**Purpose**: This function checks whether a token is valid for a particular surface and, if so, returns the claims inside it. It is used when a request arrives through a link and the route needs safe routing information before normal request context exists.

**Data flow**: It receives the expected surface name and a token string. It reads the signing secret, asks the token-signing layer to verify the signature, and parses the verified bytes as JSON. If the token is forged, malformed, for a different surface, or contains non-string claims, it returns None. If everything checks out, it removes the reserved surface claim and returns the remaining claims as a dictionary of strings.

**Call relations**: During request handling, a route can call this function to turn an incoming URL token back into trusted claims. It relies on _secret for the same environment secret used at minting time, and on ufo.token_signing.verify_token to prove the token was not changed. Unlike mint_surface_token, this function is deliberately quiet on bad tokens: it returns None so the caller can treat the token as unusable.

*Call graph*: calls 1 internal fn (_secret); 2 external calls (loads, verify_token).


##### `_secret`  (lines 54–58)

```
def _secret() -> str
```

**Purpose**: This small helper fetches the shared token-signing secret from the process environment. It keeps both token creation and token checking using the same configured secret name.

**Data flow**: It reads the environment variable named by UFO_TOKEN_SECRET_ENV. If the value exists, it returns that string. If the value is missing or empty, it raises a RuntimeError, because tokens cannot be safely signed or verified without the shared secret.

**Call relations**: Both mint_surface_token and verify_surface_token call this helper right before they interact with the signing layer. That means the file has one central place that defines how the surface-token secret is found, matching the broader bearer-token setup used elsewhere in the project.

*Call graph*: called by 2 (mint_surface_token, verify_surface_token).


### Artifact delivery routes
The surfaces package exposes web routes that serve shared artifacts only through valid signed links, refreshing them for authorized workspace members when needed.

### `core/src/ufo/surfaces/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells the language that the surrounding folder should be treated as an importable package. Here, that means code elsewhere can refer to modules under `ufo.surfaces` using normal Python import paths.

Because the file has no code, it does not create objects, run setup steps, or expose shortcuts. Its job is more like a label on a drawer: it tells Python, “the files in this drawer belong together under this name.” Without it, depending on the Python version and packaging setup, imports involving `ufo.surfaces` might fail or behave differently. Keeping it empty also means importing `ufo.surfaces` has no hidden side effects, which makes the package predictable.


### `core/src/ufo/surfaces/artifacts.py`

`io_transport` · `request handling`

This file is the gatekeeper for artifact downloads: files that the system has shared with users. The important rule is simple: no valid signature, no file bytes. A signed URL is like a temporary claim ticket; it names the file and proves the server itself issued permission to download it.

When a browser or client asks for an artifact, the route checks the URL signature, expiry time, file name, and optional preview request. If the signature is bad, it refuses the request. If the signed file no longer exists in blob storage, it returns “not found.” If the request is for an image preview, it reads the stored bytes only after checking that the image really matches the promised type and size. Otherwise it streams the file as a download, so large files are sent in pieces instead of being loaded into memory all at once.

The most user-friendly part is expired-link recovery. An expired signed link still proves what file was once shared. If the requester has the portal session cookie and is a member of the workspace that owns that artifact, the file does not get served directly; instead the server sends them to a freshly signed URL. If they are not signed in, browsers are sent to the login page with the original link saved as the target. This keeps old teammate links useful while still blocking outsiders.

#### Function details

##### `download`  (lines 46–91)

```
async def download(request: Request, artifact_id: str, filename: str, exp: str='', sig: str='', preview: str='') -> Response
```

**Purpose**: This is the HTTP endpoint that serves a shared artifact. It verifies that the URL is allowed, then either streams the file as a download or returns a safe inline image preview.

**Data flow**: It receives the web request, the artifact id, the filename, and signed URL fields such as expiry time, signature, and preview information. It reads the blob store and signing secret from the application state, checks the signed URL, confirms the blob exists, and then either validates preview bytes or streams the stored file bytes. The output is a web response: a preview image, a streamed download, a redirect to a refreshed link, or an error such as forbidden, not found, or unsupported media.

**Call relations**: This is the public-facing route called by FastAPI when a matching artifact URL is requested. It relies on the artifact URL checker to decide whether the link is valid. If the link is expired but otherwise genuine, it hands the request to _refreshed_for_member to see whether a signed-in workspace member can receive a new link. For actual file delivery, it uses the blob stream, preview validator, media-type helper, and FastAPI response objects.

*Call graph*: calls 1 internal fn (_refreshed_for_member); 8 external calls (now, HTTPException, Response, StreamingResponse, artifact_media_type, verify_artifact_url, validated_image_preview, quote).


##### `_refreshed_for_member`  (lines 94–142)

```
async def _refreshed_for_member(request: Request, claims: ArtifactClaims, secret: str) -> RedirectResponse
```

**Purpose**: This helper tries to turn an expired artifact link into a fresh one for a legitimate teammate. It protects the file by checking both the user’s signed-in session and whether their workspace owns the shared artifact.

**Data flow**: It receives the request, the expired link’s decoded claims, and the signing secret. It reads the session cookie, verifies it, parses the workspace id, looks up the user as a workspace member, and checks that the requested blob belongs to that same workspace. If all checks pass, it creates a new expiry time, mints a fresh signed artifact URL, and returns a redirect to it; if any check fails, it raises the refusal produced by _refusal.

**Call relations**: download calls this only after the URL verifier says the link has expired. This helper then consults the session verifier, database transaction, workspace context, and URL minting code. When the requester is missing a valid session or membership, it delegates to _refusal to decide whether the user should be sent to login or simply denied.

*Call graph*: calls 1 internal fn (_refusal); called by 1 (download); 9 external calls (now, RedirectResponse, or_, select, mint_artifact_url, verified_claims, workspace_tx, ws, UUID).


##### `_refusal`  (lines 145–150)

```
def _refusal(request: Request) -> HTTPException
```

**Purpose**: This small helper builds the right rejection for someone who cannot refresh an expired artifact link. Browsers that accept HTML are pointed to the login page; other clients get a plain forbidden error.

**Data flow**: It receives the web request and reads the Accept header to guess whether the requester is a browser expecting an HTML page. For browser-like requests, it URL-encodes the current path and query string and puts that target on the login URL. It returns an HTTP exception: either a redirect-style exception to login or a 403 forbidden exception with the expired-link message.

**Call relations**: _refreshed_for_member calls this whenever the user is not signed in, has an invalid workspace id, is not a member, or does not belong to the artifact’s owning workspace. It uses URL quoting so the original artifact link can safely travel through the login redirect.

*Call graph*: called by 1 (_refreshed_for_member); 2 external calls (HTTPException, quote).

## 📊 State Registers Touched

- `reg-workspace-membership` — The roster of workspaces, members, admins, seats, and which people belong where.
- `reg-auth-tokens` — Signed passes that prove who a caller is or allow short-lived access to protected routes and links.
- `reg-agent-directory` — The saved assistants in each workspace and their settings, such as model behavior, sandbox size, and internet access.
- `reg-object-catalog` — The shared catalog of manageable workspace object types and the rules for who may view or change them.
- `reg-connection-grants` — Connected third-party accounts and permissions saying which agents may use which external accounts.
- `reg-surface-installations` — Mappings from outside entry points like Slack, web, terminal, and hosted surfaces into workspaces, members, and agents.
- `reg-inbound-message-log` — Incoming external messages saved until they are safely rendered, deduplicated, and admitted into a conversation.
- `reg-conversation-transcript` — The durable history of conversations, messages, speakers, titles, context, and results.
- `reg-live-hub` — The live stream state that lets browsers, terminals, and operators watch progress and reconnect without losing updates.
- `reg-artifact-store` — Generated files, previews, blobs, and signed shared-artifact links that outlive a single message.
- `reg-hosted-site-state` — Hosted sandbox sites, their public addresses, generations, ports, visibility, and unhosting status.
- `reg-conversation-slots` — Side-panel data shown beside a conversation, such as tasks, sources, sites, automations, and workspace changes.
- `reg-human-interaction-requests` — Pending user questions, approval prompts, and credential-request prompts created by tools and resumed through surfaces.
- `reg-outbound-delivery-queue` — Pending outbound surface writebacks and retry state for messages or notifications sent back to external channels such as Slack.
- `reg-execution-scope-context` — Per-request, per-job, and per-turn scoped context carrying the active workspace, member, agent, turn, permissions, credentials, billing, and service handles through core code.
- `reg-connector-auth-flow-state` — Short-lived OAuth, consent-link, CSRF/state, and callback progress for connecting external accounts before durable connections and grants exist.
- `reg-web-chat-metadata` — Extension-owned metadata for web-chat conversations or sessions, such as visitor/channel details and routing/display data beyond the core transcript.
- `reg-payment-provider-state` — Stripe or billing-provider setup state such as customer identifiers, checkout/payment-session progress, and subscription or purchase linkage for workspaces.

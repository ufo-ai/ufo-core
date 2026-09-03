# Shared file storage and signed download links  `stage-14.1`

This stage is shared behind-the-scenes support for files that the system stores or shares. It is about letting the right people download files, making previews safely, and repairing previews that failed earlier. artifact_url.py creates short-lived signed links, meaning web addresses with a built-in proof that says which file, workspace, and time limit they are valid for. artifacts.py is the download doorway: it serves files only when that proof is valid, and lets signed-in workspace members refresh an expired link.

The preview side works like a cautious inspection station. document_renderer.py sends uploaded documents to a preview service, then checks that the returned page images and text are safe before using them. sandbox/preview.py tells the sandbox where that preview service lives without exposing its secret access token. image_previews.py checks small preview images for honest type, size, dimensions, animation frames, and memory cost. previews.py defines the simple record that stores a preview’s storage key and size. preview_renderer.py retries recent shared files whose previews failed, asks the preview service for a PNG, and saves the result.

## Files in this stage

### Signed artifact access
Signed-link creation, validation, download routing, and refresh keep shared artifact files accessible only to authorized workspace members.

### `core/src/ufo/runtime/media/artifact_url.py`

`domain_logic` · `request handling`

This file is the gatekeeper for artifact download URLs. An artifact is a stored file, and a signed URL is like a temporary ticket: the path says which file is being requested, and the query string carries proof that the request is allowed. Without this file, the system could not safely hand out anonymous download links, because anyone might tamper with a URL to fetch a different file or keep using it forever.

The main job is split in two. `mint_artifact_url` builds a URL by taking an artifact storage key, an expiry time, a workspace ID, and a secret known only to the deployment. It signs the important parts so later changes can be detected. `verify_artifact_url` checks the same signed parts before anything is served. If the signature is wrong, the URL is rejected. If the link is expired, the code reports that separately so a signed-in workspace member may be allowed to refresh it.

The file also decides how artifact files should be labeled for browsers, for example whether a file is text or an Office document. For image previews, it can add an extra signed claim saying the image type and exact byte size, so preview routes can safely show the image inline instead of downloading it. Expiry times are rounded into buckets, which means repeated links for the same file stay identical for a while and browser or edge caches can reuse them.

#### Function details

##### `is_text_media`  (lines 70–77)

```
def is_text_media(media_type: str) -> bool
```

**Purpose**: Decides whether a media type represents readable text, such as plain text, JSON, YAML, TOML, XML, shell scripts, or TypeScript. Callers use this to know whether bytes can sensibly be shown inline as characters instead of treated as an opaque download.

**Data flow**: It receives a media type string, lowercases it, then checks whether it starts with `text/` or matches one of the known text-like `application/*` types. It returns `true` for text-like content and `false` otherwise, without changing anything else.

**Call relations**: This is a small shared decision point. It does not call other project functions; other parts of the media and display flow can consult it when deciding how to present an artifact to a person.


##### `artifact_url_expiry`  (lines 84–91)

```
def artifact_url_expiry(now: datetime) -> int
```

**Purpose**: Calculates the expiry timestamp used in signed artifact URLs. It rounds expiry up to a fixed time bucket so URLs minted close together are identical, which helps browser and edge caches reuse the same link.

**Data flow**: It receives the current time, converts it to a Unix timestamp, adds the desired lifetime, then rounds up to the next bucket boundary. It returns that bucketed expiry as an integer timestamp.

**Call relations**: When `mint_image_preview_url` needs a fresh preview link, it asks this function for the expiry time first. The resulting timestamp is then passed into `mint_artifact_url`, so the same expiry rule is used for generated preview links.

*Call graph*: called by 1 (mint_image_preview_url); 1 external calls (timestamp).


##### `ArtifactUrlExpired.__init__`  (lines 113–115)

```
def __init__(self, claims: ArtifactClaims) -> None
```

**Purpose**: Creates a special error for a URL that was authentic but is no longer fresh. It keeps the verified claims attached, so a caller with another proof of access can decide whether to refresh the link.

**Data flow**: It receives the already-checked artifact claims, stores them on the exception, and sets the error message to say the artifact URL is expired. The output is an exception object carrying both the message and the claims.

**Call relations**: `verify_artifact_url` uses this when a URL signature is valid but the expiry time has passed, or when an old-style URL has no workspace claim and must not be served directly. This lets higher-level request code treat expiry differently from forgery.

*Call graph*: called by 1 (verify_artifact_url).


##### `artifact_media_type`  (lines 118–131)

```
def artifact_media_type(filename: str) -> str
```

**Purpose**: Chooses the browser-facing media type for an artifact filename. This matters because browsers use media types to decide whether to show, download, or interpret a file.

**Data flow**: It receives a filename, first checks the project’s fixed list for important suffixes like `.yaml`, `.toml`, `.docx`, or `.xlsx`, then falls back to Python’s filename guessing. If the file appears compressed or unknown, it returns a safe generic download type.

**Call relations**: This function stands on its own as the artifact type classifier. It uses standard library filename parsing and MIME guessing, while keeping project-specific answers stable across machines.

*Call graph*: 2 external calls (guess_type, PurePosixPath).


##### `mint_artifact_url`  (lines 134–157)

```
def mint_artifact_url(secret: str, blob_key: str, expires_at: int, *, workspace_id: UUID, preview: ImagePreviewGrant | None=None) -> str
```

**Purpose**: Builds a signed relative download URL for one artifact in one workspace. A caller uses it when it wants to give someone a temporary link that cannot be edited to point at a different artifact.

**Data flow**: It receives the signing secret, storage key, expiry timestamp, workspace ID, and optional preview grant. It checks that the key really names an artifact, turns the preview claim into text if present, signs the workspace, artifact ID, expiry, and preview value, then returns a URL path with query parameters carrying the expiry, workspace, signature, and optional preview claim.

**Call relations**: `mint_image_preview_url` calls this after deciding an image is eligible for preview. Inside, this function relies on `_split_key` to validate the artifact address, `_parsed_preview` to make sure any preview claim is well formed, `_signed_message` to build the exact bytes being signed, and the shared signing helper to make the detached signature.

*Call graph*: calls 3 internal fn (_parsed_preview, _signed_message, _split_key); called by 1 (mint_image_preview_url); 3 external calls (__init__, sign_detached, quote).


##### `mint_image_preview_url`  (lines 160–188)

```
def mint_image_preview_url(secret: str, public_base_url: str | None, blob_key: str, size_bytes: int | None, *, workspace_id: UUID) -> str | None
```

**Purpose**: Creates an absolute signed URL for showing a stored raster image preview inline, or returns nothing if the file is not safe or eligible for preview. It is stricter than a normal download link because inline image rendering needs an exact signed claim about type and size.

**Data flow**: It receives the secret, public base URL, blob key, file size, and workspace ID. It rejects missing configuration, non-raster images, unknown sizes, or images larger than the preview limit. For an eligible image, it calculates a bucketed expiry, creates an image preview grant, mints a signed artifact URL, attaches it to the public base URL, and returns the full link.

**Call relations**: This is the higher-level preview builder. It asks `raster_image_media_type` what kind of image the blob key names, uses `artifact_url_expiry` for the time limit, then delegates the actual signed URL construction to `mint_artifact_url`.

*Call graph*: calls 2 internal fn (artifact_url_expiry, mint_artifact_url); 3 external calls (__init__, now, raster_image_media_type).


##### `verify_artifact_url`  (lines 191–232)

```
def verify_artifact_url(secret: str, artifact_id: str, filename: str, expires_at: str, signature: str, preview: str, workspace: str, now: datetime) -> ArtifactClaims
```

**Purpose**: Checks whether an incoming artifact URL proves access to the requested file. It rejects malformed, tampered, wrongly scoped, or expired links before any bytes are served.

**Data flow**: It receives the secret, artifact ID, filename, expiry text, signature, preview text, workspace text, and current time. It validates the shape of each value, parses the optional preview claim, rebuilds the exact signed message, verifies the signature, then assembles `ArtifactClaims` describing the workspace, blob key, filename, expiry, and preview permission. If there is no workspace claim or the expiry has passed, it raises an expired-url error carrying those claims; otherwise it returns the claims.

**Call relations**: This is the matching checker for URLs created by `mint_artifact_url`. It uses `_is_canonical_uuid`, `_is_filename`, and `_parsed_preview` to reject bad input, `_signed_message` to rebuild what should have been signed, and `ArtifactUrlExpired.__init__` when a valid link cannot be served directly because of age or old unscoped format.

*Call graph*: calls 5 internal fn (__init__, _is_canonical_uuid, _is_filename, _parsed_preview, _signed_message); 5 external calls (__init__, __init__, timestamp, verify_detached, UUID).


##### `_signed_message`  (lines 235–237)

```
def _signed_message(workspace: str, artifact_id: str, expires_at: str, preview_value: str) -> bytes
```

**Purpose**: Builds the exact byte string that is signed when creating a URL and verified when checking it. Keeping this in one helper prevents the minting and verifying sides from accidentally signing different text.

**Data flow**: It receives workspace text, artifact ID, expiry text, and preview text. If a workspace is present, it prefixes the message with it, then joins the important fields with colons and encodes the result as bytes. The returned bytes are the input to signing or signature verification.

**Call relations**: `mint_artifact_url` uses this before creating the signature, and `verify_artifact_url` uses it before checking the signature. Because both sides call the same helper, a normal round trip agrees by construction.

*Call graph*: called by 2 (mint_artifact_url, verify_artifact_url).


##### `_parsed_preview`  (lines 240–251)

```
def _parsed_preview(value: str) -> ImagePreviewGrant | None
```

**Purpose**: Turns a preview claim from URL text into a structured image preview grant, but only if it is safe and allowed. It prevents someone from claiming an unsupported type or an oversized image preview.

**Data flow**: It receives a string shaped like `media/type:size`. It splits at the last colon, checks that the media type is one of the allowed raster image types, checks that the size is numeric and within the maximum preview size, then returns an `ImagePreviewGrant`. If any check fails, it returns `None`.

**Call relations**: `mint_artifact_url` calls this to double-check a preview claim before signing it, and `verify_artifact_url` calls it to interpret a preview claim from an incoming URL. It hands back the structured grant that later code can validate against the actual bytes.

*Call graph*: called by 2 (mint_artifact_url, verify_artifact_url); 3 external calls (__init__, cast, values).


##### `_split_key`  (lines 254–263)

```
def _split_key(blob_key: str) -> tuple[str, str]
```

**Purpose**: Checks that a storage key really points to one artifact file and extracts its artifact ID and filename. This stops callers from minting signed URLs for paths outside the artifact area.

**Data flow**: It receives a blob key string, removes the expected `artifacts/` prefix, and splits the rest into an artifact ID and filename. It confirms the ID is a canonical UUID and the filename is a single safe path segment. On success it returns the two pieces; on failure it raises an artifact URL error.

**Call relations**: `mint_artifact_url` calls this before signing anything. `_split_key` in turn uses `_is_canonical_uuid` and `_is_filename`, so only well-formed artifact addresses can become signed URLs.

*Call graph*: calls 2 internal fn (_is_canonical_uuid, _is_filename); called by 1 (mint_artifact_url); 1 external calls (__init__).


##### `_is_canonical_uuid`  (lines 266–270)

```
def _is_canonical_uuid(value: str) -> bool
```

**Purpose**: Checks whether a string is exactly a standard UUID spelling. This avoids accepting alternate or malformed IDs in signed artifact paths and workspace claims.

**Data flow**: It receives a string, tries to parse it as a UUID, then compares the standard string form back to the original. It returns `true` only for the canonical spelling, and `false` if parsing fails or the spelling differs.

**Call relations**: `_split_key` uses this for artifact IDs before minting URLs, and `verify_artifact_url` uses it for artifact and workspace IDs before trusting incoming URL parts.

*Call graph*: called by 2 (_split_key, verify_artifact_url); 1 external calls (UUID).


##### `_is_filename`  (lines 273–274)

```
def _is_filename(value: str) -> bool
```

**Purpose**: Checks that a filename is a single ordinary path segment, not empty and not a directory traversal marker. This helps ensure a URL can only refer to the one file under its artifact ID.

**Data flow**: It receives a filename string and checks that it is not empty, contains no slash, and is not `.` or `..`. It returns a simple yes-or-no result and changes nothing.

**Call relations**: `_split_key` uses this before allowing a blob key to be signed, and `verify_artifact_url` uses it before turning incoming URL pieces into a blob key. Together, those checks keep artifact URLs inside the intended namespace.

*Call graph*: called by 2 (_split_key, verify_artifact_url).


### `core/src/ufo/runtime/surfaces/artifacts.py`

`io_transport` · `request handling`

This file is the gatekeeper for shared files. Think of a signed artifact URL like a temporary ticket: whoever holds a valid ticket can get the file, but once the ticket expires, it no longer opens the door. The main route checks that ticket, finds the file in the workspace’s blob store, and streams the bytes back without loading the whole file into memory. That matters for large files and many simultaneous downloads.

The route also supports safe image previews. If the signed URL asks for a preview, the file is checked to make sure it really is the expected kind and size of image before it is shown inline in the browser.

A notable feature is expired-link recovery. If a teammate opens an old link, the file is not served directly. Instead, the code checks the user’s session cookie, confirms they are a member of the workspace that owns the shared file, and redirects them to a newly signed URL. If the browser is not signed in, it is sent to the login page with the old link preserved as the target. Failed downloads and redirects are marked as not cacheable, while successful signed downloads can be briefly cached because the exact URL already contains the permission grant.

#### Function details

##### `download`  (lines 59–117)

```
async def download(request: Request, artifact_id: str, filename: str, exp: str='', sig: str='', preview: str='', workspace: Annotated[str, Query(alias='ws')]='') -> Response
```

**Purpose**: This is the HTTP download endpoint for artifact files. It checks that the URL’s signature is valid, confirms the file exists in the right workspace, then returns either the full file as a streamed attachment or a verified image preview.

**Data flow**: It receives a web request, the artifact identifier, filename, and signature-related query values. It reads the blob store and signing secret from the application state, verifies the URL, and uses the verified claims to locate the file. If the URL is valid, it returns bytes to the caller with safe headers; if the link is expired, missing permission, or points to a bad preview, it returns a redirect or an error instead.

**Call relations**: This is the route FastAPI calls when someone opens an artifact link. It relies on the artifact URL verifier to decide whether the link is trusted. If the verifier says the link expired, it asks _refreshed_for_member to see whether a signed-in workspace member may receive a fresh link. For image previews it hands the blob stream to the preview validator before sending a response.

*Call graph*: calls 1 internal fn (_refreshed_for_member); 9 external calls (now, HTTPException, Response, StreamingResponse, artifact_media_type, verify_artifact_url, validated_image_preview, ws, quote).


##### `_refreshed_for_member`  (lines 120–171)

```
async def _refreshed_for_member(request: Request, claims: ArtifactClaims, secret: str) -> RedirectResponse
```

**Purpose**: This helper tries to turn an expired artifact link into a fresh one for a legitimate workspace member. It lets old links in chats or threads keep working for teammates, while still blocking people who are not signed in or do not belong to the workspace.

**Data flow**: It receives the current request, the expired link’s already-verified claims, and the signing secret. It reads the session cookie, verifies the signed-in user, checks the database to confirm both membership and artifact ownership, then creates a new expiry time and redirects to a newly signed artifact URL. If any check fails, it produces a refusal instead of a file or fresh link.

**Call relations**: download calls this only after a signed URL is recognized as authentic but expired. This helper then consults authentication, workspace context, and database records before calling the URL minting logic. When it cannot safely refresh the link, it delegates the exact rejection behavior to _refusal.

*Call graph*: calls 1 internal fn (_refusal); called by 1 (download); 10 external calls (now, RedirectResponse, or_, select, workspace_tx, verified_claims, artifact_url_expiry, mint_artifact_url, ws, UUID).


##### `_refusal`  (lines 174–179)

```
def _refusal(request: Request) -> HTTPException
```

**Purpose**: This helper creates the right response when an expired artifact link cannot be refreshed. Browser users who accept HTML are sent toward sign-in, while other clients receive a plain forbidden error.

**Data flow**: It receives the incoming request and looks at the request’s Accept header to guess whether the caller is a browser expecting an HTML page. For browsers, it builds a login redirect that includes the original artifact link as the return target. For non-browser callers, it returns a forbidden error with the expired-link message.

**Call relations**: _refreshed_for_member calls this whenever the user has no valid session, the session does not name a usable workspace, or the database checks do not prove membership and ownership. It keeps the refusal behavior in one place so expired-link recovery has a consistent fallback.

*Call graph*: called by 1 (_refreshed_for_member); 2 external calls (HTTPException, quote).


### Preview generation safety
The preview service configuration, document rendering handoff, and image validation rules ensure generated previews are safe to store and display.

### `core/src/ufo/harness/document_renderer.py`

`io_transport` · `request handling`

Some documents are hard for an automated system to read directly, especially PDFs or office files with layout, images, and page breaks. This file solves that by asking an external rendering service, called ufo-preview, to turn a document into page-sized PNG images plus text. Without this layer, the rest of the system would either need to understand many document formats itself or risk accepting huge, malformed, or misleading files.

The main class, DocumentRenderer, acts like a cautious courier. First it refuses input that is too large. Then it asks for only a bounded page range, so a 500-page file cannot flood the system. It posts the document to the render service over HTTP, using a bearer token for authorization.

The service returns a zip bundle. The private unpacking step opens that bundle and verifies it piece by piece: there must be a manifest file, the manifest must match the request, page numbers must be sensible and consecutive, every listed image must exist, and each image must really be a PNG. It also enforces size limits on the manifest, each image, and the total image data. Finally it base64-encodes the PNG images, which means converting binary image bytes into text that can be carried safely in JSON-like data. The result includes page metadata, extracted text, the next page to request, and a reminder to inspect output quality.

#### Function details

##### `DocumentRenderer.render`  (lines 60–107)

```
async def render(self, path: str, kind: str, content: bytes, start_page: int, limit: int) -> dict[str, object]
```

**Purpose**: This is the public async method used to render a document into page images and text. It protects the system by limiting input size and requested page count before sending the document to the preview service.

**Data flow**: It receives a file path, document type, raw document bytes, a requested start page, and a page limit. It clamps the page range to safe values, builds a small render request, and sends both the request and file bytes to the configured service URL over HTTP. If the service reports an error or returns too much data, it raises an error; otherwise it passes the returned zip bundle to the unpacking step and returns the final dictionary of text, images, and paging information.

**Call relations**: This function is the front door for document rendering. During a request, other code would call it when it needs a document preview. It uses httpx.AsyncClient and httpx.Timeout to talk to the external rendering service without blocking the event loop, json.dumps to encode the render instructions, and asyncio.to_thread to move the heavier zip unpacking work off the async path before returning the checked result.

*Call graph*: 4 external calls (to_thread, AsyncClient, Timeout, dumps).


##### `DocumentRenderer._unpack`  (lines 109–196)

```
def _unpack(self, path: str, kind: str, start_page: int, limit: int, bundle: bytes) -> dict[str, object]
```

**Purpose**: This helper opens and validates the zip bundle returned by the render service. It makes sure the service response is exactly the safe, expected shape before turning page images into data the rest of the system can use.

**Data flow**: It receives the original path and document type, the page range that was requested, and the raw zip bundle bytes. It opens the bundle, reads and validates manifest.json, checks that the page list matches the request, confirms there are no unexpected files, enforces size caps, verifies that each page file is a PNG, and base64-encodes each image. It returns a dictionary containing the document path, type, combined text, total page count, returned pages, next page number if more pages remain, image data, and a quality reminder.

**Call relations**: This function is called after DocumentRenderer.render successfully downloads a bundle from the preview service. It relies on io.BytesIO and zipfile.ZipFile to treat the returned bytes like a zip file in memory, and on base64.b64encode to convert each PNG into text-safe image data. Its job is to be the gatekeeper between an outside service response and the internal result handed back to callers.

*Call graph*: 3 external calls (b64encode, BytesIO, ZipFile).


### `core/src/ufo/harness/sandbox/preview.py`

`config` · `config load and sandbox proxy setup`

This file is a small but important agreement point between the sandbox, the proxy, and the preview service. The preview service is the internal service that renders shared files and document reads. Instead of letting sandboxed code reach arbitrary internet addresses for this, the system uses a special internal host name, `preview.ufo.internal`, which the proxy recognizes and routes to the real preview service.

The file also defines the authorization header name and a harmless placeholder token called a sentinel. A sentinel is like a claim ticket: sandboxed code can carry it, but it is not the real secret. When a request goes to the approved preview host, the proxy swaps that placeholder for the real deployment token. This keeps the real token out of the sandbox while still allowing previews to work.

Finally, the file includes a parser for the configured preview service address. If the deployment says there is no preview service, it returns nothing. If an address is provided, it must look like `host:port`. A bad value raises an error immediately, because that means the deployment was configured incorrectly and should be fixed rather than silently ignored.

#### Function details

##### `parse_preview_service`  (lines 16–25)

```
def parse_preview_service(value: str | None) -> tuple[str, int] | None
```

**Purpose**: This function turns the configured preview service address into a host and port the rest of the system can use. It is strict on purpose: if the deployment provides a malformed address, it reports the problem instead of pretending preview is disabled.

**Data flow**: It receives either a text value like `example.internal:8443` or no value at all. If there is no value, it returns `None`, meaning this deployment does not run a preview service. If there is text, it splits it at the last colon, checks that a host exists, converts the port part into a number, and returns them as `(host, port)`. If the text is missing the colon or host, or if the port is not a valid number, the function raises an error.

**Call relations**: Other setup code calls this when reading deployment configuration for the preview service. Its result tells the proxy or sandbox wiring whether there is a preview service to relay to, and exactly which host and port should receive those internal preview requests.


### `core/src/ufo/runtime/media/image_previews.py`

`domain_logic` · `request handling`

Image previews are convenient, but they are also untrusted input: someone can send bytes that claim to be a tiny PNG while actually being broken, oversized, mislabeled, or expensive to decode. This file is the gatekeeper for those preview bytes. It first provides a quick helper, raster_image_media_type, that guesses the expected image type from a path suffix such as .jpg or .png. The main path is validated_image_preview. It receives an asynchronous stream of byte chunks plus an ImagePreviewGrant, which is a signed-style claim saying what media type and exact byte size the preview is supposed to have. It reads the stream, refuses anything over the limit, and refuses any mismatch between claimed and actual size. Then it asks _ImagePreviewValidator to inspect the complete image in a background thread, so image decoding work does not block the main async event loop. The validator uses Pillow, the Python image library, to verify the file structure and decode each frame. It also adds its own simple end-of-file checks for JPEG, GIF, PNG, and WebP, like checking that a package has the correct seal before opening it. The important behavior is defensive: corrupted images, decompression bombs, too many frames, huge dimensions, mismatched formats, and incomplete containers all become InvalidImagePreview errors.

#### Function details

##### `raster_image_media_type`  (lines 43–44)

```
def raster_image_media_type(path: str) -> RasterImageMediaType | None
```

**Purpose**: This function guesses the image media type from a file path, using only the file extension. It is useful as an early, lightweight way to decide whether a path looks like a supported raster image such as PNG, JPEG, GIF, or WebP.

**Data flow**: It receives a path string. It extracts the final suffix from that path, lowercases it, and looks it up in the table of supported image suffixes. It returns the matching media type string, or returns nothing if the suffix is not recognized.

**Call relations**: This is a small lookup helper. It relies on pathlib.PurePosixPath to read the suffix in a path-like way, then hands back the expected media type for other code to use before deeper validation happens.

*Call graph*: 1 external calls (PurePosixPath).


##### `validated_image_preview`  (lines 47–61)

```
async def validated_image_preview(stream: AsyncIterator[bytes], grant: ImagePreviewGrant) -> bytes
```

**Purpose**: This asynchronous function reads an incoming image preview and proves that it matches its claimed size and type before returning the bytes. Someone would use it at the point where preview data arrives from an untrusted source.

**Data flow**: It receives a stream of byte chunks and an ImagePreviewGrant containing the claimed media type and exact byte count. It totals the chunks as they arrive, rejects the stream if it grows beyond the claim or the maximum allowed size, then joins the chunks into one byte string. After that it sends the bytes and claimed media type to the image validator in a background thread. If every check passes, it returns the original bytes; if not, it raises InvalidImagePreview.

**Call relations**: This function is the public validation path for streamed preview data. It raises InvalidImagePreview when the size claim is impossible or false, and it uses asyncio.to_thread to run _ImagePreviewValidator.validate without blocking the async caller while Pillow inspects the image.

*Call graph*: 2 external calls (__init__, to_thread).


##### `_ImagePreviewValidator.validate`  (lines 66–109)

```
def validate(data: bytes, media_type: RasterImageMediaType) -> None
```

**Purpose**: This function performs the deep safety check on a completed image byte string. It confirms that the bytes are a real supported image, that the real format matches the claimed media type, and that decoding it will stay within resource limits.

**Data flow**: It receives raw image bytes and the media type the sender claimed. First it checks basic container endings through _validate_container. Then it opens the bytes with Pillow, verifies the image structure, and records the actual format Pillow sees. It opens the image again to step through frames, checking frame count, width, height, and total decoded pixels while forcing each frame to load. If Pillow or Python reports corruption or dangerous decompression behavior, the function converts that into InvalidImagePreview. If the actual media type differs from the claim, it also rejects the preview. On success, it returns nothing and simply means the bytes passed.

**Call relations**: validated_image_preview hands work to this function after it has finished reading the stream and checked the byte count. Inside, this function uses warnings.catch_warnings and warnings.simplefilter so Pillow decompression-bomb warnings become hard failures, uses BytesIO so Pillow can read from in-memory bytes like a file, calls Image.open to inspect the image, and delegates quick format-specific completeness checks to _ImagePreviewValidator._validate_container.

*Call graph*: 5 external calls (__init__, open, BytesIO, catch_warnings, simplefilter).


##### `_ImagePreviewValidator._validate_container`  (lines 112–125)

```
def _validate_container(data: bytes, media_type: RasterImageMediaType) -> None
```

**Purpose**: This function does quick, format-specific checks that an image file looks complete before the heavier image decoder opens it. It catches simple truncation cases, such as a JPEG missing its required ending marker.

**Data flow**: It receives raw bytes and the claimed media type. For JPEG, GIF, and PNG it checks for the expected ending bytes. For WebP it checks the RIFF and WEBP header markers and confirms that the size written inside the file matches the actual byte length. If the container does not fit the claimed type, it raises InvalidImagePreview; otherwise it returns without changing anything.

**Call relations**: _ImagePreviewValidator.validate calls this first as a fast front-door check. If this check passes, validate continues to the deeper Pillow-based verification; if it fails, validation stops immediately with InvalidImagePreview.

*Call graph*: 1 external calls (__init__).


### Preview records and retries
Preview metadata and retry orchestration let the system record successful preview images and recover from failed initial rendering attempts.

### `core/src/ufo/runtime/media/preview_renderer.py`

`orchestration` · `scheduled background retry job`

When someone shares a document, the system tries to create a preview image right away. That first try is best-effort: if the preview service is temporarily down, the file is still shared, but the database row has empty preview fields. This file provides the background retry job that fills in those missing previews later.

The job works in small batches. It only looks at files shared recently, within a one-hour retry window. This matters because some files may be permanently impossible to preview, such as corrupt documents. Without a time limit, the system would keep retrying those bad files forever.

For each eligible file, the renderer creates two temporary signed web links: one link lets the preview service download the original file, and the other lets it upload the generated PNG preview. These are presigned URLs, meaning short-lived links that grant limited access without sending the file bytes through this core service. Core acts like a dispatcher: it gives the preview worker pickup and drop-off instructions, then records the preview size and storage key when the worker succeeds.

The file also includes a workspace finder. Because data is separated by workspace, the scheduler can first ask which workspaces have missing previews, then run the renderer inside each workspace’s own database scope.

#### Function details

##### `_eligible`  (lines 42–43)

```
def _eligible(filename_column: sa.Column) -> sa.ColumnElement[bool]
```

**Purpose**: This helper builds the database test for whether a filename is the kind of document the preview service knows how to render. It checks for supported extensions such as PDF, DOCX, CSV, SVG, and similar document formats.

**Data flow**: It takes a database column that contains filenames. It turns the list of supported suffixes into a single database condition meaning “the filename ends with one of these suffixes,” ignoring letter case. The result is not a true or false value yet; it is a condition used inside a database query.

**Call relations**: The batch runner and the workspace finder both call this helper while building their database searches. It keeps the meaning of “previewable file” consistent in both places, so the system does not pick a workspace for files that the actual renderer would later ignore.

*Call graph*: called by 2 (candidate_workspaces, run); 2 external calls (ilike, or_).


##### `PreviewRenderer.run`  (lines 57–79)

```
async def run(self) -> None
```

**Purpose**: This is the main body of the retry job for one workspace. It finds a small batch of recent shared files that still have no preview, then asks the preview service to render each one.

**Data flow**: It starts by calculating the oldest share time that is still worth retrying. It reads the workspace database for shared artifacts whose preview fields are empty, whose filenames are eligible, and whose creation time is still inside the retry window. If it finds no rows, it stops. If it finds rows, it opens an HTTP client and passes each file’s storage key and filename to the single-file rendering step.

**Call relations**: A scheduler or higher-level job constructs this renderer for a particular workspace and calls this method. This method uses `_eligible` to choose candidate rows, then delegates the actual preview request and database update to `PreviewRenderer._render_one` for each file.

*Call graph*: calls 2 internal fn (_render_one, _eligible); 4 external calls (now, AsyncClient, select, workspace_tx).


##### `PreviewRenderer._render_one`  (lines 81–117)

```
async def _render_one(self, client: httpx.AsyncClient, blob_key: str, filename: str) -> None
```

**Purpose**: This function tries to render one missing preview. It gives the preview service temporary download and upload links, then records the new PNG preview in the database if the service succeeds.

**Data flow**: It receives an HTTP client, the original file’s blob storage key, and the filename. From the filename it chooses the document kind and builds a new preview storage key. It asks the blob store for a temporary read link to the source file and a temporary write link for the preview image. It sends those links, plus size limits and page count, to the preview service. If the service cannot be reached or refuses the request, it logs the problem and leaves the database unchanged so a later job tick can try again. If the service returns success, it reads the reported preview size and updates the shared artifact row with the preview key, PNG media type, and byte size.

**Call relations**: `PreviewRenderer.run` calls this once for each selected shared file. This function hands work outward to the preview service over HTTP and to the blob store through presigned URLs, then comes back to the workspace database to save the successful result. Its failure behavior is deliberately quiet: it logs and returns, relying on the next scheduled run rather than retrying in a tight loop.

*Call graph*: called by 1 (run); 7 external calls (post, dumps, PurePosixPath, update, workspace_tx, log, uuid4).


##### `PreviewRenderer.candidate_workspaces`  (lines 119–133)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: This function finds which workspaces currently have recent shared files that are missing previews. It lets the scheduler avoid running the preview job for workspaces that have nothing to do.

**Data flow**: It calculates the same retry cutoff time used by the renderer. It reads from the owner-level database view, looking for distinct workspace IDs attached to shared artifacts with no preview, a recent creation time, and an eligible filename. It returns those workspace IDs as a tuple.

**Call relations**: A higher-level scheduler can call this before running per-workspace rendering. It uses `_eligible` so its idea of “workspaces needing preview work” matches `PreviewRenderer.run`, which later performs the actual batch processing inside each workspace’s own database context.

*Call graph*: calls 1 internal fn (_eligible); 3 external calls (now, select, owner_tx).


### `core/src/ufo/runtime/media/previews.py`

`data_model` · `media preview storage and lookup`

This file is a tiny data model for media previews. A preview is a stored picture, and the system needs a simple way to remember two facts about it: where it is stored and how large it is. The `StoredPreview` class is that note card. Its `blob_key` is a workspace-relative storage name, like a label on a box in a storage room. Its `size_bytes` records the exact number of bytes in the saved picture, which can matter for validation, display, limits, or transfer decisions.

The class is marked as a `dataclass`, which means Python automatically gives it basic behavior such as construction and readable representation. It is also `frozen`, meaning that once a `StoredPreview` is created, its fields cannot be changed. That is useful because this object is meant to be a trustworthy record of something already stored, not a draft that should be edited later.

Without this file, other code would likely pass around loose pairs of values, such as a string and a number, which is easier to mix up or misuse. This class gives those values a clear name and meaning.

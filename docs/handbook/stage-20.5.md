# Operator access and uploaded media safety  `stage-20.5`

This stage is shared behind-the-scenes protection for two places where trust matters: people entering internal tools, and image files entering the system. It is not the main work loop. It acts more like a guarded doorway before sensitive pages or risky media are allowed through.

The operator access file provides common sign-in and session rules for operator-only web tools, such as debugging or memory inspection pages. It avoids putting long-lived access tokens in web addresses, since URLs can be copied or logged. It checks that the requester’s email belongs to the approved operator domain, then decides which workspace that person may view. This keeps powerful tools limited to the right people and the right scope.

The image preview file checks uploaded or received previews before they are shown or processed. It verifies that the file is the type and size it claims to be, and rejects malformed, mislabeled, oversized, or dangerous images. Together, these parts reduce risk at system boundaries.

## Files in this stage

### Operator Session Access
Shared operator-only session logic gates administrative tools by token handling, email-domain membership, and workspace authorization.

### `core/src/ufo/ext/operator.py`

`domain_logic` · `request handling`

Operator tools need a safe way to recognize an already-authenticated operator across several web pages. This file is that shared doorway. Its main rule is simple: the bearer token, which is the secret credential proving who the user is, may come from an Authorization header, from a protected browser cookie, or from the form body of the one POST request that starts a session. It is never accepted from the URL query string, because URLs often end up in logs, browser history, screenshots, and shared links.

The flow works like a front desk. First, operator_bearer looks for the credential in the safe places. Then resolve_operator_workspace verifies that credential by asking the bearer-token code to check its claims. A claim is the token's stated identity information, such as email and workspace. The file then enforces the operator-domain gate: only emails from the configured operator domain can use these surfaces. If that passes, the request is scoped to a workspace. With no ?ws= query value, it uses the workspace in the token. With ?ws=, an operator can point the tool at another workspace by UUID, or at a customer domain that is turned into a stable UUID.

Finally, bind_operator_session opens the browser session. It takes the posted token, stores it in one HTTP-only cookie shared by operator surfaces, and redirects back to the page so later requests can use the cookie.

#### Function details

##### `operator_bearer`  (lines 25–38)

```
async def operator_bearer(request: Request) -> str
```

**Purpose**: This function finds the operator's bearer token in the safe places a request may carry it. It deliberately avoids query parameters so the secret does not leak through URLs, logs, or browser history.

**Data flow**: It receives an HTTP request. It first checks the Authorization header for a Bearer token, then checks the shared operator session cookie, and finally, only for POST requests, reads the submitted form field named token. It returns the cleaned token text if it finds one, or an empty string if the request has no acceptable credential.

**Call relations**: resolve_operator_workspace calls this first when deciding whether an operator request is allowed through. If the token is in a POST form, operator_bearer asks the request object to read the form body before handing the token back for verification.

*Call graph*: called by 1 (resolve_operator_workspace); 1 external calls (form).


##### `resolve_operator_workspace`  (lines 41–65)

```
async def resolve_operator_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: This function decides whether an operator request is authorized and, if so, which workspace it should act on. It is the gate that makes sure only users from the operator email domain can use operator-only surfaces.

**Data flow**: It receives the request and the surface-auth context passed by the surrounding web surface. It asks operator_bearer for a token, verifies that token's claims, checks that the email address belongs to the operator domain, and then chooses a workspace. Without a ws query value, it returns the workspace written inside the token. With ws, it accepts a direct UUID or turns a domain name into a stable UUID. If any check fails, it returns None, meaning the request should be rejected.

**Call relations**: This is the main authorization resolver for operator pages. It calls operator_bearer to get the credential, passes that credential to verified_claims to prove it is genuine, uses email_domain to enforce the operator-only domain rule, and uses UUID or uuid5 to turn the requested workspace scope into the identifier the rest of the system expects.

*Call graph*: calls 1 internal fn (operator_bearer); 4 external calls (verified_claims, email_domain, UUID, uuid5).


##### `bind_operator_session`  (lines 68–80)

```
async def bind_operator_session(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function opens an operator browser session after the posted token has already been accepted by the surrounding authorization flow. It stores the token in the shared operator cookie and redirects the browser back to the page.

**Data flow**: It receives the current surface context and HTTP request. It reads the form body and looks for the token field. If the token is missing or blank, it returns a JSON error with a bad-request status. If the token is present, it creates a redirect response to the same URL, sets the shared operator session cookie on that response, and returns the redirect.

**Call relations**: This function is used at the moment an operator session is created. It reads form data from the request, uses JSONResponse when the form is invalid, uses RedirectResponse for the successful browser handoff, and calls set_session_cookie so later operator requests can authenticate through the cookie instead of reposting the token.

*Call graph*: 4 external calls (JSONResponse, RedirectResponse, form, set_session_cookie).


### Image Preview Validation
Uploaded or received image previews are defensively checked for size, format, labeling, and malformed or dangerous content before use.

### `core/src/ufo/image_previews.py`

`domain_logic` · `request handling`

Image previews are small image files, but they can still cause trouble. A file might claim to be a tiny PNG while actually being huge, incomplete, corrupt, or crafted to use too much memory when decoded. This file acts like a careful gatekeeper before the rest of the system trusts an image preview.

It defines the image types the system accepts: GIF, JPEG, PNG, and WebP. It can guess the expected media type from a file path suffix, such as “.jpg” meaning “image/jpeg”. It also defines strict limits: the preview cannot be too large in bytes, too wide or tall, have too many animation frames, or expand into too many pixels when decoded.

The main flow is `validated_image_preview`. It reads an incoming async byte stream piece by piece, checks that the number of bytes exactly matches a signed claim, and refuses anything over the limit. Then it sends the full image bytes to `_ImagePreviewValidator`, which uses Pillow, the Python image library, to open and decode the image safely. It checks both the outer container ending, such as a JPEG end marker, and the actual decoded image format. This matters because file names and claimed media types are easy to fake; the bytes themselves are the source of truth.

#### Function details

##### `raster_image_media_type`  (lines 43–44)

```
def raster_image_media_type(path: str) -> RasterImageMediaType | None
```

**Purpose**: This function guesses the allowed image media type from a path or file name. For example, a name ending in `.png` becomes `image/png`; an unknown suffix returns nothing.

**Data flow**: It receives a path string, extracts the final suffix using `PurePosixPath`, lowercases it, and looks it up in the table of accepted raster image suffixes. The result is either a known media type, such as `image/jpeg`, or `None` if the suffix is not recognized.

**Call relations**: This is a small helper used before deeper validation, when the system needs to turn a user-visible file path into an expected image type. It relies on `PurePosixPath` only to read the suffix consistently, without touching the real filesystem.

*Call graph*: 1 external calls (PurePosixPath).


##### `validated_image_preview`  (lines 47–61)

```
async def validated_image_preview(stream: AsyncIterator[bytes], grant: ImagePreviewGrant) -> bytes
```

**Purpose**: This function reads an image preview from an async stream and proves that it matches the signed size and media type claim. It returns the original bytes only if the preview passes all safety and correctness checks.

**Data flow**: It receives a stream of byte chunks and an `ImagePreviewGrant`, which says the claimed media type and exact byte count. It adds up the chunks as they arrive, rejects the stream if it grows past the claimed size or the maximum allowed size, and rejects it again if the final total is not exactly what was claimed. If the byte count is correct, it joins the chunks into one byte string, runs image validation in a worker thread, and returns the same bytes unchanged.

**Call relations**: This is the public validation path for streamed previews. When basic size checks pass, it hands the more expensive image inspection to `_ImagePreviewValidator.validate` through `asyncio.to_thread`, so decoding the image does not block the async event loop. If anything is wrong, it raises `InvalidImagePreview` instead of returning unsafe data.

*Call graph*: 2 external calls (__init__, to_thread).


##### `_ImagePreviewValidator.validate`  (lines 66–109)

```
def validate(data: bytes, media_type: RasterImageMediaType) -> None
```

**Purpose**: This function deeply inspects image bytes to make sure they are a real, complete, safe image of the claimed type. It checks the image container, asks Pillow to verify the file, then decodes each frame within strict limits.

**Data flow**: It receives raw image bytes and the media type the image claims to be. First it runs `_validate_container` to catch obviously incomplete files by checking their required ending or header structure. Then it opens the bytes with Pillow, treats decompression-bomb warnings as errors, verifies the image structure, reopens the image, walks through its frames, checks dimensions, counts decoded pixels, and forces each frame to load. If Pillow discovers corruption, impossible dimensions, too many frames, too many pixels, or any parse error, the function turns that into `InvalidImagePreview`. If the actual detected image type does not match the claimed media type, it also rejects the preview. On success, it returns nothing and leaves the bytes approved.

**Call relations**: This is the heavy-duty checker called after `validated_image_preview` has already confirmed the byte count. It uses Pillow’s `Image.open` and in-memory `BytesIO` streams to inspect the image without writing it to disk. It calls `_ImagePreviewValidator._validate_container` first because quick container checks can catch truncated files before deeper decoding.

*Call graph*: 5 external calls (__init__, open, BytesIO, catch_warnings, simplefilter).


##### `_ImagePreviewValidator._validate_container`  (lines 112–125)

```
def _validate_container(data: bytes, media_type: RasterImageMediaType) -> None
```

**Purpose**: This function performs quick format-specific checks that the image file looks complete at the byte-container level. It catches common truncation cases before Pillow does deeper image decoding.

**Data flow**: It receives the raw image bytes and the claimed media type. For JPEG, GIF, and PNG, it checks for the expected final marker. For WebP, it checks the RIFF/WebP header and verifies that the declared length matches the actual byte length. If the container does not fit the claimed type, it raises `InvalidImagePreview`; otherwise it returns nothing.

**Call relations**: This is an internal first-pass check used by `_ImagePreviewValidator.validate`. It does not replace full image decoding; it is like checking that a package is sealed and labeled correctly before opening it and inspecting the contents.

*Call graph*: 1 external calls (__init__).

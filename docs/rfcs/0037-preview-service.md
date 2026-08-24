---
rfc: 0037
title: "Preview service — file and site rasterization"
status: accepted
date: 2026-08-18
---

# Preview service — file and site rasterization

> Document previews are rendered inside the sandbox at share time, and only there — a file that
> never passes through a live sandbox (a composer upload, a re-render after a failed share-time
> attempt) can never get a preview. This RFC adds `ufo-preview`: a standalone, share-nothing Rust
> service that turns a document (`pdf docx xlsx pptx csv md svg`), a video (`mp4 mov webm mkv`,
> one extracted frame) into PNG page rasters, and is the one visual renderer for previews and
> document reads. A hosted site arrives through its core-minted ingress view and leaves through a
> presigned preview-key PUT. Share sources arrive by presigned GET or direct multipart and leave by
> a caller-supplied presigned PUT. Hosted reads return a bounded page bundle directly to the sandbox;
> connected terminals relay one contained, bounded document through the deploy.

## Decisions

Settled with the author:

| Fork | Decision |
|---|---|
| Topology | A standalone Rust service (`servers/preview/` crate, image `ufo-preview`), the `servers/cache/` skeleton: axum 0.8, `Config::from_env` fail-loud, `GET /_health`, graceful shutdown. Stateless — no DB, no AWS credentials, no state beyond the in-flight request. |
| Converter | LibreOffice baked into the service image, **spawned per request** (no Gotenberg dependency); rasterization by `pdfium-render` in a separate worker binary; hosted sites captured by a bounded Chromium child. The service process itself never parses a document or page. |
| Producers | `share_file` and hosted document reads call the service through the egress proxy. Core calls it directly for hosted-site pictures and the file-preview backstop. The sandbox image keeps its renderers for document-production skills and social share-card composition. |
| Bytes and core | Share and hosted-read bytes bypass core. A connected terminal relays one contained, bounded read through core because it cannot reach the synthetic service host. |
| Output | PNG for display previews; a ZIP bundle with `manifest.json` and `page-01.png…` for document reads. Page counts are capped at 20. |
| S3 | The service holds zero AWS credentials. It writes only through a caller-supplied presigned PUT URL (`put_url`); the URL fixes the key, so the service chooses neither key nor bucket. |
| Composer preview | The composer sends a member's picked file to the service and shows the picture it returns — the service is the one renderer, not a client-side library. A stateless render, storing nothing: no `inbound_file` table, no persistence. Shipped over the `inline` sink (bytes in → PNG out); the ideal upgrades it to a presigned round-trip that reuses the render in the transcript (see Composer preview below). |

## Architecture

```
sandbox (share_file: one curl) ──▶ ufo-egress ──▶ ufo-preview ──▶ per-request children
hosted sandbox (read) ────────────▶ ufo-egress ──▶    │
connected terminal (read) ──▶ core deploy ──────────▶│
core serve job ──────────────────────────────────▶    │             soffice → PDF
Sites tool ──▶ core ingress URL + preview PUT ───▶    │             chromium → PNG
                                                      │             preview-worker (pdfium) → PNG
  source: multipart file part | presigned GET | ingress view
  sink: inline (bytes back) | put_url ────────────────┴──▶ PUT the caller's presigned URL → S3
```

### API

One render route beside `/_health`. `POST /render` takes multipart: a `request` JSON part and an
optional `file` part; absent a `file` part, `request.source_url` names the file to fetch or hosted
site to visit.

| `request` field | Meaning |
|---|---|
| `kind` | `pdf\|docx\|xlsx\|pptx\|csv\|md\|svg\|mp4\|mov\|webm\|mkv\|site`; files are cross-checked against magic bytes; `site` requires a hosted-site `source_url` |
| `source_url` | presigned GET, exclusive with the `file` part |
| `max_width`, `max_height` | pixel box; pages render to fit, aspect preserved |
| `start_page` | first page to render, one-based; default 1 |
| `pages` | page count from `start_page`; default 1, capped at 20 |
| `sink` | `{"inline": true}` \| `{"bundle": true}` \| `{"put_url": …}` |

| Sink | Response | Used by |
|---|---|---|
| `inline` | `200`, bytes (`image/png` or zip) + metadata headers | composer, filesystem development, tests |
| `bundle` | `200 application/zip`: `manifest.json` plus `page-01.png…` | document reads |
| `put_url` | `200 application/json` metadata, after PUT to the caller's URL | `share_file`, the core render job, any caller holding a presigned PUT |

The bundle manifest carries the kind, total page count, requested range, and each returned page's
original page number, dimensions, filename, and visible text extracted from the converted PDF.

Every response carries `{width, height, page_count, size_bytes, sha256}` (headers on the `inline`
byte response, JSON body on `put_url`). Refusals are typed JSON: `unsupported_type`, `kind_mismatch`,
`too_large`, `render_timeout`, `fetch_refused`.

### Auth — the URL is the capability

There is no service→core call: bytes in, bytes-or-a-store out, one hop each way.

| Edge | Credential |
|---|---|
| `inline` sink | `Authorization: Bearer UFO_PREVIEW_TOKEN` — the bytes come back to the caller, so nothing in the request stands in for authority |
| `bundle` sink from a sandbox | Fixed sentinel bearer; the egress proxy replaces it with `UFO_PREVIEW_TOKEN` only for `preview.ufo.internal`. Forwarded calls admit only direct multipart PDF, PPTX, DOCX, or XLSX bytes; no `source_url` or `inline` sink. |
| `bundle` sink from core | `Authorization: Bearer UFO_PREVIEW_TOKEN` |
| `put_url` sink | none for files — the caller-minted presigned PUT URL *is* the authority to store to exactly that key. `kind: site` also requires the bearer because it spends browser work and visits a URL. A forwarded sandbox call must carry the source as a direct file; only trusted direct callers may use `source_url`. |

`put_url` is a **plain** (unmeasured) presigned PUT: core cannot sign the output's size or checksum
into the URL before the render exists, and it need not — the agent already controls its own preview
content (the torn-out in-sandbox renderer had the same property), and a bad PNG is caught by the
read-time image validator (`core/src/ufo/media/image_previews.py`). Whoever mints the URL fixes its key,
so a request cannot store anywhere else. The capability gates the *write*, not the compute: the
render runs before any store, bounded by the request deadline and the concurrency permit cap.

The proxy admits the preview host with the `service` rule kind the cache daemon already uses, pairs
it with an injection rule for the sentinel bearer, and relays it to the preview deployment rather
than to the cache — each daemon owns the hosts it serves.
The rule carries none of the cache's internet condition: the service fronts nothing public, so
admitting it widens no reach, and an agent narrowed off the internet still shares files. Nothing else
serves that host either, so an unconfigured or unreachable service has no origin to fall through to
and the render is answered 502 inside the tunnel. Turn liveness is checked at CONNECT by the existing
`/internal/egress/authorize`, so the service adds no liveness RPC of its own.

### Pipeline

| Kind | To PDF | Then |
|---|---|---|
| `pdf` | — | `preview-worker` |
| `docx xlsx pptx svg` | `soffice --headless --convert-to pdf`, per-request `-env:UserInstallation` profile — the recipe `builtins.py:112` proves | `preview-worker` |
| `md csv` | rendered to an HTML document in-process (pure Rust on untrusted text — `pulldown-cmark` for markdown, a `csv`-crate parse into a bordered `<table>` for CSV) → `soffice` | `preview-worker` |
| `mp4 mov webm mkv` | — | `ffmpeg` extracts one frame to PNG directly |
| `site` | — | Chromium visits the core-minted ingress view, settles the page, and captures the requested viewport |

CSV goes through the controlled HTML table rather than Calc's delimiter-guessing gridless print, so
the preview is an actual grid. A spreadsheet (`csv`, `xlsx`) renders to a full sheet it does not
fill, so `preview-worker` crops those pages to their content box — the grid, not a grid marooned in
white.

`preview-worker` is a second bin target in the crate wrapping `pdfium-render`: reads the PDF,
extracts visible text, renders the requested page range into the box, crops to content when asked,
writes PNGs, exits. A video skips it —
`ffmpeg` writes the frame PNG directly. A malformed document kills its child, never the service.

### Isolation and caps

- Children run one-per-request in a fresh tmpdir: `setsid` + process-group kill on deadline,
  CPU, file-size and fd-count rlimits, cleared environment. Document and video children also carry
  an address-space rlimit. Chromium reserves more virtual address space than it uses, so its actual
  memory is bounded by the pod's 2 GiB cgroup; one renderer process and a 128 MiB JavaScript heap
  further bound each capture.
- Caps enforced by the service before and after every child: input bytes before any spawn, output
  pixels and bytes (parity with core's `IMAGE_PREVIEW_MAX_BYTES`), page count, per-phase deadlines,
  bounded render concurrency with excess requests waiting for the next permit. A whole-request
  deadline (`UFO_PREVIEW_REQUEST_TIMEOUT_SECS`, default 300s, held with the concurrency permit) answers
  `render_timeout` and frees the permit on a caller that never finishes sending its body.
- File `source_url` and every `put_url` are SSRF-guarded: https only, no redirects,
  resolve-then-connect with private/link-local ranges refused, response size capped.
- A site source must be an HTTPS subdomain of `UFO_PREVIEW_SITE_HOST`. Chromium admits network
  requests only to that exact origin, so page code cannot use the renderer as a path elsewhere.
  Local tests admit loopback.
- Container: runs as `nobody` (image `USER`), no service account, so it holds no AWS credential of
  its own — it reaches S3 only through the presigned URLs a request carries, and the SSRF guard above
  is what bounds where those may point. There is no `NetworkPolicy` (the cluster runs none for any
  workload); egress is bounded by the guard, not the network.

## Both ends

**Rust (`servers/preview/`):** the crate above — `main / config / server / admit` (magic-byte sniff +
caps), `fetch` (SSRF-guarded GET and PUT), `convert` (the soffice spawn), `worker` (the pdfium bin),
`sink` (inline / put_url).

**Core:**

- `share_file`'s in-container program becomes one `curl -F` to the preview host through the proxy,
  with a `put_url` sink core mints (a plain presigned PUT for `artifacts/<uuid>/<stem>.png`) when
  composing the command; the tool parses the metadata reply and fills the same three columns at the
  same moment it does today. The render recipe, the in-container preflight, and `pdftoppm` leave
  core — the tear-out grep is `pdftoppm`.
- A `render_previews` core job (`core/src/ufo/media/preview_renderer.py`) for `shared_artifact` rows whose
  preview never landed — the retry for a share-time service outage, and the producer for composer
  uploads once they persist. It is a core-level workflow like `delivery_sweep`, not an
  extension-context handler: it presigns a GET of the source and a plain presigned PUT of the
  preview key and calls the service **directly** at `UFO_PREVIEW_URL` (serve → the ClusterIP, not
  the proxy-relayed host the sandbox uses), then records the size from the reply — bytes never enter
  core. Only rows shared within a one-hour window are candidates, so a document the service can
  never render ages out rather than retrying forever; batch-at-interval is the whole retry. S3 only
  — the presigned PUT is an S3 operation. The job registers only where `UFO_PREVIEW_URL` is set.
- `SitePreviewer` mints a view of the current turn's hosted sandbox port and an artifact key. On S3,
  the service writes the PNG directly through a presigned PUT and returns bounded metadata. The
  filesystem development backend receives a bounded inline PNG and stores it locally. The Sites
  extension records that result on its row; no browser or preview bytes pass through the sandbox.
  This seam is core because ingress signing and blob-store presigning are not extension capabilities.
- `read` admits PDF, PPTX, DOCX, and XLSX. Hosted carriers send contained, bounded bytes through
  `preview.ufo.internal` and unpack the bundle inside the sandbox. The connected-terminal carrier
  requests a contained, byte-capped copy from the terminal client and calls the service directly
  from the deploy. Both paths return the same text, pagination metadata, and image blocks. XLSX
  rasters are visual QA; the documents extension owns cell, formula, and recalculation validation.
  This relay is core because `read` and carrier selection are core boundaries an extension cannot
  replace.

**Egress:** one `service` rule admitting the preview host for sandbox traffic, paired with an
`injection` rule that recognizes only the fixed sentinel bearer and substitutes
`UFO_PREVIEW_TOKEN`. Both rules are emitted whatever the agent's internet policy, since rendering a
file the sandbox already holds reaches nothing public.

**Tests:** Rust integration tests run Chromium against a local site and the file binaries over
checked-in PDF, PPTX, DOCX, and XLSX fixtures — non-first-page text, dimensions, byte caps, bundle
shape, the `put_url` leg against a local listener, SSRF refusals, deadline kills. `share_file`'s core
half is tested against a real S3 mint with a stubbed sandbox transport; the `render_previews` job
against a real database and S3 mint with an `httpx.MockTransport` returning the service's
contractual reply. The full sandbox → proxy → service → S3 loop is integration-tier.

## Composer preview

The web composer shows a member a picture of a document they have picked but not yet sent, and the
picture is the service's, not a client-side renderer's — one renderer for every surface. A raster
image is still drawn straight off the file (the browser does that safely); a document
(`pdf docx xlsx pptx csv md svg`) or a video (`mp4 mov webm mkv`) goes to the service.

- Core exposes `SurfaceContext.render_preview(kind, data) -> bytes | None`: it posts the bytes to the
  service's `inline` sink with the deploy's `UFO_PREVIEW_TOKEN` and hands the PNG straight back,
  storing nothing. It returns `None` — a named card, never a failed compose — when the service is
  unconfigured or refuses the file. This is the one place core holds the inline bearer.
- The web surface adds `POST /surface/web/preview`: a member session is the whole gate (the render
  is agent-agnostic), it reads one uploaded file, and answers the PNG. It admits no turn and stores
  nothing — a **stateless display render**, the endpoint category CLAUDE.md names. SVG is safe here
  precisely because the page draws the service's raster, never the document's own bytes.
- The composer's `usePickedPicture` posts a document to that route and draws the returned PNG as a
  `data:` URL (the page's policy admits `data:`, not `blob:`).

**Ideal (not yet built).** Sending the file uploads it again and the transcript re-renders the same
content. The upgrade: the browser gets a presigned PUT, uploads the file to the blob store once,
asks the service to render *from that S3 url* into a second presigned URL, and shows the result; the
sent message then carries both URLs, so the conversation view reuses the stored file and its
rendered preview rather than re-uploading and re-rendering. That is a cross-cutting change to message
admission (carrying the refs) and transcript rendering (reading them), and lands as its own unit; the
`source_url` + `put_url` sinks the service already has are what it will use.

## Deploy

`ufo-preview` joins `infra/modules/platform/ecr.tf`, a minted `UFO_PREVIEW_TOKEN`
(`secrets.tf`/`cluster-services.yaml.tpl` — gates byte-returning sinks), the `deploy.yml`
image build (egress-style, post-apply) and rollout gate, and its own Deployment + ClusterIP Service
in `hosted.yaml.tpl`, all gated by a per-env `preview_enabled` (testing on, prod off until proven).
The proxy carries `UFO_EGRESS_PREVIEW_DAEMON` and serve carries `[sandbox] preview_service` (to emit
the rule) and `UFO_PREVIEW_URL` (for the job). No `NetworkPolicy` — none exists for any workload in
this cluster. No compose service: the dev rig is filesystem-backed, so the S3-only preview path never
fires locally. The crate joins the `cargo fmt`/`clippy`/`test` pre-commit gates. spec.md's language
row names it beside `ufo-egress`.

## Risks

- **LibreOffice's parse surface.** The same binary already opens untrusted member files inside
  sandboxes today; here it runs with strictly less authority — no network, no credentials, a
  per-request profile, rlimits, a kill deadline, a read-only container.
- **A hung `soffice`.** Group-kill on deadline; the request answers `render_timeout`; the job
  retries later. Preview failure never fails a share — kept semantics.
- **A stale or overwritten preview.** The `put_url` PUT is unmeasured, so a compromised render (or
  the agent itself) could store a junk PNG to its own artifact's preview key. This is exactly the
  reach the torn-out in-sandbox renderer already had; the read-time image validator rejects a
  non-image, and the key is fixed by whoever mints the URL, so no other record is reachable.
- **Raster drift.** pdfium's rasters differ subtly from `pdftoppm`'s; every consumer asserts only
  PNG + dimensions, none a pixel.

## Non-goals

The sandbox image and document-production skills retain LibreOffice and Poppler. Social share-card
composition remains in the Sites extension because its branded layout is not a generic preview.
Raster inspection does not replace spreadsheet cell, formula, or recalculation validation. Raster
images keep previewing as themselves.

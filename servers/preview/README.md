# ufo-preview

`ufo-preview` turns a file or hosted site into a picture. Give it a PDF, a Word, Excel, or PowerPoint
file, a CSV, a Markdown file, or an SVG and it answers with PNG page images. Give it a video (`mp4`,
`mov`, `webm`, `mkv`) and it answers with one extracted frame. Give it a hosted-site view URL and it
captures the page through Chromium.

The service is stateless and holds no storage credential. A caller either uploads the file on the
request or hands over a URL to fetch it from, and asks for the result either in the response body or
written to a URL the caller supplies. Display previews come back as PNGs. Document reads come back
as a ZIP containing `manifest.json` and `page-01.png` onward; the manifest carries page numbers,
dimensions, and visible text. Nothing is kept after the response.

Two binaries are built here. `ufo-preview` is the service. `preview-worker` is the short-lived child
the service starts for each rasterization, so a malformed file can crash a bounded process instead of
the service. A developer runs the service; the worker is started for you.

Byte-returning requests need a bearer token. A file render's `put_url` is its own single-key storage
capability and needs no bearer. A site capture always needs the bearer and a configured hosted-site
domain. The service refuses anything larger, longer, or with more pages than its limits allow.
`GET /_health` answers `ok`. Configuration is read once at boot; a missing required value exits with status 2.

## Run it in development

Prerequisites:

- a stable Rust toolchain;
- LibreOffice (`soffice`) on `PATH` — every format except PDF is converted through it;
- `ffmpeg` on `PATH`, for video frames only;
- Chromium and Python 3, for hosted sites only;
- the pdfium library, which the fetch script downloads for your platform;
- a LibreOffice profile initialized by the seed script.

```bash
cd preview
sh scripts/fetch-pdfium.sh
sh scripts/seed-soffice-profile.sh .soffice-profile
export UFO_PREVIEW_LISTEN=127.0.0.1:8930
export UFO_PREVIEW_TOKEN=dev-token
export UFO_PREVIEW_PDFIUM_LIB="$PWD/.pdfium/libpdfium.so"
export UFO_PREVIEW_SOFFICE_PROFILE="$PWD/.soffice-profile"
cargo run --bin ufo-preview
```

In a second terminal, check it and render something of your own:

```bash
curl -s http://127.0.0.1:8930/_health

curl -sS -o /tmp/preview.png \
  -H 'Authorization: Bearer dev-token' \
  -F 'request={"kind":"docx","max_width":800,"max_height":800,"sink":{"inline":true}}' \
  -F 'file=@/path/to/your.docx' \
  http://127.0.0.1:8930/render
```

`kind` names the format or `site`, `max_width` and `max_height` bound the picture, `start_page` (default 1)
selects the first page, and `pages` (default 1, maximum 20) selects the range. `sink` is
`{"inline":true}` for display bytes, `{"bundle":true}` for the document-read ZIP, or
`{"put_url":"..."}` to write the preview to that URL. Instead of uploading a file, `source_url`
names one to fetch.

The egress proxy marks sandbox calls with `x-ufo-workspace`. Those calls admit only a direct file:
`put_url` for sharing, or `bundle` for PDF, PPTX, DOCX, and XLSX reads. `source_url` and `inline`
remain available only to trusted direct callers.

Fetching from, or writing to, an `http://` or a loopback address is refused unless
`UFO_PREVIEW_ALLOW_LOCAL=1` is set — which is what a local rig and the tests do, and what a deployment
never does.

| Variable | What it does |
|---|---|
| `UFO_PREVIEW_LISTEN` | Address the service listens on. Required. The container image serves 8930. |
| `UFO_PREVIEW_TOKEN` | Bearer token byte-returning requests must present. Required. |
| `UFO_PREVIEW_PDFIUM_LIB` | Path to the pdfium library used for rasterizing. Required. |
| `UFO_PREVIEW_SOFFICE_PROFILE` | Path to the profile initialized by `UFO_PREVIEW_SOFFICE_BIN` and copied into each request. Required. |
| `UFO_PREVIEW_SOFFICE_BIN` | LibreOffice command. Defaults to `soffice`. Its build must match the profile. |
| `UFO_PREVIEW_FFMPEG_BIN` | ffmpeg command. Defaults to `ffmpeg`. |
| `UFO_PREVIEW_BROWSER_BIN` | Chromium command. Defaults to `/usr/bin/chromium`. |
| `UFO_PREVIEW_PYTHON_BIN` | Python command for the browser driver. Defaults to `/usr/bin/python3`. |
| `UFO_PREVIEW_SITE_DRIVER` | Browser-driver path. Defaults to `/usr/local/libexec/ufo-site-preview.py`. |
| `UFO_PREVIEW_SITE_HOST` | Hosted-site base domain accepted by `kind: site`. Unset disables site capture. |
| `UFO_PREVIEW_ALLOW_LOCAL` | `1` admits `http://` and private or loopback addresses. Off by default. |
| `UFO_PREVIEW_CONCURRENCY` | How many renders run at once. Defaults to the core count, at most 4. |
| `UFO_PREVIEW_MAX_INPUT_MB` | Largest accepted input. Defaults to 100. |
| `UFO_PREVIEW_MAX_OUTPUT_BYTES` | Largest returned result. Defaults to 20 MiB. |
| `UFO_PREVIEW_MAX_PAGES` | Most pages one request may ask for. Defaults to 20. |
| `UFO_PREVIEW_MAX_BOX_PX` | Largest picture edge in pixels. Defaults to 4096. |
| `UFO_PREVIEW_CONVERT_TIMEOUT_SECS` | Bound on one document conversion. Defaults to 120. |
| `UFO_PREVIEW_RASTER_TIMEOUT_SECS` | Bound on one rasterization. Defaults to 30. |
| `UFO_PREVIEW_FETCH_TIMEOUT_SECS` | Bound on fetching a named source. Defaults to 60. |
| `UFO_PREVIEW_SITE_TIMEOUT_SECS` | Bound on one hosted-site capture. Defaults to 30. |
| `UFO_PREVIEW_REQUEST_TIMEOUT_SECS` | Bound on a whole request. Defaults to 300. |
| `UFO_PREVIEW_LOG` | Log filter. Defaults to `info`. Logs are JSON on stdout. |

## Run the tests

```bash
cd preview
cargo test
```

That run covers everything that needs no document tooling: request admission, refusals, limits,
fetching, and delivery. The tests that actually convert and rasterize a file are marked ignored, so
this stays green on a machine with no LibreOffice and no pdfium library.

To run those too, from the repository root:

```bash
make test-preview
```

It requires the pdfium fetch above to have run, plus LibreOffice and `ffmpeg`, and it passes the
library path through for you. The equivalent by hand:

```bash
UFO_PREVIEW_PDFIUM_LIB="$PWD/.pdfium/libpdfium.so" \
UFO_PREVIEW_SOFFICE_PROFILE="$PWD/.soffice-profile" \
cargo test -- --include-ignored --test-threads=4
```

CI runs that full pass inside a container image built from `testbed.Dockerfile`, which carries
LibreOffice, the fonts, `ffmpeg`, and the library, so the same command is reproducible without
installing document tooling on your machine:

```bash
docker build -f testbed.Dockerfile -t ufo-preview-testbed .
docker run --rm --init -v "$PWD:/crate" -w /crate ufo-preview-testbed \
  cargo test -- --include-ignored --test-threads=4
```

The static gates, which CI also runs:

```bash
cargo fmt --check
cargo clippy --all-targets -- -D warnings
# or, from the repository root
make check-preview
```

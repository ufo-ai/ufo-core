use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use ufo_preview::Config;

fn base_map() -> HashMap<String, String> {
    let mut m = HashMap::new();
    m.insert("UFO_PREVIEW_LISTEN".into(), "127.0.0.1:0".into());
    m.insert("UFO_PREVIEW_TOKEN".into(), "test-token".into());
    m.insert(
        "UFO_PREVIEW_PDFIUM_LIB".into(),
        std::env::var("UFO_PREVIEW_PDFIUM_LIB").unwrap_or_else(|_| "/nonexistent".into()),
    );
    let soffice_profile = std::env::var("UFO_PREVIEW_SOFFICE_PROFILE").ok();
    m.insert(
        "UFO_PREVIEW_SOFFICE_PROFILE".into(),
        soffice_profile
            .clone()
            .unwrap_or_else(|| "tests/fixtures/config-profile".into()),
    );
    if let Ok(bin) = std::env::var("UFO_PREVIEW_SOFFICE_BIN") {
        m.insert("UFO_PREVIEW_SOFFICE_BIN".into(), bin);
    } else if soffice_profile.is_none() {
        m.insert(
            "UFO_PREVIEW_SOFFICE_BIN".into(),
            "tests/fixtures/soffice-version".into(),
        );
    }
    m.insert("UFO_PREVIEW_ALLOW_LOCAL".into(), "1".into());
    m
}

fn config(concurrency: Option<usize>) -> Arc<Config> {
    let mut m = base_map();
    if let Some(c) = concurrency {
        m.insert("UFO_PREVIEW_CONCURRENCY".into(), c.to_string());
    }
    Arc::new(Config::from_map(&m).unwrap())
}

async fn serve_app(cfg: Arc<Config>) -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move { axum::serve(listener, ufo_preview::app(cfg)).await.unwrap() });
    format!("http://{addr}")
}

fn multipart(
    request_json: serde_json::Value,
    file: Option<(&str, Vec<u8>)>,
) -> reqwest::multipart::Form {
    let mut form = reqwest::multipart::Form::new().text("request", request_json.to_string());
    if let Some((name, bytes)) = file {
        form = form.part(
            "file",
            reqwest::multipart::Part::bytes(bytes).file_name(name.to_string()),
        );
    }
    form
}

fn req_json(kind: &str, pages: u32) -> serde_json::Value {
    serde_json::json!({
        "kind": kind, "max_width": 800, "max_height": 800, "pages": pages,
        "sink": {"inline": true},
    })
}

fn bundle_json(kind: &str, start_page: u32, pages: u32) -> serde_json::Value {
    serde_json::json!({
        "kind": kind,
        "max_width": 800,
        "max_height": 800,
        "start_page": start_page,
        "pages": pages,
        "sink": {"bundle": true},
    })
}

fn bundle_parts(body: &[u8]) -> (serde_json::Value, Vec<u8>) {
    let mut archive = zip::ZipArchive::new(std::io::Cursor::new(body)).unwrap();
    let manifest: serde_json::Value =
        serde_json::from_reader(archive.by_name("manifest.json").unwrap()).unwrap();
    let mut page = archive.by_name("page-01.png").unwrap();
    let mut bytes = Vec::new();
    std::io::Read::read_to_end(&mut page, &mut bytes).unwrap();
    (manifest, bytes)
}

#[tokio::test]
async fn missing_bearer_is_401() {
    let base = serve_app(config(None)).await;
    let resp = reqwest::Client::new()
        .post(format!("{base}/render"))
        .multipart(multipart(
            req_json("pdf", 1),
            Some(("f.pdf", b"%PDF-".to_vec())),
        ))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 401);
}

#[tokio::test]
async fn site_capture_requires_a_direct_bearer_even_with_a_put_url() {
    let base = serve_app(config(None)).await;
    let request = serde_json::json!({
        "kind": "site",
        "source_url": "http://127.0.0.1:8000/",
        "max_width": 1200,
        "max_height": 900,
        "sink": {"put_url": "https://sink.example/preview.png"},
    });
    let client = reqwest::Client::new();
    let missing = client
        .post(format!("{base}/render"))
        .multipart(multipart(request.clone(), None))
        .send()
        .await
        .unwrap();
    assert_eq!(missing.status(), 401);

    let forwarded = client
        .post(format!("{base}/render"))
        .bearer_auth("test-token")
        .header("x-ufo-workspace", "workspace")
        .multipart(multipart(request, None))
        .send()
        .await
        .unwrap();
    assert_eq!(forwarded.status(), 401);
}

#[tokio::test]
async fn unknown_kind_is_unsupported_type() {
    let base = serve_app(config(None)).await;
    let resp = reqwest::Client::new()
        .post(format!("{base}/render"))
        .bearer_auth("test-token")
        .multipart(multipart(
            req_json("exe", 1),
            Some(("f.exe", b"MZ".to_vec())),
        ))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 415);
    assert_eq!(
        resp.json::<serde_json::Value>().await.unwrap()["error"],
        "unsupported_type"
    );
}

#[tokio::test]
async fn kind_magic_mismatch_is_422() {
    let base = serve_app(config(None)).await;
    let resp = reqwest::Client::new()
        .post(format!("{base}/render"))
        .bearer_auth("test-token")
        .multipart(multipart(
            req_json("pdf", 1),
            Some(("f.pdf", b"PK\x03\x04zip".to_vec())),
        ))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 422);
}

#[tokio::test]
async fn raster_inline_round_trip() {
    use image::ImageEncoder;

    let mut source = Vec::new();
    image::codecs::png::PngEncoder::new(&mut source)
        .write_image(
            &[255, 0, 0, 0, 0, 255, 0, 255],
            2,
            1,
            image::ExtendedColorType::Rgba8,
        )
        .unwrap();
    let base = serve_app(config(None)).await;
    let response = reqwest::Client::new()
        .post(format!("{base}/render"))
        .bearer_auth("test-token")
        .multipart(multipart(req_json("png", 1), Some(("f.png", source))))
        .send()
        .await
        .unwrap();

    assert_eq!(response.status(), 200);
    assert_eq!(response.headers()["content-type"], "image/png");
    assert_eq!(response.headers()["x-preview-width"], "2");
    assert_eq!(response.headers()["x-preview-height"], "1");
    let cover = image::load_from_memory(&response.bytes().await.unwrap())
        .unwrap()
        .into_rgba8();
    assert_eq!(cover.get_pixel(0, 0).0, [255, 0, 0, 0]);
    assert_eq!(cover.get_pixel(1, 0).0, [0, 255, 0, 255]);
}

#[tokio::test]
async fn held_capacity_queues_the_next_render() {
    let cfg = config(Some(1));
    let render = ufo_preview::render::Render::new(cfg.clone());
    let held = render.semaphore().clone().acquire_owned().await.unwrap();
    let base = {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let app = ufo_preview::server::app_with(cfg, render);
        tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        format!("http://{addr}")
    };
    let request = reqwest::Client::new()
        .post(format!("{base}/render"))
        .bearer_auth("test-token")
        .multipart(multipart(req_json("pdf", 1), None))
        .send();
    tokio::pin!(request);
    assert!(
        tokio::time::timeout(Duration::from_millis(50), &mut request)
            .await
            .is_err()
    );
    drop(held);
    assert_eq!(request.await.unwrap().status(), 415);
}

#[tokio::test]
async fn forwarded_sandbox_calls_accept_only_direct_file_capabilities() {
    let base = serve_app(config(None)).await;
    let client = reqwest::Client::new();
    let source_request = serde_json::json!({
        "kind": "pdf",
        "source_url": "https://example.com/chosen-data",
        "max_width": 800,
        "max_height": 800,
        "sink": {"bundle": true},
    });
    let source = client
        .post(format!("{base}/render"))
        .bearer_auth("test-token")
        .header("x-ufo-workspace", "workspace")
        .multipart(multipart(source_request, None))
        .send()
        .await
        .unwrap();
    assert_eq!(source.status(), 401);

    let other_kind = client
        .post(format!("{base}/render"))
        .bearer_auth("test-token")
        .header("x-ufo-workspace", "workspace")
        .multipart(multipart(
            bundle_json("md", 1, 1),
            Some(("f.md", b"# workspace text".to_vec())),
        ))
        .send()
        .await
        .unwrap();
    assert_eq!(other_kind.status(), 401);

    let inline = client
        .post(format!("{base}/render"))
        .bearer_auth("test-token")
        .header("x-ufo-workspace", "workspace")
        .multipart(multipart(
            req_json("pdf", 1),
            Some(("f.pdf", b"%PDF-".to_vec())),
        ))
        .send()
        .await
        .unwrap();
    assert_eq!(inline.status(), 401);

    let share_request = serde_json::json!({
        "kind": "pdf",
        "max_width": 800,
        "max_height": 800,
        "sink": {"put_url": "https://sink.example/preview.png"},
    });
    let share = client
        .post(format!("{base}/render"))
        .header("x-ufo-workspace", "workspace")
        .multipart(multipart(
            share_request,
            Some(("f.pdf", b"PK\x03\x04zip".to_vec())),
        ))
        .send()
        .await
        .unwrap();
    assert_eq!(share.status(), 422);
}

#[tokio::test]
async fn file_part_over_max_input_is_413() {
    let mut m = base_map();
    m.insert("UFO_PREVIEW_MAX_INPUT_MB".into(), "1".into());
    let cfg = Arc::new(Config::from_map(&m).unwrap());
    let base = serve_app(cfg).await;
    let oversized = vec![0u8; 1_200_000];
    let resp = reqwest::Client::new()
        .post(format!("{base}/render"))
        .bearer_auth("test-token")
        .multipart(multipart(req_json("pdf", 1), Some(("f.pdf", oversized))))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 413);
    assert_eq!(
        resp.json::<serde_json::Value>().await.unwrap()["error"],
        "too_large"
    );
}

#[tokio::test]
#[ignore]
async fn pdf_inline_round_trip() {
    let base = serve_app(config(None)).await;
    let pdf = std::fs::read("tests/fixtures/fixture.pdf").unwrap();
    let resp = reqwest::Client::new()
        .post(format!("{base}/render"))
        .bearer_auth("test-token")
        .multipart(multipart(req_json("pdf", 1), Some(("f.pdf", pdf))))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 200);
    assert_eq!(resp.headers()["content-type"], "image/png");
    assert_eq!(resp.headers()["x-preview-page-count"], "2");
    let w: u32 = resp.headers()["x-preview-width"]
        .to_str()
        .unwrap()
        .parse()
        .unwrap();
    assert!(w <= 800);
    let body = resp.bytes().await.unwrap();
    assert!(body.starts_with(b"\x89PNG"));
}

#[tokio::test]
#[ignore]
async fn markdown_inline_uses_the_compact_dark_page() {
    let base = serve_app(config(None)).await;
    let markdown = b"# Preview\n\n| Name | State |\n| --- | --- |\n| Review | Passed |\n";
    let response = reqwest::Client::new()
        .post(format!("{base}/render"))
        .bearer_auth("test-token")
        .multipart(multipart(
            req_json("md", 1),
            Some(("preview.md", markdown.to_vec())),
        ))
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), 200);
    let bytes = response.bytes().await.unwrap();
    let image = image::load_from_memory(&bytes).unwrap().to_rgba8();
    assert_eq!(image.get_pixel(0, 0).0, [38, 41, 41, 255]);
    let background = image.get_pixel(0, 0).0;
    let (mut min_x, mut min_y) = image.dimensions();
    let mut max_x = 0;
    for (x, y, pixel) in image.enumerate_pixels() {
        if pixel
            .0
            .iter()
            .zip(background)
            .any(|(channel, background)| channel.abs_diff(background) > 8)
        {
            min_x = min_x.min(x);
            min_y = min_y.min(y);
            max_x = max_x.max(x);
        }
    }
    assert!((1..=24).contains(&min_x), "left inset is {min_x}px");
    let right_inset = image.width() - max_x - 1;
    assert!(
        (1..=24).contains(&right_inset),
        "right inset is {right_inset}px"
    );
    assert!(
        min_x.abs_diff(right_inset) <= 1,
        "horizontal insets are {min_x}px and {right_inset}px"
    );
    assert!((1..=24).contains(&min_y), "top inset is {min_y}px");
    assert!(image.width() < image.height());
}

#[tokio::test]
#[ignore]
async fn site_inline_round_trip() {
    let cross_origin_requested = Arc::new(AtomicBool::new(false));
    let requested = cross_origin_requested.clone();
    let cross_origin = serve_app_router(axum::Router::new().route(
        "/pixel.png",
        axum::routing::get(move || {
            requested.store(true, Ordering::SeqCst);
            async { "pixel" }
        }),
    ))
    .await;
    let page = axum::Router::new().route(
        "/",
        axum::routing::get(move || {
            let cross_origin = cross_origin.clone();
            async move {
                axum::response::Html(format!(
                    "<html><body style='background:#111;color:#fff'><h1>Site preview</h1><img src='{cross_origin}/pixel.png'></body></html>"
                ))
            }
        }),
    );
    let source = serve_app_router(page).await;
    let base = serve_app(config(None)).await;
    let request = serde_json::json!({
        "kind": "site",
        "source_url": format!("{source}/"),
        "max_width": 1200,
        "max_height": 900,
        "sink": {"inline": true},
    });
    let response = reqwest::Client::new()
        .post(format!("{base}/render"))
        .bearer_auth("test-token")
        .multipart(multipart(request, None))
        .send()
        .await
        .unwrap();
    let status = response.status();
    let headers = response.headers().clone();
    let bytes = response.bytes().await.unwrap();
    assert_eq!(status, 200, "{}", String::from_utf8_lossy(&bytes));
    assert_eq!(headers["content-type"], "image/png");
    assert_eq!(headers["x-preview-width"], "1200");
    assert_eq!(headers["x-preview-height"], "900");
    assert!(bytes.starts_with(b"\x89PNG\r\n\x1a\n"));
    assert!(!cross_origin_requested.load(Ordering::SeqCst));
}

async fn serve_app_router(app: axum::Router) -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    format!("http://{address}")
}

#[tokio::test]
#[ignore]
async fn docx_from_source_url_to_put_url() {
    type SinkStore = Arc<tokio::sync::Mutex<Option<(String, Vec<u8>)>>>;
    let fixture = std::fs::read("tests/fixtures/fixture.docx").unwrap();
    let store: SinkStore = Arc::default();
    let sink = store.clone();
    let origin = {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let app = axum::Router::new()
            .route(
                "/doc.docx",
                axum::routing::get(move || {
                    let f = fixture.clone();
                    async move { f }
                }),
            )
            .route(
                "/sink.png",
                axum::routing::put(move |h: axum::http::HeaderMap, b: axum::body::Bytes| {
                    let sink = sink.clone();
                    async move {
                        let ct = h["content-type"].to_str().unwrap().to_string();
                        *sink.lock().await = Some((ct, b.to_vec()));
                        "ok"
                    }
                }),
            );
        tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        format!("http://{addr}")
    };
    let base = serve_app(config(None)).await;
    let request = serde_json::json!({
        "kind": "docx", "max_width": 640, "max_height": 640, "pages": 1,
        "source_url": format!("{origin}/doc.docx"),
        "sink": {"put_url": format!("{origin}/sink.png")},
    });
    // No bearer: the caller-minted put_url is itself the capability.
    let resp = reqwest::Client::new()
        .post(format!("{base}/render"))
        .multipart(multipart(request, None))
        .send()
        .await
        .unwrap();
    let status = resp.status();
    assert_eq!(resp.headers()["content-type"], "application/json");
    let body: serde_json::Value = resp.json().await.unwrap();
    assert_eq!(status, 200, "{body:?}");
    assert_eq!(body["page_count"], 1);
    assert!(body["size_bytes"].as_u64().unwrap() > 0);
    let stored = store.lock().await.clone().unwrap();
    assert_eq!(stored.0, "image/png");
    assert!(stored.1.starts_with(b"\x89PNG"));
}

#[tokio::test]
#[ignore]
async fn multi_page_answers_zip() {
    let base = serve_app(config(None)).await;
    let pdf = std::fs::read("tests/fixtures/fixture.pdf").unwrap();
    let resp = reqwest::Client::new()
        .post(format!("{base}/render"))
        .bearer_auth("test-token")
        .multipart(multipart(req_json("pdf", 5), Some(("f.pdf", pdf))))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 200);
    assert_eq!(resp.headers()["content-type"], "application/zip");
    let body = resp.bytes().await.unwrap();
    let mut archive = zip::ZipArchive::new(std::io::Cursor::new(body.to_vec())).unwrap();
    assert_eq!(archive.len(), 2);
    assert!(archive.by_name("page-01.png").is_ok());
    assert!(archive.by_name("page-02.png").is_ok());
}

#[tokio::test]
#[ignore]
async fn document_bundle_contains_manifest_text_dimensions_and_non_first_page() {
    let base = serve_app(config(None)).await;
    let pdf = std::fs::read("tests/fixtures/fixture.pdf").unwrap();
    let resp = reqwest::Client::new()
        .post(format!("{base}/render"))
        .bearer_auth("test-token")
        .header("x-ufo-workspace", "workspace")
        .multipart(multipart(bundle_json("pdf", 2, 1), Some(("f.pdf", pdf))))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 200);
    assert_eq!(resp.headers()["content-type"], "application/zip");
    let body = resp.bytes().await.unwrap();
    let mut archive = zip::ZipArchive::new(std::io::Cursor::new(body.to_vec())).unwrap();
    assert_eq!(archive.len(), 2);
    let manifest: serde_json::Value =
        serde_json::from_reader(archive.by_name("manifest.json").unwrap()).unwrap();
    assert_eq!(manifest["kind"], "pdf");
    assert_eq!(manifest["total_pages"], 2);
    assert_eq!(manifest["requested_range"]["start_page"], 2);
    assert_eq!(manifest["requested_range"]["limit"], 1);
    assert_eq!(manifest["pages"][0]["number"], 2);
    assert!(manifest["pages"][0]["width"].as_u64().unwrap() <= 800);
    assert!(manifest["pages"][0]["height"].as_u64().unwrap() <= 800);
    assert!(manifest["pages"][0]["text"]
        .as_str()
        .unwrap()
        .contains("Fixture page two"));
    drop(manifest);
    let mut page = archive.by_name("page-01.png").unwrap();
    let mut bytes = Vec::new();
    std::io::Read::read_to_end(&mut page, &mut bytes).unwrap();
    assert!(bytes.starts_with(b"\x89PNG"));
}

#[tokio::test]
#[ignore]
async fn all_read_document_kinds_answer_visible_text_and_images() {
    let base = serve_app(config(None)).await;
    for (kind, fixture, expected) in [
        ("pdf", "fixture.pdf", "Fixture page one"),
        ("pptx", "fixture.pptx", "Fixture"),
        (
            "docx",
            "fixture-layout.docx",
            "DOCX clipping and overlap layout page one",
        ),
        (
            "xlsx",
            "fixture-print-layout.xlsx",
            "XLSX print layout page one",
        ),
    ] {
        let file = std::fs::read(format!("tests/fixtures/{fixture}")).unwrap();
        let response = reqwest::Client::new()
            .post(format!("{base}/render"))
            .bearer_auth("test-token")
            .multipart(multipart(bundle_json(kind, 1, 1), Some((fixture, file))))
            .send()
            .await
            .unwrap();
        assert_eq!(response.status(), 200, "{kind}");
        let body = response.bytes().await.unwrap();
        let (manifest, page) = bundle_parts(&body);
        assert_eq!(manifest["kind"], kind);
        assert!(
            manifest["pages"][0]["text"]
                .as_str()
                .unwrap()
                .contains(expected),
            "{kind}: {manifest}"
        );
        assert!(manifest["pages"][0]["width"].as_u64().unwrap() > 0);
        assert!(manifest["pages"][0]["height"].as_u64().unwrap() > 0);
        assert!(page.starts_with(b"\x89PNG"));
    }
}

#[tokio::test]
#[ignore]
async fn every_read_document_kind_renders_its_non_first_page() {
    let base = serve_app(config(None)).await;
    for (kind, fixture, expected) in [
        ("pdf", "fixture.pdf", "Fixture page two"),
        ("pptx", "fixture-read.pptx", "PPTX visual page two"),
        ("docx", "fixture-layout.docx", "DOCX layout page two"),
        (
            "xlsx",
            "fixture-print-layout.xlsx",
            "XLSX print layout page two",
        ),
    ] {
        let file = std::fs::read(format!("tests/fixtures/{fixture}")).unwrap();
        let response = reqwest::Client::new()
            .post(format!("{base}/render"))
            .bearer_auth("test-token")
            .multipart(multipart(bundle_json(kind, 2, 1), Some((fixture, file))))
            .send()
            .await
            .unwrap();
        assert_eq!(response.status(), 200, "{kind}");
        let body = response.bytes().await.unwrap();
        let (manifest, page) = bundle_parts(&body);
        assert_eq!(manifest["pages"][0]["number"], 2);
        assert!(
            manifest["pages"][0]["text"]
                .as_str()
                .unwrap()
                .contains(expected),
            "{kind}: {manifest}"
        );
        assert!(page.starts_with(b"\x89PNG"));
    }
}

#[tokio::test]
#[ignore]
async fn requested_pages_above_one_answers_zip_even_with_one_rendered_page() {
    let base = serve_app(config(None)).await;
    let pdf = std::fs::read("tests/fixtures/fixture-1page.pdf").unwrap();
    let resp = reqwest::Client::new()
        .post(format!("{base}/render"))
        .bearer_auth("test-token")
        .multipart(multipart(req_json("pdf", 5), Some(("f.pdf", pdf))))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 200);
    assert_eq!(resp.headers()["content-type"], "application/zip");
    let body = resp.bytes().await.unwrap();
    let mut archive = zip::ZipArchive::new(std::io::Cursor::new(body.to_vec())).unwrap();
    assert_eq!(archive.len(), 1);
    assert!(archive.by_name("page-01.png").is_ok());
}

#[tokio::test]
#[ignore]
async fn rendered_output_over_max_output_bytes_is_413() {
    let mut m = base_map();
    m.insert("UFO_PREVIEW_MAX_OUTPUT_BYTES".into(), "64".into());
    let cfg = Arc::new(Config::from_map(&m).unwrap());
    let base = serve_app(cfg).await;
    let pdf = std::fs::read("tests/fixtures/fixture.pdf").unwrap();
    let resp = reqwest::Client::new()
        .post(format!("{base}/render"))
        .bearer_auth("test-token")
        .multipart(multipart(req_json("pdf", 1), Some(("f.pdf", pdf))))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 413);
    assert_eq!(
        resp.json::<serde_json::Value>().await.unwrap()["error"],
        "too_large"
    );
}

#[tokio::test]
async fn put_url_sink_admits_without_bearer() {
    // A magic-mismatched file reaches admission and answers 422 — proving the request got past the
    // auth gate with no bearer (a 401 would mean the put_url capability was not honored).
    let base = serve_app(config(None)).await;
    let request = serde_json::json!({
        "kind": "pdf", "max_width": 800, "max_height": 800, "pages": 1,
        "sink": {"put_url": "https://sink.example/preview.png"},
    });
    let resp = reqwest::Client::new()
        .post(format!("{base}/render"))
        .multipart(multipart(
            request,
            Some(("f.pdf", b"PK\x03\x04zip".to_vec())),
        ))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 422);
}

#[tokio::test]
async fn total_request_deadline_answers_504_and_frees_the_permit() {
    let mut m = base_map();
    m.insert("UFO_PREVIEW_REQUEST_TIMEOUT_SECS".into(), "1".into());
    m.insert("UFO_PREVIEW_CONCURRENCY".into(), "1".into());
    let cfg = Arc::new(Config::from_map(&m).unwrap());
    let base = serve_app(cfg).await;

    use futures_util::StreamExt;
    let trickle = futures_util::stream::once(async {
        Ok::<_, std::io::Error>(b"--ufo-test-boundary\r\n".to_vec())
    })
    .chain(futures_util::stream::pending());
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(10))
        .build()
        .unwrap();
    let resp = client
        .post(format!("{base}/render"))
        .header(
            "content-type",
            "multipart/form-data; boundary=ufo-test-boundary",
        )
        .body(reqwest::Body::wrap_stream(trickle))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 504);
    assert_eq!(
        resp.json::<serde_json::Value>().await.unwrap()["error"],
        "render_timeout"
    );

    // A follow-up request is admitted rather than answered `busy`, proving the stalled request's
    // permit was released when its deadline expired rather than held forever.
    let follow_up = reqwest::Client::new()
        .post(format!("{base}/render"))
        .bearer_auth("test-token")
        .multipart(multipart(
            req_json("exe", 1),
            Some(("f.exe", b"MZ".to_vec())),
        ))
        .send()
        .await
        .unwrap();
    assert_eq!(follow_up.status(), 415);
}

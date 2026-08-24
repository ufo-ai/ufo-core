use std::sync::Arc;

use ufo_preview::admit::Kind;
use ufo_preview::convert::to_pdf;

fn test_config() -> Arc<ufo_preview::Config> {
    let mut m = std::collections::HashMap::new();
    m.insert("UFO_PREVIEW_LISTEN".into(), "127.0.0.1:0".into());
    m.insert("UFO_PREVIEW_TOKEN".into(), "test-token".into());
    m.insert(
        "UFO_PREVIEW_PDFIUM_LIB".into(),
        std::env::var("UFO_PREVIEW_PDFIUM_LIB")
            .expect("set UFO_PREVIEW_PDFIUM_LIB — run servers/preview/scripts/fetch-pdfium.sh"),
    );
    m.insert("UFO_PREVIEW_ALLOW_LOCAL".into(), "1".into());
    if let Ok(bin) = std::env::var("UFO_PREVIEW_SOFFICE_BIN") {
        m.insert("UFO_PREVIEW_SOFFICE_BIN".into(), bin);
    }
    Arc::new(ufo_preview::Config::from_map(&m).unwrap())
}

async fn converts(kind: Kind, fixture: &str) {
    let cfg = test_config();
    let work = tempfile::tempdir().unwrap();
    let input = work.path().join(format!("input.{}", kind.extension()));
    std::fs::copy(format!("tests/fixtures/{fixture}"), &input).unwrap();
    let pdf = to_pdf(kind, &input, work.path(), &cfg).await.unwrap();
    let head = std::fs::read(&pdf).unwrap();
    assert!(head.starts_with(b"%PDF-"), "{kind:?} produced no pdf");
}

#[tokio::test]
#[ignore]
async fn docx_converts() {
    converts(Kind::Docx, "fixture.docx").await
}

#[tokio::test]
#[ignore]
async fn xlsx_converts() {
    converts(Kind::Xlsx, "fixture.xlsx").await
}

#[tokio::test]
#[ignore]
async fn pptx_converts() {
    converts(Kind::Pptx, "fixture.pptx").await
}

#[tokio::test]
#[ignore]
async fn parallel_office_requests_complete() {
    tokio::join!(
        converts(Kind::Docx, "fixture.docx"),
        converts(Kind::Xlsx, "fixture.xlsx"),
        converts(Kind::Pptx, "fixture.pptx"),
        converts(Kind::Csv, "fixture.csv"),
    );
}

#[tokio::test]
#[ignore]
async fn csv_converts() {
    converts(Kind::Csv, "fixture.csv").await
}

#[tokio::test]
#[ignore]
async fn csv_outside_utf8_converts() {
    let cfg = test_config();
    let work = tempfile::tempdir().unwrap();
    let input = work.path().join("input.csv");
    // 0xe9 is `é` in a Windows-1252 export out of Excel, and is not valid UTF-8.
    std::fs::write(&input, b"name,city\nJos\xe9,Reno\n").unwrap();
    let pdf = to_pdf(Kind::Csv, &input, work.path(), &cfg).await.unwrap();
    let head = std::fs::read(&pdf).unwrap();
    assert!(head.starts_with(b"%PDF-"), "non-utf-8 csv produced no pdf");
}

#[tokio::test]
#[ignore]
async fn svg_converts() {
    converts(Kind::Svg, "fixture.svg").await
}

#[tokio::test]
#[ignore]
async fn md_converts_through_html() {
    converts(Kind::Md, "fixture.md").await
}

#[tokio::test]
#[ignore]
async fn pdf_passes_through() {
    let cfg = test_config();
    let work = tempfile::tempdir().unwrap();
    let input = work.path().join("input.pdf");
    std::fs::copy("tests/fixtures/fixture.pdf", &input).unwrap();
    let pdf = to_pdf(Kind::Pdf, &input, work.path(), &cfg).await.unwrap();
    assert_eq!(pdf, input);
}

#[tokio::test]
#[ignore]
async fn garbage_docx_refuses_without_hanging() {
    let cfg = test_config();
    let work = tempfile::tempdir().unwrap();
    let input = work.path().join("input.docx");
    std::fs::write(&input, b"PK\x03\x04 not actually a docx").unwrap();
    assert!(to_pdf(Kind::Docx, &input, work.path(), &cfg).await.is_err());
}

#[tokio::test]
#[ignore]
async fn video_frame_extracts_a_png() {
    let cfg = test_config();
    let work = tempfile::tempdir().unwrap();
    let input = work.path().join("input.mp4");
    std::fs::copy("tests/fixtures/fixture.mp4", &input).unwrap();
    ufo_preview::convert::video_frame(&input, work.path(), 320, 320, &cfg)
        .await
        .unwrap();
    let frame = std::fs::read(work.path().join("out").join("page-01.png")).unwrap();
    assert!(frame.starts_with(b"\x89PNG"), "ffmpeg produced no png");
}

#[tokio::test]
#[ignore]
async fn video_frame_extracts_a_png_from_a_4k_video() {
    let cfg = test_config();
    let work = tempfile::tempdir().unwrap();
    let input = work.path().join("input.mp4");
    // Generated here, not committed: the frame window only overflows past 100 frames of
    // 3840x2160, which is 30 MB of H.264 no repository should carry.
    let generated = std::process::Command::new(&cfg.ffmpeg_bin)
        .args([
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=3840x2160:rate=30:duration=4",
            "-threads",
            "2",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
        ])
        .arg(&input)
        .output()
        .unwrap();
    assert!(
        generated.status.success(),
        "generating the 4k source: {}",
        String::from_utf8_lossy(&generated.stderr)
    );
    ufo_preview::convert::video_frame(&input, work.path(), 600, 800, &cfg)
        .await
        .unwrap();
    let frame = std::fs::read(work.path().join("out").join("page-01.png")).unwrap();
    assert!(frame.starts_with(b"\x89PNG"), "4k video produced no png");
}

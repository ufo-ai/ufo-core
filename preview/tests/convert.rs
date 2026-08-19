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
            .expect("set UFO_PREVIEW_PDFIUM_LIB — run preview/scripts/fetch-pdfium.sh"),
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
async fn csv_converts() {
    converts(Kind::Csv, "fixture.csv").await
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

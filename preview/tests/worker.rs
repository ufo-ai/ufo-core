#[derive(serde::Deserialize)]
struct Meta {
    page_count: u32,
    pages: Vec<PageMeta>,
}

#[derive(serde::Deserialize)]
struct PageMeta {
    index: u32,
    width: u32,
    height: u32,
}

fn pdfium_lib() -> String {
    std::env::var("UFO_PREVIEW_PDFIUM_LIB")
        .expect("set UFO_PREVIEW_PDFIUM_LIB — run preview/scripts/fetch-pdfium.sh")
}

#[test]
#[ignore]
fn renders_first_page_within_box() {
    let out = tempfile::tempdir().unwrap();
    let output = std::process::Command::new(env!("CARGO_BIN_EXE_preview-worker"))
        .args([
            &pdfium_lib(),
            "tests/fixtures/fixture.pdf",
            out.path().to_str().unwrap(),
            "800",
            "800",
            "1",
        ])
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let meta: Meta = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(meta.page_count, 2);
    assert_eq!(meta.pages.len(), 1);
    assert_eq!(meta.pages[0].index, 1);
    assert!(meta.pages[0].width <= 800 && meta.pages[0].height <= 800);
    let png = std::fs::read(out.path().join("page-01.png")).unwrap();
    assert!(png.starts_with(b"\x89PNG"));
}

#[test]
#[ignore]
fn renders_two_pages() {
    let out = tempfile::tempdir().unwrap();
    let output = std::process::Command::new(env!("CARGO_BIN_EXE_preview-worker"))
        .args([
            &pdfium_lib(),
            "tests/fixtures/fixture.pdf",
            out.path().to_str().unwrap(),
            "400",
            "400",
            "5",
        ])
        .output()
        .unwrap();
    assert!(output.status.success());
    let meta: Meta = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(meta.pages.len(), 2);
    assert!(out.path().join("page-02.png").is_file());
}

#[test]
#[ignore]
fn garbage_pdf_exits_nonzero() {
    let out = tempfile::tempdir().unwrap();
    let bad = out.path().join("bad.pdf");
    std::fs::write(&bad, b"%PDF- not really").unwrap();
    let output = std::process::Command::new(env!("CARGO_BIN_EXE_preview-worker"))
        .args([
            &pdfium_lib(),
            bad.to_str().unwrap(),
            out.path().to_str().unwrap(),
            "800",
            "800",
            "1",
        ])
        .output()
        .unwrap();
    assert!(!output.status.success());
}

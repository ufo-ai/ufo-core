use std::path::Path;

use pdfium_render::prelude::*;

/// The rasterizing child: everything pdfium touches runs here, in a process the parent can kill.
/// Prints one metadata JSON line on success; exit 1 on any failure.
pub fn run(lib: &str, pdf: &str, outdir: &str, max_w: &str, max_h: &str, pages: &str) -> i32 {
    match render(lib, pdf, outdir, max_w, max_h, pages) {
        Ok(meta) => {
            println!("{meta}");
            0
        }
        Err(e) => {
            eprintln!("{e}");
            1
        }
    }
}

fn render(
    lib: &str,
    pdf: &str,
    outdir: &str,
    max_w: &str,
    max_h: &str,
    pages: &str,
) -> Result<String, String> {
    let max_w: i32 = max_w.parse().map_err(|_| "bad max-w".to_string())?;
    let max_h: i32 = max_h.parse().map_err(|_| "bad max-h".to_string())?;
    let pages: u32 = pages.parse().map_err(|_| "bad pages".to_string())?;
    let pdfium =
        Pdfium::new(Pdfium::bind_to_library(lib).map_err(|e| format!("pdfium bind: {e}"))?);
    let doc = pdfium
        .load_pdf_from_file(pdf, None)
        .map_err(|e| format!("pdf load: {e}"))?;
    let page_count = doc.pages().len() as u32;
    let take = page_count.min(pages);
    let config = PdfRenderConfig::new()
        .set_target_width(max_w)
        .set_maximum_height(max_h);
    let mut metas = Vec::new();
    for i in 0..take {
        let page = doc
            .pages()
            .get(i as u16)
            .map_err(|e| format!("page {i}: {e}"))?;
        let img = page
            .render_with_config(&config)
            .map_err(|e| format!("render {i}: {e}"))?
            .as_image();
        let (w, h) = (img.width(), img.height());
        img.save(Path::new(outdir).join(format!("page-{:02}.png", i + 1)))
            .map_err(|e| format!("save {i}: {e}"))?;
        metas.push(serde_json::json!({"index": i + 1, "width": w, "height": h}));
    }
    Ok(serde_json::json!({"page_count": page_count, "pages": metas}).to_string())
}

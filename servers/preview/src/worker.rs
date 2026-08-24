use std::path::Path;

use image::DynamicImage;
use pdfium_render::prelude::*;

const CROP_MARGIN_PX: u32 = 6;
const CONTENT_CHANNEL_MAX: u8 = 244;
const CONTENT_ALPHA_MIN: u8 = 8;

pub struct Request<'a> {
    pub lib: &'a str,
    pub pdf: &'a str,
    pub outdir: &'a str,
    pub max_width: &'a str,
    pub max_height: &'a str,
    pub start_page: &'a str,
    pub pages: &'a str,
    pub crop: bool,
}

/// The rasterizing child: everything pdfium touches runs here, in a process the parent can kill.
/// Prints one metadata JSON line on success; exit 1 on any failure. `crop` == "1" trims each page
/// to its content box — a spreadsheet renders to a full sheet of paper it does not fill, so the
/// preview would otherwise be a grid marooned in white.
pub fn run(request: Request<'_>) -> i32 {
    match render(&request) {
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

fn render(request: &Request<'_>) -> Result<String, String> {
    let max_w: i32 = request
        .max_width
        .parse()
        .map_err(|_| "bad max-w".to_string())?;
    let max_h: i32 = request
        .max_height
        .parse()
        .map_err(|_| "bad max-h".to_string())?;
    let start_page: u32 = request
        .start_page
        .parse()
        .map_err(|_| "bad start-page".to_string())?;
    let pages: u32 = request.pages.parse().map_err(|_| "bad pages".to_string())?;
    let pdfium =
        Pdfium::new(Pdfium::bind_to_library(request.lib).map_err(|e| format!("pdfium bind: {e}"))?);
    let doc = pdfium
        .load_pdf_from_file(request.pdf, None)
        .map_err(|e| format!("pdf load: {e}"))?;
    let page_count = doc.pages().len() as u32;
    let start = start_page.max(1).min(page_count.max(1));
    let take = page_count.saturating_sub(start - 1).min(pages);
    let config = PdfRenderConfig::new()
        .set_target_width(max_w)
        .set_maximum_height(max_h);
    let mut metas = Vec::new();
    for offset in 0..take {
        let i = start - 1 + offset;
        let page = doc
            .pages()
            .get(i as u16)
            .map_err(|e| format!("page {i}: {e}"))?;
        let text = page.text().map_err(|e| format!("text {i}: {e}"))?.all();
        let mut img = page
            .render_with_config(&config)
            .map_err(|e| format!("render {i}: {e}"))?
            .as_image();
        if request.crop {
            img = crop_to_content(img);
        }
        let (w, h) = (img.width(), img.height());
        img.save(Path::new(request.outdir).join(format!("page-{:02}.png", offset + 1)))
            .map_err(|e| format!("save {i}: {e}"))?;
        metas.push(serde_json::json!({
            "index": i + 1,
            "width": w,
            "height": h,
            "text": text,
        }));
    }
    Ok(serde_json::json!({"page_count": page_count, "pages": metas}).to_string())
}

/// Trim an image to the bounding box of its non-background pixels, with a small margin. Background
/// is near-white and fully transparent; a page with no content at all is returned untouched.
fn crop_to_content(img: DynamicImage) -> DynamicImage {
    let rgba = img.to_rgba8();
    let (w, h) = (rgba.width(), rgba.height());
    let (mut min_x, mut min_y, mut max_x, mut max_y) = (w, h, 0u32, 0u32);
    for (x, y, pixel) in rgba.enumerate_pixels() {
        let [r, g, b, a] = pixel.0;
        let content = a > CONTENT_ALPHA_MIN
            && (r <= CONTENT_CHANNEL_MAX || g <= CONTENT_CHANNEL_MAX || b <= CONTENT_CHANNEL_MAX);
        if content {
            min_x = min_x.min(x);
            min_y = min_y.min(y);
            max_x = max_x.max(x);
            max_y = max_y.max(y);
        }
    }
    if min_x > max_x || min_y > max_y {
        return img;
    }
    let x0 = min_x.saturating_sub(CROP_MARGIN_PX);
    let y0 = min_y.saturating_sub(CROP_MARGIN_PX);
    let x1 = (max_x + CROP_MARGIN_PX).min(w - 1);
    let y1 = (max_y + CROP_MARGIN_PX).min(h - 1);
    img.crop_imm(x0, y0, x1 - x0 + 1, y1 - y0 + 1)
}

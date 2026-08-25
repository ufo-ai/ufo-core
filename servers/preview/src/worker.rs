use std::{collections::HashMap, path::Path};

use image::DynamicImage;
use pdfium_render::prelude::*;

const CROP_MARGIN_PX: u32 = 6;
const CONTENT_CHANNEL_MAX: u8 = 244;
const CONTENT_ALPHA_MIN: u8 = 8;
const BACKGROUND_CHANNEL_TOLERANCE: u8 = 8;
const PAGE_CONTENT_MARGIN_DIVISOR: u32 = 30;

/// The crop the worker applies after rasterizing one PDF page.
#[derive(Clone, Copy)]
pub enum CropMode {
    None,
    Content,
    PageMargins,
}

impl CropMode {
    /// Parse the worker command's crop argument.
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "none" => Some(Self::None),
            "content" => Some(Self::Content),
            "page-margins" => Some(Self::PageMargins),
            _ => None,
        }
    }

    /// Encode the crop as the worker command's argument.
    pub fn argument(self) -> &'static str {
        match self {
            Self::None => "none",
            Self::Content => "content",
            Self::PageMargins => "page-margins",
        }
    }
}

pub struct Request<'a> {
    pub lib: &'a str,
    pub pdf: &'a str,
    pub outdir: &'a str,
    pub max_width: &'a str,
    pub max_height: &'a str,
    pub start_page: &'a str,
    pub pages: &'a str,
    pub crop: CropMode,
}

/// The rasterizing child: everything pdfium touches runs here, in a process the parent can kill.
/// Prints one metadata JSON line on success and exits 1 on failure.
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
        img = match request.crop {
            CropMode::None => img,
            CropMode::Content => crop_to_content(img),
            CropMode::PageMargins => crop_page_margins(img),
        };
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

fn crop_page_margins(img: DynamicImage) -> DynamicImage {
    let rgba = img.to_rgba8();
    let (w, h) = (rgba.width(), rgba.height());
    if w <= 2 || h <= 2 {
        return img;
    }
    let mut colors = HashMap::new();
    for pixel in rgba.pixels() {
        *colors.entry(pixel.0).or_insert(0usize) += 1;
    }
    let background = colors
        .into_iter()
        .max_by_key(|(_, count)| *count)
        .map(|(color, _)| color)
        .unwrap();
    let (mut min_x, mut min_y, mut max_x) = (w, h, 0);
    for y in 1..h - 1 {
        for x in 1..w - 1 {
            if rgba
                .get_pixel(x, y)
                .0
                .iter()
                .zip(background)
                .any(|(channel, background)| {
                    channel.abs_diff(background) > BACKGROUND_CHANNEL_TOLERANCE
                })
            {
                min_x = min_x.min(x);
                min_y = min_y.min(y);
                max_x = max_x.max(x);
            }
        }
    }
    if min_x == w || min_y == h {
        return img;
    }
    let margin = (w / PAGE_CONTENT_MARGIN_DIVISOR).max(1);
    let x0 = min_x.saturating_sub(margin).min((w - 1) / 2);
    let x1 = (max_x + margin).min(w - 1);
    let y0 = min_y.saturating_sub(margin).min((h - 1) / 2);
    img.crop_imm(x0, y0, x1 - x0 + 1, h - 2 * y0)
}

#[cfg(test)]
mod tests {
    use image::{DynamicImage, GenericImageView, Rgba, RgbaImage};

    #[test]
    fn page_margin_crop_keeps_equal_horizontal_insets() {
        let mut image = RgbaImage::from_pixel(100, 160, Rgba([38, 41, 41, 255]));
        image.put_pixel(10, 8, Rgba([245, 245, 245, 255]));
        image.put_pixel(90, 100, Rgba([245, 245, 245, 255]));
        let cropped = super::crop_page_margins(DynamicImage::ImageRgba8(image));
        assert_eq!(cropped.dimensions(), (87, 150));
        assert_eq!(cropped.get_pixel(3, 3), Rgba([245, 245, 245, 255]));
        assert_eq!(cropped.get_pixel(83, 95), Rgba([245, 245, 245, 255]));
    }

    #[test]
    fn page_margin_crop_leaves_a_blank_page_whole() {
        let image =
            DynamicImage::ImageRgba8(RgbaImage::from_pixel(100, 160, Rgba([38, 41, 41, 255])));
        assert_eq!(super::crop_page_margins(image).dimensions(), (100, 160));
    }

    #[test]
    fn page_margin_crop_ignores_page_edge_antialiasing() {
        let mut image = RgbaImage::from_pixel(100, 160, Rgba([38, 41, 41, 255]));
        for x in 0..100 {
            image.put_pixel(x, 0, Rgba([63, 66, 66, 255]));
            image.put_pixel(x, 159, Rgba([63, 66, 66, 255]));
        }
        for y in 0..160 {
            image.put_pixel(0, y, Rgba([63, 66, 66, 255]));
            image.put_pixel(99, y, Rgba([63, 66, 66, 255]));
        }
        image.put_pixel(10, 8, Rgba([245, 245, 245, 255]));
        image.put_pixel(90, 100, Rgba([245, 245, 245, 255]));
        let cropped = super::crop_page_margins(DynamicImage::ImageRgba8(image));
        assert_eq!(cropped.dimensions(), (87, 150));
        assert_eq!(cropped.get_pixel(3, 3), Rgba([245, 245, 245, 255]));
    }
}

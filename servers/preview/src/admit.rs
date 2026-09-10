const HEAD_SNIFF_BYTES: usize = 8192;

/// The document kinds the service renders; the declared kind is cross-checked against the
/// container's magic bytes, never trusted alone.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    Pdf,
    Docx,
    Xlsx,
    Pptx,
    Csv,
    Md,
    Svg,
    Mp4,
    Mov,
    Webm,
    Mkv,
    Image,
}

impl Kind {
    pub fn parse(s: &str) -> Option<Kind> {
        match s {
            "png" | "jpeg" | "jpg" | "webp" => Some(Kind::Image),
            "pdf" => Some(Kind::Pdf),
            "docx" => Some(Kind::Docx),
            "xlsx" => Some(Kind::Xlsx),
            "pptx" => Some(Kind::Pptx),
            "csv" => Some(Kind::Csv),
            "md" => Some(Kind::Md),
            "svg" => Some(Kind::Svg),
            "mp4" => Some(Kind::Mp4),
            "mov" => Some(Kind::Mov),
            "webm" => Some(Kind::Webm),
            "mkv" => Some(Kind::Mkv),
            _ => None,
        }
    }

    pub fn check_magic(&self, head: &[u8]) -> bool {
        let head = &head[..head.len().min(HEAD_SNIFF_BYTES)];
        match self {
            Kind::Pdf => head.starts_with(b"%PDF-"),
            Kind::Docx | Kind::Xlsx | Kind::Pptx => head.starts_with(b"PK\x03\x04"),
            Kind::Csv | Kind::Md | Kind::Svg => !head.contains(&0),
            Kind::Mp4 | Kind::Mov => {
                head.len() >= 12 && matches!(&head[4..8], b"ftyp" | b"moov" | b"mdat")
            }
            Kind::Webm | Kind::Mkv => head.starts_with(&[0x1A, 0x45, 0xDF, 0xA3]),
            Kind::Image => {
                head.starts_with(b"\x89PNG\r\n\x1a\n")
                    || (head.len() >= 3 && head[0] == 0xFF && head[1] == 0xD8 && head[2] == 0xFF)
                    || (head.starts_with(b"RIFF") && head.len() >= 12 && &head[8..12] == b"WEBP")
            }
        }
    }

    pub fn extension(&self) -> &'static str {
        match self {
            Kind::Pdf => "pdf",
            Kind::Docx => "docx",
            Kind::Xlsx => "xlsx",
            Kind::Pptx => "pptx",
            Kind::Csv => "csv",
            Kind::Md => "md",
            Kind::Svg => "svg",
            Kind::Mp4 => "mp4",
            Kind::Mov => "mov",
            Kind::Webm => "webm",
            Kind::Mkv => "mkv",
            Kind::Image => "png",
        }
    }

    pub fn is_video(&self) -> bool {
        matches!(self, Kind::Mp4 | Kind::Mov | Kind::Webm | Kind::Mkv)
    }

    pub fn is_image(&self) -> bool {
        *self == Kind::Image
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_the_rfc_kind_set_and_nothing_else() {
        for k in ["pdf", "docx", "xlsx", "pptx", "csv", "md", "svg"] {
            assert!(Kind::parse(k).is_some(), "{k}");
        }
        assert!(Kind::parse("exe").is_none());
        assert!(Kind::parse("PDF").is_none());
    }

    #[test]
    fn pdf_magic() {
        assert!(Kind::Pdf.check_magic(b"%PDF-1.7 rest"));
        assert!(!Kind::Pdf.check_magic(b"PK\x03\x04zip"));
    }

    #[test]
    fn ooxml_kinds_require_zip_magic() {
        for k in [Kind::Docx, Kind::Xlsx, Kind::Pptx] {
            assert!(k.check_magic(b"PK\x03\x04rest"));
            assert!(!k.check_magic(b"%PDF-1.7"));
        }
    }

    #[test]
    fn text_kinds_refuse_binary() {
        assert!(Kind::Csv.check_magic(b"a,b,c\n1,2,3\n"));
        assert!(Kind::Md.check_magic("# heading\n".as_bytes()));
        assert!(Kind::Svg.check_magic(b"<svg xmlns='x'/>"));
        assert!(!Kind::Md.check_magic(b"text with \x00 nul"));
    }

    #[test]
    fn video_kinds_parse() {
        for k in ["mp4", "mov", "webm", "mkv"] {
            assert!(Kind::parse(k).is_some(), "{k}");
        }
        assert!(Kind::parse("avi").is_none());
    }

    #[test]
    fn mp4_and_mov_require_an_isobmff_box_type() {
        let mut ftyp = vec![0u8, 0, 0, 0x18];
        ftyp.extend_from_slice(b"ftypmp42");
        for k in [Kind::Mp4, Kind::Mov] {
            assert!(k.check_magic(&ftyp));
            assert!(k.check_magic(b"\0\0\0\x10moov rest ok"));
            assert!(k.check_magic(b"\0\0\0\x10mdat rest ok"));
            assert!(!k.check_magic(b"PK\x03\x04 not video"));
            assert!(!k.check_magic(b"short"));
        }
    }

    #[test]
    fn webm_and_mkv_require_the_ebml_magic() {
        let ebml = [0x1A, 0x45, 0xDF, 0xA3, 0x01, 0x02, 0x03];
        for k in [Kind::Webm, Kind::Mkv] {
            assert!(k.check_magic(&ebml));
            assert!(!k.check_magic(b"\0\0\0\x18ftypmp42"));
        }
    }

    #[test]
    fn image_kinds_parse_and_sniff_their_containers() {
        for k in ["png", "jpeg", "jpg", "webp"] {
            assert!(Kind::parse(k) == Some(Kind::Image), "{k}");
        }
        assert!(Kind::parse("gif").is_none());
        assert!(Kind::parse("tiff").is_none());
        assert!(Kind::Image.check_magic(b"\x89PNG\r\n\x1a\nrest"));
        assert!(Kind::Image.check_magic(b"\xff\xd8\xff\xe0jpeg"));
        let webp = [
            b"RIFF".as_slice(),
            4u32.to_le_bytes().as_slice(),
            b"WEBP".as_slice(),
        ]
        .concat();
        assert!(Kind::Image.check_magic(&webp));
        assert!(!Kind::Image.check_magic(b"plain text"));
    }

    #[test]
    fn is_video_partitions_the_kind_set() {
        for k in [Kind::Mp4, Kind::Mov, Kind::Webm, Kind::Mkv] {
            assert!(k.is_video(), "{k:?}");
        }
        for k in [Kind::Pdf, Kind::Docx, Kind::Csv, Kind::Md, Kind::Svg] {
            assert!(!k.is_video(), "{k:?}");
        }
    }
}

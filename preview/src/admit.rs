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
}

impl Kind {
    pub fn parse(s: &str) -> Option<Kind> {
        match s {
            "pdf" => Some(Kind::Pdf),
            "docx" => Some(Kind::Docx),
            "xlsx" => Some(Kind::Xlsx),
            "pptx" => Some(Kind::Pptx),
            "csv" => Some(Kind::Csv),
            "md" => Some(Kind::Md),
            "svg" => Some(Kind::Svg),
            _ => None,
        }
    }

    pub fn check_magic(&self, head: &[u8]) -> bool {
        let head = &head[..head.len().min(HEAD_SNIFF_BYTES)];
        match self {
            Kind::Pdf => head.starts_with(b"%PDF-"),
            Kind::Docx | Kind::Xlsx | Kind::Pptx => head.starts_with(b"PK\x03\x04"),
            Kind::Csv | Kind::Md | Kind::Svg => !head.contains(&0),
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
        }
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
}

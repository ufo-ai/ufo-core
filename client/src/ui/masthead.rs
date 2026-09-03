//! The mark a conversation opens under: the brand's logo, drawn, with this build's version.

use ratatui::text::{Line, Span};

use crate::ui::theme::Theme;
use crate::ui::wrap;

/// The logo drawn in characters at five rows. The three discs stand on the centres the drawing
/// gives them and the letters keep its widths, measured off `ufo-logo.svg`; the strokes are ASCII,
/// which holds a curve at this size where a block glyph reads as a smudge, and which every
/// terminal paints one column wide — an East Asian Ambiguous glyph doubles on some terminals and
/// not others, and the rows would shear out of the alignment that is the drawing.
const MARK: [&str; 5] = [
    " (o)   (o)     |       |  |=====  ,---.",
    "               |       |  |      /     \\",
    "               |       |  |===  (       )",
    "    (o)        |       |  |      \\     /",
    "                \\_____/   |       `---'",
];

/// Where the version stands: the row of the mark it reads against, and the columns the drawing is
/// padded out to before it. Every row is painted behind a leading space, so the version itself
/// starts one column further right than that.
const VERSION_ROW: usize = 4;
const ART_COLUMNS: usize = 43;

/// The columns the mark is painted in: its widest row, behind the leading space every row carries.
fn drawn_width() -> usize {
    MARK.iter()
        .map(|art| wrap::width(art) + 1)
        .max()
        .expect("the mark draws rows")
}

/// The mark and this build's version, for the head of the transcript. A part with no room is left
/// off whole rather than cut, because the drawing is fixed-width and the frame diff keeps whatever
/// an over-wide row wrapped into the row below: a terminal too narrow for the version draws the
/// mark alone, and one too narrow for the mark opens the conversation bare. Half a version number
/// states the wrong build, and half a drawing is a different drawing.
pub fn masthead(theme: &Theme, width: u16) -> Vec<Line<'static>> {
    if (width as usize) < drawn_width() {
        return Vec::new();
    }
    let version = format!("v{}", env!("CARGO_PKG_VERSION"));
    let room = (width as usize).saturating_sub(ART_COLUMNS + 1);
    MARK.iter()
        .enumerate()
        .map(|(row, art)| {
            let mut spans = vec![Span::styled(format!(" {art}"), theme.text)];
            if row == VERSION_ROW && room >= wrap::width(&version) {
                let gutter = ART_COLUMNS.saturating_sub(wrap::width(art));
                spans.push(Span::styled(" ".repeat(gutter), theme.text));
                spans.push(Span::styled(version.clone(), theme.muted));
            }
            Line::from(spans)
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ui::theme::{ColorMode, Scheme};

    /// The drawing's own geometry: the cell each disc starts in, and the column the wordmark
    /// stands from.
    const DISCS: [(usize, usize); 3] = [(0, 1), (0, 7), (3, 4)];
    const DISC: &str = "(o)";
    const WORDMARK_COLUMN: usize = 15;
    const STEM: char = '|';
    /// The narrowest terminal the app paints in: below this it takes 80 columns instead.
    const NARROWEST: u16 = 20;

    fn theme() -> Theme {
        Theme::for_mode(ColorMode::Plain, Scheme::Dark)
    }

    fn painted(width: u16) -> Vec<String> {
        masthead(&theme(), width)
            .iter()
            .map(|line| line.to_string().trim_end().to_string())
            .collect()
    }

    fn version() -> String {
        format!("v{}", env!("CARGO_PKG_VERSION"))
    }

    #[test]
    fn the_mark_draws_three_discs_beside_the_wordmark() {
        for (row, col) in DISCS {
            let drawn: String = MARK[row].chars().skip(col).take(DISC.len()).collect();
            assert_eq!(drawn, DISC, "a disc stands at row {row}, column {col}");
        }
        assert_eq!(
            MARK.concat().matches(DISC).count(),
            DISCS.len(),
            "the mark draws the drawing's three discs and no more"
        );
        for (row, art) in MARK.iter().enumerate() {
            let letters: String = art.chars().skip(WORDMARK_COLUMN).collect();
            assert!(
                letters.contains(STEM),
                "the wordmark stands on every row: {row}"
            );
            assert!(
                wrap::width(art) < ART_COLUMNS,
                "the mark stays clear of the version: {art}"
            );
        }
    }

    /// The rows only mean anything stacked, so every one of them has to take the same columns on
    /// every terminal: a glyph a double-width terminal widens breaks the drawing apart, and
    /// `wrap::width` would keep reporting the narrow number the gutter is measured with.
    #[test]
    fn the_mark_is_drawn_in_glyphs_no_terminal_widens() {
        for (row, art) in MARK.iter().enumerate() {
            assert!(
                art.is_ascii(),
                "row {row} draws a glyph a terminal may widen: {art}"
            );
            assert_eq!(
                wrap::width(art),
                art.chars().count(),
                "row {row} measures one column a character: {art}"
            );
        }
    }

    #[test]
    fn the_version_reads_against_the_wordmark() {
        let painted = painted(80);
        assert_eq!(painted.len(), MARK.len());
        assert!(painted[VERSION_ROW].ends_with(&version()), "{painted:?}");
        assert_eq!(painted[0], format!(" {}", MARK[0]));
    }

    /// The gutter is measured off the art alone, so the leading space carries the version one
    /// column past the columns the drawing is given.
    #[test]
    fn the_version_stands_one_column_past_the_drawing() {
        let row = painted(80).swap_remove(VERSION_ROW);
        let at = row.find(&version()).expect("the version paints");
        assert_eq!(wrap::width(&row[..at]), ART_COLUMNS + 1, "{row}");
    }

    #[test]
    fn a_terminal_with_no_room_states_the_mark_alone() {
        let fits = ART_COLUMNS + 1 + wrap::width(&version());
        for width in [drawn_width(), fits - 1] {
            assert_eq!(
                painted(width as u16),
                MARK.map(|art| format!(" {art}")).to_vec(),
                "a version with no room is left off whole, not cut: {width}"
            );
        }
        assert!(painted(fits as u16)[VERSION_ROW].ends_with(env!("CARGO_PKG_VERSION")));
    }

    /// Below the drawing's own width there is nothing to draw: an over-wide row wraps into the row
    /// under it and the frame diff keeps the wreckage, so the conversation opens bare instead.
    #[test]
    fn a_terminal_too_narrow_for_the_mark_opens_bare() {
        for width in [NARROWEST, drawn_width() as u16 - 1] {
            assert!(
                painted(width).is_empty(),
                "the mark is drawn broken at {width}: {:?}",
                painted(width)
            );
        }
        let drawn = painted(drawn_width() as u16);
        assert_eq!(drawn.len(), MARK.len());
        for row in &drawn {
            assert!(
                wrap::width(row) <= drawn_width(),
                "every row fits the width it is drawn at: {row}"
            );
        }
    }
}

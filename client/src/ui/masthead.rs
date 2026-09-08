use ratatui::text::{Line, Span};

use crate::ui::theme::Theme;
use crate::ui::wrap;

const MARK: [&str; 8] = [
    "▗▟█▙▖     ▗▟█▙▖         ▐██▘     ▐██▌  ▐███████▘    ▄▟██████▙▄",
    "█▓███     ███▓█         ▝██       ██   ▐██        ▄█▛▀     ▝▜██▄",
    "▝▀█▀▘     ▝▀█▀▘          ██       ██   ▐██       ▟█▛         ▝██▌",
    "                         ██       ██   ▐██▄▄▄▄▄  ██▘          ▐██",
    "                         ██       ██   ▐██▀▀▀▀▀  ██▌          ▐██",
    "     ▗▟█▙▖               ██       ██   ▐██       ▜██▖         ▟█▌",
    "     ▓████               ▜█▙▖   ▄▟██   ▐██        ▀██▄▖     ▄██▀",
    "     ▝▀█▀▘                ▀█████▛▀██▌  ▐██▖         ▀▜██████▛▀",
];

const VERSION_ROW: usize = 7;
const ART_COLUMNS: usize = 67;
const MARGIN: usize = 2;

fn drawn_width() -> usize {
    MARK.iter()
        .map(|art| wrap::width(art) + 1)
        .max()
        .expect("the mark draws rows")
}

/// A part with no room is left off whole rather than cut: the drawing is fixed-width and the frame diff
/// keeps whatever an over-wide row wrapped into the row below.
pub fn masthead(theme: &Theme, width: u16) -> Vec<Line<'static>> {
    if (width as usize) < drawn_width() {
        return Vec::new();
    }
    let version = format!("v{}", env!("CARGO_PKG_VERSION"));
    let room = (width as usize).saturating_sub(ART_COLUMNS + 1);
    let air = std::iter::repeat_n(Line::raw(""), MARGIN);
    air.clone()
        .chain(MARK.iter().enumerate().map(|(row, art)| {
            let mut spans = vec![Span::styled(format!(" {art}"), theme.text)];
            if row == VERSION_ROW && room >= wrap::width(&version) {
                let gutter = ART_COLUMNS.saturating_sub(wrap::width(art));
                spans.push(Span::styled(" ".repeat(gutter), theme.text));
                spans.push(Span::styled(version.clone(), theme.muted));
            }
            Line::from(spans)
        }))
        .chain(air)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ui::theme::{ColorMode, Scheme};

    const DISCS: [(usize, usize); 3] = [(0, 0), (0, 10), (5, 5)];
    const DISC_COLS: usize = 5;
    const DISC_ROWS: usize = 3;
    const HATCH: char = '▓';
    const WORDMARK_COLUMN: usize = 24;
    const STEM: char = '█';
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
            let disc: String = (row..row + DISC_ROWS)
                .map(|at| {
                    MARK[at]
                        .chars()
                        .skip(col)
                        .take(DISC_COLS)
                        .collect::<String>()
                })
                .collect();
            assert!(
                disc.contains(STEM) && disc.contains(HATCH),
                "a disc stands inked and hatched at row {row}, column {col}: {disc}"
            );
        }
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

    /// Every row has to take the same columns: a glyph wider than one column shears the drawing apart, and
    /// `wrap::width` would keep reporting the narrow number the gutter is measured with.
    #[test]
    fn the_mark_is_drawn_in_glyphs_no_terminal_widens() {
        for (row, art) in MARK.iter().enumerate() {
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
        assert_eq!(painted.len(), MARK.len() + 2 * MARGIN);
        assert!(
            painted[MARGIN + VERSION_ROW].ends_with(&version()),
            "{painted:?}"
        );
        assert_eq!(painted[MARGIN], format!(" {}", MARK[0]));
    }

    #[test]
    fn the_drawing_stands_in_its_own_air() {
        let painted = painted(80);
        for at in 0..MARGIN {
            assert!(painted[at].is_empty(), "the air above: {painted:?}");
            let below = painted.len() - 1 - at;
            assert!(painted[below].is_empty(), "the air below: {painted:?}");
        }
    }

    #[test]
    fn the_version_stands_one_column_past_the_drawing() {
        let row = painted(80).swap_remove(MARGIN + VERSION_ROW);
        let at = row.find(&version()).expect("the version paints");
        assert_eq!(wrap::width(&row[..at]), ART_COLUMNS + 1, "{row}");
    }

    #[test]
    fn a_terminal_with_no_room_states_the_mark_alone() {
        let fits = ART_COLUMNS + 1 + wrap::width(&version());
        for width in [drawn_width(), fits - 1] {
            let air = vec![String::new(); MARGIN];
            let drawn: Vec<String> = air
                .iter()
                .cloned()
                .chain(MARK.map(|art| format!(" {art}")))
                .chain(air.iter().cloned())
                .collect();
            assert_eq!(
                painted(width as u16),
                drawn,
                "a version with no room is left off whole, not cut: {width}"
            );
        }
        assert!(painted(fits as u16)[MARGIN + VERSION_ROW].ends_with(env!("CARGO_PKG_VERSION")));
    }

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
        assert_eq!(drawn.len(), MARK.len() + 2 * MARGIN);
        for row in &drawn {
            assert!(
                wrap::width(row) <= drawn_width(),
                "every row fits the width it is drawn at: {row}"
            );
        }
    }
}

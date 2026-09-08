use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

pub fn width(text: &str) -> usize {
    UnicodeWidthStr::width(text)
}

pub fn units(text: &str) -> Vec<(&str, usize)> {
    let mut out = Vec::new();
    let mut at = 0;
    while at < text.len() {
        let rest = &text[at..];
        if rest.starts_with("\x1b]") {
            let bel = rest.find('\x07').map(|end| end + 1);
            let st = rest.find("\x1b\\").map(|end| end + 2);
            let end = bel.into_iter().chain(st).min().unwrap_or(rest.len());
            out.push((&rest[..end], 0));
            at += end;
            continue;
        }
        let end = rest.chars().next().expect("rest is non-empty").len_utf8();
        out.push((&rest[..end], width(&rest[..end]).max(1)));
        at += end;
    }
    out
}

pub fn clip(text: &str, max: usize) -> &str {
    let mut used = 0;
    for (index, ch) in text.char_indices() {
        let w = UnicodeWidthChar::width(ch).unwrap_or(0);
        if used + w > max {
            return &text[..index];
        }
        used += w;
    }
    text
}

pub fn wrap_head(text: &str, cap: usize) -> (usize, usize) {
    let mut taken = clip(text, cap);
    if taken.is_empty() && !text.is_empty() {
        let first = text.chars().next().unwrap();
        taken = &text[..first.len_utf8()];
    }
    if taken.len() == text.len() {
        return (taken.len(), taken.len());
    }
    let mut head_end = taken.len();
    if !text[head_end..].starts_with(' ') {
        if let Some(space) = taken.rfind(' ') {
            if space > 0 {
                head_end = space;
            }
        }
    }
    while head_end > 0 && text[..head_end].ends_with(' ') {
        head_end -= 1;
    }
    if head_end == 0 {
        head_end = taken.len();
    }
    let mut rest = head_end;
    while text[rest..].starts_with(' ') {
        rest += 1;
    }
    (head_end, rest)
}

pub fn hard_rows(text: &str, cap1: usize, cap: usize) -> Vec<(usize, usize)> {
    let mut rows = Vec::new();
    let mut start = 0;
    let mut used = 0;
    let mut row_cap = cap1.max(1);
    for (index, ch) in text.char_indices() {
        if ch == '\n' {
            rows.push((start, index));
            start = index + 1;
            used = 0;
            row_cap = cap.max(1);
            continue;
        }
        let w = UnicodeWidthChar::width(ch).unwrap_or(0);
        if used + w > row_cap && used > 0 {
            rows.push((start, index));
            start = index;
            used = 0;
            row_cap = cap.max(1);
        }
        used += w;
    }
    rows.push((start, text.len()));
    rows
}

pub fn cursor_pos(text: &str, rows: &[(usize, usize)], cursor: usize) -> (usize, usize) {
    let mut at = 0;
    for (index, row) in rows.iter().enumerate() {
        if row.0 <= cursor {
            at = index;
        } else {
            break;
        }
    }
    let (start, end) = rows[at];
    (at, width(&text[start..cursor.clamp(start, end)]))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn units_hold_an_osc_escape_as_one_zero_width_unit() {
        let text = "a\x1b]8;;https://u.fo\x07b\x1b]8;;\x07c";
        assert_eq!(
            units(text),
            vec![
                ("a", 1),
                ("\x1b]8;;https://u.fo\x07", 0),
                ("b", 1),
                ("\x1b]8;;\x07", 0),
                ("c", 1),
            ]
        );
    }

    #[test]
    fn units_take_an_st_terminator_and_an_unterminated_tail() {
        assert_eq!(
            units("\x1b]8;;x\x1b\\y"),
            vec![("\x1b]8;;x\x1b\\", 0), ("y", 1)]
        );
        assert_eq!(units("\x1b]8;;x"), vec![("\x1b]8;;x", 0)]);
        assert_eq!(units("日"), vec![("日", 2)]);
    }

    #[test]
    fn clip_is_char_safe() {
        assert_eq!(clip("héllo", 3), "hél");
        assert_eq!(clip("日本語", 4), "日本");
        assert_eq!(clip("abc", 10), "abc");
    }

    #[test]
    fn wrap_head_breaks_on_last_space() {
        let text = "one two three four";
        let (head, rest) = wrap_head(text, 12);
        assert_eq!(&text[..head], "one two");
        assert_eq!(&text[rest..], "three four");
    }

    #[test]
    fn wrap_head_hard_cuts_without_space() {
        let text = "abcdefghij";
        let (head, rest) = wrap_head(text, 4);
        assert_eq!(&text[..head], "abcd");
        assert_eq!(rest, 4);
    }

    #[test]
    fn wrap_head_keeps_leading_space_take() {
        let text = " x tail";
        let (head, rest) = wrap_head(text, 2);
        assert_eq!(&text[..head], " x");
        assert_eq!(&text[rest..], "tail");
    }

    #[test]
    fn wrap_head_progresses_on_tiny_cap() {
        let (head, rest) = wrap_head("日x", 1);
        assert_eq!(head, "日".len());
        assert_eq!(rest, head);
    }

    #[test]
    fn wrap_head_never_breaks_fitting_text() {
        let text = "# Title";
        assert_eq!(wrap_head(text, 20), (text.len(), text.len()));
        assert_eq!(wrap_head(text, 7), (text.len(), text.len()));
    }

    #[test]
    fn wrap_head_drops_a_space_the_window_lands_on() {
        let text = "ab cd";
        let (head, rest) = wrap_head(text, 3);
        assert_eq!(&text[..head], "ab");
        assert_eq!(&text[rest..], "cd");
    }

    #[test]
    fn wrap_head_skips_every_space_at_the_break() {
        let text = "a  b";
        let (head, rest) = wrap_head(text, 3);
        assert_eq!(&text[..head], "a");
        assert_eq!(&text[rest..], "b");
    }

    #[test]
    fn hard_rows_empty_is_one_row() {
        assert_eq!(hard_rows("", 10, 10), vec![(0, 0)]);
    }

    #[test]
    fn hard_rows_wraps_first_row_narrower() {
        let rows = hard_rows("abcdefgh", 3, 5);
        assert_eq!(rows, vec![(0, 3), (3, 8)]);
    }

    #[test]
    fn hard_rows_splits_on_newlines() {
        let rows = hard_rows("ab\ncd\n", 10, 10);
        assert_eq!(rows, vec![(0, 2), (3, 5), (6, 6)]);
    }

    #[test]
    fn hard_rows_counts_wide_chars() {
        let rows = hard_rows("日本語", 4, 4);
        assert_eq!(rows, vec![(0, 6), (6, 9)]);
    }

    #[test]
    fn cursor_lands_after_wrap_boundary() {
        let text = "abcdef";
        let rows = hard_rows(text, 3, 3);
        assert_eq!(cursor_pos(text, &rows, 3), (1, 0));
        assert_eq!(cursor_pos(text, &rows, 2), (0, 2));
        assert_eq!(cursor_pos(text, &rows, 6), (1, 3));
    }

    #[test]
    fn cursor_after_trailing_newline_gets_empty_row() {
        let text = "ab\n";
        let rows = hard_rows(text, 10, 10);
        assert_eq!(cursor_pos(text, &rows, 3), (1, 0));
    }
}

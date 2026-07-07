---
name: office-xlsx
description: Load when creating, editing, or analyzing Excel spreadsheets (.xlsx) — cells, formulas, recalculation, and tabular data.
---
# Required reading

**Read `references/xlsx-repl-workflow.md` before your first `xlsx_repl` call.** It covers the open → read → write+save → recalc → verify patterns and the recalc subprocess invocation. The schema alone won't tell you any of this; skipping the read produces split-call anti-patterns and missed recalcs. **Target: 2–3 calls per task.**

# Gotchas

1. **Don't cite values without sheet + address** — "Revenue!B5 = $1,234", not just "$1,234".
2. **Don't compute in Python; use formulas, not hardcodes** — derived values come from spreadsheet formulas (e.g. `=SUM(F2:F19)`, `=(B5-C5)/B5`), not Python snapshots. Don't forget `number_format` on formula cells either — they display raw precision otherwise.
3. **Don't ship formula errors** — after recalc, check the script's error report. Any `#REF!`, `#DIV/0!`, `#VALUE!`, `#N/A`, or `#NAME?` fails the deliverable.
4. **Don't edit beyond what's asked** — modify only the cells/sheets the user named (or strictly required to produce them). Match existing format, style, and formula conventions exactly. Don't add rows/values the user didn't request and don't touch unrelated cells or sheets.
5. **Don't tidy what wasn't asked** — don't reformat, restyle, or rewrite existing formulas/cells just because they look improvable. "Helpful tidying" is silent damage from the user's perspective.

# Primary Tool: `xlsx_repl`

Persistent Python REPL with `openpyxl`, for sandbox-side spreadsheet work. Variables persist across calls. Call the `xlsx_repl` tool directly — pass the Python source as the `code` field.

# Output Requirements

## Professional Font

Use Calibri or Arial for all deliverables unless the user or existing template specifies otherwise.

## Number Formatting

| Data Type  | Format Code | Example   |
| ---------- | ----------- | --------- |
| Integer    | `#,##0`     | 1,234,567 |
| Decimal    | `#,##0.0`   | 1,234.6   |
| Percentage | `0.0%`      | 12.3%     |
| Currency   | `$#,##0.00` | $1,234.56 |

## Alignment

| Content    | Horizontal |
| ---------- | ---------- |
| Headers    | Center     |
| Numbers    | Right      |
| Short text | Center     |
| Long text  | Left       |
| Dates      | Center     |

## Layout

| Element         | Position                     |
| --------------- | ---------------------------- |
| Left margin     | Column A empty (width 3)     |
| Top margin      | Row 1 empty                  |
| Content start   | Cell B2                      |
| Section spacing | 1 empty row between sections |
| Table spacing   | 2 empty rows between tables  |
| Charts          | Below tables (2 rows gap)    |

## Content Completeness

| Check             | Action                                       |
| ----------------- | -------------------------------------------- |
| Missing values    | Blank or "N/A", never 0 unless actually zero |
| Units             | In header: "Revenue ($M)", "Growth (%)"      |
| Abbreviations     | Define on first use                          |
| Calculated fields | Use formulas so users can audit              |

## Reference files (read on demand)

| Need                                                                         | Reference file                        |
| ---------------------------------------------------------------------------- | ------------------------------------- |
| Multi-step `xlsx_repl` workflow (open / read / write+save / recalc / verify) | `references/xlsx-repl-workflow.md`    |
| Charts, conditional formatting, tables, images, data validation              | `references/charts-and-formatting.md` |
| Financial model conventions (color coding, number standards, assumptions)    | `references/financial-models.md`      |
| Sheet organization, text rows, sorting, comparison columns                   | `references/layout-and-structure.md`  |
| Error recovery, formula verification                                         | `references/formula-verification.md`  |

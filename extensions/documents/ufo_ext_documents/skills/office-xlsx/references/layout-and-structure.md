# Extended Layout & Structure

## Sheet Organization

| Guideline | Recommendation |
|-----------|----------------|
| Sheet order | Summary/Overview first, then supporting detail (General to Specific) |
| Sheet count | 3-5 ideal, max 7 |
| Naming | Descriptive names (e.g., "Revenue Data", not "Sheet1") |

- Overview sheet should stand alone — user understands the main message without opening other sheets
- Progressive disclosure: summary first, details for those who dig deeper
- Consistent structure across sheets: same layout patterns, same starting positions

## Standalone Text Rows

Text naturally extends into empty cells to the right, but is **clipped** if right cells contain content.

| Condition | Action |
|-----------|--------|
| Right cells guaranteed empty | No action needed |
| Right cells may have content | `ws.merge_cells("B2:H2")` |
| Text exceeds content area | `ws["B20"].alignment = Alignment(wrap_text=True)` |

Common cases requiring merge: titles, subtitles, section headers, notes, disclaimers.

## Pre-sorting

Pre-sort by most meaningful dimension:
- Rankings: by value descending
- Time series: by date ascending
- Alphabetical: when no clear priority

## Data Context

Every dataset needs context:

| Element | Location | Example |
|---------|----------|---------|
| Data source | Footer or notes | "Source: Company 10-K, FY2024" |
| Time range | Near title | "Data from Jan 2022 - Dec 2024" |
| Generation date | Footer | "Generated: 2024-01-15" |
| Definitions | Notes section | "Revenue = Net sales excluding returns" |

## Comparison Columns

| Column Type | Formula Pattern | Use Case |
|-------------|-----------------|----------|
| Change | `=B2-A2` | Absolute difference |
| % Change | `=(B2-A2)/A2` | Relative growth |
| YoY Growth | `=(CurrentYear-PriorYear)/PriorYear` | Year-over-year |
| Rank | `=RANK(B2,$B$2:$B$100,0)` | Position in list |

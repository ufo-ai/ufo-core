# Data Visualization — Colors, Charts, Design

Principles for charts, graphs, and data visualizations across all formats (web, Python, PowerPoint, documents).

---

## Chart Color Sequence

Use in order for data series (bar, pie, line, scatter). Every entry is one of the two accents, a step derived from one, or a neutral — see `$UFO_HOME/skills/design-foundations/references/color.md`:

| #   | Hex       | Name                          |
| --- | --------- | ----------------------------- |
| 1   | `#0095FF` | Blue (the accent)             |
| 2   | `#FF6700` | Orange (the second accent)    |
| 3   | `#676767` | Graphite                      |
| 4   | `#7DC7FB` | Pale blue                     |
| 5   | `#AE4600` | Umber                         |
| 6   | `#C6C4C4` | Silver                        |
| 7   | `#0069B5` | Deep blue                     |
| 8   | `#00A963` | Green                         |

**Fit chart colors to the art direction.** Data viz naturally needs multiple colors to communicate — that's fine. But choose them thoughtfully: for sequential data, use monochromatic shades of the primary accent. For categorical data that needs distinct hues, use the curated sequence above — it's designed to be harmonious. When the project has a custom palette, derive chart colors from it rather than defaulting to unrelated hues. The chart colors should feel like part of the same design system as the rest of the page.

**Rules:** ≤5 series per chart (use small multiples beyond that). Sequential data: single hue, varying lightness — pale blue `#7DC7FB` through `#0095FF` to deep blue `#0069B5`. Diverging data: orange `#FF6700` positive, blue `#0095FF` negative. Highlight key series at full opacity, dim others to 40-60% or gray them out.

A series label sitting on a filled mark is `#191A1A`. A label beside the mark on a light surface takes the text step for that hue, not the fill.

**Colorblind safety:** Never color alone — add labels/patterns/markers. Avoid red/green only. Blue+orange is safer.

---

## Chart Type Selection

| Data question        | Chart type                               | Notes                   |
| -------------------- | ---------------------------------------- | ----------------------- |
| Change over time?    | Line                                     | Continuous data, trends |
| Category comparison? | Vertical bar                             | Discrete comparisons    |
| Ranking?             | Horizontal bar                           | Easier label reading    |
| Part of whole?       | Stacked bar / treemap                    | NOT pie (rarely right)  |
| Distribution?        | Histogram / box plot                     | Spread, outliers        |
| Relationship?        | Scatter                                  | Correlation, clusters   |
| Geographic?          | Choropleth map (D3, MapLibre, or Mapbox) | Regional comparisons    |
| Flow/process?        | Sankey / funnel                          | Conversion, steps       |

**Never:** 3D charts, pie with 5+ slices, dual-axis charts.

---

## Data Viz Design Principles

1. **Data-ink ratio** — Every pixel presents data. Remove decorative gridlines, borders, backgrounds.
2. **Label directly** — Labels on/near data points, not in separate legends. Legends only when direct labeling would clutter.
3. **Color with purpose** — Encode a data dimension, never decorate.
4. **Accessible** — Never color alone. 3:1 contrast between adjacent elements. Alt text or data tables as fallback.
5. **Animate transitions** — Numbers count up, bars grow, lines draw (600-800ms). No gratuitous effects.

---

## Typography in Charts

- Body font only — never display fonts
- Axis labels: 12-14px / 10-12pt
- Titles state the insight: "Revenue grew 23% in Q4" not "Revenue Chart"
- `tabular-nums lining-nums` on all numeric values

---

## KPI Cards

- **Value:** Large, bold — dominant element
- **Label:** Small, muted
- **Delta:** Colored arrow + %. Up `#007645`, down `#D0000E`, flat `#676767` on a light surface; `#00A963`, `#FF2332` and `#A7A9A9` on a dark one
- **Sparkline (optional):** Tiny trend line, no axes
- **Animate** value on change/appear

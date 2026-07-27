---
name: data-visualization
description: Load when visualizing data — choosing chart types and producing plots that communicate distributions, trends, or comparisons.
---
# Data Visualization

Chart selection, Python patterns, and design principles. See `.skills/design-foundations/references/color.md` for color palettes and `.skills/design-foundations/SKILL.md` for foundational design rules.

## Render, Inspect, Revise

Render the chart before finalizing. After saving the figure, read the saved PNG back. Look closely at the edges where text usually collides — title/subtitle stacking, annotation boxes overlapping each other, footer notes touching x-axis tick labels — and at clipping, spacing, missing content, and visual consistency. Revise until the rendered output is clean.

## Chart Selection

| What you're showing          | Best chart      | Alternatives                        |
| ---------------------------- | --------------- | ----------------------------------- |
| Trend over time              | Line            | Area (cumulative/composition)       |
| Comparison across categories | Vertical bar    | Horizontal bar (many categories)    |
| Ranking                      | Horizontal bar  | Dot plot, slope chart (two periods) |
| Part-to-whole                | Stacked bar     | Treemap (hierarchical)              |
| Composition over time        | Stacked area    | 100% stacked bar (proportion focus) |
| Distribution                 | Histogram       | Box plot (group comparison), violin |
| Correlation (2 vars)         | Scatter         | Bubble (3rd var as size)            |
| Correlation (many vars)      | Heatmap         | Pair plot                           |
| Multiple KPIs                | Small multiples | Dashboard with separate charts      |

**Avoid:** Pie charts (humans compare angles poorly — use bar), 3D charts (distortion, zero information gain), dual-axis (implies false correlation).

## Python Setup

```python
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import seaborn as sns

plt.style.use('seaborn-v0_8-whitegrid')
plt.rcParams.update({
    'figure.figsize': (10, 6), 'figure.dpi': 150,
    'font.size': 11, 'axes.titlesize': 14, 'axes.titleweight': 'bold',
})

PALETTE_CATEGORICAL = ['#F59E0B', '#3F3F3F', '#A3A3A3', '#92400E', '#FDE68A', '#006494', '#944454', '#6E6E6E']
```

## Number Formatting

```python
def format_number(val, fmt='number'):
    prefix = '$' if fmt == 'currency' else ''
    if fmt == 'percent': return f'{val:.1f}%'
    if abs(val) >= 1e9: return f'{prefix}{val/1e9:.1f}B'
    if abs(val) >= 1e6: return f'{prefix}{val/1e6:.1f}M'
    if abs(val) >= 1e3: return f'{prefix}{val/1e3:.1f}K'
    return f'{prefix}{val:,.0f}'

ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, p: format_number(x, 'currency')))
```

## Design Principles

- **Highlight the story**: Bright accent for the key insight; grey everything else.
- **Titles state insights**: "Revenue grew 23% YoY" not "Revenue by Month." Subtitle adds date range and source.
- **Sort by value**, not alphabetically, unless natural order exists (months, funnel stages).
- **Aspect ratio**: Time series wider than tall (3:1 to 2:1); comparisons squarer.
- **Bar charts start at zero.** Line charts can use non-zero baselines when range matters.
- **Consistent scales across panels** when comparing multiple charts.

## Accessibility

- Use `sns.color_palette("colorblind")` as a colorblind-safe alternative.
- Add pattern fills (`hatch` in matplotlib) or distinct line styles alongside color.
- Include alt text describing the key finding; provide data table alternative.
- Test: does the chart work in B&W? Text readable at standard zoom?

## Gotchas

- **Truncated y-axis exaggerates differences** — A bar chart starting at 95 instead of 0 makes a 2% difference look like a 10x gap. Always start bar charts at zero.
- **Sequential palettes hide categorical data** — Using a gradient (light-to-dark) for unordered categories implies a ranking that doesn't exist. Use distinct hues for categorical, sequential for ordered.
- **Legend order != data order** — Matplotlib legend order matches plot call order, not the visual stack order in area/stacked charts. Reverse legend order or label directly on the chart.
- **savefig cuts off labels** — Default `plt.savefig()` clips titles and axis labels. Always use `bbox_inches='tight'`.
- **Title and subtitle collide** — Don't hand-position a subtitle with `fig.text(y=...)` near a `fig.suptitle(y=...)` — fontsize math is fragile and the inspect step rarely catches the collision. Use `ax.set_title("Headline\nsubtitle", loc='left')` (matplotlib spaces `\n`-separated titles automatically), or `plt.subplots(layout='constrained')` with `fig.suptitle` and a separate small subtitle `Text` so spacing is computed for you.
- **Footer source/notes text** — Long source/note strings passed directly to `fig.text(...)` widen the entire figure (matplotlib expands to fit; `wrap=True` is unreliable with `bbox_inches='tight'`). Always wrap to a fixed width first:
  ```python
  import textwrap
  note = textwrap.fill("your long source string", 100)
  fig.text(0.01, -0.02, note, ha='left', va='top', fontsize=8)  # negative y; bbox_inches='tight' pads it in
  ```
- **Seaborn mutates global state** — `sns.set_theme()` changes `rcParams` globally. Reset with `plt.rcdefaults()` after use or scope changes with `plt.rc_context()`.

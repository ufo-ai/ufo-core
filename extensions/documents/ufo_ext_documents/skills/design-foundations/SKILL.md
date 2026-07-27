---
name: design-foundations
description: "Load before visual choices without a full brand system or as fallback: color, typography, and visual hierarchy across any artifact (websites, slides, charts, documents)."
---
# Design Foundations

Artifact-agnostic design guidance — works for CSS, PowerPoint, matplotlib, PDF, or any visual output.

## Core Principles

1. **Restraint** — 1 accent + neutrals. 2 fonts max, 2-3 weights. Earn every element; decoration must encode meaning.
2. **Purpose** — Every choice answers "what does this help the viewer understand?" Color encodes meaning, type size signals hierarchy, spacing groups content, animation reveals information.
3. **No decoration** — Do not add illustrations, stock images, decorative icons, or clip art unless explicitly requested. Typography, whitespace, and layout are the primary visual tools.
4. **Accessibility** — WCAG AA contrast (4.5:1 body, 3:1 large text). Never rely on color alone. 12px text floor, 16px body copy. Respect `prefers-reduced-motion`.

## References

`read` the file that covers the choice in front of you — the principles above are the whole of what applies unconditionally.

| File                       | Covers                                                                                                | Read when                                                                             |
| -------------------------- | ----------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| `references/color.md`      | "Earn Every Color", the Nexus default palette (light/dark/extended hexes), custom-palette derivation, contrast rules | Picking any hex — an accent, a surface, a semantic color — or deriving a palette from user direction |
| `references/typography.md` | Measure/leading/scale rules, display-vs-body floors, serif-vs-sans, Font Strategy by Format, brand fonts, blacklist, size hierarchy, Slides and PDF pairings | Choosing a typeface or pairing, or setting sizes for a specific output format          |
| `references/dataviz.md`    | Chart color sequence, chart type selection, data-ink rules, chart typography, KPI cards               | Building a chart, graph, or KPI tile in any medium                                     |

Paths are relative to this skill's mounted directory (`.skills/design-foundations/`).

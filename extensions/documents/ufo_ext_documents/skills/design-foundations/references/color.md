# Color — Default Palette & Accessibility

## Philosophy: Earn Every Color

Color is emphasis — every non-neutral color must answer: **what does this help the viewer understand?** The viewer's eye goes where color is; if everything is colored, nothing stands out.

**Target:** 1 accent + 0-2 semantic colors (error/warning/success). Everything else neutral. Squint at your output — you should see a calm, mostly-neutral surface with 1-2 small moments of color.

---

## Default Palette — Nexus

**Use when the user gives no color direction.** Flying Object-aligned — monochrome gray/black with a single amber highlight, professional, accessible.

**These are roles, not a mandate.** A typical output uses Background + Text + Primary. Add semantic colors (error, warning, success) only when the content requires them. Do not introduce color for decoration.

### Light Mode

| Role          | Hex       | Usage                                                          |
| ------------- | --------- | -------------------------------------------------------------- |
| Background    | `#F7F7F7` | Primary background                                             |
| Surface       | `#F9F9F9` | Cards, containers                                              |
| Surface alt   | `#FBFBFB` | Secondary surface layer                                        |
| Border        | `#D1D1D1` | Dividers, card borders                                         |
| Text          | `#232323` | Primary body text                                              |
| Text muted    | `#6F6F6F` | Secondary text                                                 |
| Text faint    | `#B8B8B8` | Placeholders, tertiary                                         |
| Accent        | `#FBBF24` | Signature amber — fills, marks, selected states (dark text on it, never text on light) |
| Primary       | `#B45309` | Links, CTAs (Deep Amber — AA on light surfaces)                |
| Primary hover | `#92400E` | Hover state                                                    |
| Error         | `#A12C7B` | Destructive states                                             |
| Warning       | `#C2410C` | Caution states                                                 |
| Success       | `#437A22` | Confirmation states                                            |

### Dark Mode

| Role          | Hex       | Usage                                            |
| ------------- | --------- | ------------------------------------------------ |
| Background    | `#141414` | Primary background                               |
| Surface       | `#191919` | Cards, containers                                |
| Surface alt   | `#1D1D1D` | Secondary surface layer                          |
| Border        | `#383838` | Dividers, card borders                           |
| Text          | `#CCCCCC` | Primary body text                                |
| Text muted    | `#8C8C8C` | Secondary text                                   |
| Text faint    | `#595959` | Tertiary text                                    |
| Accent        | `#FBBF24` | Signature amber — fills, marks, selected states  |
| Primary       | `#FBBF24` | Links, CTAs                                      |
| Primary hover | `#F59E0B` | Hover state                                      |
| Error         | `#D163A7` | Destructive states                               |
| Warning       | `#FB923C` | Caution states                                   |
| Success       | `#6DAA45` | Confirmation states                              |

### Extended Palette (data visualization only)

| Name   | Light     | Dark      |
| ------ | --------- | --------- |
| Orange | `#DA7101` | `#FDAB43` |
| Gold   | `#D19900` | `#E8AF34` |
| Blue   | `#006494` | `#5591C7` |
| Purple | `#7A39BB` | `#A86FDF` |
| Red    | `#A13544` | `#DD6974` |

**Data visualization naturally needs extra colors** to distinguish categories and series — that's legitimate. But those colors should fit within the overall art direction, not be random. Derive chart colors from the project's accent (monochromatic shades work well for sequential data) or use the curated chart color sequence in `.skills/design-foundations/references/dataviz.md`. The key: chart colors should feel like they belong in the same design system as the rest of the page.

---

## Custom Palettes

When the user provides color direction **or the content suggests a natural accent** (e.g., finance → navy, sustainability → green): start with that primary as accent → derive surfaces by desaturating → keep semantic colors recognizable (red=error, green=success) → build light AND dark → test contrast (body 4.5:1, large text 3:1). If neither user direction nor content suggest a clear hue, use the Nexus palette above.

---

## Color Accessibility (Non-Negotiable)

- **WCAG AA:** Body text 4.5:1, large text (18px+/14px bold) 3:1
- **Color independence:** Never rely on color alone — add labels, patterns, icons
- **Colorblind safety:** Avoid red/green only. Blue/orange is safer. 8% of men have red-green deficiency
- **Test:** Screenshot and verify contrast. Use DevTools audit for CSS, visual check for slides/charts

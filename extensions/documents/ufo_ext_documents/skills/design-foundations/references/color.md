# Color — Default Palette & Accessibility

## Philosophy: Earn Every Color

Color is emphasis — every non-neutral color must answer: **what does this help the viewer understand?** The viewer's eye goes where color is; if everything is colored, nothing stands out.

**Target:** 1 accent + 0-2 semantic colors (error/warning/success). Everything else neutral. Squint at your output — you should see a calm, mostly-neutral surface with 1-2 small moments of color.

---

## The Default Palette

**Use when the user gives no color direction.** Three surface steps, two text tones, two accents — the palette the product's own interface is painted from.

**These are roles, not a mandate.** A typical output uses one surface + primary text + one accent. Add semantic colors (error, warning, success) only when the content requires them. Do not introduce color for decoration.

### Surface steps and text tones

| Token              | Light     | Dark      | Usage                                        |
| ------------------ | --------- | --------- | -------------------------------------------- |
| `--bkgd-100`       | `#FAF9F7` | `#191A1A` | The page                                     |
| `--bkgd-200`       | `#F4F3F2` | `#262929` | Cards, fields, anything filled on the page   |
| `--bkgd-300`       | `#EBEAE9` | `#323535` | Dividers, card borders, hairlines            |
| `--text-primary`   | `#191A1A` | `#F5F5F5` | Body text, headings                          |
| `--text-secondary` | `#919090` | `#A7A9A9` | Secondary text                               |
| `--text-tertiary` | `#919090` | `#7D7F7F` | Labels found, not read                       |

The two accents are one hex each — they do not change with the surface.

| Token                | Hex       | Usage                                                     |
| -------------------- | --------- | --------------------------------------------------------- |
| `--accent-primary`   | `#0095FF` | Links, CTAs, marks, selected states, the first chart series |
| `--accent-secondary` | `#FF6700` | Caution and warning states, the second chart series        |

### Semantic colors

The second accent carries caution. Two more hexes join it, built on the same rule: one saturated fill that reads on either surface.

| Role    | Fill      | Usage                                |
| ------- | --------- | ------------------------------------ |
| Warning | `#FF6700` | Caution — `--accent-secondary` again |
| Danger  | `#FF2332` | Destructive states, errors           |
| Success | `#00A963` | Confirmation states                  |

### Fills carry no words on the light surface

An accent or semantic hex is a **fill** — a bar, a mark, a selected row, a solid CTA. On the light surface none of them clears AA as text; each has a text step below that does. On the dark surface the fill reads as text directly.

A label sitting **on** a fill is always `#191A1A`, never the light surface:

| Fill      | `#191A1A` on it |
| --------- | --------------- |
| `#0095FF` | 5.6:1           |
| `#FF6700` | 6.0:1           |
| `#FF2332` | 4.6:1           |
| `#00A963` | 5.7:1           |

### Every pairing that may carry a word

Nothing outside this table is set as text. Body-size text clears 4.5:1; a row marked 18px+ (or 14px bold) clears 3:1 and is used at no smaller size.

| Role      | Size  | Surface   | Text      | Ratio    |
| --------- | ----- | --------- | --------- | -------- |
| Primary   | body  | `#FAF9F7` | `#191A1A` | 16.6:1   |
| Primary   | body  | `#191A1A` | `#F5F5F5` | 16.0:1   |
| Secondary | body  | `#FAF9F7` | `#676767` | 5.4:1    |
| Secondary | body  | `#191A1A` | `#A7A9A9` | 7.4:1    |
| Secondary | 18px+ | `#FAF9F7` | `#919090` | 3.0:1    |
| Link      | body  | `#FAF9F7` | `#0069B5` | 5.4:1    |
| Link      | body  | `#191A1A` | `#0095FF` | 5.6:1    |
| Warning   | body  | `#FAF9F7` | `#AE4600` | 5.4:1    |
| Warning   | body  | `#191A1A` | `#FF6700` | 6.0:1    |
| Danger    | body  | `#FAF9F7` | `#D0000E` | 5.4:1    |
| Danger    | body  | `#191A1A` | `#FF2332` | 4.6:1    |
| Success   | body  | `#FAF9F7` | `#007645` | 5.4:1    |
| Success   | body  | `#191A1A` | `#00A963` | 5.7:1    |

**How a text step is derived:** the fill's own hue and saturation, at the lightness that reads 5.4:1 on `#FAF9F7`. Nothing shifts hue.

**How a faint tone is derived:** the secondary text tone at 50% over `--bkgd-100` — `#C6C4C4` on light, `#606262` on dark. A placeholder or a disabled control, never text the reader has to read.

**How a pale tone is derived:** the same 50% over `--bkgd-100`, applied to a fill — `#7DC7FB` from the accent. A tint behind a mark, or the light end of a sequential chart ramp.

**Data visualization needs more colors than this** to separate categories and series — that is legitimate. The sequence built from these hexes is in `$UFO_HOME/skills/design-foundations/references/dataviz.md`. Chart colors belong to the same design system as the rest of the page: for sequential data, shades of one accent.

---

## Custom Palettes

When the user provides color direction **or the content suggests a natural accent** (e.g., finance → navy, sustainability → green): start with that primary as accent → derive surfaces by desaturating → keep semantic colors recognizable (red=error, green=success) → build light AND dark → test contrast (body 4.5:1, large text 3:1). If neither user direction nor content suggest a clear hue, use the default palette above.

---

## Color Accessibility (Non-Negotiable)

- **WCAG AA:** Body text 4.5:1, large text (18px+/14px bold) 3:1
- **Color independence:** Never rely on color alone — add labels, patterns, icons
- **Colorblind safety:** Avoid red/green only. Blue/orange is safer. 8% of men have red-green deficiency
- **Test:** Screenshot and verify contrast. Use DevTools audit for CSS, visual check for slides/charts

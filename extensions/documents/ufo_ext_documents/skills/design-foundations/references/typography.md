# Typography — Selection, Hierarchy, Pairing

Type principles for any visual artifact.

---

## Foundational Rules

1. **Readable measure:** 45-75 characters/line (66 ideal). Drives container widths and font sizes.
2. **Leading:** 1.5-1.6× body, 1.15-1.25× headings. Sans-serifs need more.
3. **Typographic color:** Consistent word-spacing. Never letterspace lowercase. Flush-left/ragged-right for screen.
4. **Proportional scales:** Each size step marks a content role change. Same role = same size everywhere.
5. **Content-sympathetic typefaces:** Font chosen for novelty rather than sympathy with content fights the reader.

---

## Economy

- **3-4 text styles** per page/slide (title, heading, body, caption)
- **2 fonts max** (display + body). Weight and size for variation, not extra typefaces.
- **2-3 weights** per font. Regular + bold covers most needs.

---

## Display vs. Body

| Type                | Min screen | Min print/slides | Use for                 |
| ------------------- | ---------- | ---------------- | ----------------------- |
| Display             | 24px       | 18pt             | Titles, heroes, covers  |
| Body                | 12px       | 9pt              | Body, bullets, captions |
| Body bold (heading) | 18px       | 14pt             | Section headings        |

Never set display fonts below 24px/18pt. Never use body fonts at hero sizes expecting drama.

---

## Serif vs. Sans-Serif

- **Sans-serif** for UI, dashboards, data, product interfaces, documents, and slides. Better at small sizes. Natural default for professional output.
- **Serif** for editorial, long-form, or explicitly formal contexts. Adds authority and rhythm. Use for headings only — not body text in documents or slides.
- Below 14px/10pt, always use sans-serif.
- **Documents & slides default to professional sans-serif** unless the content calls for a formal/editorial tone.

---

## Font Strategy by Format

Font selection is fundamentally different depending on the output format. Each format has different constraints and different expectations for distinctiveness:

| Format               | Strategy                                                                                                                                                                        | Why                                                                  |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| **Websites**         | Intentionally selected distinctive fonts loaded via CDN. **Prefer Fontshare** (less overexposed) over Google Fonts. The font IS the design — it should match the art direction. | Websites load any font via CDN. System fonts are fallback only.      |
| **PDFs**             | Same quality as web — embed any TTF. Download from Google Fonts at runtime.                                                                                                     | PDFs embed fonts automatically. Use professional, distinctive fonts. |
| **Slides (PPTX)**    | System fonts only — Calibri, Trebuchet MS, Arial, Georgia.                                                                                                                      | PPTX cannot embed fonts. The viewer must have the font installed.    |
| **Documents (DOCX)** | System fonts recommended — Arial, Calibri.                                                                                                                                      | Documents must render correctly on the viewer's machine.             |

### House Fonts (Fallback Defaults)

When no font direction is given, the house fonts the `ufo-style` skill's `tokens.css` declares apply:

| Purpose | House font      | Free PDF alt (embed TTF)          | Free slide alt (system only) |
| ------- | --------------- | --------------------------------- | ---------------------------- |
| Display | Georgia         | DM Sans Bold / Work Sans SemiBold | Georgia / Calibri Bold       |
| Body    | Inter (400-600) | Inter / DM Sans                   | Calibri / Arial              |
| Code    | Roboto Mono     | JetBrains Mono                    | Consolas / Courier New       |

---

## Font Rules

**Blacklisted:** Papyrus, Comic Sans, Lobster, Impact, Jokerman, Bleeding Cowboys, Permanent Marker, Bradley Hand, Brush Script, Hobo, Trajan, Raleway, Clash Display, Courier New (body).

**Overused on the web (never use as the primary font for websites):** Roboto, Arial, Helvetica, Open Sans, Lato, Montserrat, Poppins. System fonts (Arial, Helvetica, Georgia, Calibri, Times New Roman, Verdana, Tahoma, Trebuchet MS) belong in the fallback stack only — never as the chosen font. Every website loads a distinctive font via CDN; system fonts are the safety net if loading fails. For slides and documents where embedding isn't available, system fonts are fine as the primary choice.

**Vary across projects** — never reuse the same combination twice in a row.

---

## Size Hierarchy

| Role               | Web (px) | Slides (pt) |
| ------------------ | -------- | ----------- |
| Hero / Cover       | 48-128px | 44-72pt     |
| Page / Slide title | 24-36px  | 36-44pt     |
| Section heading    | 18-24px  | 20-28pt     |
| Body               | 16-18px  | 14-18pt     |
| Captions / Labels  | 12-14px  | 10-12pt     |

**Floor:** 12px / 9pt absolute minimum for any text.

---

## Slides Pairings (System Fonts Only — No Embedding)

PPTX cannot embed fonts. Use only fonts installed on the viewer's machine:

| Heading           | Body          | Tone               |
| ----------------- | ------------- | ------------------ |
| Trebuchet MS Bold | Calibri       | Modern, clean      |
| Calibri Bold      | Calibri Light | Minimal, corporate |
| Arial Black       | Arial         | Bold, direct       |
| Georgia           | Calibri       | Classic, formal    |
| Cambria           | Calibri       | Traditional        |

## PDF Pairings (Embedded — Same Fonts as Web)

PDFs embed TTF fonts automatically. Download from Google Fonts at runtime:

| Heading             | Body          | Tone                       |
| ------------------- | ------------- | -------------------------- |
| DM Sans Bold        | Inter         | Modern, clean              |
| Work Sans SemiBold  | Work Sans     | Minimal, versatile         |
| Instrument Serif    | DM Sans       | Editorial, sophisticated   |
| Source Serif 4 Bold | Source Sans 3 | Traditional, authoritative |

Fallback: Helvetica (built-in, no download needed).

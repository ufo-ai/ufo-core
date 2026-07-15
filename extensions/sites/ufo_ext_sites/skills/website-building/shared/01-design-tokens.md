# Design Tokens — Type, Spacing, Color, Base CSS

Every website must include these design systems and base stylesheet before any component styles.

---

## Type Scale

Use a fluid type scale with `clamp()`. Every text element references a scale token — never hardcode font sizes.

**Minimum size floor: 12px (0.75rem).** No text on screen should ever render below 12px. This is the absolute floor for tiny labels and metadata.

```css
:root {
  --text-xs: clamp(0.75rem, 0.7rem + 0.25vw, 0.875rem); /* 12px floor → 14px */
  --text-sm: clamp(0.875rem, 0.8rem + 0.35vw, 1rem); /* 14px floor → 16px */
  --text-base: clamp(1rem, 0.95rem + 0.25vw, 1.125rem); /* 16px floor → 18px */
  --text-lg: clamp(1.125rem, 1rem + 0.75vw, 1.5rem); /* 18px → 24px */
  --text-xl: clamp(1.5rem, 1.2rem + 1.25vw, 2.25rem); /* 24px → 36px */
  --text-2xl: clamp(2rem, 1.2rem + 2.5vw, 3.5rem); /* 32px → 56px */
  --text-3xl: clamp(2.5rem, 1rem + 4vw, 5rem); /* 40px → 80px */
  --text-hero: clamp(3rem, 0.5rem + 7vw, 8rem); /* 48px → 128px */
}
```

### Preferred Sizes for UI Elements

| Element                                       | Token                        | Resolves to | Notes                                                                                                                               |
| --------------------------------------------- | ---------------------------- | ----------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| **Tiny labels, badges, metadata**             | `--text-xs`                  | 12-14px     | The absolute floor (12px min). Only for secondary/tertiary info.                                                                    |
| **Buttons, nav links**                        | `--text-sm`                  | 14-16px     | Standard for all interactive UI text.                                                                                               |
| **Body text (all contexts)**                  | `--text-base`                | 16-18px     | **The default for body copy.** 16px is the baseline for comfortable reading in product UI, editorial, and all other contexts.       |
| **Body text (editorial/long-form)**           | `--text-base` to `--text-lg` | 16-24px     | Long-form reading benefits from 18px.                                                                                               |
| **Section headings**                          | `--text-lg`                  | 18-24px     | One step up from body. Body font bold or display font.                                                                              |
| **Page title**                                | `--text-xl`                  | 24-36px     | ONE per page. This is where display fonts start.                                                                                    |
| **Hero heading (informational/landing ONLY)** | `--text-2xl`                 | 32-56px     | Display font. ONE per page. **Not for web apps** — web app page titles cap at `--text-xl`.                                          |
| **Display (informational/landing ONLY)**      | `--text-3xl`/`--text-hero`   | 40-128px    | Informational sites only — editorial heroes, portfolio splash, landing headlines. Never in web apps, dashboards, or interior pages. |

### Max Display Size by Site Type

| Site type                                         | Max token     | Max resolves to | Notes                                                                                        |
| ------------------------------------------------- | ------------- | --------------- | -------------------------------------------------------------------------------------------- |
| **Informational** (portfolio, editorial, landing) | `--text-hero` | 128px           | Dramatic display headlines in the hero section. Interior pages cap at `--text-2xl`.          |
| **Web app** (SaaS, dashboard, admin, e-commerce)  | `--text-xl`   | 36px            | Web apps are functional, not theatrical. Page titles use `--text-xl`. No display-scale type. |
| **Brand experience**                              | `--text-2xl`  | 56px            | ONE hero moment allowed. Everything else at `--text-xl` or below.                            |

Fluid type rules:

- **Pick 3-4 sizes per page max.** Typical page: `--text-xs` (tiny labels), `--text-sm` (buttons/nav), `--text-base` (body), `--text-lg` (headings). Informational/landing pages add `--text-2xl` for hero.
- **Web apps cap at `--text-xl`.** SaaS products, dashboards, admin panels, and e-commerce UIs should never use `--text-2xl` or above. Page titles use `--text-xl` (24-36px). If it feels small, increase font-weight or spacing — not size.
- **Body copy is `--text-base` (16px), not `--text-lg`.** The most common sizing mistake is using `--text-lg` for body text, which makes everything feel bloated. `--text-lg` is for section headings.
- Use `rem` for min/max bounds; mix `vw` with `rem` in preferred value (`1rem + 2vw`) so zooming works.
- Test at 200% zoom (WCAG requirement).

---

## 4px Spacing System

All spacing derives from a 4px base unit.

```css
:root {
  --space-1: 0.25rem; /*  4px */
  --space-2: 0.5rem; /*  8px */
  --space-3: 0.75rem; /* 12px */
  --space-4: 1rem; /* 16px */
  --space-5: 1.25rem; /* 20px */
  --space-6: 1.5rem; /* 24px */
  --space-8: 2rem; /* 32px */
  --space-10: 2.5rem; /* 40px */
  --space-12: 3rem; /* 48px */
  --space-16: 4rem; /* 64px */
  --space-20: 5rem; /* 80px */
  --space-24: 6rem; /* 96px */
  --space-32: 8rem; /* 128px */
}
```

Rules:

- **Every** margin, padding, gap must reference a spacing token. Never arbitrary pixel values.
- `--space-1`-`3` for tight spacing (icon gaps, input/badge padding); `--space-4`-`8` for component spacing (card padding, form gaps); `--space-10`-`32` for layout spacing (section padding, page gutters).
- Fluid section spacing: `padding-block: clamp(var(--space-8), 6vw, var(--space-24));`

---

## Color Hierarchy

Use OKLCH as your primary color space. Define a layered system with semantic roles — never hardcode hex values.

**Color restraint philosophy:** See `skills/design-foundations/SKILL.md` for the full rationale. In brief: 1 accent + neutrals for most pages. Remaining palette colors (orange, gold, blue, purple, notification) are reserved for data visualization only.

### Light & Dark Mode (Mandatory)

**Every website must include both light AND dark mode.** Use `prefers-color-scheme` as default, with a manual toggle (sun/moon icon) in the header. The toggle should:

- Set `data-theme="light"` or `data-theme="dark"` on `<html>` to override system preference
- CSS pattern: `:root, [data-theme="light"]` for light; `[data-theme="dark"]` for dark
- Persist the preference in `localStorage` and re-apply it on load
- Default to system preference via `window.matchMedia('(prefers-color-scheme: dark)')`

### Art Direction First — Then Fallback to Nexus

**Always infer a palette from the subject matter before reaching for defaults.** A jazz festival site should feel warm and expressive. A law firm should feel sober and restrained. A children's toy store should feel bright and playful. Derive color from the content — don't wait for the user to explicitly provide a hex code.

The decision tree:

1. **User provides colors/brand** → use those, maintain the variable structure below
2. **No colors given, but subject is clear** → infer an appropriate palette from the subject's domain, mood, and audience (see Art Direction tables in the domain files)
3. **Subject is ambiguous AND user gave no direction after being asked** → use Nexus defaults below

When building a custom palette (steps 1-2), maintain the same variable structure with both light and dark modes and ensure WCAG AA contrast (4.5:1 body text, 3:1 large text).

### Nexus Design System (Fallback Palette)

The Nexus palette is a monochrome gray/black system with a single amber highlight, designed as a safe fallback — not the default for every site. For the format-agnostic hex palette and full rationale, see `skills/design-foundations/SKILL.md`. Below is the CSS variable implementation.

```css
/* NEXUS DESIGN SYSTEM — monochrome gray/black surfaces, amber accent */

:root,
[data-theme='light'] {
  /* Surfaces (Mono Gray) */
  --color-bg: #f7f7f7;
  --color-surface: #f9f9f9;
  --color-surface-2: #fbfbfb;
  --color-surface-offset: #f1f1f1;
  --color-surface-offset-2: #eaeaea;
  --color-surface-dynamic: #e3e3e3;
  --color-divider: #d9d9d9;
  --color-border: #d1d1d1;

  /* Text (Ink) */
  --color-text: #232323;
  --color-text-muted: #6f6f6f;
  --color-text-faint: #b8b8b8;
  --color-text-inverse: #f9f9f9;

  /* Accent (Amber) — the signature highlight: solid fills, highlight marks,
     selected states, active indicators. Always near-black text/icons on it;
     never a text color on light surfaces (fails contrast — use primary). */
  --color-accent: #fbbf24;

  /* Primary (Deep Amber) — links, CTAs, interactive text; AA on light surfaces */
  --color-primary: #b45309;
  --color-primary-hover: #92400e;
  --color-primary-active: #78350f;
  --color-primary-highlight: #fef3c7;

  /* Warning (Signal Orange) */
  --color-warning: #c2410c;
  --color-warning-hover: #9a3412;
  --color-warning-active: #7c2d12;
  --color-warning-highlight: #ffedd5;

  /* Error (Jenova Maroon) */
  --color-error: #a12c7b;
  --color-error-hover: #7d1e5e;
  --color-error-active: #561740;
  --color-error-highlight: #e0ced7;

  /* Notification (Rosa Red) */
  --color-notification: #a13544;
  --color-notification-hover: #782b33;
  --color-notification-active: #521f24;
  --color-notification-highlight: #dececb;

  /* Orange (Costa) */
  --color-orange: #da7101;
  --color-orange-hover: #c55700;
  --color-orange-active: #ac3e00;
  --color-orange-highlight: #e7d7c4;

  /* Gold (Altana) */
  --color-gold: #d19900;
  --color-gold-hover: #b07a00;
  --color-gold-active: #8a5b00;
  --color-gold-highlight: #e9e0c6;

  /* Yellow (Dalmasca) — shares base with Gold; diverges in dark mode */
  --color-yellow: #d19900;
  --color-yellow-hover: #b07a00;
  --color-yellow-active: #8a5b00;
  --color-yellow-highlight: #e6ddc4;

  /* Success (Gridania Green) */
  --color-success: #437a22;
  --color-success-hover: #2e5c10;
  --color-success-active: #1e3f0a;
  --color-success-highlight: #d4dfcc;

  /* Blue (Limsa) */
  --color-blue: #006494;
  --color-blue-hover: #0b5177;
  --color-blue-active: #0b3751;
  --color-blue-highlight: #c6d8e4;

  /* Purple (Kuja) */
  --color-purple: #7a39bb;
  --color-purple-hover: #5f2699;
  --color-purple-active: #431673;
  --color-purple-highlight: #dacfde;

  /* Radius */
  --radius-sm: 0.375rem;
  --radius-md: 0.5rem;
  --radius-lg: 0.75rem;
  --radius-xl: 1rem;
  --radius-full: 9999px;

  /* Transitions */
  --transition-interactive: 180ms cubic-bezier(0.16, 1, 0.3, 1);

  /* Shadows (neutral, tone-matched to mono surfaces) */
  --shadow-sm: 0 1px 2px oklch(0 0 0 / 0.06);
  --shadow-md: 0 4px 12px oklch(0 0 0 / 0.08);
  --shadow-lg: 0 12px 32px oklch(0 0 0 / 0.12);

  /* Content widths */
  --content-narrow: 640px;
  --content-default: 960px;
  --content-wide: 1200px;
  --content-full: 100%;

  /* Font families — MUST define --font-body and --font-display in your style.css.
     Always load a distinctive font via CDN — system fonts are fallback only.
     Example: --font-display: 'Instrument Serif', Georgia, serif;
              --font-body: 'Work Sans', 'Helvetica Neue', sans-serif;
     The first name is the loaded font; system fonts after it are the safety net.
     base.css uses var(--font-body, sans-serif) as fallback. */
}

/* DARK MODE */
[data-theme='dark'] {
  --color-bg: #141414;
  --color-surface: #191919;
  --color-surface-2: #1d1d1d;
  --color-surface-offset: #1a1a1a;
  --color-surface-offset-2: #1f1f1f;
  --color-surface-dynamic: #2a2a2a;
  --color-divider: #242424;
  --color-border: #383838;
  --color-text: #cccccc;
  --color-text-muted: #8c8c8c;
  --color-text-faint: #595959;
  --color-text-inverse: #1a1a1a;
  --color-accent: #fbbf24;
  --color-primary: #fbbf24;
  --color-primary-hover: #f59e0b;
  --color-primary-active: #d97706;
  --color-primary-highlight: #3d3522;
  --color-warning: #fb923c;
  --color-warning-hover: #f97316;
  --color-warning-active: #ea580c;
  --color-warning-highlight: #46311d;
  --color-error: #d163a7;
  --color-error-hover: #b9478f;
  --color-error-active: #9b2f76;
  --color-error-highlight: #4c3d46;
  --color-notification: #dd6974;
  --color-notification-hover: #c24a59;
  --color-notification-active: #a53142;
  --color-notification-highlight: #574848;
  --color-orange: #fdab43;
  --color-orange-hover: #fec47e;
  --color-orange-active: #fdd1a4;
  --color-orange-highlight: #564b3e;
  --color-gold: #e8af34;
  --color-gold-hover: #fdc551;
  --color-gold-active: #feda74;
  --color-gold-highlight: #4d4332;
  --color-yellow: #edb336;
  --color-yellow-hover: #fdc452;
  --color-yellow-active: #feda74;
  --color-yellow-highlight: #574e3d;
  --color-success: #6daa45;
  --color-success-hover: #4d8f25;
  --color-success-active: #387015;
  --color-success-highlight: #3a4435;
  --color-blue: #5591c7;
  --color-blue-hover: #3b78ab;
  --color-blue-active: #275f8e;
  --color-blue-highlight: #3a4550;
  --color-purple: #a86fdf;
  --color-purple-hover: #9250d0;
  --color-purple-active: #7537ba;
  --color-purple-highlight: #4e4652;
  --shadow-sm: 0 1px 2px oklch(0 0 0 / 0.2);
  --shadow-md: 0 4px 12px oklch(0 0 0 / 0.3);
  --shadow-lg: 0 12px 32px oklch(0 0 0 / 0.4);
}

/* System preference fallback: duplicate [data-theme="dark"] variables
   inside @media (prefers-color-scheme: dark) { :root:not([data-theme]) { ... } }
   to support users who haven't toggled manually. */
```

**Dark mode toggle (include in every project):**

```javascript
(function () {
  const t = document.querySelector('[data-theme-toggle]'),
    r = document.documentElement;
  let d = matchMedia('(prefers-color-scheme:dark)').matches ? 'dark' : 'light';
  r.setAttribute('data-theme', d);
  t &&
    t.addEventListener('click', () => {
      d = d === 'dark' ? 'light' : 'dark';
      r.setAttribute('data-theme', d);
      t.setAttribute('aria-label', 'Switch to ' + (d === 'dark' ? 'light' : 'dark') + ' mode');
      t.innerHTML =
        d === 'dark'
          ? '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>'
          : '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>';
    });
})();
```

---

## Color Implementation Notes

- **Three text levels**: primary, muted, faint. **Surface layers**: bg → surface → surface-2 → surface-offset.
- `color-mix(in oklab, ...)` for opacity adjustments. Custom palettes: replace Nexus defaults but keep variable names + both modes.
- **Better gradients**: `linear-gradient(in oklab, var(--color-primary), var(--color-orange))`. **P3 wide-gamut**: `@media (color-gamut: p3) { :root { --color-primary: oklch(0.55 0.15 55); } }`
- **Amber is the only pop.** In Nexus, `--color-accent` (#FBBF24) carries all emphasis — a highlight mark, a selected state, a solid CTA fill with near-black text. Everything else stays gray/black. If a page squints to more than gray + one amber moment per view, it has drifted.

### Nexus HSL Equivalents (for Tailwind / shadcn projects)

When using the fullstack webapp template (Tailwind + shadcn), `index.css` uses HSL values in `H S% L%` format (no `hsl()` wrapper). Below are Nexus HSL conversions for when Nexus is the appropriate fallback (see "Art Direction First" above). For inferred or custom palettes, convert your chosen colors to the same `H S% L%` format:

**Light mode:**

| Role                 | Hex       | HSL (`H S% L%`) |
| -------------------- | --------- | --------------- |
| Background           | `#F7F7F7` | `0 0% 97%`      |
| Surface / Card       | `#F9F9F9` | `0 0% 98%`      |
| Surface-2            | `#FBFBFB` | `0 0% 98%`      |
| Surface-offset       | `#F1F1F1` | `0 0% 95%`      |
| Border               | `#D1D1D1` | `0 0% 82%`      |
| Divider              | `#D9D9D9` | `0 0% 85%`      |
| Text                 | `#232323` | `0 0% 14%`      |
| Text muted           | `#6F6F6F` | `0 0% 44%`      |
| Text faint           | `#B8B8B8` | `0 0% 72%`      |
| Accent (Amber)       | `#FBBF24` | `43 96% 56%`    |
| Primary (Deep Amber) | `#B45309` | `26 90% 37%`    |
| Primary hover        | `#92400E` | `23 83% 31%`    |
| Error (Maroon)       | `#A12C7B` | `320 57% 40%`   |
| Warning (Orange)     | `#C2410C` | `17 88% 40%`    |
| Success (Green)      | `#437A22` | `103 56% 31%`   |

**Dark mode:**

| Role            | Hex       | HSL (`H S% L%`) |
| --------------- | --------- | --------------- |
| Background      | `#141414` | `0 0% 8%`       |
| Surface / Card  | `#191919` | `0 0% 10%`      |
| Surface-2       | `#1D1D1D` | `0 0% 11%`      |
| Border          | `#383838` | `0 0% 22%`      |
| Text            | `#CCCCCC` | `0 0% 80%`      |
| Text muted      | `#8C8C8C` | `0 0% 55%`      |
| Accent (Amber)  | `#FBBF24` | `43 96% 56%`    |
| Primary (Amber) | `#FBBF24` | `43 96% 56%`    |
| Error           | `#D163A7` | `320 47% 60%`   |
| Warning         | `#FB923C` | `27 96% 61%`    |
| Success         | `#6DAA45` | `97 43% 47%`    |

These are the Nexus fallback values. **Always try to derive a concept-driven palette first** (see "Art Direction First" above). Use Nexus only when the request is truly generic with no topic to infer from. When deriving a custom palette, convert your chosen colors to the same `H S% L%` format and role structure with both light and dark modes.

---

## Base Stylesheet

**Every project must include this base CSS** before any component styles.

```css
/* base.css */
*,
*::before,
*::after {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

html {
  -moz-text-size-adjust: none;
  -webkit-text-size-adjust: none;
  text-size-adjust: none;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  text-rendering: optimizeLegibility;
  scroll-behavior: smooth;
  hanging-punctuation: first last;
  scroll-padding-top: var(--space-16);
}

body {
  min-height: 100dvh;
  line-height: 1.6;
  font-family: var(--font-body, sans-serif);
  font-size: var(--text-base);
  color: var(--color-text);
  background-color: var(--color-bg);
}

img,
picture,
video,
canvas,
svg {
  display: block;
  max-width: 100%;
  height: auto;
}
ul[role='list'],
ol[role='list'] {
  list-style: none;
}
input,
button,
textarea,
select {
  font: inherit;
  color: inherit;
}

h1,
h2,
h3,
h4,
h5,
h6 {
  text-wrap: balance;
  line-height: 1.15;
}
p,
li,
figcaption {
  text-wrap: pretty;
  max-width: 72ch;
}

::selection {
  background: oklch(from var(--color-accent) l c h / 0.4);
  color: var(--color-text);
}

:focus-visible {
  outline: 2px solid var(--color-primary);
  outline-offset: 3px;
  border-radius: var(--radius-sm);
}

@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}

button {
  cursor: pointer;
  background: none;
  border: none;
}
table {
  border-collapse: collapse;
  width: 100%;
}

/* Interactive elements: animate hover/focus transitions.
   Only clickable elements get hover states — see `shared/03-motion.md`.
   Never add :hover styles to non-interactive elements. */
a,
button,
[role='button'],
[role='link'],
input,
textarea,
select {
  transition:
    color var(--transition-interactive),
    background var(--transition-interactive),
    border-color var(--transition-interactive),
    box-shadow var(--transition-interactive);
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border-width: 0;
}
```

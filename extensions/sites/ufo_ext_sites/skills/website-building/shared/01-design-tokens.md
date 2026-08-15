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

**Color restraint philosophy:** See `.skills/design-foundations/references/color.md` for the full rationale. In brief: 1 accent + neutrals for most pages. The status colors appear only where the content is a status, and a chart draws its series from the sequence in that same skill.

### Light & Dark Mode (Mandatory)

**Every website must include both light AND dark mode.** Use `prefers-color-scheme` as default, with a manual toggle (sun/moon icon) in the header. The toggle should:

- Set `data-theme="light"` or `data-theme="dark"` on `<html>` to override system preference
- CSS pattern: `:root, [data-theme="light"]` for light; `[data-theme="dark"]` for dark
- Persist the preference in `localStorage` and re-apply it on load
- Default to system preference via `window.matchMedia('(prefers-color-scheme: dark)')`

### Art Direction First — Then Fall Back to the Default Palette

**Always infer a palette from the subject matter before reaching for defaults.** A jazz festival site should feel warm and expressive. A law firm should feel sober and restrained. A children's toy store should feel bright and playful. Derive color from the content — don't wait for the user to explicitly provide a hex code.

The decision tree:

1. **User provides colors/brand** → use those, maintain the variable structure below
2. **No colors given, but subject is clear** → infer an appropriate palette from the subject's domain, mood, and audience (see Art Direction tables in the domain files)
3. **Subject is ambiguous AND user gave no direction after being asked** → use the defaults below

When building a custom palette (steps 1-2), maintain the same variable structure with both light and dark modes and ensure WCAG AA contrast (4.5:1 body text, 3:1 large text).

### The Default Palette (Fallback)

Three surface steps, two text tones, two accents — the palette the product's own interface is painted from, and a safe fallback rather than the default for every site. For the format-agnostic hex palette, the contrast table, and the rules that derive every step, see `.skills/design-foundations/references/color.md`. Below is the CSS variable implementation.

```css
/* DEFAULT PALETTE — warm neutral surfaces, a blue accent, an orange second accent */

:root,
[data-theme='light'] {
  /* Surfaces */
  --color-bg: #faf9f7;
  --color-surface: #f4f3f2;
  --color-surface-2: #ebeae9;

  /* Text */
  --color-text: #191a1a;
  --color-text-muted: #676767;
  --color-text-faint: #c6c4c4;
  --color-text-inverse: #faf9f7;

  /* Accent — a fill: solid CTAs, highlight marks, selected states, chart bars.
     Always near-black text/icons on it; never a text color on light surfaces
     (fails contrast — use primary). */
  --color-accent: #0095ff;

  /* Primary — the accent's text step: links, CTAs, interactive text. AA on light. */
  --color-primary: #0069b5;

  /* Status — `-fill` is the mark, the bare name is the word */
  --color-warning-fill: #ff6700;
  --color-warning: #ae4600;
  --color-error-fill: #ff2332;
  --color-error: #d0000e;
  --color-success-fill: #00a963;
  --color-success: #007645;
}

/* DARK MODE — the fills are unchanged, and on this surface each one is its own text */
[data-theme='dark'] {
  --color-bg: #191a1a;
  --color-surface: #262929;
  --color-surface-2: #323535;
  --color-text: #f5f5f5;
  --color-text-muted: #a7a9a9;
  --color-text-faint: #606262;
  --color-text-inverse: #191a1a;
  --color-accent: #0095ff;
  --color-primary: var(--color-accent);
  --color-warning-fill: #ff6700;
  --color-warning: var(--color-warning-fill);
  --color-error-fill: #ff2332;
  --color-error: var(--color-error-fill);
  --color-success-fill: #00a963;
  --color-success: var(--color-success-fill);
  --shadow-sm: 0 1px 2px oklch(0 0 0 / 0.2);
  --shadow-md: 0 4px 12px oklch(0 0 0 / 0.3);
  --shadow-lg: 0 12px 32px oklch(0 0 0 / 0.4);
}

/* Everything else is derived from the steps above, so replacing them with a custom
   palette carries the whole system across. Each rule holds in both modes: mixing
   toward `--color-text` deepens a surface on light and lifts it on dark. */
:root {
  --color-border: var(--color-surface-2);
  --color-surface-offset: color-mix(in oklab, var(--color-surface-2) 92%, var(--color-text));
  --color-surface-dynamic: color-mix(in oklab, var(--color-surface-2) 84%, var(--color-text));

  --color-primary-hover: color-mix(in oklab, var(--color-primary) 85%, var(--color-text));
  --color-warning-hover: color-mix(in oklab, var(--color-warning) 85%, var(--color-text));
  --color-error-hover: color-mix(in oklab, var(--color-error) 85%, var(--color-text));
  --color-success-hover: color-mix(in oklab, var(--color-success) 85%, var(--color-text));

  --color-primary-highlight: color-mix(in oklab, var(--color-accent) 15%, var(--color-bg));
  --color-warning-highlight: color-mix(in oklab, var(--color-warning-fill) 15%, var(--color-bg));
  --color-error-highlight: color-mix(in oklab, var(--color-error-fill) 15%, var(--color-bg));
  --color-success-highlight: color-mix(in oklab, var(--color-success-fill) 15%, var(--color-bg));

  /* Radius */
  --radius-sm: 0.375rem;
  --radius-md: 0.5rem;
  --radius-lg: 0.75rem;
  --radius-xl: 1rem;
  --radius-full: 9999px;

  /* Transitions */
  --transition-interactive: 180ms cubic-bezier(0.16, 1, 0.3, 1);

  /* Shadows (neutral, tone-matched to the warm surfaces) */
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
- `color-mix(in oklab, ...)` for opacity adjustments. Custom palettes: replace the steps above but keep variable names + both modes.
- **Better gradients**: `linear-gradient(in oklab, var(--color-primary), var(--color-accent))`. **P3 wide-gamut**: `@media (color-gamut: p3) { :root { --color-primary: oklch(0.55 0.15 240); } }`
- **The accent is the only pop.** `--color-accent` (#0095FF) carries all emphasis — a highlight mark, a selected state, a solid CTA fill with near-black text. Everything else stays neutral. If a page squints to more than neutral + one blue moment per view, it has drifted.

### HSL Equivalents (for Tailwind / shadcn projects)

When using the fullstack webapp template (Tailwind + shadcn), `index.css` uses HSL values in `H S% L%` format (no `hsl()` wrapper). Below are the default palette's conversions, for when it is the appropriate fallback (see "Art Direction First" above). For inferred or custom palettes, convert your chosen colors to the same `H S% L%` format:

**Light mode:**

| Role          | Hex       | HSL (`H S% L%`) |
| ------------- | --------- | --------------- |
| Background    | `#FAF9F7` | `40 23% 97%`    |
| Surface       | `#F4F3F2` | `30 8% 95%`     |
| Surface-2     | `#EBEAE9` | `30 5% 92%`     |
| Border        | `#EBEAE9` | `30 5% 92%`     |
| Text          | `#191A1A` | `180 2% 10%`    |
| Text muted    | `#676767` | `0 0% 40%`      |
| Text faint    | `#C6C4C4` | `0 2% 77%`      |
| Accent        | `#0095FF` | `205 100% 50%`  |
| Primary       | `#0069B5` | `205 100% 35%`  |
| Warning fill  | `#FF6700` | `24 100% 50%`   |
| Warning       | `#AE4600` | `24 100% 34%`   |
| Error fill    | `#FF2332` | `356 100% 57%`  |
| Error         | `#D0000E` | `356 100% 41%`  |
| Success fill  | `#00A963` | `155 100% 33%`  |
| Success       | `#007645` | `155 100% 23%`  |

**Dark mode** — the fills are unchanged, and each one is its own text on this surface:

| Role       | Hex       | HSL (`H S% L%`) |
| ---------- | --------- | --------------- |
| Background | `#191A1A` | `180 2% 10%`    |
| Surface    | `#262929` | `180 4% 15%`    |
| Surface-2  | `#323535` | `180 3% 20%`    |
| Border     | `#323535` | `180 3% 20%`    |
| Text       | `#F5F5F5` | `0 0% 96%`      |
| Text muted | `#A7A9A9` | `180 1% 66%`    |
| Text faint | `#606262` | `180 1% 38%`    |
| Accent     | `#0095FF` | `205 100% 50%`  |
| Primary    | `#0095FF` | `205 100% 50%`  |
| Warning    | `#FF6700` | `24 100% 50%`   |
| Error      | `#FF2332` | `356 100% 57%`  |
| Success    | `#00A963` | `155 100% 33%`  |

These are the fallback values. **Always try to derive a concept-driven palette first** (see "Art Direction First" above). Use them only when the request is truly generic with no topic to infer from. When deriving a custom palette, convert your chosen colors to the same `H S% L%` format and role structure with both light and dark modes.

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

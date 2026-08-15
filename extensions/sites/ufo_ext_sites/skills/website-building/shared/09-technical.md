# Technical Rules & Workflow

Project structure, sandbox constraints, deployment workflow, and quality checklist.

---

## Project Structure

Create in project subfolder (paths are relative to workspace root):

```
project-name/
├── index.html
├── base.css          <- mandatory base stylesheet
├── style.css         <- design tokens + component styles
├── app.js (if needed)
└── assets/
    └── (images, fonts)
```

---

## Technical Rules

- **Static files only** — no server-side code in the project directory (use a backend server for server-side logic)
- **Relative paths** — `./style.css`, `./assets/logo.png`
- **CDN for libraries** — Tailwind, fonts, Chart.js, Three.js, etc.
- **Never include `integrity` attributes on CDN tags** — SRI hashes cannot be reliably generated from memory. A wrong hash silently prevents the script/stylesheet from loading with no visible error. Omit `integrity` unless the hash is computed at build time
- **No build tools required** — but allowed via `bash` if needed
- **Content images/videos must be real** — image or video URLs used to display content must be assets the user provided or real files fetched into the workspace, never hallucinated (no invented Wikipedia Commons URLs or other media sources). When no real image is available, author the section with SVG, CSS gradients, and type rather than a broken link or grey placeholder
- **Browser storage works** — the site is served at `http://localhost:<port>` inside the sandbox, so `localStorage`, `sessionStorage`, and cookies behave normally
- **Real paths, not an opaque URL** — the site is served as plain static files, so `pages/about.html`, deep links, and `<a href>` navigation all work. For protected views still prefer in-page mechanisms (hash routing `#admin`, tab/modal switches, password prompts) over security-by-obscure-path
- **Binary assets are served directly** — `<img>`/`<video>`/`<audio>`/`<source>` and JavaScript `fetch()` both work same-origin, and the HTML `download` attribute works. A backend (`19-backend.md`) is only needed for server-side logic or a forced `Content-Disposition: attachment`

---

## Workflow

### Step 1: Design Direction

Clarify purpose, pick aesthetic direction. See `SKILL.md` for the full design direction process.

### Step 2: Build

Build the site page by page. Screenshot each page via Playwright at desktop (1280px+) and mobile (375px) for QA. Fix all issues before moving to the next page.

### Step 3: Preview

```
deploy_website(
  project_path="project-name",
  site_name="project-name",
  entry_point="index.html"
)
```

Serves the folder at `http://localhost:8000` inside the sandbox — that URL is yours for validation and unreachable for the user — and hosts it at `site_url`, the link to give them. `share_file` the built output only when they want a copy to keep.

### Updating a Previewed Website

To update a site, edit the local workspace files (same `project-name/` directory from the original build) and call `deploy_website` again with the same `site_name` — that name is the site's identity, so the member's link keeps resolving. A different `site_name` on the same port retires the old registration and its link stops working.

---

## Examples

**Landing page:** `index.html`, `base.css`, `style.css`, assets. **Multi-page:** `index.html` links to `pages/*.html`, shared CSS/JS. **Dashboard:** same structure + `app.js`. **React/Vite:** create source → `npm install && npm run build` → `deploy_website(project_path="app/dist", site_name="app", entry_point="index.html")`.

---

## Server-Side Logic & Data

For forms, data storage, webhooks, or any backend logic, read `shared/19-backend.md`.

---

## Quality Checklist (Before Preview Deployment)

Each section's source file has full details.

**Tools** — Web research done. SVG logo generated. Visuals authored throughout — SVG, CSS gradients, and real assets (heroes, sections, editorial), no placeholders. Every page screenshotted via Playwright at desktop AND mobile. Issues fixed before next page.

**Tokens** (`01-design-tokens.md`) — Fluid `clamp()` type scale. 4px spacing. OKLCH colors. base.css included. Light + dark mode with toggle. The default palette when no user direction.

**Typography** (`02-typography.md`) — Distinctive loaded fonts (not system defaults). Display + body pairing, 2 fonts max. 3 text levels. ≤5 type styles/page. Display only at `--text-xl`+ (24px).

**Color** — Neutral foundation, color for emphasis. ≤2 non-neutral hues per viewport (screenshot and count). Chart colors fit art direction. Surface layers for depth. WCAG AA contrast.

**Layout** (`04-layout.md`) — Responsive 375-2560px mobile-first. Grid/clamp/container queries. Prose 65-75ch. Alpha-blended borders, nested radius, tone-matched shadows.

**Motion** (`03-motion.md`) — No instant show/hide. Golden easing curve. Scroll reveals: `opacity`/`clip-path` only. `prefers-reduced-motion` respected.

**Dashboard** (`webapp/dashboards.md`) — ONE scroll region. Sticky header/sidebar. KPIs→trends→details. `tabular-nums`. SVG logo.

**Mobile** — 375px first. Touch targets ≥44px. No hover-only UI. `:active` states. Body ≥16px. Nav adapts.

**Accessibility** (`08-standards.md`) — Semantic HTML, keyboard nav, heading hierarchy, alt text, focus rings, `aria-label` on icon buttons, skip link.

**Performance** — Images lazy-loaded with dimensions. Fonts preconnected. JS deferred. `content-visibility: auto`.

**Taste** (`05-taste.md`) — ONE primary action/screen. Empty states designed. Numbers animate. Equal polish everywhere. Distinct from last project.

**Final QA** — Every page at desktop + mobile. Cross-page consistency. Dark mode. No overflow/truncation/placeholders. SVG logo correct.

---

## Tips

Use CDN-hosted libraries. Keep the project folder clean (everything uploads). Read `shared/12-playwright-interactive.md` for all visual QA — screenshot and test locally before deploying. Never use `browser_task` for QA (too slow).

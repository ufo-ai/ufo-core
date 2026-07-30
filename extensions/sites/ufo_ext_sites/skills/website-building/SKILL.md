---
name: website-building
description: Load before writing any HTML or frontend code or building a website, web app, web game, or web experience.
---
# Website Building

Build distinctive, production-grade websites that avoid generic "AI slop" aesthetics. Every choice — type, color, motion, layout — must be intentional. Build the site in the sandbox workspace, then serve it with `deploy_website`: it comes up at `http://localhost:8000` inside the sandbox, where you validate it, and is hosted at a `site_url` the user opens.

**This skill covers everything for web projects.** When loaded via `load_skill(name="website-building")`, all files mount under `.skills/website-building/`. Read sub-files as needed based on your project type. For web applications, load the child skill: `load_skill(name="website-building/webapp")`.

Use `read` with the path relative to this skill, e.g. `shared/01-design-tokens.md`

---

## Project Type Routing

**Step 1: Identify project type and load domain-specific guidance:**

| Project Type        | Action                                          | Examples                                                                   |
| ------------------- | ----------------------------------------------- | -------------------------------------------------------------------------- |
| Informational sites | `read` `informational/informational.md`         | Personal sites, portfolios, editorial/blogs, small business, landing pages |
| Web applications    | `load_skill(name="website-building/webapp")`    | SaaS products, dashboards, admin panels, e-commerce, brand experiences     |
| Browser games       | `read` `game/game.md` + `game/game-testing.md`  | 2D Canvas games, Three.js/WebGL, HTML5 games, interactive 3D experiences   |

**Step 2: Read shared files** — read `shared/01-design-tokens.md` and `shared/02-typography.md` first (mandatory for ALL project types, including webapp). These establish the Nexus design system defaults and typography rules that apply universally. For web applications and dashboards, skip files marked with `†` below — those contain implementation details pre-configured in the fullstack template.

If the user says just "website" or "site" with no detail, ask what type or default to informational.

**Serving and sharing.** Build in the sandbox workspace, then make the site reachable. All three tools return a `http://localhost:<port>` URL reachable inside the sandbox, which is how you validate the page. `deploy_website` and `publish_website` also host that port and return `site_url` — the permanent link the user opens, gated on who may see it — so hand `site_url` back as the deliverable, with `share_file` when a downloadable copy is useful:

- `deploy_website(project_path=…, site_name=…, entry_point="index.html", visibility=…)` — serve a built static folder and host it. Re-deploy the same `site_name` to update the site behind the same link. This is the default for previews and sharing.
- `publish_website(project_path=…, dist_path=…, app_name=…, install_command=…, run_command=…, visibility=…)` — for web apps that need a build/install step or a running backend: installs dependencies, serves `dist_path`, and runs `run_command` for the backend.
- `start_server(command=…, project_path=…)` — run a dev/app server in the background during the build to test locally before serving; it hosts nothing.

Pass `visibility` (`private`, `workspace`, `public`) only when the user asked for one: a new site defaults from where it was built (a direct conversation is private, a workspace room is workspace-wide) and an existing one keeps what it has.

**If you are a website-building subagent** (no `deploy_website` in your tool set), you host nothing: bring the site up with `start_server`, validate it, `share_file` the built output, and report what you built. Hosting belongs to the conversation the member is in.

---

## Sub-File Reference

### Shared (`shared/`) — Every project

| File                                  | Covers                                                                                   | Load                       |
| ------------------------------------- | ---------------------------------------------------------------------------------------- | -------------------------- |
| `shared/01-design-tokens.md`          | Type scale, spacing, Nexus palette, base.css                                             | **Always**                 |
| `shared/02-typography.md`             | Font selection, pairing, loading, blacklist                                              | **Always**                 |
| `shared/04-layout.md`                 | Spatial composition, responsive, mobile-first                                            | **Always** †               |
| `shared/05-taste.md`                  | Skeleton loaders, empty/error states, polish                                             | **Always**                 |
| `shared/08-standards.md`              | Accessibility, performance, anti-patterns                                                | **Always**                 |
| `shared/09-technical.md`              | Project structure, sandbox, deploy, checklist                                            | **Always** †               |
| `shared/03-motion.md`                 | Scroll animations, Motion library, GSAP SVG plugins, hover/cursor                        | When animated              |
| `shared/06-css-and-tailwind.md`       | Tailwind CSS v3, shadcn/ui, modern CSS                                                   | When using Tailwind        |
| `shared/07-toolkit.md`                | CDN libraries, React, Three.js, icons, maps, SVG patterns/filters, esm.sh                | When choosing libs         |
| `shared/10-charts-and-dataviz.md`     | Chart.js, Recharts, D3, KPIs, sparklines                                                 | When data viz needed       |
| `shared/11-web-technologies.md`       | Framework versions, browser compatibility                                                | When checking compat       |
| `shared/12-playwright-interactive.md` | Persistent Playwright browser QA, screenshots, visual testing                            | When testing               |
| `shared/19-backend.md`                | FastAPI/Express/Flask servers, WebSocket, SSE, single-origin serve                       | When backend needed        |
| `shared/20-llm-api.md`                | Runtime LLM features via the Anthropic API over the sandbox egress                       | When site uses AI/LLM APIs |

All paths above are relative to this skill's mounted directory (`.skills/website-building/`).

† **Skip for webapp and dashboards** — implementation details pre-configured in the fullstack template. Design-tokens and typography are NOT skipped — they provide the authoritative design system defaults and font selection guidance for all project types.

### Domain-Specific — Load or read one

| Target                                       | When to use                                                                                |
| -------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `load_skill(name="website-building/webapp")` | SaaS, dashboard, admin, e-commerce, brand experience (child skill with fullstack template) |
| `webapp/dashboards.md`                       | Dashboard or data-dense interface (companion to the webapp skill)                          |
| `informational/informational.md`            | Personal site, portfolio, editorial, small business, landing                               |
| `game/game.md`                               | Browser game, Three.js, WebGL, interactive 3D                                              |
| `game/2d-canvas.md`                          | 2D Canvas game (companion to game.md)                                                      |
| `game/game-testing.md`                       | Any browser game — read alongside game.md                                                  |

**Interactive QA:** Read `shared/12-playwright-interactive.md` for persistent browser automation with Playwright via `js_repl` (screenshots, functional testing, visual QA). Required for game testing, useful for any complex site.

---

## Workflow

1. **Design Direction**: Clarify purpose, pick aesthetic direction
2. **Version Control**: Run `git init` in the project directory after scaffolding. Commit after each major milestone with a short message.
3. **Build**: Build the site page by page, taking screenshots via Playwright (`js_repl`) for visual QA
4. **Preview**: Commit all changes, then `deploy_website()` serves the folder, returns `http://localhost:8000` to validate it against, and hosts it at the `site_url` you hand the user

---

## Use Every Tool

- **Research first.** Search the web for reference sites, trends, and competitor examples before designing. Browse award-winning examples of the specific site type. Fetch any URLs the user provides.
- **Use real, considered visuals — generously.** Every long page needs visual rhythm, not a wall of text: heroes, section illustrations, editorial feature visuals, atmospheric backgrounds. Build that rhythm from craft you author directly — custom inline SVG (logos, marks, illustrations, patterns, filters), CSS gradients and backgrounds, and considered layout (see `shared/07-toolkit.md` for SVG patterns/filters). For photographic content, use assets the user provided or real images you fetch into the workspace; never hallucinate image URLs, and never ship lorem/placeholder text or grey placeholder boxes. When no real image is available, design the section with type, color, and SVG rather than leaving a gap. Generate a custom SVG logo for every project (see below) — SVG is for logos only unless the user specifically requests SVG output.
- **Screenshot via Playwright (complex sites only).** For multi-page sites, web apps, dashboards, and games, read `shared/12-playwright-interactive.md` to screenshot at desktop (1280px+) and mobile (375px) with `js_repl`. Skip Playwright for simple single-page static sites — see Visual QA below.
- **Write production code directly.** HTML, CSS, JS, SVG. Use bash for build tools and file processing.

---

## SVG Logo Generation

Every project gets a custom inline SVG logo. Never substitute a styled text heading.

1. **Understand the brand** — purpose, tone, one defining word
2. **Write SVG directly** — geometric shapes, letterforms, or abstract marks. One memorable shape.
3. **Principles:** Geometric/minimal (Paul Rand, Vignelli). Works at 24px and 200px. Monochrome first — add color as enhancement. Use `currentColor` for dark/light mode.
4. **Implement inline** with `aria-label`, `viewBox`, `fill="none"`, `currentColor` strokes
5. **Generate a favicon** — simplified 32x32 version if needed

For SVG animation (DrawSVG, MorphSVG), see `shared/03-motion.md`. For SVG patterns/filters, see `shared/07-toolkit.md`.

---

## Visual QA Testing Process

### When to use Playwright QA

**Skip Playwright for simple sites.** Single-page static sites do NOT need Playwright screenshots. Review the code directly, then deploy. Playwright QA adds significant time and is not worth it for simple static content.

**Use Playwright for complex sites.** Multi-page sites, web applications, dashboards, games, and any site with interactive features (forms, carts, filters, charts, drag-and-drop) MUST use Playwright QA. Read `shared/12-playwright-interactive.md` for persistent browser automation via `js_repl`.

### Playwright QA Process (complex sites only)

**Cycle:** `Build → Playwright QA → Evaluate → Fix → Repeat → Deploy when ready`

#### Stage 1: Page-by-Page QA

After building each page:

1. **Screenshot at desktop** (1280px+) and **mobile** (375px) via Playwright
2. **Evaluate critically:** Does it look professionally designed (not AI-generated)? Is typography distinctive? Is whitespace generous? Is there one clear visual hierarchy?
3. **Fix every issue before moving on.** No visual debt.

#### Stage 2: Final QA (before publishing)

1. Screenshot every page at desktop and mobile
2. Check cross-page consistency (spacing, color, type treatment)
3. Verify dark mode (screenshot both themes for homepage minimum)
4. Check interactive states: hover, focus, active, loading, empty, error
5. Cold-open first impression test: does it feel polished and intentional?

**QA failures:** text overflow, inconsistent spacing, off-token colors, missing dark mode, squished mobile, generic AI look, placeholder content, missing logo.

---

## Step 1: Art Direction — Infer Before You Ask, Ask Before You Default

Every site should have a visual identity derived from its content. **Do not skip to the Nexus fallback palette.** The Nexus palette is a last resort for when both inference and asking have failed — not a convenient default.

1. **Infer from the subject.** A coffee roaster site → earthy browns, warm cream, hand-drawn feel. A fintech dashboard → cool slate, sharp sans-serif, data-dense. A children's learning app → bright primaries, rounded type, playful motion. The content itself tells you the palette, typography, and spacing before the user says a word.
2. **Check the Art Direction tables.** Each domain file (`informational/informational.md`, `webapp/SKILL.md`, `game/game.md`) has an Art Direction table mapping site/product types to concept-driven directions and token starting points. Use these as a springboard.
3. **Derive the five pillars:** Color (warm/cool, accent from subject), Typography (serif/sans, display personality), Spacing (dense/generous), Motion (minimal/expressive), Imagery (photo/illustration/type-only).
4. **If the subject is genuinely ambiguous, ask** — "What mood are you going for?" and "Any reference sites?" One question is enough.
5. **Nexus fallback — only when inference AND asking yield nothing.** If the user has been asked and gave no direction, AND the subject matter gives no clear signal, then fall back to Nexus/Swiss defaults.

### The Fallback: Clean & Swiss (Last Resort)

When inference yielded no clear direction AND the user was asked but gave no style guidance, use defaults from `shared/01-design-tokens.md` with:

- **Typography:** Satoshi or General Sans body (Fontshare — preferred), or Inter/DM Sans. Weight contrast over font contrast. 3-4 sizes max. Keep text compact — `--text-3xl`/`--text-hero` are for informational site heroes only.
- **Color:** Nexus palette. Mono gray/black surfaces + one amber accent for CTAs only.
- **Layout:** Grid-aligned. Generous margins. Asymmetric where interesting.
- **Motion:** Minimal, functional. Smooth state transitions only.
- **Imagery:** Author clean, relevant visuals with SVG, CSS gradients, and considered layout. No stock photos, no placeholders.

### Art Direction — Avoid the AI Aesthetic

See `shared/08-standards.md` for the full anti-patterns list.

---

## Step 2: Preview

Call `deploy_website()` with the project path, site name, and entry point. It serves the folder at `http://localhost:8000` inside the sandbox — where you validate it — and hosts that port at `site_url`, the link the user opens. To update, edit the local files and re-deploy with the same `site_name`. See `shared/09-technical.md` for the exact call and examples.

After `deploy_website()` succeeds, the user-facing final answer should give `site_url` and who can open it, briefly say the website is ready, and prompt for a next action — keep iterating (re-deploy the same `site_name` to update the site behind that link), or, for an app that needs a build/install step or a backend, `publish_website`. The sandbox-local `http://localhost:8000` is yours for validation and never the user's; `share_file` the built output only when they want a copy to keep.

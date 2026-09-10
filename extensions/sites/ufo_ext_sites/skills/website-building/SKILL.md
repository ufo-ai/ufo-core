---
name: website-building
description: Load when a member asks for a page or site hosted outside ufo — a website, landing page, browser game, board, notes page, dashboard, or a build that runs its own server. Not an app or application, which is a ufo app (create-application), and not an app's homepage (application-homepage).
metadata:
  depends:
  - ufo-style
---
# Website building

Build the complete browser artifact in the sandbox, validate it, and return the hosted `site_url`.

## Choose the build shape

| Request | Action |
|---|---|
| An application's homepage — the page members open for an app on the Apps screen | Load `application-homepage`. |
| Non-interactive internal reference page or simple site | Build direct HTML, CSS, and JavaScript. |
| Public informational site | Read `informational/informational.md`. |
| Stateful app with a backend, accounts, or durable shared data | Load `website-building/webapp`. |
| Browser game | Read `game/game.md` and `game/game-testing.md`. |

For a sparse internal-page request, build a useful preview now. Infer a credible information
structure and use clearly synthetic sample data. Do not stop for content, sources, access, sections,
workflow, or format. Keep the first laptop screen dense with the most useful facts and controls;
use a compact screen title, grouped content, and no decorative empty region.

Member-supplied design direction wins. Internal pages otherwise use the house style: read
`$UFO_HOME/skills/ufo-style/references/tokens.css`, copy it into the project, and use its variables. For a
public site without member direction, derive the visual identity from its subject and use
`shared/01-design-tokens.md` and `shared/02-typography.md`.

On a house page, use an accent only as a fill, marker, or link. Normal-size text must clear 4.5:1
contrast in both colour schemes. Set secondary words in `--color-ink-soft`; `--color-mark-soft` is
only for non-text marks. Set IDs, timestamps, dates, counts, and measurements read as data in
`--font-mono`.

Read only a reference that the build needs:

| Need | Reference |
|---|---|
| Empty, loading, or error states | `shared/05-taste.md` |
| Accessibility and performance | `shared/08-standards.md` |
| Charts or dense data | `shared/10-charts-and-dataviz.md` |
| Complex or multi-page browser QA | `shared/12-playwright-interactive.md` |
| Backend behavior | `shared/19-backend.md` |
| Runtime model calls | `shared/20-llm-api.md` |

## Build and verify

Build the requested artifact directly. Do not create a separate specification, research reference
sites, initialize Git, make milestone commits, add a logo, or add unrequested features unless the
member needs one of those outputs.

Read `shared/12-playwright-interactive.md`. Run browser QA at desktop and narrow widths, check both colour
schemes, exercise every control, and check overflow and console errors. Fix each failure and run the
failed check again. Deploy only after the checks pass.

- Use the `site` collection's `deploy_website` action — `object_action(kind="site",
  action="deploy_website", input={project_path=…, site_name=…, entry_point="index.html",
  visibility=…})` — for a static folder. Reuse the requested site's name when updating it.
- Use its `publish_website` action — `object_action(kind="site", action="publish_website",
  input={project_path=…, app_name=…, install_command=…, run_command=…,
  visibility=…})` — only when the app needs a build step or backend.
- Use `start_server` only for local validation. Its URL is not a member deliverable.
- Pass `visibility` only when the member asks. Otherwise keep the conversation default.

Return the `site_url`, state who can open it, and disclose synthetic data. Offer to revise the same
site behind the same link.

## Website-building subagents

A website-building subagent uses the delegating conversation's sandbox. It validates and deploys a
static site under the requested site name, then returns the `site_url`. It never changes visibility,
reuses another site's name, or calls `share_file`. It leaves files for the parent. A backend app is
left built and validated for the parent to publish.

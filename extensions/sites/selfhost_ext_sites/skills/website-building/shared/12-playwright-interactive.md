# Playwright Interactive Skill

Use this skill when a task needs interactive browser work in a persistent `js_repl` session. Keep the Playwright handles alive across code edits, reloads, and repeated checks so iteration stays fast.

## Preconditions

- The `js_repl` tool is available automatically — it starts a persistent Node.js REPL in the sandbox on first use.
- Use `js_repl` with `reset: true` as a recovery tool, not routine cleanup. Resetting the context destroys your Playwright handles.

## Core Workflow

1. Write a brief QA inventory before testing:
   - Build the inventory from three sources: the user's requested requirements, the user-visible features or behaviors you actually implemented, and the claims you expect to make in the final response.
   - Anything that appears in any of those three sources must map to at least one QA check before signoff.
   - List the user-visible claims you intend to sign off on.
   - List every meaningful user-facing control, mode switch, or implemented interactive behavior.
   - List the state changes or view changes each control or implemented behavior can cause.
   - Use this as the shared coverage list for both functional QA and visual QA.
   - For each claim or control-state pair, note the intended functional check, the specific state where the visual check must happen, and the evidence you expect to capture.
   - If a requirement is visually central but subjective, convert it into an observable QA check instead of leaving it implicit.
   - Add at least 2 exploratory or off-happy-path scenarios that could expose fragile behavior.
2. Run the bootstrap cell once via `js_repl`.
3. Start or confirm any required dev server using `bash` with `background: true`.
4. Launch Chromium and keep reusing the same Playwright handles.
5. After each code change, reload the page.
6. Run functional QA with normal user input.
7. Run a separate visual QA pass.
8. Verify viewport fit and capture the screenshots needed to support your claims.
9. Clean up the Playwright session only when the task is actually finished.

## Bootstrap (Run Once)

Use `js_repl` to run:

```javascript
const { chromium } = await import('playwright');
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1600, height: 900 } });
const page = await context.newPage();
console.log('Playwright ready');
```

Browser handles are `const` to prevent accidentally launching duplicate Chromium instances (each one uses 200MB+). If you need to start over after an unrecoverable error, use `js_repl` with `reset: true` and re-run this bootstrap. The reset kills the old kernel and all its child processes, so nothing leaks.

## Start or Reuse Web Session

Navigate to the target URL. For local servers, prefer `127.0.0.1` over `localhost`. Use `var` for values that change across calls — the browser handles from bootstrap are `const` and persist automatically.

```javascript
var TARGET_URL = 'http://127.0.0.1:3000';
await page.goto(TARGET_URL, { waitUntil: 'domcontentloaded' });
console.log('Loaded:', await page.title());
```

## Reuse Sessions During Iteration

Keep the same session alive whenever you can.

Web renderer reload:

```javascript
for (const p of context.pages()) {
  await p.reload({ waitUntil: 'domcontentloaded' });
}
console.log('Reloaded existing tabs');
```

Default posture:

- Keep each `js_repl` call short and focused on one interaction burst.
- Use `var` for mutable top-level bindings (URLs, test data, helpers). The browser handles from bootstrap are `const` — do not redeclare them.
- If you need isolation, create a new page or a new context inside the same browser.
- Fix helper mistakes in place; do not reset the REPL unless the context is actually broken.

## Checklists

### Session Loop

- Bootstrap `js_repl` once, then keep the same Playwright handles alive across iterations.
- Launch the target runtime from the current workspace.
- Make the code change.
- Reload the page.
- Update the shared QA inventory if exploration reveals an additional control, state, or visible claim.
- Re-run functional QA.
- Re-run visual QA.
- Capture final artifacts only after the current state is the one you are evaluating.
- Execute cleanup before ending the task or leaving the session.

### Reload Decision

- After any code edit: just reload the page. The server reads files from disk — edits are visible immediately on reload.
- NEVER restart the dev server after code changes. Restarting wastes steps, causes port conflicts, and kills browser handles.
- Only restart the server if it has actually crashed (health check fails AND `lsof` shows nothing on the port).

### Functional QA

- Use real user controls for signoff: keyboard, mouse, click, touch, or equivalent Playwright input APIs.
- Verify at least one end-to-end critical flow.
- Confirm the visible result of that flow, not just internal state.
- For realtime or animation-heavy apps, verify behavior under actual interaction timing.
- Work through the shared QA inventory rather than ad hoc spot checks.
- Cover every obvious visible control at least once before signoff, not only the main happy path.
- For reversible controls or stateful toggles in the inventory, test the full cycle: initial state, changed state, and return to the initial state.
- After the scripted checks pass, do a short exploratory pass using normal input for 30-90 seconds instead of following only the intended path.
- If the exploratory pass reveals a new state, control, or claim, add it to the shared QA inventory and cover it before signoff.
- `page.evaluate(...)` may inspect or stage state, but it does not count as signoff input.

### Visual QA

- Treat visual QA as separate from functional QA.
- Use the same shared QA inventory defined before testing and updated during QA; do not start visual coverage from a different implicit list.
- Restate the user-visible claims and verify each one explicitly; do not assume a functional pass proves a visual claim.
- A user-visible claim is not signed off until it has been inspected in the specific state where it is meant to be perceived.
- Inspect the initial viewport before scrolling.
- Confirm that the initial view visibly supports the interface's primary claims; if a core promised element is not clearly perceptible there, treat that as a bug.
- Inspect all required visible regions, not just the main interaction surface.
- Inspect the states and modes already enumerated in the shared QA inventory, including at least one meaningful post-interaction state when the task is interactive.
- If motion or transitions are part of the experience, inspect at least one in-transition state in addition to the settled endpoints.
- If labels, overlays, annotations, guides, or highlights are meant to track changing content, verify that relationship after the relevant state change.
- For dynamic or interaction-dependent visuals, inspect long enough to judge stability, layering, and readability; do not rely on a single screenshot for signoff.
- For interfaces that can become denser after loading or interaction, inspect the densest realistic state you can reach during QA, not only the empty, loading, or collapsed state.
- If the product has a defined minimum supported viewport or window size, run a separate visual QA pass there; otherwise, choose a smaller but still realistic size and inspect it explicitly.
- Distinguish presence from implementation: if an intended affordance is technically there but not clearly perceptible because of weak contrast, occlusion, clipping, or instability, treat that as a visual failure.
- If any required visible region is clipped, cut off, obscured, or pushed outside the viewport in the state you are evaluating, treat that as a bug even if page-level scroll metrics appear acceptable.
- Look for clipping, overflow, distortion, layout imbalance, inconsistent spacing, alignment problems, illegible text, weak contrast, broken layering, and awkward motion states.
- Judge aesthetic quality as well as correctness. The UI should feel intentional, coherent, and visually pleasing for the task.
- Prefer viewport screenshots for signoff. Use full-page captures only as secondary debugging artifacts.
- If the full-window screenshot is not enough to judge a region confidently, capture a focused screenshot for that region.
- If motion makes a screenshot ambiguous, wait briefly for the UI to settle, then capture the image you are actually evaluating.
- Before signoff, explicitly ask: what visible part of this interface have I not yet inspected closely?
- Before signoff, explicitly ask: what visible defect would most likely embarrass this result if the user looked closely?

### Signoff

- The functional path passed with normal user input.
- Coverage is explicit against the shared QA inventory: note which requirements, implemented features, controls, states, and claims were exercised, and call out any intentional exclusions.
- The visual QA pass covered the whole relevant interface.
- Each user-visible claim has a matching visual check and artifact from the state where that claim matters.
- The viewport-fit checks passed for the intended initial view and any required minimum supported viewport or window size.
- The screenshots directly support the claims you are making.
- The required screenshots were reviewed for the relevant states and viewport or window sizes established during QA.
- The UI is not just functional; it is visually coherent and not aesthetically weak for the task.
- Functional correctness, viewport fit, and visual quality must each pass on their own; one does not imply the others.
- A short exploratory pass was completed for interactive products, and the response mentions what that pass covered.
- If screenshot review and numeric checks disagreed at any point, the discrepancy was investigated before signoff; visible clipping in screenshots is a failure to resolve, not something metrics can overrule.
- Include a brief negative confirmation of the main defect classes you checked for and did not find.
- Cleanup was executed, or you intentionally kept the session alive for further work.

## Screenshot Examples

Use `emitImage()` to return screenshots inline — the image appears directly in the tool result with no extra tool call needed.

Desktop example:

```javascript
var screenshot = await page.screenshot({ type: 'jpeg', quality: 85 });
emitImage(screenshot, 'image/jpeg');
```

Mobile example:

```javascript
var mobileCtx = await browser.newContext({
  viewport: { width: 390, height: 844 },
  isMobile: true,
  hasTouch: true,
});
var mobilePg = await mobileCtx.newPage();
await mobilePg.goto(TARGET_URL, { waitUntil: 'domcontentloaded' });
var mobileShot = await mobilePg.screenshot({ type: 'jpeg', quality: 85 });
emitImage(mobileShot, 'image/jpeg');
await mobileCtx.close();
```

`emitImage(value, mediaType?)` accepts a Buffer, Uint8Array, base64 string, or `{bytes, mimeType}` object. Up to 5 images per execution. To save screenshots to disk instead (e.g. for persistence), use `page.screenshot({ path: ... })` and `read` to view them.

## Viewport Fit Checks (Required)

Do not assume a screenshot is acceptable just because the main widget is visible. Before signoff, explicitly verify that the intended initial view matches the product requirement, using both screenshot review and numeric checks.

- Define the intended initial view before signoff. For scrollable pages, this is the above-the-fold experience. For app-like shells, games, editors, dashboards, or tools, this is the full interactive surface plus the controls and status needed to use it.
- Use screenshots as the primary evidence for fit. Numeric checks support the screenshots; they do not overrule visible clipping.
- Signoff fails if any required visible region is clipped, cut off, obscured, or pushed outside the viewport in the intended initial view, even if page-level scroll metrics appear acceptable.
- Scrolling is acceptable when the product is designed to scroll and the initial view still communicates the core experience and exposes the primary call to action or required starting context.
- For fixed-shell interfaces, scrolling is not an acceptable workaround if it is needed to reach part of the primary interactive surface or essential controls.
- Do not rely on document scroll metrics alone. Fixed-height shells, internal panes, and hidden-overflow containers can clip required UI while page-level scroll checks still look clean.
- Check region bounds, not just document bounds. Verify that each required visible region fits within the viewport in the startup state.
- Passing viewport-fit checks only proves that the intended initial view is visible without unintended clipping or scrolling. It does not prove that the UI is visually correct or aesthetically successful.

Web check:

```javascript
console.log(
  await page.evaluate(() => ({
    innerWidth: window.innerWidth,
    innerHeight: window.innerHeight,
    clientWidth: document.documentElement.clientWidth,
    clientHeight: document.documentElement.clientHeight,
    scrollWidth: document.documentElement.scrollWidth,
    scrollHeight: document.documentElement.scrollHeight,
    canScrollX: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    canScrollY: document.documentElement.scrollHeight > document.documentElement.clientHeight,
  })),
);
```

Augment the numeric check with `getBoundingClientRect()` checks for the required visible regions in your specific UI when clipping is a realistic failure mode; document-level metrics alone are not sufficient for fixed shells.

## Dev Server

Start the server **once** and leave it running for the entire session. It reads files from disk on every request — code edits appear on page reload. Never restart the server after editing code.

Do NOT use `npx serve` (the npx→sh→node process chain breaks signal propagation). Do NOT use `python3 -m http.server` (single-threaded, fails under concurrent requests). Do NOT write an inline `node -e` server (fragile shell escaping, no error handling).

**Step 1 — Start (once, at the beginning):**

```
start_server(command="serve . -l 3000 --no-clipboard --single", project_path="/workspace/my-project", port=3000)
```

`start_server` kills any existing process on the port, starts the command in the background, and polls until the port is listening. No manual health check needed. `serve` is pre-installed. `--single` enables SPA fallback (serves `index.html` for unmatched routes).

**After code edits:** Just reload the page in Playwright (`page.reload()`). Do NOT restart the server. Restarting wastes steps and causes port conflicts.

For projects with a build step (React, Vite, etc.), use the project's own dev server (`start_server(command="npm start", ...)`).

## Cleanup

Only run cleanup when the task is actually finished:

```javascript
if (context) {
  await context.close().catch(() => {});
}

if (browser) {
  await browser.close().catch(() => {});
}

console.log('Playwright session closed');
```

Closing the browser closes all its pages. To reuse Playwright after cleanup, use `js_repl` with `reset: true` and re-run the bootstrap.

## Waiting for State Changes

Never use `waitForTimeout` to wait for a condition. It always burns the full duration even when the condition is met instantly. Use event-driven waits — they return immediately when satisfied:

```javascript
// WRONG
await page.waitForTimeout(5000);

// RIGHT — wait for DOM
await page.waitForSelector('#start-btn', { state: 'visible', timeout: 10000 });

// RIGHT — wait for any JS condition
await page.waitForFunction(() => window.appReady === true, null, { timeout: 10000 });
```

Only use `waitForTimeout` when real elapsed time must pass (e.g., holding a key for 2s to test acceleration) and no programmatic condition exists.

## Common Failure Modes

- `Cannot find module 'playwright'`: run the one-time setup in the current workspace and verify the import before using `js_repl`.
- Playwright package is installed but the browser executable is missing: run `npx playwright install chromium`.
- `page.goto: net::ERR_CONNECTION_REFUSED`: the dev server may have crashed. Run `lsof -i :3000` — if nothing is listening, restart with the same `bash(background=true)` command from the Dev Server section, then `sleep 1 && curl -sf http://127.0.0.1:3000 > /dev/null && echo "ready"` before retrying navigation.
- `Identifier has already been declared`: you are redeclaring a `const` or `let`. For the browser handles (`browser`, `context`, `page`), this is intentional — just reuse them without redeclaring. For other bindings, use `var` or choose a new name. Use `js_repl` with `reset: true` only when the context is genuinely stuck.
- `js_repl` timed out or reset: rerun the bootstrap cell and recreate the session with shorter, more focused cells.

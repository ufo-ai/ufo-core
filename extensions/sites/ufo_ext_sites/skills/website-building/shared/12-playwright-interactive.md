# Playwright Interactive Skill

Use this skill when a task needs interactive browser work driven from `js_repl`. The browser runs as a background process outside the REPL, so page state survives between cells while every cell still finishes in well under a second.

## How the REPL and the Browser Fit Together

- `js_repl` runs one `node` process per call, and the call returns only when that process exits. A browser or context left open holds the process on the event loop, so the call burns its whole budget and is then killed. **Never launch a browser inside a cell and leave it open.**
- Start Chromium **once** with `bash` and `background: true`, with `--remote-debugging-port`. It outlives every cell, so the page, its URL, its scroll position, and the app state in it all persist.
- Each cell connects with `chromium.connectOverCDP(...)`, does one burst of work, then drops the connection. Over CDP, `browser.close()` closes only the Playwright connection and the contexts that connection created — the Chromium you started with `bash` keeps running.
- `js_repl` state is every block that exited 0, and each call re-runs that whole accumulated source. Keep browser cells self-contained and pass `reset: true`, so a cell never replays an earlier connect, click, or navigation.

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
2. Start or confirm any required dev server with `start_server`.
3. Start Chromium once as a background `bash` task, then confirm the debug port answers.
4. Per cell: connect over CDP, reuse the page that is already open, run one interaction burst, close the connection.
5. After each code change, reload the page — the browser and its page are still there.
6. Run functional QA with normal user input.
7. Run a separate visual QA pass.
8. Verify viewport fit and capture the screenshots your claims need. At least one screenshot must come back successfully.
9. Stop the Chromium task when the task is actually finished.

## Start Chromium (Run Once)

Call `bash` with `background: true`:

```
BROWSER="$(command -v chromium || command -v chromium-browser \
  || node -e 'console.log(require("playwright").chromium.executablePath())')"
PROFILE="$(mktemp -d /tmp/ufo-chrome-qa.XXXXXX)"
trap 'rm -r "$PROFILE"' EXIT INT TERM
CONTAINED=
if [ "$(uname -s)" = Linux ]; then CONTAINED="--no-sandbox --disable-dev-shm-usage"; fi
"$BROWSER" --headless=new --use-mock-keychain --password-store=basic $CONTAINED \
  --remote-debugging-port=9222 --remote-debugging-address=127.0.0.1 \
  --user-data-dir="$PROFILE" --no-first-run --no-default-browser-check \
  --disable-gpu about:blank
```

Never name a browser under `/Applications`: that bundle is the browser the person at this machine
uses, a headless instance of it holds their next launch, and its keychain is not reachable from
here. The profile directory is this run's own and the `trap` removes it, so no run inherits another
run's state. `$CONTAINED` is unquoted so it disappears where it does not apply: the container needs
both flags and a host must not have either.

Keep the `task`, `log`, and `stop` handles the background call hands back: `log` is where a browser that failed to start says why, and `stop` is how you end it.

Then poll until it is listening, with a normal `bash` call:

```
for _ in $(seq 30); do
  curl -sf -m 2 http://127.0.0.1:9222/json/version && break
  sleep 0.5
done
```

If the port already answers, a browser is already up — reuse it. Do not start a second one; each Chromium costs 200MB+.

In the `sandbox_chrome` deploy shape the QA browser and the member's own browser are the same Chromium on this one endpoint, by design. Nothing in this file separates them, so read every cell below as acting on a shared browser. Expect all of this: the default context can already hold the member's tabs, so take the page you opened rather than the first page in the list; the iterate cell reloads those tabs too; a screenshot can return member page content; and the cleanup command at the end of this file stops that one browser for everyone, including a browser turn running in parallel.

## Connect Per Cell

Run this through `js_repl` with `reset: true`. For local servers, prefer `127.0.0.1` over `localhost`.

```javascript
const { chromium } = await import('playwright');
const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
try {
  const context = browser.contexts()[0];
  const page = context.pages().find((p) => p.url().startsWith('http')) ?? (await context.newPage());
  await page.setViewportSize({ width: 1600, height: 900 });
  await page.goto('http://127.0.0.1:3000', { waitUntil: 'domcontentloaded' });
  console.log('Loaded:', await page.title());
} finally {
  await browser.close();
}
```

- Work in `browser.contexts()[0]` — the browser's own default context. Pages there outlive the connection, which is what lets the next cell reuse them.
- A context you create with `browser.newContext()` is discarded when you close the connection. Use one only inside a single cell (a phone-sized pass, an isolated login) and close it in that same cell.
- Close the connection in `finally`. A check that throws must still let the process exit, otherwise the call hangs to its deadline instead of reporting the failure.
- Set the viewport explicitly in each cell that judges layout. The page keeps whatever size the last cell gave it.

## Iterate Against the Same Browser

After a code edit, reload the pages that are already open:

```javascript
const { chromium } = await import('playwright');
const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
try {
  for (const p of browser.contexts()[0].pages()) {
    await p.reload({ waitUntil: 'domcontentloaded' });
    console.log('Reloaded:', p.url());
  }
} finally {
  await browser.close();
}
```

Default posture:

- Keep each `js_repl` call short and focused on one interaction burst.
- Re-derive the handles at the top of every cell. No Playwright object survives the process; the browser and its pages do.
- If you need isolation, open a new page in the default context, or a new context you close inside the same cell.
- Use `reset: true` on browser cells so nothing from an earlier cell replays.

## Checklists

### Session Loop

- Start Chromium once as a background task, then confirm the debug port answers.
- Launch the target runtime from the current workspace.
- Make the code change.
- Reload the page.
- Update the shared QA inventory if exploration reveals an additional control, state, or visible claim.
- Re-run functional QA.
- Re-run visual QA.
- Capture final artifacts only after the current state is the one you are evaluating.
- Stop the browser task before ending the task, or leave it up deliberately for further work.

### Reload Decision

- After any code edit: just reload the page. The server reads files from disk — edits are visible immediately on reload.
- NEVER restart the dev server after code changes. Restarting wastes steps and causes port conflicts.
- Only restart the server if it has actually crashed (health check fails AND `lsof` shows nothing on the port).
- Never restart Chromium to recover from a failed cell. A cell fails on its own, and the browser is a separate process that is almost certainly still healthy — confirm with `curl` before you touch it.

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
- A `js_repl` call that failed produced no evidence at all. Only a successful call that returned an image counts as a visual check.
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

- **At least one screenshot came back successfully** — a `js_repl` call with `exit_code: 0` that returned the image. A screenshot is the only evidence a visual claim may rest on.
- **With no successful screenshot, make no visual claim.** Do not say the result was verified, checked, or reviewed at any width. State plainly in the reply that visual verification was skipped, and say why: the browser never came up, the cells kept failing, or the task ran out of room.
- **A screenshot of the wrong page is not evidence.** An image of `about:blank`, or of a page you did not navigate, supports no claim at any width — re-derive the site page and capture it again.
- Every visual claim in the reply names the state and the viewport of a screenshot you actually looked at.
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
- The browser task was stopped, or you intentionally left it up for further work.

## Screenshot Examples

Use `emitImage()` to return screenshots inline — the image appears directly in the tool result with no extra tool call needed.

Both cells below take the site page with `pages().find((p) => p.url().startsWith('http'))`, the same handle the connect cell derives. Never take `pages()[0]`: the start command opens an `about:blank` tab that often sorts first, and capturing it either times out or returns an empty image that proves nothing. The desktop cell also calls `bringToFront()`, because over CDP a screenshot only lands on the active tab.

Desktop:

```javascript
const { chromium } = await import('playwright');
const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
try {
  const page = browser.contexts()[0].pages().find((p) => p.url().startsWith('http'));
  await page.bringToFront();
  await page.setViewportSize({ width: 1600, height: 900 });
  emitImage(await page.screenshot({ type: 'jpeg', quality: 85 }), 'image/jpeg');
} finally {
  await browser.close();
}
```

Phone width — the extra context lives and dies inside this one cell:

```javascript
const { chromium } = await import('playwright');
const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
try {
  const url = browser.contexts()[0].pages().find((p) => p.url().startsWith('http')).url();
  const mobileCtx = await browser.newContext({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
  });
  const mobilePg = await mobileCtx.newPage();
  await mobilePg.goto(url, { waitUntil: 'domcontentloaded' });
  emitImage(await mobilePg.screenshot({ type: 'jpeg', quality: 85 }), 'image/jpeg');
  await mobileCtx.close();
} finally {
  await browser.close();
}
```

`emitImage(value, mediaType?)` accepts a Buffer, Uint8Array, base64 string, or `{bytes, mimeType}` object. Up to 5 images per execution; an image over ~1.5 MB raises — lower the JPEG quality. To save screenshots to disk instead (e.g. for persistence), use `page.screenshot({ path: ... })` and `read` to view them.

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

Web check, inside the same connect/`finally` shape:

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

Do not discover or install a static server, or run one through `bash`. `start_server` supplies the
static server when `command` is absent. Do not write an inline server.

**Step 1 — Start (once, at the beginning):**

```
start_server(project_path="/workspace/my-project", port=3000)
```

`start_server` kills any existing process on the port, serves the static folder, and polls until the
port is listening. No manual health check is needed.

**After code edits:** Just reload the page in Playwright (`page.reload()`). Do NOT restart the server. Restarting wastes steps and causes port conflicts.

For projects with a build step (React, Vite, etc.), use the project's own dev server (`start_server(command="npm start", ...)`).

## Cleanup

The REPL has nothing to clean up — every cell already dropped its own connection. Only the browser process is left. Stop it when the task is actually finished, with the `stop` handle from the background `bash` call that started it, or with:

```
pkill -f -- '--remote-debugging-port=9222'
```

`curl -sf http://127.0.0.1:9222/json/version` must then fail. To resume browser work later, start Chromium again the same way.

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
- `connect ECONNREFUSED 127.0.0.1:9222`: Chromium is not up. Read the background task's `log`, start it again, then re-check `/json/version` before the next cell. A browser left alive by an earlier run holds the port without answering on it, so sweep with the `pkill` in Cleanup first.
- `js_repl` returned `exit_code: 124`: the cell's budget expired, the run was killed, and REPL state did not advance. The cause is nearly always a cell that opened a browser or a context and never closed it. Rewrite that cell around connect over CDP with `browser.close()` in `finally`; never re-run the same cell unchanged. Chromium is a separate process and is probably still fine — check `/json/version`.
- `page.goto: net::ERR_CONNECTION_REFUSED`: the dev server may have crashed. Run `lsof -i :3000` — if nothing is listening, restart it with the same `start_server` call from the Dev Server section, then retry navigation.
- `Identifier has already been declared`: an accumulated block already declared that binding, and every call replays the whole accumulation. Pass `reset: true` so the cell runs on its own.

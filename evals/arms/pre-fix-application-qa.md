# ufo application QA

Use this path for a direct HTML, CSS, and JavaScript application homepage. It keeps full browser
proof in a small number of model turns. Read `12-playwright-interactive.md` as well only when the
application has multi-page routing, authentication, realtime behavior, or motion that needs its
extended checks.

## Preview

Start the static folder once:

```
start_server(project_path="/workspace/my-project", port=3000)
```

Omit `command` for static files. `start_server` supplies the server, frees the port, waits for it,
and returns the URL. Do not inspect installed servers, install a package, write a server, restart
after edits, or add a manual health wait. Reload the page after an edit.

Use the Chromium endpoint at `http://127.0.0.1:9222` when it answers. Start Chromium once only when
that endpoint is absent, with `bash(background=true)`:

```
chromium --headless=new --remote-debugging-port=9222 --remote-debugging-address=127.0.0.1 \
  --user-data-dir=/tmp/ufo-chrome-qa --no-sandbox --disable-dev-shm-usage --disable-gpu about:blank
```

Every `js_repl` browser call uses `reset: true`, connects with `chromium.connectOverCDP`, and closes
that connection in `finally` with `await browser.close()` so the call ends. Never carry variables
or Playwright objects across calls: a successful call is replayed when `reset` is absent, which
makes repeated declarations fail. End every call with `console.log(JSON.stringify(out))`;
`js_repl` returns stdout, not the value of a final expression.

## Four-call protocol

Use at most four `js_repl` calls. A call is the batch. Do not make a separate smoke, setup,
contrast, keyboard, viewport, control, or single-screenshot call.

1. Call 1 opens the page and completes every functional check below. Print one result object.
2. Call 2 completes every visual check below. Call `emitImage` three times in this one call for the
   light desktop, dark desktop, and phone screenshots; `js_repl` returns up to five images. Print
   one result object.
3. Treat calls 1 and 2 as the complete defect-discovery pass. Collect every defect, then patch all
   related files once.
4. Reserve calls 3 and 4 for final repair proof. Rerun only an affected batch, functional before
   visual when both changed. Do not edit after a repair-proof call.

Do not call `js_repl` a fifth time. Do not use one call per control or one edit/check loop per
defect. If a final repair call finds a defect, do not deploy without proof that the defect is fixed.

Call 1 must:

- Load the page at desktop width and collect page and console errors.
- Set the page default timeout to 5 seconds. Locate controls by accessible role and name, and assert
  the locator count before an action instead of waiting on a guessed CSS selector.
- Exercise every accessible control with normal mouse or keyboard input.
- Prove each visible state change, including the return path for reversible controls.
- Cover the main flow and two off-happy-path actions.
- Check horizontal overflow and clipped required regions. Measure every visible text node, not
  selected classes, for contrast in light and dark modes; report every failure.
- Repeat fit and interaction checks at a realistic phone width.

Call 2 must:

- Inspect the initial, hover, focus, and one meaningful changed state for each distinct control
  style.
- Inspect light and dark desktop views and a phone view.
- Return all needed screenshots with separate `emitImage` calls inside the same `js_repl` call;
  the call must exit successfully.
- Check density, hierarchy, spacing, alignment, layering, clipping, and readability.

Keep functional, visual, and viewport checks separate in the code inside a call; combining their
execution does not let one kind of proof stand in for another. A failed browser call is no proof.
Make no visual claim without a successful screenshot. Deploy only after both calls pass.

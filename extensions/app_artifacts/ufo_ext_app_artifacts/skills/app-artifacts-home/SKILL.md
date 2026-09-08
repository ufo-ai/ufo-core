---
name: app-artifacts-home
description: Load when building or updating the Artifacts app homepage — the workspace's files and hosted sites.
metadata:
  depends:
  - ufo-style
---
# Change the Artifacts homepage

The homepage is served with the deploy: `app.tsx` in this skill is its source, built into the
page every workspace reads until it changes one. It renders the artifacts screen: the
workspace's shared files as cards, with the record panel that opens one.

To change it:

1. Copy this skill's `app.tsx`, `index.html`, and the two logo-sheet files the page imports into a
   directory of their own —
   `cp "$UFO_HOME/skills/app-artifacts-home/app.tsx" "$UFO_HOME/skills/app-artifacts-home/index.html" "$UFO_HOME/skills/app-artifacts-home/ufo-logo-ratio.pdf" "$UFO_HOME/skills/app-artifacts-home/ufo-logo-ratio-cover.png" artifacts-home/`.
2. Edit `app.tsx`. Build it from the kit, in the house style `ufo-style` states.
3. The `site` collection's `deploy_website` action (`object_action` with kind `site`) with that directory and `site_name` `artifacts-home`. It builds the page against
   the deploy's own kit and hosts what the build wrote — do not run a build yourself, and do not
   pass a `dist` directory.
4. `object_get` with an empty `ref` reads this turn's own agent; its `set_homepage` action's call template already carries the agent's name. Call it (`object_action` with kind `agent` and the name the template carries) with the site name from the deploy result, the first time only; the binding stays
   across later redeploys of the same site.

A redeploy takes the platform kit as it stands today, not the one the page was first built against.
So a rebuild is how a page picks up what the kit has since gained — and a page you never rebuild
keeps drawing against the kit of the day it was built.

To change a page this workspace has already changed, start from its own source rather than from
this skill: `object_list` kind `site` with filter `homepage_agent` set to `mine` names it;
pass that row's `ref` unchanged to `object_get`, then edit the `app.tsx` under `src` in the directory its status names, then deploy
that `src` directory. Copying this skill's `app.tsx` over it instead is how the page resets to the
screen the deploy ships.

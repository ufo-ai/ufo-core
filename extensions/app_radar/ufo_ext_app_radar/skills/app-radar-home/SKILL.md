---
name: app-radar-home
description: Load when building or updating the Radar app homepage — the digest of recent scheduled runs, each opening its full story in the drawer beside the feed.
metadata:
  depends:
  - ufo-style
---
# Change the Radar homepage

The homepage is served with the deploy: `app.tsx` in this skill is its source, built into the
page every workspace reads until it changes one. It renders the radar feed: each report's
digest, and its findings with the actors behind them.

The tour's words are `tour.md` beside `app.tsx`. Edit the tour there, quote every frontmatter
value — the page reads them with a regular expression and not a YAML parser — and keep the tour as
the last entry on the rail.

To change it:

1. Copy this skill's `app.tsx`, `index.html`, and `tour.md` into a directory of their own —
   `cp "$UFO_HOME/skills/app-radar-home/app.tsx" "$UFO_HOME/skills/app-radar-home/index.html" "$UFO_HOME/skills/app-radar-home/tour.md" radar-home/`.
2. Edit `app.tsx`. Build it from the kit, in the house style `ufo-style` states.
3. The `site` collection's `deploy_website` action (`object_action` with kind `site`) with that directory and `site_name` `radar-home`. It builds the page against
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

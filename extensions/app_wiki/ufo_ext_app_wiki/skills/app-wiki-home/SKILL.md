---
name: app-wiki-home
description: Load when building or updating the Wiki app homepage — the workspace's memory and roster.
---
# Change the Wiki homepage

The homepage is served with the deploy: `app.tsx` in this skill is its source, built into the
page every workspace reads until it changes one. It renders the wiki: the workspace's memory in
sections, its people, and the details behind each entry.

To change it:

1. Copy this skill's `app.tsx` and `index.html` into a directory of their own —
   `cp "$UFO_HOME/skills/app-wiki-home/app.tsx" "$UFO_HOME/skills/app-wiki-home/index.html" wiki-home/`.
2. Edit `app.tsx`. Import only from `ufo/kit` — React and its hooks, the portal's components,
   `SectionApp`, `mountApp`, `getJson`, `navigate`. Any other bare import fails the deploy with
   `failed to resolve import`; never install it, because a local `node_modules` makes that build
   pass and ships a second React whose hooks break in the page.
3. `deploy_website` with that directory and `site_name` `wiki-home`. It builds the page against
   the deploy's own kit and hosts what the build wrote — do not run a build yourself, and do not
   pass a `dist` directory.
4. `set_homepage` with the site name from the deploy result, the first time only; the binding stays
   across later redeploys of the same site.

A redeploy takes the platform kit as it stands today, not the one the page was first built against.
So a rebuild is how a page picks up what the kit has since gained — and a page you never rebuild
keeps drawing against the kit of the day it was built.

To change a page this workspace has already changed, start from its own source rather than from
this skill: `object_get` the site — `object_list` kind `site` with filter `homepage_agent` set to
`mine` names it — and edit the `app.tsx` under `src` in the directory its status names, then deploy
that `src` directory. Copying this skill's `app.tsx` over it instead is how the page resets to the
screen the deploy ships.

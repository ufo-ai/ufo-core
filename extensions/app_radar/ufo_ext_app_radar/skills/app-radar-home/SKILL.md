---
name: app-radar-home
description: Load when building or updating the Radar app homepage — the digest of recent scheduled runs, each opening into its full story.
metadata:
  depends:
  - app-bridge
---
# Deploy the Radar homepage

`app.tsx` in this skill is the homepage: the Radar screen as TSX, compiled in the browser by the
portal's app kit (`UfoAppKit`, loaded by `app.js` off the portal that frames the page). It renders
the radar feed: recent scheduled runs with their digests, covers, and stories, and the rebuild control
exactly as the portal draws it, and every component it composes comes off the kit, which versions
with the portal deploy. The bridge client from the `app-bridge` skill (pulled by `depends`) mounts
alongside.

Deploy or redeploy it:

1. Stage the page and the bridge client in one workspace directory:
   `mkdir -p site && cp .skills/app-radar-home/index.html .skills/app-radar-home/app.js .skills/app-radar-home/app.tsx .skills/app-bridge/bridge.js site/`
2. `deploy_website` with that `site` directory and `site_name` `radar-home`.
3. `set_homepage` with the site name from the deploy result (only needed the first time; the
   binding stays across later redeploys of the same site).

To change the page: `object_list` kind `site` with filter `homepage_agent` set to `mine` — the
one row is your homepage — then `object_get` that row's name: the read materializes the deployed
source into the sandbox directory its status names as `source_path`. Edit `app.tsx` there — plain
TSX over the `UfoAppKit` exports (React and its hooks, the portal's components, `SectionApp`,
`mountApp`, `getJson`, `navigate`) — and `deploy_website` that directory. Never edit a copy
already on disk without a fresh `object_get`: it can be stale from an earlier deploy, and
deploying it discards the member's newer page. Restage from the skill only when the member wants
the page reset to the shipped screen.

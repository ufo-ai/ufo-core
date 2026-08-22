---
name: app-artifacts-home
description: Load when building or updating the Artifacts app homepage — the workspace's files and hosted sites.
metadata:
  depends:
  - app-bridge
---
# Deploy the Artifacts homepage

`app.tsx` in this skill is the homepage: the Artifacts screen as TSX, compiled in the browser by the
portal's app kit (`UfoAppKit`, loaded by `app.js` off the portal that frames the page). It renders
the artifacts screen: the workspace's files and hosted sites
exactly as the portal draws it, and every component it composes comes off the kit, which versions
with the portal deploy. The bridge client from the `app-bridge` skill (pulled by `depends`) mounts
alongside.

Deploy or redeploy it:

1. Stage the page and the bridge client in one workspace directory:
   `mkdir -p site && cp .skills/app-artifacts-home/index.html .skills/app-artifacts-home/app.js .skills/app-artifacts-home/app.tsx .skills/app-bridge/bridge.js site/`
2. `deploy_website` with that `site` directory.
3. `set_homepage` with the site name from the deploy result (only needed the first time; the
   binding stays across later redeploys of the same site).

To change the page, edit `site/app.tsx` — it is plain TSX over the `UfoAppKit` exports (React and
its hooks, the portal's components, `SectionApp`, `mountApp`, `getJson`, `navigate`) — and
`deploy_website` again. Restage from the skill when the member wants the page reset to the shipped
screen.

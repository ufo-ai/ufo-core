---
name: app-tasks-home
description: Load when building or updating the Tasks app homepage — the workspace's scheduled tasks and source triggers.
metadata:
  depends:
  - app-bridge
---
# Deploy the Tasks homepage

`app.tsx` in this skill is the homepage: the Tasks screen as TSX, compiled in the browser by the
portal's app kit (`UfoAppKit`, loaded by `app.js` off the portal that frames the page). It renders
the tasks screen: scheduled tasks and source triggers, each opening into its record
exactly as the portal draws it, and every component it composes comes off the kit, which versions
with the portal deploy. The bridge client from the `app-bridge` skill (pulled by `depends`) mounts
alongside.

Deploy or redeploy it:

1. Stage the page and the bridge client in one workspace directory:
   `mkdir -p site && cp .skills/app-tasks-home/index.html .skills/app-tasks-home/app.js .skills/app-tasks-home/app.tsx .skills/app-bridge/bridge.js site/`
2. `deploy_website` with that `site` directory.
3. `set_homepage` with the site name from the deploy result (only needed the first time; the
   binding stays across later redeploys of the same site).

To change the page, edit `site/app.tsx` — it is plain TSX over the `UfoAppKit` exports (React and
its hooks, the portal's components, `SectionApp`, `mountApp`, `getJson`, `navigate`) — and
`deploy_website` again. Restage from the skill when the member wants the page reset to the shipped
screen.

---
name: app-chat-home
description: Load when building or updating the Chat app homepage — the workspace's chat screen with one conversation's transcript and composer, streaming replies, and the starter prompts on a fresh chat.
---
# Change the Chat homepage

The homepage is served with the deploy: `app.tsx` in this skill is its source, built into the
page every workspace reads until it changes one. It renders the chat screen: the conversation
the page was opened at — transcript, composer, streamed replies — or the start screen with the
workspace's starter prompts.

To change it:

1. `object_list` kind `site` with filter `homepage_agent` set to `mine` names it; pass that
   row's `ref` unchanged to `object_get`.
2. Edit the `app.tsx` under `src` in the directory its status names. Import only from `ufo/kit` —
   React and its hooks, the portal's components, `SectionApp`, `mountApp`, `getJson`, `navigate`.
   Any other bare import fails the deploy with `failed to resolve import`; never install it,
   because a local `node_modules` makes that build pass and ships a second React whose hooks break
   in the page.
3. The `site` collection's `deploy_website` action (`object_action` with kind `site`) with that `src` directory and `site_name` `chat-home`. It builds the page
   against the deploy's own kit and hosts what the build wrote — do not run a build yourself, and
   do not pass a `dist` directory.
4. `object_get` with an empty `ref` reads this turn's own agent; its `set_homepage` action's call template already carries the agent's name. Call it (`object_action` with kind `agent` and the name the template carries) with the site name from the deploy result, the first time only; the binding stays
   across later redeploys of the same site.

A redeploy takes the platform kit as it stands today, not the one the page was first built against.
So a rebuild is how a page picks up what the kit has since gained — and a page you never rebuild
keeps drawing against the kit of the day it was built.

Copying this skill's `app.tsx` over the project's `src/app.tsx` is how the page resets to the
screen the deploy ships.

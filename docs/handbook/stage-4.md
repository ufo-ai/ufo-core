# Surface mounting and authenticated channel setup  `stage-4`

This stage sets up the service’s user-facing and operator-facing “doors” after the system already knows its configuration. It is part of normal running, not startup or shutdown. Its job is to make sure people, apps, and browsers can reach the right surface, prove who they are when needed, and exchange messages or files safely.

The member chat surfaces are the conversation doors. The web portal, Slack, iMessage, and command-line chat each receive messages in their own format, check identity or signatures, turn the input into a shared conversation request, and send the agent’s reply back to the same channel. The web side also serves chat pages, settings, admin views, community skill browsing, and form actions.

The operator, site, and file-serving surfaces are the viewing and inspection doors. They serve signed file downloads, read-only debugger pages, workspace memory views, and hosted site preview frames. Together, these parts act like a guarded lobby: every route opens a useful room, but only after checking the visitor’s right to enter.

## Sub-stages

- [Member chat surfaces](stage-4.1.md) `stage-4.1` — 6 files
- [Operator, site, and file-serving surfaces](stage-4.2.md) `stage-4.2` — 4 files

## 📊 State Registers Touched

- `reg-extension-registry` — The loaded list of installed extensions, packs, routes, tools, skills, jobs, credentials, backends, and migrations.
- `reg-database-store` — The shared database connection and tables where workspaces, users, agents, turns, files, jobs, costs, and extension data are saved.
- `reg-workspace-principals` — The current workspace, members, agents, controlling users, and ownership identities used to decide who is acting.
- `reg-session-auth` — The login sessions, signed tokens, protected links, callback state, and request identities proving who a visitor or service is.
- `reg-visibility-boundaries` — The saved rules for who may see each conversation, agent, transcript, source, memory, artifact, or workspace object.
- `reg-surface-installations` — The saved bindings for web, Slack, iMessage, CLI, hosted sites, and other surfaces that connect outside channels to workspaces and agents.
- `reg-conversation-records` — The durable conversation rows that remember where a conversation came from, which agent owns it, its audience, title, sandbox, and current metadata.
- `reg-live-stream-state` — The live update stream that broadcasts turn progress, tool activity, subagent activity, cancellations, and final replies to connected clients.
- `reg-file-blob-store` — The shared byte storage for uploads, generated files, previews, media, and other raw data, separated by workspace or deployment scope.
- `reg-artifact-registry` — The saved list of files deliberately shared with users, including ownership, access checks, preview metadata, and download links.
- `reg-browser-site-runtime` — The shared browser sessions, hosted preview servers, public site links, and ownership records used to browse, test, and publish websites.
- `reg-memory-store` — The durable store of remembered facts and memory-search results that can be written, deduplicated, recalled, and shown later.
- `reg-extension-object-slots` — The extension-owned object and conversation-panel data, such as artifacts, sources, tasks, sites, automations, and custom workspace objects.
- `reg-pending-human-interactions` — The durable pending questions, credential-collection prompts, setup requests, and checklist-style waits that tools create and surfaces later resolve.
- `reg-surface-delivery-state` — The outbound reply/writeback bookkeeping for external chat surfaces, including delivery targets, external message identifiers, and exactly-once final reply status.

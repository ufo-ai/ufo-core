# Browser automation and website hosting work  `stage-11`

This stage is where the system works with the web as both a visitor and a publisher. It is mainly used during the main work loop, after the agent has started and needs to browse sites, test web pages, or share a site it has built. One half is the browser control center. It opens Chrome or a hosted browser, talks to it through an automation channel, watches pages load, reads what is on the screen, and turns requests like “click this,” “type here,” or “upload this file” into real browser actions. It also hides where the browser is actually running, whether locally, in a sandbox, or through a remote service.

The other half is the website preview and hosting path. After files are created, it can build the site, run a server inside the sandbox, expose that server through a safe public link, and record who owns and may access it. Together, these parts let the agent inspect existing websites, create new ones, test them in a real browser, and share them safely.

## Sub-stages

- [Chrome DevTools and browser session control](stage-11.1.md) `stage-11.1` — 24 files
- [Hosted sites and preview delivery](stage-11.2.md) `stage-11.2` — 6 files

## 📊 State Registers Touched

- `reg-session-auth` — The login sessions, signed tokens, protected links, callback state, and request identities proving who a visitor or service is.
- `reg-model-usage-accounting` — The recorded token, image, video, embedding, sandbox, egress, and cost usage used for billing and audit trails.
- `reg-tool-execution-context` — The per-turn but shared rulebook passed through tools, saying who the tool acts for, what files, accounts, sandboxes, and subagents it may use.
- `reg-sandbox-runtime` — The remembered sandbox handles, workspace directories, terminals, command sessions, ports, and cleanup state used for safe code execution.
- `reg-egress-policy` — The network access rules and proxy authorization state that decide what sandboxed code may contact outside the system.
- `reg-file-blob-store` — The shared byte storage for uploads, generated files, previews, media, and other raw data, separated by workspace or deployment scope.
- `reg-artifact-registry` — The saved list of files deliberately shared with users, including ownership, access checks, preview metadata, and download links.
- `reg-browser-site-runtime` — The shared browser sessions, hosted preview servers, public site links, and ownership records used to browse, test, and publish websites.
- `reg-extension-object-slots` — The extension-owned object and conversation-panel data, such as artifacts, sources, tasks, sites, automations, and custom workspace objects.
- `reg-conversation-workspace-files` — The mutable per-conversation working file tree that tools, skills, document automation, site building, artifacts, and cleanup read or modify before changes are snapshotted or shared.

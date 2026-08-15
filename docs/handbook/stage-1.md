# Deployment preflight and schema migration entrypoints  `stage-1`

This stage happens before the system serves users. It is the preflight checklist for deployment: make sure the database layout, safety rules, optional add-ons, and sandbox environment all match what the running code expects.

The control database and Alembic entrypoints are the front door. They connect the migration tool, Alembic, to the project and prepare shared database rules, including row-level security, which keeps each workspace’s rows separate. The core migration groups are the main instruction book for the database. They create the first tables, then gradually add support for conversations, agents, sources, billing, scheduling, audit logs, artifacts, members, permissions, and newer product behavior while keeping older stored data usable.

Extension-owned migration trees are separate instruction books for optional features such as objectives, research, sample notes, scheduled-task pauses, hosted sites, skill creation, and web chat metadata. Finally, the sandbox validation scripts check the isolated execution environment: one verifies the sandbox image recipe, and the other proves encrypted web traffic goes through the required proxy. Together, these checks reduce surprises when the service starts.

## Sub-stages

- [Control database and Alembic preflight entrypoints](stage-1.1.md) `stage-1.1` — 4 files
- [Core baseline schema and early conversation migrations](stage-1.2.md) `stage-1.2` — 14 files
- [Core source, ledger, scheduling, and inbound-message migrations](stage-1.3.md) `stage-1.3` — 19 files
- [Core workspace, grant, source, page, and agent migrations](stage-1.4.md) `stage-1.4` — 19 files
- [Core audience, control, connection, admission, and audit migrations](stage-1.5.md) `stage-1.5` — 14 files
- [Core usage, artifact, member, conversation, and billing migrations](stage-1.6.md) `stage-1.6` — 17 files
- [Extension-owned migration trees](stage-1.7.md) `stage-1.7` — 10 files
- [Sandbox deployment validation scripts](stage-1.8.md) `stage-1.8` — 2 files

## 📊 State Registers Touched

- `reg-effective-config` — The deployment’s active settings, such as enabled services, limits, paths, providers, and safety options.
- `reg-database-schema` — The agreed database layout and migration version that all stored records must follow.
- `reg-sandbox-workspace` — The per-conversation isolated workbench, including its handle, files, execution backend, and recorded file changes.
- `reg-sandbox-network-policy` — The per-run rules and proxy state that decide what sandboxed code may reach on the internet and when secrets may be injected.
- `reg-audit-access-log` — Durable audit records such as transcript-access events and security-relevant reads or administrative actions.
- `reg-proposal-state` — Saved proposed changes and their pending, approved, or rejected decision state.
- `reg-sample-notes-store` — Durable sample-note extension records and note metadata used as optional workspace content across setup, tools, retrieval, and persistence paths.
- `reg-web-chat-metadata` — Extension-owned metadata for web-chat conversations or sessions, such as visitor/channel details and routing/display data beyond the core transcript.

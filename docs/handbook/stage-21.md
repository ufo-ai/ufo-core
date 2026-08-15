# Cross-cutting persistence, schema, storage, and durable records  `stage-21` (cross-cutting infrastructure)

This stage is the system’s long-term memory and filing system. It is shared behind-the-scenes support, used during startup, upgrades, normal work, and recovery after restarts. Its job is to make sure important records are stored in agreed shapes and can still be read as the code changes.

The core persistence layer provides the basic plumbing: database connections, table blueprints, migrations, workspace separation, and blob storage for large files. On top of that, transcript storage saves conversation history safely, including protections so an old copy cannot overwrite a newer one.

Several parts are upgrade paths. Coding review and source-trigger migrations move older review records into a shared trigger model. Index and evaluation migrations add search storage and test inbox/calendar tables. Memory migrations build the tables for remembered facts, page snapshots, source links, confidence, audiences, and fast inventory lookup.

Finally, durable feature stores keep records for monitors, objectives, and hosted sites. Together these pieces act like labeled drawers in one filing room, so every feature can save, find, upgrade, and protect its data consistently.

## Sub-stages

- [Core persistence infrastructure and shared schema](stage-21.1.md) `stage-21.1` — 5 files
- [Durable conversation transcript storage](stage-21.2.md) `stage-21.2` — 2 files
- [Coding review and source-trigger migrations](stage-21.3.md) `stage-21.3` — 6 files
- [Index and evaluation-environment migrations](stage-21.4.md) `stage-21.4` — 3 files
- [Memory item schema and inventory migrations](stage-21.5.md) `stage-21.5` — 6 files
- [Memory page, provenance, and source-partition migrations](stage-21.6.md) `stage-21.6` — 6 files
- [Durable feature records and hosted-site stores](stage-21.7.md) `stage-21.7` — 4 files

## 📊 State Registers Touched

- `reg-database-schema` — The agreed database layout and migration version that all stored records must follow.
- `reg-onboarding-claims` — Temporary signup, email-verification, invitation, and workspace-claim records used while a user joins.
- `reg-workspace-membership` — The roster of workspaces, members, admins, seats, and which people belong where.
- `reg-agent-directory` — The saved assistants in each workspace and their settings, such as model behavior, sandbox size, and internet access.
- `reg-object-catalog` — The shared catalog of manageable workspace object types and the rules for who may view or change them.
- `reg-extension-store` — Per-workspace extension pins and extension-owned settings saved so enabled add-ons survive restarts.
- `reg-credential-vault` — Encrypted workspace secrets and short-lived brokered credentials used without exposing raw secrets to tools.
- `reg-connection-grants` — Connected third-party accounts and permissions saying which agents may use which external accounts.
- `reg-surface-installations` — Mappings from outside entry points like Slack, web, terminal, and hosted surfaces into workspaces, members, and agents.
- `reg-inbound-message-log` — Incoming external messages saved until they are safely rendered, deduplicated, and admitted into a conversation.
- `reg-conversation-transcript` — The durable history of conversations, messages, speakers, titles, context, and results.
- `reg-turn-queue` — The durable waiting line and status record for each unit of agent work, from queued to running to finished or failed.
- `reg-runtime-fleet` — The sign-in sheet of running server and worker instances used to detect active work, crashes, and abandoned turns.
- `reg-scheduled-automation` — Future and repeating tasks, pauses, wakeups, and their last-run state for long-running automation.
- `reg-sandbox-workspace` — The per-conversation isolated workbench, including its handle, files, execution backend, and recorded file changes.
- `reg-subagent-state` — The helper-agent catalog and child-conversation handoff state used when one agent delegates work to another.
- `reg-source-sync-state` — External source records, sync cursors, saved pages, deletion markers, and retry or backoff status.
- `reg-search-index` — The searchable text chunks, embeddings, and backend index state used to find relevant documents and pages.
- `reg-memory-store` — Saved facts, memory pages, confidence, provenance, and audience labels that let the assistant remember useful context.
- `reg-automation-objectives` — Durable objectives, steps, monitors, checks, blockers, and evidence for work that continues across turns.
- `reg-accounting-ledger` — Usage, cost, spend limits, prepaid balances, billing exports, and price versions for workspace spending.
- `reg-artifact-store` — Generated files, previews, blobs, and signed shared-artifact links that outlive a single message.
- `reg-hosted-site-state` — Hosted sandbox sites, their public addresses, generations, ports, visibility, and unhosting status.
- `reg-database-connection-pool` — Per-process database engine/session pools and workspace-scoped connection context used by servers, workers, migrations, and persistence code.
- `reg-audit-access-log` — Durable audit records such as transcript-access events and security-relevant reads or administrative actions.
- `reg-proposal-state` — Saved proposed changes and their pending, approved, or rejected decision state.
- `reg-user-skill-library` — Persisted user- or agent-authored skills and reusable skill metadata loaded into the agent’s available capabilities.
- `reg-coding-review-state` — Coding review inboxes, review runs, source bindings, and conversation links used by the code-review workflow.
- `reg-research-observation-log` — Saved web research source observations and evidence records tied to conversations for later citation and display.
- `reg-eval-environment-fixtures` — Evaluation-environment fake inbox, calendar, and connector fixture records used for deterministic test workflows.
- `reg-source-access-grants` — Durable permissions mapping agents to the synced sources they are allowed to read or search, separate from third-party account connection grants.
- `reg-source-trigger-state` — Durable source-change trigger subscriptions and wakeup markers that connect synced-record updates to conversations, reviews, monitors, or automation resumes.
- `reg-sample-notes-store` — Durable sample-note extension records and note metadata used as optional workspace content across setup, tools, retrieval, and persistence paths.
- `reg-web-chat-metadata` — Extension-owned metadata for web-chat conversations or sessions, such as visitor/channel details and routing/display data beyond the core transcript.
- `reg-scratchpad-notebooks` — Persisted scratchpad or notebook content that agents reuse across turns separately from saved skills and ordinary conversation files.

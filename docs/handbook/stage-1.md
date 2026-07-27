# Deployment preparation, sandbox image validation, and schema upgrade  `stage-1`

This stage happens before the service starts its normal work. It is the system’s pre-flight check and upgrade step. First, the deployment bundle and sandbox gates prepare what will be shipped, build the sandbox “workroom” image, and prove that workspace storage and HTTPS proxy traffic work in a real sandbox.

Next, the database migration entry points run Alembic, the tool that applies database changes in order. The baseline migrations create the first core records, such as workspaces, people, agents, conversations, turns, and charges. Other core migrations add the storage the running system will later rely on: credentials and grants for permissions, conversation surfaces like Slack and web chat, inbound-message queues, turn links, context, attribution, sources and pages, extension storage, ledgers, spending caps, exports, seats, runtime workers, and scheduled tasks.

Finally, installed extensions bring their own migrations. These add extension-specific tables, such as evaluation fixtures, sample notes, or user-created skills. Together, these parts make sure the shipped image is valid and the database is ready before live traffic begins.

## Sub-stages

- [Deployment bundle and sandbox validation gates](stage-1.1.md) `stage-1.1` — 4 files
- [Database migration entrypoints and baseline core schema](stage-1.2.md) `stage-1.2` — 4 files
- [Core credentials, grants, and agent access migrations](stage-1.3.md) `stage-1.3` — 4 files
- [Core conversation surfaces and inbound-message migrations](stage-1.4.md) `stage-1.4` — 9 files
- [Core turn linkage, context, and attribution migrations](stage-1.5.md) `stage-1.5` — 7 files
- [Core source, page, and extension-storage migrations](stage-1.6.md) `stage-1.6` — 8 files
- [Core ledger, spending, export, and seat migrations](stage-1.7.md) `stage-1.7` — 9 files
- [Core runtime fleet and scheduled-work migrations](stage-1.8.md) `stage-1.8` — 9 files
- [Installed extension schema migrations](stage-1.9.md) `stage-1.9` — 3 files

## 📊 State Registers Touched

- `reg-deployment-schema-version` — The shared record of which database and extension upgrades have already been applied.
- `reg-effective-config` — The running service’s merged settings, such as required keys, enabled backends, safety options, and service behavior.
- `reg-extension-pack-manifest` — The installed pack and extension menu that says what tools, routes, jobs, skills, credentials, and backends exist.
- `reg-workspace-tenant-record` — The customer workspace record that all users, conversations, data, tools, and billing are kept under.
- `reg-agent-profile-settings` — The saved assistant settings for a workspace, including which agent is used and what it is allowed to do.
- `reg-surface-installation-binding` — The stored connection between outside channels like Slack, web chat, or terminal clients and an internal workspace conversation.
- `reg-inbound-message-dedup` — The durable inbox and duplicate-detection state for messages arriving from external surfaces.
- `reg-conversation-thread-state` — The saved conversation identity and history that let the system continue the same thread over time.
- `reg-turn-record-lifecycle` — The durable record of each unit of agent work, including who started it, its status, parent links, and final result.
- `reg-runtime-presence` — The live roll-call of server and worker processes used to recover abandoned work safely.
- `reg-seat-entitlement` — The workspace membership and seat-limit state that decides which people the agent may serve.
- `reg-spend-ledger-billing` — The shared usage ledger, prices, caps, exports, and billing records used to track and limit spending.
- `reg-credential-secret-store` — The encrypted store of workspace secrets and credential kinds used without exposing raw tokens to agents.
- `reg-authorization-grants` — The saved permissions showing which user-approved outside accounts an agent may use.
- `reg-sandbox-workspace-handle` — The saved handle and lease for the safe workspace where a conversation can run commands and keep files.
- `reg-blob-storage-backend` — The shared large-file storage used for workspace files, transcripts, source snapshots, and artifacts.
- `reg-network-egress-policy` — The allow-or-deny rules for outbound network calls, including when approved secrets may be attached.
- `reg-source-page-sync-state` — The saved sources, pages, sync cursors, deletion markers, and retry state for imported external content.
- `reg-search-index-memory-graph` — The shared recall stores for searchable chunks, remembered facts, memory pages, and knowledge-graph links.
- `reg-scheduled-task-state` — The saved clock-based tasks, waits, pauses, last-run markers, and expiration times used to wake work later.
- `reg-extension-object-store` — The durable per-workspace storage and named objects that extensions expose or update over time.
- `reg-row-level-security-context` — The database safety context that keeps each workspace’s rows separated even when code uses shared tables.
- `reg-database-connection-pool` — The shared database engine/session pool and transaction lifecycle used by migrations, request handlers, workers, and shutdown cleanup.
- `reg-evaluation-fixture-state` — Durable fake inbox, calendar, code-search, and other test-fixture records used by evaluation jobs without touching real providers.
- `reg-sandbox-image-artifact-state` — The validated sandbox/workroom image identity and preflight health result used later when creating sandbox runtimes.
- `reg-human-approval-proposal-state` — Durable proposal records for changes or actions that must be reviewed, accepted, rejected, or superseded before taking effect.
- `reg-user-created-skill-store` — Persistent user-authored skill definitions and metadata that are loaded into the skill library and made available to prompts and tools across turns.

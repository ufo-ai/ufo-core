# Deployment Preparation and Database Upgrade  `stage-1`

This stage happens during deployment, before the service starts handling normal user traffic. It is like checking a building, updating its floor plan, and making sure every room is usable before opening the doors. First, the runtime readiness checks confirm the control database exists and matches expectations, build or validate the sandbox image where user code will run, and test that secure proxy traffic works with the right certificate.

Next, the Alembic harness runs database migrations. Alembic is a tool that applies database changes in order, so stored data keeps matching the code. The core migrations create and upgrade the main tables for workspaces, agents, conversations, turns, credentials, proposals, memory links, sources, pages, ledgers, runtimes, exports, scheduled jobs, grants, connections, and audits. These changes make the system’s main records traceable, permission-aware, and ready for newer features.

Finally, extension migrations prepare add-on storage, such as memory records, searchable text chunks, test email and calendar data, hosted sites, notes, skills, and web metadata. Together, these steps make both the service runtime and its databases safe to use.

## Sub-stages

- [Deployment Runtime Readiness Checks](stage-1.1.md) `stage-1.1` — 3 files
- [Core Alembic Harness and Foundational Schema](stage-1.2.md) `stage-1.2` — 7 files
- [Core Conversation, Surface, Inbound, and Artifact Migrations](stage-1.3.md) `stage-1.3` — 19 files
- [Core Source, Page, and Content Memory Migrations](stage-1.4.md) `stage-1.4` — 11 files
- [Core Ledger, Runtime, Sandbox Usage, and Export Migrations](stage-1.5.md) `stage-1.5` — 10 files
- [Core Scheduling, Jobs, and Task Admission Migrations](stage-1.6.md) `stage-1.6` — 8 files
- [Core Workspace, Agent, Grants, Connections, and Audit Migrations](stage-1.7.md) `stage-1.7` — 11 files
- [Memory Extension Migrations](stage-1.8.md) `stage-1.8` — 12 files
- [Non-Memory Extension Migrations](stage-1.9.md) `stage-1.9` — 8 files

## 📊 State Registers Touched

- `reg-effective-configuration` — The final startup settings that decide how the service, security, sandbox, proxy, and enabled packs should behave.
- `reg-durable-store-schema` — The shared database layout and migration version that all services rely on when saving or reading system records.
- `reg-runtime-fleet-state` — The live fleet heartbeat table that says which runtime processes are alive and what stranded work they may own.
- `reg-usage-accounting-ledger` — The spending ledger that records model usage, egress usage, prices, caps, billing exports, and payment-related state.
- `reg-credential-secret-store` — The encrypted store of workspace and connector secrets, plus the requests that say which secrets a tool or proxy may reveal.
- `reg-access-grants` — The saved approvals that say which workspace, member, agent, account, source, or conversation is allowed to use a protected resource.
- `reg-source-page-sync-state` — The source records, page bodies, cursors, revisions, deletion markers, and replay feed used to keep external content synchronized.
- `reg-memory-search-index` — The long-term memory and searchable text index that stores remembered facts, chunks, embeddings, and recall results.
- `reg-sandbox-workspace-state` — The remembered sandbox workspace for a conversation, including its backend handle, files, runtime folder, and cleanup ownership.
- `reg-egress-network-state` — The controlled network exit state, including proxy configuration, certificates, allowed destinations, metering, and last-moment credential injection.
- `reg-prompt-governance-state` — The prompt templates, rendered fingerprints, proposals, evaluations, approvals, and replacement decisions used to change agent behavior safely.
- `reg-database-connection-pool` — The live database engine/session pool and transaction doorway shared by migrations, request handlers, workers, and shutdown cleanup.
- `reg-sandbox-image-cache` — The built or validated sandbox runtime image/backend artifact that later sandbox launches reuse.
- `reg-extension-kv-store` — The generic per-workspace extension JSON store used by add-ons to persist small feature-specific state outside core tables.
- `reg-evaluation-fixture-state` — The persistent fake-world data for evaluations, including test email, calendar, code-search, and other deterministic benchmark records.
- `reg-extension-note-state` — Durable note records stored by note/sample extensions and reused by extension tools across startup and normal workspace operation.
- `reg-web-metadata-store` — Persistent web metadata/cache records captured by web/search/browser-related extensions for later lookup, indexing, or display.

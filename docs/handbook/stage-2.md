# Database preparation, migrations, and rollback paths  `stage-2`

This stage is the database “renovation crew” that runs before normal serving, or when an operator upgrades or rolls back the system. It uses Alembic, a tool that applies ordered database changes, so the stored layout matches the code that will use it.

The runners and entrypoints connect Alembic to the right database and make sure the ground is safe before traffic starts. The core migrations then build and refine the main shared records: workspaces, members, agents, conversations, turns, permissions, runtime workers, sources, pages, scheduled tasks, usage ledgers, billing limits, agents, connections, and memory transitions. Other core groups add support for user-facing surfaces like Slack and web, inbound messages, artifacts, transcript access, sandboxes, subagents, and partial replies.

Extension migrations add shelves for optional features. Coding, source triggers, sweep, and pauses support developer automation. Memory, indexing, and research migrations store searchable chunks, facts, pages, and observed web sources. User-facing extension migrations prepare monitors, objectives, sites, skills, sample notes, evaluation data, and web chat data. Together, these scripts let the database move forward safely, or back out changes when needed.

## Sub-stages

- [Database preparation runners and Alembic entrypoints](stage-2.1.md) `stage-2.1` — 2 files
- [Core baseline identity, grants, and runtime schema migrations](stage-2.2.md) `stage-2.2` — 12 files
- [Core conversation, turn, sandbox, and subagent migrations](stage-2.3.md) `stage-2.3` — 16 files
- [Core surface, inbound message, audience, and artifact migrations](stage-2.4.md) `stage-2.4` — 15 files
- [Core source and page content migrations](stage-2.5.md) `stage-2.5` — 11 files
- [Core scheduled task and job admission migrations](stage-2.6.md) `stage-2.6` — 10 files
- [Core ledger, billing, quota, and balance migrations](stage-2.7.md) `stage-2.7` — 15 files
- [Core agent, connection, egress, and memory-transition migrations](stage-2.8.md) `stage-2.8` — 13 files
- [Coding, source-trigger, sweep, and pause extension migrations](stage-2.9.md) `stage-2.9` — 8 files
- [Memory, indexing, and research extension migrations](stage-2.10.md) `stage-2.10` — 15 files
- [User-facing feature extension migrations](stage-2.11.md) `stage-2.11` — 12 files

## 📊 State Registers Touched

- `reg-extension-catalog` — The shared list of installed extensions and the capabilities they registered for this deployment.
- `reg-database-schema` — The durable database layout and migration version that every runtime component must agree on.
- `reg-db-session` — The active database connection, transaction, and workspace-safe persistence context used while work is running.
- `reg-object-store` — The durable named workspace objects owned by extensions, with their names, data, permissions, and owner routing.
- `reg-usage-ledger` — The shared cost and usage records for model calls, tools, sandboxes, connectors, network use, and generated media.
- `reg-prompt-governance` — The saved prompt proposals, approval status, evaluation results, and safety checks for changing agent instructions.
- `reg-extension-kv-store` — Private per-workspace JSON/key-value state owned by extensions for setup, feature bookkeeping, and small durable extension data that is not a user-visible object.
- `reg-coding-review-state` — The coding extension’s durable review inbox and review-run records, including links to the agent, conversation, and turn that handle review automation.
- `reg-evaluation-fixture-store` — Controlled non-production fixture data such as fake email inboxes, calendars, sample notes, and deterministic connector data used by demos, evaluations, and conformance tests.
- `reg-db-engine-pool` — The process-wide database engine, DSN binding, and connection pool from which per-request sessions and migration runners obtain connections.

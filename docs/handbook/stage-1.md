# Deployment preflight and schema upgrade  `stage-1`

This stage happens during deployment, before the system starts serving users. Its job is to make sure the database and sandbox infrastructure are ready, so new code does not run against missing tables, old fields, or broken storage.

The Alembic runner is the upgrade engine. Alembic is a database change tool that applies numbered steps in order. The core migrations first lay down the basic records for workspaces, members, agents, conversations, turns, identities, proposals, and nested work. Other core migrations add the records needed for credentials, permissions, Slack and web entry points, imported content, incoming messages, spending ledgers, seats, runtime workers, scheduled jobs, and fast lookup indexes.

Extension migrations prepare extra feature areas. They create storage for evaluations, indexing and search, knowledge graphs, sample data, skill creation, and the memory system’s facts and pages.

Finally, the sandbox safety gates test the outside pieces that code will depend on at runtime. They start real sandboxes, verify workspace storage can be mounted and written to, and confirm secure proxy access works. Together, these checks turn an upgraded deployment into a safe starting line.

## Sub-stages

- [Alembic runner and core schema baseline](stage-1.1.md) `stage-1.1` — 4 files
- [Core identity, credentials, grants, and surfaces](stage-1.2.md) `stage-1.2` — 6 files
- [Core source and content-ingestion schema](stage-1.3.md) `stage-1.3` — 5 files
- [Core turn, conversation, and inbound-message schema](stage-1.4.md) `stage-1.4` — 11 files
- [Core spending, ledger, and seat schema](stage-1.5.md) `stage-1.5` — 9 files
- [Core runtime fleet, scheduling, and job schema](stage-1.6.md) `stage-1.6` — 8 files
- [Extension platform and non-memory extension schemas](stage-1.7.md) `stage-1.7` — 7 files
- [Memory extension schema migrations](stage-1.8.md) `stage-1.8` — 6 files
- [Sandbox deployment safety gates](stage-1.9.md) `stage-1.9` — 2 files

## 📊 State Registers Touched

- `reg-workspaces-members-agents` — The durable records for workspaces, their members, and the agents that can act for them.
- `reg-connected-credentials` — The encrypted store of outside account connections and secrets that tools and sync jobs may use safely.
- `reg-permission-grants` — The permission ledger saying which agent may use which connected account or provider access.
- `reg-extension-state-store` — The per-workspace key-value storage area where extensions keep their own persistent settings and data.
- `reg-search-index-state` — The searchable content indexes, chunks, embeddings, and search backend choices used for recall and source replay.
- `reg-sandbox-session-policy` — The safe execution room state: sandbox handles, mounted workspace files, storage access, and restrictions.
- `reg-egress-proxy-state` — The controlled network gateway state that decides which outside sites sandboxed work may contact and how usage is counted.
- `reg-conversation-transcript` — The saved conversation thread, messages, transcript edits, and compacted summaries.
- `reg-turn-queue-run-state` — The durable state of each agent turn, including whether it is queued, claimed, running, parked, finished, failed, or cancelled.
- `reg-inbound-surface-state` — The stored inbound messages and surface delivery keys that connect Slack, web, terminal, and other fronts to conversations.
- `reg-source-sync-state` — The remembered external sources, imported pages, raw bodies, change records, cursors, errors, and deletion markers.
- `reg-memory-store` — The workspace memory facts, episodes, ownership labels, confidence, and consolidation indexes used for recall.
- `reg-knowledge-graph` — The stored people, companies, things, and relationships used as structured background knowledge.
- `reg-scheduled-task-calendar` — The durable calendar of one-time and repeating tasks that workers can safely claim and run later.
- `reg-runtime-instance-fleet` — The heartbeat records for live worker and runtime processes, used to detect dead workers and clean up abandoned work.
- `reg-accounting-ledger-spend` — The usage ledger, spend caps, exports, and cost totals for models, egress, and other billable work.
- `reg-seat-billing-state` — The workspace seat limits, granted seats, included seats, and external billing integration state.
- `reg-prompt-governance` — The saved prompt digests, prompt-change proposals, approvals, and experiment evidence that control instruction changes.
- `reg-subagent-work-tree` — The parent-child turn links, delegated work state, and message flow between main agents and subagents.
- `reg-schema-migration-version` — The Alembic/schema version state recording which core and extension migrations have been applied before runtime uses the database.
- `reg-evaluation-replay-state` — Durable evaluation corpora, replay runs, scores, and judgments used by self-improvement and conformance workflows beyond prompt approval records.
- `reg-action-proposal-state` — Durable non-governance proposals created by agents or tools for later user/operator review, approval, rejection, or commit.

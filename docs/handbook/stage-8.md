# Turn claiming and runtime assembly  `stage-8`

This stage is the handoff from “there is work waiting” to “one worker is ready to run it.” A worker first claims one queued turn, like taking the next ticket from a shared counter so two workers do not do the same job. It then builds the full per-turn environment: the conversation workspace, the user or team member, the chosen agent, the transcript so far, available tools, model choices, credentials, billing information, cleanup tasks, and extension hooks.

Two support stages supply the main parts of this setup. Sandbox workspace and egress setup creates the safe room where commands can run, with mounted files, controlled network access, and optional browser support. Skill and per-turn toolbox loading fills that room with the right working kit: selected skills, registered tools, and limited access to files, accounts, search, sources, subagents, and background jobs. Together, these pieces turn a queued request into a contained, well-equipped runtime where the agent can start the main work safely and with the right context.

## Sub-stages

- [Sandbox workspace and egress setup](stage-8.1.md) `stage-8.1` — 5 files
- [Skill and per-turn toolbox loading](stage-8.2.md) `stage-8.2` — 4 files

## 📊 State Registers Touched

- `reg-workspace-context` — The current workspace and member context that keeps every request acting inside the right tenant boundary.
- `reg-workspaces-members-agents` — The durable records for workspaces, their members, and the agents that can act for them.
- `reg-connected-credentials` — The encrypted store of outside account connections and secrets that tools and sync jobs may use safely.
- `reg-permission-grants` — The permission ledger saying which agent may use which connected account or provider access.
- `reg-extension-pack-lock` — The saved choice of active packs and installed extensions for a workspace.
- `reg-extension-state-store` — The per-workspace key-value storage area where extensions keep their own persistent settings and data.
- `reg-capability-registry` — The loaded menu of extension-provided routes, tools, skills, hooks, jobs, credentials, models, and search backends.
- `reg-tool-catalog` — The shared list of tools the agent may call, including their names, inputs, safety labels, and handlers.
- `reg-model-catalog` — The model switchboard that maps model names to providers, credentials, request formats, and prices.
- `reg-connector-catalog` — The shared directory of external service connectors and broker-backed provider access.
- `reg-search-index-state` — The searchable content indexes, chunks, embeddings, and search backend choices used for recall and source replay.
- `reg-sandbox-session-policy` — The safe execution room state: sandbox handles, mounted workspace files, storage access, and restrictions.
- `reg-egress-proxy-state` — The controlled network gateway state that decides which outside sites sandboxed work may contact and how usage is counted.
- `reg-browser-session` — The browser connection state used when a turn needs a Chrome endpoint or computer-use actions.
- `reg-conversation-transcript` — The saved conversation thread, messages, transcript edits, and compacted summaries.
- `reg-turn-queue-run-state` — The durable state of each agent turn, including whether it is queued, claimed, running, parked, finished, failed, or cancelled.
- `reg-artifact-blob-store` — The shared file and blob storage used for uploaded content, generated artifacts, and token-protected downloads.
- `reg-source-sync-state` — The remembered external sources, imported pages, raw bodies, change records, cursors, errors, and deletion markers.
- `reg-memory-store` — The workspace memory facts, episodes, ownership labels, confidence, and consolidation indexes used for recall.
- `reg-scheduled-task-calendar` — The durable calendar of one-time and repeating tasks that workers can safely claim and run later.
- `reg-runtime-instance-fleet` — The heartbeat records for live worker and runtime processes, used to detect dead workers and clean up abandoned work.
- `reg-accounting-ledger-spend` — The usage ledger, spend caps, exports, and cost totals for models, egress, and other billable work.
- `reg-seat-billing-state` — The workspace seat limits, granted seats, included seats, and external billing integration state.
- `reg-observability-trace-state` — The logs, metrics, traces, traceparent links, and redaction rules used to monitor work safely.
- `reg-workspace-object-catalog` — The named workspace objects and object-type registry used to list, inspect, validate, change, or delete stored things.
- `reg-subagent-work-tree` — The parent-child turn links, delegated work state, and message flow between main agents and subagents.
- `reg-database-connection-pool` — The process-wide database engine/session factory and connection pool used by request handlers, workers, schedulers, and persistence code.
- `reg-turn-cleanup-finalizers` — Per-turn cleanup/finalizer stack for resources acquired during runtime assembly and tool execution so result commit or shutdown can release them safely.
- `reg-agent-todo-goal-state` — The agent-maintained goal/todo checklist state that tools update and later prompt/runtime assembly can reload as working context.
- `reg-adapter-implementation-registry` — Process-wide mapping from configured backend/provider names to implementation adapters for Redis hubs, sandboxes, browsers, models, search, sources, and related services.

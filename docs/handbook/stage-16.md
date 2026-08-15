# External connectors, account brokering, and source synchronization  `stage-16`

This stage is shared behind-the-scenes support for safely working with outside services. It has two big jobs: let agents use third-party tools without seeing private tokens, and keep outside business records synced into the system.

The core connector and account grant layer is the guarded front desk. It tracks which user connected which account, what an agent may use, and how access can be shared or revoked. Composio and Pipedream are trusted middlemen that provide consent links, tool catalogs, remote tool execution, and proxied web requests, so secrets stay outside the sandbox. The MCP and evaluation pieces make the same connector path work for remote tool servers and predictable test connectors.

The source synchronization framework is the intake engine. It defines how connectors fetch records, remember progress with cursors, retry failures, store pages, handle deletes, and publish changes. The many source connectors are translators for specific services: productivity tools, support systems, CRMs, ads platforms, finance, HR, recruiting, and operations apps. Each reads allowed data from its service and reshapes it into one common format.

## Sub-stages

- [Core connector brokering and account grants](stage-16.1.md) `stage-16.1` — 9 files
- [Composio brokered tool integration](stage-16.2.md) `stage-16.2` — 7 files
- [Pipedream brokered tool integration](stage-16.3.md) `stage-16.3` — 5 files
- [Source synchronization framework and registry](stage-16.4.md) `stage-16.4` — 9 files
- [Productivity, collaboration, and work-management source connectors](stage-16.5.md) `stage-16.5` — 20 files
- [Customer support, CRM, marketing, ads, and observability source connectors](stage-16.6.md) `stage-16.6` — 15 files
- [Finance, billing, HR, recruiting, and operations source connectors](stage-16.7.md) `stage-16.7` — 13 files

## 📊 State Registers Touched

- `reg-effective-config` — The deployment’s active settings, such as enabled services, limits, paths, providers, and safety options.
- `reg-object-catalog` — The shared catalog of manageable workspace object types and the rules for who may view or change them.
- `reg-extension-pack-registry` — The selected packs and loaded extensions that decide which features, tools, routes, jobs, and backends exist.
- `reg-tool-catalog` — The runtime menu of tools the agent may call, including built-ins and extension-provided tools.
- `reg-credential-vault` — Encrypted workspace secrets and short-lived brokered credentials used without exposing raw secrets to tools.
- `reg-connection-grants` — Connected third-party accounts and permissions saying which agents may use which external accounts.
- `reg-background-job-schedule` — The durable list of background jobs and dispatch rules used to retry and run work outside user requests.
- `reg-sandbox-network-policy` — The per-run rules and proxy state that decide what sandboxed code may reach on the internet and when secrets may be injected.
- `reg-source-sync-state` — External source records, sync cursors, saved pages, deletion markers, and retry or backoff status.
- `reg-search-index` — The searchable text chunks, embeddings, and backend index state used to find relevant documents and pages.
- `reg-human-interaction-requests` — Pending user questions, approval prompts, and credential-request prompts created by tools and resumed through surfaces.
- `reg-coding-review-state` — Coding review inboxes, review runs, source bindings, and conversation links used by the code-review workflow.
- `reg-eval-environment-fixtures` — Evaluation-environment fake inbox, calendar, and connector fixture records used for deterministic test workflows.
- `reg-execution-scope-context` — Per-request, per-job, and per-turn scoped context carrying the active workspace, member, agent, turn, permissions, credentials, billing, and service handles through core code.
- `reg-connector-auth-flow-state` — Short-lived OAuth, consent-link, CSRF/state, and callback progress for connecting external accounts before durable connections and grants exist.
- `reg-provider-client-pools` — Per-process reusable transport/client state for external providers such as model APIs, search and embedding services, connector brokers, browser providers, billing services, and related retry or throttle windows.
- `reg-source-access-grants` — Durable permissions mapping agents to the synced sources they are allowed to read or search, separate from third-party account connection grants.
- `reg-source-trigger-state` — Durable source-change trigger subscriptions and wakeup markers that connect synced-record updates to conversations, reviews, monitors, or automation resumes.

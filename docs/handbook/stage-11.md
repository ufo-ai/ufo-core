# External connector, research, and service-tool execution  `stage-11`

This stage is the system’s controlled doorway to the outside world. It is shared behind-the-scenes support used while an agent is doing its main work and needs something beyond the local sandbox, such as calling Gmail or Slack, searching the web, fetching a page, or using a service-specific tool. Its main safety job is to let those calls happen without handing secret keys or account tokens to the agent.

One part is the connector broker system. Think of it like a reception desk for outside apps. It helps agents discover available services, check which actions they offer, confirm which connected account may be used, and run actions through brokers such as Composio, Pipedream, keyed API connectors, or MCP servers. The broker talks to the real service and returns only the approved result or files.

The other part is the research desk. It gives agents safe tools for web search, page reading, Perplexity-backed answers, broader multi-topic research, and source tracking. Together, these parts let agents use external knowledge and services while keeping access controlled and auditable.

## Sub-stages

- [Brokered connector discovery and action calls](stage-11.1.md) `stage-11.1` — 15 files
- [Research and web search tools](stage-11.2.md) `stage-11.2` — 6 files

## 📊 State Registers Touched

- `reg-extension-registry` — The live catalog of installed extensions and the capabilities each one has registered.
- `reg-acting-authority` — The shared record of whether work is acting as a member, an agent, or only the workspace.
- `reg-credentials-connections` — The stored secrets, connected accounts, grants, and refreshable permissions used to call outside services.
- `reg-egress-policy` — The network access rules that decide which outside hosts sandboxed or connector code may contact.
- `reg-billing-ledger` — The shared meter and wallet state for usage costs, spend caps, prepaid balances, and billing identity.
- `reg-tool-catalog` — The shared catalog of tools and the policies that decide which tools may run with which permissions.
- `reg-connector-brokers` — The shared catalog and runtime state for service connectors, MCP servers, broker accounts, and approved actions.
- `reg-source-index-memory` — The saved external pages, search chunks, embeddings, memories, and recall indexes used as workspace knowledge.
- `reg-extension-store` — The per-workspace storage area where extensions keep their own durable settings and small JSON records.
- `reg-egress-policy-cache-generation` — Workspace egress-rule generation counters and proxy cache-invalidation state for refreshed network access decisions.
- `reg-evaluation-fixture-state` — Mutable fake-service data for evaluation runs, such as test mail, calendar, Drive, GitHub, and business-tool records.

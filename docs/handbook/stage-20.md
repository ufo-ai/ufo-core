# Security, credentials, sessions, and access boundaries  `stage-20` (cross-cutting infrastructure)

This stage is shared safety plumbing that runs behind many parts of the system. It is not one single work loop. Instead, it sets the rules for who may act, what they may see, which secrets they may use, and which outside services they may contact.

Signed tokens, sessions, and protected links create “sealed notes” that prove a login, file link, sandbox port link, or surface identifier was made by the system and was not changed. Workspace, member, and visibility boundaries decide the scope of work: which workspace is active, which agent or member is involved, and who can read each object or task. Credential storage, declaration, and injection keeps API keys and tokens encrypted, labels which secrets exist, and only passes approved secrets into sandboxes or connectors. Network egress policy and proxy authorization checks outbound sandbox traffic, allowing only approved destinations and credentials. Operator access and uploaded media safety protects internal tools and rejects unsafe image previews. Together, these parts act like locks, badges, and guards around the system.

## Sub-stages

- [Signed tokens, sessions, and protected links](stage-20.1.md) `stage-20.1` — 5 files
- [Workspace, member, and visibility boundaries](stage-20.2.md) `stage-20.2` — 7 files
- [Credential storage, declaration, and injection](stage-20.3.md) `stage-20.3` — 4 files
- [Network egress policy and proxy authorization](stage-20.4.md) `stage-20.4` — 3 files
- [Operator access and uploaded media safety](stage-20.5.md) `stage-20.5` — 2 files

## 📊 State Registers Touched

- `reg-effective-config` — The merged deployment settings that tell the process how to run, which services to use, and which safety options are enabled.
- `reg-database-store` — The shared database connection and tables where workspaces, users, agents, turns, files, jobs, costs, and extension data are saved.
- `reg-workspace-principals` — The current workspace, members, agents, controlling users, and ownership identities used to decide who is acting.
- `reg-session-auth` — The login sessions, signed tokens, protected links, callback state, and request identities proving who a visitor or service is.
- `reg-visibility-boundaries` — The saved rules for who may see each conversation, agent, transcript, source, memory, artifact, or workspace object.
- `reg-credential-vault` — The encrypted store of API keys, OAuth tokens, and other secrets that can be injected only into approved places.
- `reg-connection-grants` — The saved account connections and per-agent permissions that say which outside accounts an agent may use.
- `reg-agent-settings` — The durable settings for each agent, including model choice, reasoning mode, internet access, sandbox size, tools, setup needs, icon, and visibility.
- `reg-surface-installations` — The saved bindings for web, Slack, iMessage, CLI, hosted sites, and other surfaces that connect outside channels to workspaces and agents.
- `reg-conversation-records` — The durable conversation rows that remember where a conversation came from, which agent owns it, its audience, title, sandbox, and current metadata.
- `reg-transcript-history` — The saved conversation transcript and compaction snapshots that all surfaces, workers, prompts, and recovery logic read and update.
- `reg-spend-controls` — The workspace spending caps, prepaid balances, top-up settings, BYOK flags, and billing export state.
- `reg-tool-execution-context` — The per-turn but shared rulebook passed through tools, saying who the tool acts for, what files, accounts, sandboxes, and subagents it may use.
- `reg-sandbox-runtime` — The remembered sandbox handles, workspace directories, terminals, command sessions, ports, and cleanup state used for safe code execution.
- `reg-egress-policy` — The network access rules and proxy authorization state that decide what sandboxed code may contact outside the system.
- `reg-file-blob-store` — The shared byte storage for uploads, generated files, previews, media, and other raw data, separated by workspace or deployment scope.
- `reg-artifact-registry` — The saved list of files deliberately shared with users, including ownership, access checks, preview metadata, and download links.
- `reg-browser-site-runtime` — The shared browser sessions, hosted preview servers, public site links, and ownership records used to browse, test, and publish websites.
- `reg-observability-traces` — The shared trace, metric, log, and traceparent information that lets operators connect startup, turns, tools, subagents, and billing events.
- `reg-seat-entitlements` — Workspace seat limits, included-seat counts, and seated-member marks that gate access and billing entitlement decisions.
- `reg-turn-surface-context` — Durable per-turn inbound context such as speaker, on-behalf-of member, timezone, original surface metadata, and connection-authorization status carried from admission into prompting, execution, and delivery.

# Core database migrations  `stage-1.1`

This stage is the database’s instruction manual and upgrade path. It works mostly during setup and system upgrades, not during the main conversation loop. Its job is to make sure the platform’s stored data has the right shape before other code depends on it.

The schema definitions describe the shared “turn” records and the database tables, then connect them to Alembic, the tool that applies database changes in order. The foundational migrations create the first filing cabinets: workspaces, members, agents, conversations, turns, credentials, proposals, and extension data. Conversation and inbound-delivery migrations add ways to store messages from Slack, the web, and other entry points, including pending replies and shared artifacts.

Other groups expand what the system can remember. Turn and runtime migrations track retries, subagent links, ownership, worker processes, and durable sandboxes. Scheduling migrations store recurring background tasks and their lifecycle. Billing migrations add ledgers, spend limits, exports, and seat counts. Source and memory migrations store synced content and clean up old knowledge data. Grant, connection, agent, and workspace-control migrations record permissions, external accounts, internet access, and workspace defaults.

## Sub-stages

- [Schema definitions and Alembic wiring](stage-1.1.1.md) `stage-1.1.1` — 3 files
- [Foundational platform tables and early extensions](stage-1.1.2.md) `stage-1.1.2` — 5 files
- [Conversation surfaces and inbound delivery migrations](stage-1.1.3.md) `stage-1.1.3` — 8 files
- [Turn execution, context, and runtime fleet migrations](stage-1.1.4.md) `stage-1.1.4` — 11 files
- [Scheduling and background task lifecycle migrations](stage-1.1.5.md) `stage-1.1.5` — 6 files
- [Billing, ledger, spend, and seat migrations](stage-1.1.6.md) `stage-1.1.6` — 9 files
- [Sources, pages, and memory-data migrations](stage-1.1.7.md) `stage-1.1.7` — 11 files
- [Grants, connections, agents, and workspace control migrations](stage-1.1.8.md) `stage-1.1.8` — 6 files

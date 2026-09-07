# Deployment packaging and schema evolution  `stage-1`

This stage happens before the service starts normal work, during build, deployment, or upgrade. It is like preparing a shop before opening: pack the tools, check the safety room, and rearrange the storage shelves so the new software knows where everything belongs.

The deployment bundle tools create a repeatable package with the right configuration, extensions, runtime code, and sandbox client. The sandbox scripts build the protected place where untrusted code can run, then test that network traffic is forced through the proxy and blocked when it should be. The Alembic wiring connects the app to Alembic, the database upgrade tool, so schema changes run in order.

Most of the stage is migration files. Core migrations build and reshape the main database tables for workspaces, conversations, turns, agents, billing, sources, artifacts, permissions, scheduling, and routing. Timestamped core migrations continue that evolution for newer product features. Extension migrations do the same for optional features such as notifications, memory, indexing, hosted sites, research, coding workflows, objectives, skills, enrichment, and test environments. Together they let old deployments safely become new ones.

## Sub-stages

- [Deployment bundle, sandbox validation, and Alembic runtime wiring](stage-1.1.md) `stage-1.1` — 4 files
- [Core migrations 0001-0022: foundational schema and early platform tables](stage-1.2.md) `stage-1.2` — 19 files
- [Core migrations 0023-0041: turns, inbound messages, sources, seats, and ledger export](stage-1.3.md) `stage-1.3` — 19 files
- [Core migrations 0042-0060: permissions, pages, agents, and scheduling refinements](stage-1.4.md) `stage-1.4` — 19 files
- [Core migrations 0061-0081: artifacts, transcript access, connection sharing, and agent runtime settings](stage-1.5.md) `stage-1.5` — 19 files
- [Core migrations 0082-0100: membership, billing, app agents, and conversation productization](stage-1.6.md) `stage-1.6` — 19 files
- [Core migrations 0101-0113 and legacy branch migrations](stage-1.7.md) `stage-1.7` — 16 files
- [Timestamped core migrations: surface routing, app archival, sources, and object journals](stage-1.8.md) `stage-1.8` — 12 files
- [Timestamped core migrations: app provisioning, billing identity, allowlists, and artifact content](stage-1.9.md) `stage-1.9` — 13 files
- [Timestamped core migrations: retries, connections, media fixes, and conversation labels](stage-1.10.md) `stage-1.10` — 16 files
- [Extension migrations: notifications, monitors, report digests, research, scheduled pauses, and web chat](stage-1.11.md) `stage-1.11` — 11 files
- [Extension migrations: hosted sites and source triggers](stage-1.12.md) `stage-1.12` — 11 files
- [Extension migrations: memory and default indexing](stage-1.13.md) `stage-1.13` — 18 files
- [Extension migrations: enrichment, evaluation environment, and sample storage](stage-1.14.md) `stage-1.14` — 4 files
- [Extension migrations: coding workflows, objectives, and user-created skills](stage-1.15.md) `stage-1.15` — 10 files

## 📊 State Registers Touched

- `reg-effective-config` — The merged service settings that tell the process how this deployment should run.
- `reg-schema-version` — The database upgrade position that says which schema changes have already been applied.
- `reg-sandbox-handles` — The remembered sandbox workspaces and conversation sandbox handles used to resume or clean up execution.
- `reg-egress-sandbox-policy` — The shared rules for sandbox network access, proxy behavior, internet permission, and exposed secrets.
- `reg-extension-state-store` — Generic per-workspace extension-owned durable key/value or configuration state not covered by a named core store.
- `reg-proposal-review-state` — Durable reviewable-change proposals with source/target digests, creator, approval state, and publication lifecycle outside the self-improvement prompt-promotion loop.

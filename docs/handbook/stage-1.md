# Deployment preparation and database migration  `stage-1`

This stage gets the system ready before normal serving begins. It has two jobs: pack the software so it can run in the right environment, and update the database so stored data matches what the current code expects.

The runtime bundle part is like packing a travel kit. It gathers the runtime code, fixed settings, allowed extensions, sandbox client program, and Docker instructions into a deployable bundle. It also checks the sandbox setup, which is the protected place where risky or untrusted code can run. One script makes sure hosted and local sandbox images are built from the same recipe. Another performs a safety check by starting a temporary sandbox and testing proxy certificate behavior before deployment.

The migration part renovates the database. Alembic, a tool that applies database changes step by step, connects to storage and runs upgrades or rollbacks in order. Core migrations create and reshape the main tables. Extension migrations do the same for optional features. Together, they preserve old data while preparing storage for the new code.

## Sub-stages

- [Runtime bundle and sandbox image gates](stage-1.1.md) `stage-1.1` — 4 files
- [Core and extension schema upgrades or rollbacks](stage-1.2.md) `stage-1.2` — 200 files

## 📊 State Registers Touched

- `reg-database-schema-version` — The current shape and migration level of the database, so old stored data can be upgraded and all code agrees on table layouts.
- `reg-effective-config` — The merged deployment settings that tell the service how to start, where storage is, and which runtime options are enabled.
- `reg-sandbox-handles` — The durable handles and leases that let conversations reconnect to their sandbox, files, ports, hosted previews, and work directories.
- `reg-egress-proxy-policy` — The network access rules and proxy state that decide which outside hosts can be reached and when secrets may be attached.
- `reg-deployment-artifact-state` — The built runtime bundle and sandbox image/client artifact state, including image recipe/version alignment and deployment preflight results used by startup and sandbox execution.
- `reg-update-check-state` — Cached software/version update-check results, last-check timestamps, retry timing, and dismissed or shown update notices for CLI and service maintenance flows.

# Public SDK, Protocol Types, and Extension Interfaces  `stage-22` (cross-cutting infrastructure)

This stage is shared behind-the-scenes support. It defines the public promises that extensions and internal parts depend on, but it does not start the system or run the main work loop. Think of it as the rulebook and front desk for anyone adding new abilities.

The core protocol files define common data shapes and interfaces, such as AI messages, tool calls, browser access, memory results, search, source syncing, and extension manifests. The SDK runtime and declaration facades give extension authors safe public ways to describe tools, jobs, skills, web routes, logs, and run context. The provider, source, model, and sandbox facades expose stable entry points for connectors, search indexes, models, browser sessions, and controlled execution. The identity, auth, governance, and accounting facades publish approved types for credentials, tokens, permissions, audiences, seats, and spending records. The hub, object, listing, and surface facades collect public records for catalogs, objects, paging, and user-interface integrations. Finally, package marker files set clean Python import boundaries. Together, these pieces keep the SDK stable, safe, and understandable while the internals evolve.

## Sub-stages

- [Core Public Protocol and Provider Contracts](stage-22.1.md) `stage-22.1` — 8 files
- [SDK Extension Runtime and Declaration Facades](stage-22.2.md) `stage-22.2` — 8 files
- [SDK Provider, Source, Model, and Sandbox Facades](stage-22.3.md) `stage-22.3` — 9 files
- [SDK Identity, Auth, Governance, and Accounting Facades](stage-22.4.md) `stage-22.4` — 8 files
- [SDK Hub, Object, Listing, and Surface Facades](stage-22.5.md) `stage-22.5` — 4 files
- [Package Boundary Marker Modules](stage-22.6.md) `stage-22.6` — 5 files

## 📊 State Registers Touched

- `reg-tool-catalog` — The shared catalog of tool names, descriptions, schemas, and implementations that the model is allowed to call.
- `reg-model-provider-catalog` — The shared list of AI models and providers, including how to call them, what keys they need, and what features they support.
- `reg-memory-search-index` — The long-term memory and searchable text index that stores remembered facts, chunks, embeddings, and recall results.
- `reg-browser-session-state` — The live browser-control session state used to click, read pages, download files, recover sessions, and route sandbox browser links.
- `reg-skill-inventory` — The declared and user-created skill inventory, including skill ownership, dependencies, files, and the load order copied into a sandbox.
- `reg-live-update-hub` — The short-lived stream of progress updates, tool activity, costs, final answers, and Redis fan-out frames for live viewers.
- `reg-workspace-object-state` — The shared shelf of workspace objects, their types, names, owners, permissions, listings, and object-specific actions.
- `reg-search-provider-registry` — The live registry of web-search, page-fetch, embedding/search provider backends and their capabilities used by research, recall, and indexing code.
- `reg-source-connector-registry` — The registered source backend implementations, credential requirements, sync hooks, and capability metadata used to instantiate external content synchronization.

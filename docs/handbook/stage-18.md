# Extension SDK, Public Contracts, and Generated Protocol Types  `stage-18` (cross-cutting infrastructure)

This stage is the shared public “contract layer” of the system. It is not where the app starts, runs a conversation, or shuts down. Instead, it defines the stable names, data shapes, and import paths that other code depends on, especially extension authors and generated network code.

One part sets core runtime rules: safe extension context objects, object naming, and input/output contracts. Several SDK facade parts act like clean front doors under ufo.sdk, exposing connectors, credentials, HTTP helpers, jobs, manifests, sources, surfaces, tools, models, search, memory, browser, terminal, accounting, observability, permissions, identity, seats, skills, and other runtime features without exposing private internals. The iMessage parts define both the local provider contract and generated Protocol Buffer types, which are machine-readable message formats used for chats, messages, attachments, events, groups, polls, and service calls. The Google API proto files and package marker files make those generated imports work. Finally, ufo/sdk/__init__.py marks the SDK folder as importable, anchoring this public API surface.

## Sub-stages

- [Core Runtime Extension Contracts and Object Naming](stage-18.1.md) `stage-18.1` — 6 files
- [Public SDK Extension Authoring Facades](stage-18.2.md) `stage-18.2` — 9 files
- [Public SDK Runtime Capability Facades](stage-18.3.md) `stage-18.3` — 14 files
- [Public SDK Governance, Identity, and Domain Facades](stage-18.4.md) `stage-18.4` — 11 files
- [iMessage Provider and Generated Service APIs](stage-18.5.md) `stage-18.5` — 9 files
- [iMessage Generated Domain Protocol Types](stage-18.6.md) `stage-18.6` — 7 files
- [Generated Proto Import Boundaries and Google API Annotation Protos](stage-18.7.md) `stage-18.7` — 8 files

## Files in this stage

### Extension SDK, Public Contracts, and Generated Protocol Types
### `core/src/ufo/sdk/__init__.py`

`other` · `import/package discovery`

This is the package doorway for the `ufo.sdk` part of the project. In Python, an `__init__.py` file tells the interpreter that a folder should be treated as an importable package. That means other parts of the system can write imports that start with `ufo.sdk` and then reach the actual modules inside this folder.

At the moment, this file is empty. It does not create objects, run setup steps, expose shortcuts, or change behavior when the package is imported. Its value is structural: it gives the SDK area a clear place in the project’s module tree. You can think of it like a label on a drawer. The label does not contain the tools, but it makes the drawer recognizable and reachable.

Without this file, depending on the Python version and packaging setup, imports involving `ufo.sdk` might fail or behave less predictably. Keeping it here makes the package boundary explicit.

## 📊 State Registers Touched

- `reg-selected-pack-services` — The chosen product pack and the shared service objects it wires up for the rest of the app.
- `reg-extension-registry` — The loaded set of extensions and the routes, tools, hooks, jobs, skills, agents, and backends they contribute.
- `reg-extension-install-store` — The saved record of which extensions are installed, removed, or holding extension-specific data.
- `reg-credential-connections` — The encrypted accounts, secrets, connection grants, and credential fulfillments that let agents use outside services safely.
- `reg-model-catalog-providers` — The shared catalog of available AI models, their prices and limits, and the provider clients used to call them.
- `reg-search-provider-catalog` — The common search and page-fetching service state used when the system needs outside web information.
- `reg-memory-index-state` — The stored knowledge, embeddings, chunks, and memory indexes that agents can search later.
- `reg-source-config-sync-state` — The configured external sources plus their sync progress, errors, backoff, ownership, and access grants.
- `reg-tool-catalog-allowlists` — The shared list of tools and actions an agent may see or run, including extension tools and sandbox bridge tools.
- `reg-skill-prompt-library` — The reusable instructions, skills, prompt rules, and agent setup guidance loaded into turns.
- `reg-presentation-slots` — The shared conversation display slots for showing artifacts, sources, tasks, sites, automations, image previews, and other side-panel content.
- `reg-object-journal` — The shared naming and change history for workspace objects such as tasks, prompts, skills, monitors, memories, and reports.

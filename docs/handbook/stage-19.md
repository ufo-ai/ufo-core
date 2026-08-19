# Extension SDK, object system, slots, and public contracts  `stage-19` (cross-cutting infrastructure)

This stage is shared support for extensions, the add-ons that let the platform grow without letting outside code poke at private internals. It is like a set of guarded doors and standard forms. Extension authors get stable public imports, while the core system keeps control over safety, naming, permissions, and data shape.

The workspace object system gives the platform a common way to name, list, open, and describe things such as agents, conversations, memories, extensions, and workspace members. Internal extension contracts define what an extension can declare in its manifest, what limited services it receives while running, and what extra conversation panels it may add. Validation checks make sure those panels and slot data are well formed and safe to show.

The SDK facade modules are the public front counter. Some expose platform services such as accounting, credentials, auth proxy, grants, logging, and visibility labels. Others expose extension-building tools for manifests, tools, skills, jobs, web routes, context, and scheduled triggers. Integration facades cover browsers, terminals, connectors, sources, search, indexing, memory, models, sandboxes, and user surfaces.

## Sub-stages

- [Workspace object system and built-in object kinds](stage-19.1.md) `stage-19.1` — 9 files
- [Internal extension contracts, runtime context, and conversation slots](stage-19.2.md) `stage-19.2` — 3 files
- [SDK cross-cutting platform service facades](stage-19.3.md) `stage-19.3` — 14 files
- [SDK extension authoring and execution facades](stage-19.4.md) `stage-19.4` — 7 files
- [SDK integration, data access, and surface facades](stage-19.5.md) `stage-19.5` — 11 files

## 📊 State Registers Touched

- `reg-extension-registry` — The loaded list of installed extensions, packs, routes, tools, skills, jobs, credentials, backends, and migrations.
- `reg-workspace-principals` — The current workspace, members, agents, controlling users, and ownership identities used to decide who is acting.
- `reg-visibility-boundaries` — The saved rules for who may see each conversation, agent, transcript, source, memory, artifact, or workspace object.
- `reg-credential-vault` — The encrypted store of API keys, OAuth tokens, and other secrets that can be injected only into approved places.
- `reg-agent-settings` — The durable settings for each agent, including model choice, reasoning mode, internet access, sandbox size, tools, setup needs, icon, and visibility.
- `reg-model-catalog` — The shared directory of available AI models, their providers, limits, prices, key requirements, and routing behavior.
- `reg-model-usage-accounting` — The recorded token, image, video, embedding, sandbox, egress, and cost usage used for billing and audit trails.
- `reg-spend-controls` — The workspace spending caps, prepaid balances, top-up settings, BYOK flags, and billing export state.
- `reg-tool-catalog` — The shared catalog of tools the model can call, including built-in tools, extension tools, connector tools, and their safety labels.
- `reg-source-sync-catalog` — The saved catalog of external sources, pages, sync cursors, deletion marks, retry backoff, and indexing needs.
- `reg-search-index` — The shared keyword and embedding indexes that let conversations, tools, and background jobs find relevant stored documents.
- `reg-memory-store` — The durable store of remembered facts and memory-search results that can be written, deduplicated, recalled, and shown later.
- `reg-scheduled-jobs` — The durable background job and scheduled task state used for recurring work, wakeups, retries, monitors, billing, and offline evaluation.
- `reg-extension-object-slots` — The extension-owned object and conversation-panel data, such as artifacts, sources, tasks, sites, automations, and custom workspace objects.
- `reg-extension-workflow-state` — Extension-owned durable workflow records that are not just UI slots, such as code-review inboxes, evaluation runs, objectives, pauses, briefs, notes, monitors, triggers, and web-chat state.
- `reg-extension-kv-store` — Per-workspace extension key/value JSON and setup marker state saved outside core schemas, used by extension setup, runtime behavior, jobs, and cleanup migrations.

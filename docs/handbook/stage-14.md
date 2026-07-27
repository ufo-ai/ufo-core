# Source synchronization and page lifecycle  `stage-14`

This stage is the system’s import pipeline. It runs during background or user-requested syncs, when UFO contacts outside services, fetches their records, and turns them into stored pages that can later be searched, remembered, or used for alerts.

At the center is the core synchronization framework. It defines the common connector contract, walks through large services in pieces, follows page-by-page web API results, retries requests, and records what changed or disappeared. The registry and source tools keep the catalogue of available connections, name each connected account consistently, expose synced pages as read-only workspace pages, and notify subscribed conversations when pages change. Special sources, such as YC and evaluation connectors, plug into the same path.

Around this engine are many connector families. Google Workspace, collaboration tools, developer tools, CRM and support systems, marketing platforms, HR systems, finance tools, and commerce services each have adapters that understand their own vendor’s API. Together, they act like translators feeding one shared machine: fetch outside data, normalize it, store page changes, and report the sync result.

## Sub-stages

- [Google Workspace source connectors](stage-14.1.md) `stage-14.1` — 6 files
- [Collaboration, knowledge, and scheduling source connectors](stage-14.2.md) `stage-14.2` — 7 files
- [Work management and developer operations source connectors](stage-14.3.md) `stage-14.3` — 9 files
- [CRM, sales, and customer support source connectors](stage-14.4.md) `stage-14.4` — 6 files
- [Marketing, advertising, and lifecycle source connectors](stage-14.5.md) `stage-14.5` — 7 files
- [HR, recruiting, and workforce source connectors](stage-14.6.md) `stage-14.6` — 6 files
- [Finance, billing, and commerce source connectors](stage-14.7.md) `stage-14.7` — 7 files
- [Source extension registry, tools, pages, and special sources](stage-14.8.md) `stage-14.8` — 4 files
- [Core source synchronization framework](stage-14.9.md) `stage-14.9` — 5 files

## 📊 State Registers Touched

- `reg-extension-set` — The saved and loaded set of extensions, packs, manifests, and contributed capabilities available to the runtime.
- `reg-credential-store` — The encrypted secrets and credential slots used to let tools and connectors act for a workspace without exposing raw secrets.
- `reg-connection-grants` — The saved approvals and safe account handles for connected external accounts such as Slack, GitHub, Composio, and Pipedream.
- `reg-tool-context` — The per-run authority envelope that gives tools only the workspace, credentials, cleanup hooks, and permissions they are allowed to use.
- `reg-source-pages` — The source connections, sync cursors, imported pages, removal markers, and page-change records from outside systems.
- `reg-search-index` — The searchable text chunks, embeddings, and selected index backend used to find relevant stored content.
- `reg-extension-store` — The per-workspace extension-owned storage where plugins keep their own durable records without private tables.
- `reg-page-alert-subscriptions` — The stored page-change watch rules, subscribed conversations, topic alerts, and pending alert notifications triggered by synced content changes.
- `reg-source-sync-backoff` — Per-source sync error counters, retry/backoff state, and last-result throttling used to decide when background imports should run again.
- `reg-eval-environment-fixtures` — Workspace-scoped fake email and calendar records used by the evaluation environment connectors and tools.

# Public SDK, extension contracts, protocols, and sample conformance  `stage-20` (cross-cutting infrastructure)

This stage is the public “plug-in counter” for the system. It is shared behind-the-scenes support, not the main work loop. Its job is to give extension and pack authors stable names to import, so they can add features without depending on private internal files that may move.

One part provides the runtime workbench an extension receives while it runs: a limited context for approved data, credentials, logs, HTTP helpers, model calls, and other safe actions. Another part exposes identity and administration doorways, such as bearer-token checks, credentials, grants, seats, and accounting reports. A third part opens public ports for outside capabilities like browsers, connectors, search indexes, memory, models, objects, sandboxes, sources, and search.

The contribution-contract part defines the manifest, the form an extension fills out to declare tools, jobs, routes, hooks, models, and other offerings. The sample extension proves these contracts work end to end. The direct scheduling and surfaces files add two more stable doorways: one for scheduling tools and one for surface-related extension types and errors.

## Sub-stages

- [Extension runtime context, package entry, HTTP, and observability SDK](stage-20.1.md) `stage-20.1` — 5 files
- [SDK identity, authorization, credentials, and administration doorways](stage-20.2.md) `stage-20.2` — 8 files
- [SDK external capability and data protocol doorways](stage-20.3.md) `stage-20.3` — 9 files
- [SDK contribution contracts and sample extension conformance](stage-20.4.md) `stage-20.4` — 6 files

## Files in this stage

### SDK Public Doorways
Stable SDK import modules expose scheduling and surface extension contracts without requiring callers to depend on internal package layout.

### `core/src/ufo/sdk/scheduling.py`

`other` · `cross-cutting import-time API surface`

This module is like a labeled doorway into the scheduling part of the system. The actual scheduling machinery lives in `ufo.scheduling`, but extension code is meant to reach it through the public SDK package, `ufo.sdk`. Because this project keeps `__init__.py` files empty, the SDK exposes named modules like this one instead of collecting everything at the package root.

The file re-exports a small set of scheduling names: a constant for one-time schedules, the `ScheduledTask` value object, the `ScheduleStore` used to work with stored schedules, `TaskInspection` for looking at scheduled work, and `due_task_workspaces`, which helps find workspaces with tasks ready to run. “Re-export” means this file imports something and immediately makes it available under the same name, so callers can write imports from `ufo.sdk.scheduling` without knowing where the implementation lives.

Without this file, extension authors would either need to import from internal modules directly, which makes their code more fragile, or they would have no clean SDK path for scheduling features. Its main value is stability and clarity: it separates the public promise of the SDK from the internal layout of the codebase.


### `core/src/ufo/sdk/surfaces.py`

`other` · `cross-cutting import-time API access`

This module does not define new behavior. Instead, it acts like a clearly labeled service desk: if someone is building a “surface” extension, they can come here to get the official tools and names they need. A surface is an integration point that can receive conversation data, expose routes, and, for durable surfaces, write information back in a careful two-step way.

The file re-exports items from deeper internal modules. These include the main surface building blocks, such as SurfaceSpec, SurfaceRoute, SurfaceContext, Writeback, and SharedArtifact. It also exposes related records used in conversations, questions, terminal frames, credentials, connection requests, and transcript summaries. Errors are included too, so extension authors can catch and respond to expected failure cases such as invalid credential requests or unknown workspaces.

The reason this file matters is stability and clarity. Internal code can move around over time, but public users should not have to chase those changes. By importing from ufo.sdk.surfaces, they use the supported public interface. Without this file, extension authors would need to know many internal module paths, which would make integrations more fragile and harder to understand.

## 📊 State Registers Touched

- `reg-config` — The effective deployment settings that tell the system how to start, what services to use, and what safety rules are enabled.
- `reg-extension-set` — The saved and loaded set of extensions, packs, manifests, and contributed capabilities available to the runtime.
- `reg-auth-session` — The login and token state that proves who a user or client is across gateway, web, terminal, and admin requests.
- `reg-credential-store` — The encrypted secrets and credential slots used to let tools and connectors act for a workspace without exposing raw secrets.
- `reg-connection-grants` — The saved approvals and safe account handles for connected external accounts such as Slack, GitHub, Composio, and Pipedream.
- `reg-model-catalog` — The shared list of available AI models, providers, limits, prices, and client adapters.
- `reg-tool-catalog` — The shared catalog of tools the model may call, including their names, schemas, handlers, and safety properties.
- `reg-tool-context` — The per-run authority envelope that gives tools only the workspace, credentials, cleanup hooks, and permissions they are allowed to use.
- `reg-browser-session` — The browser automation connection state used when tools need a controlled browser for a turn.
- `reg-skill-inventory` — The built-in and user-created skill folders, metadata, dependencies, and workspace-specific skill records.
- `reg-source-pages` — The source connections, sync cursors, imported pages, removal markers, and page-change records from outside systems.
- `reg-search-index` — The searchable text chunks, embeddings, and selected index backend used to find relevant stored content.
- `reg-memory-store` — The durable memories, memory pages, recall events, and consolidation state used for long-term recall.
- `reg-knowledge-graph` — The stored entities and relationships extracted from pages so the system can look up connected facts.
- `reg-scheduled-jobs` — The background job and scheduled-task state that records what should run later, what is claimed, and what repeats.
- `reg-extension-store` — The per-workspace extension-owned storage where plugins keep their own durable records without private tables.
- `reg-eval-environment-fixtures` — Workspace-scoped fake email and calendar records used by the evaluation environment connectors and tools.
- `reg-sample-extension-note` — Workspace-scoped note stored by the sample extension to prove extension migrations and SDK storage contracts work end to end.

# Configuration Manifests, Catalogs, and Generic Utilities  `stage-24` (cross-cutting infrastructure)

This stage is the system’s shared toolbox and map room. Some of it runs during startup, some supports normal request handling and background jobs, and much of it simply helps other code find the right pieces later.

The deployment configuration code reads UFO’s local settings and rejects bad setups early, while the extension catalog and lockfile decide which add-ons are available and exactly which ones should be loaded. The many package manifest files are like labels on drawers: they make core areas, control code, browser tools, connectors, documents, memory, evaluation, and other extension folders importable by Python. Shared runtime utilities provide common services, such as paging through long result lists safely and wiring up logs, metrics, and traces while hiding sensitive data. Browser validation files define safe shapes for commands sent to a browser and data received back. The cron helper reads scheduled-task timetables and finds the next run time. The sample extension acts as a test plug, checking that extension hooks still work. Together, these pieces make the larger system predictable, discoverable, and easier to operate.

## Sub-stages

- [Deployment Configuration and Extension Catalog State](stage-24.1.md) `stage-24.1` — 2 files
- [Shared Core Runtime Utilities](stage-24.2.md) `stage-24.2` — 2 files
- [Browser BUA Wire-Data Validation](stage-24.3.md) `stage-24.3` — 2 files
- [Scheduled Task Cron Parsing Helper](stage-24.4.md) `stage-24.4` — 1 files
- [Sample Extension API Coverage Module](stage-24.5.md) `stage-24.5` — 1 files
- [Core and Control Package Manifests](stage-24.6.md) `stage-24.6` — 9 files
- [External Connectivity and Surface Extension Manifests](stage-24.7.md) `stage-24.7` — 10 files
- [Agent Workflow, Development, and Evaluation Extension Manifests](stage-24.8.md) `stage-24.8` — 10 files
- [Document, Memory, and Domain Pack Manifests](stage-24.9.md) `stage-24.9` — 7 files

## 📊 State Registers Touched

- `reg-effective-configuration` — The final startup settings that decide how the service, security, sandbox, proxy, and enabled packs should behave.
- `reg-extension-catalog` — The loaded list of extensions and packs that tells the system which extra tools, routes, jobs, skills, and backends exist.
- `reg-durable-store-schema` — The shared database layout and migration version that all services rely on when saving or reading system records.
- `reg-tool-catalog` — The shared catalog of tool names, descriptions, schemas, and implementations that the model is allowed to call.
- `reg-browser-session-state` — The live browser-control session state used to click, read pages, download files, recover sessions, and route sandbox browser links.
- `reg-scheduled-task-state` — The durable timers and recurring jobs that remember what should run later, whether it is paused, expired, claimed, or rescheduled.
- `reg-observability-trace-state` — The shared logging, metrics, trace IDs, trace parents, and redaction context used to follow work across processes without leaking secrets.
- `reg-extension-note-state` — Durable note records stored by note/sample extensions and reused by extension tools across startup and normal workspace operation.
- `reg-web-metadata-store` — Persistent web metadata/cache records captured by web/search/browser-related extensions for later lookup, indexing, or display.

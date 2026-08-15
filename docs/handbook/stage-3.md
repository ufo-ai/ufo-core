# Pack selection and capability assembly  `stage-3`

This stage happens during startup, before the assistant begins its main work. Its job is to choose a “pack,” which is a ready-made bundle of features, and turn it into all the usable parts the host needs: tools, skills, routes, credentials, background jobs, model and browser backends, connectors, hooks, and object types.

The pack bundle definitions are the recipes. They say which feature groups to enable for local development, hosted use, evaluations, or special workflows. The core extension and skill loading runtime is the unpacking machinery. It checks what was selected, loads extension manifests, and prepares skill folders in an isolated workspace.

The agent skill and subagent manifests add task-focused helpers for browsing, coding, documents, research, sites, and similar work. The connector manifests make outside services such as Slack, Composio, Pipedream, and source systems available. The memory, objectives, monitors, and scheduled automation manifests add long-running support such as reminders, saved context, watched conditions, and recurring jobs. The platform manifests expose the web portal, debugger, shell stream, and optional Redis-backed infrastructure.

## Sub-stages

- [Core extension and skill loading runtime](stage-3.1.md) `stage-3.1` — 4 files
- [Pack bundle definitions](stage-3.2.md) `stage-3.2` — 8 files
- [Agent skill, subagent, and skill-support extensions](stage-3.3.md) `stage-3.3` — 8 files
- [External connector and source integration manifests](stage-3.4.md) `stage-3.4` — 5 files
- [Memory, objectives, monitors, and scheduled automation manifests](stage-3.5.md) `stage-3.5` — 5 files
- [Platform surface and infrastructure manifests](stage-3.6.md) `stage-3.6` — 4 files

## 📊 State Registers Touched

- `reg-effective-config` — The deployment’s active settings, such as enabled services, limits, paths, providers, and safety options.
- `reg-object-catalog` — The shared catalog of manageable workspace object types and the rules for who may view or change them.
- `reg-extension-pack-registry` — The selected packs and loaded extensions that decide which features, tools, routes, jobs, and backends exist.
- `reg-extension-store` — Per-workspace extension pins and extension-owned settings saved so enabled add-ons survive restarts.
- `reg-model-catalog` — The lookup table of available AI models, providers, routing details, capabilities, and pricing metadata.
- `reg-tool-catalog` — The runtime menu of tools the agent may call, including built-ins and extension-provided tools.
- `reg-background-job-schedule` — The durable list of background jobs and dispatch rules used to retry and run work outside user requests.
- `reg-browser-session` — The leased browser instance for a turn, including tabs, page state, downloads, dialogs, and provider connection details.
- `reg-subagent-state` — The helper-agent catalog and child-conversation handoff state used when one agent delegates work to another.
- `reg-search-index` — The searchable text chunks, embeddings, and backend index state used to find relevant documents and pages.
- `reg-memory-store` — Saved facts, memory pages, confidence, provenance, and audience labels that let the assistant remember useful context.
- `reg-automation-objectives` — Durable objectives, steps, monitors, checks, blockers, and evidence for work that continues across turns.
- `reg-conversation-slots` — Side-panel data shown beside a conversation, such as tasks, sources, sites, automations, and workspace changes.
- `reg-user-skill-library` — Persisted user- or agent-authored skills and reusable skill metadata loaded into the agent’s available capabilities.
- `reg-sample-notes-store` — Durable sample-note extension records and note metadata used as optional workspace content across setup, tools, retrieval, and persistence paths.
- `reg-web-chat-metadata` — Extension-owned metadata for web-chat conversations or sessions, such as visitor/channel details and routing/display data beyond the core transcript.
- `reg-scratchpad-notebooks` — Persisted scratchpad or notebook content that agents reuse across turns separately from saved skills and ordinary conversation files.

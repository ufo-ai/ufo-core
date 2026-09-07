# Extension discovery and capability registration  `stage-4`

This stage is the system’s plug-in discovery desk. During startup, and later as shared support, the host looks for installed extensions, checks their manifest files, and registers what each one offers. A manifest is a clear “menu card” that says which tools, apps, background jobs, screens, sign-in methods, agents, data types, and outside services an extension adds.

The core loading part finds extensions, enforces the allowed list, and turns their menus into usable system pieces. Built-in app registrations add the chat, artifacts, radar, wiki, notification, code, issues, meetings, and metrics apps so the workspace can show and run them. Connector and authentication registrations prepare safe sign-in and action routes for services such as Slack, GitHub, Composio, Pipedream, and API-key based tools. Provider and communication extensions add browser, enrichment, iMessage, Slack, and knowledge-source abilities. Skill packs register writing, coding, document, research, and skill-authoring helpers. Automation registrations add monitors, objectives, reports, scheduled tasks, and self-improvement jobs. Runtime registrations add memory, sites, debugger pages, Redis backends, and the main UFO and web surfaces.

## Sub-stages

- [Core extension loading and manifest contract](stage-4.1.md) `stage-4.1` — 4 files
- [Built-in conversational and content app registrations](stage-4.2.md) `stage-4.2` — 10 files
- [Built-in work app registrations](stage-4.3.md) `stage-4.3` — 8 files
- [Connector framework and authentication broker registrations](stage-4.4.md) `stage-4.4` — 7 files
- [External provider and communication extension registrations](stage-4.5.md) `stage-4.5` — 8 files
- [Agent skill, authoring, document, coding, and research registrations](stage-4.6.md) `stage-4.6` — 7 files
- [Task automation, monitoring, objectives, and improvement registrations](stage-4.7.md) `stage-4.7` — 6 files
- [Runtime surfaces, memory, sites, and backend registrations](stage-4.8.md) `stage-4.8` — 10 files

## 📊 State Registers Touched

- `reg-effective-config` — The merged service settings that tell the process how this deployment should run.
- `reg-feature-flags` — The shared on/off switches that let the service enable or disable behavior at runtime.
- `reg-extension-registry` — The discovered set of installed extensions and the capabilities each extension contributes.
- `reg-tool-catalog` — The shared list of tools and actions agents may ask to run, including extension tools.
- `reg-skill-library` — The stored and packaged reusable skill instructions and files available to agents.
- `reg-agent-registry` — The saved agents, their owners, visibility, model choices, tool policies, and sandbox settings.
- `reg-surface-routing-state` — The saved routing state for web, Slack, iMessage, terminal, and other public conversation surfaces.
- `reg-memory-store` — The remembered facts and searchable memory chunks that can be retrieved or condensed later.
- `reg-scheduled-work-store` — The durable records for recurring tasks, delayed resumes, scheduled fires, and background job claims.
- `reg-notification-inbox` — The stored pending notifications and delivery state used to batch notices and wake conversations.
- `reg-monitor-objective-state` — The saved monitors, objectives, plans, steps, evidence, and blocks that survive across turns.
- `reg-extension-state-store` — Generic per-workspace extension-owned durable key/value or configuration state not covered by a named core store.
- `reg-enrichment-profile-store` — Cached or recorded person/company enrichment data together with permissions controlling who may use it.
- `reg-eval-fixture-state` — Deterministic fake-service datasets and recorded mutations used by evaluation and local-development connectors.

# Extension discovery and capability registration  `stage-3`

This stage is the system’s plug-in intake desk. During startup, and sometimes during setup commands, the host looks for installed extensions, checks which ones are allowed by configuration, reads their manifest files, and turns those descriptions into features the rest of UFO can use. A manifest is like a registration card: it says “this extension provides these tools, apps, jobs, credentials, screens, agents, or backends.”

The core discovery infrastructure does the scanning, filtering, loading, and record keeping. It also chooses sandbox backends, which are isolated work areas where tasks can run safely. Provider and backend registration adds replaceable outside services, such as model providers, embedding search services, Redis message hubs, and remote terminals. Workspace app manifests register first-party apps like Chat, Code, Issues, Meetings, Metrics, Radar, Wiki, and Artifacts, including their agents, home screens, setup needs, and scheduled work.

Other manifest groups add agent skills, research and coding helpers, document and website tools, connectors, source syncing, Slack or iMessage surfaces, memory, monitors, objectives, scheduled tasks, report digests, debugging, web portals, and sample extensions. Together, they make UFO expandable without hardwiring every capability into the core.

## Sub-stages

- [Provider and backend registration](stage-3.1.md) `stage-3.1` — 6 files
- [Core extension discovery infrastructure](stage-3.2.md) `stage-3.2` — 4 files
- [Workspace app extension manifests](stage-3.3.md) `stage-3.3` — 15 files
- [Agent skill and content-work extension manifests](stage-3.4.md) `stage-3.4` — 13 files
- [Connector, source, and communication extension manifests](stage-3.5.md) `stage-3.5` — 10 files
- [Platform service, object, and surface extension manifests](stage-3.6.md) `stage-3.6` — 11 files

## 📊 State Registers Touched

- `reg-config-stack` — The merged settings that tell the whole service how to start, connect, and behave.
- `reg-feature-flags` — The shared on/off switches and rollout choices that let operators change behavior without redeploying.
- `reg-pack-composition` — The selected bundle of built-in extensions, prompts, skills, jobs, and setup steps for this deployment.
- `reg-extension-registry` — The live catalog of installed extensions and the capabilities each one has registered.
- `reg-model-catalog` — The shared list of available AI models, their abilities, providers, prices, and credential needs.
- `reg-host-environment` — The assembled per-turn world given to the agent: prompts, skills, files, model choice, tools, and extension context.
- `reg-tool-catalog` — The shared catalog of tools and the policies that decide which tools may run with which permissions.
- `reg-sandbox-state` — The remembered sandbox handles and execution environments where commands, files, and risky work run safely.
- `reg-connector-brokers` — The shared catalog and runtime state for service connectors, MCP servers, broker accounts, and approved actions.
- `reg-scheduled-jobs` — The durable background work list for timers, recurring conversations, monitors, reports, and long-running tasks.
- `reg-surface-routing` — The saved routing state that maps web, Slack, iMessage, terminal, and other surfaces to workspaces and agents.
- `reg-object-system` — The common address book and audit trail for durable workspace objects such as agents, members, artifacts, and connectors.
- `reg-extension-store` — The per-workspace storage area where extensions keep their own durable settings and small JSON records.
- `reg-agent-provisioning` — The saved provenance, setup needs, policies, and ownership for agents that are shipped by extensions or created in workspaces.
- `reg-runtime-message-bus` — Shared Redis/pub-sub or message-hub connection state used to coordinate live updates, workers, and cross-process runtime events.
- `reg-skill-library-cache` — Cached skill-package metadata, community skill listings, fetched descriptions, and probe results used when resolving skills for agents.

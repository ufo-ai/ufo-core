# Extension manifests and capability registration  `stage-3.2`

This stage is startup and shared support. It is where extensions introduce themselves to the host system. Each manifest is like a registration card: it lists the tools, agents, web routes, background jobs, credentials, data types, and helper instructions that an extension offers. The host reads these cards and wires everything into the right place before users or agents need it.

The agent, skill, and workflow manifests add work-focused abilities, such as browser use, coding help, document creation, research, website building, and reusable user-made skills. The connector and communication manifests connect outside services, such as Slack, iMessage, Composio, Pipedream, and content sources, including their sign-in paths and required secrets. The state, objectives, scheduling, and background-job manifests register longer-running support, such as memory, reminders, monitors, scheduled tasks, and recurring evaluation work. The host surface, backend, and API example manifests expose user-facing pages, web routes, Redis-backed services, and a sample extension used to prove the extension system works. Together, these files make the system’s possible abilities visible and usable.

## Sub-stages

- [Agent, skill, and domain-workflow extension manifests](stage-3.2.1.md) `stage-3.2.1` — 7 files
- [External connector and communication manifests](stage-3.2.2.md) `stage-3.2.2` — 6 files
- [State, objectives, scheduling, and background-job manifests](stage-3.2.3.md) `stage-3.2.3` — 5 files
- [Host surface, backend, and extension-API example manifests](stage-3.2.4.md) `stage-3.2.4` — 5 files

# Capability registration  `stage-3.1`

Capability registration is the system’s startup sign-in table. Before the agent can do useful work, extensions declare what they add: tools, web pages, helper agents, login methods, search sources, browser runners, long-term memory, scheduled jobs, and reusable skills. These declarations are called manifests, meaning small files that describe available features without running them yet.

The subagent profile and agentic manifest parts define specialist helpers, such as browser, coding, research, writing, and website-building agents, and say what instructions and tools each one gets. Connector, credential, and source manifests register outside services like Slack, Pipedream, Composio, and searchable content sources, including how users authenticate. Surfaces and runtime providers announce user-facing entry points like web, terminal, and debugger views, plus hosted browser access. Memory, knowledge, and page-change hooks add background knowledge features, such as graph search, long-term memory, and alerts when watched pages change. Skills and scheduled work manifests register document abilities, custom skills, recurring tasks, and daily evaluation jobs. Together, these parts build the catalog the main system uses later.

## Sub-stages

- [Subagent profile implementations](stage-3.1.1.md) `stage-3.1.1` — 5 files
- [Agentic extension manifests](stage-3.1.2.md) `stage-3.1.2` — 5 files
- [Connector, credential, and source manifests](stage-3.1.3.md) `stage-3.1.3` — 6 files
- [Surfaces and runtime providers](stage-3.1.4.md) `stage-3.1.4` — 4 files
- [Memory, knowledge, and page-change hooks](stage-3.1.5.md) `stage-3.1.5` — 3 files
- [Skills and scheduled work manifests](stage-3.1.6.md) `stage-3.1.6` — 4 files

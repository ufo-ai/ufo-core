# System Handbook

## 🗺️ System Overview

UFO is a workspace server for AI agents. A useful mental picture is a secure office building where an assistant can chat with people, use approved tools, browse the web, edit files, call outside services, and hand work to helper agents. Extensions decide which rooms and tools exist, so the same core system can support chat, coding, research, websites, reports, scheduled tasks, and connectors.

The story starts with an operator running commands through `ufoctl`. These commands prepare deployments, install extensions, check the sandbox, manage credentials, and run database migrations. Migrations upgrade the system’s long-term memory so old workspaces still fit the current code. At startup, the server reads configuration, chooses the enabled extension packs, discovers what each extension provides, and onboards or finds a workspace with its members, agents, secrets, and initial setup.

Once running, UFO opens guarded doors: the web app, Slack or iMessage, terminal input, public artifact links, OAuth callbacks, and hosted sites. Incoming messages are checked, tied to the right workspace and conversation, and saved as a “turn,” meaning one unit of agent work. A worker claims the turn, gathers conversation history, memories, skills, model choices, schedules, and reply rules, then asks a language model what to do. If the model needs action, UFO dispatches tools through strict permission checks and often runs them inside a sandbox, a contained work area that limits file and network access.

During the turn, UFO may browse websites, build pages, research sources, edit documents, use connected accounts, or delegate subtasks to specialized helper agents. It streams progress back to users, supports cancellation, records costs, and stores outputs as safe artifacts with previews and signed download links. When the turn ends, it records final replies, file changes, billing data, and cleanup.

Behind the scenes, background workers sync external sources, index knowledge for search, recall memories, run scheduled prompts, watch monitors, and deliver delayed results. Shared foundations keep everything coherent: database and blob storage, access control, encrypted credentials, billing policy, logging, tracing, public SDK contracts, model catalogs, search infrastructure, and extension rules.

---

## See also

- [State-flow registers](register.md) — global state that flows across stages
- [Stage Index](index.md) — every stage and what it does

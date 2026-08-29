# System Handbook

## 🗺️ System Overview

UFO is a platform for running AI assistants in workspaces. A useful mental picture is a busy, secure workshop. People can walk in through many doors, such as a web app, Slack, iMessage, a terminal, or a hosted site. Inside, assistants can answer, browse, write, code, use connected services, remember useful knowledge, and run scheduled work later.

Before the workshop opens, an operator chooses a product recipe and builds a deployable bundle. The system prepares its database, which is its long-term filing cabinet, by applying ordered upgrades. At startup it registers the extensions, apps, model providers, skills, tools, and background jobs that are installed and allowed. New workspaces are then onboarded with members, agents, secrets, connected accounts, and any extension setup they need.

Once running, the service announces that it is alive, starts schedulers, and listens for requests. Incoming traffic is checked, translated into the system’s internal shape, and routed to the right place. If the request changes workspace objects, safety gates check ownership and permissions. If it starts agent work, it becomes a durable “turn,” meaning one saved unit of conversation work that can survive crashes and retries.

For each turn, the engine claims the job, gathers the conversation, builds a careful prompt, and sends it to an AI model. As the answer streams back, the model may ask to use tools. UFO checks which tools are allowed, then runs them in trusted host code or inside controlled sandboxes for commands, browsers, documents, and files. Larger jobs can be delegated to helper agents. Meanwhile, connected sources can be synced, indexed, and turned into memory, and schedulers can fire recurring automations, monitors, reports, and self-improvement tests.

When work finishes, UFO publishes replies, artifacts, previews, sites, and panels back to users. If something stops, fails, or is cancelled, cleanup records file changes, releases resources, and makes retry safe. Across all of this, shared systems handle storage, extension contracts, security, credentials, billing, feature flags, logging, configuration, and packaging.

---

## See also

- [State-flow registers](register.md) — global state that flows across stages
- [Stage Index](index.md) — every stage and what it does

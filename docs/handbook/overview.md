# System Handbook

## 🗺️ System Overview

UFO is a runtime for AI agents. A useful mental picture is a busy workshop with many doors. People and outside systems can come in through a web app, Slack, iMessage, a terminal, or scheduled jobs. Inside, agents can talk, use tools, browse the web, edit files, call approved services, and hand focused tasks to helper agents. Extensions add many of these abilities, so the core does not need to know every possible tool in advance.

When a process starts, UFO first decides what job it is doing: run the server, initialize a workspace, perform an admin task, build a deployable package, or check the sandbox setup. It then reads configuration, chooses a pack of enabled features, checks feature flags, and discovers installed extensions. Each extension brings a manifest, like a registration card, describing its tools, screens, agents, jobs, storage, or connectors. For a new installation, onboarding creates the first workspace, administrator, and starter agents.

The server then opens its doors. Authentication checks who is arriving. Incoming messages and events are translated into common conversation records. Before an agent turn runs, UFO checks permissions, seats, spending limits, duplicates, and ordering. For each accepted turn, it builds a safe working environment: prompts, model choice, files, skills, tools, child agents, and sandbox access.

The main loop is simple in shape. The agent asks a model what to do, runs any approved tools the model requests, returns the results, and repeats until there is a final answer. Risky actions happen in sandboxes, which are isolated work areas. Connectors let agents use services like Slack, Gmail, search, or browsers without exposing secrets directly. Background jobs keep schedules, sync outside knowledge, build indexes, and resume long-running work.

When a turn ends or is stopped, UFO saves replies, delivers child-agent results, cancels leftover work, records file changes, and cleans up. Underneath everything are shared foundations: database migrations, blob storage, public extension contracts, security and billing checks, logs, metrics, debugging tools, and test environments that keep the whole workshop reliable and inspectable.

---

## See also

- [State-flow registers](register.md) — global state that flows across stages
- [Stage Index](index.md) — every stage and what it does

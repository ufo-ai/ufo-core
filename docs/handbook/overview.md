# System Handbook

## 🗺️ System Overview

UFO is an AI workspace server: a shared place where people can talk to assistants, connect tools and data, and let agents do work safely. A useful picture is a busy workshop. The server is the building, each workspace is a locked room, the AI agent is a worker, and tools, files, browsers, Slack, search, and databases are equipment the worker may use only with permission.

The story starts before the workshop opens. Operators use command-line tools to create or repair a workspace, package the app, run database upgrades, check billing, and prepare sandboxes. At startup, the system reads its configuration, chooses a “pack” of enabled features, connects to the database and model providers, loads extensions, and mounts web routes. These routes are the doors people and services use: the web app, Slack, iMessage, terminals, shared sites, login callbacks, downloads, and operator controls.

During normal use, an incoming message is checked, tied to the right workspace and conversation, and admitted as a durable “turn,” meaning one unit of agent work. The queue keeps turns ordered and prevents two workers from running the same turn. For each turn, the system sets the agent’s workbench: prompts, skills, allowed tools, model choice, files, browser access, credentials, and sandbox. The model then thinks and streams text. If it asks to use a tool, the server dispatches that request through guardrails, often inside a sandbox, and records what happened. Bigger jobs can be split among subagents or tracked as objectives and workflows.

When the answer is ready, the system saves the transcript, prepares files, previews, hosted pages, and side panels, then sends the result back to the right surface. In the background, scheduled jobs sync sources, refresh indexes, wake monitors, retry previews, and run maintenance. If work is stopped or a process dies, cleanup code cancels safely, releases resources, and recovers abandoned turns.

Behind all of this are shared supports: durable storage, blob files, identity, signed links, credentials, network limits, billing, model and search registries, feature flags, extension SDK contracts, logging, debugging, and evaluation tools.

---

## See also

- [State-flow registers](register.md) — global state that flows across stages
- [Stage Index](index.md) — every stage and what it does

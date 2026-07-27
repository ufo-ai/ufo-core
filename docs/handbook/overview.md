# System Handbook

## 🗺️ System Overview

UFO is an extensible AI agent platform: a place where people can talk to an assistant from Slack, a browser, or a terminal, and the assistant can safely use tools, files, outside apps, and helper agents to get work done. A useful mental picture is a busy workshop. The chat surfaces are the front counter, the agent is the craftsperson, the tools and connectors are the workshop equipment, and the sandbox is the safety room where risky work happens without spilling into the rest of the building.

When UFO starts, it first checks that the environment is safe and complete. Command-line tools can create or package a workspace, while hosted deployments start a public gateway for sign-in and onboarding. The system loads its configuration, checks installed extensions, reads their “manifests” or contribution lists, and registers the tools, models, routes, jobs, credentials, and chat surfaces they provide. It also connects to the database, storage, sandbox system, live update channels, and any fleet coordination needed when multiple server processes run together.

The main loop begins when a message arrives. UFO identifies the workspace and user, checks permission, avoids duplicates, saves the message, and turns it into a durable “turn,” meaning a unit of agent work stored in the database so it can survive crashes. A worker claims the turn, opens a sandbox, gathers conversation context, chooses a model, and asks it what to do. As the model streams text or requests tools, UFO records each step, runs allowed tools with limited powers, connects to approved outside accounts when needed, and sends live progress back to the user.

Around this live work, background jobs sync outside sources, index pages for search and memory, run scheduled tasks, manage billing, and cautiously test possible prompt improvements. When a turn finishes, fails, pauses, or is cancelled, UFO delivers the final reply, closes streams, saves status, shares any signed files, and cleans up sandboxes and temporary resources. Throughout, shared infrastructure handles schemas and migrations, extension contracts, logging, metrics, accounting, operator diagnostics, and recovery, so the workshop stays understandable, auditable, and safe to run.

---

## See also

- [State-flow registers](register.md) — global state that flows across stages
- [Stage Index](index.md) — every stage and what it does

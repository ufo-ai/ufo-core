# System Handbook

## 🗺️ System Overview

UFO is an AI agent platform: a place where people can talk to assistants, connect work tools, and let those assistants safely do work such as researching, editing files, using apps, browsing, or building sites. A useful mental picture is a busy, well-run workshop. Users bring requests to the front counter, jobs are written on durable tickets, skilled workers use controlled tools in safe rooms, and every result is recorded.

Before the doors open, deployment checks the building. It verifies the database, updates its tables, prepares the sandbox where agent code and commands can run, and loads the chosen pack of features. At startup, the service reads configuration, discovers extensions, registers tools, models, connectors, web routes, and background jobs, then brings up shared services like the network proxy and worker heartbeats.

During normal use, requests arrive from Slack, the web app, the command line, hosted site links, or other surfaces. The system first proves who is calling, then maps the request to the right workspace, member, conversation, and agent. A “turn” is created for each unit of agent work. That turn is saved in a durable queue, so it can survive crashes and be picked up or recovered by another worker.

For each turn, the system prepares context: conversation history, memory, files, skills, prompts, model choice, limits, and credentials. The agent loop then asks an AI model what to do next. If the model requests an action, the tool catalog routes it to approved tools. Those tools run inside sandboxes, use guarded network access, and reach external services only through permissioned connectors. Progress streams back live, and finished artifacts can be shared through signed links.

Alongside this main loop, background workers sync sources, build search indexes, run scheduled tasks, handle billing, evaluate behavior, and support self-improvement. When work ends, the system saves final transcripts, records usage, marks jobs complete or failed, cancels safely when needed, and cleans up sandboxes, browsers, connections, and temporary resources. Under everything are the durable database, permission rules, secret handling, SDK interfaces, model catalogs, logging, and utility code that keep the workshop safe, extensible, and reliable.

---

## See also

- [State-flow registers](register.md) — global state that flows across stages
- [Stage Index](index.md) — every stage and what it does

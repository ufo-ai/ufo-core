# System Handbook

## 🗺️ System Overview

UFO is an AI assistant runtime: a server that lets people talk to agents from the web, Slack, a terminal, or other entry points, then lets those agents use approved tools in a controlled workspace. A good mental picture is a busy workshop. Users come to the front desk with requests. The assistant thinks, calls specialists and tools, works inside a guarded sandbox, and returns visible results like replies, files, sources, tasks, or website links.

When the process starts, operators choose a mode and a pack of extensions, which is the system’s toolbox for that deployment. The database is prepared or upgraded so stored records match the code. In hosted use, the control plane helps new users prove their work email, create or join a workspace, receive a login token, and connect Slack if needed. The runtime then reads its configuration, checks safety settings, discovers approved extensions, and registers available models, tools, connectors, jobs, surfaces, and workflows.

Once the HTTP server is running, requests arrive through browser routes, Slack, command-line clients, OAuth callbacks, or sandbox website links. The system identifies the caller, opens the right workspace “filing cabinet,” checks membership and permissions, and admits a new conversation turn. A queue claims the turn so two replies do not collide. The engine gathers history, memory, goals, tools, limits, and instructions, then sends a carefully shaped request to a language model. Provider adapters make Anthropic, OpenAI-style services, and others all look the same to the rest of the system.

During the turn, the model may ask to use tools. UFO dispatches those calls through safety wrappers: files, commands, browsers, documents, web search, external connectors like Gmail or GitHub, and background source indexes all run with scoped credentials and sandbox limits. Results stream back live, then are saved and displayed as safe portal items.

In the background, schedulers resume paused work, run monitors, sync sources, recover abandoned turns, and export usage. Shared infrastructure holds everything together: durable storage, signed tokens, credential vaults, authorization checks, billing ledgers, diagnostics, SDK contracts for extensions, and simple Python package markers that make the code importable. When work is stopped or the server goes away, cancellations and recovery records let the next run finish cleanly.

---

## See also

- [State-flow registers](register.md) — global state that flows across stages
- [Stage Index](index.md) — every stage and what it does

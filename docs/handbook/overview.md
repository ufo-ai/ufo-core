# System Handbook

## 🗺️ System Overview

UFO is an AI agent platform: a server that lets people talk to assistants through web chat, Slack, iMessage, or a command line, while the assistants can use tools, files, browsers, outside services, and helper agents. A useful mental picture is a guarded workshop. Users come in through different doors, the agent plans the work, and the platform provides safe benches, approved tools, records, and supervisors.

The story starts with an operator preparing a workspace. Command-line tools create the first admin, the first agent, required settings, and any extension setup. The system checks that its sandbox, a protected place for running code, is available. On launch, it loads configuration, upgrades the database shape if needed, discovers installed extensions, selects feature packs, starts web routes, workers, background jobs, and the main API server.

When a message arrives, the system first checks identity and permission. It turns each channel’s special format into one common conversation request. A traffic controller then decides whether to start a new turn, attach to existing work, wait, stream progress, or cancel. A turn is one user request plus the agent’s attempt to answer it.

The durable engine claims the turn so only one worker runs it. It builds the agent’s context: recent conversation, instructions, skills, available subagents, tools, and relevant memories or indexed documents. It calls an AI model through provider adapters, streams the response, and, when the model asks to act, dispatches tools. Tools can edit files, run commands in sandboxes, browse websites, create artifacts, use approved connectors like Slack or GitHub, or delegate work to specialist agents. Results are saved as the turn progresses so recovery is possible after a crash.

After the answer is ready, the system sends it back to the original surface, records changed files, releases browsers, terminals, locks, and sandboxes, and marks the turn done or paused.

Behind all of this are shared supports: database records, extension contracts, credentials, sessions, access boundaries, storage for files, scheduled jobs, billing, indexing, diagnostics, and careful logging. These keep the workshop safe, durable, expandable, and understandable.

---

## See also

- [State-flow registers](register.md) — global state that flows across stages
- [Stage Index](index.md) — every stage and what it does

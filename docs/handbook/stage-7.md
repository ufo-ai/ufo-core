# Turn Admission, Conversation Queueing, and Live Attachment  `stage-7`

This stage is the system’s intake and live-observation area for agent work. It begins when a person or internal event asks for something, then turns that request into a durable “turn,” which means one cycle of agent work that can be stored, claimed, run, and finished safely.

Admission and Queue Coordination works like traffic control. It decides whether a new request should start a new turn, join a turn already in progress, wait in line, pause because spending limits were reached, or be rejected. It keeps conversation turns ordered, prevents two workers from running the same turn, and wakes the next queued turn when it is safe to run.

Live Turn Streaming is the viewing window while that work runs. It sends live updates such as text, tool activity, costs, and final status to connected clients. A local hub handles updates inside one process, while Redis Streams let other processes follow along too. Late or reconnecting viewers can still learn how the turn ended.

The two package files simply make these runtime folders importable; they add no behavior themselves.

## Sub-stages

- [Admission and Queue Coordination](stage-7.1.md) `stage-7.1` — 4 files
- [Live Turn Streaming](stage-7.2.md) `stage-7.2` — 3 files

## Files in this stage

### Runtime Package Scaffolding
Package initializer files establish the import structure for runtime and turn-handling modules without defining behavior.

### `core/src/ufo/runtime/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That lets other parts of the project write imports that refer to `ufo.runtime` and to files inside that folder.

There is no code here, so nothing runs directly from this file. Its value is structural: it tells Python and project tools that the `runtime` folder is a named part of the `ufo` code tree. Without it, some import styles or tooling setups might fail to recognize `ufo.runtime` as a package, especially in environments that expect traditional Python packages.

A simple analogy is a label on a drawer. The drawer may contain useful tools in other files, but this label is what lets the rest of the system find the drawer by name.


### `core/src/ufo/runtime/turns/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means other parts of the project can refer to code under `ufo.runtime.turns` using normal import paths. Think of it like a label on a filing cabinet drawer: the drawer may contain useful documents, but the label itself does not do the work. Without this file, depending on the Python version and packaging setup, imports from this folder might be less reliable or might not work as expected. Since the file contains no code, it does not run calculations, set up state, or change behavior at runtime. Its main value is organizational: it helps define the project’s module structure.

## 📊 State Registers Touched

- `reg-durable-database` — The main long-term database where shared business and runtime records are stored.
- `reg-workspace-member-agent-state` — The saved list of workspaces, people, memberships, seats, and agents.
- `reg-surface-routing` — The shared routing state that maps browser, Slack, iMessage, terminal, site, and object requests to the right workspace, agent, and conversation.
- `reg-access-permissions-audience` — The shared rules for who may read, use, share, or act on workspace content and conversations.
- `reg-billing-spend-ledger` — The shared accounting state for spend caps, usage charges, prepaid balances, BYOK billing, and ledger exports.
- `reg-conversation-turn-queue` — The durable state of conversations and turns, including admission, ordering, current runner, lifecycle status, and queued work.
- `reg-inbound-message-queue` — The saved queue of incoming external messages waiting to be rendered, ordered, deduplicated, and admitted as turns.
- `reg-live-turn-stream` — The live event feed that lets clients and other processes watch a running turn and learn how it ended.
- `reg-transcript-history` — The saved conversation transcript, summaries, compactions, and access records that preserve what happened in a chat.
- `reg-runtime-fleet-heartbeats` — The fleet-wide record of which runtime processes are alive and what work they may be responsible for.
- `reg-delegation-workflows` — The saved state for subagents, parent-child turns, objectives, workflow checkpoints, pending deliveries, and recovery.
- `reg-observability-trace` — The logs, metrics, traces, health signals, and trace links used to understand what the system is doing.
- `reg-active-turn-cancellation-handles` — The in-process registry of currently running turn/workflow tasks and cancellation handles used to stop active work before marking it cancelled durably.
- `reg-redis-service-pool` — The shared Redis client/connection and stream/cache coordination state used by live turn streaming, workers, and Redis-backed extension stores.
- `reg-turn-runtime-snapshot` — The per-turn frozen runtime configuration and generated references used to run, recover, bill, and debug a turn consistently after settings change.

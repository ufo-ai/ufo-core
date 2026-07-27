# Reply delivery, terminal state commit, cleanup, and shutdown  `stage-17`

This stage is the system’s “finish line” for a piece of work. After the assistant has produced an answer, stopped early, failed, or been cancelled, the system must make that ending visible and safe. It sends the final reply back to the place that asked for it, such as Slack, the web app, or a terminal. It also closes any live output stream, records the final status, shares any signed files or artifacts, and cleans up temporary resources like browser sessions, tools, sandboxes, and background callbacks. During shutdown, it also tries to leave no half-finished work behind, and later startup can recover abandoned work safely.

The directly included file, `cancellation.py`, provides the shared “stop button” for a running turn. It first tells the active workflow to stop, so child tasks and tools can wind down. Only after that does it mark the turn as cancelled in the database, keeping the recorded state in step with what actually happened.

## Files in this stage

### Reply delivery, terminal state commit, cleanup, and shutdown
### `core/src/ufo/cancellation.py`

`domain_logic` · `cross-cutting cancellation`

A “turn” is a unit of work that can have its own durable workflow, meaning the system remembers and can recover it even after failures. Cancelling is tricky because a turn may have child turns, and because the database record must not lie about what happened. This file solves the smallest safe piece of that problem: cancel exactly one turn.

The key rule is: first cancel the workflow, then write “cancelled” into the turn row. That order matters. If the system crashes after telling the workflow to stop but before updating the row, the row still looks unfinished, so later recovery or reconciliation can try again. But if the row were marked cancelled first and the workflow was never actually stopped, the database would say one thing while the work kept running. This file prevents that bad state.

It also avoids disturbing turns that are already finished. Before cancelling, it checks the turn’s current status in the database. If the turn is already in a final state, it leaves it alone. If it is still active, it asks DBOS, the durable workflow system, to cancel the workflow with the turn’s ID, then updates the turn row to a cancelled terminal state. Think of it like pulling the emergency brake before writing in the logbook that the machine has stopped.

#### Function details

##### `cancel_one_turn`  (lines 23–58)

```
async def cancel_one_turn(client: DBOSClient, turn_id: UUID) -> bool
```

**Purpose**: Cancels one turn safely and records that cancellation in the database only after the durable workflow has been told to stop. It returns true only when this call actually changed the turn from an active state to cancelled.

**Data flow**: It receives a DBOS client and a turn ID. First it opens a database transaction and reads the turn’s current status. If the status is already final, it stops and returns false. If the turn is still active, it asks DBOS to cancel the workflow whose ID matches the turn ID. After that, it opens another database transaction and updates the turn row to the cancelled status, stores a cancelled terminal frame, and refreshes the update time. The final output is true if exactly one active row was changed, otherwise false.

**Call relations**: This is the shared cancellation step used by higher-level cancel paths, such as user- or tool-driven cancellation and reconciliation of child turns. Inside the flow, it relies on `workspace_tx` to read and write the durable turn row, uses SQLAlchemy to build the database queries, and hands the actual workflow stop request to `DBOSClient.cancel_workflow_async` before it writes the cancelled result back to the database.

*Call graph*: 4 external calls (cancel_workflow_async, select, update, workspace_tx).

## 📊 State Registers Touched

- `reg-auth-session` — The login and token state that proves who a user or client is across gateway, web, terminal, and admin requests.
- `reg-surface-installations` — The stored links between outside surfaces, workspaces, channels, conversations, and agents.
- `reg-conversation-transcript` — The stored conversation history, messages, files, speakers, and outcomes that later stages read and append to.
- `reg-turn-state` — The durable status of each unit of agent work, including whether it is waiting, running, paused, finished, failed, or cancelled.
- `reg-live-stream` — The live feed of turn updates, text chunks, tool events, costs, and final frames that clients and debuggers can watch.
- `reg-runtime-fleet` — The shared heartbeat and ownership records that show which server processes are alive and which work they are responsible for.
- `reg-cancellation-state` — The shared stop signal state used to cancel active turns, child tasks, tools, and abandoned work safely.
- `reg-tool-context` — The per-run authority envelope that gives tools only the workspace, credentials, cleanup hooks, and permissions they are allowed to use.
- `reg-sandbox-session` — The sandbox handle and lifecycle state for the safe workspace where code, files, browsers, and commands run.
- `reg-workspace-storage` — The shared file, blob, artifact, and mount state that stores workspace bytes and files shared back to users.
- `reg-browser-session` — The browser automation connection state used when tools need a controlled browser for a turn.
- `reg-subagent-tree` — The shared parent-child work structure for delegated agents, including child turns, messages, waits, and cancellations.
- `reg-scheduled-jobs` — The background job and scheduled-task state that records what should run later, what is claimed, and what repeats.
- `reg-observability-context` — The shared tracing, metrics, structured logs, and trace-parent links used to understand work across processes and turns.
- `reg-db-connection-pool` — The process-global database engine, sessions, transactions, and connection pool shared by requests, workers, jobs, and shutdown cleanup.
- `reg-outbound-delivery-queue` — The durable pending, sent, failed, and retry state for replies or notifications that must be delivered back to external surfaces such as Slack.
- `reg-redis-backplane-pool` — The optional Redis client/backplane connection state used to share live stream infrastructure across server processes.
- `reg-turn-admission-context` — Durable per-turn requester/speaker/on-behalf-of, timezone, surface context, and authorization-link metadata used to attribute, resume, and safely handle work.
- `reg-turn-replay-journal` — Durable per-turn execution checkpoints for model calls, tool results, and side-effect/idempotency markers used to resume work without duplicating paid calls or irreversible actions.

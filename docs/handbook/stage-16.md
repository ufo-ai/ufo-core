# Turn teardown, cleanup, and cancellation finalization  `stage-16`

This stage happens at the end of a unit of work, whether that work finished normally or was stopped early. Its job is to leave the system tidy and honest. It releases things that were borrowed during the turn, such as browser sessions, sandbox leases, terminal state, live streams, and child work that may still be running. It also records what cleanup succeeded, marks workflows as complete or cancelled, and saves enough state so unfinished durable work can be recovered later.

The file cancellation.py provides the shared “stop this turn” procedure. A turn is one run of work through the system. When cancellation is requested, this code first tells the active workflow to stop, so the moving parts have a chance to wind down safely. Only after that does it update the database to say the turn was cancelled. That order matters: it prevents the records from claiming the work is stopped while the workflow is still running in the background.

## Files in this stage

### Turn teardown, cleanup, and cancellation finalization
### `core/src/ufo/runtime/turns/cancellation.py`

`domain_logic` · `cancel handling`

A “turn” is one unit of work in a conversation. Some turns can start subagents, and those subagents run as their own durable DBOS workflows, meaning their progress is tracked outside normal process memory. This file does not cancel an entire tree of related turns. Instead, it is the careful, reusable primitive for cancelling exactly one turn.

The important rule here is order: first cancel the live workflow, then mark the turn row in the database as cancelled. That matters because the database is the long-term source of truth. If the program crashes halfway through, it is safer to leave the row still non-terminal, so recovery code can try again, than to write “cancelled” before the workflow was actually told to stop.

The code reads the turn row, checks whether it is still active, asks DBOS to cancel the workflow attempt that owns it, then locks and rechecks the row before writing the cancelled result. This recheck prevents a race where another worker may have claimed or finished the turn while cancellation was happening. If the owner changed, it loops and cancels the new owner instead.

When cancellation succeeds, it records a terminal frame that still includes any objects the turn had already created. It then emits a metric for monitoring and asks the dispatcher to start the next waiting turn in the same conversation.

#### Function details

##### `cancel_one_turn`  (lines 24–102)

```
async def cancel_one_turn(client: DBOSClient, turn_id: UUID) -> TerminalFrame | None
```

**Purpose**: Cancels one turn if it is still active, and records that cancellation permanently in the database. It is used when something wants to stop a turn without risking a database record that says “cancelled” while the workflow was never actually cancelled.

**Data flow**: It takes a DBOS client and a turn ID. It reads the turn row from the database, checks whether the turn is still in a non-final state, and uses the row’s running attempt ID, or the turn ID as a fallback, to ask DBOS to cancel the workflow. It then rereads and locks the row, confirms the same attempt still owns it, builds a cancelled TerminalFrame containing any object references already stored on the row, and updates the row to a cancelled terminal state. If the turn was missing, already finished, or changed ownership mid-cancel, it either returns None or retries. On success, it emits a monitoring metric, dispatches the next turn for the conversation, and returns the committed terminal frame.

**Call relations**: This function is the shared cancellation path for callers such as cancellation tools, evaluators, and reconcilers. Inside its flow it opens database transactions with workspace_tx, uses SQLAlchemy select and update statements to inspect and change the turn row, calls DBOSClient.cancel_workflow_async to stop the durable workflow, validates stored created-object references through ObjectRef.model_validate, creates a TerminalFrame for the cancelled result, reports the outcome with emit_metric and turn_profile, then hands control to dispatch_next_turn so the conversation can continue with whatever work is next.

*Call graph*: 9 external calls (__init__, model_validate, cancel_workflow_async, select, update, workspace_tx, emit_metric, turn_profile, dispatch_next_turn).

## 📊 State Registers Touched

- `reg-conversation-records` — The durable conversation state, including conversation identity, title, surface label, sandbox handle, audience, and related metadata.
- `reg-turn-queue-state` — The durable queue of conversation turns, including admission source, run claim, parked state, resume state, and final status.
- `reg-live-updates-delivery` — The live reply and notification delivery state used to stream running turns and safely deliver mid-turn or delayed messages once.
- `reg-runtime-fleet-claims` — The attendance and claim sheet for running service processes, including heartbeats, work ownership, and surface listener claims.
- `reg-cancellation-cleanup-state` — The shared stop-and-cleanup state that records when active turns, workflows, child work, sandboxes, and streams are being wound down.
- `reg-sandbox-handles` — The durable handles and leases that let conversations reconnect to their sandbox, files, ports, hosted previews, and work directories.
- `reg-execution-environment-policy` — The shared rules for where commands and tools may run, such as local execution, Docker, cloud sandboxes, terminals, and browser sessions.
- `reg-schedules-automations` — The durable alarm clock for future work, pauses, monitors, source-change triggers, notification inbox items, and extension jobs.
- `reg-delegation-state` — The parent-child work state that tracks subagent turns, their contracts, trace links, pending results, and delivery back to the parent.
- `reg-observability-trace` — The tracing, health, logging, and traceparent state used to connect work across turns, subagents, workers, and cleanup.
- `reg-durable-workflow-checkpoints` — Saved workflow execution/checkpoint state used to resume, repair, cancel, or finalize long-running workflows after pauses, crashes, or worker handoff.
- `reg-browser-automation-sessions` — Live browser automation contexts, pages, cookies, downloads, screenshots, and backend session handles used by tool execution and released during teardown.
- `reg-running-command-process-state` — Active shell commands, subprocesses, terminal tasks, process identifiers, execution logs, and interrupt state tied to conversations or sandboxes.
- `reg-tool-bridge-invocation-state` — Durable request/result state for sandbox-to-host tool bridge calls, including pending bridge invocations, approvals, idempotency, and returned outputs.
- `reg-service-worker-lifecycle-state` — Process-local supervisor state for background loops and workers, including async task handles, startup readiness, shutdown signals, and drain status not represented by durable job tables.

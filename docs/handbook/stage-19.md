# Turn Completion, Cleanup, and Service Teardown  `stage-19`

This stage is the system’s “put everything away” step. It runs after a unit of work, called a turn, finishes, fails, or is cancelled, and it also helps during application shutdown. Its job is to make the final state trustworthy: save the last transcript, finish usage or billing records, mark queued jobs as done or failed, and clean up anything the turn borrowed, such as sandboxes, browser sessions, provider clients, proxy connections, containers, or remote machines. It also runs cleanup callbacks for tools and removes scheduled tasks that have expired.

The shared piece here is `cancellation.py`. It defines the safe way to cancel a turn. A cancellation is not just a database label; the running workflow must first be told to stop, like signaling a worker before closing their task record. Only after that stop signal is sent does the system record the turn as cancelled. This ordering helps avoid half-running work being marked as already finished.

## Files in this stage

### Turn Completion, Cleanup, and Service Teardown
### `core/src/ufo/cancellation.py`

`domain_logic` · `cancellation and recovery paths`

In this system, a “turn” is a unit of work, and some turns can start other turns, called subagents. Each turn has its own durable workflow, meaning DBOS keeps track of it so it can survive crashes and restarts. Cancelling one workflow does not automatically stop its child workflows, so this file focuses on one clear job: cancel exactly one turn, correctly and safely.

The important rule here is “cancel first, write cancelled second.” The code first checks the turn row in the database. If the turn does not exist, or it has already finished, it leaves it alone. If the turn is still active, it asks DBOS to cancel that turn’s workflow. Only after that cancel request has been made does it update the turn’s database row to say it ended as cancelled.

This ordering matters. Think of it like putting a stop sign on a machine only after you have actually pressed the stop button. If the program crashes halfway through, the database will not falsely say “cancelled” unless the workflow was already told to stop. After the row is updated, the file also records a metric so operators can count cancelled turns by profile.

#### Function details

##### `cancel_one_turn`  (lines 24–71)

```
async def cancel_one_turn(client: DBOSClient, turn_id: UUID) -> bool
```

**Purpose**: Cancels one turn if it is still running or waiting, and records that cancellation in the durable database. It returns true only when this call actually changed the turn into the cancelled state.

**Data flow**: It receives a DBOS client and a turn ID. It reads the turn’s current status and subagent profile from the database. If the turn is missing or already terminal, it returns false. Otherwise, it asks DBOS to cancel the workflow for that turn ID, then updates the turn row to status `cancelled`, stores a cancelled terminal record, and refreshes the update time. If the database update wins, it emits a cancellation metric and returns true; if another process finished the turn first, it returns false.

**Call relations**: This is the shared cancellation primitive used by higher-level cancel paths such as drivers, tools, or reconcilers. Inside its flow, it opens database transactions with `workspace_tx`, builds database queries with SQLAlchemy, asks `DBOSClient.cancel_workflow_async` to stop the durable workflow, and then reports the final cancellation through `emit_metric`, using `turn_profile` to label the metric.

*Call graph*: 6 external calls (cancel_workflow_async, select, update, workspace_tx, emit_metric, turn_profile).

## 📊 State Registers Touched

- `reg-transcript-state` — The shared conversation notebook containing saved messages, model events, summaries, and compaction records.
- `reg-turn-run-state` — The durable job ticket for each agent turn, including admission source, queue status, claim owner, parent turn, and final result.
- `reg-cancellation-state` — The shared stop signal and cancellation record used to safely halt turns, child turns, jobs, and cleanup work.
- `reg-runtime-fleet-state` — The live fleet heartbeat table that says which runtime processes are alive and what stranded work they may own.
- `reg-tool-execution-context` — The per-turn safety envelope that tells tools which files, credentials, browser sessions, memory, accounts, and subagents they may use.
- `reg-usage-accounting-ledger` — The spending ledger that records model usage, egress usage, prices, caps, billing exports, and payment-related state.
- `reg-sandbox-workspace-state` — The remembered sandbox workspace for a conversation, including its backend handle, files, runtime folder, and cleanup ownership.
- `reg-egress-network-state` — The controlled network exit state, including proxy configuration, certificates, allowed destinations, metering, and last-moment credential injection.
- `reg-browser-session-state` — The live browser-control session state used to click, read pages, download files, recover sessions, and route sandbox browser links.
- `reg-subagent-workflow-state` — The shared parent-child workflow state used when an agent delegates work to helper agents and waits for or cancels them.
- `reg-scheduled-task-state` — The durable timers and recurring jobs that remember what should run later, whether it is paused, expired, claimed, or rescheduled.
- `reg-live-update-hub` — The short-lived stream of progress updates, tool activity, costs, final answers, and Redis fan-out frames for live viewers.
- `reg-artifact-blob-store` — The shared file and blob store for generated artifacts, copied outputs, download records, and signed access links.
- `reg-observability-trace-state` — The shared logging, metrics, trace IDs, trace parents, and redaction context used to follow work across processes without leaking secrets.
- `reg-database-connection-pool` — The live database engine/session pool and transaction doorway shared by migrations, request handlers, workers, and shutdown cleanup.
- `reg-background-job-state` — The durable and in-memory background job registry, candidate queue, claims, retries, and worker progress for non-turn jobs such as sync, billing, evaluation, and cleanup.
- `reg-repl-scratchpad-state` — The Python and JavaScript REPL scratchpad sessions, files, and execution state kept for agent experimentation across tool calls or turns.
- `reg-external-client-pools` — The live reusable HTTP/provider client sessions and connection pools for model and connector calls, including lifecycle cleanup handles.
- `reg-turn-cleanup-callback-state` — The per-turn registry of cleanup callbacks and borrowed-resource finalizers that tools add during execution and completion/teardown later drains.
- `reg-service-lifecycle-state` — The process-wide lifecycle state containing startup task handles, shutdown signals, and service cleanup hooks drained during teardown.

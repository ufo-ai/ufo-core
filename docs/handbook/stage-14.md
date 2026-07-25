# Result commit, outbound delivery, cleanup, and shutdown  `stage-14`

This stage is the system’s “finish the job and tidy the room” step. It runs after a turn of work has reached an answer, failed, been parked for later, or been cancelled. Its job is to make the final state durable, send the last visible updates to users, make any produced files available, and clean up resources that were only needed during the turn.

The cancellation module gives the system one safe way to stop a turn. It first asks the durable workflow engine, the part that keeps long-running work reliable, to stop the running work. Then it records in the database that the turn was cancelled, so later readers see the correct final state.

The artifacts route is the file pickup counter. If a turn produced a shared file, this route lets someone download it only when they present a valid token, like a claim ticket. This works independently of chat, Slack, or other user interfaces, so artifact delivery stays available even if those surfaces are not installed.

## Files in this stage

### Cancellation and Artifact Access
Records cancelled turns through the workflow and database path, then exposes shared artifacts through verified download tokens.

### `core/src/ufo/cancellation.py`

`domain_logic` · `cancellation handling`

A “turn” is a unit of work that may also start child turns of its own. Cancelling one turn does not automatically stop its descendants, so this file deliberately solves only one small, important problem: safely cancel exactly one turn. Other parts of the system are responsible for finding and cancelling related child turns.

The key rule here is order. The workflow is cancelled first, then the database row is marked as cancelled. That matters because the database is the durable record other parts of the system trust. If the code wrote “cancelled” first and then crashed before telling the workflow engine, the database would lie: it would say the turn was stopped even though the work might still be running. This file avoids that dangerous state.

The process is like putting a stop sign on a machine only after you have actually pressed the stop button. First it checks the turn’s current status in the database. If the turn has already finished, failed, or otherwise reached an ending state, it leaves it alone. If the turn is still active, it asks DBOS, the durable workflow system, to cancel the workflow identified by the turn id. Then it updates the turn row to a cancelled terminal state, but only if the row is still non-terminal at that moment. This protects against races where the turn finishes naturally at the same time someone tries to cancel it.

#### Function details

##### `cancel_one_turn`  (lines 23–58)

```
async def cancel_one_turn(client: DBOSClient, turn_id: UUID) -> bool
```

**Purpose**: Cancels one turn safely and durably. It is used when some part of the system wants a turn to stop, while preserving the rule that a database row may say “cancelled” only after the workflow engine has already been told to cancel.

**Data flow**: It receives a DBOS client and a turn id. It first reads that turn’s status from the database. If the turn is already in a final state, it returns False and changes nothing. If the turn is still active, it asks DBOS to cancel the workflow with that turn id. After that, it writes the cancelled status and cancelled terminal record back to the database, but only if the turn is still non-terminal. It returns True only when this call actually changed the database row to cancelled.

**Call relations**: This function is the shared cancellation step used by different cancellation paths, such as an evaluation driver, a subagent-cancel tool, or a reconciler that cleans up cancellation state. Inside its flow, it opens database transactions through `workspace_tx`, builds database reads and writes with SQLAlchemy, and calls `DBOSClient.cancel_workflow_async` before committing the cancelled state so the durable workflow and the durable row stay in the right order.

*Call graph*: 4 external calls (cancel_workflow_async, select, update, workspace_tx).


### `core/src/ufo/surfaces/artifacts.py`

`io_transport` · `request handling`

This file is the guarded doorway for downloading stored files. Other parts of the system can create a special artifact link for a file. That link includes a token, which is like a signed permission slip. When someone visits the link, this route checks the permission slip before sending any bytes.

The route is mounted in the core application, so it is always available. That matters because file links may be shown in different places, such as the web interface or Slack, and those links still need one reliable place to point to.

On each request, the code gets two shared application objects: the blob store, which is where file bytes live, and the artifact token secret, which is the private key used to verify that a token was really made by this deployment. If the token is missing, the request is rejected. If the token is invalid or expired, it is also rejected. If the token is valid but the file no longer exists, the route returns a not-found error.

When everything checks out, the file is returned as a stream. Streaming means the server sends the file in pieces instead of loading the whole thing into memory at once, like pouring water through a hose rather than filling a giant bucket first. That keeps large downloads from using too much memory.

#### Function details

##### `download`  (lines 26–53)

```
async def download(request: Request, token: str='') -> StreamingResponse
```

**Purpose**: This is the HTTP download endpoint for artifact files. It verifies that the caller has a valid artifact token, checks that the requested stored file exists, and then streams the file back to the caller.

**Data flow**: It receives the incoming web request and an optional token string from the URL. It reads the blob store and token secret from the application state, rejects the request if the token is missing, asks `verify_artifact_token` to decode and validate the token using the current UTC time, checks whether the named blob exists, and prepares a download filename header if the token includes one. If all checks pass, it returns a `StreamingResponse` that reads the file stream from the blob store and sends it to the client as raw download bytes.

**Call relations**: FastAPI calls this function when a GET request arrives at the artifact download path. Inside the request flow, it delegates token checking to `ufo.artifact_token.verify_artifact_token`, uses `datetime.now` to check time-based token validity, uses `urllib.parse.quote` to safely format non-plain filenames for the download header, and hands the final byte stream to FastAPI's `StreamingResponse` so the server can send the file without buffering it all in memory.

*Call graph*: 5 external calls (now, HTTPException, StreamingResponse, verify_artifact_token, quote).

## 📊 State Registers Touched

- `reg-identity-and-session-tokens` — The identities, bearer tokens, gateway tokens, operator sessions, and other passes that prove who is allowed in.
- `reg-sandbox-session-policy` — The safe execution room state: sandbox handles, mounted workspace files, storage access, and restrictions.
- `reg-browser-session` — The browser connection state used when a turn needs a Chrome endpoint or computer-use actions.
- `reg-conversation-transcript` — The saved conversation thread, messages, transcript edits, and compacted summaries.
- `reg-turn-queue-run-state` — The durable state of each agent turn, including whether it is queued, claimed, running, parked, finished, failed, or cancelled.
- `reg-live-event-stream` — The live progress channel that lets clients attach, resume, and receive streamed turn updates.
- `reg-inbound-surface-state` — The stored inbound messages and surface delivery keys that connect Slack, web, terminal, and other fronts to conversations.
- `reg-artifact-blob-store` — The shared file and blob storage used for uploaded content, generated artifacts, and token-protected downloads.
- `reg-runtime-instance-fleet` — The heartbeat records for live worker and runtime processes, used to detect dead workers and clean up abandoned work.
- `reg-observability-trace-state` — The logs, metrics, traces, traceparent links, and redaction rules used to monitor work safely.
- `reg-subagent-work-tree` — The parent-child turn links, delegated work state, and message flow between main agents and subagents.
- `reg-database-connection-pool` — The process-wide database engine/session factory and connection pool used by request handlers, workers, schedulers, and persistence code.
- `reg-action-proposal-state` — Durable non-governance proposals created by agents or tools for later user/operator review, approval, rejection, or commit.
- `reg-secret-keyring` — Loaded signing and encryption key material used to mint/verify tokens and seal/unseal protected secrets across trusted paths.
- `reg-turn-cleanup-finalizers` — Per-turn cleanup/finalizer stack for resources acquired during runtime assembly and tool execution so result commit or shutdown can release them safely.
- `reg-process-lifecycle-state` — Process-wide startup/shutdown state: background task handles, drain/cancel signals, and resource close callbacks created during service bootstrap.
- `reg-redis-connection-pool` — Process-wide Redis client/connection pool and stream backend handles used to distribute live turn events across server processes.
- `reg-agent-todo-goal-state` — The agent-maintained goal/todo checklist state that tools update and later prompt/runtime assembly can reload as working context.

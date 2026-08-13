# Runtime Service Startup and Fleet Coordination  `stage-5`

This stage happens after the app has loaded its settings and add-ons. Its job is to bring the long-running runtime services online and keep them coordinated while the system is running. It is like opening the service doors, then making sure every worker keeps checking in.

`proxy_serve.py` starts the shared egress proxy. An egress proxy is a controlled doorway from isolated workspaces to approved outside services, such as model APIs, storage, or connector hosts. The file gathers the needed settings, secrets, database access, and security certificates, then runs one proxy process that can safely serve many workspaces.

`runtime_instance.py` keeps each running server process visible to the wider fleet. It writes heartbeat records, which are regular “I am still alive” signals. It also runs cleanup loops in the background. If a workflow was queued or owned by a process that disappeared, it can recover that work. If a parent task is cancelled, it spreads that cancellation to child tasks that are still running. Together, these files make startup durable and keep live work from being stranded.

## Files in this stage

### Service Startup and Fleet Coordination
Starts the shared egress proxy service and keeps runtime processes visible, healthy, and coordinated across the fleet.

### `core/src/ufo/proxy_serve.py`

`entrypoint` · `startup and long-running proxy service`

A sandbox is intentionally restricted, so it cannot freely call the internet. This file builds the controlled doorway it is allowed to use. Think of it like a guarded mailroom: each outgoing request is checked against rules, the right secret may be added, usage can be priced, and the request is only sent if the current workspace is allowed to send it.

The proxy is shared across workspaces. To keep workspaces separate, it reads a run token attached to each request; that token includes the workspace ID, and database queries are explicitly filtered by that ID. Because this shared process needs to look across workspaces, it uses an owner database connection rather than a normal workspace-scoped one.

At startup, the file loads the app configuration and extension manifests, reads a stable certificate authority from environment variables, opens the credential decryptor if the active pack needs one, builds model-provider access rules from API keys in the process environment, and prepares pricing information for model usage. The proxy also loads rules for artifact storage and connector-defined internet access.

The important safety behavior is that missing critical secrets fail loudly. If no model key exists, no model egress route is created. If a pack needs injectable credentials but no decryption key is set, startup stops. If the shared certificate authority is missing, startup stops because sandboxes would not trust the proxy.

#### Function details

##### `model_rule_base`  (lines 43–62)

```
def model_rule_base(config: Config) -> tuple[Rule, ...]
```

**Purpose**: Builds the basic outbound rules that let sandboxes talk to configured model providers, such as Anthropic or OpenAI. It only enables providers whose API key is present in the environment, and it stops startup if no model provider can be reached at all.

**Data flow**: It receives the loaded configuration, reads the configured environment variable names for model API keys, and checks the current process environment for those keys. For each key that exists, it asks the model-rule builder for the network and key-injection rules, combines allowed host names into one scope rule, and returns the complete set of rules. If no allowed model host is found, it raises an error instead of returning unusable proxy rules.

**Call relations**: ProxyServe.serve calls this while assembling the proxy’s per-request rule resolver. The rules it returns become part of the base permissions that every sandbox request starts with before workspace-specific grants and credentials are considered.

*Call graph*: called by 1 (serve); 2 external calls (__init__, derive_model_rules).


##### `run`  (lines 65–86)

```
def run() -> None
```

**Purpose**: Boots the standalone proxy process. It is the high-level startup path that collects configuration, secrets, manifests, pricing, and shutdown state, then hands control to the asynchronous server.

**Data flow**: It starts with no caller-provided input and reads configuration files plus environment variables. It loads extension manifests, gets the shared egress certificate and key, finds the owner database connection string, creates a credential store when needed, builds pricing data, constructs a ProxyServe object, logs that startup is beginning, and runs the server until it exits.

**Call relations**: This is the top-level coordinator for this file. It calls _egress_ca, owner_dsn, and _credential_store to validate required runtime secrets, then creates ProxyServe and uses asyncio.run to enter ProxyServe.serve, where the actual listening proxy is started.

*Call graph*: calls 3 internal fn (_credential_store, _egress_ca, owner_dsn); 9 external calls (__init__, Event, run, load_config, injecting_slots, load_manifests, model_registry, init_o11y, log).


##### `_egress_ca`  (lines 89–100)

```
def _egress_ca() -> tuple[str, str]
```

**Purpose**: Reads the shared certificate authority used by the proxy to create trusted certificates for intercepted sandbox traffic. This keeps sandbox trust stable across proxy restarts and across workspaces.

**Data flow**: It reads two environment variables: one for the certificate and one for the private key, both in PEM text form. If both are present, it returns them as a pair. If either is missing, it raises an error because the proxy would otherwise create certificates that sandboxes do not trust.

**Call relations**: run calls this during startup before creating ProxyServe. The certificate and key it returns are later passed into EgressProxy by ProxyServe.serve, so the proxy can sign per-host certificates using the shared trusted authority.

*Call graph*: called by 1 (run).


##### `owner_dsn`  (lines 103–116)

```
def owner_dsn(config: Config) -> str
```

**Purpose**: Finds the database connection string the shared proxy should use. It deliberately uses an owner-level database connection because one proxy process serves many workspaces, while request scoping is enforced later using the workspace ID from the run token.

**Data flow**: It receives the loaded configuration, then checks the UFO_OWNER_DSN environment variable first and the configuration’s owner database URL second. If neither exists, it raises an error. If it finds a PostgreSQL URL, it rewrites the beginning so the async psycopg driver is used, then returns the adjusted connection string.

**Call relations**: run calls this during startup and passes the result into ProxyServe. ProxyServe.serve later gives this database string to init_db before verifying the database is reachable and serving proxy traffic.

*Call graph*: called by 1 (run).


##### `_credential_store`  (lines 119–133)

```
def _credential_store(config: Config, slots: tuple[CredentialSlot, ...]) -> CredentialStore | None
```

**Purpose**: Creates the decryptor the proxy uses to read workspace-specific provider secrets, but only when the active extension pack needs injectable credentials. This lets the proxy add the right secret to an outgoing request without storing that secret directly in the sandbox.

**Data flow**: It receives the configuration and the credential slots declared by loaded manifests. It reads the configured credential-key environment variable. If the key is present, it creates a Fernet encryptor/decryptor and wraps it in a CredentialStore. If the key is missing but credential slots exist, it raises an error. If no slots need credentials, it returns None.

**Call relations**: run calls this after loading manifests and identifying injectable slots. The returned CredentialStore, or None, is passed into ProxyServe and then into PerAgentRules so per-workspace secrets can be decrypted and inserted into approved outbound requests.

*Call graph*: called by 1 (run); 2 external calls (__init__, Fernet).


##### `ProxyServe.serve`  (lines 151–182)

```
async def serve(self) -> None
```

**Purpose**: Runs the proxy service itself. It prepares the database, builds all rule sources, starts the network proxy, waits for a shutdown signal, and then stops the proxy cleanly.

**Data flow**: It uses the ProxyServe object’s stored configuration, manifests, database string, certificate material, credential store, pricing table, and shutdown event. It registers signal handlers, initializes and checks the database, derives artifact-store rules, combines model rules with artifact rules and manifest rules, creates the per-agent rule resolver, creates the EgressProxy, and starts listening on the configured port and public URL. When the shutdown event is set, it stops the proxy using the configured graceful-shutdown timeout.

**Call relations**: run enters this method through asyncio.run. Inside the service startup, it calls model_rule_base for model-provider access, uses RunTokenCodec.from_env so requests can be tied to run tokens, builds PerAgentRules to decide what each request may do, and hands that resolver to EgressProxy. After EgressProxy.start begins serving traffic, this method stays alive until an operating-system shutdown signal triggers the stored event.

*Call graph*: calls 2 internal fn (model_rule_base, from_env); 13 external calls (__init__, __init__, __init__, get_running_loop, blob_store_for, init_db, verify_db_reachable, connector_clis, injecting_slots, log (+3 more)).


### `core/src/ufo/runtime_instance.py`

`orchestration` · `startup, main loop, teardown`

A serve process in this system is like a worker in a shared workshop. Before it can safely take durable work, it writes a small row in the database saying “I am here,” then keeps refreshing that row as a heartbeat. Other processes use those rows to tell whether a workflow is owned by a living process or stranded under one that died.

This file provides three background loops. `Heartbeat` keeps this process’s row fresh every few seconds and removes it during graceful shutdown. `ExecutorRecovery` looks for DBOS workflows that are still marked pending. DBOS is the durable workflow system: it remembers workflow progress so work can resume after crashes. If a pending workflow belongs to an executor whose heartbeat is stale or missing, the recovery loop asks DBOS to put that work back on its queue. It also fixes a subtler case: this process may still be alive, but no local task is actually running a workflow it claimed. To avoid racing with workflows that are just starting or just finishing, it only releases such claims after seeing them absent twice.

`CancelReconciler` handles cancellation inheritance. Cancelling one turn only cancels that turn immediately. This sweeper later finds live child or grandchild turns under a cancelled ancestor and cancels them too. All loops log failures and keep going, because a temporary database or DBOS problem should not permanently stop the fleet’s self-repair.

#### Function details

##### `record_fleet_seat`  (lines 40–55)

```
async def record_fleet_seat(instance_id: UUID) -> None
```

**Purpose**: This function creates the database row that says a serve process exists in the shared fleet. It runs before DBOS starts so that this process is not mistaken for a dead executor while it is booting.

**Data flow**: It receives this process’s unique instance id. It opens an owner-level database transaction, inserts a `runtime_instance` row with no workspace attached, stamps the current time as the heartbeat and creation time, then writes a log message. It returns nothing, but the database now contains the process’s liveness marker.

**Call relations**: This is the first part of the liveness story. After it records the seat, `Heartbeat.run` can keep that row fresh, and `ExecutorRecovery.sweep` can later use runtime-instance rows to decide which workflow executors are alive.

*Call graph*: 3 external calls (insert, owner_tx, log).


##### `Heartbeat.run`  (lines 68–78)

```
async def run(self) -> None
```

**Purpose**: This is the never-ending loop that keeps the current process’s heartbeat alive. It protects the process from being falsely treated as dead because of one missed database update.

**Data flow**: It repeatedly calls `Heartbeat.beat` to update the database timestamp. If a database error happens, it logs the error class instead of stopping. Then it sleeps for the heartbeat interval and tries again.

**Call relations**: This loop calls `Heartbeat.beat` on every tick. It is part of the background runtime machinery that makes `ExecutorRecovery._live_executors` trustworthy, because recovery depends on fresh heartbeat rows to know which executors are still alive.

*Call graph*: calls 1 internal fn (beat); 2 external calls (sleep, log).


##### `Heartbeat.beat`  (lines 80–90)

```
async def beat(self) -> None
```

**Purpose**: This function performs one heartbeat update for this process. It refreshes the row that tells the fleet “this executor is still alive.”

**Data flow**: It reads the instance id stored on the `Heartbeat` object. It opens a database transaction and updates the matching `runtime_instance` row, setting `heartbeat_at` and `updated_at` to the database’s current time. It returns nothing, but the row is now fresh.

**Call relations**: `Heartbeat.run` calls this repeatedly. The fresh timestamp it writes is later read by `ExecutorRecovery._live_executors`, which uses it to avoid recovering work from a process that is still alive.

*Call graph*: called by 1 (run); 2 external calls (update, owner_tx).


##### `Heartbeat.retire`  (lines 92–98)

```
async def retire(self) -> None
```

**Purpose**: This function removes the current process’s liveness row during graceful shutdown. That lets peers see right away that this seat is gone instead of waiting for the heartbeat to become stale.

**Data flow**: It reads the instance id from the `Heartbeat` object. It opens a database transaction and deletes the matching `runtime_instance` row. It returns nothing, but the process is no longer advertised as alive.

**Call relations**: It is called by `core/src/ufo/serve._stop_executor` when the executor is stopping. This completes the lifecycle that starts with `record_fleet_seat` and is maintained by `Heartbeat.run`.

*Call graph*: called by 1 (_stop_executor); 2 external calls (delete, owner_tx).


##### `ExecutorRecovery.run`  (lines 127–133)

```
async def run(self) -> None
```

**Purpose**: This is the never-ending recovery loop for stuck durable workflows. It keeps retrying so a temporary failure in the database or DBOS does not permanently stop recovery.

**Data flow**: It sleeps for the configured recovery interval, then calls `ExecutorRecovery.sweep`. If the sweep hits a database or DBOS error, it logs the error class and continues with the next interval. It does not return during normal operation.

**Call relations**: This loop drives `ExecutorRecovery.sweep`, which does the real inspection and recovery work. It runs alongside the heartbeat loop in every serve process, so any surviving process can help recover stranded work.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `ExecutorRecovery.sweep`  (lines 135–145)

```
async def sweep(self) -> None
```

**Purpose**: This function performs one recovery pass over pending workflows. It finds work owned by dead executors and asks DBOS to recover it, then also releases this process’s own claims that no local task is actually running.

**Data flow**: It first gets pending workflows from `_pending_workflows`. It gathers the executor ids attached to those pending rows, asks `_live_executors` which executor ids still have fresh heartbeat rows, and treats the difference as dead. For each dead executor, it calls DBOS recovery in a worker thread and logs how many workflows were recovered. Finally, it passes the pending list to `_release_unexecuted_claims` to fix abandoned claims from this still-live process.

**Call relations**: `ExecutorRecovery.run` calls this on a timer. This function coordinates the helper methods: `_pending_workflows` supplies candidates, `_live_executors` supplies the liveness check, and `_release_unexecuted_claims` handles the special case that only this process can judge safely.

*Call graph*: calls 3 internal fn (_live_executors, _pending_workflows, _release_unexecuted_claims); called by 1 (run); 2 external calls (to_thread, log).


##### `ExecutorRecovery._pending_workflows`  (lines 147–159)

```
async def _pending_workflows(self) -> list[WorkflowStatus]
```

**Purpose**: This function asks DBOS for pending workflows, oldest first, so recovery can see the work most likely to be stranded. It logs when the scan hits the configured limit, because that means there may be more pending work beyond this batch.

**Data flow**: It sends a DBOS list request in a worker thread, asking only for workflow status rows and not loading inputs or outputs. It receives a list of pending workflow statuses. If the list size equals the scan limit, it logs that the limit was reached, then returns the list.

**Call relations**: `ExecutorRecovery.sweep` calls this at the start of each recovery pass. The returned workflow statuses are then used both to identify dead executors and to check this process’s own abandoned claims.

*Call graph*: called by 1 (sweep); 2 external calls (to_thread, log).


##### `ExecutorRecovery._live_executors`  (lines 161–171)

```
async def _live_executors(self) -> set[str]
```

**Purpose**: This function returns the executor ids that are considered alive right now. An executor is alive if its runtime-instance row has a heartbeat newer than the stale cutoff.

**Data flow**: It computes a cutoff time by subtracting the allowed stale interval from the current UTC time. It queries the `runtime_instance` table for rows whose heartbeat is at or after that cutoff. It converts the row ids to strings and returns them as a set.

**Call relations**: `ExecutorRecovery.sweep` calls this after finding pending workflow owners. The sweep compares pending executor ids with this live set to decide which executors are gone and should have their workflows recovered.

*Call graph*: called by 1 (sweep); 4 external calls (now, timedelta, select, owner_tx).


##### `ExecutorRecovery._release_unexecuted_claims`  (lines 173–206)

```
async def _release_unexecuted_claims(self, pending: list[WorkflowStatus]) -> None
```

**Purpose**: This function frees workflows that this process claimed but is no longer actually running. It is careful: it requires the same missing workflow to be seen in two sweeps before releasing it, which avoids disturbing work that is merely between startup or finish steps.

**Data flow**: It receives the current pending workflow statuses. It filters them to workflows whose executor id is this process’s DBOS executor id, subtracts the workflows currently listed by `_executing`, and gets a set of apparently abandoned claims. It compares that set with the previous sweep’s remembered set. Only workflows absent twice are released. Before releasing, it clears and rewrites the remembered set so a failed release does not leave stale evidence behind. For each twice-seen workflow, it checks `_executing` again; if still absent, it calls DBOS’s queue-assignment clearing method in a worker thread and logs the released claim.

**Call relations**: `ExecutorRecovery.sweep` calls this after recovering dead peers. It uses `_executing` to ask the local DBOS runtime what is truly active. Its handoff back to DBOS returns abandoned workflows to their queues so they can be dispatched again under the same workflow id.

*Call graph*: calls 1 internal fn (_executing); called by 1 (sweep); 2 external calls (to_thread, log).


##### `ExecutorRecovery._executing`  (lines 208–209)

```
def _executing(self) -> set[str]
```

**Purpose**: This function reports which workflows this process is actively running right now. It is the local truth source used to avoid recovering or releasing work that is still in progress.

**Data flow**: It reads DBOS’s active-workflows list from the `dbos` object held by `ExecutorRecovery`. It converts that list into a set of workflow ids and returns it. It does not change the database or DBOS state.

**Call relations**: `ExecutorRecovery._release_unexecuted_claims` calls this when deciding whether a claim is abandoned. Because only the current process can safely know its own active tasks, this helper is intentionally local.

*Call graph*: called by 1 (_release_unexecuted_claims).


##### `CancelReconciler.run`  (lines 233–239)

```
async def run(self) -> None
```

**Purpose**: This is the never-ending loop that spreads cancellation through a turn tree. It makes cancellation eventually reach children and grandchildren even if the original canceller only marked one turn.

**Data flow**: It sleeps for the configured interval, then calls `CancelReconciler.sweep`. If a database or DBOS error occurs, it logs the error class and continues. During normal operation it keeps running indefinitely.

**Call relations**: This loop drives `CancelReconciler.sweep` on every serve process. Because every process runs it, cancellation cleanup can continue even if the process that first cancelled a parent turn crashes.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `CancelReconciler.sweep`  (lines 241–248)

```
async def sweep(self) -> None
```

**Purpose**: This function performs one pass looking for live turns below a cancelled ancestor and cancels them. It is the mechanism that turns a local cancellation into a cascading cancellation through descendants.

**Data flow**: It opens a database transaction and runs `_orphans_query` to find non-terminal turns that have a cancelled parent, grandparent, or higher ancestor, along with each turn’s workspace. For each result, it enters that workspace context, calls `cancel_one_turn` using the DBOS client, and logs the turn id if a cancellation actually happened.

**Call relations**: `CancelReconciler.run` calls this on a timer. It relies on `_orphans_query` to identify the affected turns, then hands each turn to `cancel_one_turn`, the shared primitive that cancels a single turn and its workflow safely.

*Call graph*: calls 1 internal fn (_orphans_query); called by 1 (run); 4 external calls (cancel_one_turn, owner_tx, log, ws).


##### `CancelReconciler._orphans_query`  (lines 250–285)

```
def _orphans_query(self) -> sa.Select
```

**Purpose**: This function builds the database query that finds live turns whose ancestry includes a cancelled turn. It catches not only direct children but also deeper descendants, even if an intermediate turn has already finished normally.

**Data flow**: It starts from all turns whose status is not terminal. It builds a recursive query, meaning a query that repeatedly walks from a turn to its parent, then that parent’s parent, and so on. The walk stops once it reaches a cancelled ancestor. The final query returns distinct orphan turn ids and their workspace ids.

**Call relations**: `CancelReconciler.sweep` calls this to get the exact SQL query to run. The sweep then uses the query results to call `cancel_one_turn` for each descendant that still needs cancellation.

*Call graph*: called by 1 (sweep); 1 external calls (select).

## 📊 State Registers Touched

- `reg-effective-configuration` — The final startup settings that decide how the service, security, sandbox, proxy, and enabled packs should behave.
- `reg-turn-run-state` — The durable job ticket for each agent turn, including admission source, queue status, claim owner, parent turn, and final result.
- `reg-cancellation-state` — The shared stop signal and cancellation record used to safely halt turns, child turns, jobs, and cleanup work.
- `reg-runtime-fleet-state` — The live fleet heartbeat table that says which runtime processes are alive and what stranded work they may own.
- `reg-credential-secret-store` — The encrypted store of workspace and connector secrets, plus the requests that say which secrets a tool or proxy may reveal.
- `reg-sandbox-workspace-state` — The remembered sandbox workspace for a conversation, including its backend handle, files, runtime folder, and cleanup ownership.
- `reg-egress-network-state` — The controlled network exit state, including proxy configuration, certificates, allowed destinations, metering, and last-moment credential injection.
- `reg-observability-trace-state` — The shared logging, metrics, trace IDs, trace parents, and redaction context used to follow work across processes without leaking secrets.
- `reg-database-connection-pool` — The live database engine/session pool and transaction doorway shared by migrations, request handlers, workers, and shutdown cleanup.
- `reg-background-job-state` — The durable and in-memory background job registry, candidate queue, claims, retries, and worker progress for non-turn jobs such as sync, billing, evaluation, and cleanup.
- `reg-service-lifecycle-state` — The process-wide lifecycle state containing startup task handles, shutdown signals, and service cleanup hooks drained during teardown.

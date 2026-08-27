# Billing Ledger, Balances, and Payment Integrations  `stage-22.5`

This stage is the money meter for paid work. It runs behind the scenes while the system is doing its main job, checking whether a workspace has enough prepaid credit, recording what was used, and deciding when spending must stop. The billing package marker simply makes these billing parts importable by the rest of the code.

The accounting file is the ledger, like a detailed notebook at a shop counter. It records each unit of work, its cost, and the workspace it belongs to. From those records it can build reports, prepare usage data for export, and decide whether a spend limit has been reached.

The balance file tracks prepaid credit. It adds money or grants, subtracts usage costs, and answers the practical question: can this workspace keep running paid model work right now?

The Metronome extension connects this internal ledger to outside billing services. It sends usage to Metronome, uses Stripe for prepaid balance payments, provides billing information to chat and status pages, and runs scheduled refill and reporting jobs.

## Files in this stage

### Billing Package Setup
Defines the billing package boundary so the ledger and balance modules can be imported by the rest of the system.

### `core/src/ufo/billing/__init__.py`

`other` · `cross-cutting`

This is an empty package marker file. In Python, a folder with an `__init__.py` file is treated as a package, which means code elsewhere can refer to modules inside it using names like `ufo.billing.something`. Think of it like a label on a drawer: the drawer may contain useful billing tools, but this label itself does not do any work.

Because the file is empty, it does not set up billing rules, load configuration, connect to payment systems, or expose helper functions. Its main value is structural. It helps keep the project organized and makes imports predictable.

If this file were missing, behavior would depend on the Python version and packaging setup. In many projects, removing it can break imports or make packaging tools fail to include the folder correctly. So even though it has no executable code, it matters because it tells Python and the project tooling that `billing` is an intentional part of the `ufo` codebase.


### Ledger and Balance Enforcement
Implements internal billing records, prepaid credit tracking, usage deductions, spend-cap decisions, and allow-or-refuse checks.

### `core/src/ufo/billing/accounting.py`

`domain_logic` · `cross-cutting: turn admission, request handling, billing export, and reporting`

This file answers a simple but important question: who spent what, and are they still allowed to spend more? It writes usage into a ledger, which is like an accounting notebook where every model call, sandbox model call, image, video, or egress request gets recorded under a clear category. Token usage is priced in micro-dollars, meaning millionths of a US dollar, so small costs can be tracked without rounding errors.

The file is careful about retries. A turn can be replayed after a crash or resumed after being parked, so token billing is written in a way that updates one attempt's cumulative row and only debits the new difference. That prevents charging twice for the same work while still charging for later progress.

It also supports “bring your own key” cases. If a workspace used its own provider API key, the ledger can record the cost for reporting but skip taking money from the workspace balance, because the workspace already paid the provider directly.

On top of recording spend, the file enforces rules. Spend caps can apply to a workspace, a member, or an agent, and the tightest broken cap decides whether a turn is allowed, parked, or rejected. Balance checks separately decide whether prepaid credit is enough to start or continue. Finally, reporting and export helpers summarize the same ledger for dashboards and outside billing systems.

#### Function details

##### `applicable_caps_absent`  (lines 60–66)

```
def applicable_caps_absent(workspace_id: UUID, member_id: UUID | None, agent_id: UUID) -> bool
```

**Purpose**: Quickly answers whether the system recently checked a workspace/member/agent combination and found no spend caps for it. This avoids an unnecessary database lookup in the common case where no caps exist.

**Data flow**: It receives a workspace ID, optional member ID, and agent ID. It looks up that exact combination in a short-lived in-memory cache and compares the stored expiry time with the current clock. It returns true only if the no-cap result is still fresh.

**Call relations**: Other admission code can use this as a fast path before doing a full cap check. The cache entries it reads are written by SpendEvaluator.decide through _note_absent_caps when a real database check finds no applicable caps.

*Call graph*: 1 external calls (monotonic).


##### `_note_absent_caps`  (lines 69–78)

```
def _note_absent_caps(key: tuple[UUID, UUID | None, UUID]) -> None
```

**Purpose**: Remembers for a few seconds that a particular workspace/member/agent combination has no spend caps. This is a small performance helper, not a source of truth.

**Data flow**: It receives the exact cache key. It checks the current time, clears expired cache entries if the cache is already large, and stores a new expiry time for that key. It returns nothing and only changes the in-memory cache.

**Call relations**: SpendEvaluator.decide calls this after asking the database and finding no matching caps. Later, applicable_caps_absent can read the note and let callers skip a repeated database trip for a short time.

*Call graph*: called by 1 (decide); 1 external calls (monotonic).


##### `_total_tokens`  (lines 81–89)

```
def _total_tokens(usage: Usage) -> int
```

**Purpose**: Adds together all token categories in a usage record to get the total billable token count. This keeps token totals consistent wherever billing rows are written.

**Data flow**: It receives a Usage object containing input, output, cache-read, and cache-write token counts. It sums those fields and returns one integer total.

**Call relations**: record_turn_usage, record_workspace_usage, and record_sandbox_tokens call it before deciding whether there is anything to bill and before writing ledger amounts.

*Call graph*: called by 3 (record_sandbox_tokens, record_turn_usage, record_workspace_usage).


##### `_prompt_tokens`  (lines 92–101)

```
def _prompt_tokens(usage: Usage) -> int
```

**Purpose**: Counts the tokens that made up the prompt the provider read, including cached prompt parts. This is used later to show how much of a prompt came from cache.

**Data flow**: It receives a Usage object. It adds input tokens plus cache read and cache write token categories, leaving out output tokens. It returns that prompt-side total.

**Call relations**: The token-writing functions call this when saving ledger rows. read_turn_cost later uses the saved prompt and cache counts to compute a cache percentage.

*Call graph*: called by 3 (record_sandbox_tokens, record_turn_usage, record_workspace_usage).


##### `workspace_owns_the_key`  (lines 104–118)

```
async def workspace_owns_the_key(connection: AsyncConnection, workspace_id: UUID, key_slot: str | None) -> bool
```

**Purpose**: Checks whether a provider key slot belongs to the workspace. This decides whether the workspace paid the provider directly instead of spending platform balance.

**Data flow**: It receives a database connection, workspace ID, and optional key slot name. If no slot is given, it returns false. Otherwise it queries the credential table for that workspace and slot, and returns true if a matching row exists.

**Call relations**: BalanceGate._workspace_serves_itself calls this while deciding whether a low-balance workspace can still start a turn that uses its own provider key.

*Call graph*: called by 1 (_workspace_serves_itself); 2 external calls (execute, select).


##### `record_turn_usage`  (lines 121–256)

```
async def record_turn_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, usage: Usage, attempt: str='', pricing: Pricing=CORE_PRICING, byok: bool=False) -> None
```

**Purpose**: Records and bills the model-token usage for one turn attempt. It is designed so retries and recovery do not double-charge, while later cumulative progress is charged only for the added usage.

**Data flow**: It receives a database connection, workspace and turn IDs, model name, usage counters, attempt ID, pricing table, and a byok flag. It totals and prices the usage, finds the ledger row for this turn attempt, and either inserts it or updates it if the new usage is a larger cumulative snapshot. It debits only the newly added cost unless byok is true, and raises a conflict if the snapshots cannot safely be treated as one growing history.

**Call relations**: Turn execution calls this as model usage is finalized or recovered. It relies on _total_tokens, _prompt_tokens, Pricing.micro_usd, debit, and ledger_id_for so later readers, reports, balance checks, and exports all see one consistent billing record.

*Call graph*: calls 3 internal fn (_prompt_tokens, _total_tokens, micro_usd); 7 external calls (__init__, execute, insert, select, update, debit, ledger_id_for).


##### `read_turn_cost`  (lines 270–300)

```
async def read_turn_cost(connection: AsyncConnection, turn_id: UUID, dimension: str) -> TurnCost | None
```

**Purpose**: Reads the total cost of a turn for one ledger category, such as host tokens or sandbox tokens. It combines multiple attempts so a parked-and-resumed turn shows its real full cost.

**Data flow**: It receives a database connection, turn ID, and dimension name. It sums matching ledger rows for tokens and priced cost, reads the model, and calculates the cached prompt percentage from stored prompt/cache token counts. It returns a TurnCost object or None if nothing was billed.

**Call relations**: Terminal turn reporting can call this after usage has been recorded by record_turn_usage or record_sandbox_tokens. It reads the ledger rows those writers created rather than recalculating from in-memory state.

*Call graph*: 3 external calls (__init__, execute, select).


##### `record_workspace_usage`  (lines 303–352)

```
async def record_workspace_usage(connection: AsyncConnection, workspace_id: UUID, model: str, usage: Usage, pricing: Pricing=CORE_PRICING, byok: bool=False) -> None
```

**Purpose**: Bills model-token usage for background workspace jobs that are not tied to a turn, member, or agent. This lets workspace-wide spend totals and caps include background work.

**Data flow**: It receives a connection, workspace ID, model, usage, pricing, and byok flag. It totals and prices the usage, debits the workspace unless byok is true, and inserts a fresh ledger row with no turn ID. If there are zero tokens, it does nothing.

**Call relations**: Background jobs use this instead of record_turn_usage because there is no turn attempt to replay safely. Reports and workspace-scoped caps see these rows, while member and agent breakdowns skip them because they have no turn link.

*Call graph*: calls 3 internal fn (_prompt_tokens, _total_tokens, micro_usd); 4 external calls (execute, insert, debit, uuid4).


##### `record_egress_request`  (lines 355–384)

```
async def record_egress_request(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, amount: int=1) -> None
```

**Purpose**: Counts sandbox network egress requests for a turn. These requests are metered for visibility but priced at zero, so they do not debit balance.

**Data flow**: It receives a connection, workspace ID, turn ID, and count. It derives a stable ledger ID for that turn and egress dimension, then inserts the count or atomically adds to the existing count. It returns nothing.

**Call relations**: The egress proxy calls this when a sandbox reaches the network during a turn. It writes a ledger row separate from token rows so token billing and request counting do not collide.

*Call graph*: 2 external calls (execute, ledger_id_for).


##### `record_probe_egress_request`  (lines 387–413)

```
async def record_probe_egress_request(connection: AsyncConnection, workspace_id: UUID, amount: int=1) -> None
```

**Purpose**: Counts sandbox egress requests made by off-turn probes. Like turn egress, this is a zero-cost request count, but it is attached only to the workspace.

**Data flow**: It receives a connection, workspace ID, and count. It inserts a fresh ledger row with no turn ID, dimension egress, zero cost, and the given amount. It returns nothing.

**Call relations**: Probe or proxy code uses this when network activity is not part of a user turn. Workspace totals can include it, while member and agent reports naturally ignore it because there is no turn.

*Call graph*: 3 external calls (execute, insert, uuid4).


##### `record_sandbox_tokens`  (lines 416–492)

```
async def record_sandbox_tokens(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, usage: Usage, pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: Records and bills model-token usage made from inside a sandbox through the egress proxy. This is separate from the host-side turn model call, so the two do not double-count each other.

**Data flow**: It receives a connection, workspace ID, turn ID, model, usage, and pricing table. It totals and prices the tokens, debits the workspace for the full price, and inserts or atomically adds to a per-turn sandbox-token ledger row. It also stores token-class details for later cache and report calculations.

**Call relations**: The sandbox egress proxy calls this when in-sandbox model calls complete. It uses _total_tokens, _prompt_tokens, Pricing.micro_usd, debit, and ledger_id_for, and its rows are later read by read_turn_cost and SpendRollup.

*Call graph*: calls 3 internal fn (_prompt_tokens, _total_tokens, micro_usd); 3 external calls (execute, debit, ledger_id_for).


##### `record_image_usage`  (lines 495–514)

```
async def record_image_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, images: int, micro_usd: int) -> None
```

**Purpose**: Records generated image usage for a turn. The caller supplies the price because image pricing does not fit the normal token pricing table.

**Data flow**: It receives a connection, workspace ID, turn ID, model name, image count, and cost in micro-dollars. It passes those values to the shared media-recording helper with the images dimension. It returns nothing.

**Call relations**: Provider extensions for image generation call this after they know the provider's charge. It delegates the actual ledger write and debit to _record_media_usage.

*Call graph*: calls 1 internal fn (_record_media_usage).


##### `record_video_usage`  (lines 517–531)

```
async def record_video_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, videos: int, micro_usd: int) -> None
```

**Purpose**: Records generated video usage for a turn. It works like image recording, but counts videos and uses the videos dimension.

**Data flow**: It receives a connection, workspace ID, turn ID, model name, video count, and cost in micro-dollars. It forwards them to the shared media-recording helper with the videos dimension. It returns nothing.

**Call relations**: Provider extensions for video generation call this after a video charge is known. It shares the insertion, accumulation, and debit behavior in _record_media_usage.

*Call graph*: calls 1 internal fn (_record_media_usage).


##### `_record_media_usage`  (lines 534–575)

```
async def _record_media_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, dimension: str, model: str, amount: int, micro_usd: int) -> None
```

**Purpose**: Writes the common ledger entry for generated images or videos and debits the workspace for that media cost. This prevents the image and video paths from duplicating the same accounting rules.

**Data flow**: It receives a connection, workspace ID, turn ID, dimension, model, amount, and micro-dollar cost. It derives a stable ledger ID, debits the workspace for this increment, and inserts or atomically adds the amount and cost to the existing per-turn media row.

**Call relations**: record_image_usage and record_video_usage call this. Its rows feed the same ledger-based reports and exports as other paid usage, while egress remains the separate zero-priced exception.

*Call graph*: called by 2 (record_image_usage, record_video_usage); 3 external calls (execute, debit, ledger_id_for).


##### `mint_usage_exports`  (lines 600–715)

```
async def mint_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, floor: datetime, key_slot_for: Callable[[str], str | None]) -> None
```

**Purpose**: Freezes new billable ledger growth into export-intent rows for an outside billing consumer. This makes retries safe because the exported delta is stored before delivery.

**Data flow**: It receives a connection, workspace ID, consumer name, earliest allowed time, and a function that maps models to key slots. It finds ledger rows that have grown beyond what that consumer has already exported, applies settlement rules, decides the byok value, and inserts one immutable export row per new delta. Existing matching export rows are left unchanged.

**Call relations**: A background export job calls this before reading pending exports. read_pending_usage_exports then sends these frozen rows, and ack_usage_exports marks them done only after the external consumer accepts them.

*Call graph*: 5 external calls (now, timedelta, execute, or_, select).


##### `read_pending_usage_exports`  (lines 718–763)

```
async def read_pending_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, limit: int) -> tuple[UsageExport, ...]
```

**Purpose**: Reads usage-export rows that were minted but not yet acknowledged. This gives an external billing sender a stable batch to deliver.

**Data flow**: It receives a connection, workspace ID, consumer name, and limit. It joins export rows to their ledger rows, computes the amount and price delta, builds UsageExport objects, and returns them in mint order.

**Call relations**: An export worker calls this after mint_usage_exports. If delivery fails before acknowledgement, the same function will read the same frozen rows again for safe retry.

*Call graph*: 3 external calls (__init__, execute, select).


##### `ack_usage_exports`  (lines 766–790)

```
async def ack_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: Marks exported usage deltas as acknowledged after an outside system has accepted them. This removes them from future pending reads.

**Data flow**: It receives a connection, workspace ID, consumer name, and the UsageExport objects that were delivered. It builds matching ledger/from-amount conditions and updates those export rows with an acknowledgement time.

**Call relations**: An export worker calls this only after a successful external API call. If the process crashes before this step, read_pending_usage_exports will return the same rows again.

*Call graph*: 3 external calls (execute, or_, update).


##### `metered_workspaces`  (lines 793–796)

```
def metered_workspaces() -> WorkspaceCandidates
```

**Purpose**: Provides candidate workspaces for usage-export jobs. It chooses every workspace that has ever had a ledger row.

**Data flow**: It builds a candidate source from a database query selecting distinct workspace IDs in the ledger. It returns a WorkspaceCandidates object for the job runner to iterate over.

**Call relations**: Background scheduling code can call this to decide which workspaces might need export attention. The later per-workspace export read is allowed to be a no-op if everything is already sent.

*Call graph*: 1 external calls (owner_candidates).


##### `SpendEvaluator.decide`  (lines 832–847)

```
async def decide(self, connection: AsyncConnection, pending_micro_usd: int) -> SpendDecision
```

**Purpose**: Decides whether pending spend is allowed under the workspace's spend caps. The result is allow, park, or reject, with a human-readable message when blocked.

**Data flow**: It receives a connection and the cost about to be added. It loads applicable caps, caches the no-cap case, sums recent spend for each cap, compares used plus pending cost with each limit, and returns a SpendDecision. Reject wins if any broken cap is configured to reject; otherwise breached caps park the turn.

**Call relations**: Turn admission and mid-turn checks call this when spend caps are enabled. It orchestrates _applicable_caps, _used_micro_usd, _message, and _note_absent_caps.

*Call graph*: calls 4 internal fn (_applicable_caps, _message, _used_micro_usd, _note_absent_caps); 1 external calls (__init__).


##### `SpendEvaluator._applicable_caps`  (lines 849–877)

```
async def _applicable_caps(self, connection: AsyncConnection) -> tuple[SpendCap, ...]
```

**Purpose**: Finds the spend caps that apply to this exact workspace, member, and agent. A cap can cover the whole workspace, one member, or one agent.

**Data flow**: It receives a database connection and reads spend_cap rows for this evaluator's workspace whose scope matches the workspace, member ID, or agent ID. It converts those rows into SpendCap objects and returns them as a tuple.

**Call relations**: SpendEvaluator.decide calls this first. Its result determines whether the decision can immediately allow, cache the no-cap shortcut, or continue into usage summing.

*Call graph*: called by 1 (decide); 4 external calls (__init__, execute, or_, select).


##### `SpendEvaluator._used_micro_usd`  (lines 879–905)

```
async def _used_micro_usd(self, connection: AsyncConnection, cap: SpendCap) -> int
```

**Purpose**: Calculates how much money has already been spent inside one cap's rolling time window. This is the number compared against the cap limit.

**Data flow**: It receives a connection and a SpendCap. It computes the cutoff time from the cap window, builds a query matching the cap's scope, sums ledger priced_micro_usd values since the cutoff, and returns the integer total.

**Call relations**: SpendEvaluator.decide calls this once for each applicable cap. The returned totals are combined with pending spend to decide whether any cap is breached.

*Call graph*: called by 1 (decide); 4 external calls (now, timedelta, execute, select).


##### `SpendEvaluator._message`  (lines 907–918)

```
def _message(self, outcome: SpendOutcome, breaches: list[SpendCap]) -> str
```

**Purpose**: Builds the user-facing explanation for a breached spend cap. It names the tightest cap and says whether the turn was parked or declined.

**Data flow**: It receives the chosen outcome and the list of breached caps. It picks the breached cap with the smallest limit, converts micro-dollars into dollars, and returns a formatted message string.

**Call relations**: SpendEvaluator.decide calls this only when at least one cap is breached. The returned message is carried inside the SpendDecision.

*Call graph*: called by 1 (decide).


##### `_token_sum`  (lines 1035–1043)

```
def _token_sum() -> sa.ColumnElement[int]
```

**Purpose**: Builds a database expression that sums only token-like ledger dimensions. This keeps reports from counting images, videos, or egress requests as tokens.

**Data flow**: It takes no ordinary input, but refers to the ledger table in the query being built. It creates a SQL expression that adds ledger amount only when the dimension is tokens or sandbox_tokens, otherwise adding zero. The result is used inside larger database queries.

**Call relations**: SpendRollup.read, SpendRollup._by_origin, and _usage_details use this helper when building report queries that need token totals.

*Call graph*: called by 3 (_by_origin, read, _usage_details); 1 external calls (case).


##### `_token_cost_sum`  (lines 1046–1058)

```
def _token_cost_sum() -> sa.ColumnElement[int]
```

**Purpose**: Builds a database expression that sums cost only for token-like ledger dimensions. This separates token spending from total spending across all dimensions.

**Data flow**: It takes no ordinary input, but refers to the ledger table in the active query. It creates a SQL expression that adds priced_micro_usd only for tokens and sandbox_tokens, otherwise zero. The expression is returned for use in report queries.

**Call relations**: SpendRollup.read, SpendRollup._by_origin, and _usage_details call this alongside _token_sum to produce token-specific cost numbers.

*Call graph*: called by 3 (_by_origin, read, _usage_details); 1 external calls (case).


##### `_usage_details`  (lines 1061–1185)

```
async def _usage_details(connection: AsyncConnection, source: sa.FromClause, scope: sa.ColumnElement[bool], cutoff: datetime | None, now: datetime) -> UsageDetails
```

**Purpose**: Builds the shared usage summary used by workspace, agent, and member reports. It includes selected-window totals, all-time totals, daily history, execution breakdowns, model breakdowns, and previous-period token counts.

**Data flow**: It receives a connection, a database source to query from, a scope condition, an optional cutoff time, and the current time. It runs several aggregate queries over that scope, fills missing days with zero rows, normalizes the first-use timestamp, and returns a UsageDetails object.

**Call relations**: SpendRollup.read, SpendRollup.read_agent, and SpendRollup.read_member call this after they define their own scope. It uses _token_sum and _token_cost_sum so all report types count tokens the same way.

*Call graph*: calls 2 internal fn (_token_cost_sum, _token_sum); called by 3 (read, read_agent, read_member); 9 external calls (__init__, __init__, __init__, __init__, fromisoformat, date, timedelta, execute, select).


##### `SpendRollup.read`  (lines 1196–1300)

```
async def read(self, connection: AsyncConnection, window_seconds: int | None) -> SpendReport
```

**Purpose**: Reads a full workspace spend report for a selected time window or all time. It is the main dashboard-style summary for workspace billing.

**Data flow**: It receives a connection and optional window length. It computes the time cutoff, sums total cost, groups ledger rows by dimension, member, agent, price digest, and origin, and gathers detailed token usage. It returns a SpendReport object containing all those sections.

**Call relations**: Reporting endpoints call this for workspace-level billing views. It delegates origin grouping to _by_origin and shared usage history to _usage_details, while using _token_sum and _token_cost_sum in its grouped queries.

*Call graph*: calls 4 internal fn (_by_origin, _token_cost_sum, _token_sum, _usage_details); 8 external calls (__init__, __init__, __init__, __init__, now, timedelta, execute, select).


##### `SpendRollup._by_origin`  (lines 1302–1367)

```
async def _by_origin(self, connection: AsyncConnection, window: sa.ColumnElement[bool]) -> tuple[OriginTotal, ...]
```

**Purpose**: Groups token spend by the conversation origin where the work began. This matters because subagents may spend money in private child conversations, but users want that spend attributed to the visible surface that started it.

**Data flow**: It receives a connection and a window condition. It builds a recursive database query that climbs each spending turn's parent chain to its root conversation, then groups token amount and token cost by that root conversation's surface label. It returns OriginTotal objects.

**Call relations**: SpendRollup.read calls this when building the workspace report. It uses _token_sum and _token_cost_sum so the origin report matches the rest of the workspace token accounting.

*Call graph*: calls 2 internal fn (_token_cost_sum, _token_sum); called by 1 (read); 4 external calls (__init__, desc, execute, select).


##### `SpendRollup.read_agent`  (lines 1369–1425)

```
async def read_agent(self, connection: AsyncConnection, agent_id: UUID, window_seconds: int | None) -> AgentSpendReport
```

**Purpose**: Reads spend and usage for one agent, including that agent's own spend caps. Workspace jobs without a turn are intentionally not included.

**Data flow**: It receives a connection, agent ID, and optional window length. It joins ledger rows to turns for that agent, groups selected spend by dimension, reads agent-scoped cap lines, and calls _usage_details for detailed selected and all-time usage. It returns an AgentSpendReport.

**Call relations**: Agent billing or admin views call this when focusing on one agent. It shares the common usage-detail machinery with workspace and member reports.

*Call graph*: calls 1 internal fn (_usage_details); 7 external calls (__init__, __init__, __init__, now, timedelta, execute, select).


##### `SpendRollup.read_member`  (lines 1427–1485)

```
async def read_member(self, connection: AsyncConnection, member_id: UUID, window_seconds: int | None) -> MemberSpendReport
```

**Purpose**: Reads spend and usage for one member, plus that member's spend caps. It attributes ledger rows to the member through the turn's conversation.

**Data flow**: It receives a connection, member ID, and optional window length. It joins ledger rows to turns and conversations, filters to the member, groups selected spend by dimension, reads member-scoped caps, and calls _usage_details. It returns a MemberSpendReport.

**Call relations**: Member billing views call this when showing one person's usage. It follows the same attribution path that member spend caps use, keeping enforcement and reporting aligned.

*Call graph*: calls 1 internal fn (_usage_details); 7 external calls (__init__, __init__, __init__, now, timedelta, execute, select).


##### `BalanceGate.admits`  (lines 1517–1555)

```
async def admits(self, connection: AsyncConnection, agent_id: UUID | None=None, key_slot_for: Callable[[str], str | None] | None=None, turn_id: UUID | None=None, model: str | None=None) -> SpendDecisi
```

**Purpose**: Decides whether a workspace balance allows a turn to start, resume, or be folded into a live turn. Starting uses a reserve threshold, while special own-key cases can be allowed above zero even if below reserve.

**Data flow**: It receives a connection and optional agent, model, key-slot resolver, and turn ID. It reads balance headroom, allows self-host/no-balance work, clears stale balance cache if a balance exists, checks the reserve line, checks whether an already-debited turn should stay blocked, and optionally allows work served by the workspace's own provider key. It returns a SpendDecision.

**Call relations**: Turn admission and resume logic call this before letting work begin. It uses _workspace_serves_itself to check own-key exemptions and _turn_has_debited to avoid re-admitting turns that already charged and would just park again.

*Call graph*: calls 2 internal fn (_turn_has_debited, _workspace_serves_itself); 4 external calls (__init__, _forget_absent_balance, balance_refusal_message, read_headroom).


##### `BalanceGate._workspace_serves_itself`  (lines 1557–1579)

```
async def _workspace_serves_itself(self, connection: AsyncConnection, agent_id: UUID | None, key_slot_for: Callable[[str], str | None] | None, model: str | None=None) -> bool
```

**Purpose**: Checks whether the model for this work will be served using the workspace's own provider key. That determines whether token rounds are expected to debit platform balance.

**Data flow**: It receives a connection, optional agent ID, optional model, and optional model-to-key-slot function. If no resolver exists, it returns false. If the model is not supplied, it looks up the agent's model, then asks workspace_owns_the_key whether the resolved key slot belongs to the workspace.

**Call relations**: BalanceGate.admits calls this when balance is below the normal start reserve but still above zero. It hands off the final credential ownership check to workspace_owns_the_key.

*Call graph*: calls 1 internal fn (workspace_owns_the_key); called by 1 (admits); 2 external calls (execute, select).


##### `BalanceGate.sustains`  (lines 1581–1602)

```
async def sustains(self, connection: AsyncConnection, pending_micro_usd: int, turn_id: UUID | None=None) -> SpendDecision
```

**Purpose**: Decides whether a running turn may continue for another round. Unlike starting, continuing stops at zero balance, not at the reserve threshold.

**Data flow**: It receives a connection, pending micro-dollar cost, and optional turn ID. It reads balance headroom, allows self-host/no-balance work, clears stale balance cache, and checks whether balance minus pending spend remains above the grace-adjusted zero line. If not, it rejects only when pending spend is positive or the turn has already debited; otherwise it allows no-cost progress.

**Call relations**: Turn execution calls this between rounds or before adding more unbilled spend. It uses _turn_has_debited to distinguish truly free turns from turns that already spent platform balance.

*Call graph*: calls 1 internal fn (_turn_has_debited); 4 external calls (__init__, _forget_absent_balance, balance_refusal_message, read_headroom).


##### `BalanceGate._turn_has_debited`  (lines 1604–1627)

```
async def _turn_has_debited(self, connection: AsyncConnection, turn_id: UUID | None) -> bool
```

**Purpose**: Checks whether a turn has already taken any money from the workspace balance. This is different from asking whether the work had a listed price, because own-key token rows can be priced but debit nothing.

**Data flow**: It receives a connection and optional turn ID. If no turn ID is supplied, it returns false. Otherwise it searches for any ledger row for that turn with debited_micro_usd greater than zero and returns true if one exists.

**Call relations**: BalanceGate.admits and BalanceGate.sustains call this to avoid bad loops: re-admitting already-charged turns below the line, or parking turns that never debit and therefore can never improve the balance.

*Call graph*: called by 2 (admits, sustains); 2 external calls (execute, select).


### `core/src/ufo/billing/balance.py`

`domain_logic` · `request handling and billing checks`

This file is the project’s cash register for prepaid workspace credit. It stores the current balance as a single database row so the system can check it quickly before every model round. It also keeps every purchase or grant as an audit trail, like keeping receipts even though the wallet only needs to know how much cash is inside right now.

Amounts are stored in micro-USD, meaning millionths of a US dollar. That avoids floating-point rounding mistakes when charging tiny amounts. The file can add credit, subtract usage, read totals for admin screens, list recent purchases, and set rules for automatic refills. It also supports a “reserve,” which is a minimum cushion required before work may begin, and a limited grace amount for workspaces that have already proven a card can successfully pay.

A small in-memory shortcut remembers, briefly, when a workspace has no balance row. This helps self-hosted or unpaid deployments avoid repeated database reads. The shortcut is deliberately temporary and is cleared as soon as credit is added, so correctness still comes from the database.

Without this file, the system would not have a reliable place to answer basic questions such as “does this workspace have enough credit to start work?”, “how much has it ever bought?”, or “should automatic top-up run now?”

#### Function details

##### `balance_absent`  (lines 37–43)

```
def balance_absent(workspace_id: UUID) -> bool
```

**Purpose**: This checks a short-lived memory note that says a workspace recently had no balance row. It lets callers skip a database read in common cases where billing is not configured or no credit has ever been added.

**Data flow**: It receives a workspace ID. It looks in the local `_no_balance` map for an expiry time and compares it with the current monotonic clock, which is a clock used for measuring elapsed time safely. It returns `True` only if the note exists and has not expired; otherwise it returns `False` and changes nothing.

**Call relations**: This is a fast-path helper for other parts of the billing gate. It does not call back into the database. It only asks the system clock whether the cached “no balance” note is still fresh.

*Call graph*: 1 external calls (monotonic).


##### `_note_absent_balance`  (lines 46–53)

```
def _note_absent_balance(workspace_id: UUID) -> None
```

**Purpose**: This records, for a few seconds, that a workspace had no balance row. It is a performance shortcut, not the source of truth.

**Data flow**: It receives a workspace ID. It reads the current monotonic time, removes expired entries if the cache has reached its size limit, then stores a new expiry time for this workspace. Nothing is returned; the in-memory cache is changed.

**Call relations**: When `read_balance` or `read_headroom` looks in the database and finds no row, they call this helper. Later callers can use that note to avoid doing the same database lookup again for a short time.

*Call graph*: called by 2 (read_balance, read_headroom); 1 external calls (monotonic).


##### `_forget_absent_balance`  (lines 56–59)

```
def _forget_absent_balance(workspace_id: UUID) -> None
```

**Purpose**: This removes the short-lived “no balance” note for a workspace once credit exists. It prevents the shortcut from hiding a newly funded balance.

**Data flow**: It receives a workspace ID. It removes that ID from the local `_no_balance` map if present. It returns nothing and has no database effect.

**Call relations**: `credit` calls this after successfully adding credit to a workspace. That way, any later billing gate will not rely on an old memory note that said the workspace had no balance.

*Call graph*: called by 1 (credit).


##### `billing_screen_url`  (lines 76–85)

```
def billing_screen_url(public_base_url: str | None, home_surface: str | None) -> str | None
```

**Purpose**: This builds the web address for the workspace billing screen when the deployment has one. It returns no address for setups, such as some self-hosted or development installs, that do not expose a public browser surface.

**Data flow**: It receives a public base URL and a home surface name. If either is missing, it returns `None`. Otherwise it trims any trailing slash from the base URL and combines it with the surface path and billing screen fragment, producing one URL string.

**Call relations**: This is used as a small formatting helper by code that wants to show users where an admin can add credit. It does not call other project functions; it only combines already-known configuration values.


##### `balance_refusal_message`  (lines 88–97)

```
def balance_refusal_message(billing_url: str | None) -> str
```

**Purpose**: This creates the message shown when a workspace is out of credit. It explains the problem and, when possible, includes the billing page where an admin can fix it.

**Data flow**: It receives either a billing URL or `None`. If there is no URL, it returns a message saying an admin can set up automatic refills. If there is a URL, it returns a message pointing the admin to that address.

**Call relations**: This function sits near the gate that refuses unpaid work. It turns the billing decision into human-readable text, without doing any database work or making the decision itself.


##### `read_auto_topup`  (lines 109–131)

```
async def read_auto_topup(connection: AsyncConnection, workspace_id: UUID) -> AutoTopup | None
```

**Purpose**: This answers the refill worker’s question: should this workspace be topped up now? It returns the configured refill amount only when auto top-up exists and the balance has dropped to or below its threshold.

**Data flow**: It receives a database connection and workspace ID. It reads the workspace balance row, including current balance, top-up amount, and threshold. If there is no row, no top-up setting, or the balance is still above the threshold, it returns `None`. Otherwise it returns an `AutoTopup` object containing the amount to charge and the threshold that triggered it.

**Call relations**: A refill process can call this before asking a payment extension to charge a card. This function decides whether the workspace is short enough; the payment extension decides how money is collected.

*Call graph*: 3 external calls (__init__, execute, select).


##### `set_auto_topup`  (lines 134–154)

```
async def set_auto_topup(connection: AsyncConnection, workspace_id: UUID, amount_micro_usd: int | None, threshold_micro_usd: int | None) -> bool
```

**Purpose**: This turns automatic refills on or off for an existing workspace balance. It requires the refill amount and threshold to be set together, so the system never has a half-configured refill rule.

**Data flow**: It receives a database connection, workspace ID, optional top-up amount, and optional threshold. If exactly one of the two settings is missing, it raises a `ValueError`. Otherwise it updates the existing balance row with both settings, or clears both settings, and returns `True` if one row was updated.

**Call relations**: Admin-facing code can call this when a workspace changes its refill settings. It writes directly to the balance table and does not create a balance row for workspaces that have never been credited.

*Call graph*: 2 external calls (execute, update).


##### `mark_topup_verified`  (lines 157–171)

```
async def mark_topup_verified(connection: AsyncConnection, workspace_id: UUID) -> None
```

**Purpose**: This records that a workspace has successfully paid for a top-up at least once. That proof unlocks a small grace amount, allowing work to continue briefly while future refills are being processed.

**Data flow**: It receives a database connection and workspace ID. It updates the balance row only if the `topup_verified_at` timestamp is still empty, setting it to the current database time. It returns nothing; the database row may be changed.

**Call relations**: Payment-related code calls this after a charge has settled. Later, `read_headroom` sees that timestamp and includes the grace amount in the figures used by the billing gate.

*Call graph*: 2 external calls (execute, update).


##### `configured_auto_topup`  (lines 183–204)

```
async def configured_auto_topup(connection: AsyncConnection, workspace_id: UUID) -> AutoTopup | None
```

**Purpose**: This reads the auto top-up settings exactly as configured, whether or not the balance is currently low enough to trigger a refill. It is useful for showing an admin what settings are saved.

**Data flow**: It receives a database connection and workspace ID. It reads the top-up amount and threshold from the balance row. If there is no row or no configured amount, it returns `None`; otherwise it returns an `AutoTopup` object with the saved settings.

**Call relations**: Unlike `read_auto_topup`, this is for reporting configuration back to people or tools. It does not decide whether a charge should happen now; it only reads the saved rule.

*Call graph*: 3 external calls (__init__, execute, select).


##### `read_headroom`  (lines 207–226)

```
async def read_headroom(connection: AsyncConnection, workspace_id: UUID) -> Headroom | None
```

**Purpose**: This reads the small set of balance numbers needed before allowing a model round to start. It is intentionally cheaper than reading the full lifetime balance history.

**Data flow**: It receives a database connection and workspace ID. It reads the current balance, reserve, and whether a top-up has ever been verified. If there is no balance row, it records a short-lived absent-balance note and returns `None`. If a row exists, it returns a `Headroom` object with the balance, reserve, and either zero grace or the fixed top-up grace amount.

**Call relations**: Billing gates call this before work begins. If the database has no balance row, it calls `_note_absent_balance` so future checks can skip repeated reads for a few seconds.

*Call graph*: calls 1 internal fn (_note_absent_balance); 3 external calls (__init__, execute, select).


##### `recent_purchases`  (lines 240–273)

```
async def recent_purchases(connection: AsyncConnection, workspace_id: UUID, limit: int) -> tuple[Purchase, ...]
```

**Purpose**: This returns the newest credits added to a workspace, limited to a requested count. It supports billing screens that show where the current balance came from.

**Data flow**: It receives a database connection, workspace ID, and maximum number of rows. It queries purchase records for that workspace, orders newest first, and breaks timestamp ties using the purchase ID. It returns a tuple of `Purchase` objects, each showing how much credit was granted, how much money was charged, and when it was created.

**Call relations**: Admin or billing display code can call this when it needs a short purchase history. It reads the purchase ledger only and does not change balances.

*Call graph*: 3 external calls (__init__, execute, select).


##### `read_balance`  (lines 276–306)

```
async def read_balance(connection: AsyncConnection, workspace_id: UUID) -> Balance | None
```

**Purpose**: This reads the full balance picture for a workspace: what remains now, what must be reserved, how much has ever been granted or charged, and when the last purchase happened. It is meant for admin or operator views, not the fastest per-round gate.

**Data flow**: It receives a database connection and workspace ID. It first reads the current balance row. If none exists, it records a short-lived absent-balance note and returns `None`. If the row exists, it separately sums the workspace’s purchase records and returns a `Balance` object with current and lifetime totals.

**Call relations**: This function calls `_note_absent_balance` when the database confirms there is no balance. It is the heavier read used when a caller needs the full audit-style view, while `read_headroom` is the lighter read for frequent admission checks.

*Call graph*: calls 1 internal fn (_note_absent_balance); 3 external calls (__init__, execute, select).


##### `credit`  (lines 309–364)

```
async def credit(connection: AsyncConnection, workspace_id: UUID, granted_micro_usd: int, charged_micro_usd: int, reference: str) -> bool
```

**Purpose**: This adds credit to a workspace exactly once for a given reference, such as a payment ID or grant ID. It protects against double-crediting if the same payment notification is delivered more than once.

**Data flow**: It receives a database connection, workspace ID, granted amount, charged amount, and unique reference string. It tries to insert a purchase record; if a record with the same workspace and reference already exists, it returns `False`. If the insert succeeds, it creates or updates the workspace balance by adding the granted amount, clears any cached “no balance” note, and returns `True`.

**Call relations**: Payment fulfillment and grant code call this inside their own database transaction, so the purchase record and balance change succeed or fail together. After adding credit, it calls `_forget_absent_balance` so future gates know the workspace may now have funds.

*Call graph*: calls 1 internal fn (_forget_absent_balance); 2 external calls (execute, uuid4).


##### `debit`  (lines 367–387)

```
async def debit(connection: AsyncConnection, workspace_id: UUID, micro_usd: int) -> int
```

**Purpose**: This subtracts spent credit from a workspace after paid work has been recorded. It allows the balance to go negative, because keeping an honest record of money already spent is more important than rejecting the accounting write.

**Data flow**: It receives a database connection, workspace ID, and amount to subtract. If the amount is zero, it immediately returns zero. Otherwise it updates the existing balance row by subtracting the amount. It returns the amount actually taken, or zero if there was no balance row to update.

**Call relations**: Usage-recording code can call this in the same transaction as the ledger entry for the work. It writes directly to the balance row and reports what changed so the caller can record the real deduction.

*Call graph*: 2 external calls (execute, update).


##### `set_reserve`  (lines 390–401)

```
async def set_reserve(connection: AsyncConnection, workspace_id: UUID, reserve_micro_usd: int) -> bool
```

**Purpose**: This sets the minimum balance cushion required before a workspace may start work. The reserve helps avoid starting a task that has just enough credit for one small step and then immediately stalls.

**Data flow**: It receives a database connection, workspace ID, and reserve amount. It updates the existing workspace balance row with the new reserve and timestamp. It returns `True` if exactly one row was updated, and `False` if the workspace has no balance row.

**Call relations**: Admin or billing policy code can call this to change how much headroom a workspace needs. Later, `read_headroom` includes this reserve in the figures used by the work-admission gate.

*Call graph*: 2 external calls (execute, update).


### External Billing Integrations
Connects internal billing state to Metronome usage reporting, Stripe balance payments, chat billing tools, status pages, and scheduled billing jobs.

### `extensions/metronome/ufo_ext_metronome.py`

`domain_logic` · `scheduled jobs, chat tool calls, and billing page requests`

This extension is the bridge between the product’s internal accounting and two outside services. Metronome receives records of settled usage so humans can see rated statements. Stripe stores customer payment methods and charges cards when a workspace has arranged automatic balance refills. The important rule is that Metronome records usage but must not collect money, because money is already collected when Stripe adds prepaid credit.

The file has three main moving parts. First, UsageShipper runs on a schedule. It reads frozen usage export records from core accounting, makes sure the workspace has a Metronome customer alias, sends events to Metronome, and only then acknowledges the exports. This ordering is like mailing a package only after writing down the tracking number: if the process crashes, the same package can be resent without double-counting.

Second, manage_billing is a chat tool for admins. It can report balance and card status, create a Stripe portal link, or set automatic refills. Third, BalanceTopup runs every minute and charges the saved Stripe card when the balance falls below the configured threshold.

There is also a small HTTP billing route for the UI. It lets an admin see why turns may be blocked even when chat itself cannot run because the workspace is out of credit.

#### Function details

##### `StripeError.__init__`  (lines 174–176)

```
def __init__(self, message: str, status: int=0) -> None
```

**Purpose**: Creates an error for a failed Stripe call and keeps the HTTP status code with it. The status matters because some failures, like a declined card, are expected business outcomes rather than programming bugs.

**Data flow**: It receives an error message and an optional status code. It stores the message as the normal exception text and saves the status number on the object. Later code can read that status to decide whether to retry, stand down, or report a decline.

**Call relations**: The shared Stripe request helper creates this error when Stripe returns a non-success response. Balance top-up logic then uses the saved status to tell the difference between a card decline, an in-flight charge, and a real service failure.

*Call graph*: called by 1 (_stripe).


##### `UsageShipper.run`  (lines 206–232)

```
async def run(self) -> None
```

**Purpose**: Sends one workspace’s pending usage records to Metronome safely. It is careful to send usage before marking it done, so a crash can retry without losing records.

**Data flow**: It reads the Metronome bearer token from the environment, gets the workspace’s fixed backfill floor, then repeatedly asks core accounting for a batch of pending usage exports. For each batch, it warns if records are too old, confirms the Metronome customer alias once, converts exports into ingest events, posts them, logs success, and acknowledges the exports. It stops when there is no more work or the last batch was not full.

**Call relations**: The scheduled _ship wrapper starts this method for each metered workspace. Inside the run it depends on _floor for the cutoff date, _note_usage_aging_out for operator warnings, _ensure_metronome_customer before the first send, _events for event formatting, and _ingest for the actual HTTP post.

*Call graph*: calls 6 internal fn (_events, _floor, _note_usage_aging_out, _ensure_metronome_customer, _ingest, _require_env); 1 external calls (log).


##### `UsageShipper._floor`  (lines 234–244)

```
async def _floor(self) -> datetime
```

**Purpose**: Finds the earliest usage time this workspace is allowed to backfill into Metronome. On first run it records a fixed cutoff so old historical usage is bounded, but delayed pending usage is not silently aged out later.

**Data flow**: It reads a stored floor timestamp from the extension store. If none exists, it creates one as now minus the configured backfill window, saves it, and returns it. If one exists, it parses the saved timestamp and returns that same date.

**Call relations**: UsageShipper.run calls this before it asks core for pending exports. That means the shipping job always uses a stable workspace-specific lower bound when asking what usage can be minted for export.

*Call graph*: called by 1 (run); 3 external calls (fromisoformat, now, timedelta).


##### `UsageShipper._note_usage_aging_out`  (lines 246–267)

```
def _note_usage_aging_out(self, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: Warns operators when pending usage has been held so long that Metronome may no longer accept it as backdated usage. It does not fix the data; it makes the risk visible while there may still be time to act.

**Data flow**: It receives a batch of usage exports, finds the oldest occurrence time, compares it with the backfill window, and either returns quietly or emits a warning with the workspace id, oldest timestamp, and batch size.

**Call relations**: UsageShipper.run calls this for every non-empty batch before sending. It uses _rfc3339 to print the timestamp in a standard readable form and the warning system to alert operators.

*Call graph*: calls 1 internal fn (_rfc3339); called by 1 (run); 3 external calls (now, timedelta, warn).


##### `UsageShipper._events`  (lines 269–288)

```
def _events(self, exports: tuple[UsageExport, ...]) -> list[dict[str, object]]
```

**Purpose**: Turns internal usage export records into the event shape Metronome expects. Each event gets a deterministic transaction id so resending the same export is safe.

**Data flow**: It receives a tuple of UsageExport records. For each one, it copies the workspace id, usage dimension, model, amount, price details, turn id, and BYOK flag into a JSON-ready dictionary, formatting the timestamp along the way. It returns a list of event dictionaries ready for ingestion.

**Call relations**: UsageShipper.run calls this right before _ingest. It uses _rfc3339 for timestamps, and its output is exactly what is posted to Metronome.

*Call graph*: calls 1 internal fn (_rfc3339); called by 1 (run).


##### `_ship`  (lines 291–292)

```
async def _ship(ctx: ExtensionContext) -> None
```

**Purpose**: Acts as the scheduled job entry for usage shipping. It creates a UsageShipper for the current workspace and starts it.

**Data flow**: It receives an ExtensionContext from the job runner. It builds a UsageShipper with that context and the configured test or production transport, then waits for the shipper to finish. It returns no value.

**Call relations**: manifest registers this as the handler for the usage_shipper job. The real work is handed immediately to UsageShipper.run.

*Call graph*: 1 external calls (__init__).


##### `BillingConfig.from_env`  (lines 310–327)

```
def from_env(cls) -> 'BillingConfig'
```

**Purpose**: Loads the required Stripe and Metronome settings from environment variables. It fails early if anything needed for billing is missing.

**Data flow**: It reads the Stripe secret key, Stripe portal configuration id, and Metronome bearer token from the process environment. If any are absent, it raises one error listing all missing names. If all are present, it returns a frozen BillingConfig object.

**Call relations**: Billing chat actions, balance top-ups, and the billing page call this before contacting Stripe or Metronome. This keeps half-configured deployments from creating partial provider state.


##### `_billing_record`  (lines 340–342)

```
async def _billing_record(ctx: ExtensionContext) -> BillingRecord | None
```

**Purpose**: Reads the stored Stripe customer id for a workspace, if one has already been created. This is the local pointer that ties a workspace to its Stripe customer.

**Data flow**: It looks up the billing record in the extension store. If nothing is stored, it returns None. If data exists, it validates it as a BillingRecord and returns the customer id inside it.

**Call relations**: Billing status, portal creation, automatic refill setup, top-up jobs, and the billing page all call this before they decide whether a Stripe customer already exists.

*Call graph*: called by 5 (run, _billing_autopay, _billing_portal, _billing_projection, _billing_status).


##### `manage_billing`  (lines 364–373)

```
async def manage_billing(ctx: ToolContext, args: ManageBillingInput) -> ToolResult
```

**Purpose**: Implements the admin-only chat tool for billing. Depending on the requested action, it reports status, creates a Stripe portal link, or changes automatic refill settings.

**Data flow**: It receives the tool context and parsed user arguments. It first checks that the speaker is an admin and loads billing configuration. Then it dispatches to the status, portal, or autopay helper and returns that helper’s ToolResult.

**Call relations**: The manifest exposes this as the manage_billing tool to the chat agent. It relies on _admin_billing for permission checks and hands the real work to _billing_status, _billing_portal, or _billing_autopay.

*Call graph*: calls 4 internal fn (_admin_billing, _billing_autopay, _billing_portal, _billing_status).


##### `_billing_autopay`  (lines 376–407)

```
async def _billing_autopay(ext: ExtensionContext, config: BillingConfig, args: ManageBillingInput) -> ToolResult
```

**Purpose**: Sets or stops automatic balance refills for a workspace. It protects users by requiring both refill numbers together and by requiring a saved card before enabling unattended charges.

**Data flow**: It receives the workspace context, billing config, and autopay arguments. If both dollar amounts are omitted, it disables refills. If both are present, it checks that a Stripe customer and default payment method exist, converts dollars to micro-dollars, and saves the rule in core balance storage. It clears old refusal state, bumps an attempt marker, logs the change, and returns the new settings as JSON text.

**Call relations**: manage_billing calls this for the autopay action. It reads _billing_record, checks Stripe through _default_payment_method, writes the rule through set_auto_topup inside a transaction, and formats the response through _text_result.

*Call graph*: calls 4 internal fn (transaction, _billing_record, _default_payment_method, _text_result); called by 1 (manage_billing); 2 external calls (set_auto_topup, log).


##### `_admin_billing`  (lines 410–416)

```
async def _admin_billing(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Checks that the chat speaker is a workspace admin before allowing billing actions. Billing can affect payment methods and automatic charges, so ordinary members are not allowed through.

**Data flow**: It receives a ToolContext. It rejects calls with no speaking member, rejects non-admin speakers, and otherwise returns the ExtensionContext for the workspace. It changes nothing.

**Call relations**: manage_billing calls this before loading provider configuration or doing any billing work. It uses the tool context’s speaker_is_admin check as the gate.

*Call graph*: calls 1 internal fn (speaker_is_admin); called by 1 (manage_billing).


##### `_billing_status`  (lines 419–443)

```
async def _billing_status(ext: ExtensionContext, config: BillingConfig) -> ToolResult
```

**Purpose**: Reports the workspace’s prepaid balance and whether Stripe currently has a default payment method. It reads provider truth for the card instead of trusting a local flag.

**Data flow**: It receives the workspace context and billing config. It reads the balance from core storage, reads the stored Stripe customer id if present, asks Stripe whether that customer has a default payment method, and returns a JSON text result with card presence and balance figures.

**Call relations**: manage_billing calls this for the status action. It uses _billing_record and _default_payment_method for card state, read_balance for money state, and _text_result to make the tool response.

*Call graph*: calls 4 internal fn (transaction, _billing_record, _default_payment_method, _text_result); called by 1 (manage_billing); 1 external calls (read_balance).


##### `_billing_portal`  (lines 446–468)

```
async def _billing_portal(ext: ExtensionContext, config: BillingConfig) -> ToolResult
```

**Purpose**: Creates a short-lived Stripe Customer Portal link for an admin. The portal lets the admin save or change a payment method and view Stripe billing details.

**Data flow**: It receives the workspace context and billing config. It reads the local billing record; if none exists, it creates or reuses a Stripe customer and stores the resulting id. Then it asks Stripe for a portal session URL with a return link back to the workspace billing screen. It returns the portal URL and customer id as JSON text.

**Call relations**: manage_billing calls this for the portal action. It uses _stripe_customer when a customer must be created, _portal_session for the portal URL, ext.home_url for the return path, and _text_result for the chat response.

*Call graph*: calls 5 internal fn (home_url, _billing_record, _portal_session, _stripe_customer, _text_result); called by 1 (manage_billing); 2 external calls (__init__, log).


##### `_text_result`  (lines 471–472)

```
def _text_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: Wraps a small dictionary as a text-based tool result. The chat tool protocol expects content items, so this converts plain data into that shape.

**Data flow**: It receives a dictionary, converts it to a JSON string, puts that string in a TextContent object, and returns a ToolResult containing it. It does not write any state.

**Call relations**: The billing status, portal, and autopay helpers all call this at the end so manage_billing can return a consistent response to the agent.

*Call graph*: called by 3 (_billing_autopay, _billing_portal, _billing_status); 3 external calls (__init__, __init__, dumps).


##### `_require_env`  (lines 484–488)

```
def _require_env(name: str) -> str
```

**Purpose**: Reads one required environment variable and raises a clear error if it is missing. It is used when a setting is so necessary that continuing would create bad downstream behavior.

**Data flow**: It receives an environment variable name. It reads the process environment, returns the non-empty value if present, or raises a RuntimeError naming the missing setting.

**Call relations**: UsageShipper.run calls this before touching pending usage exports. That ordering prevents a deployment without a Metronome token from minting export intents it cannot send.

*Call graph*: called by 1 (run).


##### `_stripe_customer`  (lines 491–508)

```
async def _stripe_customer(config: BillingConfig, workspace_id: UUID, transport: httpx.AsyncBaseTransport | None) -> str
```

**Purpose**: Creates or reuses the one Stripe Customer that represents a workspace. It uses a stable idempotency key so retries do not create duplicate customers.

**Data flow**: It receives billing config, a workspace id, and an optional HTTP transport. It sends Stripe a customer creation request with workspace metadata and an idempotency key based on the workspace id. It extracts and returns the customer id from Stripe’s response.

**Call relations**: _billing_portal calls this when the workspace has no stored billing record. It sends the request through _stripe and validates the returned id with _as_str.

*Call graph*: calls 2 internal fn (_as_str, _stripe); called by 1 (_billing_portal).


##### `_portal_session`  (lines 511–536)

```
async def _portal_session(config: BillingConfig, customer_id: str, flow: str | None, transport: httpx.AsyncBaseTransport | None, return_url: str | None=None) -> str
```

**Purpose**: Asks Stripe for a temporary Customer Portal URL. This URL lets an admin manage payment details in Stripe without this app storing card data.

**Data flow**: It receives billing config, a Stripe customer id, an optional portal flow type, optional HTTP transport, and an optional return URL. It builds the form data Stripe expects, posts it, extracts the session URL, and returns that URL.

**Call relations**: _billing_portal calls this after it knows the Stripe customer id. It uses _stripe for the provider request and _as_str to make sure Stripe actually returned a usable URL.

*Call graph*: calls 2 internal fn (_as_str, _stripe); called by 1 (_billing_portal).


##### `_default_payment_method`  (lines 539–551)

```
async def _default_payment_method(config: BillingConfig, customer_id: str, transport: httpx.AsyncBaseTransport | None) -> str | None
```

**Purpose**: Finds the Stripe customer’s default payment method, if one exists. This answers the practical question, “Is there a card or other default method that an off-session charge can use?”

**Data flow**: It receives billing config, a Stripe customer id, and an optional transport. It fetches the customer from Stripe, looks inside invoice settings for a default payment method id, and returns that id or None.

**Call relations**: Automatic refill setup, balance top-up execution, billing status, and card display all call this. It uses _stripe for the Stripe read.

*Call graph*: calls 1 internal fn (_stripe); called by 4 (run, _billing_autopay, _billing_status, _card_on_file).


##### `_card_on_file`  (lines 566–581)

```
async def _card_on_file(config: BillingConfig, customer_id: str, transport: httpx.AsyncBaseTransport | None) -> CardOnFile | None
```

**Purpose**: Reads the recognizable card details for the workspace’s default payment method. It returns only the brand and last four digits, enough for an admin to identify the card.

**Data flow**: It receives billing config, a Stripe customer id, and optional transport. It first finds the default payment method id. If none exists, it returns None. If one exists, it fetches that payment method from Stripe and returns a CardOnFile object when it is a card with brand and last-four data.

**Call relations**: _billing_projection calls this for the billing page. It builds on _default_payment_method and uses _stripe for the second provider read.

*Call graph*: calls 2 internal fn (_default_payment_method, _stripe); called by 1 (_billing_projection); 1 external calls (__init__).


##### `_stripe`  (lines 584–605)

```
async def _stripe(config: BillingConfig, method: str, path: str, transport: httpx.AsyncBaseTransport | None, data: dict[str, str] | None=None, idempotency_key: str | None=None) -> dict[str, object]
```

**Purpose**: Is the shared low-level helper for Stripe HTTP requests. It adds authentication, pins the Stripe API version, sends the request, and turns non-success responses into StripeError.

**Data flow**: It receives billing config, an HTTP method, a Stripe path, optional transport, optional form data, and an optional idempotency key. It sends the request to Stripe with the right headers. On success it returns the parsed JSON response; on failure it raises StripeError with the status and response body.

**Call relations**: All Stripe-specific helpers use this: customer creation, portal sessions, default payment method lookup, card lookup, and payment intents for top-ups. It is the place where StripeError.__init__ is used.

*Call graph*: calls 1 internal fn (__init__); called by 5 (_charge, _card_on_file, _default_payment_method, _portal_session, _stripe_customer); 1 external calls (AsyncClient).


##### `_as_str`  (lines 608–612)

```
def _as_str(value: object, field: str) -> str
```

**Purpose**: Validates that a provider response field is a non-empty string. It prevents later code from treating a missing or malformed provider value as usable.

**Data flow**: It receives any value and the human name of the field being checked. If the value is a non-empty string, it returns it. Otherwise it raises a ValueError that names the missing field.

**Call relations**: _stripe_customer uses this to extract the Stripe customer id, and _portal_session uses it to extract the portal URL.

*Call graph*: called by 2 (_portal_session, _stripe_customer).


##### `_ensure_metronome_customer`  (lines 615–666)

```
async def _ensure_metronome_customer(ctx: ExtensionContext, token: str, transport: httpx.AsyncBaseTransport | None) -> None
```

**Purpose**: Makes sure Metronome has a live customer whose ingest alias is the workspace UUID. Without that alias, Metronome may accept usage events but not attach them to the right customer, which would silently lose billing visibility.

**Data flow**: It receives the workspace context, a Metronome bearer token, and optional transport. It checks whether a customer already holds the workspace alias. If not, it tries to create one. It handles conflicts by re-reading the alias, treats missing customer permissions as a hard stop, logs successful creation, and raises errors rather than letting usage be acknowledged into nowhere.

**Call relations**: UsageShipper.run calls this once before sending the first batch in a pass. It relies on _customer_by_alias for alias lookup and uses direct HTTP calls to create the Metronome customer when needed.

*Call graph*: calls 1 internal fn (_customer_by_alias); called by 1 (run); 4 external calls (__init__, __init__, AsyncClient, log).


##### `_customer_by_alias`  (lines 676–693)

```
async def _customer_by_alias(http: httpx.AsyncClient, headers: dict[str, str], alias: str) -> str | None
```

**Purpose**: Looks up the live Metronome customer that owns a given ingest alias. It returns None when no visible live customer is found.

**Data flow**: It receives an HTTP client, headers, and the alias string. It calls Metronome’s customer list endpoint filtered by that alias. If permission is denied, it raises a special scope error; if another failure occurs, it raises MetronomeError; otherwise it returns the first customer id or None.

**Call relations**: _ensure_metronome_customer uses this before creating a customer and again after a conflict. That second read tells whether another worker won a harmless race or whether the alias is held by something this token cannot see.

*Call graph*: called by 1 (_ensure_metronome_customer); 3 external calls (__init__, __init__, get).


##### `BalanceTopup.run`  (lines 712–794)

```
async def run(self) -> None
```

**Purpose**: Checks whether a workspace needs an automatic prepaid balance refill and, if so, charges its saved Stripe payment method. It is careful not to hammer a missing or declined card every minute.

**Data flow**: It reads the workspace’s configured auto-topup rule. If none exists, it exits. It respects recent “no card” and “card refused” wait periods, loads billing config, finds the Stripe customer and default payment method, reads the current charged total, and calls _charge. If the charge succeeds, it credits the workspace balance and marks the top-up verified. If the card is missing, declined, or a charge is still in flight, it records or logs the right state and returns.

**Call relations**: The scheduled _top_up wrapper starts this method for member workspaces. It uses _billing_record, _default_payment_method, BalanceTopup._charge, and core balance functions such as read_auto_topup, credit, and mark_topup_verified.

*Call graph*: calls 3 internal fn (_charge, _billing_record, _default_payment_method); 8 external calls (fromisoformat, now, credit, mark_topup_verified, read_auto_topup, read_balance, log, warn).


##### `BalanceTopup._charge`  (lines 796–853)

```
async def _charge(self, config: BillingConfig, customer_id: str, payment_method: str, wanted: AutoTopup, workspace_id: UUID, attempt: str) -> str | None
```

**Purpose**: Creates and confirms a Stripe PaymentIntent for an automatic refill. It returns the payment intent id only when Stripe says the money actually moved.

**Data flow**: It receives billing config, customer id, payment method id, the desired top-up rule, workspace id, and an attempt marker. It converts micro-dollars to cents, sends Stripe a confirmed off-session payment request with an idempotency key, and interprets the response. A succeeded intent returns its id; a card decline returns None; a still-running duplicate request raises _ChargeInFlight; other Stripe errors are re-raised.

**Call relations**: BalanceTopup.run calls this after it has confirmed there is a configured refill and a default payment method. This helper sends the request through _stripe and reports declined or unusual statuses through warnings.

*Call graph*: calls 1 internal fn (_stripe); called by 1 (run); 2 external calls (__init__, warn).


##### `_top_up`  (lines 856–857)

```
async def _top_up(ctx: ExtensionContext) -> None
```

**Purpose**: Acts as the scheduled job entry for automatic balance refills. It creates a BalanceTopup worker for the current workspace and starts it.

**Data flow**: It receives an ExtensionContext from the job runner. It builds BalanceTopup with that context and the configured billing transport, then waits for the refill check to finish. It returns no value.

**Call relations**: manifest registers this as the handler for the balance_topup job. The actual refill decision and charging flow live in BalanceTopup.run.

*Call graph*: 1 external calls (__init__).


##### `_ingest`  (lines 860–868)

```
async def _ingest(token: str, events: list[dict[str, object]], transport: httpx.AsyncBaseTransport | None) -> None
```

**Purpose**: Posts prepared usage events to Metronome’s ingest API. It treats any non-success response as a failed shipment so exports are not acknowledged.

**Data flow**: It receives a bearer token, a list of event dictionaries, and optional transport. It sends the events as JSON to Metronome with the token. If Metronome accepts the request, it returns normally; otherwise it raises MetronomeError with the status and body.

**Call relations**: UsageShipper.run calls this after formatting events and confirming the customer alias. If this raises, UsageShipper.run does not acknowledge the exports, so the next scheduled run can retry them.

*Call graph*: called by 1 (run); 2 external calls (__init__, AsyncClient).


##### `_rfc3339`  (lines 871–873)

```
def _rfc3339(moment: datetime) -> str
```

**Purpose**: Formats a datetime in the timestamp style expected by web APIs. If the time has no timezone, it treats it as UTC.

**Data flow**: It receives a datetime. If it is timezone-aware, it formats it as-is; if not, it adds UTC first. It returns the ISO/RFC3339-style string.

**Call relations**: UsageShipper._events uses this for Metronome event timestamps, and UsageShipper._note_usage_aging_out uses it in warning messages.

*Call graph*: called by 2 (_events, _note_usage_aging_out); 1 external calls (replace).


##### `_billing_request_workspace`  (lines 882–887)

```
def _billing_request_workspace(request: Request) -> UUID | None
```

**Purpose**: Identifies which workspace a billing page request belongs to by reading the session cookie. If no valid workspace claim is present, the route should not proceed.

**Data flow**: It receives an HTTP request, reads the session cookie, asks the bearer-token helper for the workspace claim, and returns a workspace UUID or None.

**Call relations**: manifest attaches this as the identify function for the billing route. Core uses its answer to bind the request to a workspace before calling _billing_projection.

*Call graph*: 1 external calls (workspace_claim).


##### `_billing_projection`  (lines 890–957)

```
async def _billing_projection(ext: ExtensionContext, request: Request) -> Response
```

**Purpose**: Builds the JSON data shown on the billing screen. This page is important because it can explain a stopped workspace’s balance and card state without requiring a chat turn.

**Data flow**: It receives the workspace extension context and HTTP request. It verifies the session cookie, checks that the signed-in email belongs to an admin in the same workspace, reads headroom, balance, autopay settings, and recent purchases from core storage, then optionally asks Stripe for card details. It returns a JSON response with either an error, an unlimited status, or the detailed billing projection.

**Call relations**: The billing route registered in manifest calls this for GET requests. It uses _billing_record and _card_on_file for Stripe-backed card display, and several balance and seat helpers for local authorization and money state.

*Call graph*: calls 3 internal fn (transaction, _billing_record, _card_on_file); 9 external calls (configured_auto_topup, read_balance, read_headroom, recent_purchases, verify_token, JSONResponse, warn, member_by_email, member_is_admin).


##### `manifest`  (lines 960–998)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the UFO host. It tells the system which tools, jobs, routes, prompt text, and credential slots this file provides.

**Data flow**: It creates and returns a Manifest object. The manifest includes the manage_billing tool, the usage shipping and top-up jobs with their schedules and workspace candidate sets, the billing HTTP route, the billing prompt guidance, and an Anthropic BYOK credential slot.

**Call relations**: The host calls this when loading the extension. The objects it returns connect _ship, _top_up, _billing_projection, _billing_request_workspace, and manage_billing into the larger runtime.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, metered_workspaces, member_workspaces).

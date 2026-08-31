# Cross-cutting billing, metering, feature flags, and operator observability  `stage-21` (cross-cutting infrastructure)

This stage is shared behind-the-scenes support for money, feature access, and operator visibility. It is used throughout the system whenever a model is called, a tool runs, usage is reported, or staff need to inspect what is happening.

The billing pieces work like a cash register and receipt book. accounting.py records workspace usage, checks spend limits, charges prepaid balances, exports usage to billing partners, and builds spend reports. balance.py tracks prepaid credit in micro-USD, very small fractions of a dollar, and decides what happens when credit is low or gone. pricing.py stores model price tables and turns token counts into charges, keeping a version stamp so old bills can be traced to the exact prices used.

The external billing bridge is metronome.py, which reports usage to Metronome and connects prepaid payment flows to Stripe, including refill and billing portal support. flags.py is the system’s feature switchboard, letting code ask whether a feature is enabled for a workspace. o11y.py provides traces, metrics, logs, and safety filters. The debugger surface gives trusted operators read-only fleet inspection tools without changing customer data.

## Files in this stage

### Billing ledger and credit
Core billing code records usage, manages prepaid balance, applies model pricing, and produces spend information.

### `core/src/ufo/runtime/billing/accounting.py`

`domain_logic` · `request handling, billing writes, reporting, and background usage export`

This file is the place where usage becomes money. When a turn uses model tokens, makes sandbox network requests, or generates images or videos, this code writes rows into a ledger, which is like an accounting notebook: each row says what was used, by whom, when, and what it cost. Some usage only counts activity, such as egress requests, while tokens and media can also debit the workspace balance.

A key idea is that model usage may be reported more than once during retries or recovery. The file avoids double-charging by treating a turn attempt as cumulative: if a later report is just a bigger version of the same attempt, only the increase is charged. If the reports contradict each other, it raises an error rather than quietly corrupting billing.

The file also decides whether work may start or continue. Balance checks stop work when prepaid credit is too low, with special rules for work paid by the workspace's own provider key. Spend caps check rolling time windows for workspace, member, or agent limits.

Finally, it turns ledger rows into reports and export batches. Reports answer questions like “who spent what?” and “which model cost the most?” Exports freeze usage deltas so an outside billing system can receive retries safely without being billed twice.

#### Function details

##### `applicable_caps_absent`  (lines 60–66)

```
def applicable_caps_absent(workspace_id: UUID, member_id: UUID | None, agent_id: UUID) -> bool
```

**Purpose**: Quickly answers whether this exact workspace, member, and agent combination was recently found to have no spend caps. It is a shortcut that avoids unnecessary database reads in deployments that do not use caps.

**Data flow**: It receives a workspace id, optional member id, and agent id. It looks in a small in-memory cache for that exact trio and compares the stored expiry time with the current clock. It returns true only if the cached “no caps here” note is still fresh; it changes nothing.

**Call relations**: This is a fast path for cap enforcement code outside this file. It pairs with _note_absent_caps, which records the cache entry after SpendEvaluator.decide has actually checked the database and found no relevant caps.

*Call graph*: 1 external calls (monotonic).


##### `_note_absent_caps`  (lines 69–78)

```
def _note_absent_caps(key: tuple[UUID, UUID | None, UUID]) -> None
```

**Purpose**: Remembers for a few seconds that no spend cap applies to one workspace/member/agent trio. This makes repeated checks cheap without making cap changes invisible for long.

**Data flow**: It receives the cache key. It reads the current time, clears expired entries if the cache is already large, and stores a new expiry time for that key. It returns nothing, but updates the in-memory cache.

**Call relations**: SpendEvaluator.decide calls this only after a real database check finds no caps. Later, applicable_caps_absent can use that note to let callers skip a database round trip briefly.

*Call graph*: called by 1 (decide); 1 external calls (monotonic).


##### `_total_tokens`  (lines 81–89)

```
def _total_tokens(usage: Usage) -> int
```

**Purpose**: Adds all token categories in a Usage object into one total token count. Billing writers use this to decide whether there is anything to record and what total amount belongs in the ledger.

**Data flow**: It receives a Usage record with input, output, cache-read, and cache-write token counts. It adds those fields together. It returns the integer total and changes nothing.

**Call relations**: record_turn_usage, record_workspace_usage, and record_sandbox_tokens call this before pricing or writing token ledger rows.

*Call graph*: called by 3 (record_sandbox_tokens, record_turn_usage, record_workspace_usage).


##### `_prompt_tokens`  (lines 92–101)

```
def _prompt_tokens(usage: Usage) -> int
```

**Purpose**: Counts the tokens that made up the prompt the provider read, including cached prompt parts. This is used later to show what share of the prompt came from cache.

**Data flow**: It receives a Usage record. It adds input tokens and all cache read/write token classes, but not output tokens. It returns that prompt-side token count and changes nothing.

**Call relations**: The token billing writers call this when storing ledger rows. read_turn_cost later uses the stored prompt and cache-read counts to calculate a cache percentage.

*Call graph*: called by 3 (record_sandbox_tokens, record_turn_usage, record_workspace_usage).


##### `workspace_owns_the_key`  (lines 104–118)

```
async def workspace_owns_the_key(connection: AsyncConnection, workspace_id: UUID, key_slot: str | None) -> bool
```

**Purpose**: Checks whether a provider key slot belongs to the workspace. This matters because work paid directly with the workspace's own key should not also be charged against the platform balance.

**Data flow**: It receives a database connection, workspace id, and optional key slot name. If no slot is supplied, it returns false. Otherwise it queries the credential table for that workspace and slot, then returns true if such a credential exists.

**Call relations**: BalanceGate._workspace_serves_itself calls this after it has figured out which key slot a model would use. The result feeds admission decisions for low-balance work.

*Call graph*: called by 1 (_workspace_serves_itself); 2 external calls (execute, select).


##### `record_turn_usage`  (lines 121–256)

```
async def record_turn_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, usage: Usage, attempt: str='', pricing: Pricing=CORE_PRICING, byok: bool=False) -> None
```

**Purpose**: Records and bills model token usage for one turn attempt. It is built to survive retries by charging only new cumulative usage, not the same usage twice.

**Data flow**: It receives a connection, workspace, turn, model name, usage counters, attempt id, pricing table, and whether the workspace brought its own key. It totals and prices the usage, locks any existing ledger row for the same attempt, compares old and new counters, debits only the price increase unless byok is true, then inserts or updates the ledger row. If the incoming report conflicts with earlier billing identity or counters, it raises TurnUsageConflict.

**Call relations**: Turn-running code calls this as usage is completed or replayed. It uses _total_tokens and _prompt_tokens for amounts, Pricing.micro_usd for cost, debit to move balance, and ledger_id_for indirectly through the generated ledger id.

*Call graph*: calls 3 internal fn (_prompt_tokens, _total_tokens, micro_usd); 7 external calls (__init__, execute, insert, select, update, debit, ledger_id_for).


##### `read_turn_cost`  (lines 270–300)

```
async def read_turn_cost(connection: AsyncConnection, turn_id: UUID, dimension: str) -> TurnCost | None
```

**Purpose**: Reads the total cost of one turn for a chosen ledger dimension, such as host-side tokens or sandbox tokens. It combines multiple attempts so the user sees the full cost of the turn.

**Data flow**: It receives a connection, turn id, and dimension name. It sums ledger rows for that turn and dimension, reads the model and prompt/cache counts, and returns a TurnCost with tokens, micro-dollars, model, and cache percentage. If nothing was billed, it returns null.

**Call relations**: Terminal turn display or reporting code can call this after usage has been recorded. It depends on billing writers having stored prompt and cache token splits in the ledger.

*Call graph*: 3 external calls (__init__, execute, select).


##### `record_workspace_usage`  (lines 303–352)

```
async def record_workspace_usage(connection: AsyncConnection, workspace_id: UUID, model: str, usage: Usage, pricing: Pricing=CORE_PRICING, byok: bool=False) -> None
```

**Purpose**: Bills model token usage that belongs to a workspace but not to a specific turn, such as a background job. It makes that spend count for workspace totals and workspace-level caps.

**Data flow**: It receives workspace, model, usage, pricing, and byok information. It totals and prices the tokens, debits the workspace unless byok is true, and inserts a fresh ledger row with no turn id. If the usage is zero, it does nothing.

**Call relations**: Background job code calls this instead of record_turn_usage. It uses the same token helpers and pricing logic as turn billing, but creates a new row each time because there is no turn attempt replay key.

*Call graph*: calls 3 internal fn (_prompt_tokens, _total_tokens, micro_usd); 4 external calls (execute, insert, debit, uuid4).


##### `record_egress_request`  (lines 355–384)

```
async def record_egress_request(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, amount: int=1) -> None
```

**Purpose**: Counts sandbox network egress requests made during a turn. These requests are measured for reporting but priced at zero, so they do not debit balance or spend caps.

**Data flow**: It receives a connection, workspace id, turn id, and request count. It builds a stable ledger id for that turn and egress dimension, then inserts the row or atomically adds to the existing amount. It returns nothing.

**Call relations**: The sandbox egress proxy calls this when it flushes request counts for a turn. It uses ledger_id_for so all egress for the same turn lands in one ledger row.

*Call graph*: 2 external calls (execute, ledger_id_for).


##### `record_probe_egress_request`  (lines 387–413)

```
async def record_probe_egress_request(connection: AsyncConnection, workspace_id: UUID, amount: int=1) -> None
```

**Purpose**: Counts sandbox egress requests made by an off-turn probe. A probe is not tied to a user turn, so the row belongs only to the workspace.

**Data flow**: It receives a connection, workspace id, and request count. It inserts a fresh zero-priced egress ledger row with no turn id. It returns nothing and does not debit balance.

**Call relations**: Probe or proxy code calls this for network activity outside a turn. Unlike record_egress_request, it uses a fresh id so separate probe batches do not merge accidentally.

*Call graph*: 3 external calls (execute, insert, uuid4).


##### `record_sandbox_tokens`  (lines 416–492)

```
async def record_sandbox_tokens(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, usage: Usage, pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: Records and bills model token usage from calls made inside the sandbox through the egress proxy. This is separate from the normal host-side turn loop so both sources can be counted without overlap.

**Data flow**: It receives workspace, turn, model, usage, and pricing. It totals and prices the tokens, debits the workspace, and inserts or atomically adds to a per-turn sandbox token ledger row. It stores the same token class details as normal turn billing.

**Call relations**: The egress proxy or sandbox model-call path calls this. It uses _total_tokens, _prompt_tokens, Pricing.micro_usd, debit, and ledger_id_for, and read_turn_cost can later read this dimension.

*Call graph*: calls 3 internal fn (_prompt_tokens, _total_tokens, micro_usd); 3 external calls (execute, debit, ledger_id_for).


##### `record_image_usage`  (lines 495–514)

```
async def record_image_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, images: int, micro_usd: int) -> None
```

**Purpose**: Records and bills generated image usage for a turn. The caller supplies the price because image providers may charge by units that are not standard text tokens.

**Data flow**: It receives workspace, turn, model, image count, and cost in micro-dollars. It passes those values to the shared media writer with the images dimension. It returns nothing itself.

**Call relations**: Image generation provider code calls this after it knows the provider charge. It delegates the actual ledger write and balance debit to _record_media_usage.

*Call graph*: calls 1 internal fn (_record_media_usage).


##### `record_video_usage`  (lines 517–531)

```
async def record_video_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, videos: int, micro_usd: int) -> None
```

**Purpose**: Records and bills generated video usage for a turn. Like image billing, the caller provides the price because video charging can use provider-specific units.

**Data flow**: It receives workspace, turn, model, video count, and cost in micro-dollars. It passes those values to the shared media writer with the videos dimension. It returns nothing itself.

**Call relations**: Video generation provider code calls this after a chargeable generation. It shares the same underlying write path as record_image_usage through _record_media_usage.

*Call graph*: calls 1 internal fn (_record_media_usage).


##### `_record_media_usage`  (lines 534–575)

```
async def _record_media_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, dimension: str, model: str, amount: int, micro_usd: int) -> None
```

**Purpose**: Writes the common ledger row for image and video usage and debits the workspace for the new cost. It keeps repeated generations on the same turn accumulated safely.

**Data flow**: It receives the dimension, model, amount, and price. It creates a per-turn ledger id, debits the workspace by the given price, then inserts the ledger row or atomically adds amount and price to the existing row. It returns nothing.

**Call relations**: record_image_usage and record_video_usage both call this. It uses debit for the balance movement and ledger_id_for to keep each media dimension separate from token and egress rows.

*Call graph*: called by 2 (record_image_usage, record_video_usage); 3 external calls (execute, debit, ledger_id_for).


##### `mint_usage_exports`  (lines 600–715)

```
async def mint_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, floor: datetime, key_slot_for: Callable[[str], str | None]) -> None
```

**Purpose**: Creates frozen export-intent rows for usage that has not yet been sent to an outside billing consumer. Freezing the delta makes retries safe: the same usage slice can be resent without recalculating a different answer.

**Data flow**: It receives a workspace, consumer name, backfill floor time, and a function that maps model names to key slots. It finds ledger rows that have grown beyond what this consumer has already exported, applies settlement rules for turn-based accumulated rows, decides the byok flag, and inserts one export intent per new delta. Existing identical intents are ignored.

**Call relations**: A background export job calls this before reading pending exports. It reads ledger, turn, credential, and ledger_export tables, then read_pending_usage_exports can deliver the frozen rows it creates.

*Call graph*: 5 external calls (now, timedelta, execute, or_, select).


##### `read_pending_usage_exports`  (lines 718–763)

```
async def read_pending_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, limit: int) -> tuple[UsageExport, ...]
```

**Purpose**: Reads usage export intents that have been minted but not acknowledged by the outside consumer. It returns the frozen deltas in a stable order for delivery.

**Data flow**: It receives workspace id, consumer name, and a maximum count. It joins pending export rows to their ledger rows, calculates amount and price deltas, and returns UsageExport objects. It does not change the database.

**Call relations**: The export delivery loop calls this after mint_usage_exports. Once the external consumer accepts a batch, ack_usage_exports marks these same entries as done.

*Call graph*: 3 external calls (__init__, execute, select).


##### `ack_usage_exports`  (lines 766–790)

```
async def ack_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: Marks exported usage intents as acknowledged after the external billing consumer accepted them. This removes them from future pending reads.

**Data flow**: It receives workspace, consumer, and the UsageExport objects that were successfully delivered. It builds a condition matching each ledger id and starting amount, then updates those rows with an acknowledgement time. It returns nothing.

**Call relations**: The export delivery loop calls this only after read_pending_usage_exports results have been sent successfully. If a crash happens before this call, the same frozen exports stay pending and can be retried.

*Call graph*: 3 external calls (execute, or_, update).


##### `metered_workspaces`  (lines 793–796)

```
def metered_workspaces() -> WorkspaceCandidates
```

**Purpose**: Finds workspaces that have ever written ledger usage, so a background exporter knows which workspaces are worth checking. It is intentionally broad and cheap to refine later.

**Data flow**: It builds a candidate source from distinct workspace ids in the ledger table and wraps it in the system's WorkspaceCandidates helper. It returns that candidate object and does not query immediately here.

**Call relations**: Usage export scheduling code can call this to decide which workspace owners to visit. The actual candidate machinery is provided by owner_candidates.

*Call graph*: 1 external calls (owner_candidates).


##### `SpendEvaluator.decide`  (lines 832–847)

```
async def decide(self, connection: AsyncConnection, pending_micro_usd: int) -> SpendDecision
```

**Purpose**: Decides whether a turn is allowed, parked, or rejected under spend caps. A spend cap is a rolling limit, such as “this agent may spend $10 per day.”

**Data flow**: It receives a connection and pending cost. It loads applicable caps, caches the no-cap case, sums recent spend for each cap, adds the pending cost, and compares against each limit. It returns a SpendDecision with an outcome and user-facing message.

**Call relations**: Turn admission or mid-turn enforcement code calls this when caps may apply. It orchestrates _applicable_caps, _used_micro_usd, _message, and _note_absent_caps.

*Call graph*: calls 4 internal fn (_applicable_caps, _message, _used_micro_usd, _note_absent_caps); 1 external calls (__init__).


##### `SpendEvaluator._applicable_caps`  (lines 849–877)

```
async def _applicable_caps(self, connection: AsyncConnection) -> tuple[SpendCap, ...]
```

**Purpose**: Loads the spend caps that apply to this specific workspace/member/agent combination. It checks workspace-wide caps plus caps for the current member or agent.

**Data flow**: It reads the spend_cap table for matching workspace, scope, and subject id. It turns each database row into a SpendCap object. It returns a tuple of caps without deciding whether they are breached.

**Call relations**: SpendEvaluator.decide calls this first. Its result determines whether the evaluator can allow immediately or must check recent spend.

*Call graph*: called by 1 (decide); 4 external calls (__init__, execute, or_, select).


##### `SpendEvaluator._used_micro_usd`  (lines 879–905)

```
async def _used_micro_usd(self, connection: AsyncConnection, cap: SpendCap) -> int
```

**Purpose**: Calculates how much money has already been spent inside one cap's rolling time window. The query changes depending on whether the cap is for a workspace, member, or agent.

**Data flow**: It receives a SpendCap, computes the cutoff time from the cap's window length, then sums priced ledger amounts since that cutoff. Workspace caps read by workspace id; member and agent caps join through turns and conversations. It returns the summed micro-dollar amount.

**Call relations**: SpendEvaluator.decide calls this once for each applicable cap. The returned spend is combined with the pending cost to determine breaches.

*Call graph*: called by 1 (decide); 4 external calls (now, timedelta, execute, select).


##### `SpendEvaluator._message`  (lines 907–918)

```
def _message(self, outcome: SpendOutcome, breaches: list[SpendCap]) -> str
```

**Purpose**: Builds the plain message shown when a spend cap parks or rejects a turn. It names the tightest breached cap and formats its limit as dollars.

**Data flow**: It receives the chosen outcome and the breached caps. It picks the lowest limit among them, converts micro-dollars to dollars, and returns a sentence explaining the park or rejection. It changes nothing.

**Call relations**: SpendEvaluator.decide calls this only after it has found cap breaches. The returned text becomes the message inside SpendDecision.

*Call graph*: called by 1 (decide).


##### `_token_sum`  (lines 1035–1043)

```
def _token_sum() -> sa.ColumnElement[int]
```

**Purpose**: Builds a database expression that sums only token-like dimensions. It keeps token totals from being polluted by images, videos, or egress counts.

**Data flow**: It creates a SQL expression: if a ledger row's dimension is normal tokens or sandbox tokens, include its amount; otherwise include zero; then sum and default null to zero. It returns the expression, not a computed number.

**Call relations**: SpendRollup.read, SpendRollup._by_origin, and _usage_details use this expression inside larger reporting queries.

*Call graph*: called by 3 (_by_origin, read, _usage_details); 1 external calls (case).


##### `_token_cost_sum`  (lines 1046–1058)

```
def _token_cost_sum() -> sa.ColumnElement[int]
```

**Purpose**: Builds a database expression that sums the cost of only token-like dimensions. This lets reports separate token cost from total cost that may include media.

**Data flow**: It creates a SQL expression that includes priced_micro_usd only for normal tokens and sandbox tokens, otherwise zero, then sums it. It returns the expression for use in a query.

**Call relations**: SpendRollup.read, SpendRollup._by_origin, and _usage_details use this alongside _token_sum in report queries.

*Call graph*: called by 3 (_by_origin, read, _usage_details); 1 external calls (case).


##### `_usage_details`  (lines 1061–1185)

```
async def _usage_details(connection: AsyncConnection, source: sa.FromClause, scope: sa.ColumnElement[bool], cutoff: datetime | None, now: datetime) -> UsageDetails
```

**Purpose**: Builds the common usage section shared by workspace, member, and agent spend reports. It gives selected-window totals, all-time totals, daily history, model breakdowns, and previous-period token comparison.

**Data flow**: It receives a connection, a table/join source, a scope condition, an optional cutoff, and the current time. It runs several aggregate queries for selected and all-time totals, daily rows, execution labels, model labels, and previous-period tokens. It returns one UsageDetails object with all those pieces.

**Call relations**: SpendRollup.read, SpendRollup.read_agent, and SpendRollup.read_member call this after setting up the right source and scope. It relies on _token_sum and _token_cost_sum to keep token reporting consistent.

*Call graph*: calls 2 internal fn (_token_cost_sum, _token_sum); called by 3 (read, read_agent, read_member); 9 external calls (__init__, __init__, __init__, __init__, fromisoformat, date, timedelta, execute, select).


##### `SpendRollup.read`  (lines 1196–1300)

```
async def read(self, connection: AsyncConnection, window_seconds: int | None) -> SpendReport
```

**Purpose**: Builds a full workspace spend report for a selected time window or all time. It answers high-level billing questions like total spend, spend by dimension, member, agent, origin, and price table.

**Data flow**: It receives a database connection and optional window length. It computes a cutoff, queries ledger totals and grouped breakdowns, asks _by_origin for origin grouping, asks _usage_details for shared usage charts, and returns a SpendReport object.

**Call relations**: Workspace billing or admin reporting endpoints call this. It coordinates helper expressions, _by_origin, and _usage_details into one report object.

*Call graph*: calls 4 internal fn (_by_origin, _token_cost_sum, _token_sum, _usage_details); 8 external calls (__init__, __init__, __init__, __init__, now, timedelta, execute, select).


##### `SpendRollup._by_origin`  (lines 1302–1367)

```
async def _by_origin(self, connection: AsyncConnection, window: sa.ColumnElement[bool]) -> tuple[OriginTotal, ...]
```

**Purpose**: Groups token spend by the place where the user-facing conversation began, even when subagents later spent tokens in private child turns. This makes the report match how people think about where work started.

**Data flow**: It receives a connection and window condition. It builds a recursive database query that walks parent_turn_id links from spending turns back to root turns, joins to the root conversation, groups token spend by surface label, and returns OriginTotal rows.

**Call relations**: SpendRollup.read calls this for the workspace report. It uses _token_sum and _token_cost_sum so origin totals count only token dimensions.

*Call graph*: calls 2 internal fn (_token_cost_sum, _token_sum); called by 1 (read); 4 external calls (__init__, desc, execute, select).


##### `SpendRollup.read_agent`  (lines 1369–1425)

```
async def read_agent(self, connection: AsyncConnection, agent_id: UUID, window_seconds: int | None) -> AgentSpendReport
```

**Purpose**: Builds a spend report for one agent, including that agent's own caps. Workspace-only jobs are excluded because they do not belong to an agent turn.

**Data flow**: It receives a connection, agent id, and optional window length. It joins ledger rows to turns for that agent, groups spend by dimension, reads agent-scoped caps, builds shared usage details, and returns an AgentSpendReport.

**Call relations**: Agent billing or admin views call this. It delegates detailed token history to _usage_details after preparing the agent-specific scope.

*Call graph*: calls 1 internal fn (_usage_details); 7 external calls (__init__, __init__, __init__, now, timedelta, execute, select).


##### `SpendRollup.read_member`  (lines 1427–1485)

```
async def read_member(self, connection: AsyncConnection, member_id: UUID, window_seconds: int | None) -> MemberSpendReport
```

**Purpose**: Builds a spend report for one workspace member, including that member's caps. It follows ledger rows through turns and conversations to find which member started the work.

**Data flow**: It receives a connection, member id, and optional window length. It joins ledger, turn, and conversation tables, filters to the member, groups by dimension, reads member-scoped caps, builds shared usage details, and returns a MemberSpendReport.

**Call relations**: Member billing or admin views call this. It mirrors SpendRollup.read_agent but uses conversation membership instead of turn agent id.

*Call graph*: calls 1 internal fn (_usage_details); 7 external calls (__init__, __init__, __init__, now, timedelta, execute, select).


##### `BalanceGate.admits`  (lines 1517–1555)

```
async def admits(self, connection: AsyncConnection, agent_id: UUID | None=None, key_slot_for: Callable[[str], str | None] | None=None, turn_id: UUID | None=None, model: str | None=None) -> SpendDecisi
```

**Purpose**: Decides whether a workspace has enough balance to start, resume, or fold in a turn. Starting requires a reserve, but there is an exception for turns paid by the workspace's own provider key while the balance is still above zero.

**Data flow**: It receives a connection plus optional agent, key-slot resolver, turn id, and model. It reads balance headroom. If there is no balance row, it allows. If balance is above the reserve adjusted by grace, it allows. Otherwise it checks whether this turn already debited anything and whether the workspace will serve the model with its own key. It returns allow or reject with a billing message.

**Call relations**: Turn admission and resume paths call this before work begins. It uses read_headroom, _workspace_serves_itself, _turn_has_debited, and balance_refusal_message.

*Call graph*: calls 2 internal fn (_turn_has_debited, _workspace_serves_itself); 4 external calls (__init__, _forget_absent_balance, balance_refusal_message, read_headroom).


##### `BalanceGate._workspace_serves_itself`  (lines 1557–1579)

```
async def _workspace_serves_itself(self, connection: AsyncConnection, agent_id: UUID | None, key_slot_for: Callable[[str], str | None] | None, model: str | None=None) -> bool
```

**Purpose**: Figures out whether the workspace's own provider key will serve the model call. This supports the rule that own-key token calls do not need the usual starting reserve.

**Data flow**: It receives optional agent id, key-slot resolver, and model. If no model was supplied, it reads the agent's model from the database. It asks the resolver which credential slot that model uses, then calls workspace_owns_the_key. It returns true only when the workspace owns that slot.

**Call relations**: BalanceGate.admits calls this when balance is below the normal entry reserve but still positive. It hands the final key-ownership question to workspace_owns_the_key.

*Call graph*: calls 1 internal fn (workspace_owns_the_key); called by 1 (admits); 2 external calls (execute, select).


##### `BalanceGate.sustains`  (lines 1581–1602)

```
async def sustains(self, connection: AsyncConnection, pending_micro_usd: int, turn_id: UUID | None=None) -> SpendDecision
```

**Purpose**: Decides whether a running turn may continue into another round. Unlike admission, continuation stops at zero balance, not at the starting reserve.

**Data flow**: It receives a connection, pending unbilled cost, and optional turn id. It reads balance headroom. If no balance row exists, it allows. If balance minus pending cost stays above the grace-adjusted floor, it allows. If the pending cost is positive or the turn has already debited balance, it rejects; otherwise it allows because non-debiting work cannot improve by being parked.

**Call relations**: The running turn loop calls this before taking more billable work. It uses read_headroom, _turn_has_debited, and balance_refusal_message.

*Call graph*: calls 1 internal fn (_turn_has_debited); 4 external calls (__init__, _forget_absent_balance, balance_refusal_message, read_headroom).


##### `BalanceGate._turn_has_debited`  (lines 1604–1627)

```
async def _turn_has_debited(self, connection: AsyncConnection, turn_id: UUID | None) -> bool
```

**Purpose**: Checks whether a turn has already taken any money from the workspace balance. This is different from asking whether the turn has a priced ledger row, because own-key token usage may be priced but not debited.

**Data flow**: It receives a connection and optional turn id. If there is no turn id, it returns false. Otherwise it searches ledger rows for that turn with debited_micro_usd greater than zero and returns true if any exist.

**Call relations**: BalanceGate.admits and BalanceGate.sustains call this to avoid letting already-charging turns cycle forever around low-balance rules.

*Call graph*: called by 2 (admits, sustains); 2 external calls (execute, select).


### `core/src/ufo/runtime/billing/balance.py`

`domain_logic` · `cross-cutting: used during billing setup, admin reads, payment fulfillment, and before paid model work starts`

This file is the billing “wallet” for a workspace. Instead of recalculating the balance from every purchase each time, it keeps a current balance row in the database, like the number shown on a prepaid card. The detailed purchase rows are still kept as the receipt trail, so the current number can be checked against what was ever granted and what was ever debited.

The file supports two kinds of reads. Fast reads, such as read_headroom, are used before model work starts and only fetch the few numbers needed to decide whether work may begin. Fuller reads, such as read_balance and recent_purchases, are for admin screens and audits, where showing totals and history matters more than speed.

It also records credits and debits. credit adds a purchase only once for a given reference, so a retried payment webhook cannot double-credit the workspace. debit subtracts actual usage, even if that makes the balance negative, because keeping an honest record of spent money is safer than rejecting the ledger entry.

There are also auto-top-up helpers. They store and read the amount to refill and the threshold that triggers it. A workspace that has successfully paid before earns a small grace amount, so normal work is not blocked during the short delay between crossing the threshold and Stripe or another payment system completing the refill.

#### Function details

##### `balance_absent`  (lines 39–45)

```
def balance_absent(workspace_id: UUID) -> bool
```

**Purpose**: This is a quick memory check for workspaces that recently had no balance row. It helps self-hosted or unpaid deployments avoid a database read before every round when there is known to be no prepaid balance.

**Data flow**: It takes a workspace ID, looks in a small in-memory map, and compares the saved expiry time with the current monotonic clock, which is a clock used for measuring elapsed time safely. It returns true only if the workspace was recently seen to have no balance row and that note has not expired.

**Call relations**: This is a connectionless fast path. Other code can call it before paying the cost of a database lookup, while _note_absent_balance creates the remembered absence and _forget_absent_balance removes it when credit appears.

*Call graph*: 1 external calls (monotonic).


##### `_note_absent_balance`  (lines 48–55)

```
def _note_absent_balance(workspace_id: UUID) -> None
```

**Purpose**: This remembers, for a few seconds, that a workspace had no balance row. The point is to avoid repeatedly asking the database the same question for workspaces that are not using prepaid billing.

**Data flow**: It takes a workspace ID, reads the current monotonic time, optionally clears expired entries if the cache is full, and stores a short-lived expiry time for that workspace. It changes only the in-memory absence cache and returns nothing.

**Call relations**: read_balance and read_headroom call this after a database lookup finds no balance row. That makes later checks cheaper until either the note expires or credit calls _forget_absent_balance because the workspace now has a balance.

*Call graph*: called by 2 (read_balance, read_headroom); 1 external calls (monotonic).


##### `_forget_absent_balance`  (lines 58–61)

```
def _forget_absent_balance(workspace_id: UUID) -> None
```

**Purpose**: This removes the temporary note saying a workspace has no balance. It is used when a balance row is seen or created, so the fast path does not accidentally hide a newly credited workspace.

**Data flow**: It takes a workspace ID and removes that ID from the in-memory absence cache if it is present. It returns nothing and does not touch the database.

**Call relations**: credit calls this after successfully adding money to a workspace balance. It is the counterpart to _note_absent_balance, keeping the shortcut safe after a workspace moves from unpaid or self-hosted behavior into having credit.

*Call graph*: called by 1 (credit).


##### `billing_screen_url`  (lines 78–87)

```
def billing_screen_url(public_base_url: str | None, home_surface: str | None) -> str | None
```

**Purpose**: This builds the web address for the billing screen, if this deployment has one. It lets later billing checks show a useful link instead of forcing each caller to know how browser surfaces are mounted.

**Data flow**: It receives the public base URL of the deployment and the name of the home browser surface. If either is missing, it returns None; otherwise it trims any trailing slash from the base URL and joins it with the surface path and billing fragment.

**Call relations**: This is usually prepared during setup and passed down to code that may need to refuse paid work. balance_refusal_message can then use the resulting URL to tell a user where an admin can add credit.


##### `balance_refusal_message`  (lines 90–99)

```
def balance_refusal_message(billing_url: str | None) -> str
```

**Purpose**: This writes the human-facing message shown when a workspace is out of credit. It makes sure the message is still clear even when there is no billing web page to link to.

**Data flow**: It takes an optional billing URL. If there is no URL, it returns a plain sentence saying an admin can set up automatic refills; if there is a URL, it includes that address in the sentence.

**Call relations**: This sits at the edge between billing decisions and user-facing text. Code that blocks work because of insufficient credit can call it after billing_screen_url has decided whether a billing link exists.


##### `read_auto_topup`  (lines 111–133)

```
async def read_auto_topup(connection: AsyncConnection, workspace_id: UUID) -> AutoTopup | None
```

**Purpose**: This answers the refill job’s practical question: should this workspace be topped up now, and by how much? It only returns settings when auto top-up exists and the current balance is at or below the configured threshold.

**Data flow**: It receives a database connection and workspace ID, reads the workspace’s balance and auto top-up columns, and checks whether a refill amount is configured. If there is no row, no refill setting, or the balance is still above the threshold, it returns None; otherwise it returns an AutoTopup object with the amount and threshold.

**Call relations**: This function is used by code that decides when to ask a payment extension to charge a card. It keeps the “is the workspace short?” decision in core billing, while the payment extension can focus on how to collect the money.

*Call graph*: 3 external calls (__init__, execute, select).


##### `set_auto_topup`  (lines 136–156)

```
async def set_auto_topup(connection: AsyncConnection, workspace_id: UUID, amount_micro_usd: int | None, threshold_micro_usd: int | None) -> bool
```

**Purpose**: This turns automatic refills on or off for a workspace that already has a balance. It prevents half-configured refill rules by requiring both the refill amount and threshold together, or neither.

**Data flow**: It receives a database connection, workspace ID, and optional amount and threshold. If only one of amount or threshold is provided, it raises a ValueError; otherwise it updates the existing balance row with the new settings and timestamp, then returns true if exactly one row was changed.

**Call relations**: Admin or billing settings code calls this when a workspace changes its refill preferences. Later, read_auto_topup and configured_auto_topup read the values it stored for charging decisions or display.

*Call graph*: 2 external calls (execute, update).


##### `mark_topup_verified`  (lines 159–173)

```
async def mark_topup_verified(connection: AsyncConnection, workspace_id: UUID) -> None
```

**Purpose**: This records that the workspace has successfully completed a top-up payment at least once. That proof of payment earns the workspace a fixed grace amount when deciding whether work can continue.

**Data flow**: It receives a database connection and workspace ID, then updates the balance row only if the verification timestamp is still empty. It stamps the verification time and update time, and returns nothing.

**Call relations**: Payment fulfillment code calls this after a charge has settled. Later, read_headroom notices the verification timestamp and includes the grace allowance used by admission checks before model work.

*Call graph*: 2 external calls (execute, update).


##### `configured_auto_topup`  (lines 185–206)

```
async def configured_auto_topup(connection: AsyncConnection, workspace_id: UUID) -> AutoTopup | None
```

**Purpose**: This reads the auto top-up rule exactly as configured, even if the workspace has not reached the refill threshold yet. It is useful for showing an admin what rule is currently set.

**Data flow**: It receives a database connection and workspace ID, reads the refill amount and threshold from the balance row, and returns None if there is no row or no configured amount. Otherwise it returns an AutoTopup object with the stored settings.

**Call relations**: Unlike read_auto_topup, this does not decide whether a refill should happen now. Settings and admin display code can use it to report the saved rule, while the refill job uses read_auto_topup for the action decision.

*Call graph*: 3 external calls (__init__, execute, select).


##### `read_headroom`  (lines 209–228)

```
async def read_headroom(connection: AsyncConnection, workspace_id: UUID) -> Headroom | None
```

**Purpose**: This reads only the numbers needed to decide whether a workspace has enough room to start paid work. It avoids the heavier lifetime totals that an admin page might need.

**Data flow**: It receives a database connection and workspace ID, reads the current balance, required reserve, and top-up verification timestamp. If no balance row exists, it notes that absence in the short-lived cache and returns None; otherwise it returns a Headroom object with balance, reserve, and either zero grace or the fixed grace amount.

**Call relations**: This is called in the fast path before model rounds or other paid work. If it finds no balance, it calls _note_absent_balance; if it finds a verified top-up, it gives admission checks the extra grace that mark_topup_verified made available.

*Call graph*: calls 1 internal fn (_note_absent_balance); 3 external calls (__init__, execute, select).


##### `recent_purchases`  (lines 242–275)

```
async def recent_purchases(connection: AsyncConnection, workspace_id: UUID, limit: int) -> tuple[Purchase, ...]
```

**Purpose**: This returns the newest balance credits for a workspace, capped at a requested limit. It supports billing screens that need to show where the current balance came from without loading an unlimited history.

**Data flow**: It receives a database connection, workspace ID, and maximum number of purchases. It queries purchase rows for that workspace, orders newest first with a stable tie-breaker, converts each row into a Purchase object, and returns them as a tuple.

**Call relations**: Admin or operator views use this alongside read_balance. read_balance gives the totals; recent_purchases gives the visible receipt list behind those totals.

*Call graph*: 3 external calls (__init__, execute, select).


##### `read_balance`  (lines 278–308)

```
async def read_balance(connection: AsyncConnection, workspace_id: UUID) -> Balance | None
```

**Purpose**: This gives the full billing picture for a workspace: what is left now, what reserve is required, and the lifetime credit and charge totals. It is meant for people or admin tools, not the tight pre-work gate.

**Data flow**: It receives a database connection and workspace ID, first reads the current balance row, and if none exists records the absence in the short-lived cache and returns None. If a row exists, it sums all purchase rows for total granted credit, total charged money, and last purchase time, then returns a Balance object.

**Call relations**: This is the fuller companion to read_headroom. Both call _note_absent_balance when there is no row, but read_balance also performs the aggregate purchase query needed for audit and display.

*Call graph*: calls 1 internal fn (_note_absent_balance); 3 external calls (__init__, execute, select).


##### `credit`  (lines 311–369)

```
async def credit(connection: AsyncConnection, workspace_id: UUID, granted_micro_usd: int, charged_micro_usd: int, reference: str) -> bool
```

**Purpose**: This adds credit to a workspace exactly once for a given reference, such as a payment ID. It protects against duplicate deliveries or retries accidentally adding the same purchase twice.

**Data flow**: It receives a database connection, workspace ID, granted amount, charged amount, and reference string. It inserts a purchase row using a database conflict rule that does nothing if the same workspace and reference already exist; if the insert did not happen, it returns false. If the purchase is new, it creates or updates the workspace balance by adding the granted amount, clears any cached “no balance” note, and returns true.

**Call relations**: Payment fulfillment or operator crediting code calls this inside its own database transaction so the payment source and balance update succeed or fail together. After that transaction commits, the caller can use count_charge when credit returned true.

*Call graph*: calls 1 internal fn (_forget_absent_balance); 2 external calls (execute, uuid4).


##### `count_charge`  (lines 372–389)

```
def count_charge(charged_micro_usd: int) -> None
```

**Purpose**: This reports positive charged money to the metrics system, so operators can see how much balance has been sold. It deliberately ignores grants, refunds, and corrections because a metric counter can go up but cannot safely be taken back down.

**Data flow**: It receives a charged amount in micro-USD. If the amount is zero or negative, it does nothing; if it is positive, it emits a metric named balance_charged_micro_usd_total with that amount.

**Call relations**: Callers use this after credit has returned true and after their database transaction has committed. That order matters because credit may be retried, while emitted metrics cannot be rolled back like a database transaction.

*Call graph*: 1 external calls (emit_metric).


##### `debit`  (lines 392–412)

```
async def debit(connection: AsyncConnection, workspace_id: UUID, micro_usd: int) -> int
```

**Purpose**: This subtracts spent money from a workspace balance. It records what was actually taken, and it allows the balance to go negative so the ledger still reflects real usage that already happened.

**Data flow**: It receives a database connection, workspace ID, and amount to subtract. If the amount is zero, it returns zero; otherwise it updates the balance row by subtracting the amount and updating the timestamp. It returns the requested amount if a balance row existed, or zero if there was no row to debit.

**Call relations**: Usage-recording code calls this in the same transaction as the ledger entry for spent work. Its return value tells that caller whether any prepaid balance was actually reduced, which matters for self-hosted or uncredited workspaces.

*Call graph*: 2 external calls (execute, update).


##### `set_reserve`  (lines 415–426)

```
async def set_reserve(connection: AsyncConnection, workspace_id: UUID, reserve_micro_usd: int) -> bool
```

**Purpose**: This sets the minimum balance cushion a workspace must keep before starting new work. The reserve helps avoid admitting work that can begin but almost immediately run out of credit.

**Data flow**: It receives a database connection, workspace ID, and reserve amount. It updates the existing workspace balance row with the new reserve and timestamp, then returns true if exactly one row was changed and false if the workspace has no balance row.

**Call relations**: Billing settings or operator code calls this to change the admission cushion. Later, read_headroom reads the reserve and gives it to the gate that decides whether paid work may start.

*Call graph*: 2 external calls (execute, update).


### `core/src/ufo/harness/models/pricing.py`

`domain_logic` · `billing/accounting`

This file is the billing price sheet for model usage. Different kinds of tokens can cost different amounts: input tokens, output tokens, cached tokens that are read, and cached tokens that are written for different lifetimes. The file stores those rates in tiny units called micro-USD, meaning millionths of a US dollar, per million tokens.

The main idea is simple: take a model name, look up its price row, multiply each kind of token by the matching rate, add the results, and divide by one million to get the final micro-dollar charge. If the model is unknown, the code logs a warning and returns zero instead of crashing. That matters for old historical records: billing can continue even if a model name is no longer in the current price table.

The file also creates a digest, which is like a fingerprint for the whole price table. It sorts the model prices, turns them into a compact JSON string, and hashes that text with SHA-256. This gives each price table a repeatable label. If any rate changes, the fingerprint changes too. The Pricing object bundles the table and its fingerprint together, so accounting code can both calculate charges and record which price version was used.

#### Function details

##### `price_digest`  (lines 26–43)

```
def price_digest(prices: Mapping[str, ModelPrice]) -> str
```

**Purpose**: Creates a stable fingerprint for a model price table. This lets billed usage records later prove which exact set of prices was used.

**Data flow**: It receives a mapping from model names to their price rows. It sorts the models, converts each rate into a compact JSON form, hashes that text with SHA-256, and returns a string beginning with "sha256:" followed by the hash.

**Call relations**: When a Pricing object is built, pricing_from calls this function first to label the table. Inside, it relies on JSON formatting and SHA-256 hashing from the standard library to make the label deterministic.

*Call graph*: called by 1 (pricing_from); 2 external calls (sha256, dumps).


##### `usage_priced_micro_usd`  (lines 46–60)

```
def usage_priced_micro_usd(model: str, usage: Usage, prices: Mapping[str, ModelPrice]) -> int
```

**Purpose**: Calculates the cost of one piece of model usage in micro-USD. It is the core arithmetic that turns token counts into a billable amount.

**Data flow**: It receives a model name, a Usage record containing token counts, and a table of model prices. It looks up the model's rates, multiplies each token category by its matching rate, adds the pieces together, divides by one million tokens, and returns the final integer charge in micro-USD. If the model is not found, it writes a log message and returns 0.

**Call relations**: Pricing.micro_usd calls this function whenever accounting code asks a Pricing object to price usage. If the model name is missing from the table, this function hands the problem to the logging system so the event is visible without stopping billing.

*Call graph*: called by 1 (micro_usd); 1 external calls (log).


##### `Pricing.micro_usd`  (lines 70–71)

```
def micro_usd(self, model: str, usage: Usage) -> int
```

**Purpose**: Provides the simple public method for pricing a Usage record with this Pricing object's table. Callers do not need to pass the price table separately.

**Data flow**: It receives a model name and a Usage record. It uses the prices stored inside the Pricing object, passes everything to usage_priced_micro_usd, and returns the calculated micro-USD charge.

**Call relations**: Accounting code calls this method when recording sandbox token use, turn-level usage, or workspace usage. This method is the small bridge between those accounting flows and the lower-level pricing arithmetic.

*Call graph*: calls 1 internal fn (usage_priced_micro_usd); called by 3 (record_sandbox_tokens, record_turn_usage, record_workspace_usage).


##### `pricing_from`  (lines 74–77)

```
def pricing_from(prices: Mapping[str, ModelPrice]) -> Pricing
```

**Purpose**: Builds a complete Pricing object from a plain model-to-price mapping. It freezes together the price table and the digest that identifies that table.

**Data flow**: It receives a mapping of model prices, copies it into a normal dictionary, computes the table's digest with price_digest, and returns a new Pricing object containing both the copied table and the digest.

**Call relations**: This is the construction helper for the file. It calls price_digest to create the version stamp, then creates the Pricing object that later accounting code can use through Pricing.micro_usd.

*Call graph*: calls 1 internal fn (price_digest); 1 external calls (__init__).


### Feature flag controls
Feature flag access centralizes startup wiring and workspace-scoped feature checks.

### `core/src/ufo/flags.py`

`util` · `startup and cross-cutting runtime feature checks`

Feature flags are switches that let a deploy turn features on for some users or workspaces without changing the code. This file is the project’s single doorway to those switches. That matters because the rest of the system should not need to know which flag service is being used, or whether there is one at all.

At startup, `init_flags` can attach a real OpenFeature provider. OpenFeature is a common interface for feature-flag systems, like a universal power adapter for different flag backends. If no provider is given, the built-in no-op provider stays in place, so every flag simply returns the default value written in the code.

Later, code calls `flag_enabled` to ask, “Is this feature on?” The question is asked for the current workspace, so a backend can enable a feature for one workspace but not another. The function is deliberately cautious: it waits at most two seconds, and if the provider errors, is missing, or is too slow, it returns the caller’s default. In other words, a broken flag service should not crash a user flow or leave it hanging. Like a locked door that stays locked when the badge reader fails, features are withheld unless the flag check gives a clear answer.

#### Function details

##### `init_flags`  (lines 28–33)

```
def init_flags(provider: FeatureProvider | None) -> None
```

**Purpose**: This function installs the feature-flag provider chosen by the deploy. If no provider is supplied, it intentionally does nothing, leaving OpenFeature’s default no-op behavior in place.

**Data flow**: It receives either a provider object or `None`. If it gets `None`, nothing changes and future flag checks fall back to their code defaults. If it gets a provider, it gives that provider to the process-wide OpenFeature API, so later flag lookups use that backend.

**Call relations**: This is meant to be called during application startup, after configuration or extensions have selected a flag backend. Its only handoff is to `openfeature.api.set_provider`, which stores the provider globally for later calls to `flag_enabled`.

*Call graph*: 1 external calls (set_provider).


##### `flag_enabled`  (lines 36–50)

```
async def flag_enabled(flag: str, *, default: bool) -> bool
```

**Purpose**: This function answers whether a named feature flag is on for the current workspace. It protects the rest of the system from slow or broken flag lookups by returning the caller’s default when the flag cannot be resolved quickly and safely.

**Data flow**: It takes a flag name and a required default boolean value. It reads the current workspace ID, builds an evaluation context from it, asks the OpenFeature client for the flag’s boolean value, and gives that answer back. If the lookup times out or raises any error, it logs a warning with the flag name, default, and error details, then returns the default instead.

**Call relations**: Runtime code calls this whenever it needs to decide whether to offer a feature. The function gathers the current workspace through `ufo.runtime.workspace.ws_current`, creates an OpenFeature `EvaluationContext` so the backend knows who the question is about, asks `openfeature.api.get_client` for the flag value, limits the wait with `asyncio.timeout`, and reports failures through `ufo.harness.o11y.warn` before falling back safely.

*Call graph*: 5 external calls (timeout, get_client, EvaluationContext, warn, ws_current).


### Observability and operator inspection
Operational tooling provides tracing, metrics, safe logging, service checks, and trusted read-only debugging views.

### `core/src/ufo/harness/o11y.py`

`io_transport` · `startup and cross-cutting runtime observability`

This file makes the system visible while it runs. In production, operators need to know what happened, how long it took, what failed, and whether a background service is healthy. Without this file, failures would be harder to connect across queues and background work, metrics could grow out of control, and logs might accidentally publish private prompt or credential data.

It uses OpenTelemetry, a standard way to collect traces, metrics, and logs. A trace is like a timeline for one piece of work; spans are the named steps on that timeline. Metrics are counters and timings that dashboards can graph. Logs are event records with searchable fields.

At startup, `init_o11y` installs exporters that send traces, metrics, and logs to an OTLP collector, if one is configured. The file also installs a log-message guard even when exporting is disabled, because third-party libraries can write huge messages directly to stderr.

During normal work, callers use helpers such as `span`, `turn_span`, `log`, `warn`, `log_error`, and the metric emitters. These helpers add the current workspace automatically, redact sensitive fields, and keep metric dimensions bounded so dashboards do not explode into thousands of accidental time series. Service checks are sent directly to Datadog because OpenTelemetry does not provide that type of health signal.

#### Function details

##### `init_service_checks`  (lines 284–300)

```
def init_service_checks(url: str | None, env: str | None, api_key: str | None) -> None
```

**Purpose**: Configures where Datadog service checks should be sent. It deliberately fails early if a service-check URL is configured without the environment tag or API key, because that would make health alerts unreliable.

**Data flow**: It receives an optional intake URL, environment name, and API key. If there is no URL, it clears the service-check destination so later submissions do nothing. If there is a URL, it validates the required pieces and stores a small intake configuration for later use.

**Call relations**: This is the setup step for `emit_service_check`. Later, when a service check is emitted, that function reads the stored intake information created here.

*Call graph*: 1 external calls (__init__).


##### `init_o11y`  (lines 303–329)

```
def init_o11y(otlp_endpoint: str | None) -> None
```

**Purpose**: Sets up the OpenTelemetry pipeline for traces, metrics, and logs. If no collector endpoint is provided, it still installs the log-size guard so unsafe library messages are blocked locally.

**Data flow**: It receives an optional OTLP collector endpoint. It first installs the log guard. If an endpoint exists, it builds separate trace, metric, and log URLs, creates OpenTelemetry providers and exporters, registers them globally, and connects warning-level standard Python logs into the OpenTelemetry log stream.

**Call relations**: This is the main startup entry for this file’s observability plumbing. It calls `_guard_log_messages` first, uses `_otlp_signal_urls` to build export destinations, and finishes by calling `_bridge_warning_logs` so warnings from other modules can reach the collector.

*Call graph*: calls 3 internal fn (_bridge_warning_logs, _guard_log_messages, _otlp_signal_urls); 13 external calls (set_logger_provider, OTLPLogExporter, OTLPMetricExporter, OTLPSpanExporter, set_meter_provider, LoggerProvider, BatchLogRecordProcessor, MeterProvider, PeriodicExportingMetricReader, create (+3 more)).


##### `_bridge_warning_logs`  (lines 332–344)

```
def _bridge_warning_logs(logger_provider: LoggerProvider) -> None
```

**Purpose**: Forwards warning and error messages from ordinary Python logging into the OpenTelemetry log pipeline. This helps operator-visible warnings from libraries or other modules avoid disappearing into local-only logging.

**Data flow**: It receives an OpenTelemetry log provider. It creates a standard logging handler that exports warning-or-higher records, filters out this project’s own structured logs and OpenTelemetry’s own internal logs, and attaches the handler to the root logger.

**Call relations**: `init_o11y` calls this after creating the log provider. From then on, warning-level standard library log records can be exported alongside structured application logs.

*Call graph*: called by 1 (init_o11y); 2 external calls (getLogger, LoggingHandler).


##### `_GuardedRecordFactory.__call__`  (lines 374–386)

```
def __call__(self, *args: object, **kwargs: object) -> logging.LogRecord
```

**Purpose**: Inspects every standard Python log record before any handler writes it. If the final message is too large, it replaces the text with a short notice that says which logger produced it and how many characters were dropped.

**Data flow**: It receives the raw arguments used to create a log record. It asks the wrapped original factory to make the record, renders the message safely, and either returns it unchanged or overwrites the message and arguments with a safe summary.

**Call relations**: _guard_log_messages installs this object as the process-wide log record factory. It relies on `_rendered_message` to decide whether a record is safe to keep.

*Call graph*: calls 1 internal fn (_rendered_message).


##### `_guard_log_messages`  (lines 389–393)

```
def _guard_log_messages() -> None
```

**Purpose**: Installs the process-wide guard that prevents oversized standard Python log messages from being written. It is careful not to wrap the logging system more than once.

**Data flow**: It reads the current log record factory. If that factory is already guarded, it leaves it alone. Otherwise, it wraps the existing factory in `_GuardedRecordFactory` and installs the wrapper globally.

**Call relations**: `init_o11y` calls this during startup before any exporter setup. Once installed, `_GuardedRecordFactory.__call__` runs for future standard logging records.

*Call graph*: called by 1 (init_o11y); 3 external calls (__init__, getLogRecordFactory, setLogRecordFactory).


##### `_rendered_message`  (lines 396–406)

```
def _rendered_message(record: logging.LogRecord) -> str | None
```

**Purpose**: Safely computes what a log record would actually print. It avoids crashing the caller if a malformed logging template cannot be interpolated.

**Data flow**: It receives a Python log record. If the message is already a plain string with no arguments, it returns it directly. Otherwise, it asks the logging record to render itself, returning `None` if that rendering raises an error.

**Call relations**: _GuardedRecordFactory.__call__ uses this helper before deciding whether a log message is too large to allow through.

*Call graph*: called by 1 (__call__); 1 external calls (getMessage).


##### `_otlp_signal_urls`  (lines 409–415)

```
def _otlp_signal_urls(otlp_endpoint: str) -> tuple[str, str, str]
```

**Purpose**: Builds the exact HTTP endpoints used to export traces, metrics, and logs to an OTLP collector. This matters because the exporter expects a complete URL, not just a base address.

**Data flow**: It receives a base collector endpoint, removes any trailing slash, and returns three URLs with the standard trace, metric, and log paths appended.

**Call relations**: `init_o11y` calls this during startup so each OpenTelemetry exporter is pointed at the correct signal-specific endpoint.

*Call graph*: called by 1 (init_o11y).


##### `_ambient_scope`  (lines 418–423)

```
def _ambient_scope() -> dict[str, str]
```

**Purpose**: Finds the current workspace and turns it into metadata for logs and traces. This lets code inside a workspace automatically carry that workspace ID without every caller passing it by hand.

**Data flow**: It reads the current workspace from a context variable. If there is no workspace, it returns an empty dictionary. If there is one, it returns a dictionary containing the workspace ID as text.

**Call relations**: `turn_span`, `span`, and `_emit_log` call this when adding shared context to traces and logs.

*Call graph*: called by 3 (_emit_log, span, turn_span); 1 external calls (get).


##### `current_traceparent`  (lines 426–432)

```
def current_traceparent() -> str | None
```

**Purpose**: Captures the currently active trace as a W3C `traceparent` header, which is a standard text form for trace identity. This can be stored and later used to reconnect work that crosses a queue or process boundary.

**Data flow**: It creates an empty carrier dictionary and asks the trace context propagator to inject the current trace into it. It returns the `traceparent` value if one was produced, otherwise `None`.

**Call relations**: This is used by code that needs to preserve trace continuity outside this file. The matching reconnection happens in `turn_span`, which can extract a parent trace from such a header.


##### `turn_profile`  (lines 435–443)

```
def turn_profile(subagent_profile: str | None, spawned: bool=False) -> str
```

**Purpose**: Chooses the small, stable profile label used for a turn. This separates main user-facing work, spawned agent work, and named subagent profiles without creating unbounded metric labels.

**Data flow**: It receives an optional subagent profile and a flag saying whether the turn was spawned. It returns the subagent profile if present, otherwise `agent` for spawned work or `main` for normal member-facing work.

**Call relations**: `turn_span` calls this when tagging a turn trace. Other code can also use it to keep metric labels aligned with trace labels.

*Call graph*: called by 1 (turn_span).


##### `turn_span`  (lines 447–483)

```
def turn_span(turn_id: UUID, conversation_id: UUID, traceparent: str | None, subagent_profile: str | None, parent_turn_id: UUID | None) -> Iterator[Span]
```

**Purpose**: Opens the main trace span for one durable turn. A span is a timed section on a trace, and this one represents the whole turn from the worker’s point of view.

**Data flow**: It receives turn and conversation IDs, an optional saved traceparent, an optional subagent profile, and an optional parent turn ID. It builds redacted trace attributes, adds workspace context, extracts the parent trace if available, starts a server-style span named `turn`, yields it to the caller, and closes it when the caller’s block ends.

**Call relations**: This is used around turn execution. It calls `_ambient_scope`, `turn_profile`, and `redact_payload` before handing control to OpenTelemetry’s tracer.

*Call graph*: calls 3 internal fn (_ambient_scope, redact_payload, turn_profile); 2 external calls (get_tracer, cast).


##### `span`  (lines 487–499)

```
def span(name: str, kind: SpanKind=SpanKind.INTERNAL, **attributes: object) -> Iterator[Span]
```

**Purpose**: Opens a smaller trace span inside whatever trace is already active. Callers use it to time and label stages such as model calls, tool calls, or sandbox operations.

**Data flow**: It receives a span name, a span kind, and arbitrary attributes. It adds workspace context, redacts sensitive values, converts attributes into OpenTelemetry-friendly values, starts the span, yields it to the caller, and closes it when the block exits.

**Call relations**: This is a general tracing helper used by runtime code. It shares the same redaction and workspace behavior as `turn_span` through `_ambient_scope` and `redact_payload`.

*Call graph*: calls 2 internal fn (_ambient_scope, redact_payload); 1 external calls (get_tracer).


##### `redact_payload`  (lines 502–508)

```
def redact_payload(fields: Mapping[str, object]) -> dict[str, JsonValue]
```

**Purpose**: Removes fields whose names look sensitive and redacts nested values. This is the main safety gate before data becomes log or trace metadata.

**Data flow**: It receives a mapping of field names to values. For each field, it normalizes the key by removing underscores and hyphens and lowercasing it; sensitive keys are dropped. The remaining values are passed through `redact_value`, and a JSON-like dictionary comes out.

**Call relations**: `turn_span`, `span`, and `_emit_log` call this before exporting data. `redact_value` also calls it recursively for nested dictionaries.

*Call graph*: calls 1 internal fn (redact_value); called by 4 (_emit_log, redact_value, span, turn_span).


##### `redact_value`  (lines 511–521)

```
def redact_value(value: object) -> JsonValue
```

**Purpose**: Converts one value into a safe JSON-like form. It preserves ordinary simple values, walks through lists and dictionaries, and turns unfamiliar objects into strings.

**Data flow**: It receives any Python object. Plain values such as strings, numbers, booleans, and `None` pass through. Dictionaries are cleaned with `redact_payload`, sequences are cleaned item by item, and everything else is stringified.

**Call relations**: `redact_payload` calls this for every non-sensitive field. For nested dictionaries, this function calls `redact_payload` again, making the two helpers work together recursively.

*Call graph*: calls 1 internal fn (redact_payload); called by 1 (redact_payload).


##### `log`  (lines 524–530)

```
def log(event: str, **fields: object) -> None
```

**Purpose**: Writes a structured information-level event. Callers use it for normal noteworthy events that should be searchable and tied to the current trace.

**Data flow**: It receives an event name and keyword fields. It passes them to `_emit_log` with information severity so the fields can be redacted, scoped to the workspace, and sent through both standard logging and OpenTelemetry logs.

**Call relations**: This is the public convenience function for ordinary structured logs. `_emit_log` does the shared work.

*Call graph*: calls 1 internal fn (_emit_log).


##### `log_error`  (lines 533–535)

```
def log_error(event: str, **fields: object) -> None
```

**Purpose**: Writes a structured error-level event. Callers use it when something failed and operators should be able to search for the failure with safe metadata.

**Data flow**: It receives an event name and keyword fields. It passes them to `_emit_log` with error severity, which adds workspace context, removes sensitive data, and emits the record.

**Call relations**: This is the public convenience function for error logs. It follows the same path as `log` and `warn`, with a different severity.

*Call graph*: calls 1 internal fn (_emit_log).


##### `warn`  (lines 538–540)

```
def warn(event: str, **fields: object) -> None
```

**Purpose**: Writes a structured warning-level event. It is for expected but important conditions that are not full errors but may deserve operator attention.

**Data flow**: It receives an event name and keyword fields. It passes them to `_emit_log` with warning severity so the event is exported consistently with other structured logs.

**Call relations**: This is the public convenience function for warning logs. `_emit_log` supplies the common redaction, workspace tagging, and export behavior.

*Call graph*: calls 1 internal fn (_emit_log).


##### `formatted_stack`  (lines 543–572)

```
def formatted_stack(error: BaseException) -> str
```

**Purpose**: Formats an exception’s stack trace for logging without including exception messages. This avoids leaking text that may have come from an untrusted command, provider, or environment.

**Data flow**: It receives an exception. It walks through the exception, its cause, and its context while avoiding loops, records each exception class and traceback frames, and trims the middle if the result is too long. It returns a bounded string.

**Call relations**: Other code can use this when adding stack information to a structured log field. It relies on Python’s traceback formatter but deliberately avoids message text.

*Call graph*: 1 external calls (format_tb).


##### `_emit_log`  (lines 575–593)

```
def _emit_log(event: str, severity_number: SeverityNumber, severity_text: str, level: int, fields: Mapping[str, object]) -> None
```

**Purpose**: Performs the shared work behind `log`, `warn`, and `log_error`. It creates one safe structured log record and sends it through both standard Python logging and OpenTelemetry logging.

**Data flow**: It receives the event name, severity values, Python logging level, and fields. It adds ambient workspace context, redacts sensitive fields, removes fields whose value is `None`, writes to the project logger, and emits an OpenTelemetry log record with the same attributes.

**Call relations**: `log`, `warn`, and `log_error` all call this helper. It calls `_ambient_scope` and `redact_payload` before handing the event to the logging systems.

*Call graph*: calls 2 internal fn (_ambient_scope, redact_payload); called by 3 (log, log_error, warn); 2 external calls (getLogger, get_logger).


##### `_bounded_error_class`  (lines 596–609)

```
def _bounded_error_class(dimensions: dict[str, str]) -> dict[str, str]
```

**Purpose**: Keeps the `error_class` metric label within a known list. This prevents one unusual exception class from permanently creating a new dashboard time series.

**Data flow**: It receives a dictionary of metric dimensions. If the `error_class` value is known, it returns the dimensions unchanged. If it is unknown, it returns a copy where that value is replaced with `other`.

**Call relations**: `emit_metric` and `emit_histogram` call this right before recording data, so both counter and timing metrics get the same protection.

*Call graph*: called by 2 (emit_histogram, emit_metric).


##### `emit_metric`  (lines 612–622)

```
def emit_metric(name: str, amount: int=1, /, **dimensions: str) -> None
```

**Purpose**: Increments a named counter metric, such as a total number of turns, retries, failures, or tool calls. It fails loudly for unknown metric names so typos do not silently create bad telemetry.

**Data flow**: It receives a metric name, an optional amount, and string dimensions. It checks the name against the registered counter list, creates and caches the OpenTelemetry counter if needed, bounds the error class dimension, and adds the amount.

**Call relations**: Runtime code calls this whenever a counted event happens. It uses `_bounded_error_class` before sending the count through OpenTelemetry’s metric meter.

*Call graph*: calls 1 internal fn (_bounded_error_class); 1 external calls (get_meter).


##### `emit_histogram`  (lines 625–647)

```
def emit_histogram(name: str, value: int, /, **dimensions: str) -> None
```

**Purpose**: Records one timing or size observation for a registered histogram metric. Histograms let dashboards calculate distributions such as percentiles, not just totals.

**Data flow**: It receives a histogram name, a numeric value, and string dimensions. It verifies the histogram exists and that all dimensions were declared for that histogram, creates and caches the OpenTelemetry histogram if needed, bounds the error class dimension, and records the value in milliseconds.

**Call relations**: Runtime code calls this for measured durations such as model rounds, tool calls, database waits, and turn time. It uses `_bounded_error_class` before recording through OpenTelemetry.

*Call graph*: calls 1 internal fn (_bounded_error_class); 1 external calls (get_meter).


##### `emit_up_down_metric`  (lines 650–662)

```
def emit_up_down_metric(name: str, amount: int, /, **dimensions: str) -> None
```

**Purpose**: Adds or subtracts from a current-state metric. This is useful for values that go up and down, such as the number of active model rounds.

**Data flow**: It receives a metric name, signed amount, and string dimensions. It checks that the name and dimensions are allowed, creates and caches the OpenTelemetry up-down counter if needed, and applies the amount.

**Call relations**: Runtime code calls this when entering or leaving work that should be counted as currently active. Unlike `emit_metric` and `emit_histogram`, it does not route through `_bounded_error_class` because its declared dimensions do not include that field.

*Call graph*: 1 external calls (get_meter).


##### `emit_service_check`  (lines 665–697)

```
async def emit_service_check(name: str, status: int, message: str='', /, **tags: str) -> None
```

**Purpose**: Sends a Datadog service check, which represents the latest health state of a named service-like thing. This is used when an alert needs to clear after a later OK status, something a simple counter cannot express.

**Data flow**: It receives a registered check name, status, optional message, and tags. It validates the check name, returns immediately if service checks were not configured, builds a Datadog report with a stable host name and environment tag, posts it to Datadog using an async HTTP client, and raises if Datadog rejects the request.

**Call relations**: This depends on `init_service_checks` having stored an intake URL, API key, and environment. It sends directly with `httpx.AsyncClient` because the OpenTelemetry collector path used elsewhere in this file does not support service checks.

*Call graph*: 1 external calls (AsyncClient).


### `extensions/debugger/ufo_ext_debugger/surface.py`

`io_transport` · `request handling`

This file is the bridge between the debugger web app in the browser and the system’s stored session data. Think of it as a read-only control room: it serves the built React page, then answers the page’s requests for lists of workspaces, conversations, transcripts, turn details, files, and live event streams.

The important safety idea is that the request already arrives with an operator-scoped SurfaceContext. That context is the “badge” that decides which workspace the request may read from. Most functions here do not make their own authorization decisions; they ask the context for data, so reads stay tied to the chosen workspace. The fleet endpoint is the exception in scope, but it still sits behind the same operator gate.

For normal data, the file returns JSON. For workspace file contents, it streams bytes back. For a running turn, it uses SSE, or Server-Sent Events: a simple browser-friendly stream where the server keeps sending named events over one HTTP response. The helper functions turn internal live frames into these SSE messages, including an event id so a browser can reconnect and continue from where it left off.

Without this file, the debugger front end would have no usable backend: the page could load only if prebuilt elsewhere, and operators could not inspect conversations or follow live turns through this surface.

#### Function details

##### `app_page`  (lines 56–61)

```
async def app_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the debugger’s main web page to the browser. If the front-end app has not been built yet, it fails clearly instead of returning a broken empty page.

**Data flow**: It receives the request and the already-prepared surface context, checks whether the compiled HTML was loaded from disk when the module started, and returns that HTML as the response. If the HTML is missing, it raises an error telling the developer to build the debugger frontend.

**Call relations**: The surface router calls this when someone opens the debugger root page with a GET request. It hands the browser the shell of the app; after that, the browser calls the JSON and stream endpoints in this same file to fill the page with data.

*Call graph*: 1 external calls (HTMLResponse).


##### `fleet`  (lines 64–68)

```
async def fleet(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the fleet-wide landing data: available workspaces and recent threads. This powers the debugger’s top-level index view for operators.

**Data flow**: It ignores the single-workspace view for this particular read, asks FleetDirectory for the deploy’s fleet directory, converts that structured result into JSON-friendly data, and sends it back as a JSON response.

**Call relations**: The surface router calls this for the fleet API endpoint. It relies on the broader operator access check that happened before the handler runs, then hands the browser a fleet overview for navigation.

*Call graph*: 2 external calls (__init__, JSONResponse).


##### `workspace_meta`  (lines 71–84)

```
async def workspace_meta(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns small pieces of information about the currently selected workspace. The debugger uses this to show which workspace is being inspected and, when available, which Slack team and Datadog site relate to it.

**Data flow**: It reads the Slack installation value from the context, strips the internal Slack team prefix if present, reads the Datadog site from the environment, and returns those values plus the workspace id as JSON.

**Call relations**: The browser calls this after selecting or entering a workspace. It asks SurfaceContext for installation metadata and combines it with an environment setting so the page can display useful workspace labels and external-tool hints.

*Call graph*: calls 1 internal fn (installation); 1 external calls (JSONResponse).


##### `conversations`  (lines 87–89)

```
async def conversations(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists the conversations visible inside the current workspace. This gives the debugger its conversation browser.

**Data flow**: It asks the surface context for conversation summaries, converts each summary into JSON-ready form, and returns the list to the browser.

**Call relations**: The surface router invokes this for the conversations API endpoint. It depends on SurfaceContext to apply the workspace scope, then gives the frontend the list it needs before a user drills into one conversation.

*Call graph*: calls 1 internal fn (list_conversations); 1 external calls (JSONResponse).


##### `conversation_turns`  (lines 92–97)

```
async def conversation_turns(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists the turns inside one conversation. A turn is one unit of back-and-forth work in a conversation, and this endpoint lets the debugger show that sequence.

**Data flow**: It reads the conversation id from the URL and checks that it is a valid UUID. If the id is bad, it returns a 404-style JSON error; otherwise it asks the context for that conversation’s turns and returns them as JSON.

**Call relations**: The router calls this when the frontend opens the turns list for a conversation. It uses _uuid_param to safely interpret the URL value before handing the id to SurfaceContext.

*Call graph*: calls 2 internal fn (list_turns, _uuid_param); 1 external calls (JSONResponse).


##### `conversation_transcript`  (lines 100–107)

```
async def conversation_transcript(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Fetches the transcript for one conversation. This lets an operator read the recorded messages in a conversation without changing anything.

**Data flow**: It pulls the conversation id from the URL, rejects it if it is not a valid UUID, then asks the context for the transcript. If no transcript exists, it returns a JSON error; if it exists, it returns the transcript as JSON.

**Call relations**: The frontend calls this when it needs the readable transcript view. The function uses _uuid_param for input safety and SurfaceContext for the actual workspace-scoped read.

*Call graph*: calls 2 internal fn (read_transcript, _uuid_param); 1 external calls (JSONResponse).


##### `conversation_compactions`  (lines 110–114)

```
async def conversation_compactions(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists compaction records for a conversation. A compaction is where older conversation history has been summarized to keep context smaller, so this helps operators see where summarization happened.

**Data flow**: It validates the conversation id from the URL, returns a not-found JSON error if the id is invalid, otherwise asks the context for the compaction indexes or records and returns them as a JSON list.

**Call relations**: The router calls this for the compactions list endpoint. It is usually followed by compaction_record when the debugger page wants the details for a particular compaction.

*Call graph*: calls 2 internal fn (list_compactions, _uuid_param); 1 external calls (JSONResponse).


##### `compaction_record`  (lines 117–132)

```
async def compaction_record(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns one detailed compaction record, including what messages existed before, what remained after, and the generated summary. This helps an operator understand exactly how history was shortened.

**Data flow**: It reads a conversation id and a compaction index from the URL. If either is invalid, it returns a not-found JSON error. Otherwise it asks the context for that record, and if found, returns the index, before messages, after messages, and summary as JSON.

**Call relations**: The frontend calls this after it knows a compaction exists and wants to inspect it. It uses _uuid_param for the conversation id, then delegates the read to SurfaceContext.

*Call graph*: calls 2 internal fn (read_compaction, _uuid_param); 1 external calls (JSONResponse).


##### `workspace_files`  (lines 135–140)

```
async def workspace_files(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists files associated with a conversation’s workspace area. This lets the debugger show attachments or generated files that belong to that conversation.

**Data flow**: It validates the conversation id from the URL, returns a not-found JSON error if invalid, then asks the context for the file list and returns each file entry as JSON.

**Call relations**: The router invokes this when the frontend opens the files panel for a conversation. It prepares the list that workspace_file can later use when the user chooses a specific file.

*Call graph*: calls 2 internal fn (list_workspace_files, _uuid_param); 1 external calls (JSONResponse).


##### `workspace_file`  (lines 143–153)

```
async def workspace_file(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Streams the contents of one workspace file back to the operator. It is used when the debugger user clicks or downloads a specific file.

**Data flow**: It validates the conversation id, reads the requested file path from the URL, and asks the context for a byte stream. If the id is invalid, the path is rejected, or the file is missing, it returns a JSON not-found error. If the file exists, it sends the bytes as an octet-stream response, which means generic binary data.

**Call relations**: The frontend calls this after listing files with workspace_files. The function relies on SurfaceContext to find and open the file safely, then uses StreamingResponse so large files do not need to be loaded into memory all at once.

*Call graph*: calls 2 internal fn (read_workspace_file, _uuid_param); 2 external calls (JSONResponse, StreamingResponse).


##### `turn`  (lines 156–163)

```
async def turn(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns detailed information about a single turn. This gives the debugger the main record for one unit of agent work.

**Data flow**: It reads and validates the turn id from the URL. If the id is invalid or no turn exists, it returns a not-found JSON error; otherwise it converts the turn detail to JSON and returns it.

**Call relations**: The router calls this when the frontend opens a specific turn. It uses _uuid_param before asking SurfaceContext for the turn detail.

*Call graph*: calls 2 internal fn (turn_detail, _uuid_param); 1 external calls (JSONResponse).


##### `turn_steps`  (lines 166–173)

```
async def turn_steps(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the individual steps within a turn. This lets an operator inspect the sequence of actions that made up the turn.

**Data flow**: It validates the turn id from the URL, asks the context for that turn’s steps, and returns a not-found JSON error if the id is invalid or the steps cannot be found. When found, it returns the steps as a JSON list.

**Call relations**: The frontend calls this after selecting a turn and wanting the step-by-step breakdown. It pairs with turn: one gives the overall detail, the other gives the internal sequence.

*Call graph*: calls 2 internal fn (turn_steps, _uuid_param); 1 external calls (JSONResponse).


##### `stream`  (lines 176–181)

```
async def stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Opens a live event stream for one turn. This lets the debugger watch new activity arrive as the turn runs, instead of repeatedly refreshing.

**Data flow**: It validates the turn id and checks that the turn exists. If not, it returns a not-found JSON error. If it exists, it reads the Last-Event-ID header, which tells where a dropped stream should resume, and returns a streaming Server-Sent Events response produced by _events.

**Call relations**: The browser calls this for the live turn view. After checking the turn through SurfaceContext, it hands off to _events to continually read live frames and format them for the browser.

*Call graph*: calls 3 internal fn (turn_detail, _events, _uuid_param); 2 external calls (JSONResponse, StreamingResponse).


##### `_events`  (lines 184–187)

```
async def _events(ctx: SurfaceContext, turn_id: UUID, since: str) -> AsyncIterator[bytes]
```

**Purpose**: Turns the context’s live tail of a turn into a stream of browser-ready event messages. It is the small adapter between internal live frames and the HTTP streaming response.

**Data flow**: It receives a context, a turn id, and an optional cursor saying where to resume. It opens the context’s tail stream, then for each incoming cursor-and-frame pair, converts the frame with _sse and yields the resulting bytes.

**Call relations**: stream calls this when it needs the body of a Server-Sent Events response. _events listens to SurfaceContext.tail and passes every live frame to _sse for formatting.

*Call graph*: calls 2 internal fn (tail, _sse); called by 1 (stream).


##### `_sse`  (lines 190–216)

```
def _sse(cursor: str, frame: LiveFrame) -> bytes
```

**Purpose**: Formats one live frame as one Server-Sent Events message. It gives each kind of frame a clear event name, such as text, reply, activity, or cost.

**Data flow**: It receives a cursor and a LiveFrame object. If there is a cursor, it writes it as the event id. It then checks what kind of frame it is, serializes the frame to JSON, and returns the exact bytes the browser expects for one SSE event. If it sees a frame kind this debugger does not know about, it raises an error instead of silently mislabeling it.

**Call relations**: _events calls this for every live frame coming from SurfaceContext.tail. Its output is yielded directly into the StreamingResponse opened by stream, so the browser receives these messages as the live debugger feed.

*Call graph*: called by 1 (_events); 1 external calls (model_dump_json).


##### `_uuid_param`  (lines 219–223)

```
def _uuid_param(request: Request, name: str) -> UUID | None
```

**Purpose**: Safely reads a UUID value from a route parameter. UUIDs are standard unique identifiers; this helper keeps bad URL values from causing confusing downstream reads.

**Data flow**: It takes a request and the name of a path parameter, tries to convert that parameter into a UUID object, and returns the UUID if conversion works. If the value is not a valid UUID string, it returns None.

**Call relations**: Many endpoint handlers call this before asking SurfaceContext for a conversation or turn. By centralizing the parsing rule, those handlers can all respond consistently with not-found errors when the URL id is malformed.

*Call graph*: called by 9 (compaction_record, conversation_compactions, conversation_transcript, conversation_turns, stream, turn, turn_steps, workspace_file, workspace_files); 1 external calls (UUID).


### External billing integrations
Metronome and Stripe integration exports usage, exposes billing status, creates portal links, and supports automatic prepaid refills.

### `extensions/metronome/ufo_ext_metronome.py`

`domain_logic` · `scheduled jobs, chat tool calls, and billing page requests`

This extension is the bridge between UFO’s internal accounting and outside billing tools. Metronome receives usage records so people can see and price what a workspace used. Stripe stores payment methods and charges cards when a workspace has automatic refills turned on. Without this file, settled usage would not be shipped to Metronome, admins would have no built-in way to open the billing portal, and prepaid balances could not be topped up from a saved card.

The file has three main jobs. First, the usage shipper wakes up on a schedule, reads settled usage exports from core, turns them into Metronome ingest events, confirms the workspace exists as a Metronome customer alias, sends the batch, and only then marks the exports as acknowledged. This order is important: if the process crashes, the same event can be resent without double-counting.

Second, the billing tool lets a workspace admin ask through chat for status, a Stripe portal link, or automatic refill settings. It checks that the speaker is an admin before doing anything.

Third, the top-up job checks workspaces that have refill rules. If the balance is low and a saved card exists, it creates a Stripe payment and credits the balance only after the payment succeeds. It slows down retries after missing cards or declined cards to avoid noisy and costly repeated attempts.

#### Function details

##### `StripeError.__init__`  (lines 186–188)

```
def __init__(self, message: str, status: int=0) -> None
```

**Purpose**: Creates an error object for a failed Stripe call and keeps the HTTP status code with it. The status is useful later because a declined card, an in-progress request, and a server problem need different reactions.

**Data flow**: It receives an error message and an optional status code. It stores the message in the normal exception machinery and saves the status on the object. The result is an exception that callers can inspect after catching it.

**Call relations**: The shared Stripe request helper raises this error when Stripe answers with a non-success response. Balance top-up code later uses the saved status to decide whether to wait, treat the card as declined, or raise the failure.

*Call graph*: called by 1 (_stripe).


##### `UsageShipper.run`  (lines 218–244)

```
async def run(self) -> None
```

**Purpose**: Sends one workspace’s settled usage records to Metronome in safe batches. It is designed so that a crash or retry does not lose usage or bill it twice.

**Data flow**: It first reads the Metronome token from the environment, then finds the workspace’s fixed backfill floor. It repeatedly asks core for pending usage exports, warns if they are getting too old, ensures Metronome has a customer alias for the workspace, converts exports into ingest events, sends them, logs success, and acknowledges the exports only after Metronome accepts them. It stops when there is no more work or the last batch is smaller than the batch limit.

**Call relations**: The scheduled shipping entry point creates a UsageShipper and calls this method. During its loop it relies on _floor for the cutoff date, _note_usage_aging_out for warnings, _ensure_metronome_customer before sending, _events to build the payload, and _ingest to make the actual Metronome call.

*Call graph*: calls 6 internal fn (_events, _floor, _note_usage_aging_out, _ensure_metronome_customer, _ingest, _require_env); 1 external calls (log).


##### `UsageShipper._floor`  (lines 246–256)

```
async def _floor(self) -> datetime
```

**Purpose**: Finds or creates the earliest date from which this workspace’s usage should be shipped. This prevents the first run from trying to backfill too far into the past.

**Data flow**: It reads a stored floor value from the extension store. If none exists, it writes a new timestamp equal to now minus the configured backfill window. It returns a datetime that the shipper uses when asking core for pending exports.

**Call relations**: UsageShipper.run calls this before touching the usage export queue. The returned floor becomes the boundary for what core is allowed to mint and return as pending usage.

*Call graph*: called by 1 (run); 3 external calls (fromisoformat, now, timedelta).


##### `UsageShipper._note_usage_aging_out`  (lines 258–279)

```
def _note_usage_aging_out(self, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: Warns operators when pending usage is older than Metronome’s backdating window. This does not fix the backlog, but it makes silent revenue loss visible.

**Data flow**: It receives a batch of usage exports, finds the oldest event time, compares it with the allowed backfill window, and either does nothing or writes a warning log with the workspace, oldest time, and batch size.

**Call relations**: UsageShipper.run calls this after reading each batch and before sending it. It uses _rfc3339 to format the timestamp for the warning.

*Call graph*: calls 1 internal fn (_rfc3339); called by 1 (run); 3 external calls (now, timedelta, warn).


##### `UsageShipper._events`  (lines 281–300)

```
def _events(self, exports: tuple[UsageExport, ...]) -> list[dict[str, object]]
```

**Purpose**: Turns UFO usage export records into the exact event objects Metronome expects. Each event includes a stable transaction ID so resending the same export is safe.

**Data flow**: It receives settled usage exports and reads the current workspace ID. For each export it builds a dictionary with the transaction ID, customer alias, timestamp, model, dimension, amount, price details, turn ID, and whether the usage was bring-your-own-key. It returns a list of event dictionaries ready for Metronome ingest.

**Call relations**: UsageShipper.run calls this immediately before _ingest. It uses _rfc3339 so event timestamps are formatted consistently.

*Call graph*: calls 1 internal fn (_rfc3339); called by 1 (run).


##### `_ship`  (lines 303–304)

```
async def _ship(ctx: ExtensionContext) -> None
```

**Purpose**: Acts as the scheduled job wrapper for usage shipping. It exists so the extension manifest can register a simple job handler.

**Data flow**: It receives an extension context for one workspace. It creates a UsageShipper using that context and the configured ingest transport, then awaits the shipper’s run. It returns nothing after the workspace’s pending usage has been drained or skipped by errors.

**Call relations**: The manifest registers this as the usage shipping job. Its only job is to hand control to UsageShipper.run.

*Call graph*: 1 external calls (__init__).


##### `BillingConfig.from_env`  (lines 322–339)

```
def from_env(cls) -> 'BillingConfig'
```

**Purpose**: Reads the required Stripe and Metronome settings from environment variables. It fails early if billing is only partly configured.

**Data flow**: It checks the environment for the Stripe secret key, Stripe portal configuration ID, and Metronome bearer token. If any are missing, it raises an error naming all missing variables. Otherwise it returns a frozen BillingConfig object containing the three values.

**Call relations**: Billing tool calls, billing status reads, portal creation, and balance top-up use this before talking to Stripe or Metronome billing-related APIs. Usage shipping reads its Metronome token separately so usage reporting can be configured independently.


##### `_billing_record`  (lines 352–354)

```
async def _billing_record(ctx: ExtensionContext) -> BillingRecord | None
```

**Purpose**: Reads the saved Stripe customer ID for a workspace, if one has been created. This is the local pointer that connects a UFO workspace to its Stripe customer.

**Data flow**: It asks the extension store for the billing record. If nothing is stored, it returns None. If data is present, it validates it as a BillingRecord and returns the record with the Stripe customer ID.

**Call relations**: Billing status, portal creation, autopay setup, top-up, and the billing page all call this when they need to know whether the workspace already has a Stripe customer.

*Call graph*: called by 5 (run, _billing_autopay, _billing_portal, _billing_projection, _billing_status).


##### `manage_billing`  (lines 377–386)

```
async def manage_billing(ctx: ToolContext, args: ManageBillingInput) -> ToolResult
```

**Purpose**: Implements the chat-facing billing action for workspace admins. It routes an admin’s request to status, portal, or autopay behavior.

**Data flow**: It receives a tool context and parsed billing arguments. It verifies admin access, loads billing configuration from the environment, chooses the requested operation, and returns a ToolResult from the matching helper.

**Call relations**: The tool definition registered in the manifest points to this function. It first calls _admin_billing, then hands off to _billing_status, _billing_portal, or _billing_autopay depending on the requested operation.

*Call graph*: calls 4 internal fn (_admin_billing, _billing_autopay, _billing_portal, _billing_status).


##### `_billing_autopay`  (lines 389–420)

```
async def _billing_autopay(ext: ExtensionContext, config: BillingConfig, args: ManageBillingInput) -> ToolResult
```

**Purpose**: Sets or stops automatic prepaid balance refills for a workspace. It makes sure a card is already saved before promising future unattended charges.

**Data flow**: It receives the workspace context, billing config, and requested refill numbers. If only one of the two required numbers is provided, it raises an error. If both are omitted, it clears the refill rule. If both are present, it checks for a billing record and a default Stripe payment method, converts dollars into micro-dollars, stores the refill rule in core, clears prior refusal state, bumps an attempt marker, logs the change, and returns the new settings as JSON text.

**Call relations**: manage_billing calls this for the autopay operation. It uses _billing_record and _default_payment_method to confirm the card, set_auto_topup inside a transaction to write the rule, and _text_result to return a tool-friendly answer.

*Call graph*: calls 4 internal fn (transaction, _billing_record, _default_payment_method, _text_result); called by 1 (manage_billing); 2 external calls (set_auto_topup, log).


##### `_admin_billing`  (lines 423–429)

```
async def _admin_billing(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Checks that the billing tool is being used by a real workspace admin. This prevents ordinary members or anonymous requests from changing billing.

**Data flow**: It receives a tool context. It rejects the call if there is no speaking member or if the speaker is not an admin. If checks pass, it returns the extension context attached to the tool call.

**Call relations**: manage_billing calls this before loading billing configuration or performing any billing operation. It relies on the ToolContext admin check supplied by core.

*Call graph*: calls 1 internal fn (speaker_is_admin); called by 1 (manage_billing).


##### `_billing_status`  (lines 432–456)

```
async def _billing_status(ext: ExtensionContext, config: BillingConfig) -> ToolResult
```

**Purpose**: Reports the workspace’s balance and whether Stripe currently has a default payment method. It answers what is true now rather than trusting a cached local card flag.

**Data flow**: It reads the workspace balance from core inside a transaction. It then reads the local billing record and, if present, asks Stripe whether the customer has a default payment method. It returns JSON text containing card presence and balance figures, using null values if the workspace has no balance record.

**Call relations**: manage_billing calls this for the status operation. It uses _billing_record and _default_payment_method for the Stripe side, read_balance for the core balance, and _text_result for the tool response.

*Call graph*: calls 4 internal fn (transaction, _billing_record, _default_payment_method, _text_result); called by 1 (manage_billing); 1 external calls (read_balance).


##### `_billing_portal`  (lines 459–481)

```
async def _billing_portal(ext: ExtensionContext, config: BillingConfig) -> ToolResult
```

**Purpose**: Creates a short-lived Stripe Customer Portal link for a workspace admin. The portal is where admins save payment methods and view billing details.

**Data flow**: It reads the workspace ID and any saved billing record. If no Stripe customer is recorded, it creates one through Stripe and stores the resulting customer ID. It then asks Stripe for a portal session URL with a return link back to UFO’s billing screen, logs the event, and returns the URL plus customer ID as JSON text.

**Call relations**: manage_billing calls this for the portal operation. It may call _stripe_customer to create the Stripe customer, then _portal_session to make the link, and _text_result to format the tool result.

*Call graph*: calls 5 internal fn (home_url, _billing_record, _portal_session, _stripe_customer, _text_result); called by 1 (manage_billing); 2 external calls (__init__, log).


##### `_text_result`  (lines 484–485)

```
def _text_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: Wraps a small JSON payload as a tool result that the chat/tool system can return. It is a formatting helper for billing actions.

**Data flow**: It receives a dictionary, converts it to a JSON string, places that string into a TextContent object, and returns a ToolResult containing that text.

**Call relations**: _billing_status, _billing_portal, and _billing_autopay all use this so their structured answers are returned in the same simple text form.

*Call graph*: called by 3 (_billing_autopay, _billing_portal, _billing_status); 3 external calls (__init__, __init__, dumps).


##### `_require_env`  (lines 499–503)

```
def _require_env(name: str) -> str
```

**Purpose**: Reads one required environment variable and raises a clear error if it is missing. It is used for settings that must exist before work can safely begin.

**Data flow**: It receives an environment variable name, looks up its value, and returns the non-empty string. If the variable is absent or empty, it raises a RuntimeError naming the missing setting.

**Call relations**: UsageShipper.run calls this before reading pending usage exports, so an unconfigured Metronome token does not cause the system to mint export intents that cannot be shipped.

*Call graph*: called by 1 (run).


##### `_stripe_customer`  (lines 506–523)

```
async def _stripe_customer(config: BillingConfig, workspace_id: UUID, transport: httpx.AsyncBaseTransport | None) -> str
```

**Purpose**: Creates or reuses the Stripe Customer for a workspace. It uses a stable idempotency key, meaning a repeated create request settles on the same customer instead of making duplicates.

**Data flow**: It receives billing config, a workspace UUID, and an optional HTTP transport. It sends Stripe a customer creation request with a description and workspace metadata. It extracts and returns the customer ID from Stripe’s response, or raises if the response does not contain one.

**Call relations**: _billing_portal calls this when a workspace asks for the billing portal before any Stripe customer has been recorded. It uses _stripe for the HTTP call and _as_str to validate the returned ID.

*Call graph*: calls 2 internal fn (_as_str, _stripe); called by 1 (_billing_portal).


##### `_portal_session`  (lines 526–551)

```
async def _portal_session(config: BillingConfig, customer_id: str, flow: str | None, transport: httpx.AsyncBaseTransport | None, return_url: str | None=None) -> str
```

**Purpose**: Creates a Stripe Customer Portal session URL. This is the temporary link an admin opens to manage payment methods, invoices, and billing details.

**Data flow**: It receives billing config, a Stripe customer ID, an optional portal flow type, an optional transport, and an optional return URL. It builds the Stripe request data, sends it, extracts the session URL, and returns that URL as a string.

**Call relations**: _billing_portal calls this after it has a Stripe customer. It uses _stripe for the provider request and _as_str to make sure Stripe returned a usable URL.

*Call graph*: calls 2 internal fn (_as_str, _stripe); called by 1 (_billing_portal).


##### `_default_payment_method`  (lines 554–566)

```
async def _default_payment_method(config: BillingConfig, customer_id: str, transport: httpx.AsyncBaseTransport | None) -> str | None
```

**Purpose**: Asks Stripe whether a customer has a default payment method. This is the source of truth for whether an unattended top-up charge can name a card.

**Data flow**: It receives billing config, a Stripe customer ID, and an optional transport. It fetches the customer from Stripe and looks inside invoice settings for a default payment method string. It returns that method ID or None.

**Call relations**: BalanceTopup.run uses it before charging, _billing_autopay uses it before enabling refills, _billing_status uses it to report card presence, and _card_on_file uses it before fetching card details.

*Call graph*: calls 1 internal fn (_stripe); called by 4 (run, _billing_autopay, _billing_status, _card_on_file).


##### `_card_on_file`  (lines 581–596)

```
async def _card_on_file(config: BillingConfig, customer_id: str, transport: httpx.AsyncBaseTransport | None) -> CardOnFile | None
```

**Purpose**: Returns the human-recognizable card details for the customer’s default card: brand and last four digits. This is for display, not for charging.

**Data flow**: It receives billing config, a Stripe customer ID, and an optional transport. It first finds the default payment method. If none exists, it returns None. If one exists, it fetches that payment method from Stripe and returns a CardOnFile object when the method contains card brand and last-four data; otherwise it returns None.

**Call relations**: _billing_projection calls this when building the billing page response. It uses _default_payment_method for the method ID and _stripe for the extra Stripe read.

*Call graph*: calls 2 internal fn (_default_payment_method, _stripe); called by 1 (_billing_projection); 1 external calls (__init__).


##### `_stripe`  (lines 599–620)

```
async def _stripe(config: BillingConfig, method: str, path: str, transport: httpx.AsyncBaseTransport | None, data: dict[str, str] | None=None, idempotency_key: str | None=None) -> dict[str, object]
```

**Purpose**: Performs the low-level HTTP request to Stripe with the right authentication, API version, timeout, and optional idempotency key. It is the shared Stripe transport helper for this file.

**Data flow**: It receives billing config, HTTP method, Stripe path, optional transport, optional form data, and optional idempotency key. It sends the request to Stripe, raises StripeError with status and body if Stripe does not return success, and otherwise returns the JSON response as a dictionary.

**Call relations**: Stripe-specific helpers and BalanceTopup._charge all call this instead of creating their own HTTP requests. It creates StripeError objects when the provider rejects a request.

*Call graph*: calls 1 internal fn (__init__); called by 5 (_charge, _card_on_file, _default_payment_method, _portal_session, _stripe_customer); 1 external calls (AsyncClient).


##### `_as_str`  (lines 623–627)

```
def _as_str(value: object, field: str) -> str
```

**Purpose**: Checks that a provider response field is a non-empty string. It protects later code from quietly using missing IDs or URLs.

**Data flow**: It receives a value and the field’s plain name for error messages. If the value is a non-empty string, it returns it. Otherwise it raises a ValueError saying the provider response did not carry that field.

**Call relations**: _stripe_customer uses this for Stripe customer IDs, and _portal_session uses it for Stripe portal URLs.

*Call graph*: called by 2 (_portal_session, _stripe_customer).


##### `_ensure_metronome_customer`  (lines 630–681)

```
async def _ensure_metronome_customer(ctx: ExtensionContext, token: str, transport: httpx.AsyncBaseTransport | None) -> None
```

**Purpose**: Makes sure Metronome has a live customer whose ingest alias is this workspace’s UUID. Without that alias, usage events can be accepted but not attributed to the right customer.

**Data flow**: It receives the extension context, Metronome token, and optional transport. It looks up a customer by the workspace alias. If one exists, it returns. If not, it tries to create one with the alias. It treats permission failures and unresolved conflicts as hard errors, logs successful creation or harmless races, and returns only when the alias is confirmed.

**Call relations**: UsageShipper.run calls this once before sending the first batch in a pass. It calls _customer_by_alias before and sometimes after creation to avoid shipping usage into nowhere.

*Call graph*: calls 1 internal fn (_customer_by_alias); called by 1 (run); 4 external calls (__init__, __init__, AsyncClient, log).


##### `_customer_by_alias`  (lines 691–708)

```
async def _customer_by_alias(http: httpx.AsyncClient, headers: dict[str, str], alias: str) -> str | None
```

**Purpose**: Looks up the live Metronome customer that owns a given ingest alias. It returns None when no readable live customer is found.

**Data flow**: It receives an HTTP client, headers, and an alias string. It asks Metronome’s customer API for that alias. Permission failures become a special customer-scope error, other failed responses become MetronomeError, and a successful response returns the first customer ID or None.

**Call relations**: _ensure_metronome_customer uses this as its safety check before creating a customer and again after a conflict to distinguish a normal race from an invisible or archived alias holder.

*Call graph*: called by 1 (_ensure_metronome_customer); 3 external calls (__init__, __init__, get).


##### `BalanceTopup.run`  (lines 727–810)

```
async def run(self) -> None
```

**Purpose**: Checks whether a workspace needs an automatic balance refill and, if so, charges its saved Stripe card and credits the workspace. It carefully avoids repeated charges or repeated declined-card attempts.

**Data flow**: It reads the workspace’s configured auto-top-up rule. If none exists, it stops. It respects short waits after finding no card and longer waits after a decline. It loads billing config, finds the Stripe customer and default payment method, reads current charged totals, builds an attempt key, and calls _charge. If the charge is in flight or declined, it records the right waiting state. If it succeeds, it credits the workspace balance, marks the top-up verified, records the charge metric, and logs success.

**Call relations**: The scheduled top-up entry point creates BalanceTopup and calls this method. It uses _billing_record and _default_payment_method for Stripe identity, _charge for the payment, and core balance functions to read rules, credit funds, and verify the refill.

*Call graph*: calls 3 internal fn (_charge, _billing_record, _default_payment_method); 9 external calls (fromisoformat, now, count_charge, credit, mark_topup_verified, read_auto_topup, read_balance, log, warn).


##### `BalanceTopup._charge`  (lines 812–869)

```
async def _charge(self, config: BillingConfig, customer_id: str, payment_method: str, wanted: AutoTopup, workspace_id: UUID, attempt: str) -> str | None
```

**Purpose**: Creates and confirms a Stripe PaymentIntent for one automatic refill. It returns the payment ID only when money actually moved.

**Data flow**: It receives billing config, Stripe customer and payment method IDs, the desired top-up rule, workspace ID, and an attempt string. It converts micro-dollars to cents, sends a Stripe payment request with an idempotency key, and interprets the response. A conflict becomes an in-flight signal, a payment-required status becomes a declined-card result, a succeeded intent returns its ID, and other non-success statuses are raised.

**Call relations**: BalanceTopup.run calls this when it has confirmed that a refill rule and saved card exist. It uses _stripe for the provider call and warns when Stripe declines or returns a non-succeeded payment state.

*Call graph*: calls 1 internal fn (_stripe); called by 1 (run); 2 external calls (__init__, warn).


##### `_top_up`  (lines 872–873)

```
async def _top_up(ctx: ExtensionContext) -> None
```

**Purpose**: Acts as the scheduled job wrapper for automatic balance refills. It gives the manifest a simple handler for each candidate workspace.

**Data flow**: It receives an extension context, creates a BalanceTopup with that context and billing transport, and waits for its run method. It returns nothing after the top-up check completes.

**Call relations**: The manifest registers this as the balance top-up job. It delegates all real work to BalanceTopup.run.

*Call graph*: 1 external calls (__init__).


##### `_ingest`  (lines 876–884)

```
async def _ingest(token: str, events: list[dict[str, object]], transport: httpx.AsyncBaseTransport | None) -> None
```

**Purpose**: Sends a batch of usage events to Metronome’s ingest endpoint. It is the actual network write for usage shipping.

**Data flow**: It receives a Metronome bearer token, a list of event dictionaries, and an optional transport. It posts the events with authorization. If Metronome does not return success, it raises MetronomeError; otherwise it returns nothing.

**Call relations**: UsageShipper.run calls this after confirming the Metronome customer alias and building events. A successful return lets the shipper acknowledge the exports.

*Call graph*: called by 1 (run); 2 external calls (__init__, AsyncClient).


##### `_rfc3339`  (lines 887–889)

```
def _rfc3339(moment: datetime) -> str
```

**Purpose**: Formats a datetime for provider-facing JSON timestamps. If the time has no timezone, it treats it as UTC.

**Data flow**: It receives a datetime. It leaves timezone-aware values alone, adds UTC to timezone-less values, and returns the ISO-formatted timestamp string.

**Call relations**: UsageShipper._events uses this for Metronome event timestamps, and UsageShipper._note_usage_aging_out uses it for warning logs.

*Call graph*: called by 2 (_events, _note_usage_aging_out); 1 external calls (replace).


##### `_billing_request_workspace`  (lines 898–903)

```
def _billing_request_workspace(request: Request) -> UUID | None
```

**Purpose**: Identifies which workspace a billing page request belongs to by reading the session cookie. This is the first gate in keeping the billing page private.

**Data flow**: It receives an HTTP request, pulls the session cookie, asks the bearer-token helper for the workspace claim, and returns the workspace UUID or None.

**Call relations**: The manifest registers this as the identify function for the billing route. Core uses its answer to bind the request to a workspace before _billing_projection runs.

*Call graph*: 1 external calls (workspace_claim).


##### `_billing_projection`  (lines 906–973)

```
async def _billing_projection(ext: ExtensionContext, request: Request) -> Response
```

**Purpose**: Builds the JSON data shown on the billing screen: balance, stopping threshold, saved card details, autopay settings, and recent purchases. It is read-only and available outside the normal chat turn path.

**Data flow**: It receives the extension context and request. It verifies the session cookie for this workspace, checks that the email belongs to an admin member, then reads headroom, balance, auto-top-up configuration, and recent purchases from core. If the workspace is not balance-limited, it returns limited false. Otherwise it loads billing config, optionally reads card details from Stripe, tolerates Stripe read failures by marking the card unread, and returns a JSON response with all display fields.

**Call relations**: The billing route registered in the manifest calls this for GET requests. It uses core seat checks and balance reads, _billing_record for the Stripe customer pointer, and _card_on_file for displayable card information.

*Call graph*: calls 3 internal fn (transaction, _billing_record, _card_on_file); 9 external calls (configured_auto_topup, read_balance, read_headroom, recent_purchases, verify_token, JSONResponse, warn, member_by_email, member_is_admin).


##### `manifest`  (lines 976–1014)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the UFO host: its tools, scheduled jobs, HTTP route, prompt guidance, and credential slot. This is how the rest of the system discovers what the file provides.

**Data flow**: It creates and returns a Manifest object. The manifest includes the manage_billing tool, the usage shipping and balance top-up jobs with their candidate workspace sets, the billing page route, the billing prompt section, and an Anthropic API key credential slot used for bring-your-own-key behavior.

**Call relations**: The extension loader calls this to register the extension. The returned manifest points later runtime activity to _ship, _top_up, _billing_projection, and manage_billing.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, metered_workspaces, member_workspaces).

## 📊 State Registers Touched

- `reg-deployment-config` — The merged deployment settings that tell the system what product, services, addresses, databases, sandboxes, and safety defaults to use.
- `reg-workspace-records` — The saved workspace records that identify each customer space and hold its limits, setup state, balance settings, and routing boundaries.
- `reg-conversation-turn-state` — The conversation and turn queue state that tracks each unit of agent work from admission through running, completion, cancellation, or recovery.
- `reg-runtime-fleet-liveness` — The heartbeat and listener-claim records that show which long-running service instances are alive and what work they currently own.
- `reg-model-catalog` — The shared AI model catalog that records available providers, model names, prices, limits, API routes, key sources, and reasoning support.
- `reg-billing-ledger` — The usage ledger, spend caps, price versions, exports, and prepaid balance records used to meter and charge workspace activity.
- `reg-feature-flags` — The workspace feature switches that let the system turn capabilities on or off without changing the code.
- `reg-observability-traces` — The shared logs, metrics, traces, traceparent links, and safety-filtered operator views used to understand what the system is doing.
- `reg-object-store` — The workspace object records and change journal for agents, members, files, credentials, sites, connectors, memory records, reports, and extension objects.
- `reg-subagent-tasks` — The parent-child delegation state that tracks spawned helper agents, their inputs, outputs, names, costs, and undelivered results.
- `reg-prompt-and-delivery-policy` — The prompt, delivery-rule, compaction, and prompt-change proposal state that controls what instructions are rendered and how replies should be shaped.
- `reg-service-connection-pools` — Process-local shared connection/client pools for database, Redis/pubsub, HTTP/provider calls, and other long-lived service clients reused by requests, workers, tools, and jobs.
- `reg-turn-resource-budget` — In-flight per-turn resource budget and usage accumulator for spend, model tokens, cache reads, sandbox tokens, egress, retries, and stop conditions before final ledger reconciliation.
- `reg-evaluation-feedback-store` — Saved evaluation and self-improvement state, including test cases from failures, replay outputs, judge results, prompt-candidate gates, and promotion decisions.
- `reg-security-audit-log` — Durable audit records for sensitive reads and administrative/object changes, such as transcript access and object-change journaling.
- `reg-backend-provider-registry` — Process-local registry mapping provider names to active backend implementations for models, search, embeddings, memory, connectors, browser access, auth, billing, and feature services.

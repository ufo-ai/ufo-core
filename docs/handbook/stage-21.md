# Billing, usage accounting, and commercial controls  `stage-21` (cross-cutting infrastructure)

This stage is the system’s money meter. It runs partly before work starts, partly while models and tools are being used, and partly afterward when usage must be reported or paid for. Its job is to make sure a workspace has enough credit, measure what it used, turn that use into a cost, and send the right records to billing services.

The main ledger is accounting.py. It records workspace usage, applies charges to prepaid balances, checks spending limits, and prepares records for outside billing systems. balance.py is the prepaid wallet. It stores credit in very small dollar units, records purchases and refills, decides whether work can continue, and provides the messages users see when credit is low or gone. pricing.py is the price list for model usage: it converts token counts into costs and stamps records with the exact price table used. ufo_ext_metronome.py connects the local system to Metronome for usage reporting and Stripe for payments, billing pages, admin tools, and automatic refills. __init__.py simply makes the billing code importable.

## Files in this stage

### Usage ledger and pricing
Core runtime accounting records workspace usage, applies spend controls, and uses model pricing rules to compute billable costs.

### `core/src/ufo/runtime/billing/accounting.py`

`domain_logic` · `request handling, billing export, reporting, and spend checks`

This file answers a basic but important question: when UFO does work for a workspace, who pays, how much, and should more work be allowed? It treats the database ledger like a cash register tape. Every model call, sandbox network request, image, video, or background job becomes a row or an update in that ledger.

The file is careful about retries. A turn may be replayed after a shutdown, so token usage for the same attempt is stored as a growing total, not blindly added again. That prevents double charging. Separate attempts, such as a parked turn that later resumes, are added separately because they really are new work.

It also checks two kinds of limits. Balance checks decide whether prepaid credit is enough to start or continue work. Spend caps decide whether recent spending by a workspace, member, or agent is over a configured limit. The difference matters: a workspace may have money but still hit a cap, or have its own provider key and avoid token balance charges.

Finally, the file can summarize spending for dashboards and mint stable export records for external billing consumers. Those exports are frozen before delivery, like sealing an invoice, so retries send the same facts again instead of recalculating different numbers.

#### Function details

##### `OffTurnSpendRefused.__init__`  (lines 64–67)

```
def __init__(self, outcome: SpendOutcome, message: str, model: str) -> None
```

**Purpose**: Creates an error for an off-turn model call that was blocked by spend rules. It remembers both the decision, such as park or reject, and the specific model that was refused.

**Data flow**: It receives an outcome, a human-readable message, and a model name. It stores the outcome and model on the exception, then passes the message to the normal error machinery so callers can display or log it.

**Call relations**: It is raised from the off-turn model access path when that path asks billing whether a model call may run and the answer is no.

*Call graph*: called by 1 (turn).


##### `applicable_caps_absent`  (lines 70–76)

```
def applicable_caps_absent(workspace_id: UUID, member_id: UUID | None, agent_id: UUID) -> bool
```

**Purpose**: Quickly answers whether recent checks found no spend caps for this exact workspace, member, and agent combination. This avoids an unnecessary database read in the common case where no caps exist.

**Data flow**: It receives the three identities being checked. It looks in a small in-memory cache and compares the saved expiry time with the current clock; it returns true only if the no-cap result is still fresh.

**Call relations**: Other billing admission paths can call this before doing a full cap check. The cache entries it reads are written by SpendEvaluator.decide when a real database check finds no caps.

*Call graph*: 1 external calls (monotonic).


##### `_note_absent_caps`  (lines 79–88)

```
def _note_absent_caps(key: tuple[UUID, UUID | None, UUID | None]) -> None
```

**Purpose**: Remembers for a few seconds that no spend caps applied to one workspace/member/agent triple. This is a performance shortcut, not the source of truth.

**Data flow**: It receives a cache key. It removes expired entries if the cache is full, then stores a new expiry time for that key based on the current clock.

**Call relations**: SpendEvaluator.decide calls this after it has checked the database and found no applicable caps. Later callers can use applicable_caps_absent to skip repeating that database check for a short time.

*Call graph*: called by 1 (decide); 1 external calls (monotonic).


##### `_total_tokens`  (lines 91–99)

```
def _total_tokens(usage: Usage) -> int
```

**Purpose**: Adds together all token counters in a usage report. It treats normal input, output, cache reads, and cache writes as parts of one total burn.

**Data flow**: It receives a Usage object. It reads each token field and returns their sum as a single integer.

**Call relations**: The token-recording functions call this before deciding whether there is anything to bill and before writing the ledger amount.

*Call graph*: called by 3 (record_sandbox_tokens, record_turn_usage, record_workspace_usage).


##### `_prompt_tokens`  (lines 102–111)

```
def _prompt_tokens(usage: Usage) -> int
```

**Purpose**: Counts the tokens that made up the prompt seen by the model, including cached prompt pieces. This is used to calculate what share of the prompt came from cache.

**Data flow**: It receives a Usage object. It adds input tokens and cache-related prompt tokens, but not output tokens, and returns that prompt total.

**Call relations**: Token ledger writers store this value beside total tokens. read_turn_cost later uses those stored prompt and cache counts to report a cache percentage.

*Call graph*: called by 3 (record_sandbox_tokens, record_turn_usage, record_workspace_usage).


##### `workspace_owns_the_key`  (lines 114–137)

```
async def workspace_owns_the_key(connection: AsyncConnection, workspace_id: UUID, key_slot: str | None) -> bool
```

**Purpose**: Checks whether the workspace has stored its own provider key for a given key slot. This matters because calls paid directly through the workspace's own key should not also drain the platform balance.

**Data flow**: It receives a database connection, workspace id, and key slot. If there is no key slot it returns false; otherwise it asks the credential table whether that workspace has a matching credential row.

**Call relations**: BalanceGate._workspace_serves_itself calls this while deciding whether a low-balance workspace can still start work because its own provider key will pay for the model call.

*Call graph*: called by 1 (_workspace_serves_itself); 3 external calls (exists, scalar, select).


##### `record_turn_usage`  (lines 140–275)

```
async def record_turn_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, usage: Usage, attempt: str='', pricing: Pricing=CORE_PRICING, byok: bool=False) -> None
```

**Purpose**: Writes the token cost for one turn attempt into the ledger and debits the workspace balance for the new cost only. It is built to survive workflow replays without charging twice.

**Data flow**: It receives the workspace, turn, model, usage counters, attempt id, pricing table, and whether the workspace brought its own key. It prices the cumulative usage, compares it with any existing row for the same attempt, debits only the positive increase when appropriate, and inserts or updates the ledger row. If a replay reports incompatible totals, it raises a conflict instead of guessing.

**Call relations**: Turn execution calls this as model usage becomes known. It relies on _total_tokens and _prompt_tokens for token math, Pricing.micro_usd for cost, debit for balance movement, and ledger_id_for for stable row identity.

*Call graph*: calls 3 internal fn (micro_usd, _prompt_tokens, _total_tokens); 7 external calls (__init__, execute, insert, select, update, debit, ledger_id_for).


##### `read_turn_cost`  (lines 289–319)

```
async def read_turn_cost(connection: AsyncConnection, turn_id: UUID, dimension: str) -> TurnCost | None
```

**Purpose**: Reads the final cost of a turn for one ledger dimension, such as normal tokens or sandbox tokens. It sums across multiple attempts so a parked and resumed turn shows its full cost.

**Data flow**: It receives a connection, turn id, and dimension name. It sums ledger rows for that turn and dimension, calculates the cache percentage from stored prompt/cache counts, and returns a TurnCost object or None if nothing was billed.

**Call relations**: Terminal reporting code can call this after billing rows have been written. It reads the same ledger data produced by record_turn_usage and record_sandbox_tokens.

*Call graph*: 3 external calls (__init__, execute, select).


##### `record_workspace_usage`  (lines 322–371)

```
async def record_workspace_usage(connection: AsyncConnection, workspace_id: UUID, model: str, usage: Usage, pricing: Pricing=CORE_PRICING, byok: bool=False) -> None
```

**Purpose**: Bills a model call made by a workspace-level background job rather than by a user turn. These costs belong to the workspace but not to any member or agent.

**Data flow**: It receives workspace id, model, usage, pricing, and bring-your-own-key status. It totals and prices the usage, debits the workspace unless the workspace key paid directly, and inserts a fresh ledger row with no turn id.

**Call relations**: Background jobs call this after a real provider call completes. It uses the same token math and pricing helpers as turn billing, but it does not use replay-style cumulative rows.

*Call graph*: calls 3 internal fn (micro_usd, _prompt_tokens, _total_tokens); 4 external calls (execute, insert, debit, uuid4).


##### `record_egress_request`  (lines 374–403)

```
async def record_egress_request(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, amount: int=1) -> None
```

**Purpose**: Counts sandbox network egress requests for a turn. These requests are metered for reporting, but priced at zero and do not move spend caps by cost.

**Data flow**: It receives workspace id, turn id, and a request count. It derives the per-turn egress ledger id and inserts a row or atomically adds to the existing count.

**Call relations**: The sandbox egress proxy calls this as turn-related network requests are flushed. It uses an upsert so concurrent writes add together instead of overwriting each other.

*Call graph*: 2 external calls (execute, ledger_id_for).


##### `record_probe_egress_request`  (lines 406–432)

```
async def record_probe_egress_request(connection: AsyncConnection, workspace_id: UUID, amount: int=1) -> None
```

**Purpose**: Counts sandbox egress requests made by an off-turn probe. Like turn egress, it is a zero-cost count, but it has no turn id.

**Data flow**: It receives workspace id and a count. It inserts a new ledger row with a fresh id, no turn id, the egress dimension, and zero price.

**Call relations**: Probe network accounting calls this when it has a batch to record. These rows appear in workspace-level totals but not member or agent attribution, because they are not attached to a turn.

*Call graph*: 3 external calls (execute, insert, uuid4).


##### `record_sandbox_tokens`  (lines 435–511)

```
async def record_sandbox_tokens(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, usage: Usage, pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: Bills model calls made from inside the sandbox through the egress proxy. These are separate from the host turn loop's model calls, so they are recorded under a separate ledger dimension.

**Data flow**: It receives workspace, turn, model, usage, and pricing. It totals and prices the tokens, debits the workspace balance, and inserts or atomically adds the token counts and costs to the turn's sandbox-token ledger row.

**Call relations**: The egress proxy calls this when sandbox code makes model calls. It uses _total_tokens, _prompt_tokens, Pricing.micro_usd, debit, and a stable ledger id so multiple sandbox calls on one turn accumulate safely.

*Call graph*: calls 3 internal fn (micro_usd, _prompt_tokens, _total_tokens); 3 external calls (execute, debit, ledger_id_for).


##### `record_image_usage`  (lines 514–533)

```
async def record_image_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, images: int, micro_usd: int) -> None
```

**Purpose**: Records generated image usage for a turn. The caller supplies the already-known provider cost because image billing is not token-based here.

**Data flow**: It receives workspace id, turn id, image model, image count, and cost. It passes those values to the shared media-recording helper using the images dimension.

**Call relations**: Image provider integrations call this after generation. It delegates the insert/update and balance debit to _record_media_usage.

*Call graph*: calls 1 internal fn (_record_media_usage).


##### `record_video_usage`  (lines 536–550)

```
async def record_video_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, videos: int, micro_usd: int) -> None
```

**Purpose**: Records generated video usage for a turn. The caller supplies the count and provider cost because video billing uses provider-specific units rather than this file's token pricing table.

**Data flow**: It receives workspace id, turn id, video model, video count, and cost. It passes those values to the shared media-recording helper using the videos dimension.

**Call relations**: Video provider integrations call this after generation. It shares the same ledger and debit path as image usage through _record_media_usage.

*Call graph*: calls 1 internal fn (_record_media_usage).


##### `_record_media_usage`  (lines 553–594)

```
async def _record_media_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, dimension: str, model: str, amount: int, micro_usd: int) -> None
```

**Purpose**: Shared helper that records image or video generation and charges the workspace balance. It keeps one accumulating ledger row per turn and media dimension.

**Data flow**: It receives the dimension, amount, model, and cost. It debits the workspace for the cost, then inserts a ledger row or atomically adds the amount and cost to the existing row.

**Call relations**: record_image_usage and record_video_usage call this so both media types behave the same way. It uses ledger_id_for for stable row identity and debit to move prepaid balance.

*Call graph*: called by 2 (record_image_usage, record_video_usage); 3 external calls (execute, debit, ledger_id_for).


##### `mint_usage_exports`  (lines 619–742)

```
async def mint_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, floor: datetime, key_slot_for: Callable[[str], str | None]) -> None
```

**Purpose**: Creates frozen export intents for ledger growth that should be sent to an external billing consumer. This turns changing ledger rows into stable delivery units.

**Data flow**: It receives a workspace, consumer name, backfill floor time, and a function that maps model names to key slots. It finds ledger rows with unexported growth, works out whether the workspace brought its own key, and inserts ledger_export rows keyed so retries or concurrent runs do not duplicate them.

**Call relations**: A billing export job calls this before reading pending exports. Later read_pending_usage_exports reads the frozen rows, and ack_usage_exports marks them delivered after the outside system accepts them.

*Call graph*: 5 external calls (now, timedelta, execute, or_, select).


##### `read_pending_usage_exports`  (lines 745–790)

```
async def read_pending_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, limit: int) -> tuple[UsageExport, ...]
```

**Purpose**: Fetches usage export records that have been minted but not yet acknowledged. These are ready to send to an outside billing system.

**Data flow**: It receives a workspace, consumer name, and limit. It joins export rows to their ledger descriptions, calculates the delta amount and delta cost, and returns UsageExport objects in stable order.

**Call relations**: An export worker calls this after mint_usage_exports. If delivery fails before acknowledgment, this function will return the same frozen records again.

*Call graph*: 3 external calls (__init__, execute, select).


##### `ack_usage_exports`  (lines 793–817)

```
async def ack_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: Marks exported usage records as acknowledged after the external consumer has accepted them. This removes them from future pending reads.

**Data flow**: It receives the workspace, consumer, and the exact UsageExport objects that were delivered. It builds a filter from each ledger id and starting amount, then updates matching rows with an acknowledgment timestamp.

**Call relations**: The export worker calls this only after a successful external API call. If the worker crashes before this point, read_pending_usage_exports will safely resend the same frozen intents.

*Call graph*: 3 external calls (execute, or_, update).


##### `metered_workspaces`  (lines 820–823)

```
def metered_workspaces() -> WorkspaceCandidates
```

**Purpose**: Finds candidate workspaces for usage export jobs. It returns every workspace that has ever written something to the ledger.

**Data flow**: It builds a candidate query over distinct workspace ids in the ledger table and wraps it in the project's workspace-candidate helper.

**Call relations**: Background export scheduling uses this to know which workspaces might have usage to mint or send. The later per-workspace checks are allowed to be no-ops if everything is already exported.

*Call graph*: 1 external calls (owner_candidates).


##### `SpendEvaluator.decide`  (lines 860–875)

```
async def decide(self, connection: AsyncConnection, pending_micro_usd: int) -> SpendDecision
```

**Purpose**: Decides whether proposed work is allowed, should be parked, or must be rejected under spend caps. A spend cap is a configured limit over a recent time window.

**Data flow**: It receives a database connection and the cost about to be incurred. It loads applicable caps, caches the no-cap case, sums recent spending for each cap, compares used plus pending cost to each limit, and returns a SpendDecision with a message if blocked.

**Call relations**: Admission and mid-turn checks call this when spend caps are enabled. It coordinates _applicable_caps, _used_micro_usd, _message, and _note_absent_caps.

*Call graph*: calls 4 internal fn (_applicable_caps, _message, _used_micro_usd, _note_absent_caps); 1 external calls (__init__).


##### `SpendEvaluator._applicable_caps`  (lines 877–905)

```
async def _applicable_caps(self, connection: AsyncConnection) -> tuple[SpendCap, ...]
```

**Purpose**: Loads the spend caps that apply to this workspace/member/agent combination. Caps can apply to the whole workspace, one member, or one agent.

**Data flow**: It reads the spend_cap table for rows matching the evaluator's workspace and either the workspace scope or the exact member or agent id. It converts each database row into a SpendCap object.

**Call relations**: SpendEvaluator.decide calls this first. Its result determines whether the decision can allow immediately or must inspect recent ledger usage.

*Call graph*: called by 1 (decide); 4 external calls (__init__, execute, or_, select).


##### `SpendEvaluator._used_micro_usd`  (lines 907–933)

```
async def _used_micro_usd(self, connection: AsyncConnection, cap: SpendCap) -> int
```

**Purpose**: Calculates how much money has already been spent inside one cap's rolling time window. The answer is in micro-USD, meaning millionths of a US dollar.

**Data flow**: It receives a cap, computes the cutoff time from the cap window, and sums matching ledger costs. Workspace caps sum by workspace; member caps join through conversations; agent caps join through turns.

**Call relations**: SpendEvaluator.decide calls this for each applicable cap before comparing usage plus pending cost against the cap limit.

*Call graph*: called by 1 (decide); 4 external calls (now, timedelta, execute, select).


##### `SpendEvaluator._message`  (lines 935–946)

```
def _message(self, outcome: SpendOutcome, breaches: list[SpendCap]) -> str
```

**Purpose**: Builds the human-readable refusal message for breached spend caps. It names the tightest breached cap and whether the turn was parked or declined.

**Data flow**: It receives the final outcome and the breached caps. It picks the cap with the smallest limit, converts micro-USD to dollars, and returns a clear message for the user.

**Call relations**: SpendEvaluator.decide calls this only after it has found at least one breached cap. The returned text becomes part of the SpendDecision.

*Call graph*: called by 1 (decide).


##### `_token_sum`  (lines 1052–1060)

```
def _token_sum() -> sa.ColumnElement[int]
```

**Purpose**: Builds a database expression that sums only token-like dimensions. It includes normal model tokens and sandbox model tokens, but not images, videos, or egress counts.

**Data flow**: It reads ledger rows inside whatever larger query uses it. For rows whose dimension is token-based it contributes the amount; for other rows it contributes zero, then wraps the sum so empty results become zero.

**Call relations**: Spend reports and usage details use this helper to keep token counting consistent across workspace, member, model, execution, and origin summaries.

*Call graph*: called by 3 (_by_origin, read, _usage_details); 1 external calls (case).


##### `_token_cost_sum`  (lines 1063–1075)

```
def _token_cost_sum() -> sa.ColumnElement[int]
```

**Purpose**: Builds a database expression that sums costs only for token-like dimensions. This separates token spending from other billed media or request dimensions.

**Data flow**: It reads ledger rows inside a larger query. For token and sandbox-token rows it contributes priced_micro_usd; for all other dimensions it contributes zero, then returns a zero-safe sum.

**Call relations**: SpendRollup.read, SpendRollup._by_origin, and _usage_details use this so every report uses the same definition of token cost.

*Call graph*: called by 3 (_by_origin, read, _usage_details); 1 external calls (case).


##### `_usage_details`  (lines 1078–1202)

```
async def _usage_details(connection: AsyncConnection, source: sa.FromClause, scope: sa.ColumnElement[bool], cutoff: datetime | None, now: datetime) -> UsageDetails
```

**Purpose**: Builds detailed usage statistics for a selected scope, such as a workspace or one member. It returns totals, daily history, breakdowns by execution style and model, and comparison with the previous time window.

**Data flow**: It receives a database source, a scope condition, an optional cutoff, and the current time. It runs several ledger queries: selected totals, all-time totals, daily rows with missing days filled as zero, execution breakdown, model breakdown, and previous-window token count. It returns all of that as a UsageDetails object.

**Call relations**: SpendRollup.read uses this for workspace-wide reports, and SpendRollup.read_member uses it for member-only reports. It relies on _token_sum and _token_cost_sum to keep token math consistent.

*Call graph*: calls 2 internal fn (_token_cost_sum, _token_sum); called by 2 (read, read_member); 9 external calls (__init__, __init__, __init__, __init__, fromisoformat, date, timedelta, execute, select).


##### `SpendRollup.read`  (lines 1213–1317)

```
async def read(self, connection: AsyncConnection, window_seconds: int | None) -> SpendReport
```

**Purpose**: Reads a full spend report for a workspace. It summarizes total cost and breaks usage down by dimension, member, agent, origin, price table, model, day, and execution style.

**Data flow**: It receives a connection and an optional time window. It builds a ledger filter for the workspace and window, runs grouped database queries for each report section, asks _by_origin for origin attribution, asks _usage_details for time-series and token details, and returns a SpendReport.

**Call relations**: Dashboard or admin reporting code calls this when showing workspace billing. It pulls together lower-level query helpers into one report object.

*Call graph*: calls 4 internal fn (_by_origin, _token_cost_sum, _token_sum, _usage_details); 8 external calls (__init__, __init__, __init__, __init__, now, timedelta, execute, select).


##### `SpendRollup._by_origin`  (lines 1319–1384)

```
async def _by_origin(self, connection: AsyncConnection, window: sa.ColumnElement[bool]) -> tuple[OriginTotal, ...]
```

**Purpose**: Groups token spend by the user-facing place where the work began, such as a chat surface or channel. It follows parent turns so subagent work is credited to the original conversation, not hidden under an internal child conversation.

**Data flow**: It receives a connection and an existing ledger window condition. It builds a recursive database query that walks from spending turns up to their root turns, joins those roots to conversations, and sums token counts and costs by surface label.

**Call relations**: SpendRollup.read calls this as one part of the workspace spend report. It uses _token_sum and _token_cost_sum so origin totals match other token summaries.

*Call graph*: calls 2 internal fn (_token_cost_sum, _token_sum); called by 1 (read); 4 external calls (__init__, desc, execute, select).


##### `SpendRollup.read_member`  (lines 1386–1444)

```
async def read_member(self, connection: AsyncConnection, member_id: UUID, window_seconds: int | None) -> MemberSpendReport
```

**Purpose**: Reads a spend report for one member only. It includes that member's ledger totals, member-specific caps, and detailed usage, without naming other members or agents.

**Data flow**: It receives a member id and optional time window. It joins ledger rows through turns to conversations, filters to that member, groups spending by dimension, loads member-scoped caps, calls _usage_details for totals and history, and returns a MemberSpendReport.

**Call relations**: Member-facing billing pages or admin member drilldowns call this. It mirrors the same attribution path used by member spend caps.

*Call graph*: calls 1 internal fn (_usage_details); 7 external calls (__init__, __init__, __init__, now, timedelta, execute, select).


##### `BalanceGate.admits`  (lines 1476–1514)

```
async def admits(self, connection: AsyncConnection, agent_id: UUID | None=None, key_slot_for: Callable[[str], str | None] | None=None, turn_id: UUID | None=None, model: str | None=None) -> SpendDecisi
```

**Purpose**: Decides whether a turn may start, be folded into a running workflow, or resume after being parked based on prepaid balance. It uses a stricter start threshold than the continue threshold to avoid repeated start-stop loops.

**Data flow**: It receives a connection and optional agent, model, key-slot resolver, and turn id. It reads balance headroom, allows self-host/no-balance work, allows enough balance, checks whether a previously debited turn should stay blocked, and may allow a positive-balance workspace using its own provider key. Otherwise it returns a rejection message.

**Call relations**: Turn admission and resume logic call this before letting work begin. It may call _turn_has_debited and _workspace_serves_itself, and it uses balance helpers to read headroom and create refusal text.

*Call graph*: calls 2 internal fn (_turn_has_debited, _workspace_serves_itself); 4 external calls (__init__, _forget_absent_balance, balance_refusal_message, read_headroom).


##### `BalanceGate._workspace_serves_itself`  (lines 1516–1538)

```
async def _workspace_serves_itself(self, connection: AsyncConnection, agent_id: UUID | None, key_slot_for: Callable[[str], str | None] | None, model: str | None=None) -> bool
```

**Purpose**: Checks whether the model that will run can be served by the workspace's own provider key. If so, model token calls may not need to debit platform balance.

**Data flow**: It receives optional agent id, key-slot resolver, and model. If no model is supplied, it looks up the agent's model in the database. It maps the model to a key slot and asks whether the workspace owns that key.

**Call relations**: BalanceGate.admits calls this only when balance is below the normal start reserve but still positive. It delegates the final credential check to workspace_owns_the_key.

*Call graph*: calls 1 internal fn (workspace_owns_the_key); called by 1 (admits); 2 external calls (execute, select).


##### `BalanceGate.sustains`  (lines 1540–1561)

```
async def sustains(self, connection: AsyncConnection, pending_micro_usd: int, turn_id: UUID | None=None) -> SpendDecision
```

**Purpose**: Decides whether a running turn may continue for another round. Unlike admission, continuation stops at zero balance, not at the start reserve.

**Data flow**: It receives a connection, pending cost, and optional turn id. It reads balance headroom, allows self-host/no-balance work, allows if balance minus pending cost stays above the grace-adjusted zero line, rejects if the pending or prior turn spend actually debits balance, and otherwise allows zero-debit work to continue.

**Call relations**: The turn loop calls this between rounds or before more spend is taken. It uses _turn_has_debited to avoid letting already-charging turns run past the balance line, and balance helpers for headroom and refusal text.

*Call graph*: calls 1 internal fn (_turn_has_debited); 4 external calls (__init__, _forget_absent_balance, balance_refusal_message, read_headroom).


##### `BalanceGate._turn_has_debited`  (lines 1563–1586)

```
async def _turn_has_debited(self, connection: AsyncConnection, turn_id: UUID | None) -> bool
```

**Purpose**: Checks whether a turn has already taken any money from the workspace balance. This is different from asking whether the turn has a priced ledger row, because bring-your-own-key token rows can be priced but debit nothing.

**Data flow**: It receives a connection and optional turn id. If there is no turn id it returns false; otherwise it looks for any ledger row for that turn with debited_micro_usd greater than zero.

**Call relations**: BalanceGate.admits and BalanceGate.sustains call this when deciding whether a low-balance turn can safely start or continue. The answer prevents endless park/resume loops and prevents charged work from continuing below the balance line.

*Call graph*: called by 2 (admits, sustains); 2 external calls (execute, select).


### `core/src/ufo/harness/models/pricing.py`

`domain_logic` · `billing and usage recording`

Model providers charge different prices for different kinds of tokens, such as input tokens, output tokens, and cached tokens. This file is the small billing calculator for that world. It stores each model’s rates in micro-dollars, meaning millionths of a US dollar, so the code can use whole numbers instead of floating-point money values that can round strangely.

The central data shape is ModelPrice, which holds one model’s rates per million tokens. A Pricing object wraps a whole table of these prices and includes a digest, which is a cryptographic fingerprint of the table. That digest works like a receipt stamp: if prices change later, old billing records can still point to the exact version of prices they used.

When usage needs to be billed, the code looks up the model’s price, multiplies each token count by the matching rate, adds the parts together, and divides by one million to convert “per million tokens” pricing into actual micro-dollars. If the model is unknown, it logs a warning and returns zero rather than crashing. That is important for historical data: an old record may mention a model that is no longer in the current price table.

#### Function details

##### `price_digest`  (lines 27–44)

```
def price_digest(prices: Mapping[str, ModelPrice]) -> str
```

**Purpose**: Creates a stable version stamp for a model price table. This lets billing records later prove which exact prices were used, even if the table changes.

**Data flow**: It receives a mapping from model names to ModelPrice values. It turns that table into sorted, compact JSON so the same prices always produce the same text, then runs SHA-256, a standard fingerprinting algorithm, over that text. It returns a string beginning with "sha256:" followed by the fingerprint.

**Call relations**: pricing_from calls this when it builds a Pricing object. price_digest relies on json.dumps to make the price table into text and hashlib.sha256 to make the final fingerprint.

*Call graph*: called by 1 (pricing_from); 2 external calls (sha256, dumps).


##### `usage_priced_micro_usd`  (lines 47–61)

```
def usage_priced_micro_usd(model: str, usage: Usage, prices: Mapping[str, ModelPrice]) -> int
```

**Purpose**: Calculates the cost of one model usage record in micro-dollars. It is the actual arithmetic that turns token counts into a billable amount.

**Data flow**: It receives a model name, a Usage object containing token counts, and a price table. It looks up the model’s rates, multiplies each kind of token by its matching rate, adds those amounts, and divides by one million because the rates are expressed per million tokens. It returns the final cost as an integer number of micro-dollars; if the model is missing, it logs that fact and returns zero.

**Call relations**: Pricing.micro_usd calls this whenever the rest of the billing system asks a Pricing object to price usage. If the model is not found, it hands a warning event to ufo.harness.o11y.log so the missing price can be noticed without stopping the run.

*Call graph*: called by 1 (micro_usd); 1 external calls (log).


##### `Pricing.micro_usd`  (lines 71–72)

```
def micro_usd(self, model: str, usage: Usage) -> int
```

**Purpose**: Provides the convenient public method for pricing usage through a Pricing object. Callers do not need to pass the price table separately because the object already carries it.

**Data flow**: It receives a model name and a Usage object. It uses the Pricing object’s stored price table and passes everything to usage_priced_micro_usd. It returns the calculated cost in micro-dollars.

**Call relations**: Billing accounting code calls this when recording sandbox, turn, or workspace usage. This method is the bridge between higher-level accounting and the lower-level pricing arithmetic in usage_priced_micro_usd.

*Call graph*: calls 1 internal fn (usage_priced_micro_usd); called by 3 (record_sandbox_tokens, record_turn_usage, record_workspace_usage).


##### `pricing_from`  (lines 75–78)

```
def pricing_from(prices: Mapping[str, ModelPrice]) -> Pricing
```

**Purpose**: Builds a complete Pricing object from a raw model price table. It packages the prices together with the digest that identifies that exact table.

**Data flow**: It receives a mapping of model names to ModelPrice values. It copies that mapping into a plain dictionary, computes its digest with price_digest, and returns a new Pricing object containing both the copied table and the digest.

**Call relations**: This is used when the system wants to turn configured or loaded price data into the immutable pricing object used by billing. It calls price_digest first so the Pricing object is born with its version stamp already attached.

*Call graph*: calls 1 internal fn (price_digest); 1 external calls (__init__).


### Prepaid billing package
Runtime billing package support and prepaid balance logic track credits, debits, refill settings, and out-of-credit messaging.

### `core/src/ufo/runtime/billing/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a file named `__init__.py` tells the language that the surrounding folder should be treated as an importable package. Here, that means code elsewhere can refer to modules under `ufo.runtime.billing` in a clean, organized way.

There is no billing calculation, setup step, or shared object defined here. Its value is structural rather than behavioral: it gives the billing subsystem a named place in the project. Think of it like a label on a filing cabinet drawer. The drawer may contain important papers in other files, but this label is what lets people reliably find and refer to that drawer.

Without this file, depending on the Python version and packaging setup, imports from this folder could be less predictable or fail in environments that expect traditional package markers.


### `core/src/ufo/runtime/billing/balance.py`

`domain_logic` · `cross-cutting: used during billing reads, admission checks, crediting, debiting, and refill jobs`

This file is the billing balance ledger for a workspace. A workspace can be given credit, spend that credit as model work runs, set aside a safety reserve, and optionally refill itself when the balance drops too low. Without this file, the system would not know whether a workspace can afford more work, whether it should trigger an automatic top-up, or how much credit has ever been bought or spent.

The key idea is that the current balance is stored as its own database row, instead of being recalculated from every purchase each time. That matters because the system checks credit before many model rounds, and a lifetime sum would get slower forever. Purchases are still stored separately as an audit trail, like keeping receipts even though the cash register also shows the current drawer total.

The file also has a tiny short-lived memory for workspaces that have no balance row. This avoids repeated database reads in self-hosted or unpaid setups, but it is only a shortcut; adding credit clears the memory so correctness does not depend on it.

Automatic top-up support is split into reading the refill rule, finding workspaces that have crossed their refill line, and marking that a card has successfully paid. Once a card has paid, the workspace earns a fixed grace amount so brief refill delays do not stop active work unnecessarily.

#### Function details

##### `balance_absent`  (lines 40–46)

```
def balance_absent(workspace_id: UUID) -> bool
```

**Purpose**: Checks a short-lived in-memory note saying that a workspace recently had no balance row. This lets the system skip some database work for workspaces that are not using prepaid billing.

**Data flow**: It receives a workspace ID, looks in the local absent-balance cache, compares the saved expiry time with the current monotonic clock, and returns true only if the note still exists and has not expired. It does not change the database.

**Call relations**: Other parts of the billing gate can use this as a fast shortcut before doing a more expensive balance read. The note it relies on is created by reads that find no balance row and is cleared when credit is added.

*Call graph*: 1 external calls (monotonic).


##### `_note_absent_balance`  (lines 49–56)

```
def _note_absent_balance(workspace_id: UUID) -> None
```

**Purpose**: Remembers, for a few seconds, that a workspace has no balance row. This avoids repeatedly asking the database the same question during a busy period.

**Data flow**: It receives a workspace ID, reads the current monotonic time, optionally removes expired cache entries if the cache is full, then stores a new expiry time for that workspace. The output is not a return value but a changed in-memory cache.

**Call relations**: read_headroom and read_balance call this when the database says there is no balance row. Later, balance_absent can use the note as a shortcut, and credit removes the note if money is added.

*Call graph*: called by 2 (read_balance, read_headroom); 1 external calls (monotonic).


##### `_forget_absent_balance`  (lines 59–62)

```
def _forget_absent_balance(workspace_id: UUID) -> None
```

**Purpose**: Clears the short-lived note that said a workspace had no balance row. This is needed when the workspace has just been credited.

**Data flow**: It receives a workspace ID and removes that ID from the local absent-balance cache if present. It returns nothing and does not touch the database.

**Call relations**: credit calls this after successfully adding money. That keeps the fast path from pretending a newly funded workspace still has no balance.

*Call graph*: called by 1 (credit).


##### `billing_screen_url`  (lines 79–88)

```
def billing_screen_url(public_base_url: str | None, home_surface: str | None) -> str | None
```

**Purpose**: Builds the URL for the workspace billing screen, if this deployment has a public browser surface. This gives error messages somewhere useful to send an admin.

**Data flow**: It receives an optional public base URL and an optional home surface name. If either is missing, it returns None; otherwise it joins them into a billing-page URL ending with the billing screen fragment.

**Call relations**: This is typically prepared by setup code and passed down to billing gates or user-message builders. The message functions use the resulting URL when telling users how credit can be fixed.


##### `balance_refusal_message`  (lines 91–100)

```
def balance_refusal_message(billing_url: str | None) -> str
```

**Purpose**: Creates the message shown when a workspace is out of credit and work is refused. It explains the problem and points an admin to the billing screen when one exists.

**Data flow**: It receives an optional billing URL. With no URL, it returns a plain message saying an admin can set up refills; with a URL, it returns the same warning plus the place to add credit.

**Call relations**: Admission or tool-spawn code can use this when stopping work immediately because there is not enough credit. It depends on billing_screen_url only indirectly through the URL passed into it.


##### `balance_park_message`  (lines 103–115)

```
def balance_park_message(billing_url: str | None) -> str
```

**Purpose**: Creates the message shown when a user message is held because the workspace is out of credit. It tells the user the message will be answered automatically after credit is added.

**Data flow**: It receives an optional billing URL. It builds a base explanation about the message being held, then either adds a generic admin refill instruction or a specific billing link.

**Call relations**: This fits into flows that pause, rather than reject, user work while waiting for payment. Like the refusal message, it uses a billing URL prepared elsewhere.


##### `read_auto_topup`  (lines 127–149)

```
async def read_auto_topup(connection: AsyncConnection, workspace_id: UUID) -> AutoTopup | None
```

**Purpose**: Checks whether a workspace has automatic refill settings and has dropped low enough to trigger them. It answers the refill job's practical question: should this workspace be charged now?

**Data flow**: It receives a database connection and workspace ID, reads the workspace balance and top-up fields, and returns None if there is no balance row, no top-up rule, or the balance is still above the threshold. If the workspace is at or below the threshold, it returns an AutoTopup value with the refill amount and trigger threshold.

**Call relations**: A payment extension or refill worker calls this before attempting a charge. It keeps the core billing code responsible for deciding when a workspace is short, while the payment integration decides how to collect money.

*Call graph*: 3 external calls (__init__, execute, select).


##### `topping_up_workspaces`  (lines 152–166)

```
def topping_up_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a candidate source for the scheduled refill job: workspaces that have automatic top-up configured and are already at or below their threshold. This keeps the job from opening transactions for workspaces that clearly do not need payment.

**Data flow**: It creates a small query-producing function, hands that function to owner_candidates, and returns a WorkspaceCandidates object. The returned object represents the workspaces that should be considered for refill.

**Call relations**: A periodic refill sweep uses this to decide which workspace owners to visit. Inside it, short_of_its_line supplies the database query that identifies workspaces needing attention.

*Call graph*: 1 external calls (owner_candidates).


##### `topping_up_workspaces.short_of_its_line`  (lines 159–164)

```
def short_of_its_line() -> sa.Select[tuple[UUID]]
```

**Purpose**: Defines the actual database query for workspaces whose balance has crossed their automatic refill line. It is the filter used by topping_up_workspaces.

**Data flow**: It takes no direct input, reads the balance table fields in a SQL expression, and returns a select statement for workspace IDs where auto top-up is configured and the balance is less than or equal to the threshold.

**Call relations**: This nested function is passed to owner_candidates by topping_up_workspaces. The refill candidate machinery can call it when it needs the current list of workspaces to consider.

*Call graph*: 1 external calls (select).


##### `set_auto_topup`  (lines 169–189)

```
async def set_auto_topup(connection: AsyncConnection, workspace_id: UUID, amount_micro_usd: int | None, threshold_micro_usd: int | None) -> bool
```

**Purpose**: Turns automatic refilling on or off for an existing workspace balance. It prevents half-configured top-up rules by requiring both amount and threshold together, or neither.

**Data flow**: It receives a database connection, workspace ID, optional refill amount, and optional threshold. If only one of amount or threshold is present, it raises an error; otherwise it updates the existing balance row and returns true if exactly one row was changed.

**Call relations**: An admin-facing billing settings flow would call this when a workspace changes refill settings. Later, read_auto_topup and topping_up_workspaces read the values it stores.

*Call graph*: 2 external calls (execute, update).


##### `mark_topup_verified`  (lines 192–206)

```
async def mark_topup_verified(connection: AsyncConnection, workspace_id: UUID) -> None
```

**Purpose**: Records that this workspace has successfully paid by card at least once. That proof gives the workspace a fixed grace allowance when refills are slightly delayed.

**Data flow**: It receives a database connection and workspace ID, then updates the balance row only if its verified timestamp is still empty. It writes the verification time and an updated timestamp, and returns nothing.

**Call relations**: A successful payment flow calls this after a top-up charge settles. read_headroom and funded later use the presence of this timestamp to allow the fixed grace amount.

*Call graph*: 2 external calls (execute, update).


##### `configured_auto_topup`  (lines 218–239)

```
async def configured_auto_topup(connection: AsyncConnection, workspace_id: UUID) -> AutoTopup | None
```

**Purpose**: Reads a workspace's automatic refill settings whether or not the balance is currently low enough to trigger a refill. This is useful for showing an admin what they configured.

**Data flow**: It receives a database connection and workspace ID, reads the top-up amount and threshold from the balance row, and returns None if there is no row or no rule. Otherwise it returns an AutoTopup value with the saved settings.

**Call relations**: Admin screens or billing APIs call this to report the current refill rule. It differs from read_auto_topup, which stays silent when the rule exists but has not been reached yet.

*Call graph*: 3 external calls (__init__, execute, select).


##### `read_headroom`  (lines 242–261)

```
async def read_headroom(connection: AsyncConnection, workspace_id: UUID) -> Headroom | None
```

**Purpose**: Reads only the small set of balance numbers needed to decide whether work may start. It avoids the more expensive lifetime purchase totals because this check can happen before every model round.

**Data flow**: It receives a database connection and workspace ID, reads current balance, reserve, and top-up verification status. If no row exists, it notes that absence in the local cache and returns None; otherwise it returns Headroom with balance, reserve, and either zero grace or the fixed verified-card grace.

**Call relations**: The balance gate uses this kind of read in the hot path before model work begins. When it finds no row, it calls _note_absent_balance so later checks can skip needless database work briefly.

*Call graph*: calls 1 internal fn (_note_absent_balance); 3 external calls (__init__, execute, select).


##### `funded`  (lines 264–302)

```
def funded(workspace_id: sa.ColumnElement[UUID], own_key_slots: tuple[str, ...]=()) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a SQL condition that answers whether a workspace should be treated as funded for background jobs. It lets jobs avoid doing work for a workspace whose later model calls would only be refused.

**Data flow**: It receives a SQL expression naming a workspace ID and, optionally, credential slots that count as the workspace using its own key. It builds and returns a database predicate that is true when there is no blocking balance row, or when the balance is above the reserve line after grace, or when the workspace still has positive balance and supplies its own key.

**Call relations**: Background jobs that sync or process workspace data can include this predicate before opening more work. It mirrors the admission gate's funding logic so queued background activity and live model calls agree about when a workspace is held.

*Call graph*: 6 external calls (and_, case, exists, literal, select, true).


##### `recent_purchases`  (lines 316–349)

```
async def recent_purchases(connection: AsyncConnection, workspace_id: UUID, limit: int) -> tuple[Purchase, ...]
```

**Purpose**: Returns the newest balance credits for a workspace, up to a requested limit. This supports billing screens that show where the current credit came from without loading an unlimited history.

**Data flow**: It receives a database connection, workspace ID, and maximum number of purchases. It queries purchase rows for that workspace, orders newest first with a stable tie-breaker, and returns Purchase objects containing granted amount, charged amount, and creation time.

**Call relations**: Admin or operator views call this when displaying purchase history. read_balance separately computes lifetime totals from the same purchase table.

*Call graph*: 3 external calls (__init__, execute, select).


##### `read_balance`  (lines 352–382)

```
async def read_balance(connection: AsyncConnection, workspace_id: UUID) -> Balance | None
```

**Purpose**: Reads the full balance picture for a workspace: current remaining credit, reserve, total credit ever granted, total money charged, and most recent purchase time. This is for humans or operators, not the fastest admission check.

**Data flow**: It receives a database connection and workspace ID, first reads the current balance row, and if no row exists records that absence in the local cache and returns None. If a row exists, it sums all purchase rows for that workspace and returns a Balance object combining the current row with those lifetime totals.

**Call relations**: Billing screens and administrative tools call this when they need the full account picture. It calls _note_absent_balance on missing rows, while faster gate checks normally use read_headroom instead.

*Call graph*: calls 1 internal fn (_note_absent_balance); 3 external calls (__init__, execute, select).


##### `credit`  (lines 385–443)

```
async def credit(connection: AsyncConnection, workspace_id: UUID, granted_micro_usd: int, charged_micro_usd: int, reference: str) -> bool
```

**Purpose**: Adds credit to a workspace exactly once for a given reference, such as a payment ID. This prevents duplicated payment deliveries from adding money twice.

**Data flow**: It receives a database connection, workspace ID, granted amount, charged amount, and unique reference. It tries to insert a purchase row; if that reference was already used for the workspace, it returns false. If the purchase is new, it inserts or updates the workspace balance by adding the granted amount, clears any cached absent-balance note, and returns true.

**Call relations**: Payment fulfillment, grants, refunds, or operator crediting flows call this inside their own transaction. After the caller commits and only if this returned true, count_charge can record the charged amount as a metric.

*Call graph*: calls 1 internal fn (_forget_absent_balance); 2 external calls (execute, uuid4).


##### `count_charge`  (lines 446–463)

```
def count_charge(charged_micro_usd: int) -> None
```

**Purpose**: Reports positive charged money to the metrics system after a successful credit has been committed. It deliberately does not count grants, refunds, or failed/rolled-back credits.

**Data flow**: It receives a charged amount in micro-USD. If the amount is zero or negative, it does nothing; if positive, it emits a metric with that amount.

**Call relations**: Callers use this after credit returns true and their transaction has safely committed. It hands the final count to emit_metric so monitoring can track real collected charges without double-counting retries.

*Call graph*: 1 external calls (emit_metric).


##### `debit`  (lines 466–486)

```
async def debit(connection: AsyncConnection, workspace_id: UUID, micro_usd: int) -> int
```

**Purpose**: Subtracts actual usage cost from a workspace balance. It allows the balance to go negative so spending that already happened is still recorded instead of being lost.

**Data flow**: It receives a database connection, workspace ID, and amount to subtract. If the amount is zero, it returns zero; otherwise it updates the balance row by subtracting the amount and returns the amount actually taken, or zero if the workspace had no balance row.

**Call relations**: Usage recording code calls this in the same transaction that records the cost. This keeps the ledger entry and the balance movement together, so they either both happen or both do not.

*Call graph*: 2 external calls (execute, update).


##### `set_reserve`  (lines 489–500)

```
async def set_reserve(connection: AsyncConnection, workspace_id: UUID, reserve_micro_usd: int) -> bool
```

**Purpose**: Sets the minimum headroom a workspace should keep before starting new work. This helps prevent a nearly empty workspace from starting work that will almost immediately run out of credit.

**Data flow**: It receives a database connection, workspace ID, and reserve amount. It updates the existing balance row with the new reserve and timestamp, then returns true if exactly one row was changed.

**Call relations**: Billing configuration code calls this when reserve policy changes for a workspace. read_headroom and funded later use the stored reserve when deciding whether more work may begin.

*Call graph*: 2 external calls (execute, update).


### Commercial integrations
The Metronome extension connects workspace usage and prepaid billing flows to external reporting, Stripe payments, admin tools, and status pages.

### `extensions/metronome/ufo_ext_metronome.py`

`domain_logic` · `scheduled jobs, chat tool calls, and billing page requests`

This file is the bridge between UFO’s internal accounting and two outside services: Metronome, which rates and records usage, and Stripe, which stores cards and collects prepaid top-ups. Without it, settled usage would stay inside UFO and never appear in Metronome, admins could not self-serve payment setup, and automatic balance refills would not happen.

The usage side works like a careful mailroom. UFO core creates frozen “usage export” intents for settled ledger changes. This file reads those pending intents in batches, turns each into a Metronome event, sends the batch, and only then marks the intents as done. If the process crashes after sending but before marking them done, the next run sends the exact same event again, using a stable transaction id, so Metronome can safely ignore the duplicate.

The billing side is deliberately prepaid. Stripe is used to save a payment method and charge top-ups into UFO’s own balance system. Metronome records and rates usage, but this module does not ask Metronome to collect money, avoiding double charging.

Admins interact through a chat tool called `manage_billing`, and there is also a small billing HTTP route that can show balance, reserve, recent purchases, saved card details, and autopay settings even when normal turns are blocked by low credit.

#### Function details

##### `StripeError.__init__`  (lines 190–192)

```
def __init__(self, message: str, status: int=0) -> None
```

**Purpose**: Creates an error object for failed Stripe calls, while remembering the HTTP status code Stripe returned. The status matters because later code treats a declined card differently from a broken request.

**Data flow**: It receives an error message and an optional status code. It stores the message in the normal exception machinery and saves the status on the object, so callers can inspect it later.

**Call relations**: The shared Stripe request helper creates this when Stripe replies with an unsuccessful status. Balance top-up code later uses the stored status to tell apart a card decline, an in-flight charge, and a real failure.

*Call graph*: called by 1 (_stripe).


##### `UsageShipper.run`  (lines 222–248)

```
async def run(self) -> None
```

**Purpose**: Sends one workspace’s settled usage to Metronome in safe batches. It is careful to send before acknowledging, so usage is not lost if the process stops halfway through.

**Data flow**: It reads the Metronome bearer token, finds the workspace’s fixed backfill floor, then repeatedly asks core for pending usage exports. For each batch, it warns if the usage is getting too old, confirms the Metronome customer alias, converts exports into event dictionaries, posts them, logs the shipment, and finally marks those exports as acknowledged.

**Call relations**: The scheduled `_ship` job creates a `UsageShipper` and calls this method. Inside the run it relies on `_floor`, `_note_usage_aging_out`, `_ensure_metronome_customer`, `_events`, and `_ingest` to move from internal ledger exports to accepted Metronome events.

*Call graph*: calls 6 internal fn (_events, _floor, _note_usage_aging_out, _ensure_metronome_customer, _ingest, _require_env); 1 external calls (log).


##### `UsageShipper._floor`  (lines 250–260)

```
async def _floor(self) -> datetime
```

**Purpose**: Finds the earliest usage time this workspace is allowed to ship. On the first run it sets a fixed floor a few days in the past, which limits the first backfill but does not silently move forward later.

**Data flow**: It reads a stored timestamp from the extension store. If none exists, it writes the current time minus the configured backfill window and returns that; otherwise it parses and returns the stored timestamp.

**Call relations**: UsageShipper.run calls this before reading pending exports. That means the job always asks core for exports using the same workspace-specific starting line.

*Call graph*: called by 1 (run); 3 external calls (fromisoformat, now, timedelta).


##### `UsageShipper._note_usage_aging_out`  (lines 262–283)

```
def _note_usage_aging_out(self, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: Warns operators when unsent usage is older than Metronome’s backdating window. This does not fix the backlog, but it makes potential revenue or reporting loss visible before it is silent.

**Data flow**: It receives a batch of usage exports, finds the oldest event time, compares it with the allowed backfill window, and writes a warning if the oldest item is too old.

**Call relations**: UsageShipper.run calls this after reading a batch and before shipping it. It uses `_rfc3339` to print a readable timestamp in the warning.

*Call graph*: calls 1 internal fn (_rfc3339); called by 1 (run); 3 external calls (now, timedelta, warn).


##### `UsageShipper._events`  (lines 285–304)

```
def _events(self, exports: tuple[UsageExport, ...]) -> list[dict[str, object]]
```

**Purpose**: Turns UFO usage export objects into the JSON-shaped events Metronome expects. It preserves important labels, such as model, dimension, price, turn id, and whether the workspace used its own provider key.

**Data flow**: It receives frozen usage exports and reads the workspace id from the context. It produces a list of event dictionaries with deterministic transaction ids and stringified properties ready for the Metronome ingest API.

**Call relations**: UsageShipper.run calls this immediately before `_ingest`. It uses `_rfc3339` so event timestamps are sent in a standard text form.

*Call graph*: calls 1 internal fn (_rfc3339); called by 1 (run).


##### `_ship`  (lines 307–308)

```
async def _ship(ctx: ExtensionContext) -> None
```

**Purpose**: Small scheduled-job wrapper that starts usage shipping for one workspace. It exists so the extension manifest can name a simple job handler.

**Data flow**: It receives an extension context, creates a `UsageShipper` using the configured test or production transport, and waits for its run to finish. It returns nothing directly.

**Call relations**: The manifest registers this as the handler for the usage shipping job. Its only job is to hand control to `UsageShipper.run`.

*Call graph*: 1 external calls (__init__).


##### `BillingConfig.from_env`  (lines 325–340)

```
def from_env(cls) -> 'BillingConfig'
```

**Purpose**: Reads the Stripe settings required for billing work. It fails early and clearly if the deployment is missing required environment variables.

**Data flow**: It looks in the process environment for the Stripe secret key and the Stripe billing portal configuration id. If either is absent it raises an error naming all missing settings; otherwise it returns a validated `BillingConfig` object.

**Call relations**: Billing tool calls, billing-page reads, and top-up jobs call this before talking to Stripe. That prevents half-started Stripe work when the deployment is not fully configured.


##### `_billing_record`  (lines 353–355)

```
async def _billing_record(ctx: ExtensionContext) -> BillingRecord | None
```

**Purpose**: Reads the saved Stripe customer id for the current workspace, if one has already been created. This is the local pointer that ties a UFO workspace to a Stripe Customer.

**Data flow**: It reads the billing record from the extension store. If nothing is stored, it returns `None`; otherwise it validates the stored data and returns a `BillingRecord` containing the Stripe customer id.

**Call relations**: Billing status, portal creation, autopay setup, top-up runs, and the billing projection route all call this when they need to know whether the workspace already has a Stripe Customer.

*Call graph*: called by 5 (run, _billing_autopay, _billing_portal, _billing_projection, _billing_status).


##### `manage_billing`  (lines 378–387)

```
async def manage_billing(ctx: ToolContext, args: ManageBillingInput) -> ToolResult
```

**Purpose**: Implements the chat-facing billing action for workspace admins. Depending on the requested operation, it reports status, opens the Stripe portal, or sets automatic refills.

**Data flow**: It receives the tool context and parsed input. It first confirms the speaker is an admin, loads billing configuration, then dispatches to the requested operation and returns that operation’s tool result.

**Call relations**: The tool definition in this file points to this function. It delegates the actual work to `_billing_status`, `_billing_portal`, or `_billing_autopay` after `_admin_billing` approves the caller.

*Call graph*: calls 4 internal fn (_admin_billing, _billing_autopay, _billing_portal, _billing_status).


##### `_billing_autopay`  (lines 390–421)

```
async def _billing_autopay(ext: ExtensionContext, config: BillingConfig, args: ManageBillingInput) -> ToolResult
```

**Purpose**: Sets or stops automatic balance refills from the workspace’s saved card. It requires both a refill amount and a threshold together, because a partial rule would be ambiguous.

**Data flow**: It receives the extension context, Stripe configuration, and user input. If amounts are provided, it checks that a Stripe customer and default payment method exist, converts whole dollars into micro-dollars, writes the auto-top-up rule in core accounting, clears any previous refusal marker, bumps the attempt marker, logs the change, and returns the new setting as JSON text.

**Call relations**: manage_billing calls this for the `autopay` operation. It reads `_billing_record`, checks `_default_payment_method`, writes through `set_auto_topup`, and formats its response with `_text_result`.

*Call graph*: calls 4 internal fn (transaction, _billing_record, _default_payment_method, _text_result); called by 1 (manage_billing); 2 external calls (set_auto_topup, log).


##### `_admin_billing`  (lines 424–428)

```
async def _admin_billing(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Checks that the person using the billing tool is a workspace admin. This keeps billing actions from being run by ordinary members or outsiders.

**Data flow**: It receives a tool context and asks the runtime to require a speaking admin for the billing action. If approved, it returns the extension context; if not, it raises an admin-required error.

**Call relations**: manage_billing calls this before doing any billing operation. The returned extension context is then passed to the status, portal, or autopay helper.

*Call graph*: calls 1 internal fn (require_speaking_admin); called by 1 (manage_billing); 1 external calls (__init__).


##### `_billing_status`  (lines 431–455)

```
async def _billing_status(ext: ExtensionContext, config: BillingConfig) -> ToolResult
```

**Purpose**: Builds the chat response for current billing status: whether a card is on file and what the workspace balance and reserve look like. It reads live card state from Stripe rather than trusting a local flag.

**Data flow**: It reads the workspace balance from core accounting, reads the stored Stripe customer id if present, asks Stripe whether that customer has a default payment method, and returns a JSON text result with card presence and balance fields.

**Call relations**: manage_billing calls this for the `status` operation. It uses `_billing_record`, `_default_payment_method`, and `_text_result` to combine core balance data with Stripe card data.

*Call graph*: calls 4 internal fn (transaction, _billing_record, _default_payment_method, _text_result); called by 1 (manage_billing); 1 external calls (read_balance).


##### `_billing_portal`  (lines 458–480)

```
async def _billing_portal(ext: ExtensionContext, config: BillingConfig) -> ToolResult
```

**Purpose**: Creates a short-lived Stripe Customer Portal link for an admin. This is how admins save or change a payment method and view billing details outside the chat turn.

**Data flow**: It checks whether the workspace already has a Stripe Customer. If not, it creates one and stores the customer id. Then it asks Stripe for a portal session URL with a return link back to UFO and returns that URL plus the customer id as JSON text.

**Call relations**: manage_billing calls this for the `portal` operation. It may call `_stripe_customer` to create the Stripe Customer, then `_portal_session` to get the URL, and `_text_result` to package the answer.

*Call graph*: calls 5 internal fn (home_url, _billing_record, _portal_session, _stripe_customer, _text_result); called by 1 (manage_billing); 2 external calls (__init__, log).


##### `_text_result`  (lines 483–484)

```
def _text_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: Wraps a small JSON payload as a tool result the chat system can return. It gives tool callers structured data inside plain text content.

**Data flow**: It receives a dictionary, serializes it to a JSON string, puts that string in a text content object, and returns a tool result containing it.

**Call relations**: The billing status, portal, and autopay helpers all use this as their final packaging step before returning to `manage_billing`.

*Call graph*: called by 3 (_billing_autopay, _billing_portal, _billing_status); 3 external calls (__init__, __init__, dumps).


##### `_require_env`  (lines 498–502)

```
def _require_env(name: str) -> str
```

**Purpose**: Reads one required environment variable and fails with a clear message if it is missing. It is used for settings that the extension cannot safely work around.

**Data flow**: It receives an environment variable name, looks up its value, and returns the value if present. If the value is empty or missing, it raises an error explaining which setting is required.

**Call relations**: UsageShipper.run calls this before touching pending usage exports. That order matters because minting export intents without a Metronome token would create a growing backlog the deployment cannot send.

*Call graph*: called by 1 (run).


##### `_stripe_customer`  (lines 505–522)

```
async def _stripe_customer(config: BillingConfig, workspace_id: UUID, transport: httpx.AsyncBaseTransport | None) -> str
```

**Purpose**: Creates or retrieves the one Stripe Customer intended for a workspace. It uses a stable idempotency key, meaning repeated create attempts settle on the same customer instead of making duplicates.

**Data flow**: It receives Stripe configuration, a workspace id, and an optional HTTP transport. It posts a customer creation request to Stripe with workspace metadata, then extracts and returns the customer id from Stripe’s response.

**Call relations**: _billing_portal calls this when an admin opens the portal for a workspace that has no saved billing record yet. It relies on `_stripe` for the HTTP call and `_as_str` to validate the returned id.

*Call graph*: calls 2 internal fn (_as_str, _stripe); called by 1 (_billing_portal).


##### `_portal_session`  (lines 525–550)

```
async def _portal_session(config: BillingConfig, customer_id: str, flow: str | None, transport: httpx.AsyncBaseTransport | None, return_url: str | None=None) -> str
```

**Purpose**: Asks Stripe for a temporary Customer Portal URL. The portal lets an admin work with payment methods, invoices, and billing details under the deployment’s configured Stripe rules.

**Data flow**: It receives Stripe configuration, a customer id, an optional flow type, an optional transport, and an optional return URL. It builds the Stripe form data, posts it, validates that the response contains a URL, and returns that URL.

**Call relations**: _billing_portal calls this after it knows the workspace’s Stripe Customer id. It uses `_stripe` to contact Stripe and `_as_str` to make sure the provider returned a usable URL.

*Call graph*: calls 2 internal fn (_as_str, _stripe); called by 1 (_billing_portal).


##### `_default_payment_method`  (lines 553–565)

```
async def _default_payment_method(config: BillingConfig, customer_id: str, transport: httpx.AsyncBaseTransport | None) -> str | None
```

**Purpose**: Checks whether a Stripe Customer has a default payment method. This answers whether automatic off-session charges have a card or payment source to use.

**Data flow**: It receives Stripe configuration, a customer id, and an optional transport. It fetches the customer from Stripe, looks inside invoice settings for `default_payment_method`, and returns that id or `None`.

**Call relations**: Autopay setup, billing status, balance top-ups, and card display all call this. `_card_on_file` builds on it when it needs human-friendly card details.

*Call graph*: calls 1 internal fn (_stripe); called by 4 (run, _billing_autopay, _billing_status, _card_on_file).


##### `_card_on_file`  (lines 580–595)

```
async def _card_on_file(config: BillingConfig, customer_id: str, transport: httpx.AsyncBaseTransport | None) -> CardOnFile | None
```

**Purpose**: Reads the recognizable card details for the workspace’s default payment method: brand and last four digits. This is for display, so an admin can tell which card will be charged.

**Data flow**: It first asks `_default_payment_method` for the default payment method id. If none exists it returns `None`; otherwise it fetches that payment method from Stripe and, if it is a card, returns a `CardOnFile` with brand and last four digits.

**Call relations**: _billing_projection calls this while building the billing page. It uses `_stripe` for the payment method lookup and returns display data rather than data needed to charge.

*Call graph*: calls 2 internal fn (_default_payment_method, _stripe); called by 1 (_billing_projection); 1 external calls (__init__).


##### `_stripe`  (lines 598–619)

```
async def _stripe(config: BillingConfig, method: str, path: str, transport: httpx.AsyncBaseTransport | None, data: dict[str, str] | None=None, idempotency_key: str | None=None) -> dict[str, object]
```

**Purpose**: Central helper for making Stripe API calls. It adds authentication, pins the Stripe API version, applies timeouts, and turns failed responses into `StripeError` exceptions.

**Data flow**: It receives configuration, HTTP method, API path, optional form data, optional idempotency key, and optional transport. It sends the request to Stripe, raises `StripeError` on a non-success response, and returns the parsed JSON body on success.

**Call relations**: All Stripe-specific helpers use this: customer creation, portal sessions, payment method reads, card display reads, and top-up charges. Keeping the call logic here gives them one consistent way to talk to Stripe.

*Call graph*: calls 1 internal fn (__init__); called by 5 (_charge, _card_on_file, _default_payment_method, _portal_session, _stripe_customer); 1 external calls (AsyncClient).


##### `_as_str`  (lines 622–626)

```
def _as_str(value: object, field: str) -> str
```

**Purpose**: Validates that a provider response field is a non-empty string. It prevents later code from treating missing or malformed provider data as a valid id or URL.

**Data flow**: It receives an arbitrary value and a field name for error messages. If the value is a non-empty string it returns it; otherwise it raises a clear validation error.

**Call relations**: _stripe_customer uses this for Stripe customer ids, and `_portal_session` uses it for Stripe portal URLs. It is a small guard at the edge where external data enters the system.

*Call graph*: called by 2 (_portal_session, _stripe_customer).


##### `_metronome_fault`  (lines 629–640)

```
def _metronome_fault(call: str, response: httpx.Response) -> str
```

**Purpose**: Builds a safe, useful error message for a failed Metronome call. It includes the call name, status code, and Metronome’s short message without storing the whole response body.

**Data flow**: It receives a label for the Metronome call and the HTTP response. It tries to read a JSON error body, extracts a `message` if present, and returns a concise text fault.

**Call relations**: Customer lookup, customer creation, and ingest calls use this when Metronome refuses a request. The resulting message is raised through `MetronomeError` so the scheduled job records a clear fault.

*Call graph*: called by 3 (_customer_by_alias, _ensure_metronome_customer, _ingest); 1 external calls (json).


##### `_ensure_metronome_customer`  (lines 643–692)

```
async def _ensure_metronome_customer(ctx: ExtensionContext, token: str, transport: httpx.AsyncBaseTransport | None) -> None
```

**Purpose**: Makes sure Metronome has a live customer whose ingest alias is the workspace id. Without that alias, usage events could be accepted by Metronome but not attached to the right customer, which would silently lose reporting.

**Data flow**: It receives the extension context, Metronome bearer token, and optional transport. It looks up a customer by the workspace alias, creates one if missing, handles alias conflicts carefully, raises if the token cannot confirm customer state, and logs successful creation.

**Call relations**: UsageShipper.run calls this once before sending a batch. It depends on `_customer_by_alias` for lookup and `_metronome_fault` for readable errors; only after it succeeds does the shipper call `_ingest`.

*Call graph*: calls 2 internal fn (_customer_by_alias, _metronome_fault); called by 1 (run); 4 external calls (__init__, __init__, AsyncClient, log).


##### `_customer_by_alias`  (lines 702–717)

```
async def _customer_by_alias(http: httpx.AsyncClient, headers: dict[str, str], alias: str) -> str | None
```

**Purpose**: Looks up the live Metronome customer that owns a given ingest alias. It returns `None` when no readable live customer is found.

**Data flow**: It receives an HTTP client, request headers, and an alias string. It calls Metronome’s customer list endpoint filtered by alias, raises clear errors for permission or provider failures, and returns the first customer id if present.

**Call relations**: _ensure_metronome_customer uses this before trying to create a customer and again after a conflict. That second read helps distinguish a harmless race from an alias held by something the token cannot read.

*Call graph*: calls 1 internal fn (_metronome_fault); called by 1 (_ensure_metronome_customer); 3 external calls (__init__, __init__, get).


##### `BalanceTopup.run`  (lines 736–819)

```
async def run(self) -> None
```

**Purpose**: Refills one workspace’s prepaid balance when core says it has fallen below its configured refill line. It charges the saved Stripe payment method without a member being present, then credits the workspace balance only after the charge succeeds.

**Data flow**: It reads the workspace’s auto-top-up rule, skips if none exists or if a recent no-card or decline wait period is still active, loads Stripe configuration, checks for a billing record and default payment method, calculates an idempotent attempt key, asks `_charge` to move the money, records refusal markers on decline, and credits the workspace balance on success.

**Call relations**: The scheduled `_top_up` job creates `BalanceTopup` and calls this. It uses `_billing_record`, `_default_payment_method`, `_charge`, and core balance functions such as `credit` and `mark_topup_verified` to connect Stripe payment to UFO balance.

*Call graph*: calls 3 internal fn (_charge, _billing_record, _default_payment_method); 9 external calls (fromisoformat, now, count_charge, credit, mark_topup_verified, read_auto_topup, read_balance, log, warn).


##### `BalanceTopup._charge`  (lines 821–878)

```
async def _charge(self, config: BillingConfig, customer_id: str, payment_method: str, wanted: AutoTopup, workspace_id: UUID, attempt: str) -> str | None
```

**Purpose**: Creates and confirms a Stripe PaymentIntent for an automatic balance top-up. It returns the payment intent id only when the money actually succeeded.

**Data flow**: It receives Stripe configuration, customer id, payment method id, the desired top-up rule, workspace id, and attempt key. It converts micro-dollars to cents, sends a confirmed off-session payment request to Stripe, treats a 402 as a card decline, treats a conflict as an in-flight earlier request, and returns the intent id only for a succeeded payment.

**Call relations**: BalanceTopup.run calls this when it has confirmed there is a top-up rule and card. It uses `_stripe` for the provider call and reports declines with warnings so the caller can decide whether to wait or credit the balance.

*Call graph*: calls 1 internal fn (_stripe); called by 1 (run); 2 external calls (__init__, warn).


##### `_top_up`  (lines 881–882)

```
async def _top_up(ctx: ExtensionContext) -> None
```

**Purpose**: Small scheduled-job wrapper that runs the balance top-up process for one workspace. It exists so the manifest can register a simple handler.

**Data flow**: It receives an extension context, creates a `BalanceTopup` with the configured billing transport, and waits for it to finish. It returns no direct value.

**Call relations**: The manifest registers this as the handler for the balance top-up job. It hands all real work to `BalanceTopup.run`.

*Call graph*: 1 external calls (__init__).


##### `_ingest`  (lines 885–893)

```
async def _ingest(token: str, events: list[dict[str, object]], transport: httpx.AsyncBaseTransport | None) -> None
```

**Purpose**: Posts prepared usage events to Metronome’s ingest endpoint. If Metronome refuses them, it raises an error so the shipper does not acknowledge the exports.

**Data flow**: It receives the bearer token, a list of event dictionaries, and optional transport. It sends the events as JSON with authorization, checks the response, and either returns successfully or raises `MetronomeError` with a readable fault message.

**Call relations**: UsageShipper.run calls this after confirming the customer alias and building events. It uses `_metronome_fault` when the provider response is not successful.

*Call graph*: calls 1 internal fn (_metronome_fault); called by 1 (run); 2 external calls (__init__, AsyncClient).


##### `_rfc3339`  (lines 896–898)

```
def _rfc3339(moment: datetime) -> str
```

**Purpose**: Formats a Python datetime as an ISO/RFC3339-style timestamp string. If the time has no timezone, it treats it as UTC.

**Data flow**: It receives a datetime. It keeps timezone-aware values as they are, adds UTC to timezone-missing values, and returns the timestamp as text.

**Call relations**: UsageShipper._events uses this for Metronome event timestamps, and UsageShipper._note_usage_aging_out uses it in warnings. It keeps time formatting consistent in both provider calls and logs.

*Call graph*: called by 2 (_events, _note_usage_aging_out); 1 external calls (replace).


##### `_billing_request_workspace`  (lines 907–912)

```
def _billing_request_workspace(request: Request) -> UUID | None
```

**Purpose**: Identifies which workspace a billing-page request belongs to by reading the session cookie. If it cannot find a workspace claim, the route should not be bound to any workspace.

**Data flow**: It receives an HTTP request, reads the session cookie, asks the bearer-token helper for the workspace claim, and returns a workspace UUID or `None`.

**Call relations**: The manifest attaches this as the billing route’s identify function. The runtime uses its answer before calling `_billing_projection`, which keeps the billing page from being open to anonymous requests.

*Call graph*: 1 external calls (workspace_claim).


##### `_billing_projection`  (lines 915–982)

```
async def _billing_projection(ext: ExtensionContext, request: Request) -> Response
```

**Purpose**: Builds the JSON data shown on the billing page: balance limits, card display, autopay setting, and recent purchases. This page is read-only and remains useful even when low balance blocks normal chat turns.

**Data flow**: It verifies the session cookie for the bound workspace, checks that the email belongs to an admin member, reads headroom, balance, autopay, and purchases from core accounting, then optionally reads card details from Stripe. It returns a JSON response with either an error, an unlimited marker, or the full billing projection.

**Call relations**: The billing HTTP route calls this after `_billing_request_workspace` identifies the workspace. It combines core accounting reads with `_billing_record` and `_card_on_file`; if Stripe card lookup fails, it still returns the local balance information and marks the card as unread.

*Call graph*: calls 3 internal fn (transaction, _billing_record, _card_on_file); 9 external calls (configured_auto_topup, read_balance, read_headroom, recent_purchases, verify_token, JSONResponse, warn, member_by_email, member_is_admin).


##### `manifest`  (lines 985–1023)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the UFO runtime: its name, version, chat tool, scheduled jobs, HTTP route, prompt text, and credential slot. This is how the rest of the system discovers what the file provides.

**Data flow**: It constructs and returns a `Manifest` object. The manifest includes the manage-billing tool, the usage-shipping job for metered workspaces, the balance-top-up job for workspaces needing refills, the billing GET route, the prompt instructions, and the Anthropic bring-your-own-key credential slot.

**Call relations**: The extension loader calls this when registering the extension. The objects it returns point later runtime activity to `_ship`, `_top_up`, `_billing_projection`, `_billing_request_workspace`, and `manage_billing`.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, metered_workspaces, topping_up_workspaces).

## 📊 State Registers Touched

- `reg-effective-config` — The merged service settings that tell the process how this deployment should run.
- `reg-persistence-handles` — The shared database and blob-storage connections used to read and save durable system data.
- `reg-model-catalog` — The live menu of AI models, their capabilities, providers, and calling rules.
- `reg-workspace-directory` — The shared record of workspaces, members, seats, admins, invitations, and onboarding status.
- `reg-runtime-authority` — The current workspace, agent, and member identity under which work is allowed to act.
- `reg-turn-queue-state` — The durable state of conversation turns, including pending, running, paused, cancelled, and finished work.
- `reg-usage-ledger-balance` — The money and usage ledger that tracks costs, prepaid balances, limits, exports, and billing status.
- `reg-telemetry-context` — The shared trace, metric, log, health, and redaction context used to observe work across the system.
- `reg-turn-billing-snapshot` — Per-turn frozen billing identity and BYOK attempt state captured before execution and consumed later for stable accounting.
- `reg-turn-resource-budget` — Per-turn context-window, token, image, reasoning, and cost/resource budgets derived before execution and consumed by prompt assembly, model calls, tools, and accounting.
- `reg-rate-limit-buckets` — Shared throttling counters, leases, and cooldown state for ingress, provider/model calls, connector actions, and background workers, separate from spend-cap accounting.

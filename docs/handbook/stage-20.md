# Cross-cutting accounting, billing, usage export, and spend reporting  `stage-20` (cross-cutting infrastructure)

This stage is the system’s behind-the-scenes cash register. It runs alongside normal work, before and after tasks, to measure what was used, check whether spending is allowed, and prepare records for billing and reports.

The accounting file is the main ledger. It records costs from model tokens, sandbox work, image and video generation, network use, connector activity, and proxy credentials. It can stop work before it starts if a spend cap would be exceeded, and it builds reports from both live and saved usage records.

The balance file focuses on prepaid funds for a workspace. It stores balances in micro-USD, meaning tiny fractions of a dollar, so calculations stay exact. It also prevents the same payment credit from being counted twice.

The pricing file is the price tag maker for model usage. It turns token counts into costs and saves a fingerprint of the price table, so old bills can be traced back to the exact prices used.

The Metronome extension connects these records to outside billing tools. It exports daily usage and member counts, links Stripe payment setup, and lets admins manage billing from chat.

## Files in this stage

### Usage ledger
Core accounting records usage, checks spend limits, exports billable events, and builds spend reports.

### `core/src/ufo/accounting.py`

`domain_logic` · `cross-cutting: active during usage recording, turn admission, billing export jobs, and spend report reads`

This file is the accounting desk for the system. Whenever a turn uses a model, a sandbox reaches the network, or media is generated, this code writes a clear entry into the ledger table. The ledger is like a checkbook: every row says what was used, how much it cost in micro-dollars, which workspace it belongs to, and sometimes which turn, model, or price table produced it. Without this file, usage could be missed, counted twice, or impossible to explain to customers.

It also protects workspaces from overspending. Before a turn runs, SpendEvaluator checks whether the workspace balance is held and whether any spend cap applies to the workspace, member, or agent. It then returns a simple answer: allow, park for later, or reject.

For external billing, the file freezes ledger growth into export rows. This matters because billing delivery can be retried; the frozen export makes each retry identical instead of recalculating changing data.

Finally, SpendRollup reads the same ledger back into human reports: totals by day, model, member, agent, origin, dimension, and price digest. The important theme is consistency: one ledger feeds caps, billing export, and reporting.

#### Function details

##### `applicable_caps_absent`  (lines 47–53)

```
def applicable_caps_absent(workspace_id: UUID, member_id: UUID | None, agent_id: UUID) -> bool
```

**Purpose**: Quickly answers whether this exact workspace, member, and agent recently had no spend caps applying to them. It is a short-lived shortcut so the common case, where no caps exist, can avoid an extra database read.

**Data flow**: It receives a workspace id, optional member id, and agent id. It looks up that exact triple in an in-memory cache and compares the stored expiry time with the current monotonic clock, which is a clock used for measuring elapsed time. It returns true only if the no-cap note is still fresh.

**Call relations**: Other admission code can use this as a fast pre-check before doing the fuller SpendEvaluator path. It does not prove there are no caps forever; it only reuses a recent decision for a few seconds.

*Call graph*: 1 external calls (monotonic).


##### `_note_absent_caps`  (lines 56–65)

```
def _note_absent_caps(key: tuple[UUID, UUID | None, UUID]) -> None
```

**Purpose**: Remembers, briefly, that no spend cap applied to a specific workspace, member, and agent combination. This keeps later checks from repeatedly asking the database when nothing is configured.

**Data flow**: It receives the exact cache key. It checks the current monotonic time, removes expired entries if the cache has grown to its soft limit, and stores a new expiry time a few seconds in the future. It changes only the in-memory cache.

**Call relations**: SpendEvaluator.decide calls this after it asks the database and finds no applicable caps. That later enables applicable_caps_absent to skip work for the same exact triple.

*Call graph*: called by 1 (decide); 1 external calls (monotonic).


##### `_total_tokens`  (lines 68–75)

```
def _total_tokens(usage: Usage) -> int
```

**Purpose**: Adds together all token counters in a Usage object. It gives the total billable token amount for a model call.

**Data flow**: It receives usage details with input, output, cache-read, and cache-write token counts. It sums those numbers and returns one integer total. It does not read or change the database.

**Call relations**: The token-recording functions use it before writing ledger rows. If it returns zero, those functions skip writing because there is nothing to bill.

*Call graph*: called by 3 (record_sandbox_tokens, record_turn_usage, record_workspace_usage).


##### `_prompt_tokens`  (lines 78–86)

```
def _prompt_tokens(usage: Usage) -> int
```

**Purpose**: Counts the prompt-side tokens: the tokens the provider read, whether they were fresh or served from cache. This is used to calculate cache share later.

**Data flow**: It receives a Usage object. It adds input tokens plus cache-read and cache-write tokens, excluding output tokens, and returns that prompt total. The result is stored beside ledger rows so reports can calculate cache percentage from durable data.

**Call relations**: The model usage writers call this when recording host, workspace, and sandbox token usage. read_turn_cost later uses the stored prompt and cache counts to summarize a turn.

*Call graph*: called by 3 (record_sandbox_tokens, record_turn_usage, record_workspace_usage).


##### `record_turn_usage`  (lines 89–130)

```
async def record_turn_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, usage: Usage, attempt: str='', pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: Writes the one token billing entry for a turn attempt. It makes replay safe: if the same attempt is run again, the existing ledger row prevents double billing.

**Data flow**: It receives a database connection, workspace id, turn id, model name, usage counts, an attempt id, and pricing data. It totals the tokens, skips zero usage, derives a stable ledger id, checks whether that row already exists, and inserts a priced ledger row if not. The output is no return value; the durable change is the ledger entry.

**Call relations**: Turn execution code calls this after a model call has real usage to record. It relies on _total_tokens, _prompt_tokens, Pricing.micro_usd, and ledger_id_for to turn raw usage into a stable, priced ledger row.

*Call graph*: calls 3 internal fn (_prompt_tokens, _total_tokens, micro_usd); 4 external calls (execute, insert, select, ledger_id_for).


##### `read_turn_cost`  (lines 144–174)

```
async def read_turn_cost(connection: AsyncConnection, turn_id: UUID, dimension: str) -> TurnCost | None
```

**Purpose**: Reads what a single turn spent for one ledger dimension, such as host tokens or sandbox tokens. It is used when the system needs to show the final cost of a turn.

**Data flow**: It receives a database connection, turn id, and dimension name. It sums matching ledger rows across attempts, totals tokens and micro-dollars, picks the model value, and computes the percent of prompt tokens served from cache. It returns a TurnCost object, or None if nothing was billed.

**Call relations**: This is the read side of the ledger rows written by record_turn_usage and record_sandbox_tokens. It intentionally reads from stored ledger facts rather than from in-memory usage that may no longer exist.

*Call graph*: 3 external calls (__init__, execute, select).


##### `record_workspace_usage`  (lines 177–210)

```
async def record_workspace_usage(connection: AsyncConnection, workspace_id: UUID, model: str, usage: Usage, pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: Records a model call made by a background job for the whole workspace, not for any specific turn. This makes workspace-level spend complete without pretending the cost belongs to a member or agent.

**Data flow**: It receives a connection, workspace id, model, usage counts, and pricing. It totals tokens, skips zero usage, prices the call, and inserts a new ledger row with no turn id. It returns nothing and leaves a durable workspace-level charge.

**Call relations**: Background job code calls this when it uses a model outside a conversation turn. It shares the same token and pricing helpers as record_turn_usage, so reports and workspace caps see comparable costs.

*Call graph*: calls 3 internal fn (_prompt_tokens, _total_tokens, micro_usd); 3 external calls (execute, insert, uuid4).


##### `record_egress_request`  (lines 213–242)

```
async def record_egress_request(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, amount: int=1) -> None
```

**Purpose**: Counts sandbox network egress requests for a turn. These requests are metered as counts, not charged dollars, so operators can report them without mixing them into model token billing.

**Data flow**: It receives a connection, workspace id, turn id, and request count. It builds a stable ledger id for the turn's egress row, inserts it if missing, or atomically adds to the amount if it already exists. It returns nothing and updates the ledger count.

**Call relations**: The sandbox egress proxy calls this as requests are observed. It uses ledger_id_for to accumulate all egress for the turn into one row, separate from token usage.

*Call graph*: 2 external calls (execute, ledger_id_for).


##### `record_probe_egress_request`  (lines 245–271)

```
async def record_probe_egress_request(connection: AsyncConnection, workspace_id: UUID, amount: int=1) -> None
```

**Purpose**: Counts network egress from an off-turn probe. Since a probe is not part of a turn, the row belongs only to the workspace.

**Data flow**: It receives a connection, workspace id, and request count. It inserts a fresh ledger row with no turn id, zero price, and the egress dimension. It returns nothing and creates one durable count row for that batch.

**Call relations**: Probe or proxy code calls this when network use happens outside a normal turn. Like record_workspace_usage, the missing turn id means member and agent reports do not claim it.

*Call graph*: 3 external calls (execute, insert, uuid4).


##### `record_sandbox_tokens`  (lines 274–327)

```
async def record_sandbox_tokens(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, usage: Usage, pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: Records model tokens used from inside the sandbox through the egress proxy. This keeps sandbox-made model calls visible and billed without confusing them with host-side turn calls.

**Data flow**: It receives a connection, workspace id, turn id, model, usage counts, and pricing. It totals and prices the usage, skips zero usage, and then inserts or atomically adds to one sandbox-token ledger row for that turn. It also adds prompt and cache-read counts so cache reporting remains accurate.

**Call relations**: The egress proxy or sandbox metering path calls this when a sandboxed process uses a model. read_turn_cost and SpendRollup later read these rows alongside normal token rows, but under a separate dimension.

*Call graph*: calls 3 internal fn (_prompt_tokens, _total_tokens, micro_usd); 2 external calls (execute, ledger_id_for).


##### `record_image_usage`  (lines 330–349)

```
async def record_image_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, images: int, micro_usd: int) -> None
```

**Purpose**: Records generated image usage for a turn. The caller provides the price because image services do not fit the normal token pricing table.

**Data flow**: It receives a connection, workspace id, turn id, model, image count, and cost in micro-dollars. It passes those values to the shared media writer with the image dimension. It returns nothing; the ledger is updated by the helper.

**Call relations**: Provider extensions call this after image generation. It delegates to _record_media_usage so image and video accounting follow the same accumulation rules.

*Call graph*: calls 1 internal fn (_record_media_usage).


##### `record_video_usage`  (lines 352–366)

```
async def record_video_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, videos: int, micro_usd: int) -> None
```

**Purpose**: Records generated video usage for a turn. Like images, videos are priced by the provider-specific caller rather than by token counters.

**Data flow**: It receives a connection, workspace id, turn id, model, video count, and cost in micro-dollars. It forwards them to the shared media writer with the video dimension. It returns nothing and relies on the helper to update the ledger.

**Call relations**: Video provider code calls this after a video generation charge is known. It shares _record_media_usage with record_image_usage so both media types are stored consistently.

*Call graph*: calls 1 internal fn (_record_media_usage).


##### `_record_media_usage`  (lines 369–401)

```
async def _record_media_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, dimension: str, model: str, amount: int, micro_usd: int) -> None
```

**Purpose**: Shared writer for image and video ledger rows. It accumulates count and cost for a turn without losing updates if several generations happen.

**Data flow**: It receives a connection, workspace id, turn id, dimension, model, amount, and cost. It builds the turn-and-dimension ledger id, inserts a row if missing, or atomically adds the new amount and cost to the existing row. It returns nothing and changes the ledger.

**Call relations**: record_image_usage and record_video_usage call this instead of duplicating the insert-or-add database logic. Reports and caps then see media costs through the same ledger table.

*Call graph*: called by 2 (record_image_usage, record_video_usage); 2 external calls (execute, ledger_id_for).


##### `mint_usage_exports`  (lines 426–535)

```
async def mint_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, floor: datetime, key_slot_for: Callable[[str], str | None]) -> None
```

**Purpose**: Creates frozen export intents for usage that has not yet been shipped to an external billing consumer. This is what makes billing retries safe: the exported delta is fixed once minted.

**Data flow**: It receives a connection, workspace id, consumer name, time floor, and a function that maps model names to credential slots. It reads stored workspace credentials, finds ledger rows that have grown beyond the last export and are settled enough to export, labels whether token usage used the workspace's own key, and inserts export rows keyed so repeats collapse into one. It returns nothing and creates pending export records.

**Call relations**: A billing export job calls this before reading pending exports. It bridges ledger writers and external billing: record_* functions grow ledger rows, then this function freezes the unshipped growth into ledger_export rows.

*Call graph*: 5 external calls (now, timedelta, execute, or_, select).


##### `read_pending_usage_exports`  (lines 538–583)

```
async def read_pending_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, limit: int) -> tuple[UsageExport, ...]
```

**Purpose**: Reads frozen usage exports that have not yet been acknowledged by an external billing consumer. It gives the sender a stable batch to deliver.

**Data flow**: It receives a connection, workspace id, consumer name, and limit. It joins export rows to their ledger rows, calculates the exported delta amount and cost, and returns UsageExport objects in mint order. It does not change the database.

**Call relations**: The export sender calls this after mint_usage_exports. If delivery fails before acknowledgment, calling this again returns the same frozen intents.

*Call graph*: 3 external calls (__init__, execute, select).


##### `ack_usage_exports`  (lines 586–610)

```
async def ack_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: Marks exported usage deltas as acknowledged after the external consumer accepted them. This removes them from future pending reads.

**Data flow**: It receives a connection, workspace id, consumer name, and the exports that were successfully delivered. It builds matching conditions from each export's ledger id and starting amount, then updates those export rows with an acknowledgment time. It returns nothing and changes ledger_export state.

**Call relations**: The export sender calls this only after the outside billing API succeeds. If a crash happens first, read_pending_usage_exports will serve the same frozen exports again for safe retry.

*Call graph*: 3 external calls (execute, or_, update).


##### `metered_workspaces`  (lines 613–616)

```
def metered_workspaces() -> WorkspaceCandidates
```

**Purpose**: Finds candidate workspaces for usage export work. A workspace qualifies if it has ever had ledger activity.

**Data flow**: It builds a candidate query that selects distinct workspace ids from the ledger. It returns a WorkspaceCandidates object rather than running the export itself. No ledger rows are changed.

**Call relations**: Scheduling code can call this to decide which workspaces a billing export job should check. The later per-workspace export path can cheaply do nothing if there is no pending usage.

*Call graph*: 1 external calls (owner_candidates).


##### `SpendEvaluator.decide`  (lines 652–669)

```
async def decide(self, connection: AsyncConnection, pending_micro_usd: int) -> SpendDecision
```

**Purpose**: Decides whether a turn may proceed under the workspace balance and spend caps. Its answer is one of allow, park, or reject, with a message when work is blocked.

**Data flow**: It receives a database connection and an estimated pending cost in micro-dollars. It first checks whether the workspace balance is below reserve, then reads caps that apply to this workspace, member, and agent, sums recent usage for each cap, and compares used plus pending cost against each limit. It returns a SpendDecision and may update the short-lived no-cap cache.

**Call relations**: Turn admission or mid-turn checks call this before spending more money. It coordinates held_below_reserve, _applicable_caps, _used_micro_usd, _note_absent_caps, and _message into one decision.

*Call graph*: calls 4 internal fn (_applicable_caps, _message, _used_micro_usd, _note_absent_caps); 2 external calls (__init__, held_below_reserve).


##### `SpendEvaluator._applicable_caps`  (lines 671–699)

```
async def _applicable_caps(self, connection: AsyncConnection) -> tuple[SpendCap, ...]
```

**Purpose**: Reads the spend caps that apply to this exact turn context. Caps can be for the whole workspace, for the member, or for the agent.

**Data flow**: It uses the evaluator's workspace id, optional member id, and agent id to query the spend_cap table. It turns each matching database row into a SpendCap object and returns them as a tuple. It does not decide whether any cap is breached.

**Call relations**: SpendEvaluator.decide calls this after the balance check. The returned caps are then passed one by one into _used_micro_usd for limit comparison.

*Call graph*: called by 1 (decide); 4 external calls (__init__, execute, or_, select).


##### `SpendEvaluator._used_micro_usd`  (lines 701–727)

```
async def _used_micro_usd(self, connection: AsyncConnection, cap: SpendCap) -> int
```

**Purpose**: Calculates how much money has already been spent inside one cap's rolling time window. This is the number compared against the cap limit.

**Data flow**: It receives a database connection and one SpendCap. It computes the cutoff time from the cap's window length, builds a query for the right scope, and sums priced ledger amounts since that cutoff. It returns the total as an integer number of micro-dollars.

**Call relations**: SpendEvaluator.decide calls this for each applicable cap. Workspace caps read directly by workspace id; member and agent caps join through turns and conversations so the spend is attributed to the right person or agent.

*Call graph*: called by 1 (decide); 4 external calls (now, timedelta, execute, select).


##### `SpendEvaluator._message`  (lines 729–740)

```
def _message(self, outcome: SpendOutcome, breaches: list[SpendCap]) -> str
```

**Purpose**: Creates the user-facing explanation when a spend cap is reached. It chooses the tightest breached cap so the message names the most restrictive limit.

**Data flow**: It receives the outcome, either park or reject, and a list of breached caps. It picks the cap with the smallest dollar limit, formats that limit as dollars, and returns a plain message. It changes nothing.

**Call relations**: SpendEvaluator.decide calls this only after it has found cap breaches. The returned text travels inside the SpendDecision for the caller to show or log.

*Call graph*: called by 1 (decide).


##### `_token_sum`  (lines 857–865)

```
def _token_sum() -> sa.ColumnElement[int]
```

**Purpose**: Builds a database expression that sums only token-like ledger dimensions. It keeps token totals from accidentally including images, videos, or egress counts.

**Data flow**: It creates a SQL expression that adds ledger amounts when the dimension is host tokens or sandbox tokens and treats all other dimensions as zero. The output is a query fragment, not a final number by itself.

**Call relations**: SpendRollup.read, SpendRollup._by_origin, and _usage_details use this expression inside their database queries. It centralizes the definition of what counts as tokens.

*Call graph*: called by 3 (_by_origin, read, _usage_details); 1 external calls (case).


##### `_token_cost_sum`  (lines 868–880)

```
def _token_cost_sum() -> sa.ColumnElement[int]
```

**Purpose**: Builds a database expression that sums the cost of only token-like ledger dimensions. This separates token model cost from other charged dimensions such as media.

**Data flow**: It creates a SQL expression that adds priced micro-dollars only for host tokens and sandbox tokens, using zero for everything else. The result is used inside larger SQL queries.

**Call relations**: SpendRollup.read, SpendRollup._by_origin, and _usage_details call this when building reports. It pairs with _token_sum so token amount and token cost follow the same rules.

*Call graph*: called by 3 (_by_origin, read, _usage_details); 1 external calls (case).


##### `_usage_details`  (lines 883–1007)

```
async def _usage_details(connection: AsyncConnection, source: sa.FromClause, scope: sa.ColumnElement[bool], cutoff: datetime | None, now: datetime) -> UsageDetails
```

**Purpose**: Builds the shared usage detail section for workspace, agent, and member reports. It covers selected-range totals, all-time totals, daily history, model breakdowns, execution breakdowns, and previous-period tokens.

**Data flow**: It receives a connection, a database source to query from, a scope condition, an optional cutoff time, and the current time. It runs several aggregate queries: selected totals, all-time totals, daily buckets, execution labels, model labels, and previous-period tokens if a window is selected. It returns one UsageDetails object with nested total and breakdown objects.

**Call relations**: SpendRollup.read, SpendRollup.read_agent, and SpendRollup.read_member call this after building the right joins and scope for their report. It keeps the common reporting math in one place.

*Call graph*: calls 2 internal fn (_token_cost_sum, _token_sum); called by 3 (read, read_agent, read_member); 9 external calls (__init__, __init__, __init__, __init__, fromisoformat, date, timedelta, execute, select).


##### `SpendRollup.read`  (lines 1018–1121)

```
async def read(self, connection: AsyncConnection, window_seconds: int | None) -> SpendReport
```

**Purpose**: Reads a full workspace spend report. It answers how much the workspace spent overall and breaks that spend down in several useful ways.

**Data flow**: It receives a connection and an optional window length. It computes the time cutoff, sums total cost, groups ledger rows by dimension, member, agent, price digest, and origin, and asks _usage_details for time-series and token details. It returns a SpendReport object.

**Call relations**: Workspace reporting endpoints or dashboards call this. It brings together _token_sum, _token_cost_sum, _by_origin, and _usage_details so the same ledger can be viewed from many angles.

*Call graph*: calls 4 internal fn (_by_origin, _token_cost_sum, _token_sum, _usage_details); 8 external calls (__init__, __init__, __init__, __init__, now, timedelta, execute, select).


##### `SpendRollup._by_origin`  (lines 1123–1188)

```
async def _by_origin(self, connection: AsyncConnection, window: sa.ColumnElement[bool]) -> tuple[OriginTotal, ...]
```

**Purpose**: Groups token spend by the conversation origin where the work began. This matters because subagents may spend money in private follow-up turns, but users want that cost attributed to the original visible surface.

**Data flow**: It receives a connection and an already-built ledger window condition. It starts from turns with token spend, recursively walks parent turns until it finds each root conversation, then groups token amount and token cost by that conversation's surface label. It returns OriginTotal objects, with turn-less usage grouped as workspace jobs.

**Call relations**: SpendRollup.read calls this as one part of the workspace report. It uses _token_sum and _token_cost_sum so only host and sandbox token dimensions count in origin totals.

*Call graph*: calls 2 internal fn (_token_cost_sum, _token_sum); called by 1 (read); 4 external calls (__init__, desc, execute, select).


##### `SpendRollup.read_agent`  (lines 1190–1246)

```
async def read_agent(self, connection: AsyncConnection, agent_id: UUID, window_seconds: int | None) -> AgentSpendReport
```

**Purpose**: Reads spend and usage for one agent, including that agent's cap lines. It leaves workspace-only rows out because they do not belong to an agent turn.

**Data flow**: It receives a connection, agent id, and optional window length. It joins ledger rows to turns, filters to the workspace and agent, groups cost by dimension, reads agent-scoped caps, and asks _usage_details for the detailed usage section. It returns an AgentSpendReport.

**Call relations**: Agent detail pages or APIs call this when showing what one agent has used. It reuses _usage_details for common totals but supplies an agent-specific scope.

*Call graph*: calls 1 internal fn (_usage_details); 7 external calls (__init__, __init__, __init__, now, timedelta, execute, select).


##### `SpendRollup.read_member`  (lines 1248–1306)

```
async def read_member(self, connection: AsyncConnection, member_id: UUID, window_seconds: int | None) -> MemberSpendReport
```

**Purpose**: Reads spend and usage for one member, plus that member's spend caps. It attributes ledger rows to a member through the conversation that contained each turn.

**Data flow**: It receives a connection, member id, and optional window length. It joins ledger to turns and conversations, filters to the workspace and member, groups by dimension, reads member-scoped caps, and asks _usage_details for the detailed usage section. It returns a MemberSpendReport.

**Call relations**: Member billing or admin views call this to show one person's usage without naming other members or agents. It follows the same attribution path used by member spend caps.

*Call graph*: calls 1 internal fn (_usage_details); 7 external calls (__init__, __init__, __init__, now, timedelta, execute, select).


### Prepaid balance controls
Workspace balance tracking records credits and decides when work should pause because funds are too low.

### `core/src/ufo/balance.py`

`domain_logic` · `cross-cutting: balance checks before model work, plus payment crediting and admin balance reads`

This file is the project’s prepaid-balance ledger helper. A workspace can receive credits, spend them elsewhere in the system, and optionally set aside a reserve amount that must remain before new model work begins. Without this file, the system would not have a reliable way to know whether a workspace has enough prepaid funds to continue, or whether a payment event had already been applied.

The key idea is that the current balance is stored as its own database row instead of being recalculated from every purchase each time. That matters because the balance may be checked before every model round, so it needs to be quick. The purchase records still remain as the audit trail: they explain where credits came from and how much was charged.

The file also has a small in-memory shortcut for workspaces that have never had a balance row. If the database recently said “there is no balance for this workspace,” the code remembers that briefly and skips another database read. This is like putting a short-lived sticky note on a desk: useful for avoiding repeated checks, but not trusted forever. When a credit is added, the sticky note is removed.

The main operations are: check whether a workspace is held below its reserve, read the full balance picture for display or administration, add a credit exactly once per reference, and update the reserve.

#### Function details

##### `balance_absent`  (lines 31–37)

```
def balance_absent(workspace_id: UUID) -> bool
```

**Purpose**: Checks the short-lived in-memory note that says a workspace recently had no balance row. It is a speed shortcut, not the source of truth.

**Data flow**: It takes a workspace ID, looks it up in the local `_no_balance` dictionary, and compares the stored expiry time with the current clock. It returns `True` only if the note exists and has not expired; otherwise it returns `False`.

**Call relations**: When `held_below_reserve` is about to decide whether work should pause, it asks this function first. If the function says the workspace recently had no balance, `held_below_reserve` can avoid a database query and treat the workspace as not held.

*Call graph*: called by 1 (held_below_reserve); 1 external calls (monotonic).


##### `_note_absent_balance`  (lines 40–47)

```
def _note_absent_balance(workspace_id: UUID) -> None
```

**Purpose**: Remembers for a few seconds that a workspace has no balance row. This prevents repeated database reads for workspaces that have never been credited.

**Data flow**: It takes a workspace ID, reads the current monotonic clock, removes expired notes if the cache is full, and stores a new expiry time for that workspace. It does not return a value; it changes the in-memory absence cache.

**Call relations**: Both `held_below_reserve` and `read_balance` call this after they ask the database for a balance row and find none. Later, `balance_absent` uses the note to skip another read until the note expires or a credit removes it.

*Call graph*: called by 2 (held_below_reserve, read_balance); 1 external calls (monotonic).


##### `held_below_reserve`  (lines 50–69)

```
async def held_below_reserve(connection: AsyncConnection, workspace_id: UUID) -> bool
```

**Purpose**: Decides whether a workspace’s work should be parked because its current balance is below its required reserve. A reserve is a safety buffer: the workspace must have at least that much prepaid balance before starting more work.

**Data flow**: It receives an async database connection and a workspace ID. First it checks the short-lived absence cache; if the workspace recently had no balance row, it returns `False`. Otherwise it reads the balance and reserve from the database. If no row exists, it records that absence and returns `False`. If a row exists, it compares the balance with the reserve and returns whether the balance is smaller.

**Call relations**: This is the fast gate used before model spending decisions. It leans on `balance_absent` and `_note_absent_balance` to avoid unnecessary database work for uncredited workspaces, and it uses the database only when it needs the actual balance and reserve.

*Call graph*: calls 2 internal fn (_note_absent_balance, balance_absent); 2 external calls (execute, select).


##### `read_balance`  (lines 83–113)

```
async def read_balance(connection: AsyncConnection, workspace_id: UUID) -> Balance | None
```

**Purpose**: Builds the full balance view for a workspace: what is left, what reserve is set, how much has ever been granted, how much has ever been charged, and when the last purchase happened. This is for display, administration, or auditing rather than the fastest per-round check.

**Data flow**: It receives an async database connection and a workspace ID. It first reads the current balance row. If there is none, it notes the absence in the cache and returns `None`. If the row exists, it separately totals all purchase records for that workspace and then returns a `Balance` object containing both the current row and the lifetime purchase totals.

**Call relations**: Unlike `held_below_reserve`, which only needs a quick yes-or-no decision, this function gathers the fuller picture. It calls `_note_absent_balance` when the workspace has no balance row, so later checks can skip needless database reads for a short time.

*Call graph*: calls 1 internal fn (_note_absent_balance); 3 external calls (__init__, execute, select).


##### `credit`  (lines 116–171)

```
async def credit(connection: AsyncConnection, workspace_id: UUID, granted_micro_usd: int, charged_micro_usd: int, reference: str) -> bool
```

**Purpose**: Adds prepaid value to a workspace exactly once for a given reference, such as a payment ID or grant ID. This protects the system from crediting the same payment twice if the same payment event is delivered more than once.

**Data flow**: It receives a database connection, workspace ID, granted amount, charged amount, and reference string. It first tries to insert a purchase record using the workspace plus reference as the uniqueness guard. If that insert does nothing because the reference was already used, it returns `False`. If the insert succeeds, it creates or updates the workspace balance by adding the granted amount, clears any cached “no balance” note for that workspace, and returns `True`.

**Call relations**: Payment fulfillment or grant code calls this inside its own database transaction, so recording the purchase and updating the balance happen together. It writes to both the purchase audit trail and the current balance row, and it removes the absence-cache note so future reserve checks see the new balance.

*Call graph*: 2 external calls (execute, uuid4).


##### `set_reserve`  (lines 174–185)

```
async def set_reserve(connection: AsyncConnection, workspace_id: UUID, reserve_micro_usd: int) -> bool
```

**Purpose**: Changes the minimum balance a workspace must keep before new work may start. It only applies to workspaces that already have a balance row.

**Data flow**: It receives a database connection, workspace ID, and reserve amount. It updates the matching workspace balance row with the new reserve and timestamp. It returns `True` if exactly one row was updated, or `False` if there was no existing balance row to update.

**Call relations**: Administrative or operator-facing code uses this when changing the safety buffer for a workspace. The value it writes is later read by `held_below_reserve`, which uses it to decide whether work should pause.

*Call graph*: 2 external calls (execute, update).


### Pricing model
Model pricing converts token usage into billable costs and fingerprints the price table used for records.

### `core/src/ufo/models/pricing.py`

`domain_logic` · `accounting and usage recording`

When the system uses an AI model, it counts different kinds of tokens: input tokens, output tokens, and cache-related tokens. These token types can cost different amounts. This file is the small pricing engine that turns those counts into a cost in micro-USD, meaning millionths of a US dollar.

The central data shape is `ModelPrice`, which stores the rate for one model. Rates are expressed per million tokens, like a price list at a store. The `Pricing` object then wraps a whole table of model names to prices, plus a digest. A digest is a short cryptographic fingerprint of the price table. It lets the system later prove, “this usage record was billed using exactly this set of rates.”

The file is careful about repeatability. `price_digest` sorts the model names and writes the price table in a compact, predictable JSON form before hashing it. That means the same prices always produce the same fingerprint, even if the input mapping was in a different order.

If a usage record refers to a model that is not in the price table, the code logs a warning and returns zero instead of crashing. That matters for old or historical records: the system can keep running even when it sees a model it no longer knows how to price.

#### Function details

##### `price_digest`  (lines 25–41)

```
def price_digest(prices: Mapping[str, ModelPrice]) -> str
```

**Purpose**: Creates a stable version stamp for a model price table. Someone would use this so billed usage can be tied back to the exact prices that were active at the time.

**Data flow**: It receives a mapping from model names to `ModelPrice` values. It sorts the models, turns the prices into a predictable JSON string, hashes that string with SHA-256, and returns a text value beginning with `sha256:`. It does not change the input table.

**Call relations**: When `pricing_from` builds a `Pricing` object, it calls `price_digest` to create the fingerprint that will travel with the price table. Inside, this function relies on JSON serialization and SHA-256 hashing to make the fingerprint deterministic.

*Call graph*: called by 1 (pricing_from); 2 external calls (sha256, dumps).


##### `usage_priced_micro_usd`  (lines 44–57)

```
def usage_priced_micro_usd(model: str, usage: Usage, prices: Mapping[str, ModelPrice]) -> int
```

**Purpose**: Calculates the cost of one model usage record in micro-USD. It is used when the system needs to convert counted tokens into a billable number.

**Data flow**: It receives a model name, a `Usage` record containing token counts, and a table of prices. It looks up the model's rates, multiplies each token count by the matching rate, adds the results, and divides by one million because the rates are stored per million tokens. It returns the final integer cost. If the model is missing from the table, it logs that fact and returns zero.

**Call relations**: `Pricing.micro_usd` calls this function whenever accounting code asks a `Pricing` object to price usage. If the model is unknown, this function hands the issue to the logging system instead of stopping the accounting flow.

*Call graph*: called by 1 (micro_usd); 1 external calls (log).


##### `Pricing.micro_usd`  (lines 67–68)

```
def micro_usd(self, model: str, usage: Usage) -> int
```

**Purpose**: Provides the convenient method on a `Pricing` table for pricing one usage record. Callers use this instead of passing the internal price mapping around themselves.

**Data flow**: It receives a model name and a `Usage` record. It reads the price table stored inside the `Pricing` object, passes everything to `usage_priced_micro_usd`, and returns that calculated micro-USD cost. It does not modify the `Pricing` object.

**Call relations**: Accounting functions such as `record_sandbox_tokens`, `record_turn_usage`, and `record_workspace_usage` call this method when they are recording usage and need the money amount. This method then delegates the actual arithmetic to `usage_priced_micro_usd`.

*Call graph*: calls 1 internal fn (usage_priced_micro_usd); called by 3 (record_sandbox_tokens, record_turn_usage, record_workspace_usage).


##### `pricing_from`  (lines 71–74)

```
def pricing_from(prices: Mapping[str, ModelPrice]) -> Pricing
```

**Purpose**: Builds a complete immutable-style `Pricing` object from a raw model price table. It is the normal constructor-style helper for creating pricing data with its matching digest.

**Data flow**: It receives a mapping of model names to `ModelPrice` entries. It copies that mapping into a plain dictionary, computes a digest for the copied table, and returns a new `Pricing` object containing both the table and the digest.

**Call relations**: This function is the setup step for pricing. It calls `price_digest` so the new `Pricing` object includes a trustworthy fingerprint, then creates the `Pricing` instance that later accounting code can use through `Pricing.micro_usd`.

*Call graph*: calls 1 internal fn (price_digest); 1 external calls (__init__).


### Billing integrations
Metronome and Stripe integration exports usage and member-count events while supporting chat-based billing setup and status checks.

### `extensions/metronome/ufo_ext_metronome.py`

`orchestration` · `scheduled jobs and chat tool calls`

This extension is the bridge between UFO and two outside services: Metronome, which receives usage and plan information, and Stripe, which collects payment details. Without it, the system could still do work, but it would not reliably report billable usage, report workspace size, or help an admin activate a paid plan.

There are three main flows. First, the usage shipper reads settled usage records that core has already prepared, turns them into Metronome events, sends them in batches, and only marks them done after Metronome accepts them. Each event has a stable transaction id, like a receipt number, so retrying after a crash sends the same event instead of double-counting.

Second, the seat shipper sends one daily snapshot of how many members a workspace has. This is informational, not enforcement: it does not decide who can use the product.

Third, the billing flow starts from chat. An admin can ask to set up billing, and the tool creates or reuses a Stripe Customer, returns a short-lived Stripe portal link, and records the intended Metronome package. A scheduled activation job later checks whether Stripe has a saved payment method, creates or finds the matching Metronome customer and contract, then notifies the original conversation once. The file is careful to use durable identities, so retries recover existing provider objects rather than creating duplicates.

#### Function details

##### `UsageShipper.run`  (lines 158–173)

```
async def run(self) -> None
```

**Purpose**: Sends one workspace's pending settled usage to Metronome. It is designed so that if the process crashes, the next run can safely resend the same events without double billing.

**Data flow**: It reads the Metronome token from the environment, finds the workspace's fixed backfill floor, then repeatedly asks the context for pending usage exports. Each batch is turned into Metronome event dictionaries, posted to Metronome, logged, and then acknowledged in local storage so those exports are not sent again.

**Call relations**: The scheduled usage job enters through `_ship`, which creates a `UsageShipper` and calls this method. During the run it relies on `_floor` to decide how far back to look, `_events` to shape local usage into provider events, `_ingest` to send them, and logging to record successful shipment.

*Call graph*: calls 4 internal fn (_events, _floor, _ingest, _require_env); 1 external calls (log).


##### `UsageShipper._floor`  (lines 175–185)

```
async def _floor(self) -> datetime
```

**Purpose**: Chooses the earliest usage time this workspace will ever ship to Metronome. This limits the first backfill while still allowing old unsent records after that point to be shipped later.

**Data flow**: It reads a stored timestamp from the workspace store. If none exists, it creates one at seven days before the current time, saves it, and returns it; if one already exists, it parses and returns that saved value.

**Call relations**: It is called by `UsageShipper.run` before asking for pending usage exports. Its output becomes the lower time boundary used by the core export queue.

*Call graph*: called by 1 (run); 3 external calls (fromisoformat, now, timedelta).


##### `UsageShipper._events`  (lines 187–206)

```
def _events(self, exports: tuple[UsageExport, ...]) -> list[dict[str, object]]
```

**Purpose**: Converts UFO's internal usage export records into the event format Metronome expects. It preserves key billing details such as model, amount, price, and whether the workspace used its own provider key.

**Data flow**: It takes a tuple of frozen `UsageExport` records and reads the workspace id from the context. For each export, it builds a dictionary with a stable transaction id, customer id, event type, timestamp, and string properties, then returns the full list.

**Call relations**: It is called by `UsageShipper.run` right before `_ingest`. It uses `_rfc3339` so event timestamps are formatted consistently for Metronome.

*Call graph*: calls 1 internal fn (_rfc3339); called by 1 (run).


##### `_ship`  (lines 209–210)

```
async def _ship(ctx: ExtensionContext) -> None
```

**Purpose**: Acts as the scheduled-job wrapper for usage shipping. It adapts the job system's context into a `UsageShipper` run.

**Data flow**: It receives an extension context from the job runner, creates a `UsageShipper` with the configured test or production transport, and waits for that shipper to finish.

**Call relations**: The `manifest` registers this as the handler for the usage shipping job. Its only job is to hand control to `UsageShipper.run`.

*Call graph*: 1 external calls (__init__).


##### `SeatShipper.run`  (lines 224–238)

```
async def run(self) -> None
```

**Purpose**: Sends one daily member-count snapshot for a workspace to Metronome. It avoids sending the same workspace's count more than once per day.

**Data flow**: It reads the Metronome token, computes today's date, and checks the workspace store to see whether today's snapshot was already sent. If not, it reads the current seat snapshot from core, builds one Metronome event, posts it, logs the count, and records today's date as shipped.

**Call relations**: The scheduled seat job enters through `_ship_seats`, which creates a `SeatShipper` and calls this method. This method uses `_event` to build the event, `_ingest` to send it, and `_require_env` to require the provider token.

*Call graph*: calls 3 internal fn (_event, _ingest, _require_env); 3 external calls (__init__, now, log).


##### `SeatShipper._event`  (lines 240–248)

```
def _event(self, snapshot: SeatSnapshot, today: str) -> dict[str, object]
```

**Purpose**: Builds the single Metronome event that reports today's member count for a workspace.

**Data flow**: It takes a seat snapshot and a date string, reads the workspace id, counts the members in the snapshot, and returns an event dictionary with a stable workspace-date transaction id.

**Call relations**: It is called by `SeatShipper.run` after the current roster snapshot is read. It uses `_rfc3339` for the event timestamp before the event is handed to `_ingest`.

*Call graph*: calls 1 internal fn (_rfc3339); called by 1 (run); 1 external calls (now).


##### `_ship_seats`  (lines 251–252)

```
async def _ship_seats(ctx: ExtensionContext) -> None
```

**Purpose**: Acts as the scheduled-job wrapper for daily seat-count shipping.

**Data flow**: It receives an extension context, creates a `SeatShipper` with the configured transport, and waits for it to run.

**Call relations**: The `manifest` registers this as the handler for the daily seat job. It simply passes control into `SeatShipper.run`.

*Call graph*: 1 external calls (__init__).


##### `BillingConfig.from_env`  (lines 271–290)

```
def from_env(cls) -> 'BillingConfig'
```

**Purpose**: Reads the required Stripe and Metronome billing settings from environment variables. It fails early if any required setting is missing.

**Data flow**: It looks up the Stripe secret key, Stripe portal configuration id, Metronome bearer token, and Metronome package alias. If any are absent, it raises an error naming all missing variables; otherwise it returns a validated `BillingConfig` object.

**Call relations**: Billing tool calls and billing activation use this before making provider changes. That keeps half-configured deployments from creating provider objects and then failing midway.


##### `BillingActivation.run`  (lines 327–354)

```
async def run(self) -> None
```

**Purpose**: Turns a pending billing setup into an active Metronome plan once Stripe has a saved payment method. It resumes safely if an earlier run stopped partway through.

**Data flow**: It reads the workspace's billing record. If there is no pending work, it returns. Otherwise it loads config, asks Stripe whether a default payment method exists, creates or finds the Metronome customer if needed, creates or finds the Metronome contract if needed, stores each new provider id, and finally notifies the original conversation.

**Call relations**: The scheduled billing activation job enters through `_activate_billing`, which creates `BillingActivation` and calls this method. This method coordinates `_billing_record`, `_has_default_payment_method`, `_metronome_customer`, `_metronome_contract`, `_contract_key`, `_store`, and `_notify`.

*Call graph*: calls 7 internal fn (_notify, _store, _billing_record, _contract_key, _has_default_payment_method, _metronome_contract, _metronome_customer).


##### `BillingActivation._store`  (lines 356–358)

```
async def _store(self, record: BillingRecord) -> BillingRecord
```

**Purpose**: Writes the latest billing record for a workspace into the extension store. It is used after each important billing step so future retries know where to resume.

**Data flow**: It takes a `BillingRecord`, turns it into JSON-friendly data, saves it under the billing key in the workspace store, and returns the same record.

**Call relations**: It is called by `BillingActivation.run` after provider ids are learned, and by `_notify` after activation is complete. It is the checkpoint mechanism for the activation flow.

*Call graph*: called by 2 (_notify, run); 1 external calls (model_dump).


##### `BillingActivation._notify`  (lines 360–373)

```
async def _notify(self, record: BillingRecord) -> None
```

**Purpose**: Tells the original chat conversation that billing is now active, then marks the billing record as activated. This prevents repeated activation messages.

**Data flow**: It reads the workspace id and notification target from the billing record, invokes the agent conversation with an idempotency key, then saves a copy of the record with `activated_at` set to the current time and logs the activation.

**Call relations**: It is called at the end of `BillingActivation.run`, after the Metronome customer and contract are known. It uses `_store` to persist the final activated state.

*Call graph*: calls 1 internal fn (_store); called by 1 (run); 3 external calls (now, model_copy, log).


##### `_activate_billing`  (lines 376–377)

```
async def _activate_billing(ctx: ExtensionContext) -> None
```

**Purpose**: Acts as the scheduled-job wrapper for billing activation.

**Data flow**: It receives an extension context, creates a `BillingActivation` with the configured transport, and waits for it to complete.

**Call relations**: The `manifest` registers this as the handler for the billing activation job. It hands control to `BillingActivation.run`.

*Call graph*: 1 external calls (__init__).


##### `_billing_record`  (lines 380–382)

```
async def _billing_record(ctx: ExtensionContext) -> BillingRecord | None
```

**Purpose**: Reads the workspace's saved billing setup state, if one exists.

**Data flow**: It asks the extension store for the billing key. If nothing is stored, it returns `None`; otherwise it validates the stored data as a `BillingRecord` and returns it.

**Call relations**: It is used by activation, setup, status, and portal flows whenever they need to know whether billing has already been started and which provider ids are known.

*Call graph*: called by 4 (run, _billing_portal, _billing_setup, _billing_status).


##### `_contract_key`  (lines 385–389)

```
def _contract_key(workspace_id: UUID) -> str
```

**Purpose**: Creates the permanent Metronome contract identity for a workspace. This lets the code recognize the workspace's own plan later.

**Data flow**: It takes a workspace UUID and returns a string containing that UUID with a fixed prefix.

**Call relations**: Activation uses it when creating or finding a contract, and status uses it when checking whether the workspace's own plan is live.

*Call graph*: called by 2 (run, _billing_status).


##### `manage_billing`  (lines 406–415)

```
async def manage_billing(ctx: ToolContext, args: ManageBillingInput) -> ToolResult
```

**Purpose**: This is the chat tool handler for billing requests. It lets an admin start setup, check status, or get a Stripe portal link.

**Data flow**: It receives the tool context and structured arguments, verifies admin access, loads billing configuration, then routes the requested action to setup, status, or portal logic. It returns a tool result containing JSON text for the agent to show or use.

**Call relations**: The `manifest` exposes this through `MANAGE_BILLING_TOOL_DEF`. It first calls `_admin_billing`, then delegates to `_billing_setup`, `_billing_status`, or `_billing_portal` depending on the requested action.

*Call graph*: calls 4 internal fn (_admin_billing, _billing_portal, _billing_setup, _billing_status).


##### `_admin_billing`  (lines 418–424)

```
async def _admin_billing(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Checks that the billing request came from a real workspace member who is an admin. This protects billing actions from ordinary users and non-member contexts.

**Data flow**: It reads the speaker member id from the tool context, asks the context whether that speaker is an admin, and returns the extension context if allowed. If not, it raises an error.

**Call relations**: It is called only by `manage_billing`, before any billing configuration is loaded or any provider call is made.

*Call graph*: calls 1 internal fn (speaker_is_admin); called by 1 (manage_billing).


##### `_billing_setup`  (lines 427–467)

```
async def _billing_setup(ctx: ToolContext, ext: ExtensionContext, config: BillingConfig) -> ToolResult
```

**Purpose**: Starts billing setup for a workspace and returns a Stripe portal link where an admin can save a payment method. It records the intended plan before handing back the link.

**Data flow**: It reads any existing billing record. If none exists, it creates or reuses a Stripe Customer, builds a new `BillingRecord` with the chosen package and notification target, and stores it. Then it creates a Stripe portal session for payment-method update and returns the URL plus provider and package details.

**Call relations**: It is called by `manage_billing` for the `setup` action. It uses `_billing_record`, `_stripe_customer`, `_portal_session`, `_text_result`, and logging; the activation job later continues from the record it writes.

*Call graph*: calls 4 internal fn (_billing_record, _portal_session, _stripe_customer, _text_result); called by 1 (manage_billing); 3 external calls (__init__, now, log).


##### `_billing_status`  (lines 470–498)

```
async def _billing_status(ext: ExtensionContext, config: BillingConfig) -> ToolResult
```

**Purpose**: Reports what Stripe and Metronome currently say about the workspace's billing state. It does not trust only the local record for whether the plan is active.

**Data flow**: It reads the billing record. If none exists, it returns `configured: false`. If one exists, it asks Stripe whether a payment method is on file and, when possible, asks Metronome whether the workspace's own contract exists, then returns those facts as JSON text.

**Call relations**: It is called by `manage_billing` for the `status` action. It uses `_has_default_payment_method`, `_contract_key`, `_contract_for`, and `_text_result` to build a provider-backed answer.

*Call graph*: calls 5 internal fn (_billing_record, _contract_for, _contract_key, _has_default_payment_method, _text_result); called by 1 (manage_billing).


##### `_billing_portal`  (lines 501–509)

```
async def _billing_portal(ext: ExtensionContext, config: BillingConfig) -> ToolResult
```

**Purpose**: Creates a fresh Stripe portal link for an already configured workspace. Admins can use it for invoices, payment methods, and billing details.

**Data flow**: It reads the billing record. If setup has not started, it raises an error telling the admin to run setup first. Otherwise it creates a general Stripe portal session and returns the portal URL as JSON text.

**Call relations**: It is called by `manage_billing` for the `portal` action. It uses `_billing_record`, `_portal_session`, and `_text_result`.

*Call graph*: calls 3 internal fn (_billing_record, _portal_session, _text_result); called by 1 (manage_billing).


##### `_text_result`  (lines 512–513)

```
def _text_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: Wraps a small data payload into the standard tool-result shape expected by UFO tools.

**Data flow**: It takes a dictionary, converts it to a JSON string, puts that string in a text content object, and returns a tool result containing it.

**Call relations**: The setup, status, and portal billing flows call this right before returning to `manage_billing` and the chat tool system.

*Call graph*: called by 3 (_billing_portal, _billing_setup, _billing_status); 3 external calls (__init__, __init__, dumps).


##### `_require_env`  (lines 525–529)

```
def _require_env(name: str) -> str
```

**Purpose**: Reads a required environment variable and gives a clear error if it is missing.

**Data flow**: It takes an environment variable name, looks it up, and returns the value if present. If the value is empty or absent, it raises a runtime error.

**Call relations**: The usage and seat shippers call this before sending events, because both need the Metronome bearer token.

*Call graph*: called by 2 (run, run).


##### `_stripe_customer`  (lines 532–549)

```
async def _stripe_customer(config: BillingConfig, workspace_id: UUID, transport: httpx.AsyncBaseTransport | None) -> str
```

**Purpose**: Creates or reuses the one Stripe Customer for a workspace. The request uses a stable key so retries do not create duplicate customers.

**Data flow**: It takes billing config, a workspace id, and an optional HTTP transport. It posts a customer creation request to Stripe with workspace metadata and an idempotency key, then extracts and returns the customer id.

**Call relations**: It is called by `_billing_setup` when no billing record exists yet. It relies on `_stripe` for the HTTP request and `_as_str` to ensure Stripe returned a usable id.

*Call graph*: calls 2 internal fn (_as_str, _stripe); called by 1 (_billing_setup).


##### `_portal_session`  (lines 552–568)

```
async def _portal_session(config: BillingConfig, customer_id: str, flow: str | None, transport: httpx.AsyncBaseTransport | None) -> str
```

**Purpose**: Creates a short-lived Stripe Customer Portal URL. Depending on the flow, it can be limited to saving a payment method or opened as the general billing portal.

**Data flow**: It takes config, a Stripe customer id, an optional flow name, and a transport. It posts a portal-session request to Stripe, including the configured portal setup, then extracts and returns the URL.

**Call relations**: It is called by `_billing_setup` for payment setup and by `_billing_portal` for general portal access. It uses `_stripe` to talk to Stripe and `_as_str` to validate the returned URL.

*Call graph*: calls 2 internal fn (_as_str, _stripe); called by 2 (_billing_portal, _billing_setup).


##### `_has_default_payment_method`  (lines 571–581)

```
async def _has_default_payment_method(config: BillingConfig, customer_id: str, transport: httpx.AsyncBaseTransport | None) -> bool
```

**Purpose**: Checks whether Stripe says the customer has a default payment method saved. This is the gate before activating the Metronome plan.

**Data flow**: It fetches the Stripe Customer, looks inside the invoice settings for a default payment method string, and returns true if one is present and false otherwise.

**Call relations**: Billing activation calls this before creating Metronome plan objects, and billing status calls it to report whether a card is on file. It delegates the actual Stripe request to `_stripe`.

*Call graph*: calls 1 internal fn (_stripe); called by 2 (run, _billing_status).


##### `_stripe`  (lines 584–602)

```
async def _stripe(config: BillingConfig, method: str, path: str, transport: httpx.AsyncBaseTransport | None, data: dict[str, str] | None=None, idempotency_key: str | None=None) -> dict[str, object]
```

**Purpose**: Performs authenticated HTTP requests to Stripe and turns Stripe failures into clear extension errors.

**Data flow**: It receives the billing config, HTTP method, Stripe path, optional form data, optional idempotency key, and optional test transport. It sends the request with the pinned Stripe API version and returns the JSON response, or raises `StripeError` if Stripe did not return success.

**Call relations**: Stripe-specific helpers call this rather than each building their own HTTP client. It supports `_stripe_customer`, `_portal_session`, and `_has_default_payment_method`.

*Call graph*: called by 3 (_has_default_payment_method, _portal_session, _stripe_customer); 2 external calls (__init__, AsyncClient).


##### `_metronome_customer`  (lines 605–650)

```
async def _metronome_customer(config: BillingConfig, alias: str, stripe_customer_id: str, transport: httpx.AsyncBaseTransport | None) -> str
```

**Purpose**: Finds or creates the Metronome customer for a workspace. It ties the workspace id used on usage events to the Metronome customer that will be billed.

**Data flow**: It first searches Metronome by the workspace alias. If found, it returns that customer id. If not, it posts a customer creation request with the Stripe billing configuration; if Metronome reports a conflict, it searches again and returns the existing customer when possible.

**Call relations**: Billing activation calls this after Stripe has a saved payment method. It uses `_customer_by_alias` for lookup and `_metronome` for provider calls.

*Call graph*: calls 2 internal fn (_customer_by_alias, _metronome); called by 1 (run); 1 external calls (__init__).


##### `_customer_by_alias`  (lines 653–662)

```
async def _customer_by_alias(config: BillingConfig, alias: str, transport: httpx.AsyncBaseTransport | None) -> str | None
```

**Purpose**: Looks up a Metronome customer by the workspace alias used for usage ingestion.

**Data flow**: It sends a Metronome customer-list request filtered by ingest alias. If the response contains at least one customer id, it returns the first id; otherwise it returns `None`.

**Call relations**: It is used inside `_metronome_customer` before creating a customer and again when reconciling a creation conflict.

*Call graph*: calls 1 internal fn (_metronome); called by 1 (_metronome_customer).


##### `_metronome_contract`  (lines 665–701)

```
async def _metronome_contract(config: BillingConfig, customer_id: str, record: BillingRecord, uniqueness_key: str, transport: httpx.AsyncBaseTransport | None) -> str
```

**Purpose**: Finds or creates the Metronome contract that represents the workspace's plan. It uses a permanent uniqueness key so retries can safely recover the same contract.

**Data flow**: It first checks whether a contract with the workspace's key already exists for the customer. If not, it posts a contract creation request using the package and start time stored in the billing record. If Metronome reports a conflict, it checks again and returns the reconciled contract id.

**Call relations**: Billing activation calls this after it knows the Metronome customer id. It uses `_contract_for` to identify existing contracts, `_metronome` to create one, and `_rfc3339` to format the stored start time.

*Call graph*: calls 3 internal fn (_contract_for, _metronome, _rfc3339); called by 1 (run); 1 external calls (__init__).


##### `_contract_for`  (lines 704–727)

```
async def _contract_for(config: BillingConfig, customer_id: str, uniqueness_key: str, transport: httpx.AsyncBaseTransport | None) -> str | None
```

**Purpose**: Finds this workspace's live Metronome contract on a customer by matching the stable uniqueness key. It avoids accidentally treating some other contract on the customer as this workspace's plan.

**Data flow**: It asks Metronome to list contracts for the customer, scans the returned contracts, and returns the id of the one whose uniqueness key matches. If none match, it returns `None`.

**Call relations**: It is called by `_metronome_contract` before and after creation attempts, and by `_billing_status` when reporting whether the plan is active.

*Call graph*: calls 1 internal fn (_metronome); called by 2 (_billing_status, _metronome_contract).


##### `_metronome`  (lines 730–750)

```
async def _metronome(config: BillingConfig, method: str, path: str, transport: httpx.AsyncBaseTransport | None, body: dict[str, object] | None=None, params: dict[str, str] | None=None, idempotency_key
```

**Purpose**: Performs authenticated HTTP requests to Metronome for billing setup work and turns provider failures into clear extension errors.

**Data flow**: It receives config, method, path, optional JSON body, query parameters, idempotency key, and transport. It sends the request with the Metronome bearer token, returns the JSON response on success, raises a special conflict error on HTTP 409, and raises a general Metronome error for other failures.

**Call relations**: Metronome billing helpers call this for customer lookup, customer creation, contract lookup, and contract creation. Usage and seat event ingestion use the separate `_ingest` helper.

*Call graph*: called by 4 (_contract_for, _customer_by_alias, _metronome_contract, _metronome_customer); 3 external calls (__init__, __init__, AsyncClient).


##### `_as_str`  (lines 753–757)

```
def _as_str(value: object, field: str) -> str
```

**Purpose**: Checks that a provider response field is a non-empty string. It prevents later code from silently using a missing id or URL.

**Data flow**: It receives an unknown value and a human-readable field name. If the value is a non-empty string, it returns it; otherwise it raises a value error naming the missing field.

**Call relations**: Stripe helpers use this after provider calls: `_stripe_customer` uses it for customer ids, and `_portal_session` uses it for portal URLs.

*Call graph*: called by 2 (_portal_session, _stripe_customer).


##### `_ingest`  (lines 760–768)

```
async def _ingest(token: str, events: list[dict[str, object]], transport: httpx.AsyncBaseTransport | None) -> None
```

**Purpose**: Posts usage or seat events to Metronome's ingest endpoint. This is the shared sending path for metering events.

**Data flow**: It takes a bearer token, a list of event dictionaries, and an optional transport. It posts the event list to Metronome with a timeout; if Metronome does not accept it, it raises `MetronomeError` so the caller will not mark the events as shipped.

**Call relations**: Both `UsageShipper.run` and `SeatShipper.run` call this after building their events. Its success is the signal that those callers may record local progress.

*Call graph*: called by 2 (run, run); 2 external calls (__init__, AsyncClient).


##### `_rfc3339`  (lines 771–773)

```
def _rfc3339(moment: datetime) -> str
```

**Purpose**: Formats a datetime for provider APIs in a standard timestamp string. If the time has no timezone, it treats it as UTC.

**Data flow**: It takes a datetime, ensures it is timezone-aware by adding UTC when needed, and returns its ISO-style string form.

**Call relations**: Usage event building, seat event building, and Metronome contract creation all call this so timestamps are sent consistently.

*Call graph*: called by 3 (_event, _events, _metronome_contract); 1 external calls (replace).


##### `manifest`  (lines 776–812)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the UFO host: its tool, scheduled jobs, billing prompt guidance, and credential slot. This is how the rest of the system discovers what the file provides.

**Data flow**: It builds and returns a `Manifest` containing the extension name and version, the `manage_billing` tool definition, three scheduled jobs with their candidate workspace selectors, one prompt section, and an Anthropic API key credential slot.

**Call relations**: The extension loader calls this to register the Metronome extension. The returned manifest wires `_ship`, `_ship_seats`, and `_activate_billing` into the scheduler and exposes `manage_billing` to chat.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, metered_workspaces, member_workspaces).

## 📊 State Registers Touched

- `reg-effective-config` — The merged settings that tell the whole system what is enabled, safe, and available in this run.
- `reg-db-session` — The active database connection, transaction, and workspace-safe persistence context used while work is running.
- `reg-workspace-roster` — The saved list of workspaces, members, admins, seats, and membership rules.
- `reg-identity-context` — The current answer to who is acting, in which workspace, and on behalf of which member or agent.
- `reg-credential-vault` — The encrypted store of workspace secrets and API keys that tools and connectors can request through guarded paths.
- `reg-model-catalog` — The shared catalog of AI models, providers, routing rules, reasoning modes, key lookup rules, and usage shapes.
- `reg-turn-queue` — The durable queue of conversation turns, including admitted work, claimed work, failures, retries, and completion state.
- `reg-sandbox-state` — The per-conversation sandbox handle, size, filesystem environment, command execution state, and cleanup state.
- `reg-network-egress-policy` — The shared network access rules and freshness counter that tell sandboxes and proxies where code may connect.
- `reg-usage-ledger` — The shared cost and usage records for model calls, tools, sandboxes, connectors, network use, and generated media.
- `reg-spend-controls` — The spend caps, prepaid balances, price table fingerprints, and checks that decide whether work may continue.
- `reg-billing-export` — The billing integration state for exported usage, member counts, Stripe setup, Metronome sync, and BYOK reporting.
- `reg-observability` — The shared logs, traces, metrics, trace links, and sanitized diagnostic records used to understand system behavior.
- `reg-prompt-context-budget` — The per-turn context-window and token-allocation state used to pack history, compact old context, reserve output/reasoning room, and pass normalized usage expectations to model calls.
- `reg-tool-execution-context` — The per-turn tool runtime context carrying permitted workspace handles, account/credential accessors, cleanup callbacks, sandbox/browser handles, and helper-agent hooks across tool calls.
- `reg-listing-cursors` — Opaque pagination and browsing cursor state used to resume stable listings across objects, pages, memory, artifacts, usage records, and portal panels.

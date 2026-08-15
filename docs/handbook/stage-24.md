# Cross-cutting accounting, observability, live hubs, and operational utilities  `stage-24` (cross-cutting infrastructure)

This stage is shared operational support that runs behind the scenes across the whole system. It helps the product measure work, show live progress, and stay safe for operators and users.

The accounting pieces act like the system’s cash register. accounting.py records model usage, checks spending limits, prepares billing data, and builds reports. balance.py keeps a fast prepaid balance for each workspace, so paid work can be approved without rereading every past payment. pricing.py stores model rates, turns token use into cost, and stamps records with the exact price version used.

The live update pieces keep people informed while an agent turn is running. hub.py broadcasts text, tool activity, costs, and final status to a command line or web page, with recent history for reconnects. The Redis hub package lets those updates pass between server processes through Redis Streams.

The remaining utilities support operations. listings.py provides safe cursor-based paging. seed.py creates a rich demo conversation for the portal. token_signing.py makes tamper-proof signed tokens. o11y.py records traces, metrics, and logs while hiding sensitive or oversized data.

## Files in this stage

### Spend accounting and balances
Core billing utilities record usage, enforce spend limits, maintain prepaid balances, and compute costs from model pricing tables.

### `core/src/ufo/accounting.py`

`domain_logic` · `cross-cutting: active during turn billing, sandbox/proxy metering, spend checks, reporting, and billing export jobs`

This file makes sure every paid or metered action is written down once, in a form the rest of the system can trust. The central idea is a ledger: a database table that works like a cash register tape. Model tokens, sandbox model calls, generated images, generated videos, and network egress counts are stored as ledger rows with amounts and costs in micro-dollars, where one US dollar equals 1,000,000 micro-USD.

The file has four main jobs. First, it records usage. Some entries are written once per turn attempt, while others accumulate safely if several events happen for the same turn. Second, it can read back what a turn cost, including how much of its prompt came from cache. Third, it creates frozen “export intents” for external billing consumers, so retries do not double-bill. Fourth, it enforces spend caps and builds reports for workspaces, members, and agents.

A few details matter. “Dimensions” separate different kinds of usage, such as normal tokens, sandbox tokens, images, videos, and egress. This prevents one kind of charge from being confused with another. Spend caps are checked against real ledger totals, not guessed memory state. Reporting uses the same ledger, so billing, caps, and dashboards all speak from the same source of truth.

#### Function details

##### `applicable_caps_absent`  (lines 46–52)

```
def applicable_caps_absent(workspace_id: UUID, member_id: UUID | None, agent_id: UUID) -> bool
```

**Purpose**: This is a quick shortcut for the common case where no spend caps apply to a workspace/member/agent combination. It lets callers skip a database check for a few seconds after the system has already learned that there are no relevant caps.

**Data flow**: It receives a workspace ID, optional member ID, and agent ID. It looks up that exact combination in a small in-memory cache and compares the stored expiry time with the current monotonic clock, which is a clock used for measuring elapsed time. It returns true only if a recent no-cap result is still fresh.

**Call relations**: Other admission code can ask this before doing a full cap check. When SpendEvaluator.decide finds no caps, it feeds this cache through _note_absent_caps, making this fast path possible.

*Call graph*: 1 external calls (monotonic).


##### `_note_absent_caps`  (lines 55–64)

```
def _note_absent_caps(key: tuple[UUID, UUID | None, UUID]) -> None
```

**Purpose**: This remembers, briefly, that a particular workspace/member/agent combination has no spend caps. It exists to avoid repeated database trips in installations that do not use caps.

**Data flow**: It receives the exact cache key. It checks the current time, removes expired cache entries if the cache is already large, then stores a new expiry time a few seconds in the future. It changes only the in-memory cache.

**Call relations**: SpendEvaluator.decide calls this after it has checked the database and found no applicable caps. Later, applicable_caps_absent can use that note to skip another check for the same combination.

*Call graph*: called by 1 (decide); 1 external calls (monotonic).


##### `_total_tokens`  (lines 67–74)

```
def _total_tokens(usage: Usage) -> int
```

**Purpose**: This adds up all token counters that count toward a billed model call. It gives the rest of the file one consistent definition of “total tokens.”

**Data flow**: It receives a Usage object containing input, output, cache-read, and cache-write token counts. It adds those fields together and returns the integer total. It does not read or change anything else.

**Call relations**: record_turn_usage, record_workspace_usage, and record_sandbox_tokens call this before writing ledger rows. If the total is zero, those callers skip billing because there is nothing to record.

*Call graph*: called by 3 (record_sandbox_tokens, record_turn_usage, record_workspace_usage).


##### `_prompt_tokens`  (lines 77–85)

```
def _prompt_tokens(usage: Usage) -> int
```

**Purpose**: This counts the tokens the model read as its prompt, including cached prompt tokens. It is used later to show what percentage of the prompt came from cache.

**Data flow**: It receives a Usage object. It adds input tokens, cache-read tokens, and cache-write tokens, but not output tokens. It returns that prompt-side total as an integer.

**Call relations**: record_turn_usage, record_workspace_usage, and record_sandbox_tokens store this value beside the cost. read_turn_cost later relies on those stored prompt and cache counts to calculate cache share from the ledger itself.

*Call graph*: called by 3 (record_sandbox_tokens, record_turn_usage, record_workspace_usage).


##### `record_turn_usage`  (lines 88–129)

```
async def record_turn_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, usage: Usage, attempt: str='', pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: This records the token usage for one turn attempt. It protects against double-recording the same attempt while still allowing a parked and resumed turn to add a separate real charge.

**Data flow**: It receives a database connection, workspace and turn IDs, a model name, usage counts, an optional attempt ID, and a pricing table. It totals the tokens, skips zero-token usage, builds a deterministic ledger ID, checks whether that row already exists, prices the usage, and inserts one ledger row if needed. The result is a database write or no write; it returns nothing.

**Call relations**: This is called when host-side model usage for a turn needs to be billed. It uses _total_tokens and _prompt_tokens for the counts, Pricing.micro_usd for the price, and ledger_id_for to make a stable row identity.

*Call graph*: calls 3 internal fn (_prompt_tokens, _total_tokens, micro_usd); 4 external calls (execute, insert, select, ledger_id_for).


##### `read_turn_cost`  (lines 143–173)

```
async def read_turn_cost(connection: AsyncConnection, turn_id: UUID, dimension: str) -> TurnCost | None
```

**Purpose**: This reads what a turn has spent under one usage dimension, such as normal tokens or sandbox tokens. It is meant for displaying or summarizing a turn’s final cost.

**Data flow**: It receives a database connection, a turn ID, and a dimension name. It sums matching ledger rows for that turn, including resumed attempts, and also sums prompt and cached-token fields. It returns a TurnCost object with total tokens, micro-USD cost, model name, and cache percentage, or None if nothing was billed.

**Call relations**: It reads the rows written by functions such as record_turn_usage and record_sandbox_tokens. Instead of trusting in-memory usage, it calculates the answer from the same ledger rows that billing used.

*Call graph*: 3 external calls (__init__, execute, select).


##### `record_workspace_usage`  (lines 176–209)

```
async def record_workspace_usage(connection: AsyncConnection, workspace_id: UUID, model: str, usage: Usage, pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: This bills model usage that belongs to a workspace as a whole, not to a specific turn. It is used for background jobs or other work that has no member or agent attribution.

**Data flow**: It receives a connection, workspace ID, model name, usage counts, and pricing table. It totals tokens, skips zero-token usage, calculates the price, creates a fresh ledger ID, and inserts a ledger row with no turn ID. It changes the ledger and returns nothing.

**Call relations**: It shares the same token-counting and pricing helpers as record_turn_usage. Because the row has no turn ID, workspace totals include it, while member and agent reports that join through turns do not.

*Call graph*: calls 3 internal fn (_prompt_tokens, _total_tokens, micro_usd); 3 external calls (execute, insert, uuid4).


##### `record_egress_request`  (lines 212–241)

```
async def record_egress_request(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, amount: int=1) -> None
```

**Purpose**: This counts sandbox network egress requests for a turn. These requests are recorded for usage visibility but have zero dollar cost here.

**Data flow**: It receives a connection, workspace ID, turn ID, and request count. It builds a stable ledger ID for the turn’s egress dimension, then inserts a row or atomically adds to the existing row. The ledger’s amount increases, while priced_micro_usd remains zero.

**Call relations**: The sandbox egress proxy uses this when a turn reaches the network. It writes to a separate egress dimension so these counts do not affect token billing or token spend caps.

*Call graph*: 2 external calls (execute, ledger_id_for).


##### `record_probe_egress_request`  (lines 244–270)

```
async def record_probe_egress_request(connection: AsyncConnection, workspace_id: UUID, amount: int=1) -> None
```

**Purpose**: This counts sandbox egress made by an off-turn probe. Like turn egress, it records request count rather than cost.

**Data flow**: It receives a connection, workspace ID, and request count. It inserts a new ledger row with no turn ID, the egress dimension, the given amount, and zero price. It returns nothing after writing the row.

**Call relations**: This is the off-turn counterpart to record_egress_request. Workspace-level reporting can see it, while member and agent attribution do not because there is no turn to join through.

*Call graph*: 3 external calls (execute, insert, uuid4).


##### `record_sandbox_tokens`  (lines 273–326)

```
async def record_sandbox_tokens(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, usage: Usage, pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: This records model tokens spent from inside a sandbox through the egress proxy. It keeps these charges separate from host-side turn tokens so they add together without being confused.

**Data flow**: It receives a connection, workspace ID, turn ID, model name, usage counts, and pricing table. It totals and prices the usage, skips zero-token calls, builds a stable ledger ID for the sandbox token dimension, then inserts or atomically increments the existing row. It updates token amount, prompt tokens, cache-read tokens, and cost.

**Call relations**: The sandbox/proxy path uses this when a model call happens inside the sandbox. It uses the same token helpers and pricing logic as host-side billing, and read_turn_cost can later read this dimension separately.

*Call graph*: calls 3 internal fn (_prompt_tokens, _total_tokens, micro_usd); 2 external calls (execute, ledger_id_for).


##### `record_image_usage`  (lines 329–348)

```
async def record_image_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, images: int, micro_usd: int) -> None
```

**Purpose**: This records generated image usage for a turn. The caller supplies the cost because image pricing may not fit the normal token-pricing table.

**Data flow**: It receives a connection, workspace ID, turn ID, model name, image count, and micro-USD cost. It passes those values to the shared media-recording helper using the images dimension. It returns nothing itself.

**Call relations**: Provider-specific image code calls this after it knows how many images were generated and what they cost. It delegates the database write to _record_media_usage.

*Call graph*: calls 1 internal fn (_record_media_usage).


##### `record_video_usage`  (lines 351–365)

```
async def record_video_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, videos: int, micro_usd: int) -> None
```

**Purpose**: This records generated video usage for a turn. The caller supplies the cost because video pricing may be based on provider-specific units such as seconds.

**Data flow**: It receives a connection, workspace ID, turn ID, model name, video count, and micro-USD cost. It forwards them to the shared media-recording helper using the videos dimension. It returns nothing itself.

**Call relations**: Provider-specific video code calls this after a video generation charge is known. It uses _record_media_usage so image and video ledger writes behave the same way.

*Call graph*: calls 1 internal fn (_record_media_usage).


##### `_record_media_usage`  (lines 368–400)

```
async def _record_media_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, dimension: str, model: str, amount: int, micro_usd: int) -> None
```

**Purpose**: This is the shared database writer for image and video usage. It safely accumulates repeated media generations on the same turn.

**Data flow**: It receives a connection, workspace ID, turn ID, media dimension, model name, amount, and micro-USD cost. It builds a stable ledger ID, inserts a row if none exists, or atomically adds the amount and cost to the existing row. It changes the ledger and returns nothing.

**Call relations**: record_image_usage and record_video_usage call this to avoid duplicating the same insert-or-increment logic. It uses ledger_id_for so each turn and media dimension has one accumulating row.

*Call graph*: called by 2 (record_image_usage, record_video_usage); 2 external calls (execute, ledger_id_for).


##### `mint_usage_exports`  (lines 425–534)

```
async def mint_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, floor: datetime, key_slot_for: Callable[[str], str | None]) -> None
```

**Purpose**: This creates frozen export records for usage that has not yet been sent to an outside billing consumer. “Frozen” means a retry sends the same numbers again, which helps avoid double billing.

**Data flow**: It receives a connection, workspace ID, consumer name, backfill floor time, and a function that maps model names to provider credential slots. It reads stored workspace credentials, finds ledger rows whose amount has grown beyond what was previously exported, filters out unsettled or non-exported dimensions, decides whether each usage item was billed with the workspace’s own key, and inserts ledger_export rows keyed so duplicates collapse. It returns nothing but writes export intents.

**Call relations**: A billing export job calls this before reading pending exports. It depends on the ledger rows written by the recording functions and prepares data that read_pending_usage_exports later delivers.

*Call graph*: 5 external calls (now, timedelta, execute, or_, select).


##### `read_pending_usage_exports`  (lines 537–582)

```
async def read_pending_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, limit: int) -> tuple[UsageExport, ...]
```

**Purpose**: This reads export records that have been minted but not yet acknowledged by an outside billing consumer. It returns stable deltas ready to send.

**Data flow**: It receives a connection, workspace ID, consumer name, and maximum number of rows. It joins pending ledger_export rows to their ledger rows, calculates the amount and cost delta, and builds UsageExport objects. It returns those objects in mint order.

**Call relations**: Export delivery code calls this after mint_usage_exports. If delivery fails before acknowledgment, this function will return the same frozen export records again.

*Call graph*: 3 external calls (__init__, execute, select).


##### `ack_usage_exports`  (lines 585–609)

```
async def ack_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: This marks exported usage records as acknowledged after the outside billing consumer has accepted them. Once acknowledged, they stop appearing in the pending list.

**Data flow**: It receives a connection, workspace ID, consumer name, and the UsageExport objects that were successfully delivered. It builds matching keys from ledger ID and starting amount, then updates those export rows with an acknowledgement time. It returns nothing.

**Call relations**: Export delivery code calls this only after a successful external API call. If a crash happens before this function runs, read_pending_usage_exports will safely re-deliver the same frozen records.

*Call graph*: 3 external calls (execute, or_, update).


##### `metered_workspaces`  (lines 612–615)

```
def metered_workspaces() -> WorkspaceCandidates
```

**Purpose**: This identifies workspaces that have ever produced ledger entries. It gives usage-export jobs a broad but safe list of candidates to check.

**Data flow**: It creates a candidate query for distinct workspace IDs from the ledger table and wraps it in the project’s owner-candidate helper. The result is a WorkspaceCandidates object for job scheduling or scanning.

**Call relations**: Background export or maintenance jobs can call this to know which workspaces might have usage to export. The actual per-workspace work still checks for pending rows, so an over-broad candidate list is acceptable.

*Call graph*: 1 external calls (owner_candidates).


##### `SpendEvaluator.decide`  (lines 650–665)

```
async def decide(self, connection: AsyncConnection, pending_micro_usd: int) -> SpendDecision
```

**Purpose**: This decides whether a turn may proceed under the applicable spend caps. It can allow the turn, park it until a cap is raised, or reject it outright.

**Data flow**: It receives a database connection and a pending micro-USD amount for the work about to happen. It reads applicable caps, caches the no-cap case, sums current spend for each cap’s rolling time window, adds the pending amount, and compares that total to each limit. It returns a SpendDecision with an outcome and, when blocked, a user-facing message.

**Call relations**: Admission or mid-turn checks call this before spending. It coordinates _applicable_caps, _used_micro_usd, _message, and _note_absent_caps to turn ledger totals and cap rules into one decision.

*Call graph*: calls 4 internal fn (_applicable_caps, _message, _used_micro_usd, _note_absent_caps); 1 external calls (__init__).


##### `SpendEvaluator._applicable_caps`  (lines 667–695)

```
async def _applicable_caps(self, connection: AsyncConnection) -> tuple[SpendCap, ...]
```

**Purpose**: This finds the spend caps that apply to the evaluator’s workspace, member, and agent. A cap can cover the whole workspace, one member, or one agent.

**Data flow**: It receives a database connection and reads spend_cap rows matching the evaluator’s workspace and relevant subject IDs. It converts each database row into a SpendCap object and returns them as a tuple.

**Call relations**: SpendEvaluator.decide calls this first. Its result determines whether the decision can be allowed immediately or must check actual ledger usage.

*Call graph*: called by 1 (decide); 4 external calls (__init__, execute, or_, select).


##### `SpendEvaluator._used_micro_usd`  (lines 697–723)

```
async def _used_micro_usd(self, connection: AsyncConnection, cap: SpendCap) -> int
```

**Purpose**: This calculates how much money has already been spent inside one cap’s rolling time window. It is the evidence used to decide whether a cap is reached.

**Data flow**: It receives a connection and a SpendCap. It computes a cutoff time from the cap’s window length, builds the right ledger query for workspace, member, or agent scope, sums priced_micro_usd, and returns the integer total. It does not change the database.

**Call relations**: SpendEvaluator.decide calls this for each applicable cap. The returned total is combined with pending spend and compared with the cap limit.

*Call graph*: called by 1 (decide); 4 external calls (now, timedelta, execute, select).


##### `SpendEvaluator._message`  (lines 725–736)

```
def _message(self, outcome: SpendOutcome, breaches: list[SpendCap]) -> str
```

**Purpose**: This creates the explanation shown when a cap blocks a turn. It chooses the tightest breached cap and formats either a parked or declined message.

**Data flow**: It receives the chosen outcome and a list of breached caps. It picks the cap with the smallest limit, converts micro-USD to dollars, and returns a readable sentence. It does not read or change outside state.

**Call relations**: SpendEvaluator.decide calls this only after it has found one or more breaches. The returned text becomes the SpendDecision message.

*Call graph*: called by 1 (decide).


##### `_token_sum`  (lines 853–861)

```
def _token_sum() -> sa.ColumnElement[int]
```

**Purpose**: This builds a database expression for summing token counts only from token-like dimensions. It keeps reports from treating images, videos, or egress counts as tokens.

**Data flow**: It takes no runtime data directly. It returns a SQL expression that sums ledger.amount where the dimension is normal tokens or sandbox tokens, and uses zero for other dimensions. The expression is later embedded in larger queries.

**Call relations**: SpendRollup.read, SpendRollup._by_origin, and _usage_details use this helper whenever they need token totals. It pairs with _token_cost_sum for cost-of-token reporting.

*Call graph*: called by 3 (_by_origin, read, _usage_details); 1 external calls (case).


##### `_token_cost_sum`  (lines 864–876)

```
def _token_cost_sum() -> sa.ColumnElement[int]
```

**Purpose**: This builds a database expression for summing cost only from token-like dimensions. It separates token cost from total cost, which may also include images or videos.

**Data flow**: It takes no direct input. It returns a SQL expression that sums priced_micro_usd for normal tokens and sandbox tokens, while counting other dimensions as zero. The expression is used inside report queries.

**Call relations**: SpendRollup.read, SpendRollup._by_origin, and _usage_details call this alongside _token_sum to produce consistent token-cost numbers.

*Call graph*: called by 3 (_by_origin, read, _usage_details); 1 external calls (case).


##### `_usage_details`  (lines 879–1003)

```
async def _usage_details(connection: AsyncConnection, source: sa.FromClause, scope: sa.ColumnElement[bool], cutoff: datetime | None, now: datetime) -> UsageDetails
```

**Purpose**: This builds the common usage summary shared by workspace, member, and agent reports. It includes selected-window totals, all-time totals, daily history, model breakdowns, execution breakdowns, and previous-period token counts.

**Data flow**: It receives a connection, a database source to query from, a scope condition, an optional cutoff time, and the current time. It runs several aggregate queries over the ledger: selected totals, all-time totals, per-day rows, per-execution rows, per-model rows, and previous-window tokens. It fills missing days with zeroes and returns a UsageDetails object.

**Call relations**: SpendRollup.read, SpendRollup.read_agent, and SpendRollup.read_member all call this so their reports share the same usage math. It uses _token_sum and _token_cost_sum to keep token calculations consistent.

*Call graph*: calls 2 internal fn (_token_cost_sum, _token_sum); called by 3 (read, read_agent, read_member); 9 external calls (__init__, __init__, __init__, __init__, fromisoformat, date, timedelta, execute, select).


##### `SpendRollup.read`  (lines 1014–1117)

```
async def read(self, connection: AsyncConnection, window_seconds: int | None) -> SpendReport
```

**Purpose**: This creates a workspace-wide spend report. It answers “how much did this workspace spend, and where did it come from?”

**Data flow**: It receives a connection and an optional window length in seconds. It calculates a cutoff if needed, sums total spend, groups spend by dimension, member, agent, price digest, and origin, and asks _usage_details for the detailed usage timeline and breakdowns. It returns a SpendReport object.

**Call relations**: Workspace dashboards or admin views call this for broad reporting. It uses _by_origin for surface-level attribution and shared helpers for token totals and usage details.

*Call graph*: calls 4 internal fn (_by_origin, _token_cost_sum, _token_sum, _usage_details); 8 external calls (__init__, __init__, __init__, __init__, now, timedelta, execute, select).


##### `SpendRollup._by_origin`  (lines 1119–1184)

```
async def _by_origin(self, connection: AsyncConnection, window: sa.ColumnElement[bool]) -> tuple[OriginTotal, ...]
```

**Purpose**: This groups token spend by the conversation origin that started it, even when subagents did the actual spending later. It makes reports match the surface a member would recognize, such as a chat channel.

**Data flow**: It receives a connection and a window condition. It builds a recursive database query, meaning a query that walks parent-turn links step by step, from spending turns up to their root turns. It joins those roots to conversations, groups token amount and token cost by surface label, and returns OriginTotal objects.

**Call relations**: SpendRollup.read calls this for workspace reports. It relies on _token_sum and _token_cost_sum so only token dimensions contribute to these origin totals.

*Call graph*: calls 2 internal fn (_token_cost_sum, _token_sum); called by 1 (read); 4 external calls (__init__, desc, execute, select).


##### `SpendRollup.read_agent`  (lines 1186–1242)

```
async def read_agent(self, connection: AsyncConnection, agent_id: UUID, window_seconds: int | None) -> AgentSpendReport
```

**Purpose**: This creates a spend report for one agent. It includes that agent’s ledger totals, its spend caps, and usage details over the selected window and all time.

**Data flow**: It receives a connection, agent ID, and optional window length. It joins ledger rows to turns for that agent, groups spend by dimension, reads agent-scoped caps, calculates the report total from the dimension lines, and calls _usage_details for timeline and model/execution breakdowns. It returns an AgentSpendReport.

**Call relations**: Agent detail pages or APIs call this when they need one agent’s accounting view. It shares usage-detail logic with workspace and member reports but filters through turn.agent_id.

*Call graph*: calls 1 internal fn (_usage_details); 7 external calls (__init__, __init__, __init__, now, timedelta, execute, select).


##### `SpendRollup.read_member`  (lines 1244–1302)

```
async def read_member(self, connection: AsyncConnection, member_id: UUID, window_seconds: int | None) -> MemberSpendReport
```

**Purpose**: This creates a spend report for one member. It shows only usage attributed to that member through their conversations, plus their member-scoped caps.

**Data flow**: It receives a connection, member ID, and optional window length. It joins ledger rows through turns to conversations, filters by conversation member, groups spend by dimension, reads member caps, totals the grouped costs, and calls _usage_details for the rest of the usage summary. It returns a MemberSpendReport.

**Call relations**: Member detail pages or APIs call this for per-person accounting. It uses the same attribution path as member spend caps, so reports and enforcement agree.

*Call graph*: calls 1 internal fn (_usage_details); 7 external calls (__init__, __init__, __init__, now, timedelta, execute, select).


### `core/src/ufo/balance.py`

`domain_logic` · `request handling and billing updates`

This file solves a speed and correctness problem. The system needs to check a workspace's remaining prepaid balance before model work starts, and that check may happen very often. Instead of adding up every purchase ever made each time, it keeps one current balance row, like a cash register total. The purchase records are still kept as the audit trail, so an operator can see how the total was built.

The file also keeps a tiny in-memory note for workspaces that were recently found to have no balance row. That is a shortcut: if a self-hosted install never uses prepaid credits, the system can skip repeated database reads for a few seconds. It is only an optimization, not a source of truth. If money is credited, the note is cleared.

The main actions are: read the current balance and lifetime purchase totals, credit a workspace exactly once for a given payment reference, and set a reserve amount. The reserve is a safety cushion: it stops a nearly empty workspace from starting work that it cannot afford to finish. Database writes are done inside the caller's transaction, so related changes can succeed or fail together.

#### Function details

##### `balance_absent`  (lines 27–33)

```
def balance_absent(workspace_id: UUID) -> bool
```

**Purpose**: This checks a short-lived memory shortcut that says, "we recently looked for this workspace's balance and found none." It helps avoid unnecessary database reads for workspaces that have never been credited.

**Data flow**: It receives a workspace ID. It looks in the local `_no_balance` map for an expiry time, compares that time with the current monotonic clock, and returns `True` only if the note exists and has not expired. It does not change the database or the cache.

**Call relations**: Other code can call this before doing a fuller balance read. It relies on `_note_absent_balance` having recorded a recent miss, and it is deliberately safe to be wrong in only one direction: a stale or missing cache entry merely means the system may do an extra database read.

*Call graph*: 1 external calls (monotonic).


##### `_note_absent_balance`  (lines 36–43)

```
def _note_absent_balance(workspace_id: UUID) -> None
```

**Purpose**: This records that a workspace was just found to have no balance row, for a few seconds only. It keeps the shortcut cache from growing forever by removing expired entries when the cache is full.

**Data flow**: It receives a workspace ID. It reads the current monotonic time, clears old expired notes if the cache has reached its maximum size, and stores a new expiry time for this workspace. The output is the changed in-memory cache; it returns nothing.

**Call relations**: It is called by `read_balance` when the database has no balance row for a workspace. Later, `balance_absent` can use this note to skip a redundant database lookup. If `credit` later adds money for the workspace, it removes the note so the shortcut does not hide the new balance.

*Call graph*: called by 1 (read_balance); 1 external calls (monotonic).


##### `read_balance`  (lines 57–87)

```
async def read_balance(connection: AsyncConnection, workspace_id: UUID) -> Balance | None
```

**Purpose**: This reads the full balance picture for a workspace: what is left now, what reserve must remain, how much was ever granted, how much was ever charged, and when the last purchase happened. If the workspace has never been credited, it returns nothing.

**Data flow**: It receives an asynchronous database connection and a workspace ID. First it looks for the current row in `workspace_balance`. If none exists, it records a short-lived "absent" note and returns `None`. If a row exists, it also sums that workspace's purchase records and finds the latest purchase time, then returns a `Balance` object containing both the current balance and the lifetime totals.

**Call relations**: This is the fuller read path used when an operator, admin screen, or other caller needs more than the fast gate check. It calls `_note_absent_balance` only on a database miss, and it builds a `Balance` data object from the database results.

*Call graph*: calls 1 internal fn (_note_absent_balance); 3 external calls (__init__, execute, select).


##### `credit`  (lines 90–145)

```
async def credit(connection: AsyncConnection, workspace_id: UUID, granted_micro_usd: int, charged_micro_usd: int, reference: str) -> bool
```

**Purpose**: This adds prepaid value to a workspace, but only once for a given reference such as a payment ID. It prevents duplicate payment deliveries from crediting the same workspace twice.

**Data flow**: It receives a database connection, workspace ID, granted amount, charged amount, and reference string. It first tries to insert a purchase record with that reference; if another record with the same workspace and reference already exists, it returns `False` and changes no balance. If the insert succeeds, it creates or updates the workspace's current balance by adding the granted amount, clears any cached "no balance" note for that workspace, and returns `True`.

**Call relations**: Billing or fulfillment code calls this inside its own database transaction, so recording the payment source and crediting the workspace can happen together. It writes both the audit record in `balance_purchase` and the fast current total in `workspace_balance`, using database conflict rules to make repeated calls safe.

*Call graph*: 2 external calls (execute, uuid4).


##### `set_reserve`  (lines 148–159)

```
async def set_reserve(connection: AsyncConnection, workspace_id: UUID, reserve_micro_usd: int) -> bool
```

**Purpose**: This sets the minimum balance cushion a workspace must keep before more work may begin. It only updates an existing balance row, because a reserve by itself should not create a prepaid account.

**Data flow**: It receives a database connection, workspace ID, and reserve amount. It updates the matching row in `workspace_balance` with the new reserve and timestamp. It returns `True` if exactly one row was changed, or `False` if the workspace had no balance row to update.

**Call relations**: Administrative or billing policy code can call this when the required safety cushion changes. It does not call other project functions; it sends a direct database update and reports whether there was an existing balance to receive the new reserve.

*Call graph*: 2 external calls (execute, update).


### `core/src/ufo/models/pricing.py`

`domain_logic` · `usage accounting and billing`

This file is the small pricing calculator for model usage. Models charge different rates for different kinds of tokens: input, output, cached reads, and cached writes. The file gives those rates a simple shape with `ModelPrice`, then uses them to calculate cost in micro-USD, meaning millionths of a US dollar. This lets the rest of the system record very small costs without floating-point rounding mistakes.

A key idea here is that prices are measured per million tokens. The calculator multiplies each token count by its matching rate, adds everything together, then divides by one million to get the final micro-USD cost. If an old usage record names a model that is no longer in the price table, the file does not crash. It writes a warning log and returns zero, which keeps historical processing from breaking.

The file also creates a digest, which is like a tamper-evident label on a jar. It sorts the price table, converts it to a compact JSON string, and hashes it with SHA-256. That digest can be stamped on billed usage so later readers know exactly which price table produced the charge.

#### Function details

##### `price_digest`  (lines 25–41)

```
def price_digest(prices: Mapping[str, ModelPrice]) -> str
```

**Purpose**: Creates a stable version label for a whole model price table. This is useful because billing records can later prove which exact set of rates was used.

**Data flow**: It receives a mapping from model names to their prices. It sorts the models, writes each rate into a compact JSON form, hashes that text with SHA-256, and returns a string starting with `sha256:` followed by the hash.

**Call relations**: When a new `Pricing` object is built, `pricing_from` calls this function to create the digest that travels with the table. Internally it relies on JSON formatting and SHA-256 hashing to make the same input always produce the same label.

*Call graph*: called by 1 (pricing_from); 2 external calls (sha256, dumps).


##### `usage_priced_micro_usd`  (lines 44–57)

```
def usage_priced_micro_usd(model: str, usage: Usage, prices: Mapping[str, ModelPrice]) -> int
```

**Purpose**: Calculates the cost of one model usage record in micro-USD. It is the main arithmetic step that turns token counts into money.

**Data flow**: It receives a model name, a `Usage` record containing token counts, and a price table. If the model has a price, it multiplies each token count by the matching rate, adds those amounts, divides by one million tokens, and returns the final integer cost. If the model is missing, it logs a warning and returns zero.

**Call relations**: This function is called by `Pricing.micro_usd`, which is the cleaner method used by the rest of the accounting code. When it finds an unknown model, it hands that fact to the logging system so the problem is visible without stopping billing work.

*Call graph*: called by 1 (micro_usd); 1 external calls (log).


##### `Pricing.micro_usd`  (lines 67–68)

```
def micro_usd(self, model: str, usage: Usage) -> int
```

**Purpose**: Provides the simple public way to price usage using a `Pricing` table. Callers do not need to pass the full price table themselves because it is already stored inside the `Pricing` object.

**Data flow**: It receives a model name and a `Usage` record. It forwards those, along with this object's stored prices, to `usage_priced_micro_usd`, then returns the calculated micro-USD cost.

**Call relations**: Accounting routines call this method when recording sandbox tokens, turn usage, and workspace usage. It acts as the neat doorway into the lower-level pricing calculation.

*Call graph*: calls 1 internal fn (usage_priced_micro_usd); called by 3 (record_sandbox_tokens, record_turn_usage, record_workspace_usage).


##### `pricing_from`  (lines 71–74)

```
def pricing_from(prices: Mapping[str, ModelPrice]) -> Pricing
```

**Purpose**: Builds a complete immutable pricing object from a raw price table. It makes sure the table and its digest are created together.

**Data flow**: It receives a mapping of model names to `ModelPrice` values. It copies that mapping into a plain dictionary, computes its digest with `price_digest`, and returns a new `Pricing` object containing both the copied prices and the digest.

**Call relations**: This is the construction step for pricing data. It calls `price_digest` before creating `Pricing`, so every pricing table used later by accounting has a matching version stamp from the start.

*Call graph*: calls 1 internal fn (price_digest); 1 external calls (__init__).


### Live turn streaming
Live hub components broadcast turn progress locally and through Redis Streams for reconnecting clients and multi-process deployments.

### `core/src/ufo/hub.py`

`io_transport` · `active during live turn streaming and reconnect handling`

This file is the project’s in-memory “news desk” for live turn updates. As an agent works, it produces small frames: text deltas, cost ticks, tool-call notices, skill-load notices, absorbed message IDs, and final or parked states. The hub publishes those frames to all current listeners and stores recent frames in a bounded ring buffer, like a small rolling tape recorder.

The main problem it solves is reconnecting without losing the story. A terminal client may disconnect while a tool is handed off to the user’s machine. When it comes back, it can say “I last saw cursor 42,” and the hub can replay frames after that point if they are still in memory. If the hub no longer has that cursor, the surface knows it must fall back to durable state elsewhere.

Publishing is deliberately non-blocking. If a subscriber is too slow and its queue fills, the oldest queued frame is dropped for that subscriber, rather than slowing the agent down. This makes live display best-effort, while the real turn result remains durable elsewhere.

`InProcessHub` protects shared turn state with a lock because publishers and subscribers may run on different asynchronous event loops. It also cleans up memory after terminal or parked streams once no one is listening.

#### Function details

##### `Hub.publish`  (lines 92–92)

```
async def publish(self, turn_id: UUID, frame: LiveFrame) -> str
```

**Purpose**: This is the interface method for adding one live frame to a turn’s stream. Code that only knows it has a `Hub` can call this without caring whether the hub is in-memory or backed by another service.

**Data flow**: It receives a turn ID and a live frame, such as a text update or terminal frame. A concrete hub stores and broadcasts that frame, then returns a cursor string that marks where the frame sits in that turn’s stream.

**Call relations**: The queue code calls this when it needs to publish a failed terminal state. Implementations such as `InProcessHub.publish` provide the actual storage, cursor assignment, and fan-out behavior.

*Call graph*: called by 1 (_commit_failed_terminal).


##### `Hub.subscribe`  (lines 94–96)

```
def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: This is the interface method for following a turn’s live stream. A surface uses it to replay missed frames after a cursor and then keep receiving new frames.

**Data flow**: It receives a turn ID and optionally the last cursor the caller saw. A concrete hub yields pairs of cursor and frame, first from replay history and then from live updates as they arrive.

**Call relations**: The hub tailing code calls this while pumping frames to a user-facing surface. Implementations such as `InProcessHub.subscribe` decide how replay and live delivery are stitched together.

*Call graph*: called by 1 (_pump).


##### `Hub.covers`  (lines 98–98)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: This is the interface method for asking whether a cursor is still covered by the hub’s replay memory. It lets a reconnecting surface know whether it can resume cleanly or must redraw from durable state.

**Data flow**: It receives a turn ID and cursor string. A concrete hub checks its retained history and returns true if that cursor is not older than the earliest frame it still keeps.

**Call relations**: The surface tailing logic calls this before deciding how to reconnect. `InProcessHub.covers` supplies the in-memory version of that check.

*Call graph*: called by 1 (tail_frames).


##### `_offer`  (lines 101–104)

```
def _offer(queue: asyncio.Queue[tuple[str, LiveFrame]], item: tuple[str, LiveFrame]) -> None
```

**Purpose**: This helper puts a frame into one subscriber’s queue without ever blocking. If the queue is already full, it removes the oldest queued frame first.

**Data flow**: It receives an asynchronous queue and a cursor-frame pair. If there is no room, it discards one old item, then places the new item into the queue; it returns nothing.

**Call relations**: Published frames are scheduled onto subscriber event loops with this helper. It is the small pressure valve that protects publishers from being slowed down by a lagging subscriber.


##### `InProcessHub._stream`  (lines 146–156)

```
def _stream(self, turn_id: UUID) -> _TurnStream
```

**Purpose**: This helper gets the live state for one turn, creating it if needed. That state includes the replay buffer, current subscribers, cursor number, and whether the stream has ended.

**Data flow**: It receives a turn ID and reads the hub’s dictionaries while the caller is holding the lock. If the turn already has a stream, it returns it; otherwise it creates a new stream with a bounded replay buffer and a cursor sequence that continues from any saved mark.

**Call relations**: `InProcessHub.publish` calls this before appending a new frame, and `InProcessHub.subscribe` calls it before registering a listener. It is the common doorway into per-turn live state.

*Call graph*: called by 2 (publish, subscribe); 2 external calls (__init__, deque).


##### `InProcessHub.publish`  (lines 158–174)

```
async def publish(self, turn_id: UUID, frame: LiveFrame) -> str
```

**Purpose**: This adds a frame to the in-memory stream for a turn and sends it to all current subscribers. It gives the frame a new cursor so clients can later resume from that point.

**Data flow**: It receives a turn ID and a live frame. Under a lock, it finds or creates the turn stream, increments the cursor, stores the frame in the replay buffer, notes the current subscribers, and marks terminal or parked streams as ended when appropriate. After releasing the lock, it schedules delivery of the frame to each subscriber’s event loop and returns the cursor.

**Call relations**: This is the concrete implementation behind `Hub.publish` for the in-process backend. It uses `_stream` to access turn state and `_offer` indirectly by scheduling it on each subscriber’s loop, so publishing never waits on a subscriber’s queue.

*Call graph*: calls 1 internal fn (_stream).


##### `InProcessHub.subscribe`  (lines 176–203)

```
async def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: This lets a caller follow one turn’s frames. It first replays buffered frames newer than the caller’s cursor, then yields new frames as they are published.

**Data flow**: It receives a turn ID and optional cursor. It creates a bounded queue for live frames, records the caller’s current asynchronous event loop, registers the subscriber under the lock, and snapshots replayable frames after the cursor. It then yields replay frames followed by live queue items until the caller stops; on exit it unregisters the subscriber and may delete finished turn state.

**Call relations**: This is the concrete implementation behind `Hub.subscribe` for the in-process backend. The surface pumping code uses it to stream updates to a user interface, while `InProcessHub.publish` feeds its queue with new frames.

*Call graph*: calls 1 internal fn (_stream); 2 external calls (Queue, get_running_loop).


##### `InProcessHub.covers`  (lines 205–213)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: This checks whether the in-memory replay buffer can still resume from a given cursor. It is a quick yes-or-no answer for reconnect logic.

**Data flow**: It receives a turn ID and cursor. If the cursor is empty, the turn has no stream, or the buffer is empty, it returns false. Otherwise it compares the requested cursor with the earliest retained cursor and returns whether replay can cover that point.

**Call relations**: This is the concrete implementation behind `Hub.covers` for the in-process backend. The hub tailing code asks it during reconnect decisions before choosing between smooth replay and a full redraw from durable state.


### `extensions/redis_hub/ufo_ext_redis_hub/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a directory often needs an `__init__.py` file to be treated as an importable package, meaning other code can refer to it by name and load modules from inside it. Here, the package is `ufo_ext_redis_hub`, which appears to belong to a Redis hub extension. Redis is an in-memory data store often used for fast messaging, caching, or coordination between processes.

Because this file is empty, it does not set up connections, define settings, or run any logic. Its job is more like putting a label on a folder: it tells Python, “this folder is part of the application’s code structure.” Without it, depending on the Python version and packaging setup, imports for modules inside this extension could fail or behave inconsistently.

So this file matters not because of what it does at runtime, but because it helps the project’s package layout work correctly.


### `extensions/redis_hub/ufo_ext_redis_hub/stream_hub.py`

`io_transport` · `request handling and live streaming`

This file is the Redis-backed version of a live update hub. A “live frame” is a small message such as a text fragment, a tool call, a cost update, or a final marker. Instead of keeping these messages only inside one Python process, this hub writes them to Redis Streams, which are append-only message lists in Redis. That means multiple running server instances can share the same live feed.

The file uses one Redis stream per turn. Publishing is like dropping numbered slips of paper into a mailbox for that turn. Subscribing is like reading from a bookmark: the caller gives a cursor, and the hub reads messages after that point. If there are no messages yet, it waits briefly and tries again. Timeouts during this wait are normal; they simply mean “nothing new right now.”

Frames are converted to a small JSON shape with a kind label, then rebuilt on the reader side. Redis entries are trimmed and expire after idleness, because these live frames are only for replaying recent UI updates. The permanent answer is stored elsewhere. A notable detail is that Redis clients are kept separately for each asyncio event loop, because an asyncio client is tied to the loop that created it. This avoids hard-to-debug cross-thread or cross-loop failures.

#### Function details

##### `frame_payload`  (lines 55–58)

```
def frame_payload(frame: LiveFrame) -> dict[str, object]
```

**Purpose**: Turns one live frame into a plain dictionary that can safely be sent through Redis as JSON. It records both what kind of frame it is and the frame’s own data, so the reader can rebuild the same kind later.

**Data flow**: It receives a LiveFrame object, looks up the text label for that frame’s concrete type, and asks the frame to dump its fields in JSON-friendly form. It returns a dictionary with a "kind" value and a "data" value; it does not change anything outside itself.

**Call relations**: RedisStreamHub.publish calls this before writing a frame to Redis. The output from this function is then serialized with json.dumps and stored as the Redis stream entry payload.

*Call graph*: called by 1 (publish); 1 external calls (model_dump).


##### `frame_from_payload`  (lines 61–65)

```
def frame_from_payload(payload: dict[str, object]) -> LiveFrame
```

**Purpose**: Rebuilds a live frame object from the dictionary form that was read out of Redis. It also rejects unknown frame kinds, which prevents the subscriber from silently misunderstanding bad or unexpected data.

**Data flow**: It receives a dictionary that should contain a "kind" label and a "data" body. It checks that the kind is a known string, chooses the matching frame model, validates the data into that model, and returns the resulting LiveFrame object.

**Call relations**: RedisStreamHub.subscribe calls this after reading and JSON-decoding a Redis entry. This is the return trip for data that was originally prepared by frame_payload during publishing.

*Call graph*: called by 1 (subscribe); 1 external calls (cast).


##### `_stream_id`  (lines 68–70)

```
def _stream_id(entry_id: str) -> tuple[int, int]
```

**Purpose**: Converts a Redis stream entry id, such as a timestamp-and-sequence string, into numbers that can be compared reliably. This is used to decide whether a saved cursor still points into the retained part of the stream.

**Data flow**: It receives a Redis entry id string, splits it around the dash, converts the millisecond timestamp and sequence part into integers, and returns them as a pair. It does not contact Redis or change state.

**Call relations**: RedisStreamHub.covers calls this for both the oldest retained entry and the caller’s cursor. By comparing the two pairs, covers can tell whether replay from that cursor should still be gap-free.

*Call graph*: called by 1 (covers).


##### `_stream_entries`  (lines 73–82)

```
def _stream_entries(batch: XReadResponse) -> list[StreamEntry]
```

**Purpose**: Pulls the actual stream entries out of Redis’s XREAD response and checks that the response has the expected shape. This protects the subscriber from treating a Redis protocol response as if it were a different format.

**Data flow**: It receives the raw batch returned by Redis XREAD. If the batch is empty, it returns an empty list. If the batch is not the expected list form, it raises an error. Otherwise it returns the list of entries from the first stream in the response.

**Call relations**: RedisStreamHub.subscribe uses this after each Redis XREAD call. It gives subscribe a simple list of entries to loop over, while keeping the Redis response-shape check in one small helper.

*Call graph*: called by 1 (subscribe).


##### `RedisStreamHub._client`  (lines 98–104)

```
def _client(self) -> Redis
```

**Purpose**: Returns the Redis client that is safe to use on the currently running asyncio event loop. An asyncio event loop is the scheduler that runs asynchronous tasks, and Redis clients must not be shared across different loops.

**Data flow**: It reads the currently running event loop, checks whether this hub already has a Redis client for that loop, and returns it if present. If not, it creates a new client from the hub’s Redis URL, stores it in the per-loop client map, and returns it.

**Call relations**: RedisStreamHub.publish, RedisStreamHub.subscribe, and RedisStreamHub.covers all call this before talking to Redis. It is the shared doorway that prevents publishing work and serving work from accidentally sharing loop-bound Redis connection state.

*Call graph*: called by 3 (covers, publish, subscribe); 2 external calls (get_running_loop, from_url).


##### `RedisStreamHub._stream`  (lines 106–107)

```
def _stream(self, turn_id: UUID) -> str
```

**Purpose**: Builds the Redis stream name for a specific turn. This keeps every turn’s live frames in its own Redis stream.

**Data flow**: It receives a turn UUID, combines it with the fixed stream prefix, and returns the resulting Redis key string. It does not read or write Redis.

**Call relations**: RedisStreamHub.publish uses this to know where to append a frame, RedisStreamHub.subscribe uses it to know where to read from, and RedisStreamHub.covers uses it to inspect the oldest retained entry for that turn.

*Call graph*: called by 3 (covers, publish, subscribe).


##### `RedisStreamHub.publish`  (lines 109–116)

```
async def publish(self, turn_id: UUID, frame: LiveFrame) -> str
```

**Purpose**: Adds one live frame to the Redis stream for a turn and returns the Redis cursor for that new entry. Callers use this when they want surfaces or other processes to see a new live update.

**Data flow**: It receives a turn id and a LiveFrame. It builds the turn’s Redis stream name, converts the frame into a JSON string, then uses a Redis pipeline to append the entry and refresh the stream’s expiry time. It returns the stream entry id assigned by Redis, which can later be used as a cursor.

**Call relations**: This is the publishing side of the hub. It relies on _stream to choose the Redis key, _client to get the correct Redis connection for the current event loop, and frame_payload plus json.dumps to prepare the frame for storage. RedisStreamHub.subscribe later reads the entries this method writes.

*Call graph*: calls 3 internal fn (_client, _stream, frame_payload); 1 external calls (dumps).


##### `RedisStreamHub.subscribe`  (lines 118–142)

```
async def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: Streams live frames for one turn, starting after a given cursor or from the beginning if no cursor is provided. It lets a UI or other surface replay recent frames and then keep waiting for new ones.

**Data flow**: It receives a turn id and an optional cursor. It repeatedly reads entries from the matching Redis stream after the last seen id, first with a quick read and then with a blocking wait if nothing is available. For each entry, it updates the cursor, JSON-decodes the stored frame, rebuilds the LiveFrame object, and yields the new cursor together with the frame. It keeps running until the caller stops iterating.

**Call relations**: This is the reading side of the hub. It uses _stream for the Redis key, _client for loop-safe Redis access, _stream_entries to normalize Redis responses, and frame_from_payload plus json.loads to turn stored JSON back into live frame objects. It is designed to tolerate Redis read timeouts by simply continuing the loop.

*Call graph*: calls 4 internal fn (_client, _stream, _stream_entries, frame_from_payload); 1 external calls (loads).


##### `RedisStreamHub.covers`  (lines 144–150)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: Checks whether Redis still has enough retained stream history to resume from a given cursor without a gap. If not, the caller knows it should redraw or restart from a safer point.

**Data flow**: It receives a turn id and a cursor. If the cursor is empty, it returns false. Otherwise it reads the first retained entry from the turn’s Redis stream. If the stream is empty, it returns false. If there is an entry, it compares that oldest entry id with the cursor and returns true when the cursor is at or after the oldest retained entry.

**Call relations**: This supports reconnect and replay decisions. It uses _stream to locate the turn’s Redis stream, _client to query Redis, and _stream_id to compare Redis entry ids as ordered numbers rather than fragile strings.

*Call graph*: calls 3 internal fn (_client, _stream, _stream_id).


### Portal data helpers
Portal-facing helpers provide stable cursor paging, signed token validation, and a seeded demonstration conversation for operators and designers.

### `core/src/ufo/listings.py`

`domain_logic` · `request handling`

This file solves a common listing problem: people may be reading a list while new rows are being created. If the system used page numbers or offsets, a new row inserted at the top could make the next page repeat something the reader already saw, or skip something they have not seen yet. Instead, this file uses keyset paging, which is like putting a bookmark on a specific row and asking for items before or after that bookmark.

All listings here use the same order: newest first, based on `created_at`, with the row id used as a tie-breaker when two rows have the same time. `ListingCursor` is that bookmark. It stores the timestamp, the item id, and whether the user is asking toward newer or older rows. It can turn itself into a query-string token and back again.

`page_query` prepares a database query so it fetches exactly the right slice of rows, plus one extra row to find out whether there is another page. `page_of` then turns those raw rows into a `ListingPage`: the visible rows plus optional “older” and “newer” cursors. If there is no row in one direction, that cursor is `None`, which also tells the user interface not to show that navigation control.

#### Function details

##### `ListingCursor.encode`  (lines 42–45)

```
def encode(self) -> str
```

**Purpose**: This turns a listing position into a compact text token that can be put in a URL or API response. Someone uses it when they want to give the client a safe “continue from here” marker.

**Data flow**: It starts with a `ListingCursor` containing a timestamp, an item id, and a direction. It chooses the word `newer` or `older`, joins that with the timestamp and item id using a separator, and returns the finished string token. It does not change anything else.

**Call relations**: This is the outward-facing half of cursor use. After a page is built, cursors made for the first or last visible row can be encoded and sent to a client so the client can request the next page later.


##### `ListingCursor.decode`  (lines 48–60)

```
def decode(cls, token: str) -> 'ListingCursor'
```

**Purpose**: This reads a cursor token from a request and turns it back into a `ListingCursor`. It also protects the listing code from bad or made-up tokens by rejecting anything that does not describe a real-looking position.

**Data flow**: It receives a text token. It splits the token into direction, timestamp, and item id; checks that the direction is either `newer` or `older`; converts the timestamp from ISO text into a `datetime`; and checks that the item id is a valid UUID. If all of that works, it returns a `ListingCursor`; if not, it raises `MalformedCursor` so the caller can report a bad cursor instead of silently showing the wrong page.

**Call relations**: The web surface functions for workspace artifacts, workspace memory, and workspace radar call this when a request includes a cursor. Once decoded, the cursor can guide the database paging work. This function calls standard parsing helpers for datetimes and UUIDs, and raises `MalformedCursor` when the token cannot be trusted.

*Call graph*: called by 3 (workspace_artifacts, workspace_memory, workspace_radar); 3 external calls (__init__, fromisoformat, UUID).


##### `page_query`  (lines 74–96)

```
def page_query(query: sa.Select[Any], cursor: ListingCursor | None, limit: int, *, created_at: sa.ColumnElement[datetime], ident: sa.ColumnElement[Any]) -> sa.Select[Any]
```

**Purpose**: This prepares a database query so it asks for one page of listing rows in the correct direction. It makes the database do the careful “before or after this bookmark” filtering instead of loading everything and trimming it later.

**Data flow**: It receives a SQLAlchemy `Select` query, an optional cursor, a page size, and the two database columns that define the listing order: creation time and id. If there is no cursor, it orders newest first and asks for `limit + 1` rows. If there is a cursor, it adds a comparison against the cursor’s timestamp and id, choosing either rows older than the cursor or rows newer than it. It returns a modified query ready to run.

**Call relations**: This is the database-side half of paging. It depends on the cursor position and uses SQLAlchemy’s tuple comparison so the timestamp and id are compared together as one position. Its result is meant to be executed by listing code, and the rows it returns are then shaped into a page by `page_of`.

*Call graph*: 2 external calls (tuple_, UUID).


##### `page_of`  (lines 99–128)

```
def page_of(rows: Sequence[SourceT], cursor: ListingCursor | None, limit: int, *, render: Callable[[SourceT], RowT], position: Callable[[SourceT], tuple[datetime, str]]) -> ListingPage[RowT]
```

**Purpose**: This turns the rows returned by `page_query` into a page object that the rest of the system can return to a client. It decides which rows are visible and whether “older” or “newer” navigation cursors should exist.

**Data flow**: It receives raw rows, the cursor that led here, the requested page size, a `render` function that converts each raw row into the public row shape, and a `position` function that extracts the row’s timestamp and id. It checks whether an extra row was fetched, because that extra row means there is another page in the direction being walked. It trims to the requested limit, reverses the rows when paging toward newer items so the final page still reads newest first, builds boundary cursors when needed, and returns a `ListingPage` containing rendered rows plus optional older and newer cursors.

**Call relations**: This is the presentation-side half of paging. It expects rows shaped by `page_query`, then creates the `ListingPage` envelope used by listing responses. Inside it, the small helper `page_of.at` creates cursors for the first and last visible rows.

*Call graph*: 1 external calls (__init__).


##### `page_of.at`  (lines 118–120)

```
def at(source: SourceT, *, newer: bool) -> ListingCursor
```

**Purpose**: This small helper builds a `ListingCursor` for one row in the page. It is used to create the boundary cursors that let the client move to the next older or newer page.

**Data flow**: It receives one source row and a direction flag. It calls the supplied `position` function to get that row’s timestamp and item id, then returns a new `ListingCursor` pointing at that row in the requested direction.

**Call relations**: This helper lives inside `page_of` because it only makes sense while building a page. `page_of` calls it for the last visible row when creating an older-page cursor and for the first visible row when creating a newer-page cursor.

*Call graph*: 1 external calls (__init__).


### `core/src/ufo/seed.py`

`domain_logic` · `operator-triggered seeding`

This file is a controlled way to plant a complete demo conversation into the system’s real storage. That matters because many parts of a finished conversation are private engine records: completed turns, terminal frames, transcripts, subagent runs, and shared artifacts. Extensions are not allowed to forge those records, so this seed code lives near the code that is allowed to write them.

The main object, `KitchenSink`, works like a stage crew setting up a full showroom. First it looks for older showroom setups that it created itself. It only deletes runs marked with its own special seed keys, and it refuses to delete anything if a real member appears to have spoken in that run or if transcript access was disclosed. This protects real workspace history.

Then it creates a new web conversation, inserts three completed turns, gives each turn a realistic final “terminal frame” with model, token, cost, and question data, and writes the chat row the web portal needs to show the conversation. It also creates nested subagent conversations, stores a transcript for one of them, attaches two files through the blob store, and finally writes the main transcript. The result is a durable, realistic sample conversation for checking the user interface.

#### Function details

##### `_framed`  (lines 74–75)

```
def _framed(turn_id: UUID, said: str) -> Message
```

**Purpose**: This helper makes a user message that includes a hidden reference to the turn it belongs to. It is used so the transcript can connect a visible user request back to the stored turn record.

**Data flow**: It receives a turn ID and the text the user supposedly said. It wraps the turn ID in a small context header, appends the user text, and returns a `Message` object marked as coming from the user.

**Call relations**: When `KitchenSink._said` builds the main transcript, it calls this helper for each user prompt that should be tied to a specific stored turn. The returned message becomes one item in the transcript that `KitchenSink.write` later saves.

*Call graph*: called by 1 (_said); 1 external calls (__init__).


##### `KitchenSink.write`  (lines 106–116)

```
async def write(self) -> UUID
```

**Purpose**: This is the top-level action that creates one complete kitchen-sink conversation and returns its new conversation ID. Someone would use it when they want to refresh the demo data shown in the portal.

**Data flow**: It starts by making one new conversation ID and three new turn IDs. It clears old safe-to-delete seed runs, opens the main conversation, creates subagent runs, attaches files, builds the transcript messages, stores the encoded transcript in the blob store, and returns the conversation ID.

**Call relations**: This method is the conductor for the whole file. It calls `_clear` before writing anything new, then hands the IDs to `_open`, `_runs`, `_files`, and `_said`; finally it uses the transcript helpers to save the finished conversation.

*Call graph*: calls 5 internal fn (_clear, _files, _open, _runs, _said); 4 external calls (__init__, encode, transcript_key, uuid4).


##### `KitchenSink._clear`  (lines 118–124)

```
async def _clear(self) -> None
```

**Purpose**: This removes older kitchen-sink demo runs that this seed tool can prove it owns. It keeps the workspace from filling up with stale copies while avoiding deletion of real user history.

**Data flow**: It asks `_prior` for earlier seed runs that are safe to remove. For each one, it asks `_drop` to delete database rows, deletes the web chat row from the extension store, and deletes related transcript and artifact blobs from blob storage.

**Call relations**: `KitchenSink.write` calls this first so the new run starts from a clean display. `_clear` relies on `_prior` to identify safe targets and `_drop` to remove the database side before it cleans up extension-store and blob-store leftovers.

*Call graph*: calls 2 internal fn (_drop, _prior); called by 1 (write); 2 external calls (__init__, transcript_key).


##### `KitchenSink._prior`  (lines 126–204)

```
async def _prior(self) -> tuple[_PriorRun, ...]
```

**Purpose**: This searches the database for previous kitchen-sink runs that are safe for the seed tool to destroy. Its safety checks are important because a demo-like title is not enough proof that data belongs to the seed.

**Data flow**: It reads conversation and turn tables inside a workspace database transaction. Starting from web conversations marked with the seed queue prefix, it follows child subagent conversations through their queue keys, collects all related turns, then checks whether any unmarked real speech or transcript disclosure exists. It returns only runs that pass those checks.

**Call relations**: `KitchenSink._clear` calls this before deleting anything. The `_PriorRun` records it returns become the deletion plan that `_drop` and the blob cleanup follow.

*Call graph*: called by 1 (_clear); 5 external calls (__init__, not_, or_, select, workspace_tx).


##### `KitchenSink._drop`  (lines 206–235)

```
async def _drop(self, run: _PriorRun) -> tuple[str, ...]
```

**Purpose**: This deletes the database records for one old seed run and reports which blob files were attached to it. It separates database cleanup from blob cleanup so the caller can remove both kinds of stored data.

**Data flow**: It receives a `_PriorRun` containing conversation IDs and turn IDs. Inside a database transaction, it reads blob keys for shared artifacts, deletes shared artifact rows, conversation change rows, turn rows, and conversation rows, then returns the artifact blob keys it found.

**Call relations**: `KitchenSink._clear` calls this for each safe prior run. After `_drop` removes the database rows, `_clear` uses the returned blob keys to delete the actual stored artifact contents.

*Call graph*: called by 1 (_clear); 3 external calls (delete, select, workspace_tx).


##### `KitchenSink._open`  (lines 237–275)

```
async def _open(self, conversation_id: UUID, turns: tuple[UUID, ...]) -> None
```

**Purpose**: This creates the main web conversation and its three finished turns. It also gives the conversation the standard demo title and creates the web chat row needed by the portal.

**Data flow**: It receives the new conversation ID and the three turn IDs. It inserts one conversation row, asks `_terminals` for the final status data for each turn, inserts those completed turn rows, retitles the conversation to “Kitchen sink,” and writes a small chat record containing the agent ID and member email.

**Call relations**: `KitchenSink.write` calls this after old data is cleared. `_open` depends on `_terminals` for the realistic final turn summaries, then hands off to the surface and extension-store helpers so the web portal can find and label the conversation.

*Call graph*: calls 1 internal fn (_terminals); called by 1 (write); 5 external calls (__init__, insert, workspace_tx, retitle_conversation, uuid4).


##### `KitchenSink._terminals`  (lines 277–333)

```
def _terminals(self) -> tuple[TerminalFrame, ...]
```

**Purpose**: This builds the final status snapshots for the three seeded turns. These snapshots make the demo show realistic costs, model names, token counts, and a still-open user question.

**Data flow**: It takes no outside input beyond the `KitchenSink` instance. It creates three `TerminalFrame` objects: two plain completed turns, and a third completed turn that includes a structured question with options and a free-text field. It returns them as a tuple.

**Call relations**: `KitchenSink._open` calls this while inserting the main turn rows. The returned terminal frames are stored directly in the turn table so the portal has completed-turn data to draw.

*Call graph*: called by 1 (_open); 4 external calls (__init__, __init__, __init__, __init__).


##### `KitchenSink._runs`  (lines 335–368)

```
async def _runs(self, conversation_id: UUID, parent: UUID) -> None
```

**Purpose**: This adds nested subagent activity to the demo conversation. It makes the kitchen-sink example show not just a main chat, but also a subagent that spawned another subagent.

**Data flow**: It receives the main conversation ID and the parent turn ID that should appear to have launched subagent work. It creates IDs for a child and grandchild turn, calls `_run` to insert the child subagent conversation and then the grandchild one, builds a short transcript for the child subagent, encodes it, and stores it in the blob store.

**Call relations**: `KitchenSink.write` calls this after the main conversation is opened. `_runs` delegates each database insertion to `_run`, then writes transcript content for the spawned subagent so the UI has nested run details to show.

*Call graph*: calls 1 internal fn (_run); called by 1 (write); 8 external calls (__init__, __init__, __init__, __init__, __init__, encode, transcript_key, uuid4).


##### `KitchenSink._run`  (lines 370–405)

```
async def _run(self, turn_id: UUID, parent: UUID, profile: str, answered: str) -> UUID
```

**Purpose**: This creates one completed subagent conversation with one completed turn. It is the small building block used to create both the child and grandchild subagent examples.

**Data flow**: It receives the turn ID to use, the parent turn ID, the subagent profile name, and the answer text to store. It creates a new conversation ID, inserts a subagent conversation whose queue key points back to the parent, inserts one completed turn with a JSON-like result in its terminal frame, and returns the new conversation ID.

**Call relations**: `KitchenSink._runs` calls this twice: once for the first subagent and once for the subagent launched from that subagent. The conversation ID it returns lets `_runs` attach a transcript to the spawned child run.

*Call graph*: called by 1 (_runs); 4 external calls (__init__, insert, workspace_tx, uuid4).


##### `KitchenSink._files`  (lines 407–428)

```
async def _files(self, turn_id: UUID) -> None
```

**Purpose**: This attaches two example files to one seeded turn: a Markdown audit and a CSV metrics file. These files let the portal demonstrate shared artifacts alongside chat messages.

**Data flow**: It receives the turn ID that should own the attachments. For each built-in file body, it encodes the text as bytes, creates a unique blob key, stores the file content in the blob store, and inserts a shared-artifact database row with filename, media type, size, and ownership details.

**Call relations**: `KitchenSink.write` calls this after creating the main conversation and subagent runs. The artifact rows it writes point back to the selected turn, while the blob keys point to the actual file contents.

*Call graph*: called by 1 (write); 3 external calls (insert, workspace_tx, uuid4).


##### `KitchenSink._said`  (lines 430–484)

```
def _said(self, turns: tuple[UUID, ...]) -> tuple[Message, ...]
```

**Purpose**: This builds the visible transcript for the main seeded conversation. It gives the demo realistic chat content, including user prompts, assistant replies, tool calls, and tool results.

**Data flow**: It receives the three stored turn IDs. It creates a sequence of `Message` objects, using `_framed` for the user messages that need turn references and direct message blocks for assistant text, tool-use requests, and tool-result responses. It returns the complete message tuple.

**Call relations**: `KitchenSink.write` calls this at the end, wraps the returned messages in a `Conversation`, encodes it, and saves it to the transcript blob. `_said` uses `_framed` so the transcript lines line up with the turn records created earlier by `_open`.

*Call graph*: calls 1 internal fn (_framed); called by 1 (write); 3 external calls (__init__, __init__, __init__).


### `core/src/ufo/token_signing.py`

`util` · `cross-cutting`

This file solves a common trust problem: if the system gives someone a token and later receives it back, how can it know the token was not changed? It uses HMAC, which is a standard way to make a tamper-evident seal from a secret key and some message bytes. Think of it like putting a wax seal on an envelope: anyone can hold the envelope, but only someone with the stamp can make a matching seal.

The token format is simple. First, the payload bytes are turned into base64url text, which is a web-safe way to write binary data using ordinary characters. Then the file signs that text with HMAC-SHA256 and joins the two parts with a dot: payload.signature. The payload is “opaque” because this file does not interpret what the bytes mean; it only wraps, signs, unwraps, and verifies them.

On the way back in, the verifier checks that the token has both parts, recomputes the expected signature from the payload text, and compares it safely. If the signature is wrong, or the payload cannot be decoded, it raises SignedTokenError. Without this file, callers would either have to repeat this security-sensitive code themselves or risk accepting altered tokens.

#### Function details

##### `sign_detached`  (lines 12–14)

```
def sign_detached(secret: bytes, message: bytes) -> str
```

**Purpose**: Creates a standalone signature for a message using a secret key. Use this when you need a compact proof that a specific byte message came from someone who knows the same secret.

**Data flow**: It receives a secret as bytes and a message as bytes. It feeds both into HMAC-SHA256 to make a binary digest, then converts that digest into base64url text and removes padding characters. It returns the signature string and does not change anything else.

**Call relations**: This is the basic sealing step used by higher-level helpers. sign_token calls it when building a full token, and verify_detached calls it to recreate the expected signature before comparing.

*Call graph*: called by 2 (sign_token, verify_detached); 2 external calls (urlsafe_b64encode, new).


##### `verify_detached`  (lines 17–18)

```
def verify_detached(secret: bytes, message: bytes, signature: str) -> bool
```

**Purpose**: Checks whether a provided signature really matches a message and secret. It answers yes or no without trying to decode or understand the message itself.

**Data flow**: It receives the secret, the original message bytes, and a signature string to check. It recreates the correct signature with sign_detached, then compares the two signatures using a timing-safe comparison, which avoids leaking clues through tiny timing differences. It returns true for a match and false otherwise.

**Call relations**: verify_token relies on this function after it splits a token into payload text and signature text. verify_detached hands the actual signature-making work to sign_detached, then performs the final safe comparison.

*Call graph*: calls 1 internal fn (sign_detached); called by 1 (verify_token); 1 external calls (compare_digest).


##### `sign_token`  (lines 21–23)

```
def sign_token(secret: bytes, payload: bytes) -> str
```

**Purpose**: Builds a complete signed token from raw payload bytes. It is the main helper for turning some data into a portable string that can later be checked for tampering.

**Data flow**: It receives a secret and payload bytes. It converts the payload into base64url text, signs that text with sign_detached, and joins the payload text and signature with a dot. It returns the finished token string.

**Call relations**: This function sits above the lower-level signature helper. It uses sign_detached for the security seal and is the natural counterpart to verify_token, which later checks and unwraps the token.

*Call graph*: calls 1 internal fn (sign_detached); 1 external calls (urlsafe_b64encode).


##### `verify_token`  (lines 26–35)

```
def verify_token(token: str, secret: bytes) -> bytes
```

**Purpose**: Checks a signed token and returns the original payload bytes if the token is valid. It rejects malformed, forged, or unreadable tokens by raising SignedTokenError.

**Data flow**: It receives a token string and the shared secret. It splits the token at the dot into payload text and signature text, makes sure both are present, verifies the signature against the payload text, then decodes the payload from base64url back into bytes. If any step fails, it raises a clear SignedTokenError instead of returning unsafe data.

**Call relations**: This is the main entry point for reading tokens made by sign_token. It calls verify_detached to prove the token has not been changed, and only after that does it hand the payload text to base64 decoding so the caller gets trusted bytes back.

*Call graph*: calls 1 internal fn (verify_detached); 2 external calls (__init__, b64decode).


### Observability toolbox
Shared observability utilities capture traces, metrics, and structured logs while redacting or trimming unsafe output.

### `core/src/ufo/o11y.py`

`io_transport` · `startup and cross-cutting runtime observability`

This file answers a basic operations question: “What happened inside UFO, and can we inspect it safely?” It connects the code to OpenTelemetry, a common system for sending traces, metrics, and logs to a collector. A trace is a timeline of work, a metric is a counted or timed measurement, and a structured log is a named event with searchable fields.

At startup, the file can install exporters that send telemetry to an OTLP endpoint, which is the OpenTelemetry HTTP protocol. If no endpoint is supplied, it leaves telemetry mostly as no-ops, but it still installs a safety guard for standard Python logs. That guard stops third-party libraries from printing giant messages, such as prompts or credentials hidden inside object representations.

During normal work, callers can open spans around turns or smaller operations, emit logs with automatic workspace tagging, and record counters or timing histograms. Before anything is sent, fields are redacted: keys such as prompt, content, token, secret, and credentials are removed, and unusual objects are converted into safe strings.

A key design choice is keeping metric dimensions bounded. For example, only known error class names are allowed through; unknown ones are grouped as “other.” This prevents monitoring systems from being flooded with endless new time series.

#### Function details

##### `init_o11y`  (lines 239–265)

```
def init_o11y(otlp_endpoint: str | None) -> None
```

**Purpose**: Sets up observability for the process. It installs the log-size guard every time, and when an OTLP endpoint is provided it connects traces, metrics, and logs to the external collector.

**Data flow**: It receives an optional collector base URL. First it protects all Python log records from oversized messages. If there is no URL, it stops there. If there is a URL, it builds separate trace, metric, and log endpoints, creates OpenTelemetry providers for each signal, and registers them globally so later tracing, metric, and logging calls use them.

**Call relations**: This is the startup doorway for the file. It asks _guard_log_messages to install the safety net, asks _otlp_signal_urls to build the exact export URLs, and then calls _bridge_warning_logs so ordinary Python warnings can also reach the OpenTelemetry log pipeline.

*Call graph*: calls 3 internal fn (_bridge_warning_logs, _guard_log_messages, _otlp_signal_urls); 13 external calls (set_logger_provider, OTLPLogExporter, OTLPMetricExporter, OTLPSpanExporter, set_meter_provider, LoggerProvider, BatchLogRecordProcessor, MeterProvider, PeriodicExportingMetricReader, create (+3 more)).


##### `_bridge_warning_logs`  (lines 268–280)

```
def _bridge_warning_logs(logger_provider: LoggerProvider) -> None
```

**Purpose**: Sends standard Python warning and error logs into the OpenTelemetry log exporter. This catches important warnings from libraries or modules that are not using this file’s structured log helpers.

**Data flow**: It receives the OpenTelemetry logger provider created during setup. It creates a Python logging handler that forwards warning-level and higher records, filters out UFO’s own structured logger and OpenTelemetry’s own logs, and attaches the handler to the root logger.

**Call relations**: init_o11y calls this after the OpenTelemetry log provider exists. It acts like a side door into the same log pipeline, so unexpected library warnings do not disappear.

*Call graph*: called by 1 (init_o11y); 2 external calls (getLogger, LoggingHandler).


##### `_GuardedRecordFactory.__call__`  (lines 310–322)

```
def __call__(self, *args: object, **kwargs: object) -> logging.LogRecord
```

**Purpose**: Creates Python log records while checking whether their final message would be too large. If a message is enormous, it replaces the text with a short notice saying which logger emitted it and how many characters were dropped.

**Data flow**: It receives the normal log-record creation arguments, passes them to the original factory, then asks _rendered_message what the message would look like. If the message is short, the record is returned unchanged. If it is too long, the record’s message and arguments are replaced with a safe summary.

**Call relations**: _guard_log_messages installs this object as the global log-record factory. After that, every standard Python log record passes through it before any handler or exporter sees the record.

*Call graph*: calls 1 internal fn (_rendered_message).


##### `_guard_log_messages`  (lines 325–329)

```
def _guard_log_messages() -> None
```

**Purpose**: Installs the global oversized-log-message guard, unless it is already installed. This protects stderr, local handlers, and telemetry exporters from huge accidental log output.

**Data flow**: It reads the current Python log-record factory. If that factory is already the guarded one, nothing changes. Otherwise it wraps the current factory in _GuardedRecordFactory and registers the wrapper globally.

**Call relations**: init_o11y calls this before doing any optional exporter setup. It prepares the logging system so _GuardedRecordFactory.__call__ can inspect every future standard log record.

*Call graph*: called by 1 (init_o11y); 3 external calls (__init__, getLogRecordFactory, setLogRecordFactory).


##### `_rendered_message`  (lines 332–342)

```
def _rendered_message(record: logging.LogRecord) -> str | None
```

**Purpose**: Safely finds the actual text of a Python log record. It avoids crashing the caller if a log message has bad formatting arguments.

**Data flow**: It receives a logging record. If the record already has a plain string message with no arguments, it returns that directly. Otherwise it asks Python logging to render the message; if rendering fails, it returns None instead of raising an error.

**Call relations**: _GuardedRecordFactory.__call__ uses this helper before deciding whether a message is too large. This keeps the guard safe even when the original log call was malformed.

*Call graph*: called by 1 (__call__); 1 external calls (getMessage).


##### `_otlp_signal_urls`  (lines 345–351)

```
def _otlp_signal_urls(otlp_endpoint: str) -> tuple[str, str, str]
```

**Purpose**: Builds the three exact HTTP URLs used for trace, metric, and log export. This matters because the exporter expects a full signal-specific URL, not just the collector base address.

**Data flow**: It receives a base OTLP endpoint string. It removes any trailing slash, appends the trace, metric, and log paths, and returns the three finished URLs.

**Call relations**: init_o11y calls this during exporter setup. The returned URLs are handed to the OpenTelemetry trace, metric, and log exporters.

*Call graph*: called by 1 (init_o11y).


##### `_ambient_scope`  (lines 354–359)

```
def _ambient_scope() -> dict[str, str]
```

**Purpose**: Finds the current workspace and turns it into metadata for logs and spans. This lets callers get workspace tagging automatically instead of passing the workspace ID everywhere.

**Data flow**: It reads the current workspace value from the database context variable. If no workspace is active, it returns an empty dictionary. If one is active, it returns a dictionary containing the workspace ID as text.

**Call relations**: turn_span, span, and _emit_log all call this when creating telemetry. It is the shared source of workspace context for this file.

*Call graph*: called by 3 (_emit_log, span, turn_span); 1 external calls (get).


##### `current_traceparent`  (lines 362–368)

```
def current_traceparent() -> str | None
```

**Purpose**: Captures the currently active trace context as a standard traceparent header. This allows later work, such as a queued turn, to reconnect to the trace that admitted it.

**Data flow**: It creates an empty carrier dictionary, asks the OpenTelemetry trace-context propagator to inject the current trace into it, and returns the traceparent value if one was produced. If there is no valid active trace, it returns None.

**Call relations**: This function is a small bridge between in-process tracing and work that may be stored or moved elsewhere. The saved value can later be passed into turn_span to continue the same trace.


##### `turn_profile`  (lines 371–376)

```
def turn_profile(subagent_profile: str | None) -> str
```

**Purpose**: Chooses the metric and trace profile name for a turn. Main user-facing turns are labeled main, while subagent turns use their configured subagent profile.

**Data flow**: It receives an optional subagent profile string. If the string is present, it returns that; otherwise it returns the fixed main profile name.

**Call relations**: turn_span calls this when tagging a turn span. The same idea is used by metrics so traces and metric charts can be compared by profile.

*Call graph*: called by 1 (turn_span).


##### `turn_span`  (lines 380–416)

```
def turn_span(turn_id: UUID, conversation_id: UUID, traceparent: str | None, subagent_profile: str | None, parent_turn_id: UUID | None) -> Iterator[Span]
```

**Purpose**: Opens a trace span for one durable turn. A span is a timed block in a trace, like a labeled segment on a work timeline.

**Data flow**: It receives turn identifiers, an optional parent trace header, an optional subagent profile, and an optional parent turn ID. It builds safe attributes, including workspace and profile information, extracts the parent trace if one was supplied, starts a server-style span named turn, and yields that span to the caller’s with-block.

**Call relations**: Callers use this around the main execution of a turn. Inside, it relies on turn_profile for the profile label, _ambient_scope for workspace metadata, and redact_payload to remove sensitive fields before handing attributes to OpenTelemetry.

*Call graph*: calls 3 internal fn (_ambient_scope, redact_payload, turn_profile); 2 external calls (get_tracer, cast).


##### `span`  (lines 420–432)

```
def span(name: str, kind: SpanKind=SpanKind.INTERNAL, **attributes: object) -> Iterator[Span]
```

**Purpose**: Opens a smaller trace span around a named piece of work, such as a model call, tool call, or setup step. This helps operators see where time was spent inside a larger trace.

**Data flow**: It receives a span name, a span kind, and arbitrary attributes. It adds the ambient workspace, redacts sensitive data, flattens values into OpenTelemetry-friendly attribute values, starts the span, and yields it to the caller’s with-block.

**Call relations**: This is the general-purpose tracing helper used throughout runtime code. It shares the same workspace and redaction path as turn_span by calling _ambient_scope and redact_payload before it talks to OpenTelemetry.

*Call graph*: calls 2 internal fn (_ambient_scope, redact_payload); 1 external calls (get_tracer).


##### `redact_payload`  (lines 435–441)

```
def redact_payload(fields: Mapping[str, object]) -> dict[str, JsonValue]
```

**Purpose**: Removes sensitive fields from a dictionary before it becomes a log or trace attribute set. It is the main privacy filter for structured telemetry.

**Data flow**: It receives a mapping of field names to values. For each field, it normalizes the key by ignoring underscores, hyphens, and case, drops fields with sensitive names, and sends the remaining values through redact_value. It returns a new safe dictionary.

**Call relations**: _emit_log, span, and turn_span use this before sending data out. redact_value also calls it when it finds a nested dictionary, so the same rules apply inside nested structures.

*Call graph*: calls 1 internal fn (redact_value); called by 4 (_emit_log, redact_value, span, turn_span).


##### `redact_value`  (lines 444–454)

```
def redact_value(value: object) -> JsonValue
```

**Purpose**: Converts a value into something safe and JSON-like for telemetry. It keeps simple values, walks through lists and dictionaries, and stringifies anything unusual.

**Data flow**: It receives any Python object. Plain values such as strings, numbers, booleans, and None pass through. Dictionaries are converted key by key through redact_payload, sequences are converted item by item, and all other objects become strings.

**Call relations**: redact_payload calls this for each kept field. When it sees a nested mapping, it hands back to redact_payload so sensitive keys are still removed at deeper levels.

*Call graph*: calls 1 internal fn (redact_payload); called by 1 (redact_payload).


##### `log`  (lines 457–462)

```
def log(event: str, **fields: object) -> None
```

**Purpose**: Emits a structured informational event. It is for normal noteworthy events that should be searchable and tied to the current trace and workspace.

**Data flow**: It receives an event name and any number of fields. It passes them to _emit_log with info-level severity, where workspace tagging, redaction, standard logging, and OpenTelemetry emission happen.

**Call relations**: Application code calls this for ordinary structured logs. It is a friendly wrapper around _emit_log with the severity already chosen.

*Call graph*: calls 1 internal fn (_emit_log).


##### `log_error`  (lines 465–467)

```
def log_error(event: str, **fields: object) -> None
```

**Purpose**: Emits a structured error event. It is used when something has gone wrong and should be visible as an error in logs.

**Data flow**: It receives an event name and fields describing the failure. It passes them to _emit_log with error-level severity, which safely prepares and exports the record.

**Call relations**: Application code calls this on failure paths. It shares all redaction and workspace behavior with log and warn through _emit_log.

*Call graph*: calls 1 internal fn (_emit_log).


##### `warn`  (lines 470–472)

```
def warn(event: str, **fields: object) -> None
```

**Purpose**: Emits a structured warning event. It is for expected but important situations that operators may need to notice.

**Data flow**: It receives an event name and extra fields. It passes them to _emit_log with warning-level severity, which adds context, removes sensitive data, and sends the record.

**Call relations**: Application code calls this when something is unusual but not necessarily fatal. Like log and log_error, it delegates the actual work to _emit_log.

*Call graph*: calls 1 internal fn (_emit_log).


##### `formatted_stack`  (lines 475–504)

```
def formatted_stack(error: BaseException) -> str
```

**Purpose**: Turns an exception and its cause chain into a safe stack trace string. It deliberately includes exception class names and frames, but not exception messages, because messages may contain secrets or user-controlled text.

**Data flow**: It receives an exception. It walks through the exception, its explicit cause, or its context when not suppressed, collecting class names and traceback frames while avoiding loops. If the resulting text is short enough, it returns it whole; if too long, it keeps the beginning and end and replaces the middle with an elision note.

**Call relations**: This helper is meant for callers that want to attach failure location to logs without leaking sensitive message content. It uses Python traceback formatting but applies this file’s safety policy around messages and length.

*Call graph*: 1 external calls (format_tb).


##### `_emit_log`  (lines 507–521)

```
def _emit_log(event: str, severity_number: SeverityNumber, severity_text: str, level: int, fields: Mapping[str, object]) -> None
```

**Purpose**: Does the shared work behind info, warning, and error structured logs. It sends the same safe event to both standard Python logging and the OpenTelemetry log pipeline.

**Data flow**: It receives an event name, OpenTelemetry severity, text severity, Python logging level, and fields. It adds ambient workspace fields, redacts the combined data, writes a standard log record with the safe fields attached, and emits an OpenTelemetry log record with the same event and attributes.

**Call relations**: log, warn, and log_error all call this. It relies on _ambient_scope and redact_payload before handing data to Python logging and OpenTelemetry.

*Call graph*: calls 2 internal fn (_ambient_scope, redact_payload); called by 3 (log, log_error, warn); 2 external calls (getLogger, get_logger).


##### `_bounded_error_class`  (lines 524–537)

```
def _bounded_error_class(dimensions: dict[str, str]) -> dict[str, str]
```

**Purpose**: Keeps the error_class metric dimension under control. Known error class names pass through, while unknown names are grouped as other.

**Data flow**: It receives a dictionary of metric dimensions. If the error_class value is missing or is in the approved set, it returns the dimensions unchanged. If the value is unapproved, it returns a copy with error_class replaced by other.

**Call relations**: emit_metric and emit_histogram call this before recording measurements. It prevents failure paths from creating unbounded metric labels in the monitoring backend.

*Call graph*: called by 2 (emit_histogram, emit_metric).


##### `emit_metric`  (lines 540–550)

```
def emit_metric(name: str, amount: int=1, /, **dimensions: str) -> None
```

**Purpose**: Increments one of the registered counter metrics. A counter is used for things that happen as discrete events, such as a turn starting or a retry occurring.

**Data flow**: It receives a metric name, an amount to add, and dimension labels. It rejects unknown metric names, creates and caches the OpenTelemetry counter the first time that name is used, normalizes the error_class dimension, and adds the amount with the given attributes.

**Call relations**: Runtime code calls this when an event should be counted. Before the value reaches OpenTelemetry, _bounded_error_class protects the metric from unapproved error labels.

*Call graph*: calls 1 internal fn (_bounded_error_class); 1 external calls (get_meter).


##### `emit_histogram`  (lines 553–575)

```
def emit_histogram(name: str, value: int, /, **dimensions: str) -> None
```

**Purpose**: Records one timing or size observation for a registered histogram, usually in milliseconds. Histograms let operators ask questions like “what is the typical model latency?” or “how slow are the worst cases?”

**Data flow**: It receives a histogram name, a numeric value, and dimension labels. It rejects unknown histogram names and labels that were not declared for that histogram, creates and caches the OpenTelemetry histogram if needed, bounds the error_class label, and records the value.

**Call relations**: Runtime code calls this around measured work such as database waits, model rounds, tools, and turns. It uses _bounded_error_class before sending the observation to OpenTelemetry.

*Call graph*: calls 1 internal fn (_bounded_error_class); 1 external calls (get_meter).


##### `emit_up_down_metric`  (lines 578–590)

```
def emit_up_down_metric(name: str, amount: int, /, **dimensions: str) -> None
```

**Purpose**: Adjusts a registered metric that represents a current level rather than a forever-increasing count. For example, it can go up when work starts and down when work finishes.

**Data flow**: It receives a metric name, a signed amount, and dimension labels. It rejects unknown names and undeclared labels, creates and caches the OpenTelemetry up-down counter if needed, and adds the signed amount with the supplied attributes.

**Call relations**: Runtime code calls this for current-state measurements such as active model rounds. Unlike counters and histograms, it does not route through _bounded_error_class because its declared dimensions do not include that label.

*Call graph*: 1 external calls (get_meter).

## 📊 State Registers Touched

- `reg-effective-config` — The deployment’s active settings, such as enabled services, limits, paths, providers, and safety options.
- `reg-auth-tokens` — Signed passes that prove who a caller is or allow short-lived access to protected routes and links.
- `reg-model-catalog` — The lookup table of available AI models, providers, routing details, capabilities, and pricing metadata.
- `reg-turn-queue` — The durable waiting line and status record for each unit of agent work, from queued to running to finished or failed.
- `reg-live-hub` — The live stream state that lets browsers, terminals, and operators watch progress and reconnect without losing updates.
- `reg-sandbox-network-policy` — The per-run rules and proxy state that decide what sandboxed code may reach on the internet and when secrets may be injected.
- `reg-accounting-ledger` — Usage, cost, spend limits, prepaid balances, billing exports, and price versions for workspace spending.
- `reg-hosted-site-state` — Hosted sandbox sites, their public addresses, generations, ports, visibility, and unhosting status.
- `reg-observability-context` — Trace IDs, metrics, logs, and sanitized operational events used to understand work across services and turns.
- `reg-audit-access-log` — Durable audit records such as transcript-access events and security-relevant reads or administrative actions.
- `reg-self-improvement-state` — Offline replay, failure-analysis, prompt-experiment, and governed update-proposal state used by self-improvement jobs.
- `reg-execution-scope-context` — Per-request, per-job, and per-turn scoped context carrying the active workspace, member, agent, turn, permissions, credentials, billing, and service handles through core code.
- `reg-prompt-version-state` — Prompt-render metadata such as system-prompt digests, contributed sections, cutoff/version information, and replay identifiers attached to turns for change detection and evaluation.
- `reg-provider-client-pools` — Per-process reusable transport/client state for external providers such as model APIs, search and embedding services, connector brokers, browser providers, billing services, and related retry or throttle windows.
- `reg-payment-provider-state` — Stripe or billing-provider setup state such as customer identifiers, checkout/payment-session progress, and subscription or purchase linkage for workspaces.

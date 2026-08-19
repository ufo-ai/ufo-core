# Billing and self-improvement background processing  `stage-16.2`

This stage is background support for two jobs that run alongside the main product: keeping billing accurate and helping agents improve safely. The billing side acts like a cash register and fuel gauge. accounting.py records each workspace’s usage, applies spend limits, subtracts prepaid funds, and prepares reports for billing systems. balance.py tracks prepaid credit in tiny dollar units, deciding whether paid model work can continue, needs a top-up, or must stop. ufo_ext_metronome.py connects those records to Metronome for usage reporting and to Stripe for saved cards, portal links, and automatic refills.

The self-improvement side studies past conversations without changing the outside world. corpus.py selects useful failed examples and splits them for learning and testing. proposer.py asks a model to suggest a better system prompt, meaning the instruction text that guides an agent. replay.py reruns old conversations with the new prompt while reusing old tool results, so no emails, purchases, or other actions happen again. evaluation.py grades old versus new answers, gate.py accepts only reliable improvements, cron.py schedules repeated checks, and model.py gives all of this one safe, metered way to call the language model.

## Files in this stage

### Billing ledgers and integrations
Tracks prepaid balance and workspace spend, then exposes usage reporting, payment methods, portal links, and top-ups through Metronome and Stripe.

### `core/src/ufo/accounting.py`

`domain_logic` · `cross-cutting: turn admission, usage recording, billing export, and spend reporting`

This file keeps the project’s financial record of work done by agents and sandboxes. The central idea is a ledger: a database table that works like a checkbook, with one row or growing row for each kind of usage, such as model tokens, sandbox network requests, generated images, or videos. Without this file, the system could answer users but would not reliably know who should pay, whether a workspace has run out of credit, or how much to show in reports.

It first turns raw usage into priced amounts, using “micro-USD” so money can be stored as whole numbers instead of fragile decimals. It writes ledger rows carefully so retries do not double-charge the same model run, while repeated sandbox or media events can safely add to an existing row. It also distinguishes platform-paid usage from “bring your own key” usage, where the workspace’s own provider key paid the model vendor directly.

The file then uses the same ledger for three jobs: deciding if a turn may start or continue, checking rolling spend caps for workspaces, members, and agents, and reading totals for dashboards. Finally, it can freeze unexported ledger growth into delivery records for an external billing consumer, so retries send the same billable facts instead of recalculating them differently.

#### Function details

##### `applicable_caps_absent`  (lines 56–62)

```
def applicable_caps_absent(workspace_id: UUID, member_id: UUID | None, agent_id: UUID) -> bool
```

**Purpose**: This is a quick shortcut for the common case where a workspace has no spend caps that apply to a particular workspace/member/agent combination. It lets the caller skip a database check for a few seconds after a recent no-cap result.

**Data flow**: It receives a workspace ID, an optional member ID, and an agent ID. It looks up that exact triple in a small in-memory cache and compares the stored expiry time with the current clock. It returns true only if a recent lookup already found no matching caps and that result has not expired.

**Call relations**: This function is a read-only fast path. When SpendEvaluator.decide later discovers that no caps apply, it records that fact through _note_absent_caps, and future callers can consult applicable_caps_absent before doing the heavier database work.

*Call graph*: 1 external calls (monotonic).


##### `_note_absent_caps`  (lines 65–74)

```
def _note_absent_caps(key: tuple[UUID, UUID | None, UUID]) -> None
```

**Purpose**: This remembers, briefly, that no spend caps apply to one workspace/member/agent combination. It is a performance helper, not a source of truth.

**Data flow**: It receives a cache key made from workspace ID, member ID, and agent ID. It checks the current time, removes expired entries if the cache is full, then stores a new expiry time a few seconds in the future. It changes only the in-memory cache.

**Call relations**: SpendEvaluator.decide calls this after it has checked the database and found no applicable caps. That allows later admission checks to avoid repeating the same database query for a short time.

*Call graph*: called by 1 (decide); 1 external calls (monotonic).


##### `_total_tokens`  (lines 77–85)

```
def _total_tokens(usage: Usage) -> int
```

**Purpose**: This adds up all token categories from a model usage record into one total count. It gives the ledger a single “how many tokens were used” number.

**Data flow**: It receives a Usage object containing input, output, cache-read, and cache-write token counts. It sums those fields and returns the integer total. It does not read or change anything outside that object.

**Call relations**: record_turn_usage, record_workspace_usage, and record_sandbox_tokens call this before writing token usage. If the total is zero, those writers skip billing entirely.

*Call graph*: called by 3 (record_sandbox_tokens, record_turn_usage, record_workspace_usage).


##### `_prompt_tokens`  (lines 88–97)

```
def _prompt_tokens(usage: Usage) -> int
```

**Purpose**: This counts the tokens the model read as its prompt, including tokens served from cache. That number is later used to explain what share of the prompt came from cache.

**Data flow**: It receives a Usage object and adds input tokens plus all cache-read and cache-write prompt-side tokens. It returns that prompt-token count without changing anything.

**Call relations**: The token ledger writers call this beside _total_tokens. read_turn_cost later uses the stored prompt and cache-read counts to calculate a cache percentage from the same ledger facts used for billing.

*Call graph*: called by 3 (record_sandbox_tokens, record_turn_usage, record_workspace_usage).


##### `workspace_owns_the_key`  (lines 100–114)

```
async def workspace_owns_the_key(connection: AsyncConnection, workspace_id: UUID, key_slot: str | None) -> bool
```

**Purpose**: This answers whether a named provider key slot belongs to the workspace. That matters because usage paid through the workspace’s own provider key should not also be charged against the platform balance.

**Data flow**: It receives a database connection, workspace ID, and optional key slot name. If no slot is given, it returns false. Otherwise it queries the credential table for that workspace and slot, then returns true if a matching credential row exists.

**Call relations**: BalanceGate._workspace_serves_itself uses this when deciding whether a turn can start even though the prepaid balance is below the usual reserve. It keeps balance behavior aligned with the usage records’ own-key labeling.

*Call graph*: called by 1 (_workspace_serves_itself); 2 external calls (execute, select).


##### `record_turn_usage`  (lines 117–180)

```
async def record_turn_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, usage: Usage, attempt: str='', pricing: Pricing=CORE_PRICING, byok: bool=False) -> None
```

**Purpose**: This records the model-token cost for one turn attempt. It is designed so a replay of the same attempt does not charge twice, while a resumed turn can still record the extra real provider usage it caused.

**Data flow**: It receives a database connection, workspace and turn IDs, model name, usage counts, an attempt ID, pricing data, and a byok flag. It totals the tokens, builds a stable ledger ID, skips the write if that ID already exists, calculates the price, optionally debits the workspace balance, then inserts a ledger row with token breakdown, price, debit amount, model, and pricing digest. The output is the database update; the function returns nothing.

**Call relations**: This is one of the main billing writers. It uses _total_tokens and _prompt_tokens for counts, Pricing.micro_usd for cost, debit to subtract prepaid balance, and ledger_id_for to make the write replay-safe.

*Call graph*: calls 3 internal fn (_prompt_tokens, _total_tokens, micro_usd); 5 external calls (execute, insert, select, debit, ledger_id_for).


##### `read_turn_cost`  (lines 194–224)

```
async def read_turn_cost(connection: AsyncConnection, turn_id: UUID, dimension: str) -> TurnCost | None
```

**Purpose**: This reads what one turn spent in a chosen ledger dimension, such as normal model tokens or sandbox model tokens. It returns a small summary suitable for showing at the end of a turn.

**Data flow**: It receives a connection, turn ID, and dimension name. It sums matching ledger rows for token amount, priced cost, prompt tokens, and cache-read tokens, then returns a TurnCost with total tokens, micro-USD cost, model name, and cache percentage. If no ledger rows exist for that dimension, it returns null.

**Call relations**: This function consumes the ledger rows written by record_turn_usage and record_sandbox_tokens. It deliberately sums across attempts so a parked and resumed turn shows the full actual cost.

*Call graph*: 3 external calls (__init__, execute, select).


##### `record_workspace_usage`  (lines 227–276)

```
async def record_workspace_usage(connection: AsyncConnection, workspace_id: UUID, model: str, usage: Usage, pricing: Pricing=CORE_PRICING, byok: bool=False) -> None
```

**Purpose**: This records model-token usage that belongs to a workspace but not to a specific turn, such as a background job. It makes that spend visible in workspace totals and workspace-level caps.

**Data flow**: It receives a connection, workspace ID, model name, usage counts, pricing data, and a byok flag. It totals tokens, skips zero usage, computes the price, possibly debits the balance, and inserts a new ledger row with no turn ID. Each real completion gets a fresh ledger ID.

**Call relations**: This follows the same pricing and balance-debit rules as record_turn_usage, but it does not attach spend to a member or agent because there is no turn to join through.

*Call graph*: calls 3 internal fn (_prompt_tokens, _total_tokens, micro_usd); 4 external calls (execute, insert, debit, uuid4).


##### `record_egress_request`  (lines 279–308)

```
async def record_egress_request(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, amount: int=1) -> None
```

**Purpose**: This counts sandbox network egress requests made during a turn. It records request volume, not money, because these rows are priced at zero.

**Data flow**: It receives a connection, workspace ID, turn ID, and request count. It builds a stable ledger ID for the turn’s egress dimension, then inserts a row or atomically adds to the existing row’s amount. It does not debit the balance.

**Call relations**: The sandbox egress proxy can call this repeatedly while a turn runs. It uses ledger_id_for so all egress for the same turn accumulates into one egress row instead of colliding with token billing.

*Call graph*: 2 external calls (execute, ledger_id_for).


##### `record_probe_egress_request`  (lines 311–337)

```
async def record_probe_egress_request(connection: AsyncConnection, workspace_id: UUID, amount: int=1) -> None
```

**Purpose**: This counts sandbox egress requests made by an off-turn probe. A probe is workspace activity, but it is not tied to a conversation turn.

**Data flow**: It receives a connection, workspace ID, and request count. It inserts a fresh zero-priced egress ledger row with no turn ID. The function changes the database and returns nothing.

**Call relations**: This is the off-turn counterpart to record_egress_request. Its rows affect workspace-level totals, but they naturally drop out of member and agent reports because there is no turn to connect them to.

*Call graph*: 3 external calls (execute, insert, uuid4).


##### `record_sandbox_tokens`  (lines 340–416)

```
async def record_sandbox_tokens(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, usage: Usage, pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: This records model-token usage caused by code running inside the sandbox through the egress proxy. It keeps that spend separate from the host turn loop’s own model calls.

**Data flow**: It receives a connection, workspace and turn IDs, model name, usage counts, and pricing data. It totals tokens, computes prompt tokens and price, debits the workspace balance, then inserts or atomically adds to a per-turn sandbox-token ledger row. The row stores token class counts, total price, debit amount, model, and price digest.

**Call relations**: This writer uses _total_tokens, _prompt_tokens, Pricing.micro_usd, debit, and ledger_id_for. read_turn_cost can later read the sandbox_tokens dimension to report the sandbox-side model cost.

*Call graph*: calls 3 internal fn (_prompt_tokens, _total_tokens, micro_usd); 3 external calls (execute, debit, ledger_id_for).


##### `record_image_usage`  (lines 419–438)

```
async def record_image_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, images: int, micro_usd: int) -> None
```

**Purpose**: This records generated image usage for a turn. The caller supplies the price because image providers may charge by units that are not normal text tokens.

**Data flow**: It receives a connection, workspace and turn IDs, model name, image count, and micro-USD cost. It passes those facts to _record_media_usage with the images dimension. The database update happens in the helper.

**Call relations**: This is a thin, readable wrapper around _record_media_usage. Provider-specific image code can call it without knowing the shared media-ledger mechanics.

*Call graph*: calls 1 internal fn (_record_media_usage).


##### `record_video_usage`  (lines 441–455)

```
async def record_video_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, videos: int, micro_usd: int) -> None
```

**Purpose**: This records generated video usage for a turn. Like image usage, the caller supplies the cost because video pricing does not fit the text-token pricing table.

**Data flow**: It receives a connection, workspace and turn IDs, model name, video count, and micro-USD cost. It forwards those values to _record_media_usage using the videos dimension. It returns after the helper writes the ledger update.

**Call relations**: This mirrors record_image_usage and shares the same media billing path through _record_media_usage.

*Call graph*: calls 1 internal fn (_record_media_usage).


##### `_record_media_usage`  (lines 458–499)

```
async def _record_media_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, dimension: str, model: str, amount: int, micro_usd: int) -> None
```

**Purpose**: This is the shared writer for generated images and videos. It records the count, records the provider cost, and subtracts that cost from the workspace balance.

**Data flow**: It receives a connection, workspace and turn IDs, a media dimension name, model name, amount, and micro-USD cost. It builds a stable per-turn ledger ID, debits the balance for this increment, then inserts a ledger row or atomically adds the amount and costs to the existing row.

**Call relations**: record_image_usage and record_video_usage call this so both media types follow the same ledger and debit rules. It is intentionally different from egress recording because media costs real money.

*Call graph*: called by 2 (record_image_usage, record_video_usage); 3 external calls (execute, debit, ledger_id_for).


##### `mint_usage_exports`  (lines 524–639)

```
async def mint_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, floor: datetime, key_slot_for: Callable[[str], str | None]) -> None
```

**Purpose**: This freezes new billable ledger growth into export-intent rows for an outside billing consumer. Freezing matters because retries must send the same facts, not a newly recalculated bill.

**Data flow**: It receives a connection, workspace ID, consumer name, lower time bound, and a function that maps model names to key slots. It finds ledger rows whose unexported amount has grown and is ready to settle, compares them with the consumer’s last exported high-water mark, decides the byok value, and inserts ledger_export rows keyed so duplicate mints collapse safely. It does not send anything outside the database.

**Call relations**: This sits between ledger writers and external billing delivery. read_pending_usage_exports later reads the frozen intents, and ack_usage_exports marks them done after the outside consumer accepts them.

*Call graph*: 5 external calls (now, timedelta, execute, or_, select).


##### `read_pending_usage_exports`  (lines 642–687)

```
async def read_pending_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, limit: int) -> tuple[UsageExport, ...]
```

**Purpose**: This reads usage-export records that have been minted but not yet acknowledged. It gives a billing sender a stable batch to deliver.

**Data flow**: It receives a connection, workspace ID, consumer name, and limit. It joins pending ledger_export rows to their ledger rows, calculates the exported delta amounts and costs, and returns a tuple of UsageExport objects. It does not change the database.

**Call relations**: A background export job would call this after mint_usage_exports. If delivery fails before acknowledgment, this function will return the same frozen records again.

*Call graph*: 3 external calls (__init__, execute, select).


##### `ack_usage_exports`  (lines 690–714)

```
async def ack_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: This marks exported usage records as acknowledged after an external billing consumer has accepted them. That removes them from future pending reads.

**Data flow**: It receives a connection, workspace ID, consumer name, and the UsageExport objects that were successfully delivered. It builds matching conditions from each export’s ledger ID and starting amount, then updates those rows with an acknowledgment time.

**Call relations**: This is called only after read_pending_usage_exports has supplied records and the external API has accepted the batch. If a crash happens before this update, the same records stay pending and can be safely retried.

*Call graph*: 3 external calls (execute, or_, update).


##### `metered_workspaces`  (lines 717–720)

```
def metered_workspaces() -> WorkspaceCandidates
```

**Purpose**: This returns candidate workspaces for a usage-export job. It includes any workspace that has ever had ledger activity.

**Data flow**: It builds a database query selecting distinct workspace IDs from the ledger and wraps it in the project’s WorkspaceCandidates helper. The result is not the rows themselves, but a candidate source that another job can iterate.

**Call relations**: Usage export scheduling can call this to decide which workspaces are worth checking. The later per-workspace pending read is allowed to be a no-op when everything is already exported.

*Call graph*: 1 external calls (owner_candidates).


##### `SpendEvaluator.decide`  (lines 756–771)

```
async def decide(self, connection: AsyncConnection, pending_micro_usd: int) -> SpendDecision
```

**Purpose**: This decides whether a turn is allowed, should be parked for later, or should be rejected because of spend caps. A spend cap is a rolling spending limit over a recent time window.

**Data flow**: It receives a connection and the micro-USD cost that is about to be added. It loads caps that apply to this workspace/member/agent, caches the no-cap case, sums recent ledger usage for each cap, compares used plus pending cost against each limit, then returns a SpendDecision with an outcome and message.

**Call relations**: This is the main spend-cap workflow. It calls _applicable_caps to find rules, _used_micro_usd to measure current spend, _note_absent_caps for the no-cap fast path, and _message when it needs to explain a breach.

*Call graph*: calls 4 internal fn (_applicable_caps, _message, _used_micro_usd, _note_absent_caps); 1 external calls (__init__).


##### `SpendEvaluator._applicable_caps`  (lines 773–801)

```
async def _applicable_caps(self, connection: AsyncConnection) -> tuple[SpendCap, ...]
```

**Purpose**: This finds the spend caps that apply to the turn being evaluated. Caps can apply to the whole workspace, one member, or one agent.

**Data flow**: It receives a database connection and reads spend_cap rows for the evaluator’s workspace. It keeps workspace-wide rows plus rows matching the evaluator’s member ID or agent ID, converts them into SpendCap objects, and returns them as a tuple.

**Call relations**: SpendEvaluator.decide calls this first. Its result determines whether the decision can immediately allow the turn or must measure usage against cap windows.

*Call graph*: called by 1 (decide); 4 external calls (__init__, execute, or_, select).


##### `SpendEvaluator._used_micro_usd`  (lines 803–829)

```
async def _used_micro_usd(self, connection: AsyncConnection, cap: SpendCap) -> int
```

**Purpose**: This calculates how much money has already been spent inside one cap’s rolling window. It uses the ledger as the source of truth.

**Data flow**: It receives a connection and one SpendCap. It computes a cutoff time from the cap’s window length, builds the right query for workspace, member, or agent scope, sums priced_micro_usd since that cutoff, and returns the integer total.

**Call relations**: SpendEvaluator.decide calls this once for each applicable cap. The returned used amount is combined with pending spend to decide whether the cap has room left.

*Call graph*: called by 1 (decide); 4 external calls (now, timedelta, execute, select).


##### `SpendEvaluator._message`  (lines 831–842)

```
def _message(self, outcome: SpendOutcome, breaches: list[SpendCap]) -> str
```

**Purpose**: This turns a cap breach into a user-facing explanation. It chooses the tightest breached cap and explains whether the turn was parked or declined.

**Data flow**: It receives the chosen outcome and the list of breached caps. It picks the cap with the lowest limit, converts micro-USD to dollars for readability, and returns a sentence describing the cap that was reached.

**Call relations**: SpendEvaluator.decide calls this only after it has found at least one breach. The returned text is placed into the SpendDecision for the caller to show or store.

*Call graph*: called by 1 (decide).


##### `_token_sum`  (lines 959–967)

```
def _token_sum() -> sa.ColumnElement[int]
```

**Purpose**: This builds a database expression that sums only token-bearing ledger dimensions. It prevents image, video, and egress counts from being mistaken for text tokens.

**Data flow**: It takes no runtime data directly. It returns a SQL expression that adds ledger amounts when the dimension is normal tokens or sandbox tokens, and otherwise contributes zero. The expression is used inside larger database queries.

**Call relations**: SpendRollup.read, SpendRollup._by_origin, and _usage_details use this helper whenever they need token totals that mean actual model tokens.

*Call graph*: called by 3 (_by_origin, read, _usage_details); 1 external calls (case).


##### `_token_cost_sum`  (lines 970–982)

```
def _token_cost_sum() -> sa.ColumnElement[int]
```

**Purpose**: This builds a database expression that sums costs only for token-bearing dimensions. It separates token cost from total cost, which may also include images or videos.

**Data flow**: It takes no direct inputs and returns a SQL expression. The expression adds priced_micro_usd for normal token and sandbox-token rows, and adds zero for other dimensions.

**Call relations**: SpendRollup.read, SpendRollup._by_origin, and _usage_details use this beside _token_sum to produce token-specific cost breakdowns.

*Call graph*: called by 3 (_by_origin, read, _usage_details); 1 external calls (case).


##### `_usage_details`  (lines 985–1109)

```
async def _usage_details(connection: AsyncConnection, source: sa.FromClause, scope: sa.ColumnElement[bool], cutoff: datetime | None, now: datetime) -> UsageDetails
```

**Purpose**: This builds the reusable “usage details” section for spend reports. It includes selected-range totals, all-time totals, daily history, breakdown by execution style, breakdown by model, and comparison with the previous range.

**Data flow**: It receives a connection, a database source to query from, a scope condition, an optional cutoff time, and the current time. It runs several aggregate queries over the ledger, fills missing days with zeroes, normalizes the first-used timestamp, and returns a UsageDetails object.

**Call relations**: SpendRollup.read, SpendRollup.read_agent, and SpendRollup.read_member all call this so workspace, agent, and member reports present usage in the same shape.

*Call graph*: calls 2 internal fn (_token_cost_sum, _token_sum); called by 3 (read, read_agent, read_member); 9 external calls (__init__, __init__, __init__, __init__, fromisoformat, date, timedelta, execute, select).


##### `SpendRollup.read`  (lines 1120–1223)

```
async def read(self, connection: AsyncConnection, window_seconds: int | None) -> SpendReport
```

**Purpose**: This reads a full workspace spend report for either all time or a selected recent window. It is the main dashboard-style summary for a workspace.

**Data flow**: It receives a connection and an optional window length in seconds. It computes the cutoff, sums total spend, groups spend by dimension, member, agent, price digest, and origin, then adds detailed usage history through _usage_details. It returns a SpendReport object.

**Call relations**: This is the top-level rollup method for workspace accounting. It relies on _token_sum and _token_cost_sum for token-safe totals, _by_origin for where work started, and _usage_details for reusable time-series details.

*Call graph*: calls 4 internal fn (_by_origin, _token_cost_sum, _token_sum, _usage_details); 8 external calls (__init__, __init__, __init__, __init__, now, timedelta, execute, select).


##### `SpendRollup._by_origin`  (lines 1225–1290)

```
async def _by_origin(self, connection: AsyncConnection, window: sa.ColumnElement[bool]) -> tuple[OriginTotal, ...]
```

**Purpose**: This groups token spend by the place where the work originally started, such as a chat surface or channel. It follows parent turns so subagent work is credited back to the visible conversation that spawned it.

**Data flow**: It receives a connection and a window condition. It builds a recursive database query, meaning a query that walks parent links step by step, to find each spending turn’s root conversation. It then groups token totals and token costs by that conversation’s surface label, returning OriginTotal objects.

**Call relations**: SpendRollup.read calls this for the workspace report. It uses _token_sum and _token_cost_sum so only model-token dimensions contribute to origin token totals.

*Call graph*: calls 2 internal fn (_token_cost_sum, _token_sum); called by 1 (read); 4 external calls (__init__, desc, execute, select).


##### `SpendRollup.read_agent`  (lines 1292–1348)

```
async def read_agent(self, connection: AsyncConnection, agent_id: UUID, window_seconds: int | None) -> AgentSpendReport
```

**Purpose**: This reads spend and usage for one agent, including that agent’s spend caps. It excludes turn-less workspace jobs because they do not belong to an agent.

**Data flow**: It receives a connection, agent ID, and optional window length. It joins ledger rows to turns for that agent, groups selected-window spend by dimension, reads agent-scoped caps, calls _usage_details for selected and all-time usage, and returns an AgentSpendReport.

**Call relations**: This is the agent-specific counterpart to SpendRollup.read. It shares the common usage-detail builder but uses an agent filter instead of the whole workspace scope.

*Call graph*: calls 1 internal fn (_usage_details); 7 external calls (__init__, __init__, __init__, now, timedelta, execute, select).


##### `SpendRollup.read_member`  (lines 1350–1408)

```
async def read_member(self, connection: AsyncConnection, member_id: UUID, window_seconds: int | None) -> MemberSpendReport
```

**Purpose**: This reads spend and usage for one workspace member, plus that member’s spend caps. It does not expose other members’ names or agents beyond the member’s own usage shape.

**Data flow**: It receives a connection, member ID, and optional window length. It joins ledger rows through turns to conversations, filters conversations owned by the member, groups spend by dimension, reads member-scoped caps, calls _usage_details, and returns a MemberSpendReport.

**Call relations**: This mirrors SpendRollup.read_agent, but reaches ownership through conversation.member_id because that is how member-level spending is attributed elsewhere, including cap checks.

*Call graph*: calls 1 internal fn (_usage_details); 7 external calls (__init__, __init__, __init__, now, timedelta, execute, select).


##### `BalanceGate.admits`  (lines 1435–1473)

```
async def admits(self, connection: AsyncConnection, agent_id: UUID | None=None, key_slot_for: Callable[[str], str | None] | None=None, turn_id: UUID | None=None, model: str | None=None) -> SpendDecisi
```

**Purpose**: This decides whether a workspace has enough prepaid balance for a turn to start, be folded into live work, or resume after parking. It uses a stricter start threshold than the continue threshold to avoid stop-start thrashing.

**Data flow**: It receives a connection plus optional agent, key-slot lookup, turn ID, and model. It reads balance headroom, allows work if no balance row exists, allows if balance is above the reserve after grace, rejects already-debited parked turns below the line, allows some positive-balance own-key model work, and otherwise returns a rejection message.

**Call relations**: This is the entry gate for turns. It calls read_headroom for balance state, _workspace_serves_itself for the own-key exception, _turn_has_debited to prevent endless resume/repark loops, and _forget_absent_balance when a real balance row is found.

*Call graph*: calls 2 internal fn (_turn_has_debited, _workspace_serves_itself); 3 external calls (__init__, _forget_absent_balance, read_headroom).


##### `BalanceGate._workspace_serves_itself`  (lines 1475–1497)

```
async def _workspace_serves_itself(self, connection: AsyncConnection, agent_id: UUID | None, key_slot_for: Callable[[str], str | None] | None, model: str | None=None) -> bool
```

**Purpose**: This checks whether the model call for a turn will be served using the workspace’s own provider key. That can let a low-balance workspace start model work that will not debit the platform balance.

**Data flow**: It receives a connection, optional agent ID, optional model-to-key-slot lookup, and optional model name. If no lookup is available, it returns false. If no model was supplied, it reads the agent’s model from the database. It then asks workspace_owns_the_key whether the workspace owns the slot for that model.

**Call relations**: BalanceGate.admits calls this for the own-key admission exception. It delegates the final credential-table check to workspace_owns_the_key.

*Call graph*: calls 1 internal fn (workspace_owns_the_key); called by 1 (admits); 2 external calls (execute, select).


##### `BalanceGate.sustains`  (lines 1499–1520)

```
async def sustains(self, connection: AsyncConnection, pending_micro_usd: int, turn_id: UUID | None=None) -> SpendDecision
```

**Purpose**: This decides whether a turn that is already running may take another round of work. Unlike admission, continuation stops only when the balance would go below zero after grace, not when it falls below the starting reserve.

**Data flow**: It receives a connection, pending micro-USD cost, and optional turn ID. It reads balance headroom, allows if there is no balance row, allows if current balance minus pending cost stays above the grace-adjusted floor, rejects if real debit would push it under or the turn has already debited, and otherwise allows zero-debit work.

**Call relations**: This is the mid-turn balance check. It calls read_headroom, _forget_absent_balance, and _turn_has_debited, returning the same SpendDecision shape used by cap checks and admission.

*Call graph*: calls 1 internal fn (_turn_has_debited); 3 external calls (__init__, _forget_absent_balance, read_headroom).


##### `BalanceGate._turn_has_debited`  (lines 1522–1545)

```
async def _turn_has_debited(self, connection: AsyncConnection, turn_id: UUID | None) -> bool
```

**Purpose**: This checks whether a turn has already taken any money from the workspace balance. It looks at actual debits, not just priced cost, so own-key model rows do not count as balance spend.

**Data flow**: It receives a connection and optional turn ID. If there is no turn ID, it returns false. Otherwise it queries the ledger for any row on that turn with debited_micro_usd greater than zero, returning true if one exists.

**Call relations**: BalanceGate.admits and BalanceGate.sustains use this to avoid pathological loops: a turn that has already charged the balance should not keep being readmitted below the safe line only to charge and park again.

*Call graph*: called by 2 (admits, sustains); 2 external calls (execute, select).


### `core/src/ufo/balance.py`

`domain_logic` · `cross-cutting billing checks, admin billing reads, credit/debit transactions`

This file is the project’s wallet ledger for each workspace. A workspace can receive credit through purchases, grants, refunds, or corrections, and it spends that credit as model work happens. The file keeps a current balance row for quick checks, because checking before every model round must be cheap. It also keeps purchase records so the balance can be audited later, like keeping both a cash register total and the receipts that prove it.

The main idea is simple: `credit` adds money once per payment reference, `debit` subtracts money when work is recorded, and read functions show either a quick gate-friendly view or a fuller admin-friendly view. Some reads return `None` when a workspace has never had a balance row, which is important for self-hosted deployments that may not charge anything.

There is also support for automatic top-ups. The file stores the amount to refill and the low-balance threshold that triggers it. A workspace whose card has successfully paid before may get a fixed grace amount, meaning it can briefly go below zero while the refill job and payment provider catch up. This avoids blocking a solvent workspace just because external payment timing is not instant.

#### Function details

##### `balance_absent`  (lines 37–43)

```
def balance_absent(workspace_id: UUID) -> bool
```

**Purpose**: This is a quick, database-free check for workspaces that were recently found to have no balance row. It helps skip repeated balance reads for self-hosted or never-credited workspaces, but it is only a shortcut, not the source of truth.

**Data flow**: It receives a workspace ID and looks in a small in-memory map for an expiry time. It compares that expiry to the current monotonic clock, which is a steady timer used for measuring time intervals. It returns `True` only if the workspace was recently marked as having no balance and that mark has not expired.

**Call relations**: This function stands at the front of a fast path used before doing costlier billing work. It relies on marks written by `_note_absent_balance`; if a later credit creates a balance row, `credit` clears the mark through `_forget_absent_balance` so this shortcut does not hide real credit.

*Call graph*: 1 external calls (monotonic).


##### `_note_absent_balance`  (lines 46–53)

```
def _note_absent_balance(workspace_id: UUID) -> None
```

**Purpose**: This remembers, for a few seconds, that a workspace had no balance row. It prevents the system from asking the database again and again for the same missing row.

**Data flow**: It receives a workspace ID, checks the current time, and stores an expiry time in an in-memory dictionary. If the dictionary is already at its size limit, it first removes expired entries so the process does not grow memory forever. The result is a temporary absent mark; nothing is returned.

**Call relations**: This is called by `read_headroom` and `read_balance` when their database lookup finds no balance row. Later, `balance_absent` can use that mark to answer quickly, and `credit` can erase it through `_forget_absent_balance` when the workspace finally receives credit.

*Call graph*: called by 2 (read_balance, read_headroom); 1 external calls (monotonic).


##### `_forget_absent_balance`  (lines 56–59)

```
def _forget_absent_balance(workspace_id: UUID) -> None
```

**Purpose**: This removes the temporary note that says a workspace has no balance. It is used when the system has just created or seen a real balance, so future checks do not incorrectly skip billing gates.

**Data flow**: It receives a workspace ID and removes that ID from the in-memory absent-balance map if it is present. If there is no mark, it does nothing. It returns no value and only changes the cache.

**Call relations**: This is called by `credit` after money has successfully been added to a workspace balance. That keeps the shortcut used by `balance_absent` safe: a workspace that was once empty but is now credited will no longer be treated as absent.

*Call graph*: called by 1 (credit).


##### `read_auto_topup`  (lines 85–107)

```
async def read_auto_topup(connection: AsyncConnection, workspace_id: UUID) -> AutoTopup | None
```

**Purpose**: This checks whether a workspace is currently eligible for an automatic refill. It returns the refill settings only when the workspace has configured auto top-up and its balance is at or below the trigger threshold.

**Data flow**: It receives a database connection and workspace ID. It reads the workspace’s current balance, top-up amount, and top-up threshold from the balance table. If there is no row, no configured top-up amount, or the balance is still above the threshold, it returns `None`; otherwise it returns an `AutoTopup` object with the refill amount and threshold.

**Call relations**: This function is shaped for the refill worker’s question: “Should this workspace be topped up now?” It uses a database select and creates an `AutoTopup` value only when the answer is yes, leaving payment-card details to whatever external billing extension actually charges the card.

*Call graph*: 3 external calls (__init__, execute, select).


##### `set_auto_topup`  (lines 110–130)

```
async def set_auto_topup(connection: AsyncConnection, workspace_id: UUID, amount_micro_usd: int | None, threshold_micro_usd: int | None) -> bool
```

**Purpose**: This turns automatic refills on or off for a workspace that already has a balance row. It prevents half-configured auto top-up by requiring both the refill amount and threshold together, or neither.

**Data flow**: It receives a database connection, workspace ID, and optional amount and threshold. If only one of amount or threshold is provided, it raises a `ValueError` because that would be an incomplete rule. Otherwise it updates the existing balance row and returns `True` if exactly one row was changed, or `False` if the workspace had no balance row to update.

**Call relations**: This is typically used when an admin changes billing settings. It writes directly to the balance table; later, `read_auto_topup` can decide whether those settings have been reached, and `configured_auto_topup` can report the saved settings back even if the threshold has not been reached.

*Call graph*: 2 external calls (execute, update).


##### `mark_topup_verified`  (lines 133–147)

```
async def mark_topup_verified(connection: AsyncConnection, workspace_id: UUID) -> None
```

**Purpose**: This records that a workspace’s payment card has successfully settled a top-up charge. That successful payment earns the workspace a fixed grace allowance for future brief overdrafts.

**Data flow**: It receives a database connection and workspace ID. It updates the balance row only if the verification timestamp is still empty, stamping it with the current database time and updating the row’s modification time. It returns nothing.

**Call relations**: This is used after a top-up payment has truly settled. Later, `read_headroom` reads the verification timestamp and, if it exists, includes the fixed grace amount that lets the workspace continue briefly while future refills are in progress.

*Call graph*: 2 external calls (execute, update).


##### `configured_auto_topup`  (lines 159–180)

```
async def configured_auto_topup(connection: AsyncConnection, workspace_id: UUID) -> AutoTopup | None
```

**Purpose**: This reports the auto top-up rule a workspace has saved, whether or not the balance is currently low enough to trigger it. It is useful for showing admins what they configured.

**Data flow**: It receives a database connection and workspace ID. It reads the saved top-up amount and threshold from the balance row. If there is no row or no top-up amount, it returns `None`; otherwise it returns an `AutoTopup` object with the stored settings.

**Call relations**: This complements `read_auto_topup`. `read_auto_topup` is for the refill job deciding whether to act now, while `configured_auto_topup` is for callers that need to display or inspect the saved rule even when no refill is currently due.

*Call graph*: 3 external calls (__init__, execute, select).


##### `read_headroom`  (lines 183–202)

```
async def read_headroom(connection: AsyncConnection, workspace_id: UUID) -> Headroom | None
```

**Purpose**: This gives the small set of numbers needed to decide whether paid work may begin: current balance, required reserve, and any earned grace. It is intentionally lighter than the full balance read because it can run before every model round.

**Data flow**: It receives a database connection and workspace ID. It reads the current balance, reserve, and top-up verification timestamp. If no balance row exists, it notes that absence in the short-lived cache and returns `None`; otherwise it returns a `Headroom` object, adding the fixed grace amount only when a top-up has been verified before.

**Call relations**: This is the fast billing-gate read. When it finds no balance row, it hands that information to `_note_absent_balance` so later checks may use `balance_absent`; when it finds a row, it packages only the gate-critical fields and avoids the heavier lifetime purchase totals used by `read_balance`.

*Call graph*: calls 1 internal fn (_note_absent_balance); 3 external calls (__init__, execute, select).


##### `read_balance`  (lines 205–235)

```
async def read_balance(connection: AsyncConnection, workspace_id: UUID) -> Balance | None
```

**Purpose**: This gives the fuller balance picture for a workspace: what is left, what reserve is required, how much was ever granted or charged, and when the last purchase happened. It is meant for admin or operator views rather than per-round gating.

**Data flow**: It receives a database connection and workspace ID. First it reads the current balance row. If there is no row, it records the absence in the short-lived cache and returns `None`. If there is a row, it also sums the workspace’s purchase records and finds the most recent purchase time, then returns a `Balance` object containing both the current balance and the lifetime totals.

**Call relations**: This is the slower, more complete companion to `read_headroom`. It calls `_note_absent_balance` on a missing row, but when a row exists it pays the cost of reading purchase aggregates so humans can audit the current balance against the recorded credit history.

*Call graph*: calls 1 internal fn (_note_absent_balance); 3 external calls (__init__, execute, select).


##### `credit`  (lines 238–293)

```
async def credit(connection: AsyncConnection, workspace_id: UUID, granted_micro_usd: int, charged_micro_usd: int, reference: str) -> bool
```

**Purpose**: This adds credit to a workspace and records the purchase or adjustment that caused it. It is designed to be safe if the same payment notification is delivered twice: the same reference only credits once.

**Data flow**: It receives a database connection, workspace ID, granted amount, charged amount, and a reference string that identifies the source. It first tries to insert a purchase record with that reference. If the reference already exists for the workspace, it returns `False` and changes no balance. If the purchase is new, it inserts or updates the workspace’s balance by adding the granted amount, clears any cached “no balance” mark, and returns `True`.

**Call relations**: This function is the entry point for fulfilled purchases, grants, refunds, and corrections. It uses a database upsert, meaning “insert or update if already present,” so the purchase record and balance movement stay together in the caller’s transaction. After a successful credit it calls `_forget_absent_balance`, making future fast-path checks see the workspace as billable.

*Call graph*: calls 1 internal fn (_forget_absent_balance); 2 external calls (execute, uuid4).


##### `debit`  (lines 296–316)

```
async def debit(connection: AsyncConnection, workspace_id: UUID, micro_usd: int) -> int
```

**Purpose**: This subtracts spent credit from a workspace when paid work has been recorded. It allows the balance to go negative, because accurately recording spent work is more important than rejecting the ledger write after the money has already been used.

**Data flow**: It receives a database connection, workspace ID, and an amount to subtract. If the amount is zero, it immediately returns zero. Otherwise it updates the workspace balance by subtracting the amount. It returns the amount actually taken if a balance row existed, or zero if there was no balance row.

**Call relations**: This is used alongside the ledger entry that records model spending, inside the same wider transaction. Unlike gate checks, it does not decide whether work may start; it records what was actually spent. In self-hosted or never-credited cases with no balance row, it becomes a no-op and reports that nothing was deducted.

*Call graph*: 2 external calls (execute, update).


##### `set_reserve`  (lines 319–330)

```
async def set_reserve(connection: AsyncConnection, workspace_id: UUID, reserve_micro_usd: int) -> bool
```

**Purpose**: This sets the minimum remaining credit a workspace must keep before starting a turn of work. The reserve helps stop a nearly empty workspace from beginning work it cannot realistically continue.

**Data flow**: It receives a database connection, workspace ID, and reserve amount. It updates the existing balance row with the new reserve and modification time. It returns `True` if a row was updated, or `False` if the workspace has no balance row.

**Call relations**: This is usually called when billing policy or admin settings change. Later, `read_headroom` reads the reserve along with the balance so the admission gate can decide whether a new paid turn should be allowed to begin.

*Call graph*: 2 external calls (execute, update).


### `extensions/metronome/ufo_ext_metronome.py`

`orchestration` · `scheduled jobs, chat tool calls, and billing page requests`

This extension is the bridge between UFO's internal prepaid balance system and two outside services. Metronome receives settled usage records so humans can see rated usage statements. Stripe stores payment methods and takes money when an automatic top-up is needed. The important rule is that UFO's own balance gate decides whether a workspace can run; Metronome only records and rates what happened.

The file has three main jobs. First, a scheduled usage shipper reads already-settled ledger export intents from core, turns them into Metronome ingest events, sends them in batches, and only marks them done after Metronome accepts them. The event IDs are deterministic, like writing the same tracking number on a package, so retries after crashes do not double-count.

Second, a chat tool lets workspace admins ask for billing status, open a Stripe portal link, or set automatic refills. It checks that the speaker is an admin before doing anything.

Third, another scheduled job watches workspaces with configured auto-top-up rules. When a balance is low, it charges the saved Stripe card off-session, then credits the workspace balance exactly once. It backs off after missing cards or declined payments so it does not hammer Stripe or card networks.

The file also exposes a small billing status web route for the usage screen and declares the extension's manifest: tools, jobs, route, prompt text, and a credential slot for bring-your-own Anthropic keys.

#### Function details

##### `StripeError.__init__`  (lines 173–175)

```
def __init__(self, message: str, status: int=0) -> None
```

**Purpose**: Creates a Stripe-specific error that remembers the HTTP status code Stripe returned. This lets later code tell the difference between, for example, a declined card and a temporary conflict.

**Data flow**: It receives an error message and an optional status code. It stores the message as the normal exception text and saves the status number on the error object. The result is an exception that callers can inspect after it is raised.

**Call relations**: The shared Stripe request helper raises this error when Stripe returns a non-success response. Top-up charging code later reads the status to decide whether to wait, treat the card as declined, or fail loudly.

*Call graph*: called by 1 (_stripe).


##### `UsageShipper.run`  (lines 205–231)

```
async def run(self) -> None
```

**Purpose**: Ships one workspace's pending settled usage to Metronome in safe batches. It is designed so retries after a crash resend the same events instead of losing or double-counting usage.

**Data flow**: It starts by reading the required Metronome token from the environment, then finds the workspace's fixed backfill floor. It repeatedly asks core for pending usage exports, warns if old usage is close to becoming too old for Metronome, ensures the Metronome customer alias exists, converts exports into events, sends them, logs success, and finally acknowledges the exports. It stops when there is no more work or the last batch was smaller than the batch size.

**Call relations**: The scheduled `_ship` wrapper creates a `UsageShipper` and calls this method. During each pass it relies on `_floor`, `_note_usage_aging_out`, `_ensure_metronome_customer`, `_events`, and `_ingest` in that order so usage is only marked shipped after Metronome has accepted it.

*Call graph*: calls 6 internal fn (_events, _floor, _note_usage_aging_out, _ensure_metronome_customer, _ingest, _require_env); 1 external calls (log).


##### `UsageShipper._floor`  (lines 233–243)

```
async def _floor(self) -> datetime
```

**Purpose**: Finds or creates the oldest usage time this workspace is allowed to ship. This limits the first backfill to a safe window but does not later move forward and accidentally drop old pending usage.

**Data flow**: It reads a stored timestamp from the extension store. If none exists, it creates one by subtracting the backfill window from the current time, stores it, and returns it. If a value already exists, it parses that stored timestamp and returns it.

**Call relations**: UsageShipper.run calls this before reading pending exports. The returned time is handed to core's pending export reader so the first run does not flood Metronome with usage older than the allowed backdating window.

*Call graph*: called by 1 (run); 3 external calls (fromisoformat, now, timedelta).


##### `UsageShipper._note_usage_aging_out`  (lines 245–266)

```
def _note_usage_aging_out(self, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: Warns operators when unshipped usage is older than Metronome's backdating window. It cannot fix the problem, but it makes silent revenue or reporting loss visible.

**Data flow**: It receives a batch of usage exports, finds the oldest event time, compares it to the current time minus the backfill window, and either does nothing or writes a warning log with the workspace, oldest time, and batch size.

**Call relations**: UsageShipper.run calls this after reading each batch and before shipping it. It uses `_rfc3339` to format the time in a standard text form for the warning.

*Call graph*: calls 1 internal fn (_rfc3339); called by 1 (run); 3 external calls (now, timedelta, warn).


##### `UsageShipper._events`  (lines 268–287)

```
def _events(self, exports: tuple[UsageExport, ...]) -> list[dict[str, object]]
```

**Purpose**: Turns core usage export records into the JSON-shaped events Metronome expects. It preserves important labels such as model, amount, price digest, turn ID, and whether the workspace used its own provider key.

**Data flow**: It receives a tuple of `UsageExport` records. For each one, it builds a dictionary with a deterministic transaction ID, workspace customer ID, event type, timestamp, and usage properties. It returns a list of event dictionaries ready to send over HTTP.

**Call relations**: UsageShipper.run calls this just before `_ingest`. The returned events are the exact payload sent to Metronome, so their deterministic IDs are central to safe retry behavior.

*Call graph*: calls 1 internal fn (_rfc3339); called by 1 (run).


##### `_ship`  (lines 290–291)

```
async def _ship(ctx: ExtensionContext) -> None
```

**Purpose**: Small scheduled-job entry function for usage shipping. It adapts the platform's job callback shape to the `UsageShipper` class.

**Data flow**: It receives an extension context for one workspace, creates a `UsageShipper` using that context and the configured test transport hook, and awaits its run. It returns nothing after shipping is complete or there is no work.

**Call relations**: The extension manifest registers this as the handler for the usage shipper job. Its only job is to hand control to `UsageShipper.run`.

*Call graph*: 1 external calls (__init__).


##### `BillingConfig.from_env`  (lines 309–326)

```
def from_env(cls) -> 'BillingConfig'
```

**Purpose**: Loads the environment variables needed for billing operations and fails early if any are missing. This prevents half-created Stripe or Metronome state during a misconfigured deployment.

**Data flow**: It reads the Stripe secret key, Stripe portal configuration ID, and Metronome bearer token from process environment variables. If any are absent, it raises one error naming all missing settings. If all are present, it returns a validated frozen `BillingConfig` object.

**Call relations**: Billing tool actions, billing projection reads, and balance top-up jobs call this before talking to Stripe or Metronome-related billing code. Usage shipping reads its Metronome token separately so usage reporting can keep working without full Stripe billing configuration.


##### `_billing_record`  (lines 339–341)

```
async def _billing_record(ctx: ExtensionContext) -> BillingRecord | None
```

**Purpose**: Reads the saved Stripe customer ID for a workspace, if one has been created. This is the local pointer that ties a UFO workspace to its Stripe customer.

**Data flow**: It asks the extension store for the billing record under a fixed key. If nothing is stored, it returns `None`. If data exists, it validates it as a `BillingRecord` and returns that object.

**Call relations**: Billing status, portal creation, autopay setup, billing page projection, and top-up execution all call this before using Stripe customer-specific operations.

*Call graph*: called by 5 (run, _billing_autopay, _billing_portal, _billing_projection, _billing_status).


##### `manage_billing`  (lines 367–376)

```
async def manage_billing(ctx: ToolContext, args: ManageBillingInput) -> ToolResult
```

**Purpose**: Implements the chat tool admins use to read billing status, open the Stripe portal, or configure automatic refills. It is the main chat-facing doorway into this file's billing features.

**Data flow**: It receives a tool context and parsed tool arguments. It first checks that the speaker is an admin, then loads billing configuration, then dispatches based on the requested action. It returns a tool result containing JSON text for the agent to report back.

**Call relations**: The manifest exposes this through `MANAGE_BILLING_TOOL_DEF`. After authorization through `_admin_billing`, it hands off to `_billing_status`, `_billing_portal`, or `_billing_autopay`.

*Call graph*: calls 4 internal fn (_admin_billing, _billing_autopay, _billing_portal, _billing_status).


##### `_billing_autopay`  (lines 379–410)

```
async def _billing_autopay(ext: ExtensionContext, config: BillingConfig, args: ManageBillingInput) -> ToolResult
```

**Purpose**: Sets or stops automatic balance refills for a workspace. It makes sure a card is already saved before promising future unattended charges.

**Data flow**: It receives the workspace extension context, billing configuration, and tool arguments. If exactly one of the refill amount or threshold is missing, it rejects the request. If both are omitted, it clears autopay. If both are present, it checks for a Stripe customer and default payment method, converts whole dollars to micro-dollars, writes the top-up rule in a transaction, clears old refusal state, bumps the attempt marker, logs the change, and returns the new autopay values.

**Call relations**: manage_billing calls this for the `autopay` action. It reads billing state with `_billing_record`, verifies the card with `_default_payment_method`, writes the balance rule through core's `set_auto_topup`, and formats its answer with `_text_result`.

*Call graph*: calls 4 internal fn (transaction, _billing_record, _default_payment_method, _text_result); called by 1 (manage_billing); 2 external calls (set_auto_topup, log).


##### `_admin_billing`  (lines 413–419)

```
async def _admin_billing(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Checks that a billing tool call is being made by a real speaking workspace admin. This protects billing actions from ordinary members and anonymous calls.

**Data flow**: It receives a tool context. If there is no speaker member, or the speaker is not an admin, it raises an error. Otherwise it returns the extension context attached to the tool call.

**Call relations**: manage_billing calls this before any billing action. It uses the tool context's admin check and then hands the authorized extension context to the selected billing helper.

*Call graph*: calls 1 internal fn (speaker_is_admin); called by 1 (manage_billing).


##### `_billing_status`  (lines 422–446)

```
async def _billing_status(ext: ExtensionContext, config: BillingConfig) -> ToolResult
```

**Purpose**: Reports whether a payment method is on file and what the workspace balance currently looks like. It reads live Stripe card state instead of trusting a local flag.

**Data flow**: It reads the workspace balance inside a transaction, reads any stored billing record, and if a Stripe customer exists asks Stripe whether there is a default payment method. It returns JSON text with card presence and balance, reserve, granted, and charged amounts, using `None` where no balance exists.

**Call relations**: manage_billing calls this for the `status` action. It combines core balance data with Stripe card data through `_billing_record` and `_default_payment_method`, then packages the response with `_text_result`.

*Call graph*: calls 4 internal fn (transaction, _billing_record, _default_payment_method, _text_result); called by 1 (manage_billing); 1 external calls (read_balance).


##### `_billing_portal`  (lines 449–471)

```
async def _billing_portal(ext: ExtensionContext, config: BillingConfig) -> ToolResult
```

**Purpose**: Creates a short-lived Stripe Customer Portal link for saving cards, viewing invoices, and editing billing details. If the workspace does not yet have a Stripe customer, this creates one safely first.

**Data flow**: It reads the workspace's billing record. If none exists, it creates a Stripe customer using the workspace ID, stores the resulting customer ID, then asks Stripe for a portal session URL with an optional return link back to UFO's billing screen. It logs the portal creation and returns JSON text containing the portal URL and Stripe customer ID.

**Call relations**: manage_billing calls this for the `portal` action. It uses `_stripe_customer` for first-time setup, `_billing_screen` to build the return URL, `_portal_session` to create the Stripe portal link, and `_text_result` to return it to chat.

*Call graph*: calls 5 internal fn (_billing_record, _billing_screen, _portal_session, _stripe_customer, _text_result); called by 1 (manage_billing); 2 external calls (__init__, log).


##### `_text_result`  (lines 474–475)

```
def _text_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: Wraps a small dictionary as JSON text in the tool result format expected by UFO's tool system. It gives chat actions a simple, consistent way to return structured facts.

**Data flow**: It receives a dictionary, serializes it to a JSON string, places that string in a text content object, and returns a tool result containing it.

**Call relations**: The billing status, portal, and autopay helpers all call this after they have prepared their response payloads.

*Call graph*: called by 3 (_billing_autopay, _billing_portal, _billing_status); 3 external calls (__init__, __init__, dumps).


##### `_require_env`  (lines 487–491)

```
def _require_env(name: str) -> str
```

**Purpose**: Reads one required environment variable and raises a clear error if it is missing. It is used where running without the value would create bad durable state.

**Data flow**: It receives the environment variable name, looks it up in the process environment, and returns the value if present. If it is missing or empty, it raises a runtime error naming the setting.

**Call relations**: UsageShipper.run calls this before touching pending usage exports. That ordering matters because reading exports can create durable work that should not be minted when there is no Metronome token to ship it.

*Call graph*: called by 1 (run).


##### `_stripe_customer`  (lines 494–511)

```
async def _stripe_customer(config: BillingConfig, workspace_id: UUID, transport: httpx.AsyncBaseTransport | None) -> str
```

**Purpose**: Creates or retrieves the one Stripe Customer that represents a workspace. It uses an idempotency key, meaning repeated identical create attempts settle on the same customer rather than making duplicates.

**Data flow**: It receives billing configuration, a workspace ID, and an optional HTTP transport. It sends a Stripe customer creation request with a description, workspace metadata, and a deterministic idempotency key. It extracts and returns the customer ID from Stripe's response.

**Call relations**: _billing_portal calls this when a workspace opens the portal before any Stripe customer has been stored. It sends the request through `_stripe` and validates the returned ID with `_as_str`.

*Call graph*: calls 2 internal fn (_as_str, _stripe); called by 1 (_billing_portal).


##### `_portal_session`  (lines 514–539)

```
async def _portal_session(config: BillingConfig, customer_id: str, flow: str | None, transport: httpx.AsyncBaseTransport | None, return_url: str | None=None) -> str
```

**Purpose**: Creates a Stripe Customer Portal session URL. The portal is where admins save payment methods and view billing details without this app handling card data directly.

**Data flow**: It receives billing configuration, a Stripe customer ID, an optional flow type, an optional return URL, and an optional transport. It builds the form data Stripe expects, asks Stripe to create a portal session, and returns the session URL from the response.

**Call relations**: _billing_portal calls this after it has a Stripe customer. It uses `_stripe` for the HTTP call and `_as_str` to ensure Stripe actually returned a usable URL.

*Call graph*: calls 2 internal fn (_as_str, _stripe); called by 1 (_billing_portal).


##### `_default_payment_method`  (lines 542–554)

```
async def _default_payment_method(config: BillingConfig, customer_id: str, transport: httpx.AsyncBaseTransport | None) -> str | None
```

**Purpose**: Checks whether a Stripe customer has a default payment method saved. This is the file's source of truth for whether unattended charges can run.

**Data flow**: It receives billing configuration, a Stripe customer ID, and an optional transport. It fetches the customer from Stripe, looks inside the customer's invoice settings for a default payment method, and returns that method ID or `None`.

**Call relations**: Autopay setup, billing status, billing page projection, and balance top-up execution all call this. It uses `_stripe` for the Stripe request.

*Call graph*: calls 1 internal fn (_stripe); called by 4 (run, _billing_autopay, _billing_projection, _billing_status).


##### `_stripe`  (lines 557–578)

```
async def _stripe(config: BillingConfig, method: str, path: str, transport: httpx.AsyncBaseTransport | None, data: dict[str, str] | None=None, idempotency_key: str | None=None) -> dict[str, object]
```

**Purpose**: Shared low-level helper for Stripe API calls. It adds authentication, pins the Stripe API version, sends the request, and turns failed responses into clear exceptions.

**Data flow**: It receives billing configuration, HTTP method, API path, optional form data, optional idempotency key, and optional test transport. It builds headers, sends the HTTP request to Stripe, raises `StripeError` on non-success, and returns the parsed JSON response on success.

**Call relations**: Customer creation, portal sessions, payment method lookup, and top-up charges all use this helper. It is the single place where Stripe HTTP behavior and error wrapping are centralized.

*Call graph*: calls 1 internal fn (__init__); called by 4 (_charge, _default_payment_method, _portal_session, _stripe_customer); 1 external calls (AsyncClient).


##### `_metronome`  (lines 581–599)

```
async def _metronome(config: BillingConfig, method: str, path: str, transport: httpx.AsyncBaseTransport | None, body: dict[str, object] | None=None, params: dict[str, str] | None=None, idempotency_key
```

**Purpose**: Shared helper for Metronome API calls that need JSON bodies or query parameters. In this file it is available as a general Metronome client wrapper, although the usage shipper uses a more specialized ingest helper.

**Data flow**: It receives billing configuration, HTTP method, API path, optional JSON body, optional query parameters, optional idempotency key, and optional transport. It sends the request with the Metronome bearer token, raises `MetronomeError` on failure, and returns parsed JSON on success.

**Call relations**: No listed function currently calls this helper. It mirrors `_stripe` for Metronome-style API calls and would be used by future code that needs non-ingest Metronome operations.

*Call graph*: 2 external calls (__init__, AsyncClient).


##### `_as_str`  (lines 602–606)

```
def _as_str(value: object, field: str) -> str
```

**Purpose**: Checks that a provider response field is a non-empty string. It turns malformed or unexpected provider responses into clear local errors.

**Data flow**: It receives any value and the human name of the field being checked. If the value is a non-empty string, it returns it. Otherwise it raises a value error saying the provider response lacked that field.

**Call relations**: _stripe_customer uses this to validate Stripe customer IDs, and `_portal_session` uses it to validate Stripe portal URLs.

*Call graph*: called by 2 (_portal_session, _stripe_customer).


##### `_ensure_metronome_customer`  (lines 609–660)

```
async def _ensure_metronome_customer(ctx: ExtensionContext, token: str, transport: httpx.AsyncBaseTransport | None) -> None
```

**Purpose**: Makes sure Metronome has a live customer whose ingest alias is the workspace UUID before usage is sent. Without this, Metronome may accept events but attach them to nobody, silently losing useful billing records.

**Data flow**: It receives an extension context, Metronome bearer token, and optional transport. It looks up a customer by the workspace alias. If one exists, it returns. If not, it tries to create a customer with that alias, handles alias conflicts by rechecking, treats permission failures as unsafe to ship, and logs successful creation.

**Call relations**: UsageShipper.run calls this once before sending the first batch in a pass. It depends on `_customer_by_alias` to confirm aliases and raises errors rather than letting `_ingest` drain usage into an unconfirmed Metronome identity.

*Call graph*: calls 1 internal fn (_customer_by_alias); called by 1 (run); 4 external calls (__init__, __init__, AsyncClient, log).


##### `_customer_by_alias`  (lines 670–687)

```
async def _customer_by_alias(http: httpx.AsyncClient, headers: dict[str, str], alias: str) -> str | None
```

**Purpose**: Looks up the live Metronome customer that owns a given ingest alias. It returns no customer when the alias is not visible through this token.

**Data flow**: It receives an HTTP client, request headers, and an alias string. It queries Metronome's customer list by ingest alias, raises if the token lacks permission or the request fails, and returns the first customer ID found or `None`.

**Call relations**: _ensure_metronome_customer calls this before and sometimes after trying to create a customer. That second lookup distinguishes a harmless race from an alias held by something this token cannot read.

*Call graph*: called by 1 (_ensure_metronome_customer); 3 external calls (__init__, __init__, get).


##### `BalanceTopup.run`  (lines 706–788)

```
async def run(self) -> None
```

**Purpose**: Runs the automatic refill process for one workspace. When core says the balance is low and autopay is configured, it charges the saved card and credits the balance.

**Data flow**: It reads the workspace's auto-top-up rule. If none exists, it stops. It respects short backoff timers for missing cards and longer timers for declined cards. It loads billing configuration, finds the Stripe customer and default payment method, reads current charged totals, decides whether a retry is allowed, asks `_charge` to move money, records refusals when needed, and on success credits the workspace balance with a Stripe-based idempotency key.

**Call relations**: The scheduled `_top_up` wrapper creates a `BalanceTopup` and calls this method. It combines core balance functions, `_billing_record`, `_default_payment_method`, and `BalanceTopup._charge` to move from “workspace is short” to “workspace has credited prepaid balance.”

*Call graph*: calls 3 internal fn (_charge, _billing_record, _default_payment_method); 8 external calls (fromisoformat, now, credit, mark_topup_verified, read_auto_topup, read_balance, log, warn).


##### `BalanceTopup._charge`  (lines 790–847)

```
async def _charge(self, config: BillingConfig, customer_id: str, payment_method: str, wanted: AutoTopup, workspace_id: UUID, attempt: str) -> str | None
```

**Purpose**: Creates and confirms a Stripe PaymentIntent for an automatic top-up. It returns a payment intent ID only when the money actually succeeded.

**Data flow**: It receives billing configuration, Stripe customer and payment method IDs, the desired top-up rule, workspace ID, and an attempt label. It converts micro-dollars to cents, sends a Stripe PaymentIntent request with an idempotency key, treats conflicts as an in-flight previous request, treats payment-required errors as card declines, and returns the successful intent ID or `None` for a refusal.

**Call relations**: BalanceTopup.run calls this when all preconditions for charging are met. It sends the Stripe call through `_stripe`, warns on declined or non-succeeded outcomes, and lets the caller decide whether to record a refusal or credit the balance.

*Call graph*: calls 1 internal fn (_stripe); called by 1 (run); 2 external calls (__init__, warn).


##### `_top_up`  (lines 850–851)

```
async def _top_up(ctx: ExtensionContext) -> None
```

**Purpose**: Small scheduled-job entry function for automatic balance refills. It adapts the platform's job callback shape to the `BalanceTopup` class.

**Data flow**: It receives an extension context for one workspace, creates a `BalanceTopup` with that context and the configured billing transport hook, and awaits its run. It returns nothing after the check or refill attempt completes.

**Call relations**: The manifest registers this as the handler for the balance top-up job. Its only role is to hand control to `BalanceTopup.run`.

*Call graph*: 1 external calls (__init__).


##### `_ingest`  (lines 854–862)

```
async def _ingest(token: str, events: list[dict[str, object]], transport: httpx.AsyncBaseTransport | None) -> None
```

**Purpose**: Sends a batch of usage events to Metronome's ingest endpoint. It is the final network step before usage exports can be acknowledged as shipped.

**Data flow**: It receives a Metronome token, a list of event dictionaries, and an optional transport. It posts the events as JSON with bearer-token authorization. If Metronome does not return success, it raises `MetronomeError`; otherwise it returns nothing.

**Call relations**: UsageShipper.run calls this after building events and confirming the customer alias. Only after this succeeds does the shipper acknowledge the exports in core.

*Call graph*: called by 1 (run); 2 external calls (__init__, AsyncClient).


##### `_rfc3339`  (lines 865–867)

```
def _rfc3339(moment: datetime) -> str
```

**Purpose**: Formats a date-time for provider-facing JSON and logs. If the time has no timezone, it treats it as UTC so the text is unambiguous.

**Data flow**: It receives a `datetime`. If the value lacks timezone information, it adds UTC. It then returns the ISO/RFC3339-style text representation.

**Call relations**: UsageShipper._events uses this for Metronome event timestamps, and UsageShipper._note_usage_aging_out uses it for warning logs.

*Call graph*: called by 2 (_events, _note_usage_aging_out); 1 external calls (replace).


##### `_billing_screen`  (lines 874–882)

```
def _billing_screen(public_base_url: str | None) -> str | None
```

**Purpose**: Builds the UFO billing screen URL that Stripe should send an admin back to after using the portal. If the deployment has no public base URL, it safely returns no return URL.

**Data flow**: It receives an optional public base URL. If it is empty, it returns `None`. Otherwise it trims any trailing slash and appends the fixed billing screen path.

**Call relations**: _billing_portal calls this before creating a Stripe portal session. The result is passed to `_portal_session` so the user can return to the billing screen after saving a card.

*Call graph*: called by 1 (_billing_portal).


##### `_billing_request_workspace`  (lines 885–890)

```
def _billing_request_workspace(request: Request) -> UUID | None
```

**Purpose**: Identifies which workspace a billing page request belongs to by reading the signed session cookie. This prevents the billing route from being an open unauthenticated endpoint.

**Data flow**: It receives an HTTP request, reads the session cookie, and asks the bearer-token helper for the workspace claim inside it. It returns the workspace UUID or `None` if the cookie does not identify one.

**Call relations**: The manifest registers this as the route identifier for the billing page route. Core uses its answer to bind the request to an extension context before `_billing_projection` runs.

*Call graph*: 1 external calls (workspace_claim).


##### `_billing_projection`  (lines 893–938)

```
async def _billing_projection(ext: ExtensionContext, request: Request) -> Response
```

**Purpose**: Serves the billing status shown on the web usage screen. It gives admins a way to see balance, stopping thresholds, card status, and autopay even when chat turns are blocked by low balance.

**Data flow**: It verifies the session cookie for the bound workspace. Inside a transaction, it finds the member by email, checks admin status, reads headroom, balance, and configured auto-top-up. If the workspace is not balance-limited, it returns `limited: false`. Otherwise it loads billing configuration, checks Stripe for a card, and returns a JSON response with balance, reserve, grace, refusal line, charged and granted amounts, card status, and autopay settings.

**Call relations**: The manifest exposes this as the GET billing route. It uses bearer verification, seat membership checks, core balance reads, `_billing_record`, and `_default_payment_method` to assemble a read-only projection for the UI.

*Call graph*: calls 3 internal fn (transaction, _billing_record, _default_payment_method); 7 external calls (configured_auto_topup, read_balance, read_headroom, verify_token, JSONResponse, member_by_email, member_is_admin).


##### `manifest`  (lines 941–979)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to UFO: its name, version, chat tool, scheduled jobs, billing route, prompt guidance, and credential slot. This is how the rest of the platform discovers what the file provides.

**Data flow**: It constructs and returns a `Manifest` object. The manifest includes the manage-billing tool, the usage shipping and top-up jobs with their candidate workspace sets, the billing GET route with its workspace identifier, prompt text explaining billing behavior to the agent, and a credential slot for a workspace's Anthropic API key.

**Call relations**: The platform calls this when loading the extension. The objects it returns point back to `_ship`, `_top_up`, `_billing_projection`, `_billing_request_workspace`, and `manage_billing`, wiring the file's internal functions into runtime behavior.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, metered_workspaces, member_workspaces).


### Self-improvement evaluation safety
Replays old conversations with candidate prompts, grades outcomes, and applies cautious gates so only clear prompt improvements can proceed.

### `extensions/self_improvement/ufo_ext_self_improvement/evaluation.py`

`domain_logic` · `self-improvement evaluation`

This file is the evidence-gathering step for self-improvement. When the system invents a candidate prompt, it cannot simply trust that the new wording is better. It must test it fairly. The fair test here is like a taste test with two recipes: use the same ingredients, same kitchen, and same judge, changing only the recipe. In this case, the “recipe” is the prompt.

The main class, CandidateEvaluation, compares two prompt versions: the current prompt and the candidate prompt. For each saved task example, it replays the task twice. One replay uses the current prompt, called “absent” because the candidate is absent. The other uses the candidate prompt, called “present” because the candidate is present. The replay uses archived task messages and a replay model, so the comparison focuses on the prompt rather than fresh outside variation.

After each replay, a judge model is asked whether the final answer satisfies the original request. The judge must return a small JSON object saying whether the answer was accepted. If the judge gives malformed output, the answer is treated as not accepted.

Finally, the file passes all pass/fail labels to a two-stage gate. The candidate must improve on the task class it was designed for and must not make other held-out task classes worse.

#### Function details

##### `CandidateEvaluation.evaluate`  (lines 24–33)

```
async def evaluate(self, candidate_prompt: str, current_prompt: str, local_held_out: tuple[TaskExample, ...], global_held_out: tuple[TaskExample, ...]=()) -> GateVerdict
```

**Purpose**: This is the top-level evaluation step for a candidate prompt. It tests the candidate against the current prompt on local held-out examples and optional global held-out examples, then asks the gate whether the evidence is strong enough to accept the candidate.

**Data flow**: It receives the candidate prompt, the current prompt, a set of local test examples, and optionally a broader set of global test examples. It turns each set into pass/fail labels by running both prompt versions through the same replay-and-judge process. It then returns a GateVerdict, which is the final decision about whether the candidate prompt passes the improvement gate.

**Call relations**: This method starts the file’s main workflow. It calls CandidateEvaluation._labels once for the local examples and once for the global examples. After both sets of labels are ready, it hands them to two_stage_gate, which makes the final accept-or-reject decision.

*Call graph*: calls 1 internal fn (_labels); 1 external calls (two_stage_gate).


##### `CandidateEvaluation._labels`  (lines 35–45)

```
async def _labels(self, candidate_prompt: str, current_prompt: str, held_out: tuple[TaskExample, ...]) -> tuple[OutcomeLabel, ...]
```

**Purpose**: This function produces the raw evidence used by the gate. For every saved task, it checks whether the current prompt succeeds and whether the candidate prompt succeeds, creating one outcome label for each run.

**Data flow**: It receives two prompt texts and a tuple of held-out task examples. For each example, it replays the task first with the current prompt and then with the candidate prompt. It sends each replayed final answer to the judge through CandidateEvaluation._accepts, then records whether that run used the candidate prompt and whether it succeeded. The result is a tuple of OutcomeLabel objects.

**Call relations**: CandidateEvaluation.evaluate calls this when it needs evidence for either the local or global test set. Inside, it creates a ReplayEvaluation object to rerun saved task conversations, calls CandidateEvaluation._accepts to grade each answer, and packages each result as an OutcomeLabel for the later gate decision.

*Call graph*: calls 1 internal fn (_accepts); called by 1 (evaluate); 2 external calls (__init__, __init__).


##### `CandidateEvaluation._accepts`  (lines 47–59)

```
async def _accepts(self, request: str, answer: str) -> bool
```

**Purpose**: This function asks a judge model whether an answer truly satisfies the original user request. It reduces the judge’s response to a simple yes-or-no result.

**Data flow**: It receives the original request and the answer produced during replay. It builds a user message containing both pieces of text, sends that message with grading instructions to the judge model, and expects the judge to return JSON like {"accepted": true}. It extracts the JSON part of the judge response, parses it, and returns true only if the parsed object clearly says accepted is true. If the response is missing JSON or the JSON is invalid, it returns false.

**Call relations**: CandidateEvaluation._labels calls this after each replay to turn a generated answer into a success or failure. It uses the external Message type to format the judge request and json.loads to read the judge’s JSON verdict before handing the boolean result back to _labels.

*Call graph*: called by 1 (_labels); 2 external calls (__init__, loads).


### `extensions/self_improvement/ufo_ext_self_improvement/gate.py`

`domain_logic` · `self-improvement evaluation`

This file is the “promotion gate” for self-improvement. Imagine testing a new recipe against the old one: you do not want to switch just because four tasters happened to like it once. You want enough evidence that the new recipe is truly better, and that it did not ruin other dishes. Here, the “recipe” is a prompt, and each replayed example records whether the candidate prompt was present and whether the judge accepted the answer.

The file compares two groups: examples run with the candidate prompt and examples run without it. It counts successes in each group, then estimates the improvement in acceptance rate. Because small sample sizes can be misleading, it uses Wilson confidence bounds, a statistical way to say “given limited data, what range of true success rates is still plausible?” It then builds a cautious lower bound for the candidate’s lift, meaning the improvement that is still believable even after accounting for uncertainty.

There are two checks. First, `score_gate` requires enough examples in both groups and a local improvement above a small floor. Second, `global_non_inferior` makes sure the candidate does not clearly harm other task classes. `two_stage_gate` combines both: a candidate must win locally and avoid confident global regression.

#### Function details

##### `wilson_lower_bound`  (lines 53–60)

```
def wilson_lower_bound(accepted: int, total: int, z: float=WILSON_Z_95) -> float
```

**Purpose**: This function gives a cautious low estimate of a success rate. It answers: “Based on accepted out of total examples, what success rate can we reasonably be at least this confident about?”

**Data flow**: It takes a number of accepted examples, a total number of examples, and an optional confidence setting. If there are no examples, it returns 0. Otherwise it computes the Wilson lower confidence bound and returns a number between 0 and 1.

**Call relations**: The lift calculations call this when they need the pessimistic side of a success rate. It uses `math.sqrt` for the statistical formula, then hands the bound back to `lift_lower_bound` or `lift_upper_bound` so those functions can compare two prompt arms fairly.

*Call graph*: called by 2 (lift_lower_bound, lift_upper_bound); 1 external calls (sqrt).


##### `wilson_upper_bound`  (lines 63–70)

```
def wilson_upper_bound(accepted: int, total: int, z: float=WILSON_Z_95) -> float
```

**Purpose**: This function gives a cautious high estimate of a success rate. It answers: “Given this evidence, how good could this arm plausibly be?”

**Data flow**: It takes accepted examples, total examples, and an optional confidence setting. If there are no examples, it returns 1, meaning the unknown rate is treated as possibly as high as perfect. Otherwise it computes the Wilson upper confidence bound and returns a number between 0 and 1.

**Call relations**: The lift calculations call this when they need the optimistic side of a success rate. It pairs with `wilson_lower_bound` so `lift_lower_bound` and `lift_upper_bound` can describe a plausible range for the difference between candidate and baseline.

*Call graph*: called by 2 (lift_lower_bound, lift_upper_bound); 1 external calls (sqrt).


##### `lift_lower_bound`  (lines 73–84)

```
def lift_lower_bound(cont: Contingency) -> float
```

**Purpose**: This function estimates the cautious lower edge of the candidate prompt’s improvement. It is used to decide whether the candidate has shown enough real gain to be promoted.

**Data flow**: It takes a `Contingency`, which contains success counts for examples where the candidate prompt was present and absent. If either side has no examples, it returns 0. Otherwise it computes each side’s observed success rate, widens that comparison using Wilson uncertainty, and returns the lower believable improvement: candidate rate minus baseline rate.

**Call relations**: `score_gate` calls this after building the counts. Inside, it asks `wilson_lower_bound` and `wilson_upper_bound` for the uncertain edges of the two rates, then uses `math.sqrt` to combine that uncertainty into one cautious lift number.

*Call graph*: calls 2 internal fn (wilson_lower_bound, wilson_upper_bound); called by 1 (score_gate); 1 external calls (sqrt).


##### `lift_upper_bound`  (lines 87–98)

```
def lift_upper_bound(cont: Contingency) -> float
```

**Purpose**: This function estimates the optimistic upper edge of the candidate prompt’s improvement. It is mainly used to detect clear harm: if even the optimistic estimate is bad, the candidate should not pass.

**Data flow**: It takes a `Contingency` of candidate-present and candidate-absent results. If either side has no examples, it returns 0. Otherwise it computes the observed difference in success rates and adds the combined uncertainty, producing the best plausible lift.

**Call relations**: `global_non_inferior` calls this during the broader safety check. The function relies on `wilson_upper_bound`, `wilson_lower_bound`, and `math.sqrt` to build the optimistic end of the lift range.

*Call graph*: calls 2 internal fn (wilson_lower_bound, wilson_upper_bound); called by 1 (global_non_inferior); 1 external calls (sqrt).


##### `contingency`  (lines 101–109)

```
def contingency(labels: tuple[OutcomeLabel, ...]) -> Contingency
```

**Purpose**: This function turns a list of replay outcomes into simple counts. It separates examples where the candidate prompt was present from those where it was absent, then counts successes in each group.

**Data flow**: It receives a tuple of `OutcomeLabel` records. Each record says whether the candidate prompt was present and whether that run succeeded. The function splits those records into two groups and returns a `Contingency` containing accepted counts and total counts for both groups.

**Call relations**: `score_gate` and `global_non_inferior` both call this first, because the later statistical checks need counts rather than individual labels. It creates the `Contingency` object that feeds the lift calculations.

*Call graph*: called by 2 (global_non_inferior, score_gate); 1 external calls (__init__).


##### `score_gate`  (lines 112–139)

```
def score_gate(labels: tuple[OutcomeLabel, ...], lower_bound: float=LIFT_LOWER_BOUND, n_floor: int=N_FLOOR) -> GateVerdict
```

**Purpose**: This function makes the local promotion decision for the task class being improved. It checks that there are enough replay examples on both sides and that the candidate’s cautious acceptance lift clears the required floor.

**Data flow**: It receives replay outcome labels, plus optional thresholds for the minimum lift and minimum examples per arm. It converts labels into counts, computes the lower lift bound, and returns a `GateVerdict`. The verdict says whether the candidate passed, why, the measured lower bound, and the sample sizes.

**Call relations**: `two_stage_gate` calls this as the first stage. `score_gate` calls `contingency` to count the data and `lift_lower_bound` to judge the candidate’s improvement; if the evidence is too thin or the lift is too small, it returns a failing `GateVerdict` immediately.

*Call graph*: calls 2 internal fn (contingency, lift_lower_bound); called by 1 (two_stage_gate); 1 external calls (__init__).


##### `global_non_inferior`  (lines 142–154)

```
def global_non_inferior(labels: tuple[OutcomeLabel, ...], margin: float=GLOBAL_REGRESSION_MARGIN, n_floor: int=N_FLOOR) -> bool
```

**Purpose**: This function checks that a prompt change does not clearly make other task classes worse. It is deliberately lenient when evidence is weak, and blocks only when the data confidently shows harmful regression.

**Data flow**: It receives outcome labels from the broader, held-out task set, plus optional margin and sample-size settings. It counts the results. If either side has too few examples, it returns true, meaning “do not block on weak evidence.” Otherwise it computes the optimistic lift and returns whether that optimistic value is still no worse than the allowed negative margin.

**Call relations**: `two_stage_gate` calls this only after the local gate has passed. It uses `contingency` to prepare counts and `lift_upper_bound` to ask whether the candidate could still plausibly be acceptable globally.

*Call graph*: calls 2 internal fn (contingency, lift_upper_bound); called by 1 (two_stage_gate).


##### `two_stage_gate`  (lines 157–174)

```
def two_stage_gate(local_labels: tuple[OutcomeLabel, ...], global_labels: tuple[OutcomeLabel, ...]) -> GateVerdict
```

**Purpose**: This function gives the final promotion verdict. A candidate must first prove a local win, then avoid clear damage on other task classes.

**Data flow**: It receives two sets of outcome labels: local labels for the task class being improved and global labels for other tasks. It runs the local gate first. If that fails, it returns the local failure verdict. If local passes but the global check finds regression, it returns a new failing verdict explaining that global regression blocked promotion. If both pass, it returns the successful local verdict.

**Call relations**: This is the top-level decision point in the file. It calls `score_gate` for the improvement test, then `global_non_inferior` for the safety test, and creates a `GateVerdict` when a candidate wins locally but is rejected for broader harm.

*Call graph*: calls 2 internal fn (global_non_inferior, score_gate); 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/replay.py`

`domain_logic` · `offline evaluation / prompt replay`

This file solves a careful testing problem: how can we compare prompts without letting new tool calls change the world or muddy the result? Its answer is a counterfactual replay. Think of it like re-recording only the actor's lines in a movie while keeping the same props, scenes, and off-screen events. The archived conversation supplies the past user messages, tool requests, and tool results. The new system prompt is swapped in, and the model is asked to continue from the same situation.

The replay first removes the original final assistant answer, because that is what it wants the model to regenerate. It then builds a lookup table of archived tool results, keyed by the tool name and its input. It also creates a small tool catalog containing only the tools that appeared in the old conversation. During replay, if the model asks for a tool in the same way the archived run did, the code feeds back the saved result. It never executes the real tool.

If the model asks for a tool call that was not in the archive, the replay is marked as diverged. That means the model left the known path, so the system grades whatever answer text it had produced so far. A round limit prevents an endless loop.

#### Function details

##### `_canonical_input`  (lines 35–36)

```
def _canonical_input(value: object) -> str
```

**Purpose**: This helper turns a tool's input into a stable text form so the same input can be recognized later. It is used because two dictionaries with the same meaning may otherwise be written in different key orders.

**Data flow**: It receives any input value, usually the argument object from a tool call. It converts that value to compact JSON text with sorted keys. It returns that text, which can be safely used as part of a lookup key.

**Call relations**: When archived tool results are indexed, archived_tool_results uses this to label each saved tool call. Later, _feed_archived uses the same conversion on the model's replayed tool request, so it can check whether that request exactly matches something from the archive.

*Call graph*: called by 2 (_feed_archived, archived_tool_results); 1 external calls (dumps).


##### `replay_head`  (lines 39–52)

```
def replay_head(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This function prepares the conversation history that the replay should start from. It removes the original final assistant answer, because the whole point is to see what answer the new prompt produces instead.

**Data flow**: It receives the full archived conversation. Starting from the end, it drops trailing assistant messages that are ordinary final answers and do not contain tool requests. It stops once it reaches a tool-using assistant message or a non-assistant message, then returns the shortened conversation.

**Call relations**: ReplayEvaluation.replay calls this near the start of a replay. The returned conversation becomes the context sent to the model, while the original answer is kept out so it cannot simply influence the new result.

*Call graph*: called by 1 (replay).


##### `archived_tool_results`  (lines 55–77)

```
def archived_tool_results(messages: tuple[Message, ...]) -> dict[tuple[str, str], ToolResultBlock]
```

**Purpose**: This function builds the replay's memory of past tool calls and their saved answers. It lets the replay answer tool requests from the archive instead of running real tools.

**Data flow**: It receives the archived messages. First it collects tool result blocks by the internal tool-use id they answered. Then it walks through the archived tool-use blocks, finds their matching result, and stores that result under a key made from the tool name and canonicalized input. It returns this lookup table.

**Call relations**: ReplayEvaluation.replay calls this before the model starts replaying. _feed_archived later relies on the returned table to decide whether a new tool request matches the old path and to provide the saved result when it does.

*Call graph*: calls 1 internal fn (_canonical_input); called by 1 (replay).


##### `replay_tools`  (lines 80–97)

```
def replay_tools(messages: tuple[Message, ...]) -> tuple[ToolSchema, ...]
```

**Purpose**: This function creates the list of tools the model is allowed to see during replay. The list is limited to tool names that appeared in the archived conversation.

**Data flow**: It receives the archived messages, scans them for tool-use blocks, and records each distinct tool name in first-seen order. It returns permissive tool schemas, meaning simple tool descriptions that allow object-shaped inputs with any properties.

**Call relations**: ReplayEvaluation.replay calls this during setup and passes the resulting tool list to the model on each turn. The model gets enough information to reproduce archived tool calls, but this file does not need access to the live system's real tool registry.

*Call graph*: called by 1 (replay); 1 external calls (__init__).


##### `_feed_archived`  (lines 100–116)

```
def _feed_archived(tool_uses: tuple[ToolUseBlock, ...], results: Mapping[tuple[str, str], ToolResultBlock]) -> Message | None
```

**Purpose**: This function answers the model's replayed tool requests using saved archive results. If any request does not match the archive, it reports that by returning nothing.

**Data flow**: It receives the tool calls the model just requested and the archived result lookup table. For each call, it canonicalizes the input and searches for a saved result with the same tool name and input. If all calls match, it creates a user message containing tool-result blocks copied from the archive but tied to the new call ids. If any call is missing, it returns None.

**Call relations**: ReplayEvaluation.replay calls this after any model turn that contains tool requests. A returned message is appended to the replay conversation so the model can continue; a None result tells the replay that the model has diverged from the archived path.

*Call graph*: calls 1 internal fn (_canonical_input); called by 1 (replay); 2 external calls (__init__, __init__).


##### `ReplayEvaluation.replay`  (lines 129–150)

```
async def replay(self, archived: tuple[Message, ...], system_prompt: str) -> ReplayResult
```

**Purpose**: This is the main replay procedure. It reruns the model parts of one archived task under a new system prompt while replaying old tool results and watching for divergence.

**Data flow**: It receives an archived conversation and a candidate system prompt. It builds the archived tool-result lookup, creates the replay tool catalog, and strips the old final answer from the message history. Then it repeatedly asks the model for the next assistant turn. If the model gives a final text answer with no tool calls, it returns that answer as a non-diverged result. If the model asks for matching archived tools, it feeds back the saved results and continues. If a requested tool result is missing, or if the round limit is reached, it returns the best text seen so far and marks the replay as diverged.

**Call relations**: This method ties together all helpers in the file: archived_tool_results prepares the saved tool answers, replay_tools prepares the visible tool list, replay_head prepares the starting conversation, and _feed_archived supplies archived results after each tool-using model turn. Its output, ReplayResult, is the compact summary that later grading code can use to compare prompt arms.

*Call graph*: calls 4 internal fn (_feed_archived, archived_tool_results, replay_head, replay_tools); 1 external calls (__init__).


### Self-improvement proposal workflow
Builds failure-focused corpora, uses the metered model path to propose prompt rewrites, and schedules repeated checks before opening governed proposals.

### `extensions/self_improvement/ufo_ext_self_improvement/corpus.py`

`domain_logic` · `self-improvement corpus building`

The self-improvement loop needs real examples of where the system struggled. This file finds those examples by reading saved conversation trajectories and looking for tool errors. A trajectory is a full record of one conversation, including user messages, tool calls, and tool results. Since the system does not have a separate “I had trouble here” signal, a tool error inside the transcript becomes the clue that this conversation is worth studying.

The file reduces each useful trajectory into a TaskExample: the conversation ID, the user’s original request, the full message history needed for replay, and a plain description of the problem. It then groups examples into TaskClass objects, named by the failed tool, such as “tool:search”. This matters because a fix for one tool should be proposed and judged using examples where that same tool failed.

The split between “mine” and “held_out” is important. “Mine” examples are like practice worksheets used to suggest an improvement. “Held-out” examples are like a quiz kept hidden until grading time. This prevents the system from being rewarded just for solving the exact cases it already studied. Very small groups are skipped, because there must be at least one example for learning and one for evaluation.

#### Function details

##### `first_request`  (lines 39–43)

```
def first_request(messages: tuple[Message, ...]) -> str | None
```

**Purpose**: Finds the first real user request in a conversation. This gives the self-improvement system the original task it should later judge against.

**Data flow**: It receives the full list of conversation messages. It scans from the beginning until it finds a user message whose content is plain non-empty text. It returns that text, or returns nothing if no suitable user request exists.

**Call relations**: When a trajectory is being checked for usefulness, bad_trajectory asks this function for the request. If no request can be found, the trajectory cannot become a training or evaluation example, because there is no clear user goal to grade against.

*Call graph*: called by 1 (bad_trajectory).


##### `first_tool_error`  (lines 46–65)

```
def first_tool_error(messages: tuple[Message, ...]) -> tuple[str, str] | None
```

**Purpose**: Finds the first failed tool result in a conversation and identifies which tool caused it. This is the file’s main way of detecting that a trajectory contains useful friction for self-improvement.

**Data flow**: It receives the conversation messages. First it records the names of tool calls by their IDs, because a later tool result points back to the call it answers. Then it scans again for the first tool result marked as an error. If it can match that error to a tool name, it returns the tool name and the error text; otherwise it returns nothing.

**Call relations**: bad_trajectory calls this before building an example. The returned tool name becomes the task class, and the error text becomes part of the problem description that the proposer can learn from.

*Call graph*: called by 1 (bad_trajectory).


##### `bad_trajectory`  (lines 68–78)

```
def bad_trajectory(trajectory: Trajectory) -> tuple[str, TaskExample] | None
```

**Purpose**: Decides whether one saved conversation is useful for self-improvement. A conversation qualifies only if it has both a user request and a tool error.

**Data flow**: It receives one trajectory, which includes the conversation ID and all messages. It asks first_tool_error for the first failed tool round and first_request for the original user request. If either is missing, it returns nothing. If both exist, it builds a TaskExample containing the ID, request, messages, and problem text, and returns it together with a class name based on the failed tool.

**Call relations**: task_classes calls this for every trajectory it is given. This function is the filter that turns a raw conversation history into either a useful labeled example or something to ignore.

*Call graph*: calls 2 internal fn (first_request, first_tool_error); called by 1 (task_classes); 1 external calls (__init__).


##### `task_classes`  (lines 81–94)

```
def task_classes(trajectories: tuple[Trajectory, ...]) -> tuple[TaskClass, ...]
```

**Purpose**: Builds the final set of task classes from many trajectories. Each class contains examples for one failed tool, split into examples to learn from and examples to test on.

**Data flow**: It receives a group of trajectories. For each one, it calls bad_trajectory; useful examples are collected under names like “tool:<name>”. It then asks _split to divide each large-enough group into mine and held-out sets. It returns the resulting classes, sorted so larger classes come first and ties are ordered by name.

**Call relations**: This is the main public assembly step in the file. It coordinates the lower-level checks: bad_trajectory extracts usable examples, and _split turns each group into a safe learning-versus-testing split.

*Call graph*: calls 2 internal fn (_split, bad_trajectory).


##### `_split`  (lines 97–102)

```
def _split(name: str, examples: tuple[TaskExample, ...]) -> TaskClass | None
```

**Purpose**: Splits one group of examples into a held-out evaluation set and a mining set. It also rejects groups that are too small to split safely.

**Data flow**: It receives a class name and all examples for that class. If there are not enough examples for both learning and evaluation, it returns nothing. Otherwise it sorts examples by conversation ID for a stable order, chooses an evaluation count, and returns a TaskClass with the early examples held out and the rest available for mining.

**Call relations**: task_classes calls this after grouping examples by failed tool. This function enforces the rule that the system should not be graded only on the same examples it used to propose an improvement.

*Call graph*: called by 1 (task_classes); 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/cron.py`

`orchestration` · `scheduled background self-improvement tick`

This file is the careful “tick” of the self-improvement system. It does not directly edit an agent’s prompt. Instead, it acts like a quality-control station: gather recent work, suggest one possible better prompt, test it on reserved examples, and only then ask the wider system to approve the change.

On each run, it asks the extension context for stored trajectories, which are records of past agent conversations and outcomes. It groups those records by agent, because each agent has its own prompt and history. For each agent, it checks a scoped store for an existing candidate prompt. That store is like a small notebook for this extension, remembering whether a candidate is still being tested, was rejected, or has already been promoted for approval.

If there is no active candidate for the agent’s current prompt version, the file asks a PromptProposer to suggest a new prompt based on one task class. It then saves that candidate and the held-out examples that should be used to test it. “Held-out” means examples reserved for checking quality, not for creating the proposal.

The candidate is evaluated against both its own held-out examples and other task examples. A single pass is not enough. The candidate must pass for a configured number of scheduled ticks in a row. If it fails, it is marked rejected and will not be proposed again for the same prompt version. If it keeps passing, this file opens an AgentChange proposal, leaving final approval to governance.

#### Function details

##### `ImproveCron.run`  (lines 50–52)

```
async def run(self) -> None
```

**Purpose**: Runs one full self-improvement pass across all agents that have trajectory data. It is the top-level method for this cron-style background job.

**Data flow**: It asks the context for all trajectories, groups them by agent, then sends each agent’s group of trajectories onward for processing. It does not return a result; its effect is to advance saved candidate states or open proposals when candidates qualify.

**Call relations**: This is the entry point within the file’s flow. It uses _by_agent to split the workspace history into per-agent batches, then calls ImproveCron._advance once for each agent so the rest of the logic can focus on one agent at a time.

*Call graph*: calls 2 internal fn (_advance, _by_agent).


##### `ImproveCron._advance`  (lines 54–60)

```
async def _advance(self, agent_id: UUID, trajectories: tuple[Trajectory, ...]) -> None
```

**Purpose**: Moves one agent’s improvement candidate forward by one step. It either finds or opens a candidate prompt for the agent, then tests whether that candidate should continue, fail, or become a proposal.

**Data flow**: It receives one agent ID and that agent’s trajectories. It reads the current prompt digest from the first trajectory, builds the store key for that agent, asks for an active candidate or opens a new one, and stops if there is nothing to test. If a candidate exists, it passes the candidate into the gate-checking step.

**Call relations**: ImproveCron.run calls this after grouping trajectories. This method is the bridge between candidate setup, done by ImproveCron._active_or_open, and candidate judging, done by ImproveCron._gate.

*Call graph*: calls 2 internal fn (_active_or_open, _gate); called by 1 (run).


##### `ImproveCron._active_or_open`  (lines 62–83)

```
async def _active_or_open(self, key: str, from_digest: str, trajectories: tuple[Trajectory, ...]) -> CandidateState | None
```

**Purpose**: Finds the agent’s current candidate prompt, or creates a new candidate if the agent’s current prompt version has no active one. This prevents the system from opening duplicate candidates for the same prompt version.

**Data flow**: It receives a store key, the current prompt digest, and the agent’s trajectories. It first reads the scoped store. If it finds a saved candidate tied to the same prompt digest and still marked as evaluating, it returns that candidate. If the saved candidate is already promoted or rejected, it returns nothing. If there is no matching active candidate, it builds task classes from the trajectories, asks the proposer for a new prompt, stores the new CandidateState, and returns it.

**Call relations**: ImproveCron._advance calls this before any evaluation happens. It relies on task_classes to find meaningful groups of examples, and on the proposer to create a possible improved prompt. If it returns a candidate, ImproveCron._advance hands that candidate to ImproveCron._gate.

*Call graph*: called by 1 (_advance); 2 external calls (__init__, task_classes).


##### `ImproveCron._gate`  (lines 85–117)

```
async def _gate(self, agent_id: UUID, key: str, from_digest: str, candidate: CandidateState, trajectories: tuple[Trajectory, ...]) -> None
```

**Purpose**: Tests a candidate prompt and decides whether it has failed, needs more successful runs, or is ready to become an approval proposal. This is the safety gate that stops one lucky test result from immediately changing an agent.

**Data flow**: It receives the agent ID, store key, current prompt digest, candidate state, and trajectories. It gathers held-out examples for the candidate’s own task and also held-out examples from other tasks. It asks the evaluator to compare the candidate prompt with the current prompt. If the candidate fails, it saves it as rejected with zero passes. If it passes but has not passed enough consecutive ticks, it saves the higher pass count and keeps it evaluating. If it reaches the stability threshold, it creates an AgentChange proposal and saves the candidate as promoted with the proposal ID.

**Call relations**: ImproveCron._advance calls this after a candidate is available. This method uses _held_out to rebuild the candidate’s reserved examples from current trajectories, uses task_classes to collect broader test examples, and calls ImproveCron._save whenever the candidate state changes. When the candidate is stable enough, it hands an AgentChange to the context’s proposal system instead of editing the agent directly.

*Call graph*: calls 2 internal fn (_save, _held_out); called by 1 (_advance); 2 external calls (__init__, task_classes).


##### `ImproveCron._save`  (lines 119–131)

```
async def _save(self, key: str, candidate: CandidateState, *, status: CandidateStatus, gate_passes: int, proposal_id: str | None=None) -> None
```

**Purpose**: Writes an updated candidate state to the extension’s scoped store. It keeps the stored record in step with the latest test result.

**Data flow**: It receives the store key, the old candidate state, the new status, the new pass count, and optionally a proposal ID. It copies the candidate with those updated fields, converts it to JSON-friendly data, and writes it back to the store. The output is not a returned value; the important change is the persisted candidate record.

**Call relations**: ImproveCron._gate calls this whenever a candidate is rejected, remains under evaluation, or is promoted. It is the small persistence helper that keeps the gate logic from repeating the same store-writing steps.

*Call graph*: called by 1 (_gate); 1 external calls (model_copy).


##### `_by_agent`  (lines 134–138)

```
def _by_agent(trajectories: tuple[Trajectory, ...]) -> Mapping[UUID, tuple[Trajectory, ...]]
```

**Purpose**: Splits a mixed list of trajectories into separate groups for each agent. This lets the improvement loop treat every agent independently.

**Data flow**: It receives all trajectories from the context. It builds a dictionary keyed by agent ID, appending each trajectory to the right agent’s list, then returns the same groups as tuples. It does not change the trajectories themselves.

**Call relations**: ImproveCron.run calls this at the start of a cron tick. Its output decides how many times ImproveCron._advance runs and which trajectories each agent’s advancement step receives.

*Call graph*: called by 1 (run).


##### `_held_out`  (lines 141–153)

```
def _held_out(trajectories: tuple[Trajectory, ...], held_out: tuple[str, ...]) -> tuple[TaskExample, ...]
```

**Purpose**: Rebuilds the candidate’s held-out test examples from the latest trajectory data. It only includes examples that are currently recognized as bad trajectories, because those are the cases useful for judging whether the new prompt improves behavior.

**Data flow**: It receives all trajectories for an agent and the stored conversation IDs for the candidate’s held-out set. It looks up each requested conversation ID, skips any that are missing, checks whether the trajectory is flagged as bad, and collects the corresponding TaskExample when one exists. It returns those examples as a tuple.

**Call relations**: ImproveCron._gate calls this when preparing data for evaluation. This helper uses bad_trajectory to turn raw trajectory records into the task examples that the evaluator needs.

*Call graph*: called by 1 (_gate); 1 external calls (bad_trajectory).


### `extensions/self_improvement/ufo_ext_self_improvement/model.py`

`io_transport` · `during model calls for proposing, replay, and grading`

The self-improvement extension needs to ask a language model for two kinds of help: plain text completions, and full conversation turns that may include tool use. This file defines that narrow doorway. Without it, each part of the extension would have to know how to build model requests, choose token limits, set caching, and turn off reasoning mode, which would make behavior easier to accidentally change in one place but not another.

There are two small protocol classes here: ModelLeg and ReplayLeg. A protocol is like a promise about shape: anything with the right method can be used, even if it does not inherit from the protocol directly. ModelLeg promises a text-only complete method. ReplayLeg promises a turn method that can include tool descriptions.

ModelAccessLeg is the real adapter. It wraps the SDK's ModelAccess object, which is the project's metered access point to the model. Think of it like a checkout counter: all model calls pass through the same counter so usage can be measured and controlled. For both kinds of calls, it builds a ModelRequest with the chosen model name, the system instruction, conversation messages, a 2048-token output limit, a short conversation cache lifetime, and reasoning set to off. The turn path also includes available tools.

#### Function details

##### `ModelLeg.complete`  (lines 13–13)

```
async def complete(self, system: str, messages: tuple[Message, ...]) -> str
```

**Purpose**: This defines the expected shape for something that can ask the model for a plain text answer. Code can depend on this small promise instead of depending on a specific model client class.

**Data flow**: It takes a system instruction and a tuple of conversation messages as inputs. An implementation is expected to send those to a model and return the model's text response as a string.

**Call relations**: This is a contract rather than working code. Other parts of the extension can call an object through this shape when they only need text completion, and ModelAccessLeg.complete is one concrete method that satisfies that shape.


##### `ReplayLeg.turn`  (lines 17–19)

```
async def turn(self, system: str, messages: tuple[Message, ...], tools: tuple[ToolSchema, ...]) -> Message
```

**Purpose**: This defines the expected shape for something that can ask the model to produce a full conversation message, possibly using tools. It is useful for replay-style flows where the model needs to act inside a tool-aware conversation rather than just return plain text.

**Data flow**: It takes a system instruction, conversation messages, and tool schemas, which are descriptions of tools the model is allowed to use. An implementation is expected to return one model-produced Message.

**Call relations**: This is also a contract rather than working code. Replay code can rely on this shape when it needs a model turn with tools, and ModelAccessLeg.turn is the concrete adapter method that fulfills it.


##### `ModelAccessLeg.complete`  (lines 28–38)

```
async def complete(self, system: str, messages: tuple[Message, ...]) -> str
```

**Purpose**: This sends a text-only request to the SDK-backed model access layer. It is used when the extension wants a simple string answer from the model, with the extension's standard limits and settings applied.

**Data flow**: It receives a system instruction and conversation messages. It packages them into a ModelRequest, adding the current model name, a 2048-token output cap, a five-minute conversation cache time, and reasoning turned off. It then awaits the SDK model call and returns the resulting text string.

**Call relations**: When caller code needs a plain completion, it comes through this method instead of building the model request itself. This method creates the ModelRequest and hands it to the wrapped ModelAccess.complete call, so model usage still goes through the SDK's metered access point.

*Call graph*: 1 external calls (__init__).


##### `ModelAccessLeg.turn`  (lines 40–53)

```
async def turn(self, system: str, messages: tuple[Message, ...], tools: tuple[ToolSchema, ...]) -> Message
```

**Purpose**: This sends a tool-aware conversation-turn request to the SDK-backed model access layer. It is used when the extension needs the model to return a Message, not just raw text, and to know what tools are available.

**Data flow**: It receives a system instruction, conversation messages, and tool schemas. It builds a ModelRequest that includes those pieces plus the model name, output-token limit, cache time, and reasoning setting. It then awaits the SDK model turn and returns the Message produced by the model.

**Call relations**: Replay-style or tool-aware flows call this method when they need the model's next conversational action. The method packages the request consistently and hands it to the wrapped ModelAccess.turn call, keeping tool-capable model calls on the same metered SDK path as other model use.

*Call graph*: 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/proposer.py`

`domain_logic` · `self-improvement proposal phase`

This file is part of a self-improvement loop for an AI agent. The basic idea is: if the system has collected examples of a task type where the agent ran into trouble, this code prepares those examples, shows them to another model, and asks for a better version of the agent’s system prompt. A system prompt is the instruction text that shapes how an AI agent behaves.

The file is careful not to let one bad case overly distort the whole agent. The instruction given to the model says to preserve the prompt’s voice and broad purpose, make the smallest helpful change, and avoid turning the agent into something that only works for this one task class.

The main class, `PromptProposer`, is like an editor who receives the current handbook, a short label for a problem area, and a few marked-up examples of what went wrong. It writes a draft revision and then checks whether the draft is worth keeping. If there are no mined examples, if the model returns nothing, or if the revised prompt is exactly the same as the old one, the file returns `None` instead of pretending there is an improvement.

One small helper also cleans up a common model habit: wrapping answers in Markdown code fences. That matters because the caller wants the prompt text itself, not formatting around it.

#### Function details

##### `PromptProposer.propose`  (lines 33–43)

```
async def propose(self, current_prompt: str, task_class: TaskClass) -> PromptCandidate | None
```

**Purpose**: This is the main entry point for asking the model to suggest a better system prompt for one task class. It only produces a candidate when there is evidence to learn from and when the proposed prompt is actually different from the current one.

**Data flow**: It receives the current prompt text and a task class containing mined examples of failures or friction. If the task class has no examples, it stops immediately. Otherwise, it builds a user-facing prompt with `PromptProposer._prompt`, sends that plus the fixed proposer instruction to the model, cleans the model’s reply with `_clean`, and compares it with the original prompt. The output is either a `PromptCandidate` containing the task name and revised prompt, or `None` when there is no useful proposal.

**Call relations**: This function drives the whole proposal step. It calls `PromptProposer._prompt` to package the evidence clearly for the model, creates a `Message` to send that evidence, waits for the model’s completion, then calls `_clean` so the result is plain prompt text. If the result passes the basic checks, it hands back a `PromptCandidate` for the next stage of the self-improvement process to evaluate.

*Call graph*: calls 2 internal fn (_prompt, _clean); 2 external calls (__init__, __init__).


##### `PromptProposer._prompt`  (lines 45–56)

```
def _prompt(self, current_prompt: str, task_class: TaskClass) -> str
```

**Purpose**: This builds the text that tells the model what task class is being improved, what the current system prompt says, and what examples show the agent struggling. It turns raw examples into a readable request for a prompt rewrite.

**Data flow**: It takes the current prompt and a task class. From the task class, it reads the task name and up to a limited number of mined examples, trimming each request and problem description to a safe length. It returns one formatted block of text containing the task label, the current prompt, the examples, and a final instruction to return the full revised prompt.

**Call relations**: This helper is used by `PromptProposer.propose` just before the model is called. Its job is to make sure the model sees the right context in a consistent shape, rather than receiving scattered fields or overly long examples.

*Call graph*: called by 1 (propose).


##### `_clean`  (lines 59–68)

```
def _clean(text: str) -> str
```

**Purpose**: This removes extra wrapping from the model’s answer so the caller gets just the revised prompt text. In particular, it strips whitespace and removes Markdown code fences if the model added them.

**Data flow**: It receives the raw text returned by the model. It trims leading and trailing whitespace, then checks whether the answer starts with a code fence like triple backticks. If so, it removes the opening fence and a closing fence if present, trims again, and returns the cleaned body.

**Call relations**: This helper is called by `PromptProposer.propose` after the model responds. It protects later stages from treating formatting as part of the actual system prompt, which would make comparisons and prompt storage less reliable.

*Call graph*: called by 1 (propose).

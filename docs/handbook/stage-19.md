# Cross-cutting observability, accounting, metering, and spend control  `stage-19` (cross-cutting infrastructure)

This stage is shared behind-the-scenes support for knowing what the system did and what it cost. It is not one single work step. Instead, it is used throughout the main work loop, background jobs, and admin reporting whenever the system calls a model, serves a proxy request, or needs to decide whether more work is allowed.

The accounting file is the central record keeper. When model usage happens, it writes token counts into a ledger, which is like a checkbook for usage. It can also prepare those records for an outside billing service, check spending limits before a task starts, and build summaries that humans can read on admin or spend pages.

The pricing file is the price tag machine. It knows how to turn token usage into money for each model. It also stamps each price table with a stable version, so later reports can prove which exact prices were used. Together, pricing calculates the charge, and accounting records, limits, exports, and summarizes it.

## Files in this stage

### Spend accounting and pricing
Records usage, checks spend limits, prepares billing data, and applies the model price catalog used to calculate costs.

### `core/src/ufo/accounting.py`

`domain_logic` · `cross-cutting: active during turn completion, sandbox proxy metering, spend admission checks, billing export jobs, and spend reporting`

This file solves a practical money problem: every model call costs something, and the system must count it once, price it correctly, stop work when a budget is reached, and later explain where the money went. The central idea is a ledger, like a bank statement. Each usage event becomes a row with a workspace, an amount, a cost in micro-dollars (millionths of a US dollar), and a dimension such as normal model tokens, sandbox model tokens, or sandbox network egress requests.

The file writes usage from several places. A normal turn gets one token bill per run attempt. Background jobs can bill directly to a workspace. Sandbox activity can add egress counts or sandbox token costs. The code is careful about retries and resumes, so a real provider charge is not lost, but the same charge is not billed twice.

It also prepares frozen export records for external billing consumers. “Frozen” matters because delivery may be retried; the receiver should see the same usage delta each time.

Finally, it enforces spend caps. A cap can apply to a whole workspace, one member, or one agent. The evaluator looks at recent ledger spending inside the cap’s time window, adds the cost that is about to happen, and returns allow, park, or reject. A separate rollup reads the same ledger to produce human-facing spending reports.

#### Function details

##### `applicable_caps_absent`  (lines 44–50)

```
def applicable_caps_absent(workspace_id: UUID, member_id: UUID | None, agent_id: UUID) -> bool
```

**Purpose**: Quickly answers whether the system recently learned that no spend cap applies to a specific workspace, member, and agent combination. This lets the common “no caps configured” case avoid an extra database check for a few seconds.

**Data flow**: It receives the workspace ID, optional member ID, and agent ID. It looks up that exact triple in a small in-memory cache and compares the stored expiry time with the current monotonic clock, which is a clock used for measuring elapsed time. It returns true only if the cache entry exists and has not expired; it changes nothing.

**Call relations**: This is a fast path other admission code can call before doing full cap enforcement. It relies on entries written by SpendEvaluator.decide through _note_absent_caps when a database check found no matching caps.

*Call graph*: 1 external calls (monotonic).


##### `_note_absent_caps`  (lines 53–62)

```
def _note_absent_caps(key: tuple[UUID, UUID | None, UUID]) -> None
```

**Purpose**: Remembers, briefly, that a particular workspace/member/agent combination has no applicable spend caps. This is a small performance shortcut, not the source of truth.

**Data flow**: It receives a cache key made from workspace ID, optional member ID, and agent ID. It reads the current monotonic time, removes expired cache entries if the cache is already large, then stores a new expiry time a few seconds in the future. It returns nothing and updates the module-level cache.

**Call relations**: SpendEvaluator.decide calls this after it has checked the database and found no caps. Later, applicable_caps_absent can use this note to skip a database round-trip for the same exact combination until the short time-to-live expires.

*Call graph*: called by 1 (decide); 1 external calls (monotonic).


##### `record_turn_usage`  (lines 65–105)

```
async def record_turn_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, usage: Usage, attempt: str='', pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: Records the model-token cost for one turn and one run attempt. It is built to be safe when a workflow is replayed, so the same attempt is not billed twice.

**Data flow**: It receives a database connection, workspace ID, turn ID, model name, token usage details, an attempt identifier, and pricing rules. It totals all token categories, returns immediately if the total is zero, computes a stable ledger ID for this turn, dimension, and attempt, checks whether that ledger row already exists, and inserts a priced ledger row only if it is new. The result is no returned value; the ledger table may gain one billing row.

**Call relations**: Turn-running code calls this after model usage is known. It asks Pricing.micro_usd to convert tokens into micro-dollar cost and uses ledger_id_for to make the write replay-safe. read_turn_cost later reads these rows to report a turn’s final billed cost.

*Call graph*: calls 1 internal fn (micro_usd); 4 external calls (execute, insert, select, ledger_id_for).


##### `read_turn_cost`  (lines 108–126)

```
async def read_turn_cost(connection: AsyncConnection, turn_id: UUID) -> tuple[int, int, str] | None
```

**Purpose**: Reads the total billed token cost for a turn. This matters because a parked and resumed turn may have several billed attempts that must be added together.

**Data flow**: It receives a database connection and a turn ID. It asks the ledger for all normal token rows for that turn, sums their token amounts and micro-dollar costs, and chooses a model value from the rows. It returns nothing if there are no billed rows, or a tuple containing total tokens, total cost, and model.

**Call relations**: Code that needs to show or use the final cost of a turn calls this after billing rows have been written by record_turn_usage. It does not write anything; it is a ledger reader.

*Call graph*: 2 external calls (execute, select).


##### `record_workspace_usage`  (lines 129–165)

```
async def record_workspace_usage(connection: AsyncConnection, workspace_id: UUID, model: str, usage: Usage, pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: Records model-token usage that belongs to a workspace but not to a specific turn, such as a background job. This keeps workspace-wide spending accurate without falsely attributing the cost to a member or agent.

**Data flow**: It receives a database connection, workspace ID, model name, usage details, and pricing rules. It totals the token usage, skips zero totals, prices the usage, and inserts a new ledger row with no turn ID. It returns nothing and may add one workspace-level billing row.

**Call relations**: Background-job code uses this when a model call happens outside the normal turn loop. Spend caps and rollups that sum by workspace include these rows, while member and agent reports generally do not because those reports join through turns.

*Call graph*: calls 1 internal fn (micro_usd); 3 external calls (execute, insert, uuid4).


##### `record_egress_request`  (lines 168–197)

```
async def record_egress_request(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, amount: int=1) -> None
```

**Purpose**: Counts sandbox egress requests, meaning outbound network requests made through the sandbox proxy. These are metered as a count, but priced at zero, so they are visible without increasing spend.

**Data flow**: It receives a database connection, workspace ID, turn ID, and an amount to add. It builds a stable ledger ID for the turn’s egress dimension, then inserts a row or, if it already exists, atomically adds to its amount. It returns nothing and updates the ledger count for that turn.

**Call relations**: The sandbox egress proxy calls this as requests happen. It uses ledger_id_for so all egress for a turn accumulates into one egress row, separate from token billing written by record_turn_usage or record_sandbox_tokens.

*Call graph*: 2 external calls (execute, ledger_id_for).


##### `record_sandbox_tokens`  (lines 200–251)

```
async def record_sandbox_tokens(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, usage: Usage, pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: Records model-token usage caused by code running inside the sandbox through the egress proxy. This keeps sandbox model calls separate from the host system’s own model calls while still billing their real cost.

**Data flow**: It receives a database connection, workspace ID, turn ID, model name, usage details, and pricing rules. It totals tokens, skips zero usage, calculates the micro-dollar cost, then inserts or atomically updates a sandbox-token ledger row for the turn. It returns nothing and may increase both token amount and priced cost in the ledger.

**Call relations**: Sandbox proxy code calls this when an in-sandbox model call completes. It uses Pricing.micro_usd for cost and ledger_id_for for a stable per-turn sandbox-token row. Later, usage export and spend reports include these rows as their own dimension.

*Call graph*: calls 1 internal fn (micro_usd); 2 external calls (execute, ledger_id_for).


##### `mint_usage_exports`  (lines 276–381)

```
async def mint_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, floor: datetime, key_slot_for: Callable[[str], str | None]) -> None
```

**Purpose**: Creates frozen export-intent rows for usage that has not yet been sent to an external billing consumer. This turns growing ledger rows into stable chunks that can be retried safely.

**Data flow**: It receives a database connection, workspace ID, consumer name, a lower time bound, and a function that maps model names to credential slots. It reads workspace-stored credential slots, finds ledger rows whose amounts have grown beyond what this consumer has already exported, filters to usage that is settled enough to export, labels host token usage as bring-your-own-key when appropriate, and inserts export rows keyed so duplicates are ignored. It returns nothing and may add rows to the ledger_export table.

**Call relations**: A usage-export job calls this before reading pending exports. It reads ledger rows written by record_turn_usage and record_sandbox_tokens, deliberately ignores zero-priced egress rows, and prepares the stable records that read_pending_usage_exports will deliver.

*Call graph*: 5 external calls (now, timedelta, execute, or_, select).


##### `read_pending_usage_exports`  (lines 384–429)

```
async def read_pending_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, limit: int) -> tuple[UsageExport, ...]
```

**Purpose**: Reads the frozen usage export records that have not yet been acknowledged by an external billing consumer. It gives the sender a stable batch to deliver.

**Data flow**: It receives a database connection, workspace ID, consumer name, and maximum number of rows. It joins export-intent rows to their ledger rows for descriptive details, filters to unacknowledged exports, orders them in mint order, and converts each row into a UsageExport data object. It returns a tuple of those export objects and changes nothing.

**Call relations**: An export worker calls this after mint_usage_exports has created export intents. If delivery fails before acknowledgement, the same function will return the same frozen intents again.

*Call graph*: 3 external calls (__init__, execute, select).


##### `ack_usage_exports`  (lines 432–456)

```
async def ack_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: Marks exported usage records as acknowledged after the external billing consumer has accepted them. This prevents successfully delivered items from being sent again.

**Data flow**: It receives a database connection, workspace ID, consumer name, and the exact UsageExport objects that were delivered. It builds matching conditions from each export’s ledger ID and starting amount, then updates those rows with an acknowledgement time. It returns nothing and changes matching ledger_export rows.

**Call relations**: The export worker calls this only after the outside billing API accepts a batch returned by read_pending_usage_exports. If the process crashes before this update, the records remain pending and can be safely retried.

*Call graph*: 3 external calls (execute, or_, update).


##### `metered_workspaces`  (lines 459–462)

```
def metered_workspaces() -> WorkspaceCandidates
```

**Purpose**: Finds candidate workspaces for the usage-export job: any workspace that has ever had ledger activity. It is intentionally broad so the job does not need a perfect schedule of who currently has pending usage.

**Data flow**: It takes no arguments. It builds a candidate source from the distinct workspace IDs found in the ledger table. It returns a WorkspaceCandidates object, which is a helper for iterating over workspace owners.

**Call relations**: Billing export orchestration can call this to decide which workspaces to inspect. The returned candidates are later checked more precisely by minting and pending-export reads, so extra no-op candidates are acceptable.

*Call graph*: 1 external calls (owner_candidates).


##### `SpendEvaluator.decide`  (lines 497–512)

```
async def decide(self, connection: AsyncConnection, pending_micro_usd: int) -> SpendDecision
```

**Purpose**: Decides whether a turn is allowed to proceed under the workspace’s spend caps. It can allow the turn, park it until a cap changes, or reject it outright.

**Data flow**: It receives a database connection and the cost that is about to be added, in micro-dollars. It reads all caps that apply to this workspace/member/agent, caches the absence of caps when none apply, sums current spending for each cap’s rolling time window, adds the pending cost, and compares the result to each cap’s limit. It returns a SpendDecision with an outcome and, when blocked, a human-readable message.

**Call relations**: Admission or mid-turn enforcement code calls this before allowing more paid work. It coordinates the helper steps: _applicable_caps finds relevant caps, _used_micro_usd measures spend for each cap, _message explains a breach, and _note_absent_caps speeds up later no-cap checks.

*Call graph*: calls 4 internal fn (_applicable_caps, _message, _used_micro_usd, _note_absent_caps); 1 external calls (__init__).


##### `SpendEvaluator._applicable_caps`  (lines 514–542)

```
async def _applicable_caps(self, connection: AsyncConnection) -> tuple[SpendCap, ...]
```

**Purpose**: Loads the spend caps that apply to this particular turn context. A cap can match the whole workspace, the current member, or the current agent.

**Data flow**: It receives a database connection and reads the evaluator’s stored workspace ID, member ID, and agent ID. It queries the spend_cap table for rows that match those subjects and converts each row into a SpendCap object. It returns a tuple of SpendCap objects without changing the database.

**Call relations**: SpendEvaluator.decide calls this first. If it returns an empty tuple, decide can allow the turn and record a short-lived no-cap cache entry.

*Call graph*: called by 1 (decide); 4 external calls (__init__, execute, or_, select).


##### `SpendEvaluator._used_micro_usd`  (lines 544–570)

```
async def _used_micro_usd(self, connection: AsyncConnection, cap: SpendCap) -> int
```

**Purpose**: Calculates how much money has already been spent inside one cap’s rolling time window. This is the number compared against the cap limit.

**Data flow**: It receives a database connection and a SpendCap. It computes the window cutoff from the current time and the cap’s length, then sums priced ledger rows after that cutoff. For a workspace cap it sums the workspace ledger directly; for a member cap it follows ledger rows through turns and conversations to that member; for an agent cap it follows ledger rows through turns to that agent. It returns the summed micro-dollar amount.

**Call relations**: SpendEvaluator.decide calls this once for each applicable cap. The returned spend is combined with the pending cost to decide whether that cap has been breached.

*Call graph*: called by 1 (decide); 4 external calls (now, timedelta, execute, select).


##### `SpendEvaluator._message`  (lines 572–583)

```
def _message(self, outcome: SpendOutcome, breaches: list[SpendCap]) -> str
```

**Purpose**: Builds the message shown when spending is blocked by a cap. It chooses the tightest breached cap and explains whether the turn was parked or rejected.

**Data flow**: It receives the chosen outcome and the list of breached caps. It finds the breached cap with the smallest limit, converts the limit from micro-dollars into dollars, and formats a plain English sentence. It returns that string and changes nothing.

**Call relations**: SpendEvaluator.decide calls this only after one or more caps are breached. The resulting message is placed in the SpendDecision returned to the caller.

*Call graph*: called by 1 (decide).


##### `SpendRollup.read`  (lines 633–709)

```
async def read(self, connection: AsyncConnection, window_seconds: int) -> SpendReport
```

**Purpose**: Builds a spending report for one workspace over a recent time window. This powers user-facing views such as a command-line spend report or web dashboard.

**Data flow**: It receives a database connection and a window length in seconds. It computes the cutoff time, sums total priced ledger cost for the workspace, then runs grouped queries to break spending down by ledger dimension, by member, by agent, and by price digest, which identifies the pricing table version used. It returns a SpendReport containing all those totals and does not modify the database.

**Call relations**: Reporting code calls this when someone asks how much a workspace has spent. It reads the same ledger rows written by the usage-recording functions, so the report and cap enforcement are based on the same source of truth.

*Call graph*: 8 external calls (__init__, __init__, __init__, __init__, now, timedelta, execute, select).


### `core/src/ufo/models/pricing.py`

`domain_logic` · `cross-cutting during accounting and usage recording`

Language models are usually billed by how many tokens they read and write. A token is a small chunk of text, and different kinds of tokens can cost different amounts: input text, output text, cached reads, and cached writes. This file gives the project one clear place to describe those rates and apply them consistently.

The core idea is simple: each model has a `ModelPrice`, which says how many micro-dollars are charged per million tokens for each token category. A micro-dollar is one millionth of a US dollar, which lets the system store tiny costs as whole numbers instead of using floating-point decimals that can round strangely.

When usage is billed, `usage_priced_micro_usd` multiplies each token count by the matching rate, adds the pieces together, then divides by one million to get the final micro-dollar cost. If the system sees usage for a model that is no longer in the price table, it logs a warning and returns zero instead of crashing. That matters for old historical records.

The file also creates a digest, which is like a fingerprint for the price table. If any model price changes, the fingerprint changes too. The `Pricing` object keeps the table and this fingerprint together, so accounting records can say not only what was charged, but which price list produced the charge.

#### Function details

##### `price_digest`  (lines 24–39)

```
def price_digest(prices: Mapping[str, ModelPrice]) -> str
```

**Purpose**: Creates a stable fingerprint for a model price table. This lets the system later identify exactly which set of prices was used for billing.

**Data flow**: It receives a mapping from model names to their prices. It sorts the models, turns the prices into a compact JSON string, hashes that string with SHA-256, and returns the hash prefixed with `sha256:`. The input table is not changed.

**Call relations**: When a new `Pricing` object is built, `pricing_from` calls this function to stamp the price table with a version-like identifier. Internally it relies on JSON formatting and SHA-256 hashing to make the same table always produce the same fingerprint.

*Call graph*: called by 1 (pricing_from); 2 external calls (sha256, dumps).


##### `usage_priced_micro_usd`  (lines 42–54)

```
def usage_priced_micro_usd(model: str, usage: Usage, prices: Mapping[str, ModelPrice]) -> int
```

**Purpose**: Calculates the cost of one model usage record in micro-US dollars. It is the main billing math for token usage.

**Data flow**: It receives a model name, a usage record with token counts, and the current price table. It looks up the model’s rates, multiplies each token count by the matching rate, adds the totals, and converts from “per million tokens” into micro-dollars. If the model is missing from the table, it logs `pricing.unknown_model` and returns 0.

**Call relations**: This is the worker function behind `Pricing.micro_usd`. The accounting code does not call it directly; it asks a `Pricing` object for a cost, and that method hands the calculation to this function.

*Call graph*: called by 1 (micro_usd); 1 external calls (log).


##### `Pricing.micro_usd`  (lines 64–65)

```
def micro_usd(self, model: str, usage: Usage) -> int
```

**Purpose**: Provides a convenient method for pricing usage with the price table stored inside a `Pricing` object. Callers use it when they need to attach a cost to recorded model activity.

**Data flow**: It receives a model name and a usage record. It combines those with the `prices` stored on the `Pricing` object, passes them to `usage_priced_micro_usd`, and returns the resulting micro-dollar amount. It does not modify the price table or the usage record.

**Call relations**: Accounting routines call this when recording sandbox, turn, or workspace usage. It acts like the public doorway into the pricing calculation, while `usage_priced_micro_usd` does the detailed math.

*Call graph*: calls 1 internal fn (usage_priced_micro_usd); called by 3 (record_sandbox_tokens, record_turn_usage, record_workspace_usage).


##### `pricing_from`  (lines 68–71)

```
def pricing_from(prices: Mapping[str, ModelPrice]) -> Pricing
```

**Purpose**: Builds a complete `Pricing` object from a raw model price table. It pairs the prices with their digest so billing can be both calculated and traceable.

**Data flow**: It receives a mapping of model names to `ModelPrice` values. It copies that mapping into a plain dictionary, computes its digest with `price_digest`, and returns a new immutable `Pricing` object containing both the copied table and the digest.

**Call relations**: This is used when the system needs to turn a configured or supplied price list into the object used by accounting. It calls `price_digest` first, then constructs `Pricing` with the finished table and its fingerprint.

*Call graph*: calls 1 internal fn (price_digest); 1 external calls (__init__).

## 📊 State Registers Touched

- `reg-workspace-boundary` — The current workspace or tenant boundary used to keep each customer’s data and actions separate.
- `reg-pricing-table` — The shared price list used to turn model and service usage into cost records.
- `reg-credential-store` — The encrypted store of API keys, service secrets, and owner-provided credentials.
- `reg-membership-and-seats` — The shared membership, admin role, paid seat, and seat-limit state for a workspace.
- `reg-turn-queue` — The durable queue of conversation turns waiting to be claimed, run, completed, cancelled, or retried.
- `reg-sandbox-session` — The saved or live sandbox workspace where an agent can run commands and keep files across tool calls.
- `reg-egress-policy` — The network access rules that decide which outside sites sandboxed work may contact and which secrets may be injected.
- `reg-background-jobs` — The shared registry and saved queue of scheduled, recurring, delayed, and administrative background work.
- `reg-fleet-presence` — The shared record of live runtime processes used for supervision, cancellation, and recovery after crashes.
- `reg-accounting-ledger` — The shared usage ledger that records tokens, egress, sandbox usage, billing exports, and spend-limit checks.
- `reg-observability-context` — The shared logging, metrics, tracing, and trace-link state used to understand work across requests and subagents.
- `reg-proposal-governance` — The saved proposals and safety checks used to govern prompt or system improvements before applying them.
- `reg-acting-principal-scope` — The current acting principal context—member, agent, on-behalf-of member, and object/agent scope—used to authorize actions, attribute turns, choose grants, and keep tool work tied to the right actor.
- `reg-model-token-budget` — The per-turn model budget state derived from model context/output limits and spend policy, used while assembling prompts, truncating or summarizing context, and tracking remaining usage during model calls.

# Usage accounting and prepaid billing  `stage-21.3` (cross-cutting infrastructure)

This stage is the money meter for the system’s main work loop. As workspaces use paid resources, it tracks what was used, checks whether spending is allowed, and records charges against prepaid credit.

The pricing file defines how model use becomes a cost, such as turning token counts into dollars. It also stamps each price table version, so later reports can show which prices were applied. The balance file is like a wallet manager. It tracks credit added, credit spent, required reserve money, and automatic refills, and it decides whether paid work may continue. The accounting file is the trusted ledger. It records model calls, sandbox network use, images, videos, and other charges, applies spend limits, deducts prepaid balance when needed, and builds usage summaries.

The Metronome extension connects this internal ledger to outside billing tools. It exports usage to Metronome, connects prepaid payments through Stripe, and provides admin-facing billing controls and status pages. The package marker file simply makes the billing code importable by the rest of the system.

## Files in this stage

### Billing foundations
Package setup and model pricing definitions provide the basic structures used by the billing system.

### `core/src/ufo/runtime/billing/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Here, that package is `ufo.runtime.billing`, which likely groups code related to billing during runtime.

Because the file is empty, it does not run setup code, expose shortcut imports, or define any classes or functions. Its value is structural: it makes the folder part of the project’s import tree. A helpful analogy is a labeled folder in a filing cabinet. The label does not contain the documents, but it lets people find and refer to the folder reliably.

Without this file, depending on the Python version and packaging setup, imports that expect `ufo.runtime.billing` to be a regular package could fail or behave differently. So this file matters mainly because it keeps the project’s package layout clear and importable.


### `core/src/ufo/harness/models/pricing.py`

`domain_logic` · `billing and usage recording`

This file is the small billing calculator for model usage. Models charge different rates for different kinds of tokens, such as input tokens, output tokens, and cached tokens. The file stores those rates in `ModelPrice`, then uses them to compute a cost in micro-dollars, meaning millionths of a US dollar. Using micro-dollars keeps billing as whole-number math, which avoids rounding surprises from decimal fractions.

The main idea is simple: take a usage report, look up the model’s prices, multiply each token count by the right rate, add the results, and divide by one million because the rates are expressed per million tokens. If the system sees usage for a model that is not in the price table, it logs a warning and returns zero instead of crashing. That matters for old or historical records where a model name may no longer be known.

The file also makes a digest, a cryptographic fingerprint, of the price table. Think of it like a tamper-evident label on a jar: if any rate changes, the label changes too. The `Pricing` object bundles the table and that fingerprint together, so later billing code can both calculate cost and record which price version was used.

#### Function details

##### `price_digest`  (lines 27–44)

```
def price_digest(prices: Mapping[str, ModelPrice]) -> str
```

**Purpose**: This function creates a stable fingerprint for a whole model price table. It is used so billed usage can be tied back to the exact set of rates that produced the charge.

**Data flow**: It receives a mapping from model names to `ModelPrice` values. It sorts the models, turns their rates into a compact JSON string, then runs that string through SHA-256, a standard one-way fingerprint algorithm. It returns a text label beginning with `sha256:` followed by the fingerprint.

**Call relations**: When a new `Pricing` object is built, `pricing_from` calls this function to stamp the table with its version. Inside, it relies on JSON formatting and SHA-256 hashing to make the same price table always produce the same digest.

*Call graph*: called by 1 (pricing_from); 2 external calls (sha256, dumps).


##### `usage_priced_micro_usd`  (lines 47–61)

```
def usage_priced_micro_usd(model: str, usage: Usage, prices: Mapping[str, ModelPrice]) -> int
```

**Purpose**: This function calculates the cost of one usage record for one model. It is the core arithmetic that turns token counts into micro-US dollars.

**Data flow**: It receives a model name, a `Usage` record containing token counts, and a price table. It looks up the model’s rates. If the model is missing, it writes a warning to the observability log and returns zero. If the model is found, it multiplies each kind of token count by its matching rate, adds those values together, divides by one million tokens, and returns the final cost as an integer number of micro-dollars.

**Call relations**: This function is called by `Pricing.micro_usd`, which provides the price table stored inside a `Pricing` object. It also calls the logging helper when it finds usage for an unknown model, so the system can notice the data problem without stopping billing.

*Call graph*: called by 1 (micro_usd); 1 external calls (log).


##### `Pricing.micro_usd`  (lines 71–72)

```
def micro_usd(self, model: str, usage: Usage) -> int
```

**Purpose**: This method is the convenient public way to price a usage record using a `Pricing` object. Callers do not need to pass the price table separately because the object already carries it.

**Data flow**: It receives a model name and a `Usage` record. It takes the price table stored on `self`, passes everything to `usage_priced_micro_usd`, and returns the computed cost in micro-US dollars. It does not change the `Pricing` object.

**Call relations**: Billing code calls this method when recording sandbox, turn, or workspace usage. The method then delegates the actual calculation to `usage_priced_micro_usd`, keeping the outside billing code simple and consistent.

*Call graph*: calls 1 internal fn (usage_priced_micro_usd); called by 3 (record_sandbox_tokens, record_turn_usage, record_workspace_usage).


##### `pricing_from`  (lines 75–78)

```
def pricing_from(prices: Mapping[str, ModelPrice]) -> Pricing
```

**Purpose**: This function builds a complete `Pricing` object from a raw model price table. It packages the rates together with the digest that identifies those rates.

**Data flow**: It receives a mapping of model names to prices. It copies that mapping into a normal dictionary, computes a digest for the copied table with `price_digest`, then returns a new `Pricing` object containing both the table and the digest.

**Call relations**: This is the setup helper for creating pricing data. It calls `price_digest` before constructing `Pricing`, so any later caller of `Pricing.micro_usd` is using a table that already has its version stamp attached.

*Call graph*: calls 1 internal fn (price_digest); 1 external calls (__init__).


### Prepaid balance and usage ledger
Core billing logic tracks prepaid credit, reserves, spend limits, usage charges, and usage reports.

### `core/src/ufo/runtime/billing/balance.py`

`domain_logic` · `cross-cutting billing checks, admin billing actions, and refill jobs`

A workspace in this system can have a prepaid balance measured in micro-USD, meaning millionths of a US dollar. This file treats that balance like a wallet. Purchases add credit to the wallet, model usage spends from it, and billing gates check whether enough credit remains before more work starts.

The important design choice is that the current balance is stored as its own database row, instead of being recalculated from every purchase each time. That matters because the system checks the balance very often, before model rounds, and summing a lifetime of purchases would get slower forever. The purchase records still remain as the audit trail, like receipts kept behind the wallet total.

The file also supports automatic top-ups. A workspace can say, “when my balance falls below this threshold, add this amount.” The refill job can then find only the workspaces that are actually low. There is also a small grace amount for workspaces whose payment card has successfully paid before, so work is not stopped just because the refill process takes a short time.

To avoid unnecessary database reads, the file briefly remembers when a workspace has no balance row at all. This is only a speed shortcut; real correctness still comes from the database, and the shortcut is cleared as soon as credit is added.

#### Function details

##### `balance_absent`  (lines 40–46)

```
def balance_absent(workspace_id: UUID) -> bool
```

**Purpose**: Quickly answers whether this process recently learned that a workspace has no balance row. It is used as a cheap shortcut so unpaid or self-hosted workspaces do not force a database lookup every time.

**Data flow**: It takes a workspace ID, checks an in-memory note for that workspace, compares its expiry time with the current clock, and returns true only if the note is still fresh. It does not change the database or prove anything permanently.

**Call relations**: Other billing code can ask this before doing a more expensive read. It relies on notes written by balance reads that found no row, and those notes are removed when credit is later added.

*Call graph*: 1 external calls (monotonic).


##### `_note_absent_balance`  (lines 49–56)

```
def _note_absent_balance(workspace_id: UUID) -> None
```

**Purpose**: Remembers for a few seconds that a workspace had no balance row. This makes repeated balance checks cheaper when there is nothing to bill.

**Data flow**: It receives a workspace ID, reads the current monotonic clock, cleans out expired cache entries if the cache is full, and stores a new expiry time for that workspace. The result is a temporary in-memory marker.

**Call relations**: It is called by read_headroom and read_balance after they look in the database and find no balance row. Later, balance_absent can use this marker as a fast path.

*Call graph*: called by 2 (read_balance, read_headroom); 1 external calls (monotonic).


##### `_forget_absent_balance`  (lines 59–62)

```
def _forget_absent_balance(workspace_id: UUID) -> None
```

**Purpose**: Clears the temporary “no balance exists” marker for a workspace. This prevents the shortcut from hiding a balance after credit has been added.

**Data flow**: It receives a workspace ID and removes that ID from the in-memory absent-balance cache if present. It returns nothing and touches no database rows.

**Call relations**: credit calls this after it successfully creates or increases a workspace balance. That way, future checks do not keep believing the old “no balance” result.

*Call graph*: called by 1 (credit).


##### `billing_screen_url`  (lines 79–88)

```
def billing_screen_url(public_base_url: str | None, home_surface: str | None) -> str | None
```

**Purpose**: Builds the web address for the workspace billing screen, if this deployment has one. It gives refusal messages somewhere useful to send an admin.

**Data flow**: It takes a public base URL and a home surface name. If either is missing, it returns None; otherwise it joins them into a URL ending at the billing section.

**Call relations**: Startup or setup code can prepare this URL and pass it down to billing gates. balance_refusal_message can then include it when explaining why work was refused.


##### `balance_refusal_message`  (lines 91–100)

```
def balance_refusal_message(billing_url: str | None) -> str
```

**Purpose**: Creates the message shown when a workspace is out of credit. It explains the problem and, when possible, tells an admin where to fix it.

**Data flow**: It takes an optional billing URL. If no URL exists, it returns a plain message saying an admin can set up refills; if a URL exists, it includes that address.

**Call relations**: Billing gates and tools can use this when they stop work because credit is exhausted. It pairs naturally with billing_screen_url, which supplies the link when the deployment has a browser billing page.


##### `read_auto_topup`  (lines 112–134)

```
async def read_auto_topup(connection: AsyncConnection, workspace_id: UUID) -> AutoTopup | None
```

**Purpose**: Checks whether a workspace has automatic refill settings and is currently low enough to need a refill. It answers the refill job’s question: “Should this workspace be charged now?”

**Data flow**: It takes a database connection and workspace ID, reads the balance and auto-top-up settings from the workspace balance row, and returns None if there is no row, no refill setting, or the balance is still above the threshold. If the workspace is at or below the threshold, it returns the refill amount and threshold as an AutoTopup value.

**Call relations**: A payment extension or refill worker calls this before attempting a charge. It keeps the “is the workspace short?” decision in core billing, while leaving the actual payment method to the payment extension.

*Call graph*: 3 external calls (__init__, execute, select).


##### `topping_up_workspaces`  (lines 137–151)

```
def topping_up_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds the candidate set for the periodic refill job: workspaces that configured automatic refills and have fallen to or below their threshold. This lets the job avoid waking every workspace unnecessarily.

**Data flow**: It defines a database query for low-balance workspaces with auto-top-up enabled, then passes that query to the workspace candidate system. The output is a WorkspaceCandidates object that can be used to iterate or schedule refill work.

**Call relations**: The refill job uses this as its starting list. Inside it, short_of_its_line creates the actual database selection, and owner_candidates wraps that selection in the project’s workspace-candidate machinery.

*Call graph*: 1 external calls (owner_candidates).


##### `topping_up_workspaces.short_of_its_line`  (lines 144–149)

```
def short_of_its_line() -> sa.Select[tuple[UUID]]
```

**Purpose**: Defines the database query for workspaces that are below their automatic refill line. It is the precise filter used by topping_up_workspaces.

**Data flow**: It reads no data immediately. Instead, it builds a SQL select statement that will return workspace IDs where an auto-top-up amount exists and the balance is no higher than the configured threshold.

**Call relations**: It is a small inner helper used only by topping_up_workspaces. topping_up_workspaces hands it to owner_candidates so the broader candidate system can run the query when needed.

*Call graph*: 1 external calls (select).


##### `set_auto_topup`  (lines 154–174)

```
async def set_auto_topup(connection: AsyncConnection, workspace_id: UUID, amount_micro_usd: int | None, threshold_micro_usd: int | None) -> bool
```

**Purpose**: Turns automatic refilling on or off for a workspace. It requires the refill amount and the trigger threshold to be set together, so there is no half-configured billing rule.

**Data flow**: It takes a database connection, workspace ID, optional refill amount, and optional threshold. If exactly one of amount or threshold is missing, it raises an error. Otherwise it updates the existing balance row and returns true if one row was changed, false if the workspace has no balance row.

**Call relations**: Admin billing flows call this when a workspace owner changes refill settings. It writes the settings that read_auto_topup and configured_auto_topup later read.

*Call graph*: 2 external calls (execute, update).


##### `mark_topup_verified`  (lines 177–191)

```
async def mark_topup_verified(connection: AsyncConnection, workspace_id: UUID) -> None
```

**Purpose**: Records that this workspace has successfully paid at least one automatic top-up. That proof earns the workspace a fixed grace amount when balance checks are made.

**Data flow**: It takes a database connection and workspace ID, then updates the workspace balance row only if the verification timestamp is still empty. It stamps the current time and leaves existing verification alone.

**Call relations**: Payment or refill code calls this after a charge has settled. read_headroom later sees the timestamp and includes the grace amount used by billing gates.

*Call graph*: 2 external calls (execute, update).


##### `configured_auto_topup`  (lines 203–224)

```
async def configured_auto_topup(connection: AsyncConnection, workspace_id: UUID) -> AutoTopup | None
```

**Purpose**: Reads the automatic refill settings exactly as configured, whether or not the workspace is currently low on credit. This is useful for showing an admin the current settings.

**Data flow**: It takes a database connection and workspace ID, reads the auto-top-up amount and threshold from the balance row, and returns None if there is no row or no refill setting. Otherwise it returns those two numbers as an AutoTopup value.

**Call relations**: Admin-facing code uses this to display settings. It differs from read_auto_topup, which returns a refill only when the balance has already reached the trigger line.

*Call graph*: 3 external calls (__init__, execute, select).


##### `read_headroom`  (lines 227–246)

```
async def read_headroom(connection: AsyncConnection, workspace_id: UUID) -> Headroom | None
```

**Purpose**: Reads the small set of balance numbers needed before allowing another model round to start. It is deliberately lighter than a full balance report because it runs very often.

**Data flow**: It takes a database connection and workspace ID, reads the current balance, required reserve, and whether a top-up has ever been verified. If no row exists, it records a short-lived absent-balance note and returns None. If a row exists, it returns a Headroom value with balance, reserve, and any earned grace amount.

**Call relations**: Billing gates call this before admitting work. If it finds no balance row, it calls _note_absent_balance so later checks can skip needless reads for a short time.

*Call graph*: calls 1 internal fn (_note_absent_balance); 3 external calls (__init__, execute, select).


##### `recent_purchases`  (lines 260–293)

```
async def recent_purchases(connection: AsyncConnection, workspace_id: UUID, limit: int) -> tuple[Purchase, ...]
```

**Purpose**: Returns the newest balance credits for a workspace, limited to a requested count. This supports billing screens that show where the current credit came from.

**Data flow**: It takes a database connection, workspace ID, and maximum number of rows. It queries purchase records for that workspace, orders newest first with a stable tie-breaker, and returns Purchase values containing granted amount, charged amount, and creation time.

**Call relations**: Admin or operator views use this beside read_balance. read_balance gives lifetime totals, while recent_purchases gives the recent receipt-like entries behind those totals.

*Call graph*: 3 external calls (__init__, execute, select).


##### `read_balance`  (lines 296–326)

```
async def read_balance(connection: AsyncConnection, workspace_id: UUID) -> Balance | None
```

**Purpose**: Reads the full balance picture for a workspace: what is left, what reserve is required, how much was ever granted, how much was ever charged, and when the last purchase happened. This is meant for humans or operators, not the fast gate path.

**Data flow**: It takes a database connection and workspace ID. First it reads the workspace balance row; if none exists, it notes that absence and returns None. If a row exists, it separately sums all purchase records for that workspace and returns a Balance value with the current row plus lifetime totals.

**Call relations**: Admin and operator reporting call this when they need the whole story. When it finds no row, it calls _note_absent_balance; when a later credit happens, credit clears that note.

*Call graph*: calls 1 internal fn (_note_absent_balance); 3 external calls (__init__, execute, select).


##### `credit`  (lines 329–387)

```
async def credit(connection: AsyncConnection, workspace_id: UUID, granted_micro_usd: int, charged_micro_usd: int, reference: str) -> bool
```

**Purpose**: Adds credit to a workspace balance exactly once for a given reference, such as a payment ID. This prevents duplicate payment notifications from adding the same money twice.

**Data flow**: It takes a database connection, workspace ID, granted amount, charged amount, and unique reference. It tries to insert a purchase record; if a record with the same workspace and reference already exists, it returns false and changes nothing. If the purchase is new, it creates or updates the workspace balance by the granted amount, clears any “no balance” cache note, and returns true.

**Call relations**: Payment fulfillment and operator credit flows call this inside their own database transaction. If it returns true and the transaction later commits, the caller can call count_charge to publish the charged-money metric.

*Call graph*: calls 1 internal fn (_forget_absent_balance); 2 external calls (execute, uuid4).


##### `count_charge`  (lines 390–407)

```
def count_charge(charged_micro_usd: int) -> None
```

**Purpose**: Reports a metric for money actually charged, after the database transaction that recorded the credit has committed. It avoids counting grants, refunds, failed transactions, or duplicate payment deliveries.

**Data flow**: It takes a charged amount in micro-USD. If the amount is zero or negative, it does nothing. If it is positive, it emits the balance_charged_micro_usd_total metric with that amount.

**Call relations**: Callers use this only after credit returned true and their transaction committed. It hands the number to emit_metric, keeping measurement separate from the database write so rolled-back credits are not counted.

*Call graph*: 1 external calls (emit_metric).


##### `debit`  (lines 410–430)

```
async def debit(connection: AsyncConnection, workspace_id: UUID, micro_usd: int) -> int
```

**Purpose**: Subtracts usage cost from a workspace balance when paid work has spent money. It records what was actually taken, while allowing the balance to go negative if a turn overshoots.

**Data flow**: It takes a database connection, workspace ID, and amount to subtract. If the amount is zero, it returns zero. Otherwise it updates the balance row by subtracting the amount; if a row existed, it returns the requested amount, and if no row existed, it returns zero.

**Call relations**: Ledger-writing code calls this in the same transaction as the usage record. That way the spending record and the balance movement succeed or fail together.

*Call graph*: 2 external calls (execute, update).


##### `set_reserve`  (lines 433–444)

```
async def set_reserve(connection: AsyncConnection, workspace_id: UUID, reserve_micro_usd: int) -> bool
```

**Purpose**: Sets the minimum balance cushion required before a workspace may begin more work. The reserve helps avoid starting a task that will immediately run out of credit.

**Data flow**: It takes a database connection, workspace ID, and reserve amount. It updates the existing workspace balance row with the new reserve and returns true if a row was updated, false if the workspace has no balance row.

**Call relations**: Admin or operator billing code calls this when changing the required cushion. read_headroom later reads this reserve so admission gates can decide whether to allow more model work.

*Call graph*: 2 external calls (execute, update).


### `core/src/ufo/runtime/billing/accounting.py`

`domain_logic` · `cross-cutting: admission, turn execution, usage export, reporting`

This file is the money counter and spending guard for the runtime. It writes usage into a ledger, which is a database table that acts like a store receipt: each row says what was used, by whom, when, and what it cost in micro-USD, meaning millionths of a dollar. It covers normal turn model tokens, model calls made from inside a sandbox, background jobs, egress requests, generated images, and videos.

A key idea is that a turn may be retried or resumed. The code is careful not to charge twice for the same work. For normal turn tokens, each run attempt has one cumulative row; if a retry reports the same or smaller usage, nothing new is charged, and if it reports more, only the added cost is debited.

The file also exports frozen usage deltas for outside billing systems, so retries send the same billable event instead of recomputing it. It checks rolling spend caps for a workspace, member, or agent, deciding whether work may continue, park, or be rejected. Finally, it produces rollup reports by dimension, member, agent, model, origin, and day. Balance checks are separate from spend caps: a workspace can start only if its prepaid balance has enough headroom, but a running turn is allowed until it would cross zero, to avoid stop-start thrashing.

#### Function details

##### `OffTurnSpendRefused.__init__`  (lines 61–63)

```
def __init__(self, outcome: SpendOutcome, message: str) -> None
```

**Purpose**: Creates an exception for a model call that happens outside a normal turn and is blocked by spend rules. It keeps both the decision, such as park or reject, and the human-facing refusal message.

**Data flow**: It receives a spend outcome and a message. It stores the outcome on the exception object, then passes the message to the normal error machinery so callers can display or log it.

**Call relations**: ModelAccess.turn raises or prepares this when off-turn model access is not allowed. The exception carries the decision back to the caller instead of losing it as a plain error string.

*Call graph*: called by 1 (turn).


##### `applicable_caps_absent`  (lines 66–72)

```
def applicable_caps_absent(workspace_id: UUID, member_id: UUID | None, agent_id: UUID) -> bool
```

**Purpose**: Quickly answers whether the system recently learned that no spend caps apply to a workspace/member/agent combination. This avoids a database check on every round in the common case where no caps are configured.

**Data flow**: It receives a workspace id, optional member id, and agent id. It looks up that exact triple in a small in-memory cache and compares its expiry time with the current monotonic clock. It returns true only if the cache entry is still fresh.

**Call relations**: Other admission or per-round code can use this as a fast path before asking SpendEvaluator to read caps from the database. SpendEvaluator.decide fills this cache through _note_absent_caps when it finds no applicable caps.

*Call graph*: 1 external calls (monotonic).


##### `_note_absent_caps`  (lines 75–84)

```
def _note_absent_caps(key: tuple[UUID, UUID | None, UUID | None]) -> None
```

**Purpose**: Remembers for a few seconds that a specific workspace/member/agent combination has no spend caps. This is a small performance helper, not a source of truth.

**Data flow**: It receives the cache key. It checks the current time, removes expired entries if the cache is already large, then stores a new expiry time for that key.

**Call relations**: SpendEvaluator.decide calls this after a database read proves no caps apply. Later, applicable_caps_absent can skip a fresh database trip until the short time-to-live expires.

*Call graph*: called by 1 (decide); 1 external calls (monotonic).


##### `_total_tokens`  (lines 87–95)

```
def _total_tokens(usage: Usage) -> int
```

**Purpose**: Adds all token categories in a usage record into one total token count. This gives billing rows a single amount while still preserving the detailed split elsewhere.

**Data flow**: It receives a Usage object containing input, output, cache-read, and cache-write token counts. It sums those fields and returns the integer total.

**Call relations**: record_turn_usage, record_workspace_usage, and record_sandbox_tokens call this before deciding whether anything billable happened and before writing ledger amounts.

*Call graph*: called by 3 (record_sandbox_tokens, record_turn_usage, record_workspace_usage).


##### `_prompt_tokens`  (lines 98–107)

```
def _prompt_tokens(usage: Usage) -> int
```

**Purpose**: Counts the tokens the model read as its prompt, including cached prompt tokens. This is used to calculate how much of a prompt came from cache in later reports.

**Data flow**: It receives a Usage object. It adds input tokens and all cache read/write prompt-token fields, leaving out output tokens, and returns that prompt total.

**Call relations**: The token-recording functions store this value beside the ledger total. read_turn_cost and reporting can then compute cache share from the ledger instead of from temporary in-memory state.

*Call graph*: called by 3 (record_sandbox_tokens, record_turn_usage, record_workspace_usage).


##### `workspace_owns_the_key`  (lines 110–133)

```
async def workspace_owns_the_key(connection: AsyncConnection, workspace_id: UUID, key_slot: str | None) -> bool
```

**Purpose**: Checks whether a workspace has stored its own provider credential for a given key slot. This matters because work paid directly through the workspace’s own provider key should not also be charged against platform balance.

**Data flow**: It receives a database connection, workspace id, and optional key slot name. If there is no slot, it returns false. Otherwise it asks the credential table whether that workspace has a row for the slot and returns the yes/no result.

**Call relations**: BalanceGate._workspace_serves_itself calls this while deciding whether a low-balance workspace can still start work that will not debit platform funds.

*Call graph*: called by 1 (_workspace_serves_itself); 3 external calls (exists, scalar, select).


##### `record_turn_usage`  (lines 136–271)

```
async def record_turn_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, usage: Usage, attempt: str='', pricing: Pricing=CORE_PRICING, byok: bool=False) -> None
```

**Purpose**: Records and charges the model-token usage for one turn attempt. It is designed to survive retries without double-billing and to charge only the growth in cumulative usage.

**Data flow**: It receives the database connection, workspace and turn ids, model name, usage counters, attempt id, pricing table, and whether the workspace used its own key. It totals and prices the usage, finds the deterministic ledger row for this attempt, and either inserts it or safely advances it. It debits balance only for the new platform-funded cost; own-key usage is priced but not debited.

**Call relations**: Turn execution calls this as usage snapshots become available. It relies on _total_tokens, _prompt_tokens, Pricing.micro_usd, ledger_id_for, and debit; later read_turn_cost, reports, caps, and exports all read the ledger rows it creates.

*Call graph*: calls 3 internal fn (micro_usd, _prompt_tokens, _total_tokens); 7 external calls (__init__, execute, insert, select, update, debit, ledger_id_for).


##### `read_turn_cost`  (lines 285–315)

```
async def read_turn_cost(connection: AsyncConnection, turn_id: UUID, dimension: str) -> TurnCost | None
```

**Purpose**: Reads the final cost summary for a turn under one usage dimension, such as normal tokens or sandbox tokens. It combines multiple attempts so a parked and resumed turn shows its true total.

**Data flow**: It receives a connection, turn id, and dimension name. It sums matching ledger rows for amount, cost, prompt tokens, and cache-read tokens. It returns a TurnCost object, or none if nothing was billed.

**Call relations**: Terminal turn reporting uses this after record_turn_usage or record_sandbox_tokens have written ledger rows. It turns raw ledger data into a compact user-facing cost summary.

*Call graph*: 3 external calls (__init__, execute, select).


##### `record_workspace_usage`  (lines 318–367)

```
async def record_workspace_usage(connection: AsyncConnection, workspace_id: UUID, model: str, usage: Usage, pricing: Pricing=CORE_PRICING, byok: bool=False) -> None
```

**Purpose**: Bills a model call made by a background workspace job rather than by a conversation turn. It keeps this spend in workspace totals but out of member and agent attribution.

**Data flow**: It receives a workspace id, model, usage counters, pricing table, and own-key flag. It totals and prices the usage, debits the workspace unless the workspace paid with its own key, and inserts a fresh ledger row with no turn id.

**Call relations**: Background jobs call this when they make real provider calls. It uses the same token and pricing helpers as turn billing, so workspace reports and workspace-scoped caps see the spend consistently.

*Call graph*: calls 3 internal fn (micro_usd, _prompt_tokens, _total_tokens); 4 external calls (execute, insert, debit, uuid4).


##### `record_egress_request`  (lines 370–399)

```
async def record_egress_request(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, amount: int=1) -> None
```

**Purpose**: Counts sandbox network egress requests for a turn. These requests are tracked but priced at zero, so they do not debit balance or move spend caps by cost.

**Data flow**: It receives a workspace id, turn id, and request count. It builds the per-turn egress ledger id, then inserts the row or atomically adds to the existing count.

**Call relations**: The sandbox egress proxy calls this when turn-bound sandbox traffic is flushed. It writes a separate ledger dimension so network request counts do not get mixed with token charges.

*Call graph*: 2 external calls (execute, ledger_id_for).


##### `record_probe_egress_request`  (lines 402–428)

```
async def record_probe_egress_request(connection: AsyncConnection, workspace_id: UUID, amount: int=1) -> None
```

**Purpose**: Counts sandbox network egress made by an off-turn probe. Like turn egress, it is metered as a count with zero dollar cost.

**Data flow**: It receives a workspace id and count. It inserts a fresh ledger row with no turn id, the egress dimension, the count, and zero price.

**Call relations**: Probe or proxy code calls this for network activity not tied to a turn. Workspace-level reports can see it, while member and agent reports ignore it because there is no turn to join through.

*Call graph*: 3 external calls (execute, insert, uuid4).


##### `record_sandbox_tokens`  (lines 431–507)

```
async def record_sandbox_tokens(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, usage: Usage, pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: Records model-token usage from model calls made inside a sandbox through the egress proxy. This is separate from the host turn loop’s own model calls, so the two sources add together without double-counting.

**Data flow**: It receives workspace and turn ids, model, usage counters, and pricing. It totals and prices the tokens, debits the workspace, and inserts or atomically increments one sandbox-token ledger row for the turn.

**Call relations**: The egress proxy calls this when sandbox code uses a model. It uses _total_tokens, _prompt_tokens, Pricing.micro_usd, debit, and ledger_id_for, and its rows are later included by read_turn_cost and token rollups.

*Call graph*: calls 3 internal fn (micro_usd, _prompt_tokens, _total_tokens); 3 external calls (execute, debit, ledger_id_for).


##### `record_image_usage`  (lines 510–529)

```
async def record_image_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, images: int, micro_usd: int) -> None
```

**Purpose**: Records generated-image usage for a turn. The caller supplies the price because image providers may charge by units that are not ordinary text tokens.

**Data flow**: It receives workspace and turn ids, model name, image count, and cost. It passes those values to the shared media writer using the images dimension.

**Call relations**: Provider extensions call this after image generation. It delegates the actual ledger insert and balance debit to _record_media_usage.

*Call graph*: calls 1 internal fn (_record_media_usage).


##### `record_video_usage`  (lines 532–546)

```
async def record_video_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, videos: int, micro_usd: int) -> None
```

**Purpose**: Records generated-video usage for a turn. It mirrors image billing, but under the videos dimension.

**Data flow**: It receives workspace and turn ids, model name, video count, and cost. It forwards them to the shared media writer using the videos dimension.

**Call relations**: Video provider code calls this after a generation completes. _record_media_usage does the shared work of debiting and accumulating the per-turn ledger row.

*Call graph*: calls 1 internal fn (_record_media_usage).


##### `_record_media_usage`  (lines 549–590)

```
async def _record_media_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, dimension: str, model: str, amount: int, micro_usd: int) -> None
```

**Purpose**: Shared writer for image and video billing. It charges the workspace and accumulates counts and cost on one ledger row per turn and media type.

**Data flow**: It receives a dimension, amount, model, and cost. It creates the deterministic ledger id, debits the workspace by the new cost, then inserts or updates the ledger row by adding the new amount and cost.

**Call relations**: record_image_usage and record_video_usage call this so media billing behaves the same for both. Reports and spend checks later read the rows it writes.

*Call graph*: called by 2 (record_image_usage, record_video_usage); 3 external calls (execute, debit, ledger_id_for).


##### `mint_usage_exports`  (lines 615–738)

```
async def mint_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, floor: datetime, key_slot_for: Callable[[str], str | None]) -> None
```

**Purpose**: Creates frozen usage-export intents for an external billing consumer. An intent is a saved promise to send a particular ledger growth amount, so retries can resend exactly the same data.

**Data flow**: It receives a workspace, consumer name, backfill floor time, and a function that maps models to key slots. It finds ledger rows whose amount has grown since the consumer’s last export, skips zero-priced egress, waits for accumulating turn-bound dimensions to settle, decides the own-key flag, and inserts export rows without duplicating existing ones.

**Call relations**: A usage-export job calls this before reading pending exports. It reads ledger and credential tables, then writes ledger_export rows that read_pending_usage_exports will deliver.

*Call graph*: 5 external calls (now, timedelta, execute, or_, select).


##### `read_pending_usage_exports`  (lines 741–786)

```
async def read_pending_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, limit: int) -> tuple[UsageExport, ...]
```

**Purpose**: Reads frozen usage deltas that have not yet been acknowledged by an external billing consumer. It returns exactly what should be sent next.

**Data flow**: It receives workspace id, consumer name, and a limit. It joins pending export rows to their ledger rows, calculates the delta amount and delta price, and returns UsageExport objects in stable order.

**Call relations**: The export sender calls this after mint_usage_exports. If delivery fails before acknowledgment, the same rows remain pending and this function returns them again.

*Call graph*: 3 external calls (__init__, execute, select).


##### `ack_usage_exports`  (lines 789–813)

```
async def ack_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: Marks exported usage deltas as acknowledged after the outside billing system accepts them. This removes them from the pending queue.

**Data flow**: It receives the workspace, consumer, and the UsageExport objects that were accepted. It builds matching keys from ledger id and starting amount, then updates those export rows with an acknowledgment timestamp.

**Call relations**: The export sender calls this only after a successful external API response. If the process crashes before this call, read_pending_usage_exports will resend the same frozen intents.

*Call graph*: 3 external calls (execute, or_, update).


##### `metered_workspaces`  (lines 816–819)

```
def metered_workspaces() -> WorkspaceCandidates
```

**Purpose**: Finds candidate workspaces for usage-export work. It includes every workspace that has ever had a ledger row.

**Data flow**: It builds a database query for distinct workspace ids from the ledger and wraps it in the project’s WorkspaceCandidates helper.

**Call relations**: Scheduled export orchestration uses this to decide which workspaces to scan. The later per-workspace export steps cheaply do nothing if there is no pending usage.

*Call graph*: 1 external calls (owner_candidates).


##### `SpendEvaluator.decide`  (lines 856–871)

```
async def decide(self, connection: AsyncConnection, pending_micro_usd: int) -> SpendDecision
```

**Purpose**: Decides whether pending spend is allowed under configured spend caps. The result is allow, park, or reject, with a message if work cannot continue.

**Data flow**: It receives a database connection and the cost about to be added. It reads applicable caps, caches the no-cap case, sums recent usage for each cap, compares usage plus pending cost with each limit, and returns a SpendDecision.

**Call relations**: Admission and mid-turn spending checks call this when cap enforcement is needed. It coordinates _applicable_caps, _used_micro_usd, _message, and _note_absent_caps.

*Call graph*: calls 4 internal fn (_applicable_caps, _message, _used_micro_usd, _note_absent_caps); 1 external calls (__init__).


##### `SpendEvaluator._applicable_caps`  (lines 873–901)

```
async def _applicable_caps(self, connection: AsyncConnection) -> tuple[SpendCap, ...]
```

**Purpose**: Reads the spend caps that apply to this workspace, and optionally its member and agent. It turns database rows into small SpendCap objects.

**Data flow**: It receives a connection and uses the evaluator’s stored workspace, member, and agent ids. It selects workspace-wide caps plus matching member and agent caps, converts each row, and returns them as a tuple.

**Call relations**: SpendEvaluator.decide calls this first. Its result determines whether the rest of the cap check is needed at all.

*Call graph*: called by 1 (decide); 4 external calls (__init__, execute, or_, select).


##### `SpendEvaluator._used_micro_usd`  (lines 903–929)

```
async def _used_micro_usd(self, connection: AsyncConnection, cap: SpendCap) -> int
```

**Purpose**: Calculates how much money has already been spent inside one cap’s rolling time window. A rolling window means “the last N seconds from now.”

**Data flow**: It receives a connection and one SpendCap. It computes the cutoff time, builds the right ledger query for workspace, member, or agent scope, sums priced micro-USD, and returns the integer total.

**Call relations**: SpendEvaluator.decide calls this for every applicable cap. The returned usage is compared with the cap limit plus the pending new spend.

*Call graph*: called by 1 (decide); 4 external calls (now, timedelta, execute, select).


##### `SpendEvaluator._message`  (lines 931–942)

```
def _message(self, outcome: SpendOutcome, breaches: list[SpendCap]) -> str
```

**Purpose**: Builds the human-readable explanation for a breached spend cap. It names the tightest cap and whether the turn is parked or declined.

**Data flow**: It receives the chosen outcome and breached caps. It picks the cap with the lowest limit, converts micro-USD to dollars, and returns a sentence for the user.

**Call relations**: SpendEvaluator.decide calls this only when at least one cap is breached. The message becomes part of the SpendDecision returned to admission or turn-running code.

*Call graph*: called by 1 (decide).


##### `_token_sum`  (lines 1048–1056)

```
def _token_sum() -> sa.ColumnElement[int]
```

**Purpose**: Builds a reusable database expression for summing token counts. It counts only token-like dimensions, not images, videos, or egress requests.

**Data flow**: It takes no runtime input. It returns a SQL expression that sums ledger amount when the dimension is normal tokens or sandbox tokens, otherwise adds zero.

**Call relations**: SpendRollup.read, SpendRollup._by_origin, and _usage_details use this expression so all reports agree on what “tokens” means.

*Call graph*: called by 3 (_by_origin, read, _usage_details); 1 external calls (case).


##### `_token_cost_sum`  (lines 1059–1071)

```
def _token_cost_sum() -> sa.ColumnElement[int]
```

**Purpose**: Builds a reusable database expression for summing the cost of token-like usage only. It separates token spend from other billed dimensions.

**Data flow**: It takes no runtime input. It returns a SQL expression that sums priced micro-USD for normal tokens and sandbox tokens, otherwise adds zero.

**Call relations**: SpendRollup.read, SpendRollup._by_origin, and _usage_details call this when producing token-cost totals for reports.

*Call graph*: called by 3 (_by_origin, read, _usage_details); 1 external calls (case).


##### `_usage_details`  (lines 1074–1198)

```
async def _usage_details(connection: AsyncConnection, source: sa.FromClause, scope: sa.ColumnElement[bool], cutoff: datetime | None, now: datetime) -> UsageDetails
```

**Purpose**: Builds the shared usage summary used by workspace and member reports. It includes selected-window totals, all-time totals, daily history, model breakdowns, execution breakdowns, and previous-period token count.

**Data flow**: It receives a connection, a table join to read from, a scope filter, an optional cutoff, and the current time. It runs several aggregate queries, fills missing days with zeroes, normalizes the first-use timestamp, and returns a UsageDetails object.

**Call relations**: SpendRollup.read and SpendRollup.read_member both call this so their usage sections follow the same rules. It relies on _token_sum and _token_cost_sum for consistent token accounting.

*Call graph*: calls 2 internal fn (_token_cost_sum, _token_sum); called by 2 (read, read_member); 9 external calls (__init__, __init__, __init__, __init__, fromisoformat, date, timedelta, execute, select).


##### `SpendRollup.read`  (lines 1209–1313)

```
async def read(self, connection: AsyncConnection, window_seconds: int | None) -> SpendReport
```

**Purpose**: Reads a full workspace spending report for either all time or a selected recent window. It answers where the money went by dimension, member, agent, origin, price table, day, model, and execution type.

**Data flow**: It receives a connection and optional window length. It computes a cutoff, sums ledger spend for the workspace, runs grouped queries for each breakdown, asks _by_origin for source-conversation totals, asks _usage_details for the usage section, and returns a SpendReport.

**Call relations**: Billing dashboards or admin APIs call this to show workspace-wide spending. It pulls together many small report helpers into one report object.

*Call graph*: calls 4 internal fn (_by_origin, _token_cost_sum, _token_sum, _usage_details); 8 external calls (__init__, __init__, __init__, __init__, now, timedelta, execute, select).


##### `SpendRollup._by_origin`  (lines 1315–1380)

```
async def _by_origin(self, connection: AsyncConnection, window: sa.ColumnElement[bool]) -> tuple[OriginTotal, ...]
```

**Purpose**: Groups token spend by the original surface or conversation that started it. This matters because subagents may spend money in private child conversations, but users want that cost shown under the visible place that launched the work.

**Data flow**: It receives a connection and a ledger window filter. It builds a recursive database query that walks from spending turns up through parent turns to the root conversation, joins that root to its surface label, sums token amount and token cost, and returns OriginTotal rows.

**Call relations**: SpendRollup.read calls this for the workspace report. It uses _token_sum and _token_cost_sum so origin totals match the rest of the token reporting.

*Call graph*: calls 2 internal fn (_token_cost_sum, _token_sum); called by 1 (read); 4 external calls (__init__, desc, execute, select).


##### `SpendRollup.read_member`  (lines 1382–1440)

```
async def read_member(self, connection: AsyncConnection, member_id: UUID, window_seconds: int | None) -> MemberSpendReport
```

**Purpose**: Reads spending and usage for one member only, plus that member’s own spend caps. It avoids naming other members or agents.

**Data flow**: It receives a connection, member id, and optional window length. It joins ledger rows through turns to conversations for that member, sums spending by dimension, reads member-scoped caps, builds shared usage details, and returns a MemberSpendReport.

**Call relations**: Member-facing billing views or APIs call this. It reuses _usage_details for consistent totals, while applying a member-specific scope.

*Call graph*: calls 1 internal fn (_usage_details); 7 external calls (__init__, __init__, __init__, now, timedelta, execute, select).


##### `BalanceGate.admits`  (lines 1472–1510)

```
async def admits(self, connection: AsyncConnection, agent_id: UUID | None=None, key_slot_for: Callable[[str], str | None] | None=None, turn_id: UUID | None=None, model: str | None=None) -> SpendDecisi
```

**Purpose**: Decides whether a turn may start, be folded into live work, or resume from being parked based on prepaid balance. Starting requires reserve headroom unless the work will use the workspace’s own provider key and the balance is still above zero.

**Data flow**: It receives a connection plus optional agent, model, key-slot resolver, and turn id. It reads balance headroom, allows self-host/no-balance cases, rejects if below the start line, checks whether a turn already debited money, and may allow own-key work above zero. It returns a SpendDecision.

**Call relations**: Turn admission and resume logic call this before beginning work. It uses read_headroom, _workspace_serves_itself, _turn_has_debited, and balance_refusal_message to turn raw balance state into an allow or reject decision.

*Call graph*: calls 2 internal fn (_turn_has_debited, _workspace_serves_itself); 4 external calls (__init__, _forget_absent_balance, balance_refusal_message, read_headroom).


##### `BalanceGate._workspace_serves_itself`  (lines 1512–1534)

```
async def _workspace_serves_itself(self, connection: AsyncConnection, agent_id: UUID | None, key_slot_for: Callable[[str], str | None] | None, model: str | None=None) -> bool
```

**Purpose**: Determines whether the model for this work will be served using the workspace’s own stored key. If so, token rounds do not need platform balance, though other costs may still debit.

**Data flow**: It receives a connection, optional agent id, key-slot resolver, and optional model. If the model is missing, it reads the agent’s model from the database. It maps the model to a key slot, checks whether the workspace owns that key, and returns true or false.

**Call relations**: BalanceGate.admits calls this when balance is below the normal reserve but still above zero. It delegates the credential check to workspace_owns_the_key.

*Call graph*: calls 1 internal fn (workspace_owns_the_key); called by 1 (admits); 2 external calls (execute, select).


##### `BalanceGate.sustains`  (lines 1536–1557)

```
async def sustains(self, connection: AsyncConnection, pending_micro_usd: int, turn_id: UUID | None=None) -> SpendDecision
```

**Purpose**: Decides whether a running turn may take another round of work. Unlike admission, continuation stops at zero balance, not at the reserve line, to avoid repeatedly parking a turn after every small charge.

**Data flow**: It receives a connection, pending cost, and optional turn id. It reads balance headroom, subtracts the pending cost, allows if the result stays above the grace-adjusted zero line, rejects if the pending work or earlier turn work has actually debited balance, and otherwise allows zero-cost continuation.

**Call relations**: The turn loop calls this before spending another round. It uses read_headroom, _turn_has_debited, and balance_refusal_message to decide whether to continue or stop.

*Call graph*: calls 1 internal fn (_turn_has_debited); 4 external calls (__init__, _forget_absent_balance, balance_refusal_message, read_headroom).


##### `BalanceGate._turn_has_debited`  (lines 1559–1582)

```
async def _turn_has_debited(self, connection: AsyncConnection, turn_id: UUID | None) -> bool
```

**Purpose**: Checks whether a turn has already taken any money from the workspace balance. This is different from being priced: own-key token usage may have a price but debit nothing.

**Data flow**: It receives a connection and optional turn id. If there is no turn id, it returns false. Otherwise it looks for any ledger row for that turn with debited micro-USD greater than zero and returns whether one exists.

**Call relations**: BalanceGate.admits and BalanceGate.sustains call this to avoid endlessly resuming a turn that has already charged money while the balance is too low.

*Call graph*: called by 2 (admits, sustains); 2 external calls (execute, select).


### External billing integration
The Metronome extension exports usage, connects prepaid billing to Stripe, and exposes admin billing controls and status pages.

### `extensions/metronome/ufo_ext_metronome.py`

`domain_logic` · `background jobs, billing tool calls, and billing page requests`

This extension is the bridge between UFO’s internal accounting and two outside services. Metronome receives settled usage events so humans can see and rate what each workspace used. Stripe stores payment methods, opens the billing portal, and charges cards when automatic balance refills are needed.

The usage part works like a careful mailroom. UFO core creates frozen usage export records after ledger entries settle. `UsageShipper` reads a workspace’s pending records in batches, checks that Metronome has a customer alias for the workspace, sends the events, and only then marks the exports as acknowledged. Each event has a deterministic transaction id, so if the job crashes and sends the same batch again, Metronome can recognize the duplicate instead of counting it twice.

The billing part is deliberately prepaid. Metronome rates and records usage, but it must not collect money. Money comes from Stripe top-ups into UFO’s balance system. Admins use the `manage_billing` tool to ask for status, get a Stripe Customer Portal link, or configure automatic refills. A separate top-up job watches workspaces that are low on balance and charges the saved card, with safeguards against repeated declined charges.

The file also registers its jobs, route, tool, prompt instructions, and one BYOK credential slot in `manifest()`, which is how the host system discovers the extension.

#### Function details

##### `StripeError.__init__`  (lines 187–189)

```
def __init__(self, message: str, status: int=0) -> None
```

**Purpose**: Creates an error object for failed Stripe calls and keeps the HTTP status code attached. The status code matters because the caller treats a declined card differently from a system failure.

**Data flow**: It receives an error message and an optional numeric status code. It stores the message in the normal exception and saves the status on the object, so later code can inspect why Stripe failed.

**Call relations**: The shared Stripe helper `_stripe` creates this error whenever Stripe returns a non-success response. Top-up charging code then looks at the saved status to decide whether to wait, retry, or report a card decline.

*Call graph*: called by 1 (_stripe).


##### `UsageShipper.run`  (lines 219–245)

```
async def run(self) -> None
```

**Purpose**: Sends one workspace’s settled usage records to Metronome safely. It is built so a crash or retry does not lose usage or bill the same usage twice.

**Data flow**: It first reads the Metronome bearer token from the environment, then finds the workspace’s fixed backfill floor. It repeatedly asks core for a batch of pending usage exports, warns if they are getting too old, confirms the Metronome customer alias, converts the exports into Metronome events, sends them, logs success, and finally acknowledges the exports. It stops when there is no more work or the final batch is smaller than the batch size.

**Call relations**: The scheduled `_ship` job calls this method. During the run it relies on `_floor` for the earliest shippable date, `_note_usage_aging_out` for warnings, `_ensure_metronome_customer` so events attach to a real Metronome customer, `_events` to build the payload, and `_ingest` to send the HTTP request.

*Call graph*: calls 6 internal fn (_events, _floor, _note_usage_aging_out, _ensure_metronome_customer, _ingest, _require_env); 1 external calls (log).


##### `UsageShipper._floor`  (lines 247–257)

```
async def _floor(self) -> datetime
```

**Purpose**: Finds or creates the fixed earliest time from which this workspace’s usage may be shipped. This prevents the first run from trying to backfill usage older than Metronome can accept.

**Data flow**: It reads a stored timestamp from the workspace extension store. If none exists, it creates one set to seven days before now, stores it, and returns it. If one exists, it parses and returns that same stored time.

**Call relations**: Only `UsageShipper.run` calls this before reading pending exports. The result limits what core will mint as export intents, so the usage shipper has a stable starting boundary.

*Call graph*: called by 1 (run); 3 external calls (fromisoformat, now, timedelta).


##### `UsageShipper._note_usage_aging_out`  (lines 259–280)

```
def _note_usage_aging_out(self, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: Warns operators when unsent usage is older than Metronome’s backdating window. This is an alert that delayed shipping may soon turn into revenue or reporting loss.

**Data flow**: It receives a batch of usage exports, finds the oldest event time, compares it with the allowed backfill window, and emits a warning if the oldest item is already too old. It does not change the exports.

**Call relations**: `UsageShipper.run` calls this before sending each batch. It uses `_rfc3339` to print the timestamp in a standard readable format and then hands control back to the normal shipping flow.

*Call graph*: calls 1 internal fn (_rfc3339); called by 1 (run); 3 external calls (now, timedelta, warn).


##### `UsageShipper._events`  (lines 282–301)

```
def _events(self, exports: tuple[UsageExport, ...]) -> list[dict[str, object]]
```

**Purpose**: Turns UFO usage export records into the event format Metronome expects. This is where internal ledger facts become external usage events.

**Data flow**: It receives a tuple of `UsageExport` records. For each one, it creates a dictionary with a deterministic transaction id, workspace customer id, timestamp, model, amount, price details, turn id, and BYOK label. It returns the list of event dictionaries.

**Call relations**: `UsageShipper.run` calls this just before `_ingest`. The resulting payload is sent unchanged to Metronome, so it is the key translation step between UFO accounting and Metronome ingestion.

*Call graph*: calls 1 internal fn (_rfc3339); called by 1 (run).


##### `_ship`  (lines 304–305)

```
async def _ship(ctx: ExtensionContext) -> None
```

**Purpose**: Small scheduled-job entry point for usage shipping. It adapts the generic job callback shape to the `UsageShipper` class.

**Data flow**: It receives an extension context for one workspace, creates a `UsageShipper` with that context and the configured test or production transport, and runs it. It returns nothing except completion or an error.

**Call relations**: The extension manifest registers `_ship` as the handler for the usage shipping job. The actual work is delegated to `UsageShipper.run`.

*Call graph*: 1 external calls (__init__).


##### `BillingConfig.from_env`  (lines 322–337)

```
def from_env(cls) -> 'BillingConfig'
```

**Purpose**: Loads the Stripe settings needed for billing actions and automatic top-ups. It fails early if the deployment is missing required Stripe configuration.

**Data flow**: It reads the Stripe secret key and Stripe billing portal configuration id from environment variables. If either is missing, it raises an error naming all missing settings. If both exist, it returns a validated `BillingConfig` object.

**Call relations**: Billing tool calls, billing page reads, and top-up jobs call this before contacting Stripe. This keeps half-configured deployments from creating partial billing state.


##### `_billing_record`  (lines 350–352)

```
async def _billing_record(ctx: ExtensionContext) -> BillingRecord | None
```

**Purpose**: Reads the saved Stripe customer id for a workspace, if one has been created. This is the local pointer from UFO’s workspace to Stripe’s customer record.

**Data flow**: It looks up the billing record in the extension store. If nothing is stored, it returns `None`; otherwise it validates the stored data as a `BillingRecord` and returns it.

**Call relations**: Billing status, billing portal creation, autopay setup, the billing page, and the balance top-up job all call this when they need to know whether the workspace already has a Stripe customer.

*Call graph*: called by 5 (run, _billing_autopay, _billing_portal, _billing_projection, _billing_status).


##### `manage_billing`  (lines 375–384)

```
async def manage_billing(ctx: ToolContext, args: ManageBillingInput) -> ToolResult
```

**Purpose**: Implements the admin-facing chat action for billing. It chooses between reading billing status, opening the Stripe portal, or setting automatic refills.

**Data flow**: It receives the tool context and structured arguments from the agent. It first proves the speaker is an admin, loads Stripe billing configuration, then dispatches based on `operation`. It returns a tool result containing JSON text for the agent to report.

**Call relations**: This is the handler registered in `MANAGE_BILLING_TOOL_DEF`. It delegates permission checking to `_admin_billing` and the three operations to `_billing_status`, `_billing_portal`, and `_billing_autopay`.

*Call graph*: calls 4 internal fn (_admin_billing, _billing_autopay, _billing_portal, _billing_status).


##### `_billing_autopay`  (lines 387–418)

```
async def _billing_autopay(ext: ExtensionContext, config: BillingConfig, args: ManageBillingInput) -> ToolResult
```

**Purpose**: Sets or stops automatic balance refills for a workspace. It makes sure a card is already saved before promising future off-session charges.

**Data flow**: It receives the extension context, Stripe config, and requested refill settings. If both dollar fields are omitted, it disables autopay. If both are provided, it verifies there is a Stripe customer and a default payment method, converts dollars to micro-dollars, saves the top-up rule in core balance storage, clears old refusal state, increments an attempt marker, logs the change, and returns the saved values.

**Call relations**: `manage_billing` calls this for the `autopay` operation. It reads billing state through `_billing_record`, checks Stripe through `_default_payment_method`, writes the rule through `set_auto_topup`, and formats the answer with `_text_result`.

*Call graph*: calls 4 internal fn (transaction, _billing_record, _default_payment_method, _text_result); called by 1 (manage_billing); 2 external calls (set_auto_topup, log).


##### `_admin_billing`  (lines 421–427)

```
async def _admin_billing(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Checks that the billing tool is being used by a real speaking workspace admin. Billing actions are sensitive, so non-admins and anonymous tool calls are rejected.

**Data flow**: It receives a tool context, checks that there is a speaking member, asks the context whether that member is an admin, and returns the extension context if allowed. If the speaker is missing or not an admin, it raises an error.

**Call relations**: `manage_billing` calls this before every billing operation. It is the gatekeeper that protects status, portal creation, and autopay changes.

*Call graph*: calls 1 internal fn (speaker_is_admin); called by 1 (manage_billing); 1 external calls (__init__).


##### `_billing_status`  (lines 430–454)

```
async def _billing_status(ext: ExtensionContext, config: BillingConfig) -> ToolResult
```

**Purpose**: Reports the workspace’s prepaid balance and whether Stripe has a card on file. It answers the admin’s basic question: can this workspace pay for more usage?

**Data flow**: It reads the balance from UFO core, reads any stored Stripe customer record, asks Stripe whether that customer has a default payment method, and returns JSON text containing card presence, balance, reserve, granted credit, and charged credit.

**Call relations**: `manage_billing` calls this for the `status` operation. It uses `_billing_record` and `_default_payment_method` for Stripe-related information and `_text_result` to package the result for the chat tool.

*Call graph*: calls 4 internal fn (transaction, _billing_record, _default_payment_method, _text_result); called by 1 (manage_billing); 1 external calls (read_balance).


##### `_billing_portal`  (lines 457–479)

```
async def _billing_portal(ext: ExtensionContext, config: BillingConfig) -> ToolResult
```

**Purpose**: Creates a fresh Stripe Customer Portal link for an admin. The portal lets the admin save or change a payment method and view billing details in Stripe.

**Data flow**: It checks whether the workspace already has a billing record. If not, it creates or retrieves the workspace’s Stripe customer and stores the customer id. It then asks Stripe for a portal session URL that returns to UFO’s billing screen, logs the event, and returns the URL and customer id.

**Call relations**: `manage_billing` calls this for the `portal` operation. It may call `_stripe_customer` to establish the Stripe customer, always calls `_portal_session` to get the link, and returns the result through `_text_result`.

*Call graph*: calls 5 internal fn (home_url, _billing_record, _portal_session, _stripe_customer, _text_result); called by 1 (manage_billing); 2 external calls (__init__, log).


##### `_text_result`  (lines 482–483)

```
def _text_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: Wraps a small JSON payload as text for a chat tool response. This gives the agent a simple machine-readable result to quote or link.

**Data flow**: It receives a dictionary, serializes it to a JSON string, puts that string in a `TextContent` object, and returns a `ToolResult` containing it.

**Call relations**: The billing operation helpers call this after they have gathered or changed billing information. It is the common final packaging step for `manage_billing` responses.

*Call graph*: called by 3 (_billing_autopay, _billing_portal, _billing_status); 3 external calls (__init__, __init__, dumps).


##### `_require_env`  (lines 497–501)

```
def _require_env(name: str) -> str
```

**Purpose**: Reads a required environment variable and raises a clear error if it is missing. It is used for settings that the extension cannot safely operate without.

**Data flow**: It receives the environment variable name, looks it up, and returns the value if present. If the value is empty or missing, it raises a runtime error naming the missing setting.

**Call relations**: `UsageShipper.run` calls this before touching pending usage exports, so an unconfigured Metronome deployment fails loudly instead of minting export records it cannot send.

*Call graph*: called by 1 (run).


##### `_stripe_customer`  (lines 504–521)

```
async def _stripe_customer(config: BillingConfig, workspace_id: UUID, transport: httpx.AsyncBaseTransport | None) -> str
```

**Purpose**: Creates or reuses the one Stripe Customer for a workspace. It uses a stable idempotency key so retries do not create duplicate customers.

**Data flow**: It receives billing config, a workspace id, and an optional HTTP transport. It posts a customer creation request to Stripe with workspace metadata and a deterministic idempotency key, then extracts and returns the Stripe customer id.

**Call relations**: `_billing_portal` calls this when a workspace has no stored billing record yet. It sends the HTTP request through `_stripe` and checks the returned id with `_as_str`.

*Call graph*: calls 2 internal fn (_as_str, _stripe); called by 1 (_billing_portal).


##### `_portal_session`  (lines 524–549)

```
async def _portal_session(config: BillingConfig, customer_id: str, flow: str | None, transport: httpx.AsyncBaseTransport | None, return_url: str | None=None) -> str
```

**Purpose**: Asks Stripe for a short-lived Customer Portal URL. This URL is what an admin opens to save a card or view billing details.

**Data flow**: It receives Stripe config, a customer id, an optional narrowed portal flow, an optional transport, and an optional return URL. It builds the Stripe form data, posts it, extracts the returned URL, and returns that URL as a string.

**Call relations**: `_billing_portal` calls this after it knows the Stripe customer id. It relies on `_stripe` for the HTTP call and `_as_str` to reject malformed Stripe responses.

*Call graph*: calls 2 internal fn (_as_str, _stripe); called by 1 (_billing_portal).


##### `_default_payment_method`  (lines 552–564)

```
async def _default_payment_method(config: BillingConfig, customer_id: str, transport: httpx.AsyncBaseTransport | None) -> str | None
```

**Purpose**: Finds the customer’s default payment method in Stripe, if one exists. This is the extension’s source of truth for whether a card is saved.

**Data flow**: It receives Stripe config, a customer id, and an optional transport. It fetches the Stripe customer object and looks inside `invoice_settings.default_payment_method`. It returns that payment method id or `None`.

**Call relations**: Autopay setup, top-up charging, billing status, and card display all call this. It uses `_stripe` for the Stripe request and gives callers the payment method id they need before charging or reporting card presence.

*Call graph*: calls 1 internal fn (_stripe); called by 4 (run, _billing_autopay, _billing_status, _card_on_file).


##### `_card_on_file`  (lines 579–594)

```
async def _card_on_file(config: BillingConfig, customer_id: str, transport: httpx.AsyncBaseTransport | None) -> CardOnFile | None
```

**Purpose**: Reads the recognizable card details for the saved default payment method. It returns the brand and last four digits so an admin can identify the card.

**Data flow**: It first calls `_default_payment_method`. If there is no default method, it returns `None`. If there is one, it fetches that payment method from Stripe and, when it is a card, returns a `CardOnFile` with brand and last four digits.

**Call relations**: `_billing_projection` calls this for the billing page. It builds on `_default_payment_method` and `_stripe`, keeping display-only card details separate from the charging path.

*Call graph*: calls 2 internal fn (_default_payment_method, _stripe); called by 1 (_billing_projection); 1 external calls (__init__).


##### `_stripe`  (lines 597–618)

```
async def _stripe(config: BillingConfig, method: str, path: str, transport: httpx.AsyncBaseTransport | None, data: dict[str, str] | None=None, idempotency_key: str | None=None) -> dict[str, object]
```

**Purpose**: Central helper for Stripe HTTP requests. It applies authentication, the pinned Stripe API version, timeout, optional idempotency key, and consistent error handling.

**Data flow**: It receives a billing config, HTTP method, Stripe API path, optional transport, optional form data, and optional idempotency key. It sends the request to Stripe, raises `StripeError` if Stripe does not return success, and otherwise returns the parsed JSON response.

**Call relations**: All Stripe-specific helpers use this: creating customers, creating portal sessions, reading customers, reading payment methods, and creating payment intents. It is the single place where Stripe network calls are made.

*Call graph*: calls 1 internal fn (__init__); called by 5 (_charge, _card_on_file, _default_payment_method, _portal_session, _stripe_customer); 1 external calls (AsyncClient).


##### `_as_str`  (lines 621–625)

```
def _as_str(value: object, field: str) -> str
```

**Purpose**: Checks that a provider response field is a non-empty string. It prevents later code from silently using missing or malformed ids and URLs.

**Data flow**: It receives an arbitrary value and a human-readable field name. If the value is a non-empty string, it returns it. Otherwise it raises a `ValueError` naming the missing field.

**Call relations**: `_stripe_customer` uses this for Stripe customer ids, and `_portal_session` uses it for portal URLs. It is a small safety check after external API calls.

*Call graph*: called by 2 (_portal_session, _stripe_customer).


##### `_ensure_metronome_customer`  (lines 628–679)

```
async def _ensure_metronome_customer(ctx: ExtensionContext, token: str, transport: httpx.AsyncBaseTransport | None) -> None
```

**Purpose**: Makes sure Metronome has a live customer whose ingest alias is this workspace id. Without this, Metronome may accept usage events but attach them to no visible customer.

**Data flow**: It receives the extension context, Metronome bearer token, and optional transport. It looks up a customer by workspace alias. If one exists, it returns. If not, it tries to create a customer with that alias, handles conflicts by re-reading, treats permission failures as blocking errors, and logs customer creation on success.

**Call relations**: `UsageShipper.run` calls this once before sending a batch. It calls `_customer_by_alias` for the read side, then uses direct HTTP calls for creation, ensuring usage is not acknowledged until the customer alias is confirmed.

*Call graph*: calls 1 internal fn (_customer_by_alias); called by 1 (run); 4 external calls (__init__, __init__, AsyncClient, log).


##### `_customer_by_alias`  (lines 689–706)

```
async def _customer_by_alias(http: httpx.AsyncClient, headers: dict[str, str], alias: str) -> str | None
```

**Purpose**: Looks up the live Metronome customer that owns a given ingest alias. It returns the customer id if visible to this token.

**Data flow**: It receives an HTTP client, request headers, and an alias string. It asks Metronome for customers matching the alias. Permission failures become `_CustomerScopeDenied`, other failed responses become `MetronomeError`, and a successful response returns the first customer id or `None`.

**Call relations**: `_ensure_metronome_customer` uses this before creating a customer and again after a conflict. This lets the shipper distinguish a harmless race from an alias held somewhere it cannot read.

*Call graph*: called by 1 (_ensure_metronome_customer); 3 external calls (__init__, __init__, get).


##### `BalanceTopup.run`  (lines 725–808)

```
async def run(self) -> None
```

**Purpose**: Attempts to refill one workspace’s prepaid balance from its saved Stripe card when core says the balance is too low. It avoids charging repeatedly after declines or while another charge is still being decided.

**Data flow**: It reads the workspace’s configured auto-top-up rule. If none exists, it stops. It skips recent cardless retries, loads Stripe config, finds the billing record and default payment method, reads current balance and retry markers, respects the decline waiting period, then calls `_charge`. If the charge succeeds, it credits the workspace balance and marks the top-up verified; if the card is declined, it records a refusal and advances the attempt marker.

**Call relations**: The scheduled `_top_up` job calls this. It uses `_billing_record` and `_default_payment_method` to find a chargeable card, `_charge` to ask Stripe for money, and core balance functions to record the credit exactly once.

*Call graph*: calls 3 internal fn (_charge, _billing_record, _default_payment_method); 9 external calls (fromisoformat, now, count_charge, credit, mark_topup_verified, read_auto_topup, read_balance, log, warn).


##### `BalanceTopup._charge`  (lines 810–867)

```
async def _charge(self, config: BillingConfig, customer_id: str, payment_method: str, wanted: AutoTopup, workspace_id: UUID, attempt: str) -> str | None
```

**Purpose**: Creates and confirms a Stripe PaymentIntent for an automatic balance refill. It returns the payment intent id only when the money has actually moved.

**Data flow**: It receives Stripe config, customer id, payment method id, desired top-up amount, workspace id, and attempt key. It converts micro-dollars to cents, posts a confirmed off-session payment intent to Stripe with an idempotency key, and interprets the result. A successful payment returns the intent id; a card decline returns `None`; an in-flight duplicate raises `_ChargeInFlight`; other Stripe failures are re-raised.

**Call relations**: `BalanceTopup.run` calls this after deciding a top-up should be attempted. It sends the actual Stripe request through `_stripe` and reports declines with warnings so the caller can update retry state.

*Call graph*: calls 1 internal fn (_stripe); called by 1 (run); 2 external calls (__init__, warn).


##### `_top_up`  (lines 870–871)

```
async def _top_up(ctx: ExtensionContext) -> None
```

**Purpose**: Small scheduled-job entry point for automatic balance refills. It adapts the job system’s callback into a `BalanceTopup` run.

**Data flow**: It receives an extension context, creates a `BalanceTopup` with that context and configured billing transport, and runs it. It returns only completion or an error.

**Call relations**: The manifest registers `_top_up` as the handler for the balance top-up job. The real refill decision-making happens in `BalanceTopup.run`.

*Call graph*: 1 external calls (__init__).


##### `_ingest`  (lines 874–882)

```
async def _ingest(token: str, events: list[dict[str, object]], transport: httpx.AsyncBaseTransport | None) -> None
```

**Purpose**: Sends a batch of usage events to Metronome’s ingest endpoint. It treats any non-success response as a failed shipment.

**Data flow**: It receives a bearer token, a list of event dictionaries, and an optional transport. It posts the events to Metronome with authorization. If Metronome accepts the request, it returns nothing; otherwise it raises `MetronomeError` with the status and body.

**Call relations**: `UsageShipper.run` calls this after building events and confirming the customer alias. Exports are acknowledged only after `_ingest` succeeds.

*Call graph*: called by 1 (run); 2 external calls (__init__, AsyncClient).


##### `_rfc3339`  (lines 885–887)

```
def _rfc3339(moment: datetime) -> str
```

**Purpose**: Formats a timestamp as an ISO/RFC3339-style string with timezone information. This keeps timestamps suitable for logs and Metronome events.

**Data flow**: It receives a `datetime`. If the value has no timezone, it treats it as UTC; otherwise it leaves the timezone intact. It returns the timestamp’s ISO string.

**Call relations**: `UsageShipper._events` uses this for event timestamps, and `_note_usage_aging_out` uses it for warning logs.

*Call graph*: called by 2 (_events, _note_usage_aging_out); 1 external calls (replace).


##### `_billing_request_workspace`  (lines 896–901)

```
def _billing_request_workspace(request: Request) -> UUID | None
```

**Purpose**: Identifies which workspace a billing page request belongs to by reading the session cookie. If no workspace can be proven, the route should not be served.

**Data flow**: It receives an HTTP request, reads the session cookie, asks the bearer-token helper for the workspace claim, and returns a workspace UUID or `None`.

**Call relations**: The manifest registers this as the route identifier for the billing page. The host uses its answer to bind the request to the right workspace before calling `_billing_projection`.

*Call graph*: 1 external calls (workspace_claim).


##### `_billing_projection`  (lines 904–971)

```
async def _billing_projection(ext: ExtensionContext, request: Request) -> Response
```

**Purpose**: Builds the JSON data for the billing status page. This page stays available even when the workspace cannot run chat turns because it is out of credit.

**Data flow**: It verifies the session cookie for the bound workspace, checks that the email belongs to an admin member, reads balance headroom, balance totals, autopay settings, and recent purchases from core, then optionally reads card details from Stripe. It returns a JSON response showing whether the workspace is limited, its balances, card status, autopay settings, and recent purchase history. If Stripe card lookup fails, it still returns the local balance data and marks the card as unread.

**Call relations**: The billing route calls this after `_billing_request_workspace` identifies the workspace. It uses `_billing_record` and `_card_on_file` for Stripe display details, and several balance and seat helpers for local authorization and billing state.

*Call graph*: calls 3 internal fn (transaction, _billing_record, _card_on_file); 9 external calls (configured_auto_topup, read_balance, read_headroom, recent_purchases, verify_token, JSONResponse, warn, member_by_email, member_is_admin).


##### `manifest`  (lines 974–1012)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the UFO host. It declares the tool, scheduled jobs, HTTP route, prompt instructions, and credential slot that make the extension active.

**Data flow**: It creates and returns a `Manifest` containing the extension name and version, the `manage_billing` tool definition, the usage shipping job, the top-up job, the billing route, the billing prompt section, and the Anthropic BYOK credential slot.

**Call relations**: The host system calls `manifest()` when loading the extension. The objects it returns cause `_ship`, `_top_up`, `_billing_projection`, and `manage_billing` to be called later at the right times.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, metered_workspaces, topping_up_workspaces).

# Observability, accounting, spend limits, and billing integration  `stage-18` (cross-cutting infrastructure)

This stage is shared behind-the-scenes support that keeps the system measurable, billable, and safe from runaway spending. It is not one step in the main chat loop; it watches and records work across the whole system, especially when the agent uses models or serves a workspace.

The observability toolbox records what happened as logs, metrics, and traces. A trace is a step-by-step record of a request. Before details leave the process, it strips sensitive data so operators can debug problems without exposing private content.

The accounting center turns model usage, such as tokens, into dollars. It writes those costs into a ledger, checks spend limits, and prepares exports for outside billing systems. This is the cash register and spending guardrail.

The seats code decides who in a workspace the agent may answer, based on grants, automatic rules, and billing limits. The Metronome integration connects those seats and usage numbers to the external billing service, and gives workspace owners chat tools to grant, revoke, and inspect seats. Together, these pieces make usage visible, chargeable, and controlled.

## Files in this stage

### Metronome integration
Connects workspace usage and seat counts to the external billing and metering service.

### `extensions/metronome/ufo_ext_metronome.py`

`domain_logic` · `scheduled billing jobs and chat tool handling`

This extension is the bridge between UFO’s internal records and Metronome’s billing records. Without it, settled model usage would stay inside UFO and would not appear in Metronome for invoicing or visibility, and daily seat counts would not be reported. It also gives the chat agent a safe way to ask the owner before adding paid seats.

The file has two scheduled shipping jobs. The usage shipper reads already-settled usage exports from the workspace, turns each one into a Metronome event, sends a batch, and only then marks those exports as acknowledged. This order matters: if the process crashes after sending but before marking, the same event is sent again later with the same transaction ID, so Metronome can ignore the duplicate. Think of it like mailing a letter with a tracking number: if you are unsure it arrived, you resend the same tracked letter, not a different one.

The seat shipper sends one daily snapshot of how many seats are in use. Separately, the seat approval job finds unseated members when the included allowance is full and asks the workspace owner in chat whether to grant a paid overage seat.

Finally, the file declares the extension manifest: its tools, scheduled jobs, prompt guidance, and one credential slot for a workspace-owned Anthropic key.

#### Function details

##### `UsageShipper.run`  (lines 121–136)

```
async def run(self) -> None
```

**Purpose**: Sends a workspace’s pending settled usage records to Metronome in batches. It is careful to mark records as shipped only after Metronome accepts them, so retries do not lose usage.

**Data flow**: It starts by reading the Metronome bearer token from the environment and finding this workspace’s fixed backfill floor. It repeatedly asks the context for pending usage exports after that floor, converts them into Metronome event dictionaries, sends them to Metronome, logs the successful shipment, and then acknowledges those exports so they will not be sent again unless the previous send was not confirmed.

**Call relations**: This is the main worker behind the usage shipping job. It relies on _require_token for credentials, _floor to decide how far back it may look, _events to shape internal usage records into Metronome events, and _ingest to do the HTTP delivery.

*Call graph*: calls 4 internal fn (_events, _floor, _ingest, _require_token); 1 external calls (log).


##### `UsageShipper._floor`  (lines 138–148)

```
async def _floor(self) -> datetime
```

**Purpose**: Finds or creates the oldest timestamp this workspace is allowed to ship usage from. This prevents a first run from accidentally sending unlimited old history while still allowing delayed records after that first setup to be shipped later.

**Data flow**: It reads a stored value named ship_floor from the workspace store. If none exists, it creates one by subtracting the configured backfill window from the current UTC time, saves it, and returns it; if one already exists, it parses the saved timestamp and returns that same fixed point.

**Call relations**: UsageShipper.run calls this before asking for pending exports. The returned time becomes the lower bound for the export query, so the rest of the usage shipping loop only sees records that are eligible to leave UFO.

*Call graph*: called by 1 (run); 3 external calls (fromisoformat, now, timedelta).


##### `UsageShipper._events`  (lines 150–169)

```
def _events(self, exports: tuple[UsageExport, ...]) -> list[dict[str, object]]
```

**Purpose**: Turns UFO usage export records into the exact event shape Metronome expects. It preserves billing details such as model, amount, price information, and whether the workspace brought its own provider key.

**Data flow**: It receives a tuple of UsageExport objects. For each export, it builds a dictionary with a stable transaction ID, the workspace customer ID, an event type, a timestamp formatted for web APIs, and string properties describing the usage. The output is a list of event dictionaries ready to POST to Metronome.

**Call relations**: UsageShipper.run calls this immediately before _ingest. It uses _rfc3339 to format timestamps so Metronome receives consistent date strings.

*Call graph*: calls 1 internal fn (_rfc3339); called by 1 (run).


##### `_ship`  (lines 172–173)

```
async def _ship(ctx: ExtensionContext) -> None
```

**Purpose**: Acts as the scheduled job entry function for usage shipping. It creates a UsageShipper for the workspace context supplied by the job runner.

**Data flow**: It receives an ExtensionContext for one workspace. It constructs a UsageShipper with that context and the optional test transport, then runs the shipper so pending usage can be delivered.

**Call relations**: The manifest registers this function as the handler for the usage shipping job. Its job is mostly handoff: it connects the scheduler’s context to the UsageShipper object that does the real work.

*Call graph*: 1 external calls (__init__).


##### `SeatShipper.run`  (lines 186–202)

```
async def run(self) -> None
```

**Purpose**: Sends Metronome one daily snapshot of a workspace’s seat count. It also initializes the workspace’s default seat limit and included-seat allowance if those values have not been set yet.

**Data flow**: It reads the Metronome token, checks whether today’s seat snapshot has already been shipped, and stops if so. Otherwise it opens a transaction, ensures default seat settings exist, reads the current seat snapshot, sends one event to Metronome, logs the result, and records today’s date so the snapshot is not sent again that day.

**Call relations**: This is the main worker behind the daily seat shipping job. It uses _require_token for credentials, Seats to read and initialize seat state, _event to build the Metronome payload, and _ingest to send it.

*Call graph*: calls 3 internal fn (_event, _ingest, _require_token); 3 external calls (__init__, now, log).


##### `SeatShipper._event`  (lines 204–215)

```
def _event(self, snapshot: SeatSnapshot, today: str) -> dict[str, object]
```

**Purpose**: Builds the Metronome event for one day’s seat snapshot. The event ID includes the workspace and date so retries for the same day are treated as the same billing event.

**Data flow**: It takes a SeatSnapshot and today’s date string. It reads the workspace ID, formats the current timestamp, and returns a dictionary containing the transaction ID, customer ID, seat event type, and properties for current seated count and seat limit.

**Call relations**: SeatShipper.run calls this after reading the current seat snapshot and before calling _ingest. It uses _rfc3339 to turn the current time into the timestamp string sent to Metronome.

*Call graph*: calls 1 internal fn (_rfc3339); called by 1 (run); 1 external calls (now).


##### `_ship_seats`  (lines 218–219)

```
async def _ship_seats(ctx: ExtensionContext) -> None
```

**Purpose**: Acts as the scheduled job entry function for daily seat-count shipping. It wraps the SeatShipper class so the job runner has a simple function to call.

**Data flow**: It receives an ExtensionContext for a workspace, creates a SeatShipper with that context and optional test transport, and runs it to send today’s snapshot if needed.

**Call relations**: The manifest registers this as the handler for the seat shipping job. It hands control to SeatShipper, which performs the actual seat snapshot work.

*Call graph*: 1 external calls (__init__).


##### `SeatApprovals.run`  (lines 235–260)

```
async def run(self) -> None
```

**Purpose**: Finds members who do not have seats when the included seat allowance is already full, then asks the workspace owner in chat whether to grant each one a paid seat. This keeps paid seat additions owner-approved instead of automatic.

**Data flow**: It reads the current seat snapshot inside a transaction. If there is no included-seat setting or there are still included seats available, it stops. Otherwise it looks at unseated members, skips anyone already asked about, finds the owner’s conversation, sends an internal prompt asking for a grant-or-decline decision, and then records a marker so that member is not asked about again.

**Call relations**: This is the worker behind the seat approval scheduled job. It uses Seats to inspect members and owner_conversation to find where to send the owner prompt; once it invokes the conversation, later chat behavior can lead to grant_seat being called.

*Call graph*: 3 external calls (__init__, now, owner_conversation).


##### `_ask_seat_approvals`  (lines 263–264)

```
async def _ask_seat_approvals(ctx: ExtensionContext) -> None
```

**Purpose**: Acts as the scheduled job entry function for seat approval prompts. It creates a SeatApprovals worker for the workspace.

**Data flow**: It receives an ExtensionContext, constructs SeatApprovals with it, and runs the approval scan so any newly waiting unseated members can be brought to the owner’s attention.

**Call relations**: The manifest registers this as the handler for the frequent seat approval job. It is a thin bridge from the scheduler to SeatApprovals.run.

*Call graph*: 1 external calls (__init__).


##### `grant_seat`  (lines 279–285)

```
async def grant_seat(ctx: ToolContext, args: GrantSeatInput) -> ToolResult
```

**Purpose**: Chat tool that lets the workspace owner give a seat to a member by email. This allows the agent to act on an owner’s approval without exposing a separate admin screen.

**Data flow**: It receives the tool context and an email argument. It first checks that the speaker is allowed to change seats, then opens a transaction, grants the seat through the Seats object, reads the updated snapshot, and returns that snapshot as a JSON tool result.

**Call relations**: The agent can call this tool after the owner approves a seat request. It depends on _owner_seats for the owner-only permission check and _snapshot_result to turn the updated seat state into a response.

*Call graph*: calls 2 internal fn (_owner_seats, _snapshot_result).


##### `revoke_seat`  (lines 288–298)

```
async def revoke_seat(ctx: ToolContext, args: RevokeSeatInput) -> ToolResult
```

**Purpose**: Chat tool that lets the workspace owner remove a member’s seat by email. It also marks that member as already decided so the approval job will not immediately ask about them again.

**Data flow**: It receives the tool context and an email argument. It checks owner permission, opens a transaction, revokes the seat, reads the updated snapshot, writes an approval marker for that email with the current time, and returns the updated seat snapshot as JSON.

**Call relations**: The agent can call this when the owner asks to unseat someone. Like grant_seat, it uses _owner_seats for authorization and _snapshot_result for the reply; the extra marker affects future SeatApprovals.run scans.

*Call graph*: calls 2 internal fn (_owner_seats, _snapshot_result); 1 external calls (now).


##### `list_seats`  (lines 301–305)

```
async def list_seats(ctx: ToolContext, args: ListSeatsInput) -> ToolResult
```

**Purpose**: Chat tool that shows the current seat setup and member seating state. It is useful when the owner wants to know the limit, included allowance, overage count, or who currently has a seat.

**Data flow**: It receives the tool context, opens a transaction through the extension context, reads the workspace’s seat snapshot, and converts that snapshot into a JSON tool result.

**Call relations**: The manifest exposes this as a non-mutating tool for the agent. It uses Seats to read the current state and _snapshot_result to format the answer consistently with the grant and revoke tools.

*Call graph*: calls 1 internal fn (_snapshot_result); 1 external calls (__init__).


##### `_owner_seats`  (lines 308–313)

```
async def _owner_seats(ctx: ToolContext) -> Seats
```

**Purpose**: Checks that a seat-changing tool is being used by a real speaking member who is the workspace owner. If the check passes, it returns the Seats helper for that workspace.

**Data flow**: It receives a ToolContext. It verifies that there is a speaker member ID, asks the context whether that speaker is the owner, raises an error if not, and otherwise returns a Seats object tied to the current workspace.

**Call relations**: grant_seat and revoke_seat call this before making any seat change. This keeps the permission rule in one place so both tools enforce the same owner-only policy.

*Call graph*: calls 1 internal fn (speaker_is_owner); called by 2 (grant_seat, revoke_seat); 1 external calls (__init__).


##### `_snapshot_result`  (lines 316–329)

```
def _snapshot_result(snapshot: SeatSnapshot) -> ToolResult
```

**Purpose**: Formats a seat snapshot into the JSON text returned by the chat tools. It gives the agent and user a compact summary of limits, overage seats, and each member’s status.

**Data flow**: It receives a SeatSnapshot. It builds a plain dictionary containing the seat limit, included seats, billed overage count, total seated count, and member entries, serializes that dictionary to JSON text, and wraps it in a ToolResult.

**Call relations**: grant_seat, revoke_seat, and list_seats all call this after reading or changing seat state. It provides one shared response format for all seat-related tools.

*Call graph*: called by 3 (grant_seat, list_seats, revoke_seat); 3 external calls (__init__, __init__, dumps).


##### `_require_token`  (lines 354–360)

```
def _require_token() -> str
```

**Purpose**: Reads the Metronome API token from the environment and fails loudly if it is missing. This prevents silent billing gaps caused by trying to ship events without credentials.

**Data flow**: It looks up METRONOME_BEARER_TOKEN in the process environment. If a value is present, it returns that token; if not, it raises a runtime error explaining that the extension requires it.

**Call relations**: UsageShipper.run and SeatShipper.run call this before trying to contact Metronome. The token it returns is passed on to _ingest as the bearer credential for the HTTP request.

*Call graph*: called by 2 (run, run).


##### `_ingest`  (lines 363–371)

```
async def _ingest(token: str, events: list[dict[str, object]], transport: httpx.AsyncBaseTransport | None) -> None
```

**Purpose**: Sends prepared event batches to Metronome’s ingest API. It turns any non-success response into a clear error so the scheduled job can retry later.

**Data flow**: It receives a bearer token, a list of event dictionaries, and an optional HTTP transport used mainly for tests. It opens an asynchronous HTTP client, POSTs the events to Metronome with the authorization header, and returns nothing if the response succeeds; if Metronome returns an error status, it raises MetronomeError with the status and response body.

**Call relations**: Both UsageShipper.run and SeatShipper.run hand their prepared events to this function. It is the shared network doorway to Metronome, while the callers decide what events should be sent and when to acknowledge local records.

*Call graph*: called by 2 (run, run); 2 external calls (__init__, AsyncClient).


##### `_rfc3339`  (lines 374–376)

```
def _rfc3339(moment: datetime) -> str
```

**Purpose**: Formats a Python datetime as a timestamp string suitable for Metronome events. If the datetime has no timezone, it treats it as UTC so the event time is not ambiguous.

**Data flow**: It receives a datetime value. If the value already has timezone information, it uses it as-is; otherwise it adds the UTC timezone. It then returns the ISO-style timestamp string.

**Call relations**: UsageShipper._events and SeatShipper._event call this while building event payloads. It keeps timestamp formatting consistent across usage and seat events.

*Call graph*: called by 2 (_event, _events); 1 external calls (replace).


##### `manifest`  (lines 379–415)

```
def manifest() -> Manifest
```

**Purpose**: Declares what this extension contributes to UFO: tools, scheduled jobs, prompt text, and a credential slot. The host system uses this declaration to load and run the extension.

**Data flow**: It constructs and returns a Manifest object containing the extension name and version, the three seat tools, the usage shipping job, the seat snapshot job, the seat approval job, a prompt section explaining seat behavior to the agent, and an Anthropic API key credential slot for bring-your-own-key billing behavior.

**Call relations**: This is how the surrounding extension system discovers the file’s capabilities. The jobs it registers point to _ship, _ship_seats, and _ask_seat_approvals, while the tools point to grant_seat, revoke_seat, and list_seats.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, metered_workspaces, member_workspaces).


### Accounting and spend controls
Calculates model costs, records ledger usage, exports billing data, and enforces spend limits.

### `core/src/ufo/accounting.py`

`domain_logic` · `request handling, background billing export, and spend reporting`

This file treats usage like entries in a checkbook. Every model call, sandbox model call, or sandbox network request becomes a row in a ledger table. Token rows also get priced in micro-dollars, where one micro-dollar is one millionth of a dollar, so the system can keep exact integer totals instead of using error-prone floating-point money math.

The file starts with model price tables and helpers that calculate the cost of a usage record. It stamps each priced row with a digest, which is a stable fingerprint of the price table used at the time. That matters when prices change later: old spending can still be audited against the exact rates that created it.

It then provides the write paths for different kinds of usage: normal turn usage, background workspace usage, sandbox egress request counts, and sandbox model tokens. Some rows are insert-once; others accumulate safely so repeated sandbox calls do not overwrite each other.

A second part freezes ledger growth into export records for external billing consumers. This makes retries safe: if delivery fails, the same frozen usage delta can be sent again without changing its meaning.

Finally, the file enforces and reports spending caps. It can decide whether a turn should run, park, or be rejected, and it can roll up recent spending by workspace, member, agent, usage type, and price-table version.

#### Function details

##### `applicable_caps_absent`  (lines 46–52)

```
def applicable_caps_absent(workspace_id: UUID, member_id: UUID | None, agent_id: UUID) -> bool
```

**Purpose**: This is a quick shortcut for the common case where no spend caps apply. It answers whether the system recently checked this exact workspace, member, and agent combination and found no cap, so it can skip an unnecessary database read for a few seconds.

**Data flow**: It receives a workspace ID, optional member ID, and agent ID. It looks up that exact triple in a small in-memory cache and compares the stored expiry time with the current monotonic clock, which is a clock used for measuring elapsed time. It returns true only if the cache entry exists and has not expired; it changes nothing.

**Call relations**: Other admission code can call this before doing a full spend-cap check. It does not ask the database itself; it relies on SpendEvaluator.decide having previously recorded that no caps applied through _note_absent_caps.

*Call graph*: 1 external calls (monotonic).


##### `_note_absent_caps`  (lines 55–64)

```
def _note_absent_caps(key: tuple[UUID, UUID | None, UUID]) -> None
```

**Purpose**: This remembers, briefly, that a specific workspace/member/agent combination has no applicable spend caps. It keeps no-cap deployments fast without making cap changes permanently invisible.

**Data flow**: It receives a cache key made from workspace ID, member ID, and agent ID. It checks the current time, removes expired cache entries if the cache is already large, and stores a new expiry time a few seconds in the future. It returns nothing but updates the in-memory cache.

**Call relations**: SpendEvaluator.decide calls this after it has actually queried the database and found no caps. Later, applicable_caps_absent can use that note to let nearby checks skip the database round trip.

*Call graph*: called by 1 (decide); 1 external calls (monotonic).


##### `price_digest`  (lines 93–113)

```
def price_digest(prices: Mapping[str, ModelPrice] | None=None) -> str
```

**Purpose**: This creates a stable fingerprint of a model price table. The fingerprint is written beside billed usage so old ledger rows can be tied back to the exact prices that were used.

**Data flow**: It takes an optional mapping of model names to prices. If none is provided, it uses the built-in price table. It sorts the table, turns it into compact JSON text, hashes that text with SHA-256, and returns a string such as a version stamp.

**Call relations**: pricing_with calls this when building a Pricing object. The resulting digest is later stored by record_turn_usage, record_workspace_usage, and record_sandbox_tokens through the Pricing object.

*Call graph*: called by 1 (pricing_with); 2 external calls (sha256, dumps).


##### `usage_priced_micro_usd`  (lines 119–138)

```
def usage_priced_micro_usd(model: str, usage: Usage, prices: Mapping[str, ModelPrice] | None=None) -> int
```

**Purpose**: This calculates the dollar cost of one model usage record, in micro-dollars. It is the main arithmetic rule that turns input, output, cache-read, and cache-write tokens into a billed amount.

**Data flow**: It receives a model name, a Usage object with token counts, and optionally a price table. It finds the model’s rates, multiplies each token count by the matching rate, adds the results, and divides by one million tokens to get micro-dollars. If the model is unknown, it logs a warning and returns zero instead of failing the billing write.

**Call relations**: Pricing.micro_usd calls this so all billing writes use the same pricing math. The warning behavior is important because billing happens at the end of a turn, where raising an error could trap the user in a retry loop.

*Call graph*: called by 1 (micro_usd); 1 external calls (log).


##### `Pricing.micro_usd`  (lines 152–153)

```
def micro_usd(self, model: str, usage: Usage) -> int
```

**Purpose**: This is the convenient method for pricing usage with the specific price table currently in force. Callers use it when writing ledger rows so the cost and the price-table digest stay paired.

**Data flow**: It receives a model name and a Usage record. It forwards them, along with this Pricing object’s price table, to usage_priced_micro_usd and returns the calculated micro-dollar cost. It does not change any state.

**Call relations**: record_turn_usage, record_workspace_usage, and record_sandbox_tokens call this while preparing ledger rows. It is the small bridge between the stored Pricing object and the shared pricing calculation.

*Call graph*: calls 1 internal fn (usage_priced_micro_usd); called by 3 (record_sandbox_tokens, record_turn_usage, record_workspace_usage).


##### `pricing_with`  (lines 156–161)

```
def pricing_with(contributed: Mapping[str, ModelPrice]) -> Pricing
```

**Purpose**: This builds a Pricing object from the built-in model prices plus extra model prices contributed by providers. It lets extensions add or override model prices while still producing one clear price-table digest.

**Data flow**: It receives a mapping of contributed model prices. It overlays those entries on top of the core table, calculates a digest for the merged table, and returns a new Pricing object containing both the merged prices and digest.

**Call relations**: This calls price_digest to make the version stamp. It is also used to create the core-only default pricing object when no provider contributions are present.

*Call graph*: calls 1 internal fn (price_digest); 1 external calls (__init__).


##### `record_turn_usage`  (lines 167–207)

```
async def record_turn_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, usage: Usage, attempt: str='', pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: This writes the model-token bill for a turn into the ledger, once per run attempt. It prevents double billing when the same attempt is replayed, while still billing separate resumed attempts that caused real provider charges.

**Data flow**: It receives a database connection, workspace ID, turn ID, model name, Usage counts, an attempt identifier, and Pricing. It totals the tokens; if the total is zero, it does nothing. Otherwise it builds a stable ledger ID, checks whether that row already exists, and inserts a ledger row with token amount, priced cost, model, price digest, and timestamps if it has not already been billed.

**Call relations**: Turn-completion code calls this when a model run has produced usage. It uses Pricing.micro_usd for the cost and ledger_id_for for a repeatable row ID, then hands the final write to the database through SQLAlchemy.

*Call graph*: calls 1 internal fn (micro_usd); 4 external calls (execute, insert, select, ledger_id_for).


##### `read_turn_cost`  (lines 210–228)

```
async def read_turn_cost(connection: AsyncConnection, turn_id: UUID) -> tuple[int, int, str] | None
```

**Purpose**: This reads the total billed token cost for a turn. It is useful when a turn may have been parked and resumed, because each attempt can have its own ledger row.

**Data flow**: It receives a database connection and a turn ID. It queries token-dimension ledger rows for that turn, sums token amount and micro-dollar cost, and keeps a model value. It returns those totals as a tuple, or returns null if nothing was billed.

**Call relations**: Code that needs to show or record the final cost of a turn can call this after billing rows have been written. It reads the ledger only; it does not create or update anything.

*Call graph*: 2 external calls (execute, select).


##### `record_workspace_usage`  (lines 231–267)

```
async def record_workspace_usage(connection: AsyncConnection, workspace_id: UUID, model: str, usage: Usage, pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: This bills a model call made by a background job to the workspace rather than to a specific turn. It makes sure workspace-level spend and caps include those background costs.

**Data flow**: It receives a database connection, workspace ID, model name, Usage counts, and Pricing. It totals the tokens and exits if there are none. Otherwise it creates a fresh ledger ID and inserts a token ledger row with no turn ID, priced cost, model, digest, and timestamps.

**Call relations**: Background job code calls this after a metered model call completes outside a normal turn. It uses Pricing.micro_usd like turn billing does, but because there is no turn ID, later member and agent reports do not attribute this spend to a person or agent.

*Call graph*: calls 1 internal fn (micro_usd); 3 external calls (execute, insert, uuid4).


##### `record_egress_request`  (lines 270–299)

```
async def record_egress_request(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID) -> None
```

**Purpose**: This counts one sandbox network egress request in the ledger. It records usage volume without adding any dollar charge or affecting spend caps based on priced model tokens.

**Data flow**: It receives a database connection, workspace ID, and turn ID. It builds a stable ledger ID for the egress dimension, then inserts a row with amount one or, if the row already exists, atomically increases the amount by one. It writes zero priced cost and updates timestamps.

**Call relations**: The sandbox egress proxy calls this when a sandbox makes an outgoing request. It uses database upsert behavior, meaning insert-or-update, so concurrent requests for the same turn do not lose counts.

*Call graph*: 2 external calls (execute, ledger_id_for).


##### `record_sandbox_tokens`  (lines 302–353)

```
async def record_sandbox_tokens(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, usage: Usage, pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: This records model tokens used by code running inside the sandbox through the egress proxy. It keeps those costs separate from host-side turn model usage, but still adds them to the ledger and spending totals.

**Data flow**: It receives a database connection, workspace ID, turn ID, model name, Usage counts, and Pricing. It totals tokens and exits if the total is zero. Otherwise it prices the usage, builds a stable sandbox-token ledger ID, and inserts or atomically updates a ledger row by adding both tokens and micro-dollar cost.

**Call relations**: The sandbox model-call path calls this after in-sandbox model usage. It uses Pricing.micro_usd for cost and an upsert so multiple sandbox model calls in one turn accumulate safely in one row.

*Call graph*: calls 1 internal fn (micro_usd); 2 external calls (execute, ledger_id_for).


##### `mint_usage_exports`  (lines 378–483)

```
async def mint_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, floor: datetime, key_slot_for: Callable[[str], str | None]) -> None
```

**Purpose**: This freezes new, unexported ledger growth into delivery records for an external billing consumer. It is what makes usage export safe when jobs retry or when a ledger row grows over time.

**Data flow**: It receives a database connection, workspace ID, consumer name, a lower time bound, and a function that maps model names to provider key slots. It reads stored workspace credential slots, finds ledger rows whose amount has grown beyond what this consumer has already exported, applies settlement rules, and inserts ledger_export rows that freeze the from/to amounts, costs, timing, and whether usage used a workspace-provided key. Duplicate minting attempts collapse because the export key includes consumer, ledger row, and starting amount.

**Call relations**: A background usage-export job calls this before reading pending exports. It reads ledger and credential tables, then writes export intent rows that read_pending_usage_exports can later deliver.

*Call graph*: 5 external calls (now, timedelta, execute, or_, select).


##### `read_pending_usage_exports`  (lines 486–531)

```
async def read_pending_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, limit: int) -> tuple[UsageExport, ...]
```

**Purpose**: This reads frozen usage-export records that have not yet been acknowledged by an outside billing consumer. It returns stable deltas, not freshly recomputed usage.

**Data flow**: It receives a database connection, workspace ID, consumer name, and maximum number of rows. It joins pending ledger_export rows to their ledger rows to get descriptive fields such as dimension, model, price digest, and turn ID. It returns a tuple of UsageExport objects ordered by mint time and identity.

**Call relations**: The export delivery worker calls this after mint_usage_exports. The returned UsageExport objects are what the worker sends to the external billing system.

*Call graph*: 3 external calls (__init__, execute, select).


##### `ack_usage_exports`  (lines 534–558)

```
async def ack_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: This marks exported usage records as acknowledged after the outside consumer accepts them. Once acknowledged, they stop appearing in pending export reads.

**Data flow**: It receives a database connection, workspace ID, consumer name, and a tuple of UsageExport objects. It builds matching conditions from each export’s ledger ID and starting amount, then updates those rows with an acknowledgement time and updated timestamp. It returns nothing.

**Call relations**: The export delivery worker calls this only after a successful external API response. If the process crashes before this call, read_pending_usage_exports will return the same frozen records again for safe retry.

*Call graph*: 3 external calls (execute, or_, update).


##### `metered_workspaces`  (lines 561–564)

```
def metered_workspaces() -> WorkspaceCandidates
```

**Purpose**: This identifies workspaces that might need usage export work. It intentionally returns a broad candidate set: any workspace that has ever written to the ledger.

**Data flow**: It takes no direct inputs. It builds a candidate query for distinct workspace IDs from the ledger table and wraps it in the project’s workspace-candidate helper. It returns that candidate source for a background job to scan.

**Call relations**: Usage-export scheduling code can call this to decide which workspaces to check. The later per-workspace export read is cheap when there is nothing new, so this function does not try to be overly selective.

*Call graph*: 1 external calls (owner_candidates).


##### `SpendEvaluator.decide`  (lines 599–614)

```
async def decide(self, connection: AsyncConnection, pending_micro_usd: int) -> SpendDecision
```

**Purpose**: This decides whether a turn is allowed to proceed under the workspace’s spend caps. The answer can be allow, park, or reject.

**Data flow**: It receives a database connection and a pending micro-dollar amount that is about to be spent. It reads all caps that apply to the workspace, member, and agent. If none exist, it records that fact in the short-lived cache and allows the turn. If caps exist, it sums recent spending for each cap, adds the pending amount, finds breaches, and returns an allow, park, or reject decision with a human-facing message when needed.

**Call relations**: Admission or mid-turn checking code calls this before allowing more work. It orchestrates _applicable_caps, _used_micro_usd, _message, and _note_absent_caps; reject wins over park if any breached cap says to reject.

*Call graph*: calls 4 internal fn (_applicable_caps, _message, _used_micro_usd, _note_absent_caps); 1 external calls (__init__).


##### `SpendEvaluator._applicable_caps`  (lines 616–644)

```
async def _applicable_caps(self, connection: AsyncConnection) -> tuple[SpendCap, ...]
```

**Purpose**: This reads the spend caps that apply to the current workspace, member, and agent. It narrows the cap table down to the rules that could affect this turn.

**Data flow**: It receives a database connection through the SpendEvaluator instance. It queries caps for the workspace where the scope is workspace-wide, matches the member, or matches the agent. It turns the result rows into SpendCap objects and returns them as a tuple.

**Call relations**: SpendEvaluator.decide calls this first. Its result determines whether the no-cap cache is updated or whether spending totals must be checked.

*Call graph*: called by 1 (decide); 4 external calls (__init__, execute, or_, select).


##### `SpendEvaluator._used_micro_usd`  (lines 646–667)

```
async def _used_micro_usd(self, connection: AsyncConnection, cap: SpendCap) -> int
```

**Purpose**: This calculates how much money has already been spent within one cap’s rolling time window. A rolling window means, for example, 'the last 24 hours' counted backward from now.

**Data flow**: It receives a database connection and one SpendCap. It computes the cutoff time from the cap’s window length, then sums priced ledger amounts since that cutoff. For workspace caps it sums by workspace; for member caps it joins through turns and conversations to find that member; for agent caps it joins through turns to find that agent. It returns the integer micro-dollar total.

**Call relations**: SpendEvaluator.decide calls this once for each applicable cap. The returned total is combined with pending spend to determine whether the cap would be exceeded.

*Call graph*: called by 1 (decide); 4 external calls (now, timedelta, execute, select).


##### `SpendEvaluator._message`  (lines 669–680)

```
def _message(self, outcome: SpendOutcome, breaches: list[SpendCap]) -> str
```

**Purpose**: This builds the message shown when a spend cap blocks a turn. It chooses the tightest breached cap and explains whether the turn was parked or declined.

**Data flow**: It receives the final outcome and a list of breached caps. It picks the cap with the smallest dollar limit, converts micro-dollars into dollars for display, and returns a readable sentence. It does not read or write external state.

**Call relations**: SpendEvaluator.decide calls this only after it has found breaches. The returned message is placed into the SpendDecision that the caller can show to a user.

*Call graph*: called by 1 (decide).


##### `SpendRollup.read`  (lines 730–801)

```
async def read(self, connection: AsyncConnection, window_seconds: int) -> SpendReport
```

**Purpose**: This builds a spending report for a workspace over a recent rolling window. It is meant for command-line or web views that need totals and useful breakdowns.

**Data flow**: It receives a database connection and a window length in seconds. It calculates the cutoff time, sums total priced spend for the workspace, then runs grouped queries by ledger dimension, member, agent, and price digest. It returns a SpendReport containing the total and all breakdown lists.

**Call relations**: Spend-reporting UI or CLI code calls this when someone asks to inspect spending. It reads from the ledger and related turn, conversation, member, and agent tables, then packages the results into small report data objects.

*Call graph*: 8 external calls (__init__, __init__, __init__, __init__, now, timedelta, execute, select).


### Observability tooling
Provides tracing, metrics, structured logging, and sensitive-data scrubbing for operational visibility.

### `core/src/ufo/o11y.py`

`util` · `startup and cross-cutting during turns, jobs, and logging`

This file answers a practical question: when something happens inside UFO, how can a person later see what happened, where, and why, without leaking private prompts, tokens, or credentials? It sets up OpenTelemetry, a standard way for applications to send traces, metrics, and logs to an external collector. A trace is like a breadcrumb trail for one unit of work. A metric is a counted number, such as how many turns started. A structured log is a log message with named fields attached, so machines can search it easily.

At startup, `init_o11y` can connect the app to an OTLP endpoint, which is the HTTP address of an OpenTelemetry collector. If no endpoint is given, the system keeps the default no-op behavior and does not export anything. Once enabled, traces, metrics, and logs each get their own export URL.

During normal work, helpers add useful context automatically. For example, `_ambient_scope` reads the current workspace from a shared context, so logs and spans can say which workspace they belong to without every caller passing that value by hand. `turn_span` wraps one durable “turn” in a trace span, and can continue a trace that started before the turn entered a queue.

The file is careful about privacy. Before log fields or span attributes are emitted, `redact_payload` and `redact_value` remove known sensitive keys and simplify values into JSON-safe shapes. Without this file, operators would lose much of the system’s visibility, or risk sending private data to monitoring tools.

#### Function details

##### `init_o11y`  (lines 62–81)

```
def init_o11y(otlp_endpoint: str | None) -> None
```

**Purpose**: Sets up observability export for the whole process. If an OTLP collector address is provided, it installs OpenTelemetry providers for traces, metrics, and logs; if not, it leaves observability in its harmless default state.

**Data flow**: It receives an optional collector endpoint. When the endpoint is missing, it stops immediately. When present, it builds the three signal-specific URLs, creates a shared service identity called `ufo`, wires trace, metric, and log exporters to the collector, and finally connects standard warning logs into the same log pipeline.

**Call relations**: This is the top-level setup function for this file. It asks `_otlp_signal_urls` to build the correct collector URLs, then calls `_bridge_warning_logs` so ordinary Python warnings and errors can also reach the observability backend.

*Call graph*: calls 2 internal fn (_bridge_warning_logs, _otlp_signal_urls); 13 external calls (set_logger_provider, OTLPLogExporter, OTLPMetricExporter, OTLPSpanExporter, set_meter_provider, LoggerProvider, BatchLogRecordProcessor, MeterProvider, PeriodicExportingMetricReader, create (+3 more)).


##### `_bridge_warning_logs`  (lines 84–96)

```
def _bridge_warning_logs(logger_provider: LoggerProvider) -> None
```

**Purpose**: Sends regular Python warning-and-error logs into the OpenTelemetry log pipeline. This matters because not every warning comes from UFO's structured logging helpers; some come from libraries or extension code.

**Data flow**: It receives the OpenTelemetry logger provider created during setup. It builds a logging handler that only accepts warning-level and higher records, filters out UFO's own structured logger and OpenTelemetry's own exporter logs, and attaches that handler to Python's root logger.

**Call relations**: It is called by `init_o11y` after the log provider exists. Its job is to catch important ordinary logging records that would otherwise stay in local Python logging instead of being exported.

*Call graph*: called by 1 (init_o11y); 2 external calls (getLogger, LoggingHandler).


##### `_otlp_signal_urls`  (lines 99–105)

```
def _otlp_signal_urls(otlp_endpoint: str) -> tuple[str, str, str]
```

**Purpose**: Builds the exact HTTP URLs used for trace, metric, and log export. This is needed because the OTLP HTTP exporter expects a complete endpoint and does not add the signal path by itself.

**Data flow**: It receives the base collector endpoint, removes any trailing slash, then appends `v1/traces`, `v1/metrics`, and `v1/logs`. It returns those three finished URLs in trace, metric, log order.

**Call relations**: It is used by `init_o11y` before exporters are created. Without this helper, all exporters might post to the base URL and be rejected by the collector.

*Call graph*: called by 1 (init_o11y).


##### `_ambient_scope`  (lines 108–113)

```
def _ambient_scope() -> dict[str, str]
```

**Purpose**: Finds the current workspace and turns it into metadata for logs and traces. This lets code inside a workspace-scoped operation be tagged automatically without passing the workspace ID through every function call.

**Data flow**: It reads `current_workspace` from shared execution context. If no workspace is active, it returns an empty dictionary. If a workspace is active, it returns a dictionary containing its ID as text.

**Call relations**: It is called by `_emit_log` to tag log records and by `turn_span` to tag trace spans. It acts like a name badge automatically attached to work done inside a workspace.

*Call graph*: called by 2 (_emit_log, turn_span); 1 external calls (get).


##### `current_traceparent`  (lines 116–122)

```
def current_traceparent() -> str | None
```

**Purpose**: Returns the current trace identity in the standard W3C `traceparent` header format. This is useful when work is about to cross a queue or process boundary and later needs to reconnect to the same trace.

**Data flow**: It starts with an empty carrier dictionary, asks the OpenTelemetry trace context propagator to write the current trace information into it, then returns the `traceparent` value if one was produced. If there is no valid active span, it returns `None`.

**Call relations**: This helper is used by callers that need to capture the current trace before handing work off elsewhere. Later, `turn_span` can receive that saved traceparent and continue the same trace.


##### `turn_span`  (lines 126–149)

```
def turn_span(turn_id: UUID, conversation_id: UUID, traceparent: str | None) -> Iterator[Span]
```

**Purpose**: Creates a trace span around one durable turn. A span is one timed section inside a trace, like a chapter in a story of what happened.

**Data flow**: It receives a turn ID, a conversation ID, and optionally a saved `traceparent`. It builds span attributes from those IDs plus the current workspace, redacts them, extracts the parent trace context if one was supplied, starts a server-style span named `turn`, yields that span to the caller's code, and closes it when the caller leaves the context block.

**Call relations**: It calls `_ambient_scope` so the span knows the workspace and `redact_payload` so attributes are safe to export. It uses OpenTelemetry's tracer to create the span. It can continue a trace captured earlier by `current_traceparent`, which keeps queued or spawned turns connected to their origin.

*Call graph*: calls 2 internal fn (_ambient_scope, redact_payload); 2 external calls (get_tracer, cast).


##### `redact_payload`  (lines 152–158)

```
def redact_payload(fields: Mapping[str, object]) -> dict[str, JsonValue]
```

**Purpose**: Cleans a dictionary of log fields or span attributes before they are exported. It removes fields whose names look sensitive, such as prompts, content, secrets, tokens, or credentials.

**Data flow**: It receives a mapping of field names to values. For each field, it normalizes the key by removing underscores and dashes and lowercasing it, skips the field if the normalized key is sensitive, and otherwise passes the value through `redact_value`. It returns a new JSON-friendly dictionary.

**Call relations**: It is used by `turn_span` before trace attributes are attached and by `_emit_log` before log fields are emitted. It also works together recursively with `redact_value` when nested dictionaries appear.

*Call graph*: calls 1 internal fn (redact_value); called by 3 (_emit_log, redact_value, turn_span).


##### `redact_value`  (lines 161–171)

```
def redact_value(value: object) -> JsonValue
```

**Purpose**: Converts one value into something safe and JSON-like for logs or trace attributes. It preserves simple values, walks through containers, and stringifies unusual objects.

**Data flow**: It receives any Python object. If the value is already a basic JSON-style value, it returns it unchanged. If it is a mapping, it converts keys to strings and sends the nested dictionary through `redact_payload`. If it is a non-string sequence, it redacts each item. Anything else becomes a string.

**Call relations**: It is called by `redact_payload` for each retained field. When it finds a nested dictionary, it calls `redact_payload` again, so the same sensitive-key filtering applies at every depth.

*Call graph*: calls 1 internal fn (redact_payload); called by 1 (redact_payload).


##### `log`  (lines 174–179)

```
def log(event: str, **fields: object) -> None
```

**Purpose**: Writes a structured informational log event. Callers use it for normal noteworthy events that should be searchable and tied to the active trace and workspace.

**Data flow**: It receives an event name and any number of named fields. It passes them to `_emit_log` with information-level severity, where workspace metadata is added, sensitive data is removed, and the record is emitted.

**Call relations**: This is the friendly public helper for ordinary logs. It delegates the real work to `_emit_log`, which keeps all log formatting, redaction, and OpenTelemetry emission consistent.

*Call graph*: calls 1 internal fn (_emit_log).


##### `log_error`  (lines 182–184)

```
def log_error(event: str, **fields: object) -> None
```

**Purpose**: Writes a structured error log event. Callers use it when something failed and should be visible as an error in monitoring tools.

**Data flow**: It receives an event name and fields describing the failure. It forwards them to `_emit_log` with error-level severity, which adds scope, redacts data, and sends the record to both logging paths.

**Call relations**: Like `log` and `warn`, this is a small public wrapper around `_emit_log`. It exists so callers can clearly choose error severity without repeating logging details.

*Call graph*: calls 1 internal fn (_emit_log).


##### `warn`  (lines 187–189)

```
def warn(event: str, **fields: object) -> None
```

**Purpose**: Writes a structured warning log event. It is for expected but important conditions that an operator may want to notice, even if they are not full errors.

**Data flow**: It receives an event name and extra fields. It passes them to `_emit_log` with warning-level severity, where they are scoped, redacted, and emitted.

**Call relations**: This public helper shares the same path as `log` and `log_error`. By delegating to `_emit_log`, warnings get the same workspace tagging, privacy filtering, and trace correlation.

*Call graph*: calls 1 internal fn (_emit_log).


##### `_emit_log`  (lines 192–206)

```
def _emit_log(event: str, severity_number: SeverityNumber, severity_text: str, level: int, fields: Mapping[str, object]) -> None
```

**Purpose**: Performs the common work behind all structured log helpers. It prepares safe fields, writes to Python's standard logger, and also emits an OpenTelemetry log record that can be tied to the active trace.

**Data flow**: It receives an event name, severity information, a standard logging level, and raw fields. It adds the current workspace from `_ambient_scope`, removes sensitive data through `redact_payload`, sends the event to the `ufo` standard logger with the cleaned fields attached, and emits the same event through OpenTelemetry logs.

**Call relations**: It is called by `log`, `log_error`, and `warn`. Those public functions decide the severity; `_emit_log` supplies the shared machinery so every structured log is shaped and protected the same way.

*Call graph*: calls 2 internal fn (_ambient_scope, redact_payload); called by 3 (log, log_error, warn); 2 external calls (getLogger, get_logger).


##### `emit_metric`  (lines 209–217)

```
def emit_metric(name: str, amount: int=1, **dimensions: str) -> None
```

**Purpose**: Increments one of the project's known metric counters. It is used to count important events, such as a turn starting or a recovery path being used.

**Data flow**: It receives a metric name, an amount to add, and optional text dimensions that describe the count. It first checks that the name is registered; unknown names raise an error so typos do not silently create bad metrics. It then reuses or creates an OpenTelemetry counter named with a `ufo.` prefix and adds the amount with the supplied dimensions.

**Call relations**: This function is the metric-writing entry point in the file. It uses OpenTelemetry's meter when a counter is first needed, then caches that counter so later calls can update it directly.

*Call graph*: 1 external calls (get_meter).


### Seat policy
Defines workspace seat eligibility rules used by billing, grants, automatic seats, and refusal behavior.

### `core/src/ufo/seats.py`

`domain_logic` · `request handling and cross-cutting admission checks`

A “seat” is permission for a workspace member to use the agent. Think of it like chairs at a table: some workspaces have unlimited chairs, while others have a fixed number, and only people with a chair can speak to the agent. This file contains the rules for giving out, checking, and removing those chairs.

The important idea is that membership and seating are separate. A refused person can still exist as a workspace member, with an email and identity, but the agent will not answer them until they are seated. This matters because the system can remember who they are and later let them continue once the owner grants a seat.

The file supports several moments in the product flow. When a member is created, it may automatically seat them if there is still included allowance. When someone sends a message, the gate checks whether their workspace enforces seats and whether that member has one. When an owner explicitly grants or revokes access, the same shared rules are used. The owner is protected from losing their own seat, because otherwise the workspace could get stuck with nobody able to grant seats.

There is also a small short-lived cache for workspaces with no seat limits. That avoids repeated database checks in the common unlimited case, while still letting a newly added limit take effect shortly after.

#### Function details

##### `gate_member`  (lines 45–59)

```
def gate_member(speaker_member_id: UUID | None, admission_source: TurnAdmissionSource, on_behalf_of_member_id: UUID | None) -> UUID | None
```

**Purpose**: Chooses which member a turn should be checked against for seat access. Usually this is the person speaking, but for a scheduled job it is the member the job acts for.

**Data flow**: It receives the speaker member id, the kind of admission, and an optional “on behalf of” member id. It first prefers the direct speaker. If there is no direct speaker and the turn came from a scheduled job, it uses the creator or represented member. If neither applies, it returns no member, meaning there is no seat check target.

**Call relations**: This is the shared answer to “whose seat matters here?” Admission, scheduled work, resume logic, and per-round checks can all use this same decision instead of inventing slightly different rules.


##### `seat_gate_absent`  (lines 62–68)

```
def seat_gate_absent(workspace_id: UUID) -> bool
```

**Purpose**: Quickly tells the caller whether this workspace was recently seen to have no seat limit at all. This lets common unlimited workspaces skip an extra database lookup for a few seconds.

**Data flow**: It receives a workspace id and looks in a small in-memory cache. If the cache has a future expiry time for that workspace, it returns true. Otherwise it returns false, meaning the caller should check the database normally.

**Call relations**: It relies on `_note_absent_limit` having recorded a recent no-limit result. It is used as a fast path for enforcement code that wants to avoid doing repeated work when seating is not enabled.

*Call graph*: 1 external calls (monotonic).


##### `_note_absent_limit`  (lines 71–76)

```
def _note_absent_limit(workspace_id: UUID) -> None
```

**Purpose**: Records that a workspace currently appears to have no seat rules. This supports the short-lived fast path used by seat checks.

**Data flow**: It receives a workspace id, reads the current clock, removes expired cache entries if the cache is full, and stores a new expiry time a few seconds in the future. It changes only the in-memory cache, not the database.

**Call relations**: It is called by `Seats.gated` and `Seats.admits` after they confirm from the database that both seat limit fields are empty. Later, `seat_gate_absent` can use that note to skip a database round trip.

*Call graph*: called by 2 (admits, gated); 1 external calls (monotonic).


##### `SeatSnapshot.seated`  (lines 106–107)

```
def seated(self) -> int
```

**Purpose**: Counts how many members in a seat snapshot currently have seats. It is a convenience property for reporting or display.

**Data flow**: It reads the snapshot’s member entries, checks each one’s seated flag, and returns the number marked as seated. It does not change anything.

**Call relations**: It belongs to the `SeatSnapshot` data returned by `Seats.snapshot`, giving callers an easy total without making them count manually.


##### `Seats.gated`  (lines 118–132)

```
async def gated(self, connection: AsyncConnection) -> bool
```

**Purpose**: Checks whether this workspace enforces seat access at all. If both the hard limit and included-seat allowance are absent, the workspace is treated as unlimited.

**Data flow**: It receives a database connection, reads the workspace’s `seat_limit` and `included_seats` values, and returns false if both are empty. In that unlimited case it also records a short cache note. If either value is present, it returns true.

**Call relations**: This is used when code needs to know whether seat rules should apply before asking about a particular member. When it discovers the unlimited case, it hands that fact to `_note_absent_limit` for later fast checks.

*Call graph*: calls 1 internal fn (_note_absent_limit); 2 external calls (execute, select).


##### `Seats.admits`  (lines 134–161)

```
async def admits(self, connection: AsyncConnection, member_id: UUID) -> bool
```

**Purpose**: Decides whether a particular member is allowed through the seat gate. Unlimited workspaces admit everyone; limited workspaces admit only members with a seat timestamp.

**Data flow**: It receives a database connection and a member id. It looks up that member in this workspace together with the workspace’s seat settings. If the member is not found, it returns false. If the workspace has no seat settings, it returns true and records the fast-path cache note. Otherwise it returns true only when the member’s `seated_at` field is set.

**Call relations**: This is the main yes-or-no check used when the agent is deciding whether to answer someone. It calls `_note_absent_limit` when it proves the workspace is unlimited.

*Call graph*: calls 1 internal fn (_note_absent_limit); 2 external calls (execute, select).


##### `Seats.snapshot`  (lines 163–185)

```
async def snapshot(self, connection: AsyncConnection) -> SeatSnapshot
```

**Purpose**: Builds a readable picture of a workspace’s current seat state. It includes the limits and every member’s email, whether they are seated, and whether they are considered the owner.

**Data flow**: It receives a database connection, reads the workspace’s seat bounds, then reads all members ordered by creation time. It turns those rows into `SeatEntry` objects and wraps them in a `SeatSnapshot`. The first member in creation order is marked as the owner.

**Call relations**: This is useful for owner tools, billing extensions, or status reports that need to show who has seats. It does not change the database; it only packages the current state.

*Call graph*: 4 external calls (__init__, __init__, execute, select).


##### `Seats.grant`  (lines 187–199)

```
async def grant(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Gives a seat to the workspace member with the given email. It is safe to call again for someone already seated, and it refuses if the hard seat limit is already full.

**Data flow**: It receives a database connection and an email address. It locks and reads the workspace limits, finds the member by email, and stops if they already have a seat. If there is a hard limit, it counts seated members and raises `SeatLimitReached` when no seat is open. Otherwise it writes the member’s seat timestamp.

**Call relations**: This is the explicit owner-grant path. It uses `_locked_limits` to avoid two grants racing past the limit, `_member_by_email` to identify the target, `_seated_count` to enforce the cap, and `_seat` to write the grant.

*Call graph*: calls 4 internal fn (_locked_limits, _member_by_email, _seat, _seated_count); 1 external calls (__init__).


##### `Seats.revoke`  (lines 201–215)

```
async def revoke(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Removes a member’s seat by email. It refuses to remove the workspace owner’s seat, because that could leave the workspace with nobody able to grant seats again.

**Data flow**: It receives a database connection and an email address. It locks the workspace limit row, finds the member, asks who the owner is, and raises `OwnerSeatRevocation` if the target is the owner. If the member is already unseated, it does nothing. Otherwise it clears their seat timestamp and updates their record.

**Call relations**: This is the explicit seat-removal path. It calls `_locked_limits` for consistent writes, `_member_by_email` to find the target, and `owner_member_id` to enforce the owner protection rule.

*Call graph*: calls 3 internal fn (_locked_limits, _member_by_email, owner_member_id); 3 external calls (__init__, execute, update).


##### `Seats.ensure_limit`  (lines 217–229)

```
async def ensure_limit(self, connection: AsyncConnection, limit: int) -> None
```

**Purpose**: Sets a workspace’s hard seat limit only if no limit has been set yet. This is useful when a billing or setup flow wants to establish an initial limit without overwriting later manual changes.

**Data flow**: It receives a database connection and a limit number. It rejects numbers below 1. Then it updates the workspace row only where the current `seat_limit` is still empty. If a limit already exists, nothing changes.

**Call relations**: This is called by setup or billing-related code that wants a one-time initialization rule. It does not call the grant logic; it only establishes the boundary future grants will obey.

*Call graph*: 2 external calls (execute, update).


##### `Seats.ensure_included`  (lines 231–243)

```
async def ensure_included(self, connection: AsyncConnection, included: int) -> None
```

**Purpose**: Sets the number of seats that may be handed out automatically, but only if that value has not already been set. This protects existing operator or billing choices from being overwritten.

**Data flow**: It receives a database connection and an included-seat count. It rejects numbers below 1. Then it updates the workspace row only when `included_seats` is currently empty. If a value already exists, it leaves it alone.

**Call relations**: This supports plan setup and billing extensions. `Seats.auto_seat` later reads this included allowance to decide whether a newly created member should be silently seated.

*Call graph*: 2 external calls (execute, update).


##### `Seats.auto_seat`  (lines 245–254)

```
async def auto_seat(self, connection: AsyncConnection, member_id: UUID) -> None
```

**Purpose**: Seats a newly created member automatically when there is still free included allowance. If the allowance is full, it leaves the member unseated so an owner must explicitly grant access.

**Data flow**: It receives a database connection and a member id. It locks and reads the workspace’s limit and included-seat allowance. It uses the included allowance if present; otherwise it uses the hard limit. If that bound exists and the current seated count has reached it, it returns without writing. Otherwise it marks the member as seated.

**Call relations**: This is called from `create_member` after a new member row is inserted. It uses `_locked_limits`, `_seated_count`, and `_seat` so automatic seating follows the same counting rules as explicit grants.

*Call graph*: calls 3 internal fn (_locked_limits, _seat, _seated_count).


##### `Seats._locked_limits`  (lines 256–264)

```
async def _locked_limits(self, connection: AsyncConnection) -> tuple[int | None, int | None]
```

**Purpose**: Reads the workspace’s seat settings while locking the workspace row. The lock is a database safeguard that stops two concurrent operations from both thinking the same last seat is free.

**Data flow**: It receives a database connection, selects this workspace’s `seat_limit` and `included_seats`, and asks the database to lock that row for update. It returns the two values.

**Call relations**: It is a helper for write operations. `Seats.grant`, `Seats.revoke`, and `Seats.auto_seat` call it before making decisions that depend on current seat counts.

*Call graph*: called by 3 (auto_seat, grant, revoke); 2 external calls (execute, select).


##### `Seats._member_by_email`  (lines 266–279)

```
async def _member_by_email(self, connection: AsyncConnection, email: str) -> tuple[UUID, datetime | None]
```

**Purpose**: Finds a workspace member by email and returns the information needed for seat changes. It treats email matching as case-insensitive and ignores extra spaces around the supplied email.

**Data flow**: It receives a database connection and an email string. It normalizes the email for lookup, reads the matching member id and seat timestamp from this workspace, and returns them. If no member matches, it raises `UnknownMember`.

**Call relations**: It is used by `Seats.grant` and `Seats.revoke`, because both owner actions start from a human-friendly email address rather than a database id.

*Call graph*: called by 2 (grant, revoke); 3 external calls (__init__, execute, select).


##### `Seats._seated_count`  (lines 281–289)

```
async def _seated_count(self, connection: AsyncConnection) -> int
```

**Purpose**: Counts how many members in this workspace currently have seats. This is how the file enforces seat limits before adding another seated member.

**Data flow**: It receives a database connection, queries the member table for rows in this workspace where `seated_at` is not empty, and returns the count as a number.

**Call relations**: It is called by `Seats.grant` and `Seats.auto_seat` before seating someone. Those callers use the count to decide whether adding another seat would exceed the allowed bound.

*Call graph*: called by 2 (auto_seat, grant); 2 external calls (execute, select).


##### `Seats._seat`  (lines 291–296)

```
async def _seat(self, connection: AsyncConnection, member_id: UUID) -> None
```

**Purpose**: Marks a member as seated. This is the single small write used after the higher-level rules have already decided seating is allowed.

**Data flow**: It receives a database connection and a member id. It updates that member’s `seated_at` and `updated_at` fields to the current database time. It does not return a value.

**Call relations**: It is called by `Seats.grant` for explicit owner-approved seating and by `Seats.auto_seat` for automatic seating during member creation.

*Call graph*: called by 2 (auto_seat, grant); 2 external calls (execute, update).


##### `owner_member_id`  (lines 299–309)

```
async def owner_member_id(connection: AsyncConnection, workspace_id: UUID) -> UUID | None
```

**Purpose**: Identifies the workspace owner using the project’s current rule: the earliest-created member is the owner. There is no separate owner column here.

**Data flow**: It receives a database connection and workspace id. It queries members in that workspace ordered by creation time and id, takes the first one, and returns that member id. If the workspace has no members, it returns nothing.

**Call relations**: It is used by `Seats.revoke` to prevent revoking the owner’s seat, and by `owner_conversation` to find where owner-facing messages should be sent.

*Call graph*: called by 2 (revoke, owner_conversation); 2 external calls (execute, select).


##### `create_member`  (lines 312–345)

```
async def create_member(connection: AsyncConnection, workspace_id: UUID, email: str) -> UUID
```

**Purpose**: Creates a workspace member in the one approved way, then applies the automatic seating rule. If another process already created the same member at the same time, it reuses the existing row.

**Data flow**: It receives a database connection, workspace id, and email. It tries to insert a new member with a fresh id. If the insert succeeds, it calls `Seats.auto_seat` for that new member and returns the new id. If the insert does nothing because the member already exists, it looks up and returns the existing member id.

**Call relations**: This is the shared creation path for onboarding and join flows. By routing member creation through this function, the project avoids forgetting to apply seat rules at some call site.

*Call graph*: 4 external calls (__init__, execute, select, uuid4).


##### `owner_conversation`  (lines 348–379)

```
async def owner_conversation(connection: AsyncConnection, workspace_id: UUID) -> tuple[UUID, UUID] | None
```

**Purpose**: Finds the best conversation where the system can ask the workspace owner something, such as a seat grant request. It returns both the conversation and the agent that should speak there.

**Data flow**: It receives a database connection and workspace id. It first finds the owner member. If there is no owner, it returns nothing. Then it finds the owner’s most recently updated member-bound conversation and the workspace’s earliest-created agent. If either is missing, it returns nothing; otherwise it returns both ids.

**Call relations**: It calls `owner_member_id` to use the same owner rule as the rest of the file. Billing or seat-request flows can use this function to get a destination without knowing the details of conversation and agent tables.

*Call graph*: calls 1 internal fn (owner_member_id); 2 external calls (execute, select).


##### `member_workspaces`  (lines 382–390)

```
def member_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a candidate list for jobs that should run for workspaces that have at least one member. It gives extensions a safe, central way to ask for those workspaces.

**Data flow**: It defines a small query that selects distinct workspace ids from the member table, then passes that query into `owner_candidates`. The result is a `WorkspaceCandidates` object that job scheduling code can use.

**Call relations**: This keeps the member-table knowledge inside core code. An extension can declare that it wants these candidates without directly reaching into the database schema.

*Call graph*: 1 external calls (owner_candidates).


##### `member_workspaces.with_a_member`  (lines 387–388)

```
def with_a_member() -> sa.Select[tuple[UUID]]
```

**Purpose**: Creates the actual database query for “workspaces that have at least one member.” It is intentionally broad and simple.

**Data flow**: It takes no outside input. It builds and returns a select query that reads distinct workspace ids from the member table. It does not execute the query itself.

**Call relations**: This inner helper is handed to `owner_candidates` by `member_workspaces`, so the wider candidate-building system can run or adapt the query when needed.

*Call graph*: 1 external calls (select).

## 📊 State Registers Touched

- `reg-effective-config` — The combined settings that tell the service which features, adapters, limits, and deployment options to use.
- `reg-workspace-context` — The current workspace and member context that keeps every request acting inside the right tenant boundary.
- `reg-workspaces-members-agents` — The durable records for workspaces, their members, and the agents that can act for them.
- `reg-permission-grants` — The permission ledger saying which agent may use which connected account or provider access.
- `reg-model-catalog` — The model switchboard that maps model names to providers, credentials, request formats, and prices.
- `reg-egress-proxy-state` — The controlled network gateway state that decides which outside sites sandboxed work may contact and how usage is counted.
- `reg-turn-queue-run-state` — The durable state of each agent turn, including whether it is queued, claimed, running, parked, finished, failed, or cancelled.
- `reg-scheduled-task-calendar` — The durable calendar of one-time and repeating tasks that workers can safely claim and run later.
- `reg-accounting-ledger-spend` — The usage ledger, spend caps, exports, and cost totals for models, egress, and other billable work.
- `reg-seat-billing-state` — The workspace seat limits, granted seats, included seats, and external billing integration state.
- `reg-observability-trace-state` — The logs, metrics, traces, traceparent links, and redaction rules used to monitor work safely.
- `reg-prompt-governance` — The saved prompt digests, prompt-change proposals, approvals, and experiment evidence that control instruction changes.
- `reg-subagent-work-tree` — The parent-child turn links, delegated work state, and message flow between main agents and subagents.
- `reg-database-connection-pool` — The process-wide database engine/session factory and connection pool used by request handlers, workers, schedulers, and persistence code.
- `reg-evaluation-replay-state` — Durable evaluation corpora, replay runs, scores, and judgments used by self-improvement and conformance workflows beyond prompt approval records.
- `reg-http-client-pools` — Shared outbound HTTP client/session pools and retry-capable transport state used for provider APIs, OAuth/credential bridges, connectors, model calls, billing, email, and other integrations.

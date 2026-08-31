---
rfc: 0041
title: "Harness and runtime in Rust, extensions as components"
status: proposed
date: 2026-08-23
---

# Harness and runtime in Rust, extensions as components

> Port `ufo-harness` and `ufo-runtime` to Rust. Move every extension to one of two tiers:
> infrastructure extensions become Rust crates linked into the one `ufoctl` binary; capability
> extensions become JS/TS WebAssembly components that run under wasmtime. The result is one
> language across the whole server, a real ABI where live Python objects cross a re-export facade
> today, and third-party
> extensions that are safe to run. **The measured cost is 10–14 months.** Read the Cost section
> before the rest.

## Decisions

Each row is a call this plan makes. Each is open to review at its unit. The rest of the document
is the detail behind this table.

| Fork | Decision |
|---|---|
| Core language | Rust. A **nested** cargo workspace rooted at `crates/` holds the core crates and the static extension crates. Never the repo root: a root manifest relocates `Cargo.lock` and `target/`, which breaks the four cargo-chef Dockerfiles, `dev/Dockerfile`, six `rust-cache` keys, `deploy.yml`'s tree-hash image skip, and three call sites in `evals/`. `rust-toolchain.toml` and `clippy.toml` are scoped to `crates/` — clippy walks ancestors, and `client/src` alone holds 113 `std::fs` sites. `client/` and `servers/*` stay standalone crates. |
| Crate boundaries | **Drawn against the import graph, not the package names.** Core's packages are not separable as they stand: 25 of 35 form one 44,142-line strongly connected component. The seam cut (R0b) inverts `ufo-sdk` into a leaf trait crate and moves ten symbols down before any unit builds on it. |
| Durable execution | An in-repo `ufo-durable` crate on the **sibling `_dbos` database** (SQLite in dev, Postgres in deploys) — not the app database, which fails the control plane's `bootstrap_policies` (`servers/control/src/rls.rs:331-346`) and closes an atomicity window `engine.py:1780-1795` depends on. It implements only the DBOS subset core uses. The community port `dbos-transact-rust` was audited and rejected (below). DBOS for Rust previews 2026-09-10 and changes nothing unless it ships partitioned queues, dedup ids, SQLite, a serde serializer, and a multi-executor test rig. |
| Dequeue | `FOR UPDATE NOWAIT` under a global ceiling, with a skip-partition rule. **Not `SKIP LOCKED`:** under a ceiling, count-then-claim with `SKIP LOCKED` lets two workers both claim — the defect this RFC rejects `dbos-transact-rust` for. SQLite uses `BEGIN IMMEDIATE` for every read-then-write, with two busy timeouts (5,000 app, 30,000 `_dbos`). |
| Extension tiers | An extension implementing a core backend seam (carrier, hub, index, embed, model, CDP, search, auth proxy, memory search, connector broker, terminal transport) or holding a privileged surface becomes a static Rust crate. An extension working over the capability context becomes a JS/TS WebAssembly component. A third answer exists for declaration-only extensions (below). |
| Component runtime | wasmtime 46 or later (WASI 0.3, native async). JS/TS guests build with StarlingMonkey via componentize-js. `jco` generates the TS types, published as `@ufo/sdk`. |
| Extension ABI | One WIT package, `ufo:ext`, **designed at R0a against the measured call list** — the draft below is a starting shape, not a contract. The Python re-export facade dies. |
| DB layer | sqlx with runtime-checked queries. No `query!` macros, so RFC 0036's objection (compile-time checks need a live database at image build) does not apply. One query set serves SQLite and Postgres. |
| Migrations | **Python owns the schema until R16.** Every unit before it runs against a database `uv run ufoctl migrate` created. At R16 the alembic history closes at a baseline a Rust migration runner adopts, together with the comparator that proves the adoption. |
| HTTP | axum 0.8 — the house standard in three existing crates. |
| Model clients | Hand-rolled over reqwest, ported from `models/{anthropic,openai}.py`. |
| Cutover | Build beside, swap whole, one deploy at a time (dev → testing → prod). No dual-stack turn processing. No cross-language DBOS: drain, migrate, start. |
| Scope change | **`servers/*` changes after all.** R6 adds fixtures under `servers/egress/tests/` and `servers/cache/tests/`, and R16 needs two changes to `servers/control/src/rls.rs`. The earlier non-goal was wrong. |
| Out of scope | The portal and app frontends (browser TS). `containment.py`'s in-image copy, which stays Python permanently. `evals/` stays Python. Every wire. Every schema, beyond the baseline adoption. |

## Current state

The server is Python. The services around it are already Rust.

| Scope | Lines |
|---|---|
| `core/src/ufo/` — 348 files | 57,790, plus 118,388 of tests |
| 49 extensions, shipped Python | 74,623, plus 102,550 of tests |
| Extension TS/TSX (browser code) | 56,841 |
| `evals/` — 63 files reaching into core | 46,006 |
| Rust in production: `client/`, `servers/{cache,control,egress,preview}` | 45,345 |

`spec.md:28` rejected Rust for core on four grounds. Each has expired, or this design answers it:

| Ground | Now |
|---|---|
| "the salvage is Python" | The salvage is finished. This repo is the source. |
| "DBOS has no Rust SDK" | Still true. DBOS announces its first Rust release for 2026-09-10. The subset core uses is small enough to own either way. |
| "the loop is I/O-bound" | True, and it stays true. This is not a speed claim; the repo has no performance budget to cite. The wins are correctness and topology: the pickle replay serializer dies, three asyncio loops become one runtime, a deploy becomes one static binary, and the server joins the language its data and control planes already use. |
| "extensions must be writable in the AI ecosystem's default language" | TS is that language, at least as much as Python in 2026. The component tier also gives users and agents what Python packages never could: a one-artifact install with no dependency resolution, and a sandbox that makes third-party code safe to run. RFC 0039 calls the current path "a dead end for third-party (WASM) extensions"; archived RFC 0015 designed this isolation. |

RFC 0035 named the real obstacle: egress policy is extension-coupled, and none of it can leave core
Python "without re-porting the extension system". This RFC is that re-port.

Facts the port builds on, each measured:

- **DBOS usage is narrow but wider than its declarations.** Core declares 3 workflows
  (`loop/queue.py:809`, `runtime/jobs.py:812`, `:820`) and 4 steps. It runs **three queues**: the
  turn queue, the job queue, and DBOS's `_dbos_internal_queue`, which carries every cron fire —
  schedules are registered with no queue name (`core/tests/runtime/test_jobs.py:321` asserts it).
  Without that third queue no scheduled job ever fires. Three of the four steps are
  `preemptible=True`, implemented upstream as a 1 QPS per-step poller; core catches DBOS's private
  `DBOSWorkflowCancelledError`. Every workflow inherits `DEFAULT_MAX_RECOVERY_ATTEMPTS = 100`,
  past which the row dead-letters and releases its dedup id — a second release path, and the guard
  that stops the 5-second recovery sweep re-dispatching a crash-looping turn forever.
- **The packages do not separate.** 25 of core's 35 packages are one 44,142-line strongly
  connected component on top-level imports. `ufo-harness` and `ufo-runtime` are mutually dependent
  across 48 and 46 module edges; merged they are 41,989 lines. `db.py:423` and `db.py:316` hide
  cycles behind function-local imports, which Rust cannot express. `ufo-sdk` is today the *top* of
  the stack — a 36,571-line closure with 315 import sites — not the leaf the port needs.
- **The SDK is a type seam, not a wire seam.** 35 re-export modules, 1,629 lines, passing live
  objects: `ToolDef.handler` is a coroutine, `ScopedStore` opens transactions inline,
  `ExtensionContext.transaction()` hands out a raw connection. Only an AST gate holds it.
- **The schema is one metadata.** 31 tables in `schema/tables.py`, dialect-neutral. 118 core
  alembic revisions plus 45 extension revisions. RLS policy DDL lives in the Rust control plane.
- **Three asyncio loops share one process**, forcing per-loop engine pools and hand-tuned
  connection arithmetic (`db.py:13-58`). One tokio runtime deletes the problem class.
- **The wires are fenced** by two-sided golden fixtures — and one of them, `golden.json`, has
  three silently drifted families, because nothing regenerates it.

## Architecture

### Crates

Drawn against the import graph. `ufo-foundation` exists because `schema/`, `o11y`, `workspace`,
`turns/audience` and the object-scope helpers sit beneath everything and are cyclic with their
callers as packages stand.

| Crate | Ports |
|---|---|
| `ufo-foundation` | `schema/` (6,312), `o11y.py`, `workspace.py`, `turns/audience`, `object_name.py`, `object_scope.py`, `agent_scope.py`, the containment host half |
| `ufo-config` | `config.py`, once `models.interface` is no longer reachable from it |
| `ufo-db` | `db.py`. sqlx pools; `workspace_tx` (RLS GUC); `owner_tx`; `encode.rs` for the SQLAlchemy on-disk encodings. The migration runner arrives at R16. |
| `ufo-blob` | `blob.py`, once two symbols move down; filesystem and S3; the lz4 frame format |
| `ufo-durable` | The DBOS subset (below) |
| `ufo-models` | `models/`. Wire blocks, streaming clients, registry, pricing. **`ModelClient` is defined here**, not in `ufo-sdk`, because extensions contribute live constructors through it. |
| `ufo-sdk` | A **leaf** trait crate: the traits and context types both tiers program against, with `ext/context.py` and `ext/surface.py` signatures inverted and their implementations left above the engine |
| `ufo-access` | `access/` (3,042) — grants, credentials, egress rules and resolver, the egress-control RPC |
| `ufo-sandbox-host` | `sandbox/` host half, ingress serve, `proxy_serve.py` |
| `ufo-ext-host` | The wasmtime host: component loading against `ufo.lock` digests, per-dispatch instantiation, epoch deadlines, host functions |
| `ufo-harness` | Product-neutral model streaming, round progression, context-window and compaction decisions, and sandbox execution protocols. No tenant, database, billing, extension-host, or DBOS dependency (RFC 0043). |
| `ufo-runtime` | DBOS workflow adapters and the durable agent host in Python's `ufo.runtime`. It composes authorized effects from the domain crates and supplies them to `ufo-harness`. |
| `ufo-onboard` | Workspace onboarding from Python's `ufo.onboard`; separate from agent execution. |
| `ufo-billing` | Balance, accounting, and spend policy from Python's `ufo.runtime.billing`; separate from agent execution. |
| `ufoctl` (bin) | `cli.py`. clap verbs over the crates above. The one shipped binary. |

A static extension crate lives where its extension lives today, depends on `ufo-sdk` alone, and
joins the workspace. The active set still comes from `ufo.lock` at boot.

### Durable execution

`ufo-durable` re-implements the DBOS subset core uses, on the sibling `_dbos` database.

| Primitive | Semantics reproduced |
|---|---|
| Workflow | One row per workflow id. The id is the deterministic uuid5 turn id, so re-enqueue is idempotent. The terminal commits on the failure path too. |
| Step | Memoized outputs in an `operation_outputs`-equivalent table, serde-serialized. Replay returns recorded outputs. The model round and tool dispatch keep frozen `call_id`s. |
| Preemption | A per-in-flight-step poller that reads the workflow row, aborts the step task, and raises a **public** cancelled error. Without it a member's stop waits out the model round. Three of core's four steps need it. |
| Queues | Three: the partitioned turn queue (concurrency 1 per conversation), the job queue with worker concurrency, and the internal queue every schedule enqueues onto. |
| Dedup | A dedup id held from enqueue to terminal, released on **both** paths — terminal and dead-letter — plus the typed duplicate-enqueue error the job dispatcher handles. |
| Poison guard | `recovery_attempts`, the 100 cap, and the dead-letter terminal. The stranded-turn reconciler is what terminalizes a poisoned turn, since that status is not an advancing one. |
| Schedules | Upsert-only dynamic schedules re-armed from rows at boot, plus the list and delete verbs the suite needs. A deterministic `sched-<name>-<RFC3339>` fire id collapses every instance's tick, so no leader election. |
| App version | Three places decide dispatch: the dequeue predicate, the pending-workflow query, and the schedule thread's latest-version read. Core pins the literal `"ufo"`, which makes all three inert; a derived version would silently drop scheduled work on a rolling deploy. |
| Recovery | Executor id = the instance heartbeat row. The survivor sweep re-dispatches workflows whose executor has no fresh heartbeat, and never a live peer's. Cancel. Two list shapes. |

Serialization is serde behind a `Serializer` trait with a per-row `serialization` tag column, so a
format change leaves old rows readable. The replay-safe pickle apparatus has no Rust equivalent,
because typed serialization removes the problem.

Two dialects are two targets. DBOS's own SQLite backend has no row-level locking, and both of
core's multi-process contention tests skip unconditionally off Postgres — so the SQLite half of
every contention proof is net-new design, not a port.

#### The community port, audited

`dbos-transact-rust` (MIT, 14,823 lines) was audited against the matrix above and **rejected**.
Three findings each disqualify it alone:

| Finding | Evidence |
|---|---|
| Partitioned queues are unimplemented, and fail silently | `queue_partition_key` is stored, returned, and decoded, but no dequeue query in either backend reads it. The concurrency count is `WHERE queue_name = $1 AND status = $2` — per queue, not per partition (`db/postgres.rs:688-795`). `context/mod.rs:29` marks the field `#[allow(dead_code)] // used by partitioned-queue dequeue (later phase)`. A caller sets a key, gets no error, and gets no isolation. |
| The queue ceiling is advisory | The dequeue counts pending rows without locking them, then claims candidates one at a time. Two workers that each count zero against a ceiling of one both claim successfully. |
| Recovery double-executes a live peer's work | `DEFAULT_EXECUTOR_ID` is the literal `"local"`, every `launch()` sweeps its own id, `run.rs:232` exempts recovery from the ownership guard, and no heartbeat column exists. Distinct VM ids invert the fault into permanent stranding, so no configuration is correct. |

Its replay-safety guard is dead — `is_within_step: true` appears nowhere — and
`await_workflow_result` is an unbounded poll with no deadline. No test runs two executors against
one database, which is why none of this was caught.

Four artifacts are **harvested as prior art**, carrying both copyright notices (the author's and
DBOS, Inc.'s — this repo ships no `LICENSE` or `NOTICE` today, so that is a deliberate first):
the initial schema SQL; the single-trait, two-backend shape (the shape, not its 3,441 hand-mirrored
lines); `Serializer` plus the per-row format tag; and the deterministic schedule fire id. Its
unlocked count-then-claim is kept as negative prior art — the reason the dequeue decision above
is `NOWAIT` and not `SKIP LOCKED`.

### The component world

One WIT world is the extension ABI. **It is designed at R0a against the measured call list** —
roughly 81 `ctx.*` entry points across the component-tier extensions. The draft below is missing
whole capability families (sandbox, spawn, search, index, embed, skills, connector broker, probes,
seats, accounting, blob, artifact links, member context, trajectories) and three of its seven
exports do not exist. It is a starting shape:

```wit
package ufo:ext@0.1.0;

world extension {
  import store;         // get / put / put-if (CAS) / delete / list — ext_store
  import credentials;   // get / resolve / rotate — declared slots only
  import model;         // complete -> stream<model-event>; turn
  import objects;       // the five verbs over registered kinds
  import conversations; // open-conversation, invoke, tail, retitle, facts
  import pages;         // pages-changed-since(cursor) — the PageFeed
  import sql;           // owned-table statements
  import files;         // conversation workspace writes
  import http-out;      // bounded egress through the host client
  import o11y;          // log / warn / emit-metric

  export manifest: func() -> manifest;
  export handle-tool: async func(call: tool-call) -> tool-result;
  export handle-hook: async func(event: hook-event) -> hook-outcome;
  export run-job: async func(run: job-run) -> result<_, job-fault>;
  export handle-route: async func(req: request) -> response;
  export sync-source: async func(run: sync-run) -> sync-result;
  export object-store: object-verbs;
}
```

WASI 0.3 gives native `async func`, `stream`, and `future`, so handlers keep their async contract
with no sync-over-async bridge. Components import nothing from `wasi:filesystem` or
`wasi:sockets`: the world is the whole reachable surface, which makes the boundary deny-by-default.

Hosting: one precompiled component per extension loaded at boot against its `ufo.lock` digest,
instantiated per dispatch from the pooling allocator, with the handler's existing budget as an
epoch deadline and a memory cap per instance.

Three problems R0a must settle before R7 can be scoped:

- **Component data access is not "own tables only" today.** Four extensions JOIN core tables —
  `sweep` reads `turn`, `report_digest` reads five core tables plus another extension's, and
  `metronome` owns no tables and writes `workspace_balance` directly. A per-extension Postgres
  role also breaks Python core's access to those tables while Python still runs, because
  `grant_serve_role` cannot grant on tables `ufo_owner` does not own.
- **Job dispatch has no expressible component form.** `JobSpec.candidates` is a SQLAlchemy Select
  core runs under `owner_tx`, cross-workspace and RLS-bypassing, and a CI gate refuses any
  `JobSpec` without one. Eight component extensions declare jobs. Either the seam is redesigned
  as a host-owned due-work query, or those eight are not components.
- **Hooks pass live validated instances between extensions**, and a gating hook that raises fails
  closed with the Python exception class name in member-visible text. Both need an ABI answer.

### The static tier

A static extension crate exports a `Manifest` value built from `ufo-sdk` types — the same
declaration model, typed by the compiler. Backend seams stay Rust traits, selected by config and
built once at boot. Surfaces keep the privileged `SurfaceContext` as a Rust API: the surface seam
asserts member identity, so isolating it buys nothing — it is the trust boundary.

The boundary gate survives, translated: a static extension crate may depend only on `ufo-sdk` and
external crates, checked from `cargo metadata`.

### The split — all 48

**Static tier (24):** `ufo`, `web` (backend), `slack`, `imessage`, `debugger`, `sites`; `docker`,
`e2b`; `redis_hub`; `index_default`, `turbopuffer`; `embed_openai`; `bedrock`,
`openrouter`; `perplexity`; `browser`, `sandbox_chrome`, `browserbase`; `memory`; `connectors`,
`composio`, `pipedream`, `eval_env`, `mcp`. Each holds a backend seam, a privileged surface, or a
live session the component boundary would cut.

**Component tier (22, JS/TS):** `browser_use`, `coding`, `gbrain`, `metronome`, `monitors`,
`objectives`, `repl`, `report_digest`, `research`, `sample`, `scheduled_tasks`,
`self_improvement`, `skill_create`, `sweep`, `todos`, and the five `app_*` homepages once the
declaration question below is settled.

**Two tiering questions this RFC does not close:**

- **`sources` is not one of the 24.** Its `direct` auth proxy and stream-sync driver already move
  to the static tier, it declares `auth_proxies`, and its 50 providers stand on a 1,398-line core
  Python framework that must be re-authored in TypeScript. It needs its own tier split and its own
  unit.
- **Eight extensions have no handler code at all** — the five `app_*`, `brief_pipeline`,
  `documents`, `keyed_connectors` — averaging under 250 lines of pure declaration. A StarlingMonkey
  binary per constant is the wrong shape; they need a declaration-only path with no component.

### Dependency map

| Python | Rust |
|---|---|
| dbos 2.28 | `ufo-durable` |
| SQLAlchemy Core + asyncpg + aiosqlite | sqlx (postgres + sqlite) |
| alembic | the in-repo runner, SQL files, baseline adoption |
| FastAPI + uvicorn + starlette | axum 0.8 |
| pydantic | serde with `deny_unknown_fields`; schemars + jsonschema for member- and model-facing payloads |
| anthropic / openai SDKs | ported wire clients over reqwest |
| httpx | reqwest |
| cryptography (Fernet, x509) | the fernet crate; rcgen for the ephemeral egress CA |
| opentelemetry-* | tracing + opentelemetry-otlp |
| aiobotocore | aws-sdk-s3 |
| websockets | tokio-tungstenite |
| lz4 | lz4_flex, frame-format parity vector |
| pillow | image (already in `servers/preview`) |
| croniter | a cron crate, pinned by fixtures on the 6-field secs-first grammar |
| jsonschema + referencing | the jsonschema crate |
| pyyaml | serde_yaml |
| click | clap |
| grpcio + protobuf | tonic + prost |
| fastmcp | rmcp |
| segno / tomli-w / dnspython | qrcode / toml / hickory-resolver |

## Both ends

Every unit lands its producer, its consumer, and its tests in one change.

- **Tests land with their unit.** Rust's guarantees excuse almost none of them: the skippable
  classes — type-confusion tests (6 assertions in the whole suite), pure shape validation, the
  pickle serializer's mechanics, per-loop plumbing — total about 2%. The compiler prevents data
  races, not race conditions, so every race regression stays. Note the honest precedent: **both
  completed rewrites in this repo retired their Python suites rather than porting them.** Where a
  unit retires rather than ports, it says so and prices the replacement.
- **The sample stays the seam probe, doubled** — a component sample for the world, a static sample
  for the backend traits. The two-sided conformance gate needs a well-formed partition first, and
  `ConnectorProvider` and `SourceProvider` currently straddle both tiers in one value; R0a settles
  that or the gate has no premise.
- **Fixtures carry a Python-side regenerate-and-compare test for as long as Python lives.** One
  exemption, named: raw provider SSE, which the Python client cannot round-trip because it
  consumes the vendor SDK's typed events. Those fixtures are refreshed nightly against a live
  provider instead.
- **The extension ABI gets a design unit, not a spike slot.** R0a lands an RFC amendment before
  any host code exists.

## Parity contracts

The honest cost of the port. Each row is asserted from both sides.

A recorded set that nothing regenerates rots silently: `directives.jsonl` stays correct because a
test regenerates it, while `servers/egress/tests/golden.json` has no reader and has drifted in
three families — its pricing digest reproduces only if `cache_write_30m` is omitted, and its Usage
block lists five fields where the wire carries six. A `gates.py` closure fails any vector file no
test reads.

| Contract | Held by |
|---|---|
| Terminal directive wire | The existing `directives.jsonl` — **one file, two producers**: 16 workspace verbs and 9 onboard verbs whose producer is already Rust. The generation direction must be settled per half before either flips, or the mandatory Python regenerate test goes red. |
| Client `--json` event wire | Derived from the directive wire; existing client tests. |
| Provider stream | A tagged `ModelEvent` encoding (none exists today — the event union is untagged and one variant is a bare dataclass), plus decode-equality between Python's client and Rust's. **The two Rust SSE decoders — `ufo-models` and `servers/egress` — must agree**, or the same call is priced two ways with nothing failing. |
| Tool and object JSON Schema, and validation-error text | Every tool call is conditioned on `model_json_schema()` bytes and on raw pydantic `ValidationError` strings returned to the model. Under the component tier `@ufo/sdk` emits both. No TS schema library reproduces pydantic's output by default. |
| `price_digest` bytes and the rendered prompt digest | Both reach the model or the ledger and both are generated, not copied — the model-catalog skill renders Python-only format specs. |
| Portal read projections, SSE frame shapes, prepared intents | Golden JSON from the Python routes, replayed against the Rust `web` crate. The frontend's vitest suite runs unchanged. |
| Internal RPCs | `rule_contract.json`, `bearer_contract.json`, `onboard_contract.json`. |
| Bearer, surface, run, probe, artifact and ingress tokens | Two-sided vectors. Note the codecs are triplicated: core Python, `servers/egress`, and now core Rust, with no sharing across the workspace boundary. |
| Fernet values at rest and ttl-sealed states | No Rust in this repo has done Fernet. Owned by R5. |
| uuid5 identities | Vectors. A Slack retry crossing cutover must dedup to the same turn id. |
| Blob formats | Old transcripts must read back. |
| SQLAlchemy on-disk encodings | `sa.Uuid` stores a **32-character dashless hex string** (`CHAR(32)`) where sqlx writes a 16-byte BLOB. Plain `sa.JSON` stores Python `None` as literal JSON `null`, not SQL NULL — `ext_store.value`, `agent.tools`, `agent.setup`, `proposal.body`, `source.config`. Neither fails loudly. |
| Schema | At R16, a purpose-built dump-and-diff. `information_schema` cannot see indexes, partial predicates, or plpgsql bodies, and SQLite has none. |
| Prompt bytes | `loop/prompts/*.md` and skill bodies copy byte-for-byte; renderer digest parity. |

## Build order

One unit = one reviewable branch, proven end-to-end before the next. The first milestone is a
**vertical heartbeat, not a horizontal foundation**: a foundations unit whose proof is a schema
diff would violate `docs/plan.md` and would answer a question nobody doubts.

| # | Unit | Delivers | Proof |
|---|---|---|---|
| R0a | ABI world design | RFC amendment only: the `ufo:ext` world drawn against the ~81 measured `ctx.*` entry points; homes for `JobSpec.candidates`, route and surface `identify`, conversation slots, onboarding steps, member skills; tier answers for `connectors`, `sources`, the packs, and the declaration-only eight; the component-SQL mechanism | Every call site in the component-tier sources maps to a named import, a static trait, or a recorded refusal; no `ctx.` verb is unassigned; the Open decisions it settles are struck |
| R0b | Seam cut | `ufo-foundation`; the ten symbol moves; `ufo-sdk` inverted into a real leaf trait crate, implementations left above the engine | Every crate builds alone; `cargo metadata` is a DAG with `ufo-sdk` a leaf; a crate naming any core crate but `ufo-sdk` fails the gate |
| R1 | Spine | The `crates/` nested workspace, pinned toolchain, scoped `clippy.toml`; `ufo-config`; `ufo-db` with `encode.rs` | Rust reads and writes a Python-migrated database byte-identically on both dialects; a uuid Rust writes is the 32-char dashless hex SQLAlchemy wrote and a JSON `None` round-trips as literal `null`; Python reads back every row Rust wrote |
| R2 | Heartbeat | `ufo-blob`; `ufo-durable` replay subset; `ufo-models` Anthropic half; `ufo-harness`'s zero-tool path; `ufo-runtime` + the `ufo` surface; the seat and heartbeat halves of `runtime/` | `ufoctl init` in Python, then Rust `serve` on that SQLite file: the unmodified release client streams a real answer and returns to its prompt; the same exchange on Postgres under the non-superuser `ufo_serve` role; Python decodes the `messages.json.lz4` Rust wrote and `ledger_id_for` names its ledger row; a second message admits at seq 2 under the conversation row lock; `kill -9` in a two-process fleet leaves the survivor's sweep re-dispatching with exactly one completion call recorded across both |
| R3 | Durable, complete | Partitioned dequeue on both dialects; all three queues; dedup with both release paths; `recovery_attempts`, the cap and the dead-letter terminal; app-version rules; schedules with list and delete; cancel **including the preemptible-step poller**; two list shapes | Two Rust `serve` processes on one `_dbos` store, both dialects: a second workflow on partition A stays enqueued until A's first commits while B's starts at once, and the losing dequeue claims nothing; a fault-killed row keeps its recorded step outputs and the peer's sweep returns it to enqueued with the loopback model's counter showing round one never re-ran; crash-looped past the cap it dead-letters and releases its dedup id; a duplicate id raises typed while the same workflow id re-enqueued is a silent no-op; one fire id collapses two processes' tick; cancelling mid-preemptible-step aborts within the poll interval and commits the terminal |
| R4 | Model wire | The OpenAI half, the byte-level SSE decoder, a tagged `ModelEvent` encoding, image trimming and budgets, the retry and fault policy, pricing, catalog, a manifest-free registry | A checked-in fixture synthesized from the same vendor objects today's 105 tests build: Python's shipped client and Rust decode it to the same tagged event list, with thinking signature and redacted data verbatim in block order; request bodies compare as parsed JSON trees; each fault shape gives the same prefix, retry count and terminal; one live sample per provider refreshed nightly proves the synthesized shapes and a real signature round-trip |
| R5 | Credential plane | `access/credentials.py`, workspace scope, `media/`, `runtime/candidates.py`; **Rust Fernet**; the ambient-workspace decision (threaded scope vs task-local) | A Fernet value Python sealed decrypts in Rust and back; a sealed credential request past its ttl is refused by both; an unset slot raises where a set slot resolves; a model call under a Rust workspace scope books its usage to that workspace and none other |
| R6 | Egress control plane | `ufo-access` with derivation inverted to take manifest-shaped values; the git-credential route; run and probe token codecs; the ephemeral CA; local carrier and the containment host half | One manifest fixture drives Python and Rust to byte-identical rule sets; the unmodified `ufo-egress` binary against Rust serve admits a CONNECT for a live turn and refuses it after the terminal, the real key never on the sandbox leg; a metered request lands one ledger row at the id `ledger_id_for` names; the real cache daemon resolves a credential and clones a private repo, a second workspace getting a different principal; new two-sided fixtures pin all five routes, and `golden.json`'s three drifted families are corrected |
| R7 | Component host | `ufo-ext-host`, `@ufo/sdk`, `ufoctl ext build`, the CI component job, one probe component | `ufoctl ext build` emits `probe.wasm` and `ext install` records its sha256, a flipped byte refused by name; `ufoctl ext dispatch` runs `handle-tool` in a bound workspace and Python's `ScopedStore` reads the rows back with JSON `None` as literal `null`; a second workspace reaches none; a lost `put-if` returns the conflict variant; a handler past its epoch traps with its rows absent; the WIT manifest record's fields equal `ufo-sdk`'s from one generated source; the probe's tool schema bytes equal Python's; a replacement enumerator keeps the wheel gate and the floor test green |
| R8 | Turn engine core | `loop/`, `turns/`, `skills/`, `hub.py`, `tools/` and the **16** builtins | Three tool calls in one round dispatch and stream directives the release client renders; Python decodes the transcript and replays tool_use order with the memoized `call_id`s; a conversation crossing the window compacts and a later turn quotes verbatim a fact only in the `before` blob; the rendered prompt is byte-identical to Python's against a regenerated vector; a foreground spawn returns a contract-validated result and a background spawn returns its child turn id then delivers |
| R9 | Admission and surface seam | `surfaces/`, `SurfaceContext` and the writeback poller, `seats.py`, serve boot and route mounting, the pack seam | A second message folds onto the inbound queue, drains at the round boundary firing `user_prompt_submit` on a probe, and the terminal refuses to close over a non-empty queue; a seatless speaker commits cancelled; a cap breached at admission parks held-not-enqueued and resumes at the same seq; a durable surface's writeback commits with its turn row and the poller delivers; a pack narrowing to a missing extension refuses boot by name |
| R10 | Objects and kinds | `objects.py`, `kinds/`, links, the change journal, listings | The `agent` kind's five verbs round-trip an authored spec; status, apply and delete each refuse a stale generation before disclosure or mutation; a listing projection reads back what the verb wrote |
| R11 | Spend plane | `billing/`, spend evaluation, caps, the balance gate, rollups, usage exports, non-token meters | A turn over a member cap parks and resumes when raised; a workspace at zero balance refuses admission and a credit unparks it; a rollup groups by `price_digest` bytes Python computes identically; an export mints, reads pending and acks once |
| R12 | Eval harness severance | An admission, step-timeline and cancel wire; pack-aware stack boot; `ufoctl` verb parity; the ablation and GEPA rigs against a Rust stack | `python -m evals` runs a suite end-to-end against Rust serve with zero `from ufo.*` imports reaching core internals; `evals.ablate` materializes two arms and returns sample-level verdicts; `core/tests/evals/` is green |
| R13 | Static surfaces | ufo, web backend, slack, imessage, debugger, sites, and the sandbox ingress | Each surface's suite lands. Portal vitest green against Rust routes. |
| R14 | Static infra | Carriers, hubs, indexes, embeds, models, cdp, brokers, connectors, memory; the sample split and the two-sided conformance gate | Per-extension suites land. Memory-recall evals hold. |
| R15 | Components | The component-tier extensions | Each lands with tests. The floor test runs over every installed component. |
| R16 | Operator surface and schema handover | `ufoctl` verbs, the onboarding RPC, bundle, store installs of `.wasm`; the migration runner, baseline, comparator; per-extension SQL role enforcement | init → serve → portal on SQLite, zero services. A bundle boots. The baseline equals the alembic head on both dialects, and Python stops owning the schema. |
| R17 | Cutover + tear-out | dev, testing, prod swaps; delete the ported packages under `core/src/ufo` **except the permanent Python residue**; the spec.md + AGENTS.md rewrite | Full eval suites at parity or better on Rust before prod. The Python shape greps to zero, save the named residue. |

Newly placed prerequisites that previously lived in no unit: `access/`, `sources/`, `billing/`,
`runtime/` and fourteen smaller modules — about 11,500 lines. The permanent Python residue is
`containment.py`'s in-image copy, which must be maintained in Rust for the host and Python for the
sandbox image with matching semantics. R17 exempts it from deletion by name. The two in-sandbox CLIs
that used to sit beside it are already Rust: `sbx` and `sbxfs` became the `ufo llm` and `ufo fs`
verbs on the client, and `client/src/guard.rs` carries the guard's checks 2 to 4 for the paths those
verbs are handed.

## Cost

Measured against this repo's own completed ports — `servers/control` came out at 1.86× source and
0.78× tests — R3 through R12 is roughly **140,000–170,000 lines of Rust and 105–150 sessions**,
of which R8 alone is 20–26 and R6 is 15–17. R1 and R2 add 9–13k lines and 12–16 sessions.

**The honest total is 10–14 months.** An earlier draft of this RFC implied 12–16 weeks for the same
span, a 3–4× miss driven by the ~11,500 unpriced lines, the eval-harness severance, and the
assumption that suites port rather than retire.

## Deploy and cutover

Per deploy: stop admission, drain (the 600s graceful window exists), verify no live workflow
remains, run the baseline adoption R16 delivered, start the Rust image.

Scheduled work needs no bridge — task and job rows are app rows, and the durable crate re-arms
schedules from rows at boot. Parked turns resume normally. Until the first post-baseline Rust
migration lands, the Python image restarts cleanly against the same schema; that is the rollback
window.

## Doctrine fit / implications

- **Core stays core.** The boundary moves for no capability. Everything statically linked was
  already infrastructure a deploy selects by config.
- **Enforce, don't document.** The SDK import gate becomes physics: a component cannot name a core
  symbol, and CI checks a static crate's dependency set from `cargo metadata`. The ruff ASYNC
  class, the banned-import list, and the `__init__` gates die with their language;
  `clippy.toml` disallowed-methods holds blocking-in-async.
- **One event loop.** Satisfied structurally: one tokio runtime replaces three asyncio loops.
- **One shape.** Violated for the duration and restored at R17 — the longest dual shape this repo
  will have carried, and at 10–14 months that is the plan's largest single cost.
- **Both ends or neither.** The build order itself violated this in an earlier draft: admission
  sat in a later unit than the proof that needed it. The order above places each consumer with
  its producer.
- **What gets worse.** Two contributor toolchains. Compile times. A narrower extension API. A
  permanent Python residue inside the sandbox image. Token codecs implemented three times, because
  the nested workspace cannot share with `servers/*`.

## Risks

| Risk | Position |
|---|---|
| The 10–14 month window | The dominant risk, and the reason to weigh this RFC as a program rather than a refactor. Units land dark but CI-green from R1; cutover is one switch per deploy. |
| Test volume | 220k lines, and the two prior rewrites retired rather than ported. Each unit states which it does. |
| `ufo-durable` is bespoke | Bounded by the enumerated subset. The one existing Rust port failed that subset on three independent counts, so bespoke is the measured choice. |
| componentize-js is labeled experimental | StarlingMonkey runs production at Fastly and Fermyon. Pin by digest; the world is ours, so a backend swap stays behind `ufoctl ext build`. |
| The component tier may not fit every extension assigned to it | R0a settles `sources`, the declaration-only eight, and the job-candidates seam before any host code exists. |
| No acceptance harness until R12 | Units R8–R11 run without evals. R12 could move earlier at the cost of building wires against an incomplete engine. |

## Alternatives

| Option | Why not |
|---|---|
| Stay Python; move only extensions to wasm | Delivers the isolation win at a fraction of the cost, and at 10–14 months for the full port this option is stronger than it looked. It keeps the pickle serializer, the three-loop process, the GIL caveat, and a second language between core and its own data and control planes. The honest partial option. |
| PyO3, incrementally | Two shapes of every ported module, during and after. The GIL still owns the loop. The extension seam stays Python. |
| Go | Viable. Rust is the house systems language — 45,345 lines, five crates, conventions set. |
| Temporal / Restate for durability | A second server binary breaks zero-services dev. Temporal's Rust SDK is not GA. Restate co-locates a journal store core does not need. |
| All extensions static Rust | Kills user- and agent-authored extensions and the store's third-party story. |
| Extensions as JS on embedded V8/Node | No capability sandbox — Node reaches the filesystem and network by default. |
| Per-extension RPC plugin processes | 49 processes under one serve, and every context call becomes a network hop. |

## Open decisions

| Fork | Options |
|---|---|
| Whether to run the program at all | 10–14 months is the measured price. The partial option above buys the isolation win alone for a fraction of it. The owner's call, and the first one. |
| The official DBOS Rust preview (2026-09-10) | Re-check against the bar the community port failed: partitioned queues, dedup ids, SQLite, serde, and a multi-executor test rig. |
| Component instance lifecycle | Per-dispatch instantiation (the correctness default) vs pooled instances. Measure at R7. |
| `sources` packaging and tier | Its own unit; settled at R0a. |
| Prod cutover timing | Against live launch workload. The owner's call at R17. |

## Non-goals

- No wire changes: terminal, portal API, Slack, webhooks, internal RPCs.
- No schema reshape beyond the baseline adoption. No behavior changes. No features ride the port.
- No prompt-text changes: prompt and skill bodies copy byte-for-byte.
- `client/` does not change.
- The portal and app frontends do not change — browser TS is not server code.
- `evals/` stays Python. It drives deploys over the wire and becomes the judge, not the subject.
- No cross-language DBOS interop, ever: drained, never bridged.

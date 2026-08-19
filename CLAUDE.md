# ufo — agent guidelines

## Where to look first

`spec.md` is the source of truth for design — the core/extension doctrine, fixed decisions, the
workspace model, and non-goals. Read it before proposing structural changes. `docs/salvage.md` maps
what ports from the previous repo (`~/src/metalcraft`) and what was deliberately left behind —
consult it before writing something the old repo already proved. `docs/plan.md` is the build order.
The member portal's look is held by its own components and by `theme.test.tsx`, so read
`extensions/web/frontend/src/theme.css` and the component set before changing a screen.

**Core doctrine is the first review question:** if a capability can be an extension, it is not
core. Every addition to `core/` must name why extensions cannot express it.

**Every member action happens in chat.** Connecting an account, granting access, approving a
change — a member expresses it in natural conversation and the agent drives it (a tool it calls,
surfacing any link in its reply); never a slash-command, keyword, or bespoke end-user HTTP
endpoint. The only endpoints are the chat transport itself, authenticated **read projections** of
object, status, usage, and audit data (a portal page reads directly), **prepared intents** — a
portal form's one mutation path: the panel's structured intent is admitted as a turn the engine
dispatches verbatim to the typed object verb, so the turn IS the chat transport and the audit
record, the route only prepares and admits, and the panel reads back the typed result or refusal —
a **stateless display render** (the composer rasterizing a member's picked file to show them what
they are about to send: bytes in, a picture back, storing nothing and admitting no turn — a preview
is not an action, it grants nothing and changes nothing, so it never has to be a chat turn) — and
unavoidable third-party plumbing (e.g. an OAuth callback). The speaker gates the granting act;
subsequent use is the wire's job. (`ufoctl` CLI verbs are the operator surface — a different
audience, not member actions.)

- Study how established products solve the problem before designing a solution. Adopt their proven
  patterns and conventions rather than inventing an approach from scratch.

## One shape

**The code is exactly what it does, nothing else.** Anything that creates a second answer to "what
is this" — past form, future form, conditional form, alias, hypothesis — is dead weight on the next
reader.

**No transition language.** Every file reads as if designed this way from day one: no `legacy`,
`deprecated`, `formerly`, `for now`, `v1`/`v2` staging, `TODO`, migration notes. This repo has no
past; salvaged code arrives as if written here.

## Enforce, don't document

If a constraint can be made true by code — a required argument, a type, a test, a CI gate — encode
it there and delete the prose. A cross-cutting precondition (workspace scoping, credential access,
sandbox egress) is established once at the boundary and threaded down; the unsafe primitive stays
module-private behind a factory, backed by a gate. Extensions import only `ufo.sdk` — a CI
gate forbids `core` internals in `extensions/`.

## Succinctness

Generated text and code are as succinct as possible — specs, docs, commits, code. Keep every
hard-to-vary decision; cut the words around it. Prefer a table to prose. One example, not three.

## User-facing copy

**Spartan and factual.** Every word a member reads — terminal screens, emails, web pages, agent
replies — states what happened, what is true, or what to do next. No UFO metaphors (`beam`,
`transmit`, `signal`, `saucer`, `mothership`, `identification`, `unidentified`): the product is
named ufo and that is the whole joke. No greeting, reassurance, exclamation, or restatement of what
the member just did. A line the member cannot act on and did not ask for does not ship.

**Default typography, zero styling.** Copy makes no typographic statement: sentences take standard
capitalization and end punctuation (one ending in a URL, path, or address drops the period);
headings and labels are cased normally — Title Case or sentence case, one convention per surface —
never lowercase-as-aesthetic (`Join Waitlist`); placeholder values are neutral (`email@work.com`).
Commands, addresses, and header names render verbatim (`curl`, `gmail.com`, `x-ufo-session`) even
at sentence start; the landing panel's `>` prompt and `submit` verb are terminal chrome fixed by
test, not labels. Letter-spacing spelled in spaces (`u f o`) and decorative punctuation do not
ship — visual character lives in CSS and the drawn craft, never in the characters of the words.

**The ASCII craft is the mark and stays.** It is drawn, not said — the metaphor ban governs words,
and the landing fleet is live data (one craft per workspace). The curl card and the landing page
keep it, each held there by a test; strip decoration around it, never it.

## Skills

Creating or editing a Skill requires reading and applying
[Designing, Refining, and Maintaining Agent Skills at Perplexity](https://research.perplexity.ai/articles/designing-refining-and-maintaining-agent-skills-at-perplexity)
as a review gate:

- Write evals first from real queries, known failures, and neighbor confusion. A description change
  requires positive and negative loading evals; a body change requires an end-to-end behavior eval.
- Make the description a ≤50-word routing trigger beginning `Load when`; name member intent and
  never summarize the workflow.
- Keep only non-obvious procedure, taste, and gotchas the agent would otherwise miss. Use imperative
  form, maximize signal per token, and delete generic tool instructions or system-prompt repeats.
- Keep `SKILL.md` as the hub. Put deterministic repeated logic in `scripts/`, heavy conditional
  knowledge in `references/`, and output material in `assets/`; name exactly when to read each.
- Prefer negative examples for known traps. Treat every Skill as a context tax and every routing
  edit as a possible regression in adjacent Skills.
- Forward-test non-trivial changes on realistic tasks without leaking the intended answer. A Skill
  change is incomplete until its focused evals and validation pass.

## Code style

- **Lean and concrete by default** — small direct implementations; three similar lines beat a
  clever helper; generic only where a real matrix demands it (tool/connector/carrier type tables).
- **Organize by workflow, not by layer** — a flow with ≥2 steps and a live dependency is ONE frozen
  dataclass: deps in fields, one public method that *is* the workflow, private steps beneath it
  **in execution order** — the reader reaches the whole flow by reading downward once. Free
  module-level functions are for genuinely shared stateless primitives only, never a workflow's
  steps.
- **Two containers, two meanings** — `BaseModel` = data that crosses a boundary (wire, persisted,
  config, untrusted): validated at construction, fully serializable, never
  `arbitrary_types_allowed` (gated). `@dataclass(frozen=True)` = internal: workflows holding live
  deps, value objects that never leave the process. The container IS the classification; a class
  that seems to need both is misclassified. Never mirror one concept in both forms — a record
  flows as its one type everywhere.
- **Inline the trivial** — a function whose body is a single return earns a name only when that
  name is a reused domain concept (≥2 callers) or a Protocol implementation. One caller → inline;
  a private method survives only as a narrative step, recursion, or reuse. The test is "does the
  module read more cleanly?", never "might someone reuse it?".
- **Localize next to the user** — a type, constant, or helper lives beside its only user; nothing
  moves to a shared module before the second user exists. No `utils.py`, `helpers.py`, or grab-bag
  modules, ever.
- **`__init__.py` is an empty marker** — never code, never re-exports. A symbol is imported from
  the named module that defines it, so its one home is obvious from the import path. A package that
  holds one module is a module (`tools/builtins.py`, not `tools/builtins/__init__.py`). A leading
  docstring is the only statement an `__init__` may carry (gated).
- **Follow-the-flow test** — understanding one verb must not require hopping across files: entry
  point → steps → types, one file, top to bottom. More than two file-hops to trace a flow means
  the seams are wrong — fix the seams.
- **Fail loud** — raise on missing config or unexpected values; no silent fallbacks.
- **Async-native, one event loop** — `ufoctl serve` is one process: a blocking call stalls
  every surface, stream, and turn at once. Async DB driver, async HTTP, `async def` DBOS
  workflows/steps; never `time.sleep`, `subprocess.run`, sync `open()`, or a sync client inside
  `async def` (ruff `ASYNC` rules gate this; `requests`/`psycopg2` are banned imports). A sync
  impl behind `await asyncio.to_thread(sync_impl, …)` is two code paths and two error models for
  one operation — `to_thread` is a last resort for a library with genuinely no async API, and
  GIL-bound CPU work beyond ~10ms goes to a pool deliberately, never incidentally. Sync I/O is
  fine only off the loop: CLI startup, migrations, build scripts.
- **No comments; docstrings only on public APIs** — Terraform, tests, and supporting actor methods
  get no explanatory comments or docstrings. Names and structure carry intent.
- **Constants over magic values** — top-level `SCREAMING_SNAKE_CASE`.
- **Absolute imports, top-level imports, pathlib, guard clauses, built-ins over hand-rolled loops,
  match/case over isinstance chains, no `hasattr`/`getattr`.**
- **Explicit data structures** across module boundaries — never `dict[str, Any]`.

## Hot paths and failure domains

- **Root-cause, never work around.** An internal fault — deadlock, race, wedged query — is
  diagnosed to its root and fixed deterministically, never masked by a retry, sleep, timeout bump,
  or CI rerun. This is a realtime system; injected latency and retry loops are unacceptable.
  Retry/backoff/timeout is legitimate only against proven *external* uncertainty (a model, OAuth,
  or network egress call), never for our own DB, DBOS, queue, or code — and a root fix deletes the
  stopgap that preceded it.
- A client's wait always ends: one try encloses the turn; the except commits the terminal state.
- Derived state (embeddings, summaries, index rows) is produced by jobs, never inline on a write.
- Bound every payload sent to an external API next to the call.
- Event-fired work must be unable to fire on events it caused; batch-at-interval is the default.

## Hard problems

A problem that survives the first approach — a wedged bug, an open design, deep research — runs
as a portfolio, not a single bet:

- Keep a registry of approaches tried and exactly where each failed. A stalled route is blocked;
  it earns renewed effort only with a materially new mechanism, never a rerun.
- No route dominates on elegance. Keep incompatible routes alive across rounds; cross-pollinate
  only after each has exposed its real strengths and gaps.
- Subagents return concrete work — a failing test, a measurement, a counterexample, a diff, a
  primary source — never status reports. "Routine" and "should work" are rejected for any
  unproven step; the same bar governs review: audit a change against its known failure modes.
- The orchestrating agent synthesizes, challenges, redirects, and launches new rounds; a failed
  first wave is data, not a stopping condition. Budget effort in rounds, not wall-clock —
  parallelism compresses time, never rigor.

## Testing

- New function → new test; bug fix → the test that would have caught it.
- **Never test fakes**: test pure logic directly, or integrate against the real dependency
  (Postgres, Docker). A fake may stand in for a dependency, never be the thing asserted.
- **The sample extension is the probe for the extension seam** — it is a real consumer (real entry
  point, real manifest, real dispatch, real scoped context), so asserting how core calls it tests
  the system, not a fake. It records received calls through its own capability APIs (its store,
  memory writes) and tests read those back through public surfaces — no mock call-logs. It proves
  the seam (that core calls correctly, and that forbidden acts raise), never a subsystem's logic —
  those keep their real end-to-end proofs.
- Run the focused tests for the touched path before claiming done; never the full suite locally.
  One file or one test goes through `make test-one FILE=…`, which is serial: every xdist worker
  boots and migrates a database of its own, so `-n auto` only pays above roughly 100 tests.
- `uv run pytest`, `uv run ruff`. uv for everything Python.
- **Every pytest run records where its wall-clock went** — the shared plugin writes `.pytest-timings/`
  (gitignored): a row per test in `tests-<worker>.jsonl` and `tests-<worker>.csv` (nodeid, directory,
  outcome, setup/call/teardown seconds, xdist worker, `database_url` param, start/stop), what each
  fixture cost that process in `fixtures-<worker>.json`, and one `run-summary.json` (sha, PR, job,
  shard, worker count, wall seconds, counts by outcome, ten slowest tests). `--timings-dir` moves the
  directory; `--timings-dir ""` writes nothing.
- **To find CI time, merge those artifacts — never re-time the suite locally.** Every pytest job
  uploads its directory as `test-timings-test-shard-<0-5>` / `-control-test` / `-rls` /
  `-integration-<0-2>` (`if: always()`, kept 14 days; an artifact name takes no `/`, so the shard is
  spelled in full inside each `run-summary.json`). `gh run download <run-id> --dir /tmp/timings
  --pattern 'test-timings-*'` then `python3 .github/scripts/ci_timings_report.py /tmp/timings
  [--merged-csv merged.csv]` prints per-job wall time, shard balance, the slowest tests, directories
  and fixtures, the sqlite-vs-postgres split, and tests whose outcome moved between runs. Rankings
  settle around ten runs.

## Completing work

- Land tear-outs whole; before claiming done, grep for the source shape by name.
- **A tear-out includes its rows.** Removing an extension drops its tables *and* its `ext_store`
  rows in the same migration — nothing else ever will, and absence alone cannot be the trigger,
  since a failed import would then destroy the live cursors of an extension that is merely
  unreachable this boot.
- **Both ends or neither** — every declared surface (column, field, event, frame kind, enum
  member, config knob, Manifest point) ships with its producer AND its consumer in the same
  change, and the unit's proof exercises both. A producer without a consumer is dead weight; a
  consumer without a producer is a lie to the reader. If one end can't be built yet, the
  declaration doesn't land — this repo has no punch list and nothing declared "for later". A
  migration adds only the columns its unit wires; later units bring their own migrations.
- **A new migration's id is a UTC stamp** — scaffold it with `uv run ufoctl new-migration <slug>`,
  which writes `core/src/ufo/schema/migrations/versions/<YYYYMMDDHHMMSS>_<slug>.py` carrying that
  stamp as `revision` and core's head as `down_revision`. The id carries no ordering —
  `down_revision` does, and the single-head gate is the guard. When another branch's migration
  merges first, repoint `down_revision` at the new head and keep the stamp.
- Prove the chain end-to-end: realistic input → durable state → a user or agent can use it.
- Failed experiments are reverted in the same session, with the revert committed.
- Docs (spec.md, README.md) update in the same commit as the architectural change they
  describe.

## Commits

- Terse messages (`add turn loop`, `wire slack surface`). No AI attribution, ever — the ban covers
  the whole message, subject line and body alike. No `Co-authored-by:` or `Signed-off-by:` trailer
  naming a model, agent, or tool, and no agent address (`noreply@anthropic.com` and its like). No
  generator credit in any form: a `Generated with …` line, an emoji-marked credit, a tool's name
  or URL, a "written by" footer. A PR body holds to the same ban, because a squash merge copies it
  into the commit message; the conversation id a coding subagent stamps points at the record that
  asked for the change, names no author, and stays.
- Always a branch; PR when ready; never merge without approval.
- Split into independently reviewable units; squash fixups within a unit.

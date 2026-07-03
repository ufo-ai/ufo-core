# selfhost — agent guidelines

## Where to look first

`spec.md` is the source of truth for design — the core/extension doctrine, fixed decisions, the
workspace model, and non-goals. Read it before proposing structural changes. `docs/salvage.md` maps
what ports from the previous repo (`~/src/metalcraft`) and what was deliberately left behind —
consult it before writing something the old repo already proved. `docs/plan.md` is the build order.

**Core doctrine is the first review question:** if a capability can be an extension, it is not
core. Every addition to `core/` must name why extensions cannot express it.

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
module-private behind a factory, backed by a gate. Extensions import only `selfhost.sdk` — a CI
gate forbids `core` internals in `extensions/`.

## Succinctness

Generated text and code are as succinct as possible — specs, docs, commits, code. Keep every
hard-to-vary decision; cut the words around it. Prefer a table to prose. One example, not three.

## Code style

- **Lean and concrete by default** — small direct implementations; three similar lines beat a
  clever helper; generic only where a real matrix demands it (tool/connector/carrier type tables).
- **Organize by workflow, not by layer** — a flow with ≥2 steps and a live dependency is ONE frozen
  dataclass: deps in fields, one public method that *is* the workflow, private steps beneath it
  **in execution order** — the reader reaches the whole flow by reading downward once. Free
  module-level functions are for genuinely shared stateless primitives only, never a workflow's
  steps. Pydantic at boundaries (wire/persisted); frozen dataclasses for internal workflow and
  value objects.
- **Inline the trivial** — a function whose body is a single return earns a name only when that
  name is a reused domain concept (≥2 callers) or a Protocol implementation. One caller → inline;
  a private method survives only as a narrative step, recursion, or reuse. The test is "does the
  module read more cleanly?", never "might someone reuse it?".
- **Localize next to the user** — a type, constant, or helper lives beside its only user; nothing
  moves to a shared module before the second user exists. No `utils.py`, `helpers.py`, or grab-bag
  modules, ever.
- **Follow-the-flow test** — understanding one verb must not require hopping across files: entry
  point → steps → types, one file, top to bottom. More than two file-hops to trace a flow means
  the seams are wrong — fix the seams.
- **Fail loud** — raise on missing config or unexpected values; no silent fallbacks.
- **Async-native** — async DB driver and HTTP; `asyncio.to_thread` only for libraries with no
  async API; never block the event loop.
- **No comments** — code self-documents through naming; docstrings on public APIs OK.
- **Constants over magic values** — top-level `SCREAMING_SNAKE_CASE`.
- **Absolute imports, top-level imports, pathlib, guard clauses, built-ins over hand-rolled loops,
  match/case over isinstance chains, no `hasattr`/`getattr`.**
- **Explicit data structures** across module boundaries — never `dict[str, Any]`.

## Hot paths and failure domains

- A client's wait always ends: one try encloses the turn; the except commits the terminal state.
- Derived state (embeddings, summaries, index rows) is produced by jobs, never inline on a write.
- Bound every payload sent to an external API next to the call.
- Event-fired work must be unable to fire on events it caused; batch-at-interval is the default.

## Testing

- New function → new test; bug fix → the test that would have caught it.
- **Never test fakes**: test pure logic directly, or integrate against the real dependency
  (Postgres, Docker). A fake may stand in for a dependency, never be the thing asserted.
- Run the focused tests for the touched path before claiming done; never the full suite locally.
- `uv run pytest`, `uv run ruff`. uv for everything Python.

## Completing work

- Land tear-outs whole; before claiming done, grep for the source shape by name.
- Prove the chain end-to-end: realistic input → durable state → a user or agent can use it. Every
  new primitive ships with a live reader or writer in the same change.
- Failed experiments are reverted in the same session, with the revert committed.
- Docs (spec.md, README.md) update in the same commit as the architectural change they describe.

## Commits

- Terse messages (`add turn loop`, `wire slack surface`). No AI attribution, ever.
- Always a branch; PR when ready; never merge without approval.
- Split into independently reviewable units; squash fixups within a unit.

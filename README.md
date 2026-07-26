# ufo

An agent runtime you can run, read, and extend: a hard-to-vary core — sandboxed agent loop, memory,
Slack/CLI/web surfaces, accounting, Anthropic/OpenAI/Bedrock Mantle model access — plus an extension
system for everything else (connectors, data sources, tools, subagents, onboarding).

- `spec.md` — source of truth: doctrine, fixed decisions, workspace model, non-goals.
- `docs/contracts.md` — core contracts per unit (sections delete as code lands).
- `docs/plan.md` — build order. `docs/salvage.md` — file-level port map from the previous repo.
- `docs/handbook/` — generated stage-by-stage reference to the harness (start at `overview.md`).
- `core/` — the axiomatic unit. `extensions/` — first-party extensions. `packs/` — skill packs.

## Run it

**Zero services** — SQLite, filesystem blobs, in-process hub; one process, no Docker:

```bash
export ANTHROPIC_API_KEY=...
uv run ufoctl init --email you@example.com  # writes ufo.toml; SQLite — zero services
uv run ufoctl serve                         # one process: surfaces + workers
uv run ufoctl chat                          # second terminal; sessions persist across runs
```

**Full hosted stack** — the whole hosted topology in one command: Postgres, the onboarding
**gateway** (`/login`), and the **serve** fleet (surfaces + DBOS workers + embedded egress proxy):

```bash
export ANTHROPIC_API_KEY=...   # required for serve to boot; the gateway/login needs no key
docker compose up
```

One image (`dev/Dockerfile`, the whole workspace via uv) runs as four roles (`dev/entrypoint.sh`):
`init` migrates and runs `rls-bootstrap` (the `ufo_serve` role + RLS policies);
`gateway` serves `/login` on :8080 with the code emailed to the log (`UFO_CONTROL_EMAIL_MODE=console`
— read it from `docker compose logs gateway`); `serve` runs the shared fleet on :8710 over the
`assistant` pack with local backends (filesystem blobs, in-process hub, the built-in `local` sandbox
carrier). Sign in at `http://localhost:8080/login` with a work email and the code from the gateway
log (local signup needs no invite — `UFO_INVITE_REQUIRED=false`). Host ports override via
`UFO_PG_PORT` / `UFO_GATEWAY_PORT_HOST` /
`UFO_SERVE_PORT_HOST` / `UFO_REDIS_PORT`, and `UFO_DEV_PACK` selects the pack the serve config names
(`assistant_billing` adds Metronome, so the owner's billing choice can be driven locally — see
`docs/onboarding.md`). The dev config (`dev/ufo.toml`) swaps the hosted cloud
backends for local ones; the Turbopuffer/Exa index, the Redis hub (multi-replica), and the Docker
sandbox carrier each need their extension added to a local pack — out of scope for this single node.

Postgres (this compose, or an existing instance) also backs the Postgres half of the test matrix;
Docker enters for sandboxes (U2+).

## Evals

With `ufoctl serve` running against a disposable workspace:

```bash
uv run python -m evals --workspace <workspace-id> --label baseline
uv run python -m evals --view
uv run python -m evals --share <current-run> <baseline-run>
```

`python -m evals.stack matrix.toml` runs several suites at once, each in an auto-provisioned
isolated stack — a run directory under `.local/evals/<stamp>/<label>/` with a derived `ufo.toml`
(its own database, blob root, and probed serve/proxy ports), a freshly seeded workspace, and its
own `ufoctl serve` — recording every run into the shared archive. Each `[[run]]` block names a
label, a template config carrying the suite knobs, and the `python -m evals` arguments; run
directories and per-run databases are retained for reconstruction.

Each invocation records an immutable JSON run under `eval-reports/runs/` and rebuilds the offline
`eval-reports/index.html` viewer. Every case shows its full setup (message or scenario, rubric,
member binding) and every attempt its tool trajectory, tool errors, per-criterion judge verdicts,
grader evidence, turn log, and stored transcript snapshot (with the agent's base prompt); inline
images and private credential handoffs are omitted. Evidence keys the viewer does not model render
as raw JSON, so a new suite's evidence is never invisible. Comparisons show deltas only for
digest-identical suites.

Every case is recorded whole — `record_run` refuses a case whose prompt or response arrived null —
and a run whose archive was recorded without evidence renders as "not recorded — rerun the case",
never as a valid empty result; `--reconstruct <run> --workspace <id>` rebuilds a diagnostic copy
from the durable conversation, turn, and blob records under `eval-reports/reconstructions/`
without touching the original. Shared viewers are private and carry the whole record: they contain
only the named runs, their S3 object key has 192 random bits, and the presigned URL expires after
seven days by default. Sharing uses `--s3-bucket`, then `UFO_EVAL_SHARE_BUCKET`, then the
configured S3 `[blob]` bucket.

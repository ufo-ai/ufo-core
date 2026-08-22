# ufo

An agent runtime you can run, read, and extend: a hard-to-vary core — sandboxed agent loop, memory,
Slack/CLI/web surfaces, accounting, Anthropic/OpenAI/Bedrock Mantle model access — plus an extension
system for everything else (connectors, data sources, tools, subagents, onboarding).

- `spec.md` — source of truth: doctrine, fixed decisions, workspace model, non-goals.
- `docs/contracts.md` — core contracts per unit (sections delete as code lands).
- `docs/plan.md` — build order. `docs/salvage.md` — file-level port map from the previous repo.
- `docs/sweep.md` — Daily Brief application setup and operating checks.
- `docs/handbook/` — generated stage-by-stage reference to the harness (start at `overview.md`).
- `evals/swebench/README.md` — pinned SWE-bench Verified local generation and official grading.
- `core/` — the axiomatic unit. `extensions/` — first-party extensions. `packs/` — skill packs.

## Run it

`make` lists every target below and the rest of the working set (`check`, `test`, `fmt`,
`reinstall`).

`make setup` creates `.env` once and leaves an existing file unchanged. Set
`UFO_ANTHROPIC_API_KEY` and `UFO_OPENAI_API_KEY` there before starting either topology. `.env`
refuses the bare names — every tool reading the file picks those up; a bare `ANTHROPIC_API_KEY`
or `OPENAI_API_KEY` exported in the environment still serves as a fallback.

**Zero services** — SQLite, filesystem blobs, in-process hub; one process, no Docker:

```bash
make install                          # uv sync, both npm trees, git hooks
make init EMAIL=you@example.com       # writes ufo.toml; SQLite — zero services
make build                            # the web portal is the local client
make serve                            # one process: surfaces + workers
make portal                           # second terminal: opens the portal in your browser, signed in
```

A node with no sign-in page in front of it has one browser door: `ufoctl portal` hands this
machine's CLI token to a local one-shot page, which posts it to the portal exactly as the hosted
sign-in card does. Nothing is typed or pasted, and the bearer never rides a URL.

**Full hosted stack** — the whole hosted topology in one command: Postgres, the onboarding
**gateway** (`/login`), the **serve** fleet (surfaces + DBOS workers + the egress-control RPC), and
the standalone **ufo-egress** data plane (the Rust egress proxy, sharing serve's network namespace):

```bash
make stack STACK=1             # http://ufo-1.localhost:18080/login
make stack STACK=2             # http://ufo-2.localhost:18180/login
```

One image (`dev/Dockerfile`, the whole workspace via uv) runs as four roles (`dev/entrypoint.sh`):
`init` migrates, shapes the `ufo_control` gateway ledgers (`ufo-control migrate`), and runs
`rls-bootstrap` (the `ufo_serve` role + RLS policies);
`gateway` serves `/login` on the selected host port with the code emailed to the log
(`UFO_CONTROL_EMAIL_MODE=console` — read it with `make stack-logs STACK=N`); `serve` runs the shared fleet on :8710 over the
`assistant` pack with local backends (filesystem blobs, in-process hub, the built-in `local` sandbox
carrier). `STACK` accepts 1 through 5. Each slot has its own Compose project, image, network,
workspace directory under `.local/ufo-N/workspaces`, volumes, ports, and `ufo-N.localhost` browser
origin. The origin keeps session cookies separate.
Slot 1 uses gateway :18080, serve :18710, Postgres :15541, and Redis :15543. Each next slot adds 100
to each port. `make stack STACK=N` rebuilds the selected slot's image from the current code. Use
`make stack-logs STACK=N` for its sign-in code. `make stack-down STACK=N` keeps the slot's image,
volumes, and local workspaces. Pointing a slot at different code and clearing the prior branch's
schema and data requires
`docker volume rm ufo-N_pgdata ufo-N_blobs && rm -rf .local/ufo-N`.
A worktree also needs the repo root's `.env` copied in. The portal is built by npm and not tracked
in git, which is why `make stack` builds it first; raw `docker compose up` bypasses that build and
the validated local-secret boundary. Host ports override via
`UFO_PG_PORT` / `UFO_GATEWAY_PORT_HOST` /
`UFO_SERVE_PORT_HOST` / `UFO_REDIS_PORT`, and `UFO_DEV_PACK` selects the pack the serve config names
(`assistant_billing` adds Metronome, so the owner's billing choice can be driven locally — see
`docs/onboarding.md`). The dev config (`dev/ufo.toml`) swaps the hosted cloud
backends for local ones; the Turbopuffer index, Perplexity search, the Redis hub (multi-replica), and the Docker
sandbox carrier each need their extension added to a local pack — out of scope for this single node.

Postgres (this compose, or an existing instance) also backs the Postgres half of the test matrix —
`make db` starts that service alone; Docker enters for sandboxes (U2+).

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

`python -m evals.ablate experiment.toml` measures a change to text a model reads — a skill, a
prompt section, a corpus file — the way the Prompts rule demands: one arm per variant, each arm
its own git worktree, venv, and stack, always beside a control arm on the unmodified base. The
experiment file names the base revision, the suites and cases, repeats, a budget the preflight
refuses to exceed, the stack's template config, and `[[arm]]` blocks mapping repo paths to variant
files. Verdicts compare sample-level pass counts per case and call a move only past a two-sample
gap at equal sample counts (or a total collapse or fix); a failing case is data — a stack's
nonzero exit is fatal only when it wrote no record. Reports, every arm's records, and each arm's
stack logs land under `eval-reports/experiments/<name>/`; an arm that owed a record and wrote none
names the gap in the report and keeps its worktree, so the stack that spent the money can still be
read. Judge and simulator variance is real: compare arms from the same experiment, never across
runs.

```toml
name = "customers-section-clauses"
base = "origin/main"
suites = ["onboarding_help"]
cases = ["slack-install-pending", "promised-credits"]
repeats = 2
budget_usd = 60.0

[template]
database = { url = "postgresql+asyncpg://ufo:ufo@127.0.0.1:5541/ufo" }
blob = { backend = "filesystem", root = "./blobs" }
connect = { public_base_url = "http://evals.invalid" }
pack = { name = "assistant_hosted" }

[[arm]]
name = "no-topic-list"
[arm.files]
"packs/assistant_hosted/ufo_pack_assistant_hosted.py" = "arms/no-topic-list.py"
```

`python -m evals.gepa optimize.toml` writes the variants an ablation then measures. It runs GEPA
(Genetic-Pareto reflective prompt evolution, [Agrawal et al.](https://arxiv.org/abs/2507.19457))
over the `modules` the config names — a skill description, a skill body, a corpus reference, or a
pack prompt section. The scored cases split into a minibatch source and a held-out `D_pareto`, and
each iteration picks a parent off the Pareto frontier (the candidates best on at least one case,
dominated ones pruned, weighted by how many cases they lead), takes one module round-robin, samples
a minibatch, and asks the reflector model to rewrite that module. The reflection reads the case's
structured evidence — the member message, the answer, every tool call with its result, each judge
criterion's own rationale, and the rig signatures the harness owns — never the case's top-level
reason, which `combine` builds by joining every grader's phrasing into one line.

One candidate changes exactly one module, so a rollout measures one lever; candidates that changed
disjoint modules merge by concatenating their diffs. Every verdict is a paired run: the proposal and
the parent reduced to that same module run at the same moment over the minibatch plus the `controls`
the change could break, and a proposal is accepted only when it gains on the targets and loses no
control. A comparison reads the cases both arms scored: an instance one arm's stack infra-excluded
scored nothing there, and a pair with no case in common accepts nothing. Scores from two runs are
never compared — identical-text controls have swung 4/6 → 0/6 → 3/6 on one family. Cases the
environment cannot pass go in `unscorable` and leave the task set before optimizing; a case the seed
rollout could not score drops the same way. A module longer than the reflector reads whole is
refused when the config loads, because the reply replaces the file and would come back missing the
tail. `--dry-run` prints the plan and the estimate and spends nothing.

Before anything ships, every arm — the top `arms` candidates and the merge of the disjoint winners
— is measured against the base over the whole suites in one paired run, because a minibatch cannot
see a neighbour regression, and then passes the claims gate: fitting a rubric is not truth, so text
that wins a judge by asserting something false about the product is refused. A clause-level question
— whether one sentence carries weight — belongs in `evals.ablate`, not here.

GEPA proposes; the ablation decides. No repo file is written: a shipped candidate lands as
`eval-reports/experiments/<name>/arms/<module>.<candidate>.<ext>` beside a ready-to-run
`experiment.toml` naming them as `[[arm]]` blocks, and `state.jsonl` holds every candidate, its
paired scores, its ancestry, its gate verdicts and its spend, so an interrupted run resumes instead
of restarting. A stack exits nonzero as soon as one case fails, which is data: its records are
archived first and scored whatever the exit code was, and only a stack that wrote no record is an
error.

```toml
name = "digest-routing"
base = "origin/main"
suites = ["report_digest", "skill_routing", "daily_brief"]
cases = ["report-digest-attributed-findings", "report-digest-outside-companies"]
controls = ["board-visual-narrative", "forecast-assumption-model"]
unscorable = ["connector-composio-install"]
modules = [
  "extensions/report_digest/ufo_ext_report_digest/skills/report-digest/SKILL.md",
  "packs/assistant_hosted/ufo_pack_assistant_hosted.py",
]
iterations = 6
minibatch_size = 2
budget_usd = 180.0

[template]
database = { url = "postgresql+asyncpg://ufo:ufo@127.0.0.1:5541/ufo" }
blob = { backend = "filesystem", root = "./blobs" }
connect = { public_base_url = "http://evals.invalid" }
pack = { name = "assistant_eval" }
```

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

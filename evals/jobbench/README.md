# JobBench Boundary

## Corpus

JobBench Boundary is a harness diagnostic over [JobBench](https://job-bench.github.io/)
(arXiv:2605.26329): 128 professional tasks across 35 occupations, sourced from the WorkBank
worker-desire survey, so every case is work professionals want delegated. Each task is a dossier
of heterogeneous reference files with deliberate cross-source tensions; grading is fact-anchored
weighted binary rubrics. The corpus is unsaturated (top published model score: 57.4%), which makes
it a capability-boundary probe for the full chat surface: dossier in, durable `share_file`
deliverables out.

The builder pins `JobBench/job-bench` revision `934d4c93542c24926d0957ed6feecd6bbd922c41` (MIT),
including both split parquets' byte sizes, SHA-256 digests, Arrow schema, and row counts — `main`
(65 tasks, hidden web-research materials) and `easy` (63 tasks, local-only smoke split). Upstream
asset URL columns name `main`; the builder verifies those URLs against their relative paths, then
fetches the same paths at the pinned revision.

The repository contains only [pin metadata](data/upstream.json) and an
[upstream notice](data/NOTICE.md). It contains no JobBench prompts, rubrics, task cards, or
reference files.

## Build

Download the pinned parquets once:

```bash
mkdir -p .local/jobbench/assets
curl --fail --location \
  --output .local/jobbench/assets/tasks.parquet \
  https://huggingface.co/datasets/JobBench/job-bench/resolve/934d4c93542c24926d0957ed6feecd6bbd922c41/tasks.parquet
curl --fail --location \
  --output .local/jobbench/assets/easy.parquet \
  https://huggingface.co/datasets/JobBench/job-bench/resolve/934d4c93542c24926d0957ed6feecd6bbd922c41/easy.parquet
```

Build a one-case smoke snapshot (the first `easy` case) with its references materialized:

```bash
uv run --with pyarrow python -m evals.jobbench.build \
  --tasks-parquet .local/jobbench/assets/tasks.parquet \
  --easy-parquet .local/jobbench/assets/easy.parquet \
  --smoke-count 1
```

The command prints the content-addressed snapshot directory under `.local/jobbench/snapshots/`.
Every snapshot contains all 128 case records; only the chosen cases' assets are materialized under
`objects/sha256/`. `--case CASE_ID` (repeatable, split-qualified ids such as
`easy.bookkeeping_accounting_and_auditing_clerks__task1`) selects explicit cases, `--cases FILE`
reads a JSON id list, `--all-assets` stages all 128, and `--asset-root PATH` reads an
already-downloaded dataset tree instead of the network. Every mode verifies the parquet pins and
fails on size, digest, schema, row, URL, or corpus-shape drift.

## Run

JobBench measures the product surface, not a bespoke bundle: run under the default assistant
packs, which already carry document production plus web research. Set `[pack] name =
"assistant_hosted"` (managed backends) or `"assistant"` (local index and carrier), run
`ufo serve`, then:

```bash
uv run python -m evals \
  --jobbench .local/jobbench/snapshots/SNAPSHOT_DIGEST \
  --jobbench-case easy.bookkeeping_accounting_and_auditing_clerks__task1
```

Omit `--jobbench-case` to run every materialized case. Each case opens a fresh conversation,
stages its dossier beneath `references/`, and preserves the upstream prompt ahead of a fixed
submission envelope. `main` cases are marked web-dependent: their prompts require locating
upstream materials on the open web. The in-run verdict asserts durable capture only — every
deliverable submitted with `share_file`, saved with its digest under
`--jobbench-submissions` (default `.local/jobbench/submissions/CASE_ID/`) for the grading step.
JobBench runs wait up to 30 minutes for a professional deliverable turn. Cases hold their staged
dossiers in memory: run the full corpus in batches, not one invocation.

## Grade

Grade one captured case against its pinned rubric with one declared judge command. The judge
receives `{"system": "...", "prompt": "..."}` on standard input and returns one structured verdict
on standard output; give `name` the stable provider and model revision identity used for the run.
Judging mirrors upstream JobBench: every rubric item is judged over the full submission text
views, a rubric passes only when all of its anchored criteria pass, and the case score is the
weighted sum of passed rubrics.

```bash
uv run python -m evals.jobbench.grading \
  --snapshot .local/jobbench/snapshots/SNAPSHOT_DIGEST \
  --case easy.bookkeeping_accounting_and_auditing_clerks__task1 \
  --submission .local/jobbench/submissions/easy.bookkeeping_accounting_and_auditing_clerks__task1 \
  --judge .local/jobbench/judge.json \
  --out .local/jobbench/grades/easy.bookkeeping_accounting_and_auditing_clerks__task1.json
```

Submission views convert `xlsx`/`pdf`/`docx` and plain-text formats to text (200K-character cap
per file, matching upstream); a file that cannot be converted is judged as an explicit unreadable
placeholder rather than silently dropped. A judge transport or format failure zeroes that rubric
and records the error in the scorecard.

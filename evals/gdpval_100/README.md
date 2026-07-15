# GDPval Boundary-100

## Corpus

GDPval Boundary-100 is a harness diagnostic selected from all 220 GDPval tasks after controlled
full-harness and ablation runs. The selected task IDs are an output of that experiment, not a
checked-in corpus assumption.

The builder pins `openai/gdpval` revision
`11e7900cdcac61bc4daf59e65feb238acda98fbf`, including the parquet's byte size, SHA-256, Arrow
schema, 220 rows, 44 occupations, prompts, rubrics, and asset paths. Upstream asset URL columns
name `main`; the builder verifies those URLs against their relative paths, then fetches the same
paths at the pinned revision.

The repository contains only [pin metadata](data/upstream.json) and an
[upstream notice](data/NOTICE.md). It contains no GDPval prompts, rubrics, reference files, or gold
deliverables. The upstream dataset does not declare a license.

## Build

Download the pinned parquet once:

```bash
mkdir -p .local/gdpval-100/assets
curl --fail --location \
  --output .local/gdpval-100/assets/train.parquet \
  https://huggingface.co/datasets/openai/gdpval/resolve/11e7900cdcac61bc4daf59e65feb238acda98fbf/data/train-00000-of-00001.parquet
```

Build a one-case smoke snapshot, including that case's references and gold deliverables:

```bash
uv run --with pyarrow python -m evals.gdpval_100.build \
  --parquet .local/gdpval-100/assets/train.parquet \
  --smoke-count 1
```

The command prints the content-addressed snapshot directory under
`.local/gdpval-100/snapshots/`. Every snapshot contains all 220 case records. Only assets for the
chosen cases are materialized under `objects/sha256/`; `.local/` is gitignored.

Use a pilot-generated JSON list of task IDs for a candidate set:

```bash
uv run --with pyarrow python -m evals.gdpval_100.build \
  --parquet .local/gdpval-100/assets/train.parquet \
  --asset-cases .local/gdpval-100/candidate-task-ids.json
```

`--asset-case TASK_ID` selects explicit cases and may repeat. `--all-assets` stages all 220 tasks.
`--asset-root PATH` reads an already-downloaded dataset tree instead of using the network. Every
mode verifies the parquet pin and fails on size, digest, schema, row, URL, or corpus-shape drift.

## Calibrate

The four treatment packs form a 2x2 capability matrix:

| Treatment | Document production | Web research |
|---|---:|---:|
| `gdpval_core` | no | no |
| `gdpval_documents` | yes | no |
| `gdpval_research` | no | yes |
| `gdpval_full` | yes | yes |

Set `[pack] name` to the treatment, run `ufo serve`, then execute one independently checkpointable
case as a smoke test:

```bash
uv run python -m evals \
  --gdpval-100 .local/gdpval-100/snapshots/SNAPSHOT_DIGEST \
  --gdpval-treatment gdpval_full \
  --gdpval-task TASK_ID
```

Omit `--gdpval-task` to run every materialized case. Each case opens a fresh conversation, streams
its inputs beneath `references/` before admission, and preserves the upstream prompt before a fixed
submission envelope. Only artifacts delivered with `share_file` are captured. Reports retain their
durable blob keys, sizes, and SHA-256 digests even when an artifact exceeds the generic in-memory
inspection cap. GDPval runs wait up to 30 minutes for a professional deliverable turn; this is an
observer deadline, not a shorter agent round limit.

## Grade

Grade one local pairwise request with a declared three-judge panel. The command takes the task
instruction, rubric IDs, criteria, and weights from the pinned snapshot, then reads prepared
submission and parser views from the request. It does not run the target agent or fetch artifacts
from a report viewer. `--judges` is a JSON array of exactly three
`{"name": "...", "argv": ["..."], "timeout_seconds": 300}` objects. Each command receives a
`{"system": "...", "prompt": "..."}` object on standard input and returns one structured judge
verdict on standard output. Give each `name` the stable provider and model revision identity used
for the run:

```bash
uv run python -m evals.gdpval_100.grading \
  --snapshot .local/gdpval-100/snapshots/SNAPSHOT_DIGEST \
  --request .local/gdpval-100/grading/pairwise-request.json \
  --judges .local/gdpval-100/grading/judge-panel.json \
  --out .local/gdpval-100/grading/task-grade.json
```

## Select

Once all 220 tasks have measurements from the treatment and anchor runs, solve the fixed quotas and
assign the public 60/20/20 folds with a pinned seed:

```bash
uv run python -m evals.gdpval_100.selection \
  --measurements .local/gdpval-100/measurements.json \
  --fold-seed gdpval-boundary-100-v1 \
  --out .local/gdpval-100/selection.json
```

The selection output contains the chosen 100 task IDs, fold labels, utilities, and rejection
reasons. Extract its IDs into the flat list consumed by `--asset-cases`, then build their asset
snapshot:

```bash
jq '[.selected[].id]' \
  .local/gdpval-100/selection.json \
  > .local/gdpval-100/selected-task-ids.json

uv run --with pyarrow python -m evals.gdpval_100.build \
  --parquet .local/gdpval-100/assets/train.parquet \
  --asset-cases .local/gdpval-100/selected-task-ids.json
```

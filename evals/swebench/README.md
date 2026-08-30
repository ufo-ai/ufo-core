# SWE-bench Verified

One snapshot carries the whole pinned roster — 66 cases in four subsets, graded by the unmodified
`swebench==4.1.0` harness. Docker must be running.

| Subset | Cases | Purpose | Images to pull |
| --- | --- | --- | --- |
| `smoke` | 3 | the frozen comparability set; every historical score is against these | 4 GB |
| `hillclimb` | 10 | the climb target: one case per repository, seeded draw | 14 GB |
| `holdout` | 10 | validates a completed climb | 15 GB |
| `hard` | 45 | every Verified row labeled `1-4 hours` or `>4 hours` | Varies |

Sizes are compressed registry bytes; unpacked images take more. Run `docker system df` before a
pass and `--prune-images` after one, so the hillclimb images are gone before the holdout pass pulls
its own. Holdout validates a completed climb; it never appears in an optimize or experiment file —
the tools refuse it.

The roster is `evals/swebench/data/upstream.json`. `smoke` is frozen. `hillclimb` and `holdout` are
a seeded, repository-stratified round-robin over every row outside `smoke` and outside the
`>4 hours` label; `hard` independently contains every row estimated above one hour. The frozen
representative roster overlaps `hard` by two cases, and combined runs execute each once. Within a repository, instances rank by
`sha256(seed + "\0" + instance_id)`; repositories take turns, largest pool first. The checked-in ids
are the pin and the algorithm is the enforcement — `build` recomputes the split and refuses a
snapshot that disagrees.

```bash
PARQUET=.local/swebench/assets/test.parquet
mkdir -p "$(dirname "$PARQUET")"
curl -fL \
  https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified/resolve/c104f840cc67f8b6eec6f759ebc8b2693d585d4a/data/test-00000-of-00001.parquet \
  -o "$PARQUET"
uv run --with pyarrow python -m evals.swebench.build \
  --parquet "$PARQUET" \
  --output .local/swebench/snapshot

RUN_TAG=$(date -u +%Y%m%dT%H%M%SZ)-$(uuidgen | tr '[:upper:]' '[:lower:]')
SUBMISSIONS=.local/swebench/submissions/$RUN_TAG-ufo
uv run python -m evals \
  --swebench \
  --swebench-subset hillclimb \
  --swebench-submissions "$SUBMISSIONS" \
  --label swebench-hillclimb

uv run --with swebench==4.1.0 python -m evals.swebench.grading \
  --subset hillclimb \
  --submissions "$SUBMISSIONS" \
  --run-id "$RUN_TAG-ufo" \
  --prune-images
```

Run every pinned case against a deployed workspace with bounded parallelism:

```bash
uv run python -m evals \
  --swebench \
  --swebench-subset all \
  --remote \
  --fresh-workspace \
  --concurrency 66
```

For a model-comparison run, start an isolated service from a config that sets both
`models.auto_model` and `models.subagent_models.coding` to the target model. The latter is scoped to
that service; the product coding profile remains pinned to Opus.

`--fresh-workspace` derives a new workspace from the run id, founds its admin and main agent through
hosted onboarding, and records the workspace id with the run. It requires
`UFO_ONBOARD_CONTROL_TOKEN` and `connect.public_base_url`. Pass `--workspace <workspace-id>` instead
to reuse an existing workspace.

`--swebench` requires `--swebench-subset` or `--swebench-case`; `all` selects the complete pinned
roster. Each subset runs as its own
task (`swebench_verified.smoke`, `.hillclimb`, `.holdout`, `.hard`). Grading takes the same pair: `--subset`
grades a whole subset, `--case` grades named instances of one subset. A case's digest tag is keyed
on the parquet digest, not the snapshot digest, so a case's score stays comparable across snapshot
versions that add or drop other cases.

Grade the complete captured roster with `--subset all`.

To regenerate the roster and check every pick against its prebuilt image:

```bash
uv run --with pyarrow python -m evals.swebench.selection --parquet "$PARQUET"
```

It prints the block `data/upstream.json` must carry. A difference is a review question, never an
automatic rewrite: the pinned ids are what every recorded score was measured on.

The immutable snapshot is under `.local/swebench/snapshot.versions/`, captured patches are under the
explicit submissions path, and each grade is retained under
`.local/swebench/grades/<subset>/<run-id>/`. That grade directory contains predictions when
applicable, native `logs/run_evaluation/`, the official aggregate report, and `summary.json`;
official output files are not moved or rewritten. Grading runs on the official prebuilt evaluation
images from the `swebench` Docker Hub namespace — the harness's stock source. The wrapper verifies
an exact `linux/amd64` image with an official repository digest or pulls it, grades that case, then
aggregates the native reports. An unresolved nonempty prediction runs the official gold patch in
the same image; if gold also fails, grading stops without a summary. Prediction generation omits
candidate changes to files owned by the case's official test patch, so agent-authored tests cannot
prevent the grader tests from applying;
the captured patch remains unchanged. Local builds are not an option: the pinned specs have drifted from the world
(sympy's deleted `1.7` branch, pip 25.3 dropping `--no-use-pep517`), while the prebuilt images were
frozen before that drift.

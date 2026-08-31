# Coding evals

This records the setup, execution, grading, and measured results for remote coding evals with
`z-ai/glm-5.3-flash`.

## Work list

Ordered by leverage; 1–3 are prerequisites that roughly double usable samples per dollar.

1. Root-cause the "coding delegation timed out waiting for a database connection" failure class.
   It voided 14 of the 17 ungraded SWE-bench cases. ROOT-CAUSED 2026-08-30: `TURN_QUEUE` has no
   `worker_concurrency`, so ONE serve pod claimed all 66 parent turns plus their 66 coding
   children; ~130 simultaneous turn startups saturated that process's CPU for ~7 minutes, checkout
   waits on the `ufo_app` pool (5+10, 10s timeout) blew past the timeout, and every affected spawn
   returned `TimeoutError: QueuePool limit of size 5 overflow 10 reached`. The database was healthy
   throughout (CPU <=32%, sub-ms I/O, 155/397 connections) and the second pod idle; the near-idle
   owner pool on the same host stretched with the busy one, which is what convicts process CPU
   rather than pool demand. An in-process rig (core/tests/loop/test_turn_burst_probe.py) clears the
   core turn machinery: 64 concurrent turn+spawn trees peak at 244 ms acquire, so the weight is in
   what production turns add (pack, sandbox create, streams, model rounds). FIXED on branch
   `worktree-coding-evals-db-timeout`: `MEMBER_TURN_CONCURRENCY = 8` gates member turns per process
   (spawned children exempt — a gated child could wait forever on slots its ancestors hold; DBOS
   `worker_concurrency` is unusable here: refused beside per-conversation `concurrency=1` and
   counted per partition), `serve_replicas` is a per-env template variable (testing 4, prod 2
   unchanged), `MAX_OVERFLOW` 10 -> 5 keeps the 4-pod ceiling at 348 of 397, monitors re-derived,
   `ufo.turn_slot_wait_ms` shows the queueing the gate introduces. Probe with the gate: 64
   turn+spawn trees, worst acquire 244 ms -> 70 ms, all done. Autoscaling filed as #2736. MERGED
   as #2737 (main 3ade40119), deployed to testing (4 pods), and validated live: a
   `coding_subagent` remote batch at concurrency 12 placed turn workflows on all 4 executors, and
   every joinable parent-to-child pair ran cross-host with the children completing (DBOS
   `workflow_status.executor_id` joined to `turn.parent_turn_id`). Remaining: py-spy the claiming
   pod during the next run start to name the hot startup work.
2. Recalibrate the `coding_profile` deterministic scorers (`evals/suites/coding_subagent.py`
   `PROFILE_CASES`). Manual review found 15/21 correct; the scorers credited 9/21 because six
   correct answers lacked required literal phrases. DONE as PR #2741: the twenty decision cases
   keep the tool ban deterministic and judge decision content as rubric criteria derived from the
   same requirement declarations. Calibrated on the recorded baseline: old scorer agreed with a
   human read on 4/12 completed decisions, the judged rubrics on 12/12 (all eight flips verified
   by reading the responses; the one kept failure is genuine).
3. Record the missing baselines after item 1 lands: a full GLM SWE-bench run (the GLM column is
   empty) and a clean Terminal-Bench rerun.
4. When PR #2729 merges, drive ablation arms through `x-ufo-environment` against one shared stack
   (prompt and tool-description overrides per turn) instead of one isolated stack per arm.
5. Build a deliverable-verification eval from the incomplete-deliverable failures (9 of GLM's 18
   valid Terminal-Bench failures were missing output files), then hill-climb on it. Single prompt
   clauses are a measured dead end: the five text variants below and the wait-budget clause
   (#2731) all failed the ablation bar.

## Remote runtime

`--remote` admits each case through `ufo --remote --json` at `connect.public_base_url`. The current
`ufo` binary must be on `PATH`, and `UFO_TOKEN_SECRET` must match the service. `--model` selects the
model for the main agent, extension agents, and spawned profiles in that turn tree.

The runner records and checks the server's revision, image, config, sandbox templates, model, and
reasoning. A missing or mixed runtime identity stops the run before scores are recorded.

Hosted fresh workspaces also require `UFO_ONBOARD_CONTROL_TOKEN`:

```bash
uv run python -m evals \
  --remote \
  --model z-ai/glm-5.3-flash \
  --fresh-workspace \
  --label remote-glm
```

`--workspace <workspace-id>` reuses an existing workspace.

## SWE-bench Verified

The snapshot contains 66 pinned cases and uses the unmodified `swebench==4.1.0` harness.

| Subset | Cases |
| --- | ---: |
| `smoke` | 3 |
| `hillclimb` | 10 |
| `holdout` | 10 |
| `hard` | 45 |

Build the pinned snapshot:

```bash
PARQUET=.local/swebench/assets/test.parquet
mkdir -p "$(dirname "$PARQUET")"
curl -fL \
  https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified/resolve/c104f840cc67f8b6eec6f759ebc8b2693d585d4a/data/test-00000-of-00001.parquet \
  -o "$PARQUET"
uv run --with pyarrow python -m evals.swebench.build \
  --parquet "$PARQUET" \
  --output .local/swebench/snapshot
```

Run the complete remote roster:

```bash
RUN_TAG=$(date -u +%Y%m%dT%H%M%SZ)-$(uuidgen | tr '[:upper:]' '[:lower:]')
SUBMISSIONS=.local/swebench/submissions/$RUN_TAG-ufo

uv run python -m evals \
  --swebench \
  --swebench-subset all \
  --swebench-submissions "$SUBMISSIONS" \
  --remote \
  --model z-ai/glm-5.3-flash \
  --fresh-workspace \
  --concurrency 66
```

Grade captured patches with the official harness:

```bash
uv run --with swebench==4.1.0 python -m evals.swebench.grading \
  --subset all \
  --submissions "$SUBMISSIONS" \
  --run-id "$RUN_TAG-ufo" \
  --prune-images
```

The immutable snapshot is stored under `.local/swebench/snapshot.versions/`. Captured patches are
stored under the supplied submissions path. Official reports and logs are stored under
`.local/swebench/grades/<subset>/<run-id>/`.

## Terminal-Bench 2.1

The roster contains 89 tasks pinned to Harbor dataset revision 6 and content hash
`sha256:7d7bdc1cbedad549fc1140404bd4dc45e5fd0ea7c4186773687d177ad3a0699a`. Runs use
Harbor 0.21.0 and require `E2B_API_KEY` for the E2B environment.

Build the static x86-64 client uploaded to each Harbor environment:

```bash
uv run python -m evals.terminal_bench.setup
```

Run a selected batch:

```bash
uv run python -m evals \
  --terminal-bench \
  --terminal-bench-case write-compressor \
  --terminal-bench-case openssl-selfsigned-cert \
  --terminal-bench-case regex-log \
  --terminal-bench-case build-pov-ray \
  --remote \
  --model z-ai/glm-5.3-flash \
  --workspace <workspace-id> \
  --concurrency 4
```

Omitting `--terminal-bench-case` runs all 89 tasks. The outer `--remote` selects the remote Harbor
environment and public ufo service. The client inside Harbor uses `--json` without `--remote`, so
its tools remain attached to the filesystem and services that Harbor grades. The attached client
still sends `--model` to the public service.

Harbor retains each job under `.local/terminal_bench/jobs/`:

```text
<job>/result.json
<job>/<trial>/result.json
<job>/<trial>/verifier/reward.txt
<job>/<trial>/verifier/test-stdout.txt
<job>/<trial>/verifier/ctrf.json
```

`reward.txt` and the Harbor verifier output are the score. `exception_info` in the trial result
records errors that occurred before a valid grade.

## Coding profile

The coding profile runs through the same remote transport and targets the production coding
profile explicitly:

```bash
uv run python -m evals \
  --remote \
  --only coding_profile \
  --agent profile:coding \
  --model z-ai/glm-5.3-flash \
  --workspace <workspace-id> \
  --label coding-profile
```

The member turn foreground-spawns `profile:coding` with the case objective. The grader reads the
child's validated result. Run records are immutable JSON under `eval-reports/runs/`; the viewer is
`eval-reports/index.html`.

## Ablations

`python -m evals.ablate` runs an unchanged control and every text variant on separate git
worktrees, virtual environments, databases, blob roots, ports, and workspaces. `remote = true`
admits every arm through `ufo --remote --json` while keeping one isolated stack per arm.

```toml
name = "validation-scope-glm"
base = "56fdc6680dd2a9b104af2142e15a429709277983"
suites = ["coding_profile"]
cases = [
  "coding-subagent-local-validation-scope",
  "coding-subagent-cross-cutting-validation-scope",
]
agent = "profile:coding"
model = "z-ai/glm-5.3-flash"
repeats = 1
concurrency = 2
max_stacks = 2
remote = true
budget_usd = 1.0
est_usd_per_case = 0.10

[template]
database = { url = "postgresql+asyncpg://ufo:ufo@127.0.0.1:5541/ufo" }
blob = { backend = "filesystem", root = "./blobs" }
browser = { cdp_provider = "browserbase" }
connect = { public_base_url = "http://evals.invalid" }
memory = { index_backend = "turbopuffer" }
pack = { name = "assistant_hosted" }
research = { search_provider = "perplexity" }

[[arm]]
name = "candidate"

[[arm.replacements]]
path = "extensions/coding/ufo_ext_coding/prompts/subagent_coding.md"
old = '''<exact control text>'''
new = '''<candidate text>'''
```

Run the experiment:

```bash
source .local/swebench/remote/runtime.env
export OPENAI_API_KEY="$UFO_OPENAI_API_KEY"
export ANTHROPIC_API_KEY="$UFO_ANTHROPIC_API_KEY"
uv run python -m evals.ablate .local/experiments/<experiment>.toml
```

Reports, arm records, and stack logs are written to
`eval-reports/experiments/<experiment>/`. Verdicts compare sample counts within the same paired
experiment. A case is marked changed when the equal-sized arms differ by at least two samples, a
case fully fails, or a case is fully fixed. Excluded samples are named as record gaps rather than
scored.

## Grading categories

| Category | Recorded state |
| --- | --- |
| Resolved | Official reward passes and `exception_info` is empty. |
| Valid model failure | The official verifier runs and returns a failing reward. |
| Operational failure | The trial records a timeout, provider error, sandbox error, cancellation, or missing agent result. |
| Harness failure | Official verifier output or required grading artifacts are missing or unreadable. |

The strict pass rate divides passes by all attempted cases. The valid-outcome rate divides passes
by passes plus valid model failures.

## Measured results

Measurements below use `z-ai/glm-5.3-flash`.

### Remote wait behavior

Testing revision `56fdc6680dd2a9b104af2142e15a429709277983` completed a command that slept for
300 seconds and wrote a file. The remote turn finished in 349 seconds with one command start and
one completion check.

### Terminal-Bench

Job `ufo-20260830T060529Z-d02475fa` attempted 63 tasks:

| Result | Cases |
| --- | ---: |
| Resolved | 29 |
| Valid model failure | 18 |
| Operational failure | 16 |

The strict pass rate is 29/63, or 46.0%. The valid-outcome rate is 29/47, or 61.7%.

The 18 valid failures contained nine incomplete or unverified deliverables, five algorithmic
errors, two performance failures, and two precision failures.

Job `ufo-20260830T214501Z-a6334c4f` ran the four-case batch after the wait change:

| Case | Result |
| --- | --- |
| `build-pov-ray` | Passed all three official checks, including source provenance. |
| `openssl-selfsigned-cert` | `AgentTimeoutError`; verifier did not run. |
| `regex-log` | `AgentTimeoutError`; verifier did not run. |
| `write-compressor` | `AgentTimeoutError`; verifier did not run. |

`build-pov-ray` failed its source-provenance check in the comparison run and passed it in this run.
The other three cases produced no graded comparison.

### Coding profile

Across 21 completed coding-profile decisions, manual review found 15 substantively correct answers
(71.4%). The deterministic scorer recorded 9/21 (42.9%). Six additional cases had no completed
decision, giving a strict manual result of 15/27 (55.6%) and a strict deterministic result of 9/27
(33.3%). Six manually correct answers omitted literal phrases required by their deterministic
scorers.

### Text variants

| Variant | Measured result |
| --- | --- |
| Completion-contract wording | No target improvement; neighboring artifact cases regressed. |
| Early-artifact checkpoint wording | No improvement or neighboring regressions. |
| Behavior-reach wording | Cross-cutting validation improved 0/1 to 1/1; local validation regressed 1/1 to 0/1. |
| Narrowest-scope wording | Both control and variant scored 0/1 cross-cutting and 1/1 local. |
| Exact-objective wording | Consumer-boundary case regressed 1/1 to 0/1; paired local case stayed 0/1. |

None of these text variants changed the repository prompt or Skill.

## Appendix: per-case results

`Pass` and `Fail` are official harness outcomes. `Error` means no valid outcome was produced. `—`
means that model has no result for the case in the retained comparison run.

The Opus columns below come from deployed-default runs whose records predate runtime model
attestation and whose client commands omitted `--model`. The label records the deployed coding
default used for those runs, not a model identifier serialized in the artifacts. GLM identifies
the explicit `--model z-ai/glm-5.3-flash` run.

### SWE-bench Verified

Run `91d9a339-b66e-4827-9e2d-a91973e9484f` covered all 66 roster cases with Opus. Of those, 49
patches reached the official harness: 41 passed and 8 failed. The other 17 cases produced no
official grade. No complete GLM SWE-bench run is retained, so the GLM column is unrecorded.

#### `smoke`

| Case | Opus | GLM | Failure note |
| --- | --- | --- | --- |
| `django__django-10097` | Fail | — | Seven `FAIL_TO_PASS` authentication-template tests failed. |
| `sympy__sympy-20590` | Pass | — | |
| `scikit-learn__scikit-learn-25102` | Error | — | No official grade: coding delegation timed out waiting for a database connection. |

#### `hillclimb`

| Case | Opus | GLM | Failure note |
| --- | --- | --- | --- |
| `django__django-16877` | Pass | — | |
| `sympy__sympy-21612` | Pass | — | |
| `sphinx-doc__sphinx-7440` | Pass | — | |
| `matplotlib__matplotlib-24570` | Error | — | No official grade: coding delegation timed out waiting for a database connection. |
| `scikit-learn__scikit-learn-11578` | Pass | — | |
| `astropy__astropy-7336` | Pass | — | |
| `pydata__xarray-4629` | Pass | — | |
| `pytest-dev__pytest-6197` | Pass | — | |
| `pylint-dev__pylint-7080` | Pass | — | |
| `psf__requests-1921` | Fail | — | `PASS_TO_PASS` regression in `test_cookie_quote_wrapped`. |

#### `holdout`

| Case | Opus | GLM | Failure note |
| --- | --- | --- | --- |
| `mwaskom__seaborn-3069` | Pass | — | |
| `pallets__flask-5014` | Pass | — | |
| `django__django-15022` | Pass | — | |
| `sympy__sympy-11618` | Pass | — | |
| `sphinx-doc__sphinx-8621` | Pass | — | |
| `matplotlib__matplotlib-24870` | Error | — | No official grade: coding delegation timed out waiting for a database connection. |
| `scikit-learn__scikit-learn-10297` | Pass | — | |
| `astropy__astropy-12907` | Pass | — | |
| `pydata__xarray-4075` | Pass | — | |
| `pytest-dev__pytest-5840` | Pass | — | |

#### `hard`

| Case | Opus | GLM | Failure note |
| --- | --- | --- | --- |
| `astropy__astropy-13398` | Error | — | No official grade: coding delegation timed out waiting for a database connection. |
| `astropy__astropy-13579` | Error | — | No official grade: coding delegation timed out waiting for a database connection. |
| `astropy__astropy-14369` | Error | — | No official grade: coding delegation timed out waiting for a database connection. |
| `django__django-10554` | Pass | — | |
| `django__django-11138` | Pass | — | |
| `django__django-11400` | Pass | — | |
| `django__django-11885` | Pass | — | |
| `django__django-12325` | Pass | — | |
| `django__django-12708` | Error | — | No official grade: the turn produced no terminal transcript. |
| `django__django-13128` | Error | — | No official grade: coding delegation timed out waiting for a database connection. |
| `django__django-13212` | Fail | — | Two validator placeholder `FAIL_TO_PASS` tests failed. |
| `django__django-13344` | Pass | — | |
| `django__django-13449` | Pass | — | |
| `django__django-13837` | Error | — | No official grade: coding delegation timed out waiting for a database connection. |
| `django__django-14007` | Pass | — | |
| `django__django-14011` | Pass | — | |
| `django__django-14631` | Pass | — | |
| `django__django-15128` | Pass | — | |
| `django__django-15268` | Pass | — | |
| `django__django-15503` | Error | — | No official grade: coding delegation timed out waiting for a database connection. |
| `django__django-15629` | Error | — | No official grade: coding delegation timed out waiting for a database connection. |
| `django__django-15957` | Error | — | No official grade: the turn timed out. |
| `django__django-16263` | Fail | — | `test_unused_aliased_aggregate_pruned` failed. |
| `django__django-16560` | Pass | — | |
| `django__django-16631` | Pass | — | |
| `pydata__xarray-3993` | Pass | — | |
| `pydata__xarray-6992` | Pass | — | |
| `pylint-dev__pylint-4551` | Pass | — | |
| `pylint-dev__pylint-8898` | Pass | — | |
| `pytest-dev__pytest-10356` | Error | — | No official grade: coding delegation timed out waiting for a database connection. |
| `pytest-dev__pytest-5787` | Fail | — | `PASS_TO_PASS` regression in `test_deserialization_failure`. |
| `sphinx-doc__sphinx-11510` | Pass | — | |
| `sphinx-doc__sphinx-7590` | Error | — | No official grade: coding delegation timed out waiting for a database connection. |
| `sphinx-doc__sphinx-8548` | Error | — | No official grade: coding delegation timed out waiting for a database connection. |
| `sphinx-doc__sphinx-9229` | Pass | — | |
| `sphinx-doc__sphinx-9461` | Fail | — | `test_properties` failed. |
| `sympy__sympy-12489` | Pass | — | |
| `sympy__sympy-13852` | Fail | — | `test_polylog_values` failed. |
| `sympy__sympy-13878` | Error | — | No official grade: coding delegation timed out waiting for a database connection. |
| `sympy__sympy-14248` | Pass | — | |
| `sympy__sympy-16597` | Fail | — | Three infinity-related `FAIL_TO_PASS` tests failed. |
| `sympy__sympy-17630` | Pass | — | |
| `sympy__sympy-18199` | Error | — | No official grade: coding delegation timed out waiting for a database connection. |

### Terminal-Bench 2.1

The retained comparison consists of two 64-task selections, not two complete 89-task roster runs.
Each selection retained 63 trial results; `install-windows-3-11` was selected in both but retained
no trial result. The Opus job is `ufo-20260828T191753Z-f9dfc30e`: 30 passes, 14 valid failures,
and 19 trial errors. The GLM job is `ufo-20260830T060529Z-d02475fa`: 29 passes, 18 valid failures,
and 16 trial errors.

| Case | Opus | GLM | Failure note |
| --- | --- | --- | --- |
| `write-compressor` | Fail | Error | Opus: the compressed output was missing. GLM: agent timed out. |
| `torch-tensor-parallelism` | Error | Error | Opus: verifier timed out. GLM: agent timed out. |
| `schemelike-metacircular-eval` | Pass | Error | GLM: agent timed out. |
| `kv-store-grpc` | Pass | Pass | |
| `pypi-server` | Pass | Pass | |
| `dna-assembly` | Pass | Fail | GLM: `/app/primers.fasta` was missing. |
| `torch-pipeline-parallelism` | Pass | Error | GLM: agent timed out. |
| `qemu-alpine-ssh` | Pass | Pass | |
| `openssl-selfsigned-cert` | Pass | Pass | |
| `regex-chess` | Fail | Fail | Both runs were missing `/app/re.json`. |
| `log-summary-date-ranges` | Pass | Pass | |
| `model-extraction-relu-logits` | Pass | Error | GLM: agent timed out. |
| `path-tracing` | Pass | Error | GLM: agent timed out. |
| `regex-log` | Pass | Pass | |
| `caffe-cifar-10` | Pass | Pass | |
| `mteb-leaderboard` | Fail | Pass | Opus: `/app/result.txt` was missing. |
| `llm-inference-batching-scheduler` | Pass | Error | GLM: agent timed out. |
| `pytorch-model-recovery` | Pass | Pass | |
| `circuit-fibsqrt` | Error | Fail | Opus: provider concurrency rate limit. GLM: circuit tests failed. |
| `merge-diff-arc-agi-task` | Pass | Pass | |
| `build-pov-ray` | Pass | Fail | GLM: required POV-Ray 2.2 source-provenance files were missing. |
| `cobol-modernization` | Error | Pass | Opus: provider concurrency rate limit. |
| `mcmc-sampling-stan` | Pass | Pass | |
| `gpt2-codegolf` | Fail | Error | Opus: `/app/gpt2.c` was missing. GLM: agent timed out. |
| `filter-js-from-html` | Fail | Pass | Opus: the filter modified benign input. |
| `sam-cell-seg` | Fail | Fail | Opus: required mask files were missing. GLM: the mask remained rectangular and IoU was below 0.5. |
| `mteb-retrieve` | Fail | Fail | Both runs returned HumanEval instead of MTEB. |
| `adaptive-rejection-sampler` | Fail | Error | Opus: required modules and validation functions were missing. GLM: agent timed out. |
| `vulnerable-secret` | Fail | Pass | Opus: the required results file was missing. |
| `extract-elf` | Pass | Pass | |
| `nginx-request-logging` | — | Fail | GLM: 10 requests/second rate limiting was not configured. |
| `make-doom-for-mips` | Error | Error | Opus: provider concurrency rate limit. GLM: agent timed out. |
| `configure-git-webserver` | — | Pass | |
| `build-cython-ext` | Pass | Pass | |
| `train-fasttext` | Fail | Fail | Both runs were missing `/app/model.bin`. |
| `compile-compcert` | Fail | Pass | Opus: the `compcert` executable was missing. |
| `fix-ocaml-gc` | Error | Pass | Opus: provider concurrency rate limit. |
| `gcode-to-text` | Pass | Pass | |
| `dna-insert` | Fail | Error | Opus: primer melting temperature was outside tolerance. GLM: sandbox was killed or timed out. |
| `raman-fitting` | — | Fail | GLM: fitted peak parameters were far outside tolerance. |
| `overfull-hbox` | Error | Fail | Opus: provider concurrency rate limit. GLM: input tokens changed beyond allowed substitutions. |
| `video-processing` | Pass | Fail | GLM: takeoff frame 224 was one frame outside tolerance. |
| `sanitize-git-repo` | Error | Fail | Opus: provider concurrency rate limit. GLM: secrets and required replacements were wrong. |
| `qemu-startup` | Pass | Pass | |
| `hf-model-inference` | Error | Pass | Opus: provider concurrency rate limit. |
| `reshard-c4-data` | Error | Fail | Opus: provider concurrency rate limit. GLM: output had 343 items; the maximum was 30. |
| `feal-differential-cryptanalysis` | Error | Error | Opus: provider concurrency rate limit. GLM: agent timed out. |
| `rstan-to-pystan` | — | Error | GLM: agent timed out. |
| `make-mips-interpreter` | — | Fail | GLM: the VM timed out and `/tmp/frame.bmp` was missing. |
| `password-recovery` | Error | Fail | Opus: provider concurrency rate limit. GLM: recovered password was truncated. |
| `sqlite-db-truncate` | — | Pass | |
| `query-optimize` | — | Fail | GLM: query was correct but slower than the golden query. |
| `cancel-async-tasks` | — | Pass | |
| `financial-document-processor` | — | Pass | |
| `install-windows-3-11` | Error | Error | Selected by Harbor in both runs, but no trial result was retained. |
| `polyglot-c-py` | — | Pass | |
| `pytorch-model-cli` | Error | Error | Opus: provider concurrency rate limit. GLM: agent timed out. |
| `db-wal-recovery` | — | Pass | |
| `largest-eigenval` | — | Fail | GLM: runtime was narrowly slower than the reference. |
| `large-scale-text-editing` | — | Pass | |
| `break-filter-js-from-html` | — | Fail | GLM: the XSS bypass failed. |
| `count-dataset-tokens` | Error | Pass | Opus: provider concurrency rate limit. |
| `extract-moves-from-video` | Fail | Error | Opus: `/app/solution.txt` was missing. GLM: agent timed out. |
| `path-tracing-reverse` | — | Error | GLM: agent timed out. |
| `chess-best-move` | Error | — | Opus: provider concurrency rate limit. |
| `prove-plus-comm` | Pass | — | |
| `git-multibranch` | — | — | |
| `multi-source-data-merger` | — | — | |
| `sqlite-with-gcov` | — | — | |
| `polyglot-rust-c` | Error | — | Opus: provider concurrency rate limit. |
| `feal-linear-cryptanalysis` | Error | — | Opus: provider concurrency rate limit. |
| `crack-7z-hash` | Error | — | Opus: provider concurrency rate limit. |
| `sparql-university` | Pass | — | |
| `protein-assembly` | Pass | — | |
| `custom-memory-heap-crash` | — | — | |
| `code-from-image` | Pass | — | |
| `fix-git` | — | — | |
| `winning-avg-corewars` | Fail | — | Opus: `/app/my_warrior.red` was missing. |
| `modernize-scientific-stack` | — | — | |
| `git-leak-recovery` | Pass | — | |
| `distribution-search` | — | — | |
| `build-pmars` | Pass | — | |
| `mailman` | Pass | — | |
| `headless-terminal` | — | — | |
| `bn-fit-modify` | Pass | — | |
| `constraints-scheduling` | Error | — | Opus: provider concurrency rate limit. |
| `tune-mjcf` | Error | — | Opus: provider concurrency rate limit. |
| `fix-code-vulnerability` | — | — | |
| `portfolio-optimization` | — | — | |

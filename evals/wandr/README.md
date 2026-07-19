# WANDR Boundary

## Corpus

WANDR Boundary is a harness diagnostic over [WANDR](https://github.com/perplexityai/wandr)
(Perplexity, Apache-2.0): 500 wide-and-deep research tasks — broad entity discovery plus
per-entity evidence, submitted as JSONL rows of `{item, url, excerpts, answer}` and graded
reference-free by each task's vendored verifier, which re-fetches every submitted URL and
validates excerpt fidelity and requirement support. Published scores are low (best system:
0.363 soft F1), which makes it a capability-boundary probe for the research loop: instruction
in, durable `share_file` results files out, evidence verified against the live web.

The checked-in [selection](selection.json) pins upstream revision
`ca82dc224d5c03a8cde5409c6ba49c1c4f67fff3` and 43 tasks by the upstream dataset manifest's
content digests: `hillclimb` and `holdout` (21 each, disjoint, 7 per difficulty label —
difficulty maps to report tiers T1/T2/T3) plus the upstream `smoke` task. `hillclimb` is the
optimization target for prompt/skill iteration; `holdout` stays unseen until a candidate is
final. The repository contains only the selection pins; task packages materialize at build time.

## Build

```bash
uv run python -m evals.wandr.build
```

Sparse-fetches the 43 pinned task packages from the upstream revision into a content-addressed
snapshot under `.local/wandr/snapshots/<digest>/`, verifying every package against its pinned
digest. Pass `--source <checkout>` to copy from an existing clone instead of fetching.

## Run

The suite runs under the default assistant packs (`[pack] name = "assistant"` or
`"assistant_hosted"`) against a running `ufoctl serve`, one real turn per case, 90-minute
deadline. Target a disposable `--workspace`.

```bash
uv run python -m evals --wandr .local/wandr/snapshots/<digest> \
  --wandr-subset smoke --workspace <uuid> --concurrency 4
```

`--wandr-subset hillclimb|holdout` runs a scored subset; `--wandr-case <task>` (repeatable)
narrows further. The in-run verdict is durable submission capture only: a case passes when every
required results file arrived through `share_file`; bytes land under `.local/wandr/submissions/`.

## Grade

Offline, re-runnable without re-running turns. Requires `OPENAI_API_KEY` (judge) and
`PERPLEXITY_API_KEY` (page re-fetch); the verifier makes paid API calls.

```bash
uv run python -m evals.wandr.grading \
  --snapshot .local/wandr/snapshots/<digest> --subset hillclimb --metric-stdout
```

Each case stages its captured bytes into a fresh workspace and runs the task's vendored
`tests/test.sh` (shared uv environment under `.local/wandr/venv`). `reward.json` is
authoritative: a case with no captured submission grades 0.0 through the verifier's own path; a
verifier fault raises instead of scoring. Per-case and per-tier soft/hard F1 land in
`.local/wandr/grades/`; `--metric-stdout` ends stdout with `soft_f1: <mean>` — the metric line
an optimizer loop (`weco run --metric soft_f1`) reads.

The optimizer eval command chains run and grade with `;` (not `&&`): a failed capture case must
still grade as zero, and `python -m evals` exits 1 on any failed case.

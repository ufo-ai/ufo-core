# compaction

The compaction suite measures what survives the context boundary. Each case is a full-scale
message window (default 152k estimated tokens, crossing the auto-compaction trigger) synthesized
from this repo's own docs and source, with generated facts spliced in. Every fact carries a
distinctive numeric literal proven absent from the window's filler at build time, so grading is a
deterministic substring check — presence after the boundary can only mean survival. No judge.

The default target is `DEFAULT_CONTEXT_WINDOW_TOKENS` less the summary's output reserve and the
autocompact buffer, so a window clears the trigger of a model declaring a 200k-token context
window. That is the scale the suite is built and calibrated at. A target agent on a longer-window
model triggers proportionally later — `claude-opus-5` declares 1M and triggers at 950k — and the
live leaves refuse such a run by name rather than reporting a compaction that never fired; point
`--agent` at an agent whose model declares a 200k-token window.

The suite is built to have a structural ceiling below a perfect score: the planted decisions
overload the summary's token budget, so the leaves measure prioritization under forced loss, not
retrieval.

| Leaf | Cases | Measures | Pass bar |
|---|---:|---|---|
| `compaction.overload` | 2 | importance-weighted recall of 120 decisions vs. a 20k-token summary budget; distractor selectivity | weighted recall ≥ 30% |
| `compaction.buried` | 1 | recall of 40 operative values stated only inside tool-result text, against 40 matched spoken decisions — the channel gap | buried recall ≥ 20% |
| `compaction.supersession` | 1 | corrected values recalled, superseded values absent | correction recall ≥ 60%, stale rate ≤ 20% |
| `compaction.chain` | 1 | survival of critical (weight ≥ 4) facts across three compaction generations, the window refilled between each | gen-3 critical survival ≥ 25% |
| `compaction.reference` | 1 | 9 load-bearing offloaded runtime files against the `MAX_REFERENCE_PATHS = 5` harvester cap, heaviest planted earliest — recency keeps the wrong five, so coverage above the mechanical floor requires the summary's own `files` field to carry the early heavy paths | weighted coverage ≥ 50% |
| `compaction.image` | 1 | a fact whose only home is an inline image in the head | fact survives the boundary |
| `compaction.behavior` | 2×4 probes | live probe turns on a materialized full-scale conversation: recall, supersession, re-read of an offloaded runtime file, and a verbatim-tail control | per probe |
| `compaction.real` | 3×4–5 probes | real agent transcripts composed into full-scale windows with spliced graded exchanges: a plan revised twice ending in a reversal-by-reference, seven versions of one deliverable whose keeper pointer flips twice, and a confident mid-run recap that two later corrections poison | sanity: the window compacts; survival recorded as metrics |

Reference evidence splits every surviving path into `harvested` (kept by the pipeline's
recency-capped durable-reference block) vs `carried` (preserved by the model in the summary's
files field), so the cap's cost is visible per run, not just in the aggregate.

`compaction.image` passes only when an image-borne fact survives compaction; the pipeline reduces
head images to `[image]` markers before summarizing, so this leaf is the standing boundary
condition on that loss channel and fails the run until the channel exists.

Artifact leaves call `Compaction.maybe_compact` directly with the agent's model and grade the
rendered replacement message; every compaction's `before`/`after`/`summary` record persists in the
blob store for audit. The behavior leaf opens a real conversation, runs a seed turn, swaps the
transcript blob for the fixture window, and drives probe turns through the engine — compaction
fires on the first probe exactly as in production. Chain metrics (`survival_gen1..3`) and leaf
rates surface as report metrics in stdout and the viewer.

Cost: each artifact generation and each behavioral first-probe summarizes a ~150k-token head with
the target model; a full run is on the order of 2M input tokens.

## Build the snapshot

```bash
SNAPSHOT="$PWD/.local/compaction/snapshot"
uv run python -m evals.compaction.build --out "$SNAPSHOT"
```

The builder harvests `spec.md`, `README.md`, `docs/**/*.md`, and `core/src/ufo/**/*.py` from the
checkout it runs in (`--repo` overrides), so the snapshot digest moves with the harvested
material. Reports are comparable only across runs of the byte-identical snapshot; keep the
snapshot directory with the runs it graded.

## Real cases

`--transcripts DIR` adds the `compaction.real` cases: each composes two real agent transcripts
(exported conversation blobs, `<conversation-id>.messages.json.lz4`) into one full-scale window
and splices the graded exchanges in at round boundaries — the skeletons contribute real plans,
tool traffic, and pacing; every graded fact stays a collision-checked literal. Skeletons are
scrubbed (emails, secret-shaped tokens) at load and sha256-pinned in the manifest. The roster's
skeleton ids name JobBench campaign runs; export them from that archive's blob store:

```bash
SRC=…/jobbench/campaign/blobs/conversations
mkdir -p .local/compaction/skeletons
for id in $(uv run python -c "from evals.compaction.build import REAL_SKELETONS; \
  print('\n'.join(sorted({n for p in REAL_SKELETONS.values() for n in p})))"); do
  cp "$SRC/$id/messages.json.lz4" ".local/compaction/skeletons/$id.messages.json.lz4"
done
uv run python -m evals.compaction.build --out "$SNAPSHOT" --transcripts .local/compaction/skeletons
```

Real cases run through the behavior flow (seed turn, transcript swap, live probe turns). Sanity
is their pass bar: each case's scorable result asserts that the window compacted through the
probe turn. Probe verdicts and the artifact-layer survival grades read from the compaction record
(weighted recall, stale rate, reference carry) are recorded as excluded results and observability
metrics — the same contract as the chain leaf's `survival_gen1..3` rates. The version-churn
case's deliverables live at ordinary `/workspace/` paths, which the durable-reference harvester
never keeps — its mechanical coverage floor is 0%, and any survival is the summary's own files
field.

## Run the eval

Requires a running `ufoctl serve` against the same config, exactly like the capability suites.
The behavior leaf's probe turns are real member turns: target a disposable workspace.

```bash
uv run python -m evals --compaction "$SNAPSHOT" --out "$PWD/.local/compaction/reports"
```

`--only compaction.overload` (or any leaf name) reruns one leaf. The report digest folds the
snapshot digest, the grader revision, and the trigger, so a rebuilt snapshot never compares
against an old baseline silently.

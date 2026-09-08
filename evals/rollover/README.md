# rollover

The rollover suite measures what a fresh window can recover after the context boundary. Each case
is a full-scale message window (default 152k estimated tokens, crossing the rollover line)
synthesized from this repo's own docs and source, with generated facts spliced in. Every fact
carries a distinctive numeric literal proven absent from the window's filler at build time, so
grading is a deterministic substring check. No judge, no model call at the boundary. The history is
a file in the sandbox the agent reads with grep and read; the artifact leaves roll over into a local
file the grader reads back.

The default target is `DEFAULT_CONTEXT_WINDOW_TOKENS` less the recovery reserve and the buffer, so a
window clears the line of a model declaring a 200k-token context window. A target agent on a
longer-window model rolls over proportionally later, and the live leaves refuse such a run by name
rather than reporting a rollover that never fired; point `--agent` at an agent whose model declares
a 200k-token window.

Each planted fact is graded on the surfaces a recovering agent can reach:

| Surface | Meaning |
|---|---|
| `carried` | stated in the recovery record the fresh window opens with |
| `journaled` | inside the history lines the record names, which `read` reaches by line |
| `searchable` | anywhere in the history file, which `search_history` reaches after any number of resets |
| `staleCarried` | a superseded value the record asserts in its own voice, outside the verbatim member-words and unread-results sections |
| `addressed` | an offload path the named lines still carry, so `read` reaches the file it names |

Nothing at the boundary is lossy, so the fidelity bars are 1.0: a fact the window held and the
boundary made unrecoverable is a defect, not a prioritization trade.

| Leaf | Cases | Measures | Pass bar |
|---|---:|---|---|
| `rollover.overload` | 2 | weighted recovery fidelity of 120 decisions; the record-carry split is reported | fidelity 100% |
| `rollover.buried` | 1 | fidelity of 40 operative values stated only inside tool-result text | fidelity 100% |
| `rollover.supersession` | 1 | corrected values recoverable, superseded values never asserted by the record | recall 100%, stale rate 0% |
| `rollover.chain` | 1 | critical (weight ≥ 4) facts still reachable with `grep` after three resets | fidelity 100% |
| `rollover.reference` | 1 | 9 offloaded runtime files whose paths the named history lines must carry | coverage 100% |
| `rollover.image` | 1 | a fact whose only home is an inline image | fact survives the boundary |
| `rollover.behavior` | 2×4 probes | live probe turns on a materialized full-scale conversation: recall, supersession, re-read of an offloaded file, and a verbatim-tail control | per probe |
| `rollover.real` | 3×4–5 probes | real agent transcripts composed into full-scale windows with spliced graded exchanges | sanity: the window rolls over; recovery fidelity recorded as metrics |

`rollover.image` stays red by design: the history file keeps rendered text, so an inline image becomes an
`[image]` marker and an image-borne fact cannot survive as a literal. The leaf is the standing
boundary condition on that loss channel.

Artifact leaves call `ContextRollover.maybe_roll_over` directly and grade the recovery record plus
the history lines it names; every boundary's `before`/`after`/`recovery` record persists in the blob
store for audit. The behavior leaf opens a real conversation, runs a seed turn, swaps the transcript
blob for the fixture window, and drives probe turns through the engine — the rollover fires on the
first probe exactly as in production.

## Build the snapshot

```bash
SNAPSHOT="$PWD/.local/rollover/snapshot"
uv run python -m evals.rollover.build --out "$SNAPSHOT"
```

The builder harvests `spec.md`, `README.md`, `docs/**/*.md`, and `core/src/ufo/**/*.py` from the
checkout it runs in (`--repo` overrides), so the snapshot digest moves with the harvested
material. Member messages carry the `<context>` envelope the engine renders, so a built window reads
the way a live one does. Reports are comparable only across runs of the byte-identical snapshot;
keep the snapshot directory with the runs it graded.

## Real cases

`--transcripts DIR` adds the `rollover.real` cases: each composes two real agent transcripts
(exported conversation blobs, `<conversation-id>.messages.json.lz4`) into one full-scale window
and splices the graded exchanges in at round boundaries. Skeletons are scrubbed (emails,
secret-shaped tokens) at load and sha256-pinned in the manifest. The roster's skeleton ids name
JobBench campaign runs; export them from that archive's blob store:

```bash
SRC=…/jobbench/campaign/blobs/conversations
mkdir -p .local/rollover/skeletons
for id in $(uv run python -c "from evals.rollover.build import REAL_SKELETONS; \
  print('\n'.join(sorted({n for p in REAL_SKELETONS.values() for n in p})))"); do
  cp "$SRC/$id/messages.json.lz4" ".local/rollover/skeletons/$id.messages.json.lz4"
done
uv run python -m evals.rollover.build --out "$SNAPSHOT" --transcripts .local/rollover/skeletons
```

Real cases run through the behavior flow. Sanity is their pass bar: each case's scorable result
asserts that the window rolled over through the probe turn. Probe verdicts and the record-graded
recovery fidelity are recorded as excluded results and observability metrics.

## Run the eval

Requires a running `ufoctl serve` against the same config, exactly like the capability suites.
The behavior leaf's probe turns are real member turns: target a disposable workspace.

```bash
uv run python -m evals --rollover "$SNAPSHOT" --out "$PWD/.local/rollover/reports"
```

`--only rollover.overload` (or any leaf name) reruns one leaf. The report digest folds the
snapshot digest, the grader revision, and the trigger, so a rebuilt snapshot never compares
against an old baseline silently.

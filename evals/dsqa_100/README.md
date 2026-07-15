# dsqa_100

`dsqa_100` fixes 100 DeepSearchQA questions into a private logical snapshot and runs the same
questions across six main-agent treatment leaves. It measures the quality gained from research
instructions, search, and browser delegation against their tool use, tokens, and cost.

## Corpus

The source is Google DeepMind's 900-row DeepSearchQA dataset, Kaggle version 4. Its native
`example_id` is the case identity. The builder accepts the pinned archive, verifies every byte and
the complete CSV schema, recomputes the selection, and writes deterministic gzip JSONL.

The selection preserves all 17 source categories with proportional largest-remainder allocation
and a one-case floor. Within each category quota it takes the lowest and highest prompt-visible
research-pressure cases, producing 50 `lower` and 50 `higher` cases. The score uses only prompt
features: source anchors, chained filters, temporal and numeric constraints, exclusions, rankings,
enumeration, format constraints, and length. Gold answers and answer types never participate.

The resulting snapshot contains 66 set-answer and 34 single-answer cases. Pressure bands are
analysis slices, not routing labels; every DeepSearchQA question was authored for web research.

Source: [Kaggle dataset](https://www.kaggle.com/datasets/deepmind/deepsearchqa),
[dataset card](https://huggingface.co/datasets/google/deepsearchqa), and
[paper](https://arxiv.org/abs/2601.20975). The checked selection is a modified Apache-2.0
derivative; attribution and license text live in [`data/`](data/THIRD_PARTY_NOTICES.md).

Build the snapshot offline:

```bash
uv run python -m evals.dsqa_100.build \
  --dataset-archive .dsqa-100/kaggle-deepsearchqa-v4.zip \
  --out .dsqa-100/snapshot
```

The snapshot holds benchmark questions and gold answers. Keep it out of git and never mount it in
the evaluated sandbox. Recorded runs retain questions, candidate answers, trajectories, and scores,
but not gold answers, correctness-detail keys, the judge prompt, or raw judge output.

## Leaves

Each leaf contains the same 100 case ids. The overlap is the factorial treatment, unlike a corpus
partition whose leaves are disjoint.

| Leaf | User workflow | Main-agent capability pack |
|---|---|---|
| `dsqa_100.general.core` | question unchanged | `dsqa_core` |
| `dsqa_100.research.core` | fixed research instruction | `dsqa_core` |
| `dsqa_100.general.search` | question unchanged | `dsqa_search` |
| `dsqa_100.research.search` | fixed research instruction | `dsqa_search` |
| `dsqa_100.general.browser` | question unchanged | `dsqa_browser` |
| `dsqa_100.research.browser` | fixed research instruction | `dsqa_browser` |

The packs are cumulative main-agent surfaces:

- `dsqa_core`: core tools plus the required index, embed, and judge-model providers.
- `dsqa_search`: `dsqa_core` plus Exa and the research extension.
- `dsqa_browser`: `dsqa_search` plus browser delegation and sandbox Chrome.

A main turn receives every non-profile-only tool in its active pack, so one server cannot enforce
all three capability tiers. Run one server and eval invocation per pack. With `--dsqa-100` and no
`--only`, the runner selects the two workflow leaves matching the configured pack. A mismatched
leaf and pack fails before the first turn.

The three configurations share the same target model, reasoning setting, workspace shape, search
provider, browser provider, and agent prompt. The target agent uses a concrete model id. The
background judge is fixed independently:

```toml
[models]
auto_model = "google/gemini-2.5-flash"

[research]
search_provider = "exa"

[pack]
name = "dsqa_search"
```

Start `ufoctl serve` under that config, then run:

```bash
uv run python -m evals \
  --dsqa-100 .dsqa-100/snapshot \
  --agent assistant \
  --label dsqa-search
```

Repeat with `pack.name = "dsqa_core"` and `"dsqa_browser"`. `--only` runs one named leaf.

## Scoring

The scorer reproduces the official Kaggle starter prompt and scoring math with zero-shot Gemini
2.5 Flash through OpenRouter. The serving route differs from the starter's direct Google client,
so results are operator measurements rather than official benchmark scores. For each question, the
judge returns one Boolean per expected answer and a list of excessive answers:

- true positives are matched expected answers;
- false negatives are unmatched expected answers;
- false positives are excessive answers.

Per-question precision, recall, and F1 follow from those counts. Leaf metrics are arithmetic means
of per-question values, matching the starter implementation. Strict outcomes are
`fully_correct`, `fully_incorrect`, `partially_correct`, and `correct_with_extraneous`; the generic
case pass rate is the strict fully-correct rate. Empty candidate answers and invalid judge output
remain visible failures and do not enter the metric means.

Every attempt records the ordered tool trajectory and the terminal tokens and micro-USD of the main
turn and its delegated descendants. The report digest also pins the snapshot, leaf treatment,
judge protocol and bound, active pack manifests, agent prompt, target and judge models, reasoning
setting, search provider, and browser provider.

The six leaves are named `dsqa_100`; their scores are operator metrics for this fixed subset, not
full DeepSearchQA benchmark scores.

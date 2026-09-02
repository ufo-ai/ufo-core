# Memory ingestion evaluation

This evaluation tests this path:

`source file -> page sync -> derive_facts -> memory index -> answer`

The snapshot has 100 cases:

| Corpus | Cases | Selection |
| --- | ---: | --- |
| LongMemEval | 30 | The same question IDs as `memory_100` |
| LoCoMo | 70 | Deterministic selection from QA categories 1 through 4 |

The builder keeps only the annotated evidence turns. It splits long turns before the 8,000
character fact-extraction bound. The materializer does not index the raw pages. An answer must use
a memory item that `derive_facts` made.

The producer requires `models.background_jobs_model = "gpt-5.6-luna"`. It writes a normalized,
content-attested derived corpus. Paired targets install that same corpus in separate stacks; Luna
quality remains visible in the producer readiness, while target comparisons no longer include two
independent derivations. If Luna makes no fact for annotated evidence, producer coverage records
that miss and both targets still run against the corpus it produced.

## Build

```bash
ASSETS=.local/memory-ingestion/assets
mkdir -p "$ASSETS"
curl --fail --location --output "$ASSETS/longmemeval_s_cleaned.json" \
  https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/98d7416c24c778c2fee6e6f3006e7a073259d48f/longmemeval_s_cleaned.json
curl --fail --location --output "$ASSETS/locomo10.json" \
  https://raw.githubusercontent.com/snap-research/locomo/3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376/data/locomo10.json

uv run python -m evals.memory_ingestion.build \
  --longmem "$ASSETS/longmemeval_s_cleaned.json" \
  --locomo "$ASSETS/locomo10.json" \
  --output .local/memory-ingestion/snapshot
```

Add repeated `--case CASE_ID` arguments to build an exact smoke snapshot. This nine-case selection
has three single-fact, three multi-session, and three temporal cases:

```text
longmem/118b2229
locomo/conv-26/120
locomo/conv-30/060
longmem/9aaed6a3
locomo/conv-49/002
locomo/conv-41/021
longmem/0bb5a684
locomo/conv-26/062
locomo/conv-42/037
```

## Derive and run

Derive once in a clean migrated database, then give the resulting corpus to every target stack.

```bash
cat > .local/memory-ingestion/template.toml <<'EOF'
[database]
url = "postgresql+asyncpg://ufo:ufo@127.0.0.1:5541/ufo"

[blob]
backend = "filesystem"
root = "./blobs"

[models]
background_jobs_model = "gpt-5.6-luna"

[pack]
name = "assistant"

[research]
search_provider = "perplexity"

[o11y]
otlp_endpoint = "http://127.0.0.1:4318"
EOF

set -a; source .env; set +a
UFO_CONFIG=.local/memory-ingestion/template.toml uv run ufoctl migrate
UFO_CONFIG=.local/memory-ingestion/template.toml uv run python \
  -m evals.memory_ingestion.materialize \
  --snapshot .local/memory-ingestion/snapshot \
  --state .local/memory-ingestion/producer-state \
  --corpus-output .local/memory-ingestion/derived-corpus.json

cat > .local/memory-ingestion/matrix.toml <<'EOF'
[[run]]
label = "memory-ingestion"
config = ".local/memory-ingestion/template.toml"
memory_ingestion = ".local/memory-ingestion/snapshot"
memory_ingestion_corpus = ".local/memory-ingestion/derived-corpus.json"
EOF

uv run python -m evals.stack .local/memory-ingestion/matrix.toml
```

An `evals.ablate` experiment over a `memory_ingestion.*` suite names both paths with
`memory_ingestion` and `memory_ingestion_corpus`; every control and candidate repeat then consumes
the same derivation.

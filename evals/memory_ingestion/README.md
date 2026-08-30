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

The materializer requires `models.background_jobs_model = "gpt-5.6-luna"`. It records this model
in readiness data and the runner checks it. If Luna makes no fact for annotated evidence, readiness
records an empty mapping and the case fails derived-evidence coverage.

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

## Materialize and run

Use the stack runner with a Postgres template. It creates a clean database and runs materialization,
serve, recall capture, and evaluation with one config.

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

cat > .local/memory-ingestion/matrix.toml <<'EOF'
[[run]]
label = "memory-ingestion"
config = ".local/memory-ingestion/template.toml"
memory_ingestion = ".local/memory-ingestion/snapshot"
EOF

set -a; source .env; set +a
uv run python -m evals.stack .local/memory-ingestion/matrix.toml
```

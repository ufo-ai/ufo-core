# memory_100

`memory_100` fixes 100 questions and their evidence into a portable logical snapshot:

| Corpus | Cases | Stored as |
|---|---:|---|
| EnterpriseRAG-Bench | 60 | shared source pages plus deterministic hard negatives |
| LongMemEval | 30 | member-private recallable memories |
| UFO | 10 | shared/private, synthesis, conflict, isolation, and no-answer fixtures |

One snapshot and materialized workspace serve 18 disjoint report leaves:

| Prefix | Labels | Cases |
|---|---|---:|
| `memory_100.enterprise.` | `basic`, `semantic`, `intra_document_reasoning`, `project_related`, `constrained`, `conflicting_info`, `completeness`, `miscellaneous`, `high_level`, `info_not_found` | 4–8 each |
| `memory_100.longmem.` | `information_extraction`, `multi_session`, `knowledge_update`, `temporal_reasoning`, `abstention` | 6 each |
| `memory_100.ufo.pages` | shared, multi-page, conflicting evidence | 3 |
| `memory_100.ufo.memories` | private, decision, preference, event memory | 4 |
| `memory_100.ufo.boundaries` | member isolation, no answer, mixed scope | 3 |

The builder verifies every upstream byte against `assets.py`; `snapshot.json` pins the canonical
gzip JSONL files. Source NUL characters become `U+FFFD` before selection and hashing so every
snapshot loads into Postgres. It never snapshots database files. Building needs about 1.5 GB of
downloads plus working space.

## Build the snapshot

Download the four pinned inputs once:

```bash
ASSETS="$PWD/.local/memory-100/assets"
SNAPSHOT="$PWD/.local/memory-100/snapshot"
mkdir -p "$ASSETS"

curl --fail --location \
  --output "$ASSETS/questions.jsonl" \
  https://github.com/onyx-dot-app/EnterpriseRAG-Bench/releases/download/v1.0.0/questions.jsonl
curl --fail --location \
  --output "$ASSETS/all_documents.zip" \
  https://github.com/onyx-dot-app/EnterpriseRAG-Bench/releases/download/v1.0.0/all_documents.zip
curl --fail --location \
  --output "$ASSETS/company_overview.md" \
  https://raw.githubusercontent.com/onyx-dot-app/EnterpriseRAG-Bench/56ba6a62cb66bf0a68ff995b1c423680980bf70a/generated_data/company_overview.md
curl --fail --location \
  --output "$ASSETS/longmemeval_s_cleaned.json" \
  https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/98d7416c24c778c2fee6e6f3006e7a073259d48f/longmemeval_s_cleaned.json

uv run python -m evals.memory_100.build \
  --enterprise-questions "$ASSETS/questions.jsonl" \
  --enterprise-documents "$ASSETS/all_documents.zip" \
  --enterprise-overview "$ASSETS/company_overview.md" \
  --longmem "$ASSETS/longmemeval_s_cleaned.json" \
  --out "$SNAPSHOT"
```

The canonical snapshot digest is
`sha256:724e05a8f6c2879686c808baa1e0da845ec9dd1787a878341d78a693b09bae7c`.

## Run the eval

Run the block from one shell at the repository root. It creates new Postgres application and DBOS
databases plus a blob root on every invocation, materializes the snapshot, starts the eval receiver
before the worker, executes only `memory_100`, and writes the reports beside the run state.
Set `ONLY` to one leaf name, such as `memory_100.longmem.multi_session`, to rerun only that leaf.

Use Postgres for this corpus. SQLite's single writer cannot serve the worker, scheduler, eval
driver, and a 36,000-chunk brute-force vector read concurrently. The block reuses a pgvector
container already publishing the repository's port, or starts the Compose service when none exists.

`.env` must contain `ANTHROPIC_API_KEY` for the target and judge and `OPENAI_API_KEY` for corpus and
query embeddings. The direct Python entry points do not load `.env`, so the block exports it. Do
not run `ufoctl init`: materialization requires a database with no workspace or index chunks.

```bash
set -euo pipefail

set -a
source .env
set +a

SNAPSHOT="$PWD/.local/memory-100/snapshot"
RUN_ID="$(date +%Y%m%d_%H%M%S)"
RUN="$PWD/.local/memory-100/runs/$RUN_ID"
mkdir -p "$RUN"

POSTGRES_CONTAINER="$(
  docker ps --filter ancestor=pgvector/pgvector:pg17 --filter publish=5541 \
    --format '{{.ID}}' | head -1
)"
if test -z "$POSTGRES_CONTAINER"; then
  docker compose up -d --wait postgres
  POSTGRES_CONTAINER="$(docker compose ps -q postgres)"
fi
APP_DB="memory_100_$RUN_ID"
DBOS_DB="${APP_DB}_dbos"
docker exec "$POSTGRES_CONTAINER" createdb -U ufo "$APP_DB"
docker exec "$POSTGRES_CONTAINER" createdb -U ufo "$DBOS_DB"

export UFO_CONFIG="$RUN/ufo.toml"
export UFO_CREDENTIAL_KEY="${UFO_CREDENTIAL_KEY:-$(
  uv run python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
)}"
export UFO_ARTIFACT_TOKEN_SECRET="${UFO_ARTIFACT_TOKEN_SECRET:-memory-100-local-eval}"

cat > "$UFO_CONFIG" <<EOF
[database]
url = "postgresql+asyncpg://ufo:ufo@127.0.0.1:5541/$APP_DB"
system_url = "postgresql+psycopg://ufo:ufo@127.0.0.1:5541/$DBOS_DB"

[blob]
backend = "filesystem"
root = "$RUN/blobs"

[pack]
name = "yc"

[o11y]
otlp_endpoint = "http://127.0.0.1:4318"
EOF

uv run ufoctl migrate
READINESS="$(
  uv run python -m evals.memory_100.materialize \
    --snapshot "$SNAPSHOT" \
    --state "$RUN/state"
)"

ONLY_ARGS=()
if test -n "${ONLY:-}"; then
  ONLY_ARGS=(--only "$ONLY")
fi

uv run python -m evals \
  --memory-100 "$SNAPSHOT" \
  --memory-100-state "$READINESS" \
  --out "$RUN/reports" \
  "${ONLY_ARGS[@]}" >"$RUN/eval.log" 2>&1 &
EVAL_PID=$!
until curl --silent --max-time 1 --output /dev/null http://127.0.0.1:4318/; do
  kill -0 "$EVAL_PID" 2>/dev/null || { cat "$RUN/eval.log"; exit 1; }
  sleep 0.2
done

uv run ufoctl serve >"$RUN/serve.log" 2>&1 &
SERVER_PID=$!
cleanup() { kill "$EVAL_PID" "$SERVER_PID" 2>/dev/null || true; }
trap cleanup EXIT
until curl --silent --fail http://127.0.0.1:8710/openapi.json >/dev/null; do
  kill -0 "$SERVER_PID" 2>/dev/null || { cat "$RUN/serve.log"; exit 1; }
  kill -0 "$EVAL_PID" 2>/dev/null || { cat "$RUN/eval.log"; exit 1; }
  sleep 0.2
done

if wait "$EVAL_PID"; then
  EVAL_STATUS=0
else
  EVAL_STATUS=$?
fi
cat "$RUN/eval.log"
cleanup
trap - EXIT
test "$EVAL_STATUS" -eq 0
```

Materialization is API-bound and may be quiet while embedding 1,429 memories and 12,239 pages.
It drains every selected pack's page-change consumer without model access, completing deterministic
derivations without letting background corpus jobs race the eval. Target and judge turns still use
Anthropic normally.
`readiness.json` attests the snapshot digest, materialized-corpus digest, workspace/member bindings,
row counts, and every evidence ref's durable owner.

The eval command owns the configured OTLP endpoint while it runs. For each turn, the memory
extension's `user_prompt_submit` hook emits one content-free structured event containing the turn
id, the ordered memory-item ids actually injected into context, and the error class when recall
degraded. Episodic topic pointers are excluded from both the injected context and the event. Event
logging is fail-open and does not alter the recall query, ranking, timeout, or context.

The receiver accepts only the configured event for the materialized workspace and discards traces
and metrics. The endpoint must be unused, loopback, and dedicated to this disposable eval tenant.
Runs are recorded as structured JSON and rendered by the offline HTML viewer, including the
captured event and evidence ranks. Memory reports surface `meanMappedEvidenceCoverage`,
`minMappedEvidenceCoverage`, `degradedRecallCount`, and `unmappedEvidenceCount` at top level;
stdout and viewer suite metadata repeat the same aggregate. The report digest includes the logical
snapshot, materialized corpus, and recall-grading policy.

Evidence coverage maps injected memory-item ids to readiness owners. A source page without a
materialized memory-item owner is reported as unmapped rather than inferred from page provenance.

The offline viewer is `reports/index.html`; structured runs are
`reports/runs/<run-uuid>.json`. The CLI prints the pass count and digest and exits nonzero when the
suite fails. The reports record the target model, judge model, judge revision, logical snapshot,
materialized corpus, and recall-grading policy. The target pack and loopback endpoint in `ufo.toml`
must remain unchanged through migration, materialization, serving, and evaluation; the endpoint
must be unused and dedicated to this disposable eval tenant.

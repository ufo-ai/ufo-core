# memory_100

`memory_100` fixes 102 questions and their evidence into a portable logical snapshot:

The runner pins semantic answer grading to `gpt-5.4`; retrieval coverage remains deterministic.

| Corpus | Cases | Stored as |
|---|---:|---|
| EnterpriseRAG-Bench | 60 | shared source pages plus deterministic hard negatives |
| LongMemEval | 30 | member-private recallable memories |
| UFO | 12 | shared/private, synthesis, conflict, isolation, no-answer, and alias-identity fixtures |

One snapshot and materialized workspace serve 19 disjoint report leaves:

| Prefix | Labels | Cases |
|---|---|---:|
| `memory_100.enterprise.` | `basic`, `semantic`, `intra_document_reasoning`, `project_related`, `constrained`, `conflicting_info`, `completeness`, `miscellaneous`, `high_level`, `info_not_found` | 4–8 each |
| `memory_100.longmem.` | `information_extraction`, `multi_session`, `knowledge_update`, `temporal_reasoning`, `abstention` | 6 each |
| `memory_100.ufo.pages` | shared, multi-page, conflicting evidence | 3 |
| `memory_100.ufo.memories` | private, decision, preference, event memory | 4 |
| `memory_100.ufo.boundaries` | member isolation, no answer, mixed scope | 3 |
| `memory_100.ufo.alias_identity` | one person under an initialism, under a handle | 2 |

`memory_100.ufo.alias_identity` is the one leaf whose bar is retrieval rather than the answer
rubric. Each of its cases names one person by a surface form no recalled row carries — an
initialism, a chat handle — while the rows about that person are written under other forms and split
across a shared page, a shared memory, and a member-private memory. Its grader requires injected
recall to cover every evidence row that owns a memory item, so a judge that infers the link from
answer text cannot pass the case: `min_mapped_evidence_coverage` on `Memory100Leaf` is what turns
the recorded `coverage` into the verdict, and the bar is part of the leaf's suite digest.

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

The build prints the canonical snapshot digest on stdout as its last line; pin it here from a build
run. `_builder_digest` hashes `build.py`, `selection.json`, `ufo_cases.json`, and
`THIRD_PARTY_NOTICES.md`, so a change to the case corpus moves the snapshot digest, and
`load_memory_100` refuses a readiness attested against a different snapshot.

## Run the eval

Runs go through the stack orchestrator (`python -m evals.stack`), which provisions an isolated
deploy per run — its own Postgres application and DBOS databases on the template's server, blob
root, serve and proxy ports, and a private loopback OTLP endpoint for the recall receiver —
materializes the snapshot (`ufoctl migrate` then `python -m evals.memory_100.materialize`; never
`ufoctl init`, which would occupy the workspace-free database materialization requires), boots
`ufoctl serve`, and drives `python -m evals --memory-100 …` against it. Run directories land under
`.local/evals/<stamp>/<label>/` with `seed.log`, `serve.log`, `eval.log`, and the run's `state/`;
reports land in the shared `eval-reports/` archive.

Use Postgres for this corpus. SQLite's single writer cannot serve the worker, scheduler, eval
driver, and a 36,000-chunk brute-force vector read concurrently. Start the Compose service first
(`docker compose up -d --wait postgres`); the template's `database.url` names that server and an
existing database on it (the stack dials it to create the per-run databases).

The environment must carry `ANTHROPIC_API_KEY` for the target and judge and `OPENAI_API_KEY` for
corpus and query embeddings; the stack passes the environment through to every subprocess, and
the Python entry points do not load `.env`, so export it first (`set -a; source .env; set +a`).

```bash
mkdir -p .local/memory-100

cat > .local/memory-100/template.toml <<'EOF'
[database]
url = "postgresql+asyncpg://ufo:ufo@127.0.0.1:5541/ufo"

[blob]
backend = "filesystem"
root = "./blobs"

[pack]
name = "assistant"

[research]
search_provider = "perplexity"

[connect]
public_base_url = "http://127.0.0.1"

[o11y]
otlp_endpoint = "http://127.0.0.1:4318"
EOF

cat > .local/memory-100/matrix.toml <<'EOF'
[[run]]
label = "memory-100"
config = ".local/memory-100/template.toml"
memory_100 = ".local/memory-100/snapshot"
EOF

set -a; source .env; set +a
uv run python -m evals.stack .local/memory-100/matrix.toml
```

The template's OTLP endpoint is re-pointed to a free loopback port per run, so concurrent runs
never contend; database, blob root, and ports are likewise rewritten per run. Select a single leaf
by adding `args = ["--only", "memory_100.longmem.multi_session"]` to the run block.

Materialization is API-bound and may be quiet while embedding 1,433 memories and 12,241 pages.
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

The offline viewer is `eval-reports/index.html`; structured runs are
`eval-reports/runs/<run-uuid>.json`. The CLI prints the pass count and digest and exits nonzero
when the suite fails. The reports record the target model, judge model, judge revision, logical
snapshot, materialized corpus, and recall-grading policy. The stack derives one `ufo.toml` per run,
so the pack and loopback endpoint hold unchanged through migration, materialization, serving, and
evaluation, and each run's endpoint is unused and dedicated to its disposable eval tenant.

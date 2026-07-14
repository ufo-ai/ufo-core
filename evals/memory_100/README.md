# memory_100

`memory_100` fixes 100 questions and their evidence into a portable logical snapshot:

| Corpus | Cases | Stored as |
|---|---:|---|
| EnterpriseRAG-Bench | 60 | shared source pages plus deterministic hard negatives |
| LongMemEval | 30 | member-private recallable memories |
| UFO | 10 | shared/private, synthesis, conflict, isolation, and no-answer fixtures |

The builder verifies every upstream byte against `assets.py`; `snapshot.json` pins the canonical
gzip JSONL files. It never snapshots database files.

```bash
python -m evals.memory_100.build \
  --enterprise-questions questions.jsonl \
  --enterprise-documents all_documents.zip \
  --enterprise-overview company_overview.md \
  --longmem longmemeval_s_cleaned.json \
  --out .memory-100/snapshot
```

Point `ufo.toml` at an empty eval database and blob root, apply migrations, then replay the snapshot
through the real folder source, memory store, page-change consumers, and default index:

```bash
ufoctl migrate
python -m evals.memory_100.materialize \
  --snapshot .memory-100/snapshot \
  --state .memory-100/state
```

The command prints the generated `readiness.json`. It contains the snapshot and materialized-corpus
digests, deterministic workspace/member bindings, row counts, and every evidence ref's durable
owner. Materialization refuses a database that already contains a workspace or index chunks.

Configure the dedicated eval tenant to export OTel to a loopback receiver hosted by the eval run:

```toml
[o11y]
otlp_endpoint = "http://127.0.0.1:4318"
```

Start the answer suite first so its receiver owns the endpoint:

```bash
python -m evals \
  --memory-100 .memory-100/snapshot \
  --memory-100-state .memory-100/state/<snapshot-sha>/readiness.json
```

Start `ufoctl serve` against the same config in a second terminal. The cases may queue before its
worker starts.

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

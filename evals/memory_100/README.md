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

Run `ufoctl serve` against that database, then execute the answer suite:

```bash
python -m evals \
  --memory-100 .memory-100/snapshot \
  --memory-100-state .memory-100/state/<snapshot-sha>/readiness.json
```

Each case opens a conversation as its materialized member and grades the served answer against the
pinned reference and answer facts. Evidence refs remain in the snapshot and readiness artifact so
recall instrumentation can compare later variants against this baseline. The report digest includes
both the logical snapshot and materialized corpus digests.

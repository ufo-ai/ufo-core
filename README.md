# ufo

An agent runtime you can run, read, and extend: a hard-to-vary core — sandboxed agent loop, memory,
Slack/CLI/web surfaces, accounting, Anthropic/OpenAI model abstraction — plus an extension system
for everything else (connectors, data sources, tools, subagents, onboarding).

- `spec.md` — source of truth: doctrine, fixed decisions, workspace model, non-goals.
- `docs/contracts.md` — core contracts per unit (sections delete as code lands).
- `docs/plan.md` — build order. `docs/salvage.md` — file-level port map from the previous repo.
- `core/` — the axiomatic unit. `extensions/` — first-party extensions. `packs/` — skill packs.

```bash
export ANTHROPIC_API_KEY=...
uv run ufoctl init --email you@example.com  # writes ufo.toml; SQLite — zero services
uv run ufoctl serve                         # one process: surfaces + workers
uv run ufoctl chat                          # second terminal; sessions persist across runs
```

Dev is zero-services: SQLite + filesystem blobs + in-process hub. Postgres (the checked-in
compose or an existing instance) is for deploys and the Postgres half of the test matrix; Docker
enters for sandboxes (U2+).

## Evals

With `ufoctl serve` running against a disposable workspace:

```bash
uv run python -m evals --workspace <workspace-id> --label baseline
uv run python -m evals --view
uv run python -m evals --share <current-run> <baseline-run>
```

Each invocation records an immutable JSON run under `eval-reports/runs/` and rebuilds the offline
`eval-reports/index.html` viewer. Comparisons show deltas only for digest-identical suites. Shared
viewers contain only the named runs; their S3 object key has 192 random bits and the presigned URL
expires after seven days by default. Sharing uses `--s3-bucket`, then `UFO_EVAL_SHARE_BUCKET`, then
the configured S3 `[blob]` bucket.

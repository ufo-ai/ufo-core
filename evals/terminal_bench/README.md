# Terminal-Bench 2.1

The complete 89-task Terminal-Bench 2.1 roster is pinned to Harbor dataset revision 6 and content
hash `sha256:7d7bdc1cbedad549fc1140404bd4dc45e5fd0ea7c4186773687d177ad3a0699a`.
Runs use Harbor 0.21.0.

```bash
uv run python -m evals.terminal_bench.setup
uv run python -m evals \
  --terminal-bench \
  --terminal-bench-case openssl-selfsigned-cert \
  --terminal-bench-case regex-log \
  --terminal-bench-case cancel-async-tasks \
  --remote \
  --workspace <workspace-id> \
  --concurrency 3
```

Omit `--terminal-bench-case` to run all 89 tasks. The configured `connect.public_base_url` must be
a public HTTPS URL for the running ufo service, `UFO_TOKEN_SECRET` must match that service, and the
selected Harbor environment must be configured.

Setup builds the static x86-64 client uploaded to each remote environment. Harbor resolves the
immutable dataset directly; no benchmark task archive is downloaded or rewritten locally. One
Harbor job runs the selected cases at the eval runner's `--concurrency` and retains official trial
results, verifier output, and rewards under `.local/terminal_bench/jobs/`.

The client runs with `--json` inside each Harbor environment and stays attached to that environment.
This is required: the agent changes the same filesystem and live services Harbor verifies. The
outer eval runner's `--remote` selects the remote Harbor environment and public ufo service; using
the client's `--remote` flag inside a task would provision a different sandbox and leave the graded
environment untouched.

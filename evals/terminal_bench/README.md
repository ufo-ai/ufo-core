# Terminal-Bench 3

Three Terminal-Bench 3 tasks are pinned to the `v3.0.0` release asset and graded by Harbor 0.21.0:
`interleaved-vigenere`, `html-js-filter`, and `kv-live-surgery`.

```bash
uv run python -m evals.terminal_bench.setup
uv run python -m evals \
  --terminal-bench \
  --remote \
  --workspace <workspace-id> \
  --concurrency 24
```

The configured `connect.public_base_url` must be a public HTTPS URL for the running ufo service,
`UFO_TOKEN_SECRET` must match that service, and Harbor's Modal environment must be configured. Add
`--terminal-bench-case NAME` to select cases.

Setup downloads and verifies the pinned release asset, extracts only the selected task directories,
and builds the static x86-64 client uploaded to the remote environments. One Harbor job runs the
selected cases with `--n-concurrent` set from the eval runner's `--concurrency`; Harbor retains its
official trial results, verifier output, and rewards under `.local/terminal_bench/jobs/`.

The client runs with `--json` inside each Harbor environment and stays attached to that environment.
This is required: the agent changes the same filesystem and live services Harbor verifies. The
outer eval runner's `--remote` selects the remote Harbor environment and public ufo service; using
the client's `--remote` flag inside a task would provision a different sandbox and leave the graded
environment untouched.

Setup sets `network_mode = "no-network"` for `interleaved-vigenere` and `html-js-filter`; the Harbor
job admits only the configured ufo host during the agent phase. `kv-live-surgery` retains its
upstream network policy because its live service topology cannot use Harbor's egress sidecar.

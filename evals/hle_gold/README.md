# HLE Gold sentinels

The suite pins 100 HLE-Verified Gold records by ID and digest without committing benchmark
questions, answers, rationales, or images. Six leaf suites exercise compaction, code generation,
vision, web research, sandbox computation, and tool restraint through real UFO turns.

Download the pinned `Gold_subset.jsonl` asset from HLE-Verified commit
`b705e0fb541c025a1532ce0d60d70ae2f53b00e0`, then run:

```bash
uv run python -m evals --hle-gold /path/to/Gold_subset.jsonl --hle-gold-smoke \
  --workspace <disposable-workspace-id>
uv run python -m evals --hle-gold /path/to/Gold_subset.jsonl --label hle-gold-full \
  --workspace <disposable-workspace-id>
uv run python -m evals --share <run-id>
```

The final command uses `--s3-bucket`, `UFO_EVAL_SHARE_BUCKET`, or the configured S3 blob bucket,
in that order, and prints a seven-day presigned URL. HLE reports redact prompts, answers, tool
arguments, and tool results before they reach the local archive or shared viewer.

HLE source material remains in the target's durable turn transcripts. The CLI therefore requires
an explicit disposable workspace; delete that workspace after the run.

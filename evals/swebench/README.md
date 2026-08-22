# SWE-bench Verified

This local smoke set grades the three pinned cases with the unmodified `swebench==4.1.0` harness.
Docker must be running. Its images can consume substantial disk; check `docker system df` before
starting.

```bash
PARQUET=.local/swebench/assets/test.parquet
mkdir -p "$(dirname "$PARQUET")"
curl -fL \
  https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified/resolve/c104f840cc67f8b6eec6f759ebc8b2693d585d4a/data/test-00000-of-00001.parquet \
  -o "$PARQUET"
uv run --with pyarrow python -m evals.swebench.build \
  --parquet "$PARQUET" \
  --output .local/swebench/snapshot

RUN_TAG=$(date -u +%Y%m%dT%H%M%SZ)-$(uuidgen | tr '[:upper:]' '[:lower:]')
SUBMISSIONS=.local/swebench/submissions/$RUN_TAG-ufo
uv run python -m evals \
  --swebench \
  --swebench-submissions "$SUBMISSIONS" \
  --label swebench-local

uv run --with swebench==4.1.0 python -m evals.swebench.grading \
  --gold \
  --run-id "$RUN_TAG-gold"
uv run --with swebench==4.1.0 python -m evals.swebench.grading \
  --submissions "$SUBMISSIONS" \
  --run-id "$RUN_TAG-ufo"
```

The immutable snapshot is under `.local/swebench/snapshot.versions/`, captured patches are under the
explicit submissions path, and each grade is retained under `.local/swebench/grades/<run-id>/`.
That grade directory contains predictions when applicable, native `logs/run_evaluation/`, the
official aggregate report, and `summary.json`; official output files are not moved or rewritten.
Grading runs on the official prebuilt evaluation images from the `swebench` Docker Hub namespace —
the harness's stock source. The wrapper pre-pulls each selected instance image with an explicit
`--platform linux/amd64` because the pinned harness pulls without a platform, which arm64 daemons
refuse for these x86_64-only manifests; the harness then finds the images locally and never
rebuilds them. Local builds are no longer an option: the pinned specs have drifted from the world
(sympy's deleted `1.7` branch, pip 25.3 dropping `--no-use-pep517`), while the prebuilt images were
frozen before that drift.

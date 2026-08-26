#!/usr/bin/env bash
# Regenerate docs/handbook/*.md from the current source tree.
#
# Runs the Harness Handbook `handbook_generate_large` pipeline (file-as-leaf,
# complete-by-construction) over the Python application source and rewrites the
# committed markdown pages in place. README.md is hand-written and preserved.
#
# Local:  OPENAI_API_KEY=sk-... docs/handbook/regenerate.sh
# CI:     invoked by .github/workflows/handbook.yml
#
# Requires: uv, git, rsync. The tool venv (Python 3.13 + tree-sitter, pyyaml,
# requests, markdown, pygments) is built in a throwaway dir; nothing is installed
# into the repo.
set -euo pipefail

TOOL_REPO=https://github.com/Ruhan-Wang/Harness_Handbook.git
TOOL_SHA=98d4db1457ce562acc51aa4b72eed7bee9548b2b   # pinned for reproducibility
export OPENAI_MODEL="${OPENAI_MODEL:-gpt-5.5}"           # the tool reads OPENAI_MODEL from the env
export OPENAI_MAX_TOKENS="${OPENAI_MAX_TOKENS:-32000}"   # headroom for gpt-5.5 reasoning + output

: "${OPENAI_API_KEY:?set OPENAI_API_KEY (OpenAI-compatible endpoint) before running}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HANDBOOK_DIR="$REPO_ROOT/docs/handbook"
SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH"' EXIT
TOOL="$SCRATCH/tool"
STAGE="$SCRATCH/src"
WORK="$SCRATCH/work"
SOURCE_ROOTS=(core extensions servers/control sandbox packs)

# A source move leaves this list stale. Say so here, before the tool install and the
# paid pipeline, instead of failing on an rsync stat error further down.
for d in "${SOURCE_ROOTS[@]}"; do
  [ -d "$REPO_ROOT/$d" ] || { echo "source root '$d' is missing; update SOURCE_ROOTS in ${BASH_SOURCE[0]}" >&2; exit 1; }
done

echo "==> clone tool @ ${TOOL_SHA:0:12}"
git clone --quiet "$TOOL_REPO" "$TOOL"
git -C "$TOOL" checkout --quiet "$TOOL_SHA"

echo "==> apply tool patch (tree-sitter binding compatibility)"
git -C "$TOOL" apply "$HANDBOOK_DIR/tool.patch"

echo "==> venv + deps"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$SCRATCH/uvcache}"
uv venv --quiet --python 3.13 "$TOOL/.venv"
# shellcheck disable=SC1091
. "$TOOL/.venv/bin/activate"
uv pip install --quiet tree-sitter==0.26.0 tree-sitter-language-pack==1.13.3 pyyaml requests markdown pygments

echo "==> stage Python source (tests/caches excluded)"
mkdir -p "$STAGE"
for d in "${SOURCE_ROOTS[@]}"; do
  rsync -a \
    --exclude='__pycache__/' --exclude='.venv/' --exclude='node_modules/' \
    --exclude='.mypy_cache/' --exclude='.pytest_cache/' --exclude='.ruff_cache/' \
    --exclude='*.pyc' --exclude='dist/' --exclude='build/' \
    --exclude='tests/' --exclude='test/' \
    --exclude='test_*.py' --exclude='*_test.py' --exclude='conftest.py' \
    "$REPO_ROOT/$d" "$STAGE/"
done

echo "==> run pipeline (model=$OPENAI_MODEL)"
python3 "$TOOL/handbook_generate_large/run.py" \
  --source-root "$STAGE" --work-dir "$WORK" --lang python \
  --read-detail deep --read-batch-size 1 --read-workers 24 \
  --synth-mode doctor --doctor-workers 12 --doctor-llm-workers 24 \
  --organize-workers 16 --phase3-workers 16

# Fail loud before the destructive refresh: a pipeline that exits 0 without the
# always-present core pages must not wipe the committed handbook. register.md is
# emitted only when the system has cross-stage state, so it is optional.
pages="$(find "$WORK/handbook" -maxdepth 1 -name 'stage-*.md' | wc -l | tr -d ' ')"
if [ ! -s "$WORK/handbook/overview.md" ] || [ ! -s "$WORK/handbook/index.md" ] || [ "$pages" -eq 0 ]; then
  echo "pipeline produced an incomplete handbook (need overview.md, index.md, stage pages); refusing to overwrite committed pages" >&2
  exit 1
fi

echo "==> refresh committed markdown (README.md preserved)"
rm -f "$HANDBOOK_DIR"/overview.md "$HANDBOOK_DIR"/index.md "$HANDBOOK_DIR"/register.md "$HANDBOOK_DIR"/stage-*.md
cp "$WORK"/handbook/overview.md "$WORK"/handbook/index.md "$HANDBOOK_DIR"/
cp "$WORK"/handbook/stage-*.md "$HANDBOOK_DIR"/
[ -f "$WORK/handbook/register.md" ] && cp "$WORK/handbook/register.md" "$HANDBOOK_DIR"/

# Empty stages are dropped (no page emitted); turn any link to a missing stage
# page into plain text across every page so no dead link ships.
python3 "$HANDBOOK_DIR/strip_dead_links.py" "$HANDBOOK_DIR"

echo "==> done. $(find "$HANDBOOK_DIR" -maxdepth 1 -name 'stage-*.md' | wc -l | tr -d ' ') stage pages in $HANDBOOK_DIR"

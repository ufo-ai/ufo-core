# HANDBOOK.md

## Corpus

[HANDBOOK.md](https://github.com/surge-ai/handbook) (Surge AI, Apache-2.0) is 65 long-context
agentic instruction-following tasks across finance, medical billing, insurance, logistics, and HR.
Each task drops the agent into one company: a policy document (44 KB HTML to 2.1 MB PDF), a stack of
xlsx and pdf working files, and a seeded inbox, Slack workspace, Jira queue, calendar, and
storefront. Every task modifies its base handbook's rules and thresholds, so no two tasks share a
policy and memorization does not carry. No published model exceeds 25%, which makes this a
capability-boundary suite for the full chat surface.

Grading is upstream's own: 824 deterministic Python rubrics (12.7 per task on average), 475 of
which read mutated service state and 318 of which read spreadsheets the agent had to edit in place.
A case passes only on a clean sweep — upstream's bar.

The repository contains only [pin metadata](data/upstream.json): the upstream revision, the uniform
tool sets, the verifier digest, and each task's rubric count and tree digest. It contains no upstream
prompts, handbooks, rubrics, or working files.

## Design

The environment is upstream's own container, unmodified. Its mock services are stdio MCP servers
aggregated by `mcp_proxy` into one Streamable-HTTP endpoint — the transport the `mcp` extension
already speaks — so nothing is reimplemented and the seeded state is upstream's verbatim.

| Half | Owner |
| --- | --- |
| Mail, Slack, calendar, Jira, storefront | Upstream's container, reached through `list_mcp_tools` / `call_mcp_tool` |
| The handbook and working files | ufo's own sandbox, staged into the conversation workspace |
| Grading | Upstream's `sop_verifier.py`, run inside the container over the copied-back workspace |

Upstream's file tools stay off (`WORLDBENCH_TOOL_SETS` minus `syntara_ds_all`), so the agent's file
surface is the product's own and the 2.1 MB handbook PDFs never cross the MCP pack's 1 MiB response
bound. Arbitrary upstream `verifier_code` only ever executes inside the container.

The agent sees two generic MCP tools rather than upstream's 82 named ones. That is the product's real
shape — the same funnel Composio rides — and it is strictly harder than the published harness, so a
score here is not directly comparable to the upstream leaderboard.

Cases are exclusive: the workspace's `mcp_servers` slot points at exactly one task's services at a
time. Each case raises its own container, named for its task and workspace on a port Docker assigns,
and writes that slot itself; nothing has to be configured by hand.

## Pin

Pin a checkout once and commit the result:

```bash
git clone https://github.com/surge-ai/handbook .local/handbook
uv run python -m evals.handbook.build --checkout .local/handbook
```

Every later run verifies the checkout against the pin and fails loud on any drift — a missing task,
an edited handbook, a changed rubric, a patched verifier.

## Run

Requires Docker. The first run builds upstream's base image (a few minutes, cached afterwards); each
case then builds its own thin task image.

Grading copies the finished conversation workspace into the environment container, so `[sandbox]
backend` must be one whose `/workspace` is a host directory — `local` or `docker`. E2B keeps the
workspace on its own disk and is rejected. Prefer `docker`: under `local` the sandbox is the host
shell, so the image's baked `openpyxl`/`pdfplumber` do not apply and `/workspace` is an argv rewrite
rather than a real path. Measured on one case, the carriers cost the same wall time and money; the
Docker carrier simply produced far fewer tool errors.

With `ufoctl serve` running against a disposable workspace:

```bash
uv run python -m evals \
  --workspace <workspace-id> \
  --handbook .local/handbook \
  --handbook-task finance_meridian_partners_19d57538
```

Omit `--handbook-task` to run all 65. Cases run one at a time regardless of `--concurrency` — each
holds the whole workspace's `mcp_servers` slot — and each turn is allowed up to an hour, matching
upstream's own budget. `--handbook-ingest STAGING` indexes each task's policy documents as a synced
source before its turn and withholds them from the workspace, which is the deployment a company
running ufo would have; it covers the tasks whose rubrics do not assert on a document filename and
whose workspace stages something other than documents.

The verdict is in-run and deterministic: no judge, no offline grading step. Each case's evidence
carries upstream's full scorecard, so the viewer shows which rubrics failed and why.

## Running the whole corpus

One case is one container, one conversation, and roughly 2–10M tokens; the corpus is ~18 hours and
several hundred dollars serially. Two things matter at that scale.

**Parallelise across stacks, not within one.** Each case points its workspace's `mcp_servers` slot at
its own container, so concurrency needs one workspace per lane — that is what `python -m evals.stack`
provisions. A container is named for its task and its workspace and publishes a port Docker assigns,
so lanes never touch each other's services, and a lane reclaims its own containers on the next
case.

**Prune Docker networks between batches.** The Docker carrier creates a network per conversation and
frees it only on idle reclaim, which is slower than cases complete. Left alone, the corpus exhausts
Docker's address pool part-way through and every later turn dies with `all predefined address pools
have been fully subnetted` — visible only as `turn produced no terminal transcript`. `docker network
prune -f` between batches, while nothing is attached, keeps the pool bounded.

Run in batches rather than one invocation: a run's archive records are written when its cases finish,
so an interruption costs the batch in flight rather than everything.

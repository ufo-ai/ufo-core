# HANDBOOK.md as a ufo eval suite

[HANDBOOK.md](https://github.com/surge-ai/handbook) (Surge AI, Apache 2.0) is 65 long-context
agentic instruction-following tasks across finance, medical billing, insurance, logistics, and HR.
Each task drops the agent into one company: a 44 KB–2.1 MB policy document, a stack of xlsx/pdf
working files, and a seeded inbox, Slack workspace, Jira queue, calendar, and storefront. Every
task modifies its base handbook's rules and thresholds, so no two tasks share a policy and
memorization does not carry. No frontier model exceeds 25%.

It measures exactly the shape ufo sells — cluttered multi-tool workspace, one policy document that
decides the answer, durable state as the deliverable — and it is unsaturated. That makes it a
capability-boundary suite alongside `jobbench` and `gdpval_100`.

## What upstream ships

| Piece | Shape |
| --- | --- |
| `tasks/<id>/instruction.md` | The member's request, one paragraph |
| `tasks/<id>/system_prompt.md` | Fixed date + which tool families exist |
| `tasks/<id>/environment/initial_workspace/` | Policy doc + working files, staged at `/workdir` |
| `tasks/<id>/environment/initial_external_services/` | Per-service seed JSON, staged at `/data` |
| `tasks/<id>/tests/rubrics.json` | 824 rubrics total (avg 12.7/task), each a `verify(workspace_path, external_services_path)` Python body |
| `docker/packages/{slack,google_mail,google_calendar,jira,shopify,core}` | Mock services, each a stdio MCP server |
| `docker/packages/mcp_proxy` | Aggregates them into one Streamable-HTTP endpoint at `/mcp` |
| `agent_harness/` | Harbor + OpenHands runner |

Rubric dependence splits across both halves of the environment, so neither can be stubbed: 475 of
824 rubrics read mutated service state (mailbox 232, Slack 180, Jira 63, calendar 22) and 318 read
`.xlsx` files the agent had to edit in place.

## What is already proven here

Driven through `extensions/mcp`'s own client seam (`mcp_client` / fastmcp Streamable-HTTP) against
the built `handbook_base` image:

| Link | Result |
| --- | --- |
| Tool discovery | 82 tools listed — `google_mail` 29, `jira` 19, `slack` 12, `shopify` 10, `google_calendar` 6, `syntara` 6 |
| Seeded file reads | `/workdir` returns `SOP.html`, `ap_ledger.xlsx`, `match_log_2025_Q2.xlsx`, `vendor_master.xlsx` |
| Seeded service reads | Slack channels (`#ap-exceptions`, …) and the task's inbox come back populated |
| Write durability | `slack__post_message` landed in `/data/slack/final.json` — the exact file the grader reads |
| Grading leg | `python /tests/sop_verifier.py` scored the untouched environment 3/9, `score=0.33` (the 3 passes are correctly-absent negative rubrics) — a clean no-op baseline |
| Toolset selection | Dropping `syntara_ds_all` from `WORLDBENCH_TOOL_SETS` yields 76 tools and no file tools |

The mock services need no reimplementation. They already speak the transport our MCP pack ships,
they snapshot state to `final.json` after every write, and their grader runs standalone against a
live container.

## Design

**Files ours, services theirs.** Drop `syntara_ds_all` so the container serves the 76 business
tools, and stage `initial_workspace` into the conversation workspace instead. Under the local
carrier (`sandbox/local.py`) commands run as host subprocesses directly over
`workspace_root/<conversation_id>`, so the agent reads and writes the same bytes the eval process
staged — no round-trip. This keeps our real file surface (documents, repl, coding) under test
rather than proxying file work through a generic MCP funnel, and it keeps the 2.1 MB SOP PDF away
from the MCP pack's 1 MiB response bound. Pin the suite to a local-carrier pack the way `jobbench`
pins `JOBBENCH_PACKS`.

**Grading in the container, never on the host.** `verifier_code` is arbitrary upstream Python. The
conversation's visible entries are copied into `/workdir`, the services snapshot to `/data`, and
`sop_verifier.py` runs by `docker exec`. Nothing upstream executes on the host and no host verifier
dependencies are needed.

**Case shape.** `instruction.md` is a single request, so this is a `CapabilityCase` (as
`gdpval_100`/`jobbench` are), not a `ScenarioCase`. `system_prompt.md` — which carries the task's
date — is preserved verbatim ahead of the envelope.

**Wiring.** The agent reaches the container through the existing `mcp` extension: point the
workspace's MCP server row at `http://localhost:<port>/mcp`. No new extension, no
new connector provider, no `eval_env` work.

### Suite layout, as built

| File | Job |
| --- | --- |
| `evals/handbook/data/upstream.json` | Pinned revision, uniform tool sets, verifier digest, per-task rubric count + tree digest |
| `evals/handbook/build.py` | Write the pin from a checkout (run once, commit the result) |
| `evals/handbook/corpus.py` | Verify a checkout against the pin; fail loud on any drift |
| `evals/handbook/environment.py` | Build the task image, start the proxy, land the MCP server row, copy the finished workspace in, run the verifier, tear down |
| `evals/handbook/ingest.py` | Index a task's policy documents as a synced source before its turn |
| `evals/handbook/runner.py` | Task → `CapabilityCase` (staged workspace, envelope) + the rubric-scoring grader |

There is no offline grading step: upstream's rubrics are deterministic, so the verdict is in-run and
the scorecard rides the case evidence. Two harness seams carry it: `CapabilityOutput.workspace_dir`,
the host directory the turn's `/workspace` was served from, and `CapabilityCase.prepare`, which runs
once that directory exists and before the turn opens.

## Tool surface and concurrency

**Tool surface fidelity.** Our `mcp` pack gives the model two generic tools
(`list_mcp_tools`, `call_mcp_tool`); the upstream harness registers all 82 by name. Ours is strictly
harder — the model pays a discovery round and must fetch schemas before calling — so a score here is
not directly comparable to the published leaderboard. We run our real shape and treat the gap as a
finding, since it is the same funnel Composio rides in production.

**Concurrency.** An MCP server row is workspace-scoped, so cases cannot share a workspace
concurrently. Cases are `exclusive=True` (the `scenario_task` precedent) and run one at a time
regardless of `--concurrency`; a sweep parallelises across stacks instead, one workspace per lane,
which `python -m evals.stack` provisions.

## Fidelity notes

- **Clock.** Upstream's own harness starts the proxy as
  `/app/scripts/start.sh --method http --port 8000` and passes no `--current-time`, so its services
  run on the real clock and the task's date reaches the model only through `system_prompt.md`. This
  suite does the same. (Deriving a clock from that prose is not an option regardless: 10 of the 65
  preambles state no parseable year, and one states no date at all.)
- **Their `core`/`syntara` bash tool is dropped** under this design. Nothing in the rubrics requires
  it; file work moves to our sandbox.
- **Image size.** `handbook_base` carries a full uv workspace plus an isolated OpenHands venv we
  never use.

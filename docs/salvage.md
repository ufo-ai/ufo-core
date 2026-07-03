# Salvage map — what ports from `~/src/metalcraft`

The previous repo stays deployed and untouched. Code ports by copy, arrives rewritten to this
repo's constitution (no k8s imports, workspace-scoped, `metalcraft.sdk` seams), and reads as if
written here. Audit provenance: the 2026-07-02 four-agent sweep (authority, tools, multiplayer,
subsystem liveness).

## Port (k8s-free, proven live)

| Asset | Old location | Lands in |
|---|---|---|
| Model clients (Anthropic/OpenAI, image-count trim, token accounting) | `src/metalcraft_agent/model_clients*` | core model abstraction |
| OpenRouter routing | model router path (02e5e417) | openrouter extension |
| Compaction + transcript design (`messages.json.lz4` + `compactions/<cid>/{before,after}`, monotonic seq) | trajectory-edge work on main (dbf86337) | core loop, blob-store-backed (filesystem default / S3), port as-is |
| Sandbox workspace mount (sandbox-fs, workspace-prefix-scoped creds, framework state above) | `src/metalcraft_agent/sandbox/workspace.py` + sandbox-fs | core sandbox S3 backend (filesystem backend = bind mount) |
| Artifact delivery (`share_file`: blob-store custody + TTL token URL) | `store/artifact_providers.py`, gateway artifact routes | core `share_file` + web surface |
| DBOS turn workflow/queue (durable turns, child workflows, cancel) | `src/metalcraft_agent/turn_work*` | core loop (bake in poll-interval + system-DB retention lessons) |
| Redis stream hub | `src/metalcraft_store/` stream hub | hubs extension (multi-instance) |
| OTel o11y (Logger, metrics, tracing contracts, redaction) | `src/metalcraft_o11y/` | core observability |
| Tool framework + sandbox tools (bash/read/write/edit, serve) | `src/metalcraft_agent/tools/` | core minimal tools; the rest become extensions |
| Browser/BUA (CDP driving, 4.1k LOC, live) | `src/metalcraft_agent/bua/`, `tools/browser.py` | assistant-pack browser subagent (extension) |
| Subagent spawn machinery (typed profiles, child turns) | `src/metalcraft_agent/subagents.py`, `tools/browser.py` | core typed subagents |
| Skills runtime (folders, load/list, digests) | `src/metalcraft_agent/skills/` | core skill loading; skill content → `packs/` |
| Connector framework + 48 providers + Composio client | `src/metalcraft_connectors/` (closed leaf — port framework + registry wholesale) | connectors extension(s) |
| Grant-through-chat flow (`/connect`, link/complete OAuth) | `gateway/tool_connections.py`, `ufo_commands.py` | core grant flow (the P4 exemplar — this shape is the house style) |
| Egress credential injection (secrets never in sandbox) | `src/metalcraft_servers/egress_proxy/` | core sandbox egress proxy (Docker network) |
| Slack surface (signature verify, thread keys, Block Kit writeback, speaker→member linking) | `contracts/slack.py`, gateway slack ingress, `slack_members.py` | core slackbot surface (de-k8s: link map → `member` rows) |
| ufo CLI client (streaming, sessions) | `scripts/ufo`, `src/metalcraft_cli/` | `metalcraft chat` |
| Spend evaluation (decide at inbound + per-step, scoped caps, price pins) | `contracts/spend_context.py`, `spend_status.py`, `store/price.py` | core accounting (SpendPolicy CRD → `spend_cap` rows) |
| Memory/knowledge (chunk/embed/fusion, pgvector, curated recall, `{subject, shared}`) | `src/metalcraft_brain/` | core memory + knowledge; the IndexBackend seam ports with it |
| turbopuffer backend (per-env, live-proven PR #108) | `src/metalcraft_brain/index.py` turbopuffer path | indexes extension |
| Source-sync contracts (cursor, pages, change commits) | `src/metalcraft_brain/sync/` | sdk `sources` contract |
| Product schema (RLS-era tables as reference) | `src/metalcraft_store/migrations/001_*.sql` | reference only — new schema is written fresh, workspace-scoped |
| Sandbox image recipe (baked toolchain, the two build traps) | `src/metalcraft_agent/sandbox/build_template.py` | Docker image build |
| E2B carrier | `src/metalcraft_agent/sandbox/` (e2b) | e2b extension |
| Eval harness (deterministic scenarios pattern) | `evals/` | ported once core loop exists |

## Leave behind (deliberately)

| Asset | Why |
|---|---|
| `metalcraft_k8s/`, CRDs/kinds, operator, admission, reconcile | k8s is the enterprise layer, not core |
| Gateway SAR/virtual-action machinery, dual-principal + speaker gates | caller-identity authority contradicts principle 4; the grant flow replaces it |
| `sandbox_proxy` kubectl minting, `metalcraft-agent-writer` RBAC | returns with the enterprise k8s layer |
| Self-improvement (`metalcraft_improve/`, Refinement CRD, miner, promotions) | rebuilt as a fully supported extension on `trajectories.read` + `agents.propose_change` (PR #62's offline-replay design is the reference); the CRD machinery dies |
| `metalcraft_cloud/` onboarding | signup is a hosted concern; core owns the onboarding *engine*; identity is core |
| Helm chart, tenant kustomize, Terraform | replaced by `metalcraft bundle` |
| ~10 duplicate tools (`echo`, `website`, 3× serve, `save_custom_skill`, `confirm_action`, `update_todo_status`, `search_vertical`, `glob`/`grep`) | tool-premium rule; collapse on port |

# Salvage map — `~/src/metalcraft` → selfhost

The previous repo stays deployed and untouched; code ports **by copy**, arrives rewritten to this
repo's constitution (no k8s imports, `workspace_tx` scoping, `selfhost.sdk` seams), and reads as if
written here. Old paths are relative to `~/src/metalcraft/src/`. Unit column = `docs/plan.md`.

Working against the old repo:
- API keys: `~/src/metalcraft/.env` has `ANTHROPIC_API_KEY` (+ `OPENAI_API_KEY`, `E2B_API_KEY`) —
  copy what a unit's proof needs into this repo's `.env` (gitignored).
- The old repo's local stack holds `:5432`/`:8080` when up; `make -C ~/src/metalcraft
  docker-down-all` before `compose up` here, or bind a different Postgres port.
- Old-repo PR numbers cited in rows (`#62`, `#108`) are viewable via `gh pr view <n>` from
  `~/src/metalcraft`.

## → core

| Old file(s) | Lands in | Unit | Transformation |
|---|---|---|---|
| `metalcraft_agent/model_clients.py` | `models/` (split anthropic/openai) | U1 | drop OpenRouter routing; keep streaming, usage accounting, image-count trim. Backports from upstream fixes (landed after the U1 port): raise `ModelResponseTruncated` on `finish_reason=length` instead of letting cut-off tool-call JSON fail in json.loads (24723e46); flag errored tool results in-band ("[tool error] " prefix) on the chat-completions path where is_error has no field (24723e46); treat an empty 200 completion as a retryable provider failure, never success (ed876401) |
| `metalcraft_agent/dbos_turns.py`, `turn_work.py`, `turn_host.py`, `turn_store.py` | `loop/queue.py`, `loop/engine.py` | U1 | de-k8s (no CRD projection/readiness); queue partition key = conversation_id; keep the 0.1s poll + system-DB retention lessons |
| `metalcraft_agent/turn_engine.py` | `loop/engine.py` (model rounds), `loop/compaction.py` | U1/U5 | drop substring injection scan (spec divergence in old repo); keep round loop, exhaustion policy |
| `metalcraft_store/thread_store.py` | `loop/transcript.py` | U1 | `<ns>/threads/…` keys → `conversations/<cid>/…`; S3-only → BlobStore |
| `metalcraft_store/object_store.py`, `metalcraft_brain/object_store.py` | `blob.py` | U1 | merge; add FilesystemBlobStore (new, default) |
| `metalcraft_store/db.py`, `migrations/001_product_tables.sql` | `schema/` | U1 | reference only — new schema written fresh, workspace-scoped, no RLS |
| `metalcraft_store/{json,env}.py` | `config.py` + shared prims | U1 | fold |
| `metalcraft_o11y/{emit,logging,metrics,sdk,tracing}.py` | `o11y.py` | U1 | collapse to one module; OTel APIs only |
| `metalcraft_store/price.py` | `accounting.py` prices | U1 | refresh model list; fix lossy per-token ints (micro-USD/MTok); PRICE_DIGEST returns with U7 audit columns |
| `metalcraft_cli/gateway_client.py`, `scripts/ufo`, `gateway/channels/ufo*.py` | `surfaces/cli.py` + `cli.py` (`selfhost chat`) | U1 | one CLI; sessions stay; drop signin bridge (member token instead) |
| `metalcraft_agent/tools/base.py` | `tools/registry.py`, `tools/context.py` | U2 | ToolContext replaces env-threading |
| `metalcraft_agent/tools/sandbox.py` | `tools/builtins/` (`bash read write edit`) + `extensions/sites` (the serve tools) | U2 | drop `glob`, `grep`, `echo` (bash + ripgrep subsume); keep read-before-write + image/PDF read; `website` and the 3 serve tools move to the sites extension |
| `metalcraft_servers/sandbox_proxy/{proxy,server}.py` (minus kubectl mint), `metalcraft_servers/egress_proxy/server.py`, `metalcraft_store/egress.py` | `sandbox/proxy/` | U2 | MERGE into one proxy: sentinel swap, host/account scoping, wire metering; rules derived, no register API |
| `metalcraft_agent/sandbox/workspace.py`, `executor/sandbox_fs_mount.py`, `metalcraft_store/sandbox_fs_creds.py` | `sandbox/` mount (S3 backend) | U2 | filesystem backend = plain bind mount (new); keep `mountpoint -q` health check |
| `metalcraft_agent/sandbox/{sbx,sbxfs}` (in-sandbox toolchain), image recipe | `sandbox/image/` | U2 | rebuild Dockerfile fresh; two traps learned on e2b: build-time env vars don't reach runtime (set ENV in the Dockerfile), and per-step USER matters (apt fails non-root) |
| `metalcraft_agent/sandbox/local.py`, `executor/executor_sandbox.py` | `sandbox/local.py` (core default), `extensions/docker/`, `extensions/e2b/` | U2 | local carrier is the zero-dependency core default; Docker/E2B are carrier extensions; async wait replaces the sync `time.sleep` poll |
| `metalcraft_store/secrets.py` | `credentials.py` | U3 | k8s Secrets → encrypted PG rows; slot-scoped access |
| `metalcraft_brain/{chunk,embed,fusion,index}.py` | `memory/{embed,index}.py` | U4 | IndexBackend seam ports intact; pgvector impl stays |
| `metalcraft_brain/{store_service,store_writes,store_work,store_reindex,store_distill}.py` | `memory/service.py` + jobs | U4 | Store CRD → plain workspace memory; `{subject, shared}` recall filter stays |
| `metalcraft_brain/{memory,memory_inbox,memory_curation,memory_curation_runtime}.py` | `memory/pipeline.py` default condenser | U4 | curation loop becomes the core Condenser; interface opens for gbrain-style replacements |
| `metalcraft_brain/sync/{contracts,jobs,persistence,runtime}.py` | `sources.py` + sync driver | U4 | Source CRD → source rows; cursor/claim/commit shape stays |
| `metalcraft_agent/tools/knowledge.py` | `tools/builtins/memory*` | U4 | `store_search`/`file_fetch`/`load_sessions` fold into `memory_search` modes |
| `metalcraft_agent/subagents.py` | `loop/subagents.py` | U5 | typed profiles become the registry; spawn = DBOS child |
| `metalcraft_agent/skills/__init__.py` + `skills/` content | `tools/builtins/load_skill.py` + `packs/` | U5 | `list_skills` folds into `load_skill`; `save_custom_skill` folds into `share_file` |
| `metalcraft_agent/tools/files.py` | `tools/builtins/share_file.py`, `load_skill` | U5 | one artifact-share path |
| `metalcraft_agent/tools/interaction.py` | `tools/builtins/ask_user.py` | U5 | `confirm_action` folds in; `pause_and_wait`/`send_notification` deferred to extensions |
| `metalcraft_store/{artifact_delivery,artifact_providers}.py`, `gateway/{artifacts,tokens}.py` | `share_file` + web delivery | U5/U6 | TTL token URL over BlobStore; drop provider zoo (fs/S3 only) |
| `metalcraft_contracts/{slack,slack_members}.py`, `gateway/{ingress,channel_ingress,inbound}.py` (slack parts), `executor/{slack_thread,channel_writeback*}.py`, `jobrunner/channel_event*.py` | `extensions/slack` (on the core surface seam: `ext/surface.py` + `surfaces/admission.py`) | U6 | link map ConfigMap → `surface_identity` rows; drop SAR/dual-principal; keep signature verify, thread keys, writeback claims, idempotency-on-message-identity; two-way file attachments stream end-to-end — `share_file` records a `shared_artifact` the writeback streams into the posted reply's thread, inbound Slack files stream into the workspace before the turn runs (b3edf476, a9d50f99) — plus Block Kit markdown rendering for replies (4a0bc3af) |
| `gateway/{streams,queue_sequences}.py` | `surfaces/web.py` + hub tail | U6 | Redis tail → Hub interface |
| `metalcraft_contracts/{spend_context,spend_status,spend}.py`, spend parts of `contracts/actions.py`, `executor/executor_observer.py` | `accounting.py` | U7 | CRD SpendPolicy → `spend_cap` rows; keep inbound + per-step decide, park/reject; close the scheduled-fire bypass (old bug) |
| sandbox_proxy metering (`server.py` ledger writes) | `sandbox/proxy/` MeterRule | U7 | same ledger as step metering |
| `gateway/tool_connections.py`, `/connect` flow in `gateway/channels/ufo_commands.py` | `grants.py` | U8 | THE house-style exemplar; persist account id on the Grant row (old gap: audit-only) |
| `metalcraft_contracts/{turns,tool_calls,content,limits,tracing,jobs}.py` | core types, as needed per unit | U1+ | cherry-pick; no k8s condition/readiness machinery |

## → extensions / packs (port later, tracked here)

| Old | Becomes | Notes |
|---|---|---|
| `metalcraft_connectors/{base,composio,composio_proxy,errors,integration_descriptors,mcp,registry}.py` + 48 provider dirs | `extensions/sources` (read-only REST connector framework + providers) + `extensions/connectors` (Composio broker: dynamic tools, OAuth, the `composio` auth-proxy backend) | U8; the framework consumes the pluggable auth-proxy seam (`composio` / `direct` BYOK), never importing the broker; providers land per tranche |
| `metalcraft_agent/bua/` (18 files), `browser.py`, `browser_cdp.py`, `tools/browser.py`, `sandbox/browser_runtime.py` | `extensions/browser` (bundled by the assistant pack) | U10 |
| `metalcraft_agent/tools/web.py` | `extensions/exa` (Exa search/fetch; bundled by the assistant pack) | U10; `search_web`, `fetch_url`, `search_vertical` each a distinct tool |
| `metalcraft_agent/tools/todos.py` | `extensions/todos` | `update_todo_list`/`update_todo_status` over the pack's scoped store, keyed by conversation; `<todo_list>` prompt section |
| `metalcraft_agent/tools/repl.py` | `extensions/repl` | `js_repl` (Node) + `xlsx_repl` (Python/openpyxl), persistent accumulate-and-run over the sandbox |
| `metalcraft_agent/tools/sandbox.py` (serve tools) | `extensions/sites` | `website`/`start_server`/`deploy_website`/`publish_website` + the `website_building` subagent; the served URL is sandbox-internal (the browser tools and js_repl reach it) — no public reverse-proxy seam yet, so exposure is via `share_file` |
| `metalcraft_agent/platform_control_tools.py`, `metalcraft_contracts/scheduling.py` | scheduled-tasks extension | U10; CronJob → JobSpec |
| `executor/sandbox_e2b.py` | e2b carrier extension | post-U10; include the dead-pooled-connection-as-dropped-stream handling (393d832c) |
| `metalcraft_store/stream_hub.py` | redis hubs extension | post-U10 |
| `metalcraft_brain/index.py` (turbopuffer path) | turbopuffer indexes extension | post-U10 |
| `metalcraft_improve/` offline-replay loop, now MERGED on main (8e20fa70: proposer → eval cron → gate w/ consecutive-pass stability → audited CAS promotion, candidate suppression) | self-improvement extension | post-U10; port from main, not a PR. Grounding differences here: the corpus is `trajectories_read` over full transcripts (selfhost cut the system_diagnostic side-channel tool); promotion goes through `propose_change` (governed), never a direct write; the eval cron is a manifest `job`; suppression state lives in the extension's own scoped store |
| `evals/` harness (deterministic scenario pattern) | eval harness | post-U10 |

## Leave behind (deliberately, with reasons)

| Old | Why |
|---|---|
| `metalcraft_k8s/`, `metalcraft_contracts/kinds/`, operator, admission, reconcile, conditions/readiness | k8s is the enterprise layer |
| `gateway/{auth,dispatch,audit,members,persistence}.py` SAR/virtual-action machinery, dual-principal + speaker gates | caller-identity authority contradicts principle 4; grants replace it |
| sandbox_proxy kubectl mint (apiserver rewrite, token mint), `metalcraft-agent-writer` RBAC | returns as the enterprise rewriter module |
| `metalcraft_store/{payload,payloads}.py` (`trajectory_event` + `payload/`) | second trajectory representation; transcript store is the one representation |
| `metalcraft_agent/{agent_projection,platform_tools,in_memory,env,tool_runtime}.py` | CRD projection/catalog plumbing; frozen-per-turn-config concept survives in `AgentRuntime` |
| `metalcraft_agent/tools/{diagnostics,platform,broker,egress,mcp,composio,connector_tools}.py` | diagnostics: cut (o11y subsumes); the connector invocation set re-enters via `extensions/connectors` |
| `metalcraft_cloud/`, `deploy/`, `charts/`, Terraform | signup is hosted-side; `selfhost bundle` replaces install packaging |
| `metalcraft_store/{catalog_registry,catalog_registry_writes,server,migrate}.py`, `jobrunner/improve_*` | registry/catalog + improve runtime ride k8s-era machinery; superseded by packs + extension store |
| Refinement CRD machinery (`contracts/kinds/refinement.py`, `k8s/kinds/refinement.py`, gateway promotions) | self-improvement returns as an extension, not a kind |

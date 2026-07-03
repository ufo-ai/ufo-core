# Build plan

Each unit is one fresh session, independently reviewable, proven end-to-end before the next
(realistic input → durable state → usable output). Order is dependency order.

| Unit | Delivers | Proven by |
|---|---|---|
| U1 heartbeat | uv workspace scaffold; Postgres schema (workspace/member/agent/conversation/turn/ledger); DBOS turn workflow + queue (per-conversation serialization, cancel from day one); BlobStore (filesystem default, S3 impl) holding `messages.json.lz4`; ModelClient (Anthropic+OpenAI); in-process stream hub; OTel spans; `selfhost chat` streaming | a real chat turn against Anthropic; transcript in the blob store, ledger rows in PG; mid-turn cancel works |
| U2 sandbox + tools | Docker carrier (pinned image, workspace bind-mount from blob store); the core sandbox proxy (sole egress route, sentinel swap, grant-scoped rules, rewriter seam); `bash`/`read`/`write`/`edit`; carrier interface | agent writes + runs code in the container; egress denied except proxy; a sentinel credential swaps on the wire; sandbox cannot reach transcripts above `workspace/` |
| U3 extension system | `selfhost.sdk`, Manifest, entry-point loading, ExtensionContext (incl. `trajectories.read`, `agents.propose_change`), credential store (encrypted BYOK slots), CI gate (extensions import sdk only); one sample extension exercising every point | sample extension registers a tool/job/route/credential and all fire; a propose_change round-trips through approval |
| U4 memory | memory tables, IndexBackend seam (pgvector default), embed/recall (`{member, shared}` subjects), auto-inject, `memory_search`/`memory_update`; derivation-pipeline + triggers seams (source pages → condense → memory) | facts recalled across conversations, per-member isolation |
| U5 loop depth | compaction (transcript + before/after records), typed subagents, skill loading + `packs/` layout, `ask_user`/`share_file`/`spawn_subagent` | long conversation compacts correctly; a typed subagent round-trips schema I/O |
| U6 surfaces | slackbot (signature verify, thread conversations, member linking, writeback), web chat; onboarding flow engine + first-owner bootstrap | same agent answers in Slack thread and web; second member links and has own memory subject |
| U7 accounting | spend caps (scope × dimension × window, reject/park), inbound + per-step evaluation, wire metering in the sandbox proxy, realtime cost on stream, CLI/web rollups | cap breach parks a turn; an in-sandbox API call meters; costs visible live |
| U8 connectors | port connector framework + Composio client as the connectors extension; grant-through-chat (`/connect` → OAuth → grant row); sources syncing into memory | OAuth a real provider in chat; agent calls it; source pages recalled |
| U9 bundling + store | `selfhost.toml` (config single-source), `selfhost bundle` (OCI image + pinned config + lockfile), `selfhost serve`; instance heartbeat + scale-out boot guard (live peer + in-process hub or filesystem blobs → refuse to start); extension store (`selfhost ext search/install`, digest pinning, disabled-store = bundle-only) | one bundle boots the full stack on a clean machine; an extension installs from the store and fires; a misconfigured second instance refuses to boot |
| U10 packs | assistant pack (deep/wide research, browser subagent via BUA port, office docs); scheduled-tasks extension | pack onboarding installs skills; a cron job fires a real turn |

Standing gates from U3 on: sdk-only imports in `extensions/`; no k8s imports anywhere; no vendor
o11y SDKs (OTel APIs only); Redis only inside the hubs extension; tool-count budget (new tool
requires proving no existing tool subsumes it).

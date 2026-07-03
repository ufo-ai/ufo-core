# Build plan

Each unit is one fresh session, independently reviewable, proven end-to-end before the next
(realistic input → durable state → usable output). Order is dependency order.

| Unit | Delivers | Proven by |
|---|---|---|
| U1 heartbeat | uv workspace scaffold; Postgres schema (workspace/member/agent/conversation/turn/transcript/ledger); ModelClient (Anthropic+OpenAI); minimal loop (no sandbox/tools); `metalcraft chat` streaming | a real chat turn against Anthropic, transcript + ledger rows in PG |
| U2 sandbox + tools | Docker carrier (pinned image, workspace mount, default-deny egress via proxy); `bash`/`read`/`write`/`edit`; carrier interface | agent writes + runs code in the container; egress denied except proxy |
| U3 extension system | `metalcraft.sdk`, Manifest, entry-point loading, ExtensionContext, credential store (encrypted BYOK slots), CI gate (extensions import sdk only); one sample extension exercising every point | sample extension registers a tool/job/route/credential and all fire |
| U4 memory | memory tables, pgvector embed/recall (`{member, shared}` subjects), auto-inject, `memory_search`/`memory_update`; triggers seam (source pages → memory) | facts recalled across conversations, per-member isolation |
| U5 loop depth | compaction (transcript + before/after records), typed subagents, skill loading + `packs/` layout, `ask_user`/`share_file`/`spawn_subagent` | long conversation compacts correctly; a typed subagent round-trips schema I/O |
| U6 surfaces | slackbot (signature verify, thread conversations, member linking, writeback), web chat; onboarding flow engine + first-owner bootstrap | same agent answers in Slack thread and web; second member links and has own memory subject |
| U7 accounting | spend caps (scope × dimension × window, reject/park), inbound + per-step evaluation, realtime cost on stream, CLI/web rollups | cap breach parks a turn; costs visible live |
| U8 connectors | port connector framework + Composio client as the connectors extension; grant-through-chat (`/connect` → OAuth → grant row); sources syncing into memory | OAuth a real provider in chat; agent calls it; source pages recalled |
| U9 bundling | `metalcraft.toml` (config single-source), `metalcraft bundle` (OCI image + pinned config + lockfile), `metalcraft serve` | one bundle boots the full stack on a clean machine |
| U10 packs | assistant pack (deep/wide research, browser subagent via BUA port, office docs); scheduled-tasks extension | pack onboarding installs skills; a cron job fires a real turn |

Standing gates from U3 on: sdk-only imports in `extensions/`; no k8s/Redis/S3/DBOS imports
anywhere; tool-count budget (new tool requires proving no existing tool subsumes it).

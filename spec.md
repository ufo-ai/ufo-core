# Metalcraft Spec

Metalcraft is an agent runtime a developer can run, read, and extend: a hard-to-vary **core**
(sandboxed agent loop, memory, surfaces, accounting, model abstraction, the extension system) plus
**extensions** through which nearly every easy-to-vary capability is built — connectors, data
sources, triggers, tools, subagents, onboarding. A **workspace** hosts one team and its agents.
Agents accumulate capabilities through **grants made in chat** — never borrowed from whoever is
speaking.

## Principles

Long-term product principles (the destination all design serves):

1. Multiplayer / permissioned.
2. Open source / on-prem / hosted.
3. Agents build with real infrastructure (Kubernetes is the enterprise upgrade, wrapping this core).
4. Agents are granted access via connectors through chat — never via caller identity.

Core doctrine: **if a capability can be an extension, it is not core.** Core earns a module only
when extensions cannot express it: the loop, the sandbox, persistence, surfaces, accounting, model
access, and the extension system itself. The example-extension list at the bottom is the acceptance
test for the extension API — every entry must be expressible without touching core.

## Fixed decisions

| Decision | Value |
|---|---|
| Language | Python 3.12+, uv. Monorepo: `core/` + `extensions/*` + `packs/*` (uv workspace). |
| Persistence | Postgres only. One schema; every row carries `workspace_id`; a deploy serves ONE workspace (hosted multi-workspace is the enterprise layer). Blobs (transcripts, compaction records, skill content, artifacts) live in Postgres — no object store in core. |
| Queue/stream | Postgres queue tables + worker poll; live deltas over LISTEN/NOTIFY with poll fallback. No Redis, no DBOS. |
| Sandbox | Docker is the default carrier, built into core. E2B (and any other carrier) is an extension. No unsandboxed mode. |
| Models | Anthropic + OpenAI direct clients behind one `ModelClient` interface. No router service, no OpenRouter. |
| Kubernetes | Absent from core by construction. The enterprise offering later wraps core with k8s (principle 3); nothing in core may assume or import it. |
| CLI | One CLI: `metalcraft` (`chat`, `serve`, `bundle`, admin verbs). |

## Workspace model

Tables (all keyed by `workspace_id`, `created_at`, `updated_at`):

| Table | Owns |
|---|---|
| `workspace` | The team unit: name, config digest. |
| `member` | A human. Role: `owner` or `member`. Surface identities link here (Slack user id, CLI token, web session) — one human, many surfaces, one memory subject. |
| `agent` | A configured agent: name, prompt, model policy, granted tool set, skill packs, memory scope. |
| `grant` | Agent ← capability binding: a connector account, a credential slot, a tool group. Records grantor, when, via which conversation. Created through chat; the speaker gates the *granting act*, never subsequent use. |
| `credential` | BYOK secrets, encrypted at rest. Slots are declared by extensions; values are workspace-scoped. |
| `conversation` | Surface context ↔ queue key (Slack thread, CLI session, web session). Private to its creating member unless the surface is shared (a Slack channel is shared by construction). |
| `turn`, `turn_step`, `transcript` | The loop's durable log: lifecycle, steps, full-conversation transcript + compaction records. |
| `memory_item`, `memory_summary` | Memory: subject = member or `shared`. |
| `ledger` | Metered usage: every model and tool call, priced. |
| `spend_cap` | Caps by scope (`workspace` \| `member` \| `agent`), dimension, window; `reject` or `park` on breach. |
| `job` | Recurring/one-time background work (source sync, triggers, extension jobs). |

## Agent loop

One workflow: inbound → admission (identity, spend preflight) → queue row → worker turn → steps →
terminal frame. A client's wait always ends — the terminal state commits on the failure path too.

- **Tool calling** — typed registry; per-call metering; results bounded before hitting the model.
- **Skill loading** — skills are folders of files (SKILL.md + assets), mounted into the sandbox on
  `load_skill`; packs are collections of skills plus onboarding steps.
- **Typed subagents** — a registry of profiles (name, prompt, tool subset, input/output schema);
  spawn = child turn with parent linkage; foreground awaits, background returns an id. Extensions
  register profiles.
- **Compaction** — full-conversation transcript with monotonic seq + before/after compaction
  records (port of the shipped design, Postgres-stored); history compacts as it approaches the
  model window so a long turn never exceeds it.
- **Memory** — recall (lexical + vector over pgvector, subject ∈ {member, shared}) auto-injected at
  turn load; `memory_update` writes; triggers feed it from data sources.
- **Minimal built-in tools** — `bash`, `read`, `write`, `edit`, `memory_search`, `memory_update`,
  `ask_user`, `spawn_subagent`, `load_skill`, `share_file`. Everything else arrives via extensions.
  Two tools where one would do is a defect.

## Sandboxing

Every turn executes tools in a per-conversation sandbox: Docker container from a pinned image
(baked toolchain), workspace-mounted working dir, default-deny network egress through the core's
egress proxy (credential injection happens at the proxy; raw secrets never enter the sandbox).
Carrier interface: `create / exec / mount / route / destroy` — Docker implements it in core; E2B
implements it as an extension.

## Extension system

An extension is a Python package exposing one entry point (`metalcraft.extension`) that returns a
`Manifest`. Extensions import only the public SDK (`metalcraft.sdk`); a CI gate forbids reaching
into core internals.

Manifest registers (each optional):

| Point | Contract |
|---|---|
| `tools` | Typed tool defs + handlers; appear in agents' granted tool sets. |
| `subagents` | Typed subagent profiles. |
| `connectors` | Provider actions behind the connector framework; OAuth via the grant flow. |
| `sources` | Data feeds: `sync(cursor) -> pages` run as jobs; pages land in memory/knowledge. |
| `triggers` | Data → memory (and → invocation): hooks on source pages and platform events. |
| `jobs` | Recurring/one-time background work. |
| `routes` | HTTP endpoints under `/ext/<name>/` (webhooks, OAuth callbacks, plugin UIs). |
| `credentials` | Named BYOK slots the workspace must fill (drives onboarding). |
| `onboarding` | Steps contributed to the workspace/pack onboarding flow. |
| `packs` | Bundled skill packs. |

`ExtensionContext` (capability-scoped, handed to every handler): workspace-scoped store access,
`credentials.get(slot)`, `memory.write(...)`, `invoke(agent, input, conversation=...)`,
`schedule(job)`. Extensions never see raw DB handles or other workspaces.

## Surfaces (core)

| Surface | Identity | Conversation key |
|---|---|---|
| Slackbot | Slack user → linked member | channel:thread_ts (shared) |
| CLI | member token | session (private) |
| Web | web session → member | session (private) |

Onboarding flow engine is core (steps are contributed by extensions/packs); first-run creates the
workspace and its first `owner`.

## Accounting / billing

Every model call and tool call meters into `ledger` in the same commit as the step. Realtime
visibility: live per-turn cost on the stream, workspace/member/agent rollups in CLI and web. Caps
evaluated at inbound and per-step; `reject` refuses new turns, `park` suspends. Prices are a pinned
table per model; BYOK usage still meters (visibility without billing).

## Model abstraction

`ModelClient`: `complete(messages, tools, stream)` + token accounting + provider image/content
limits. Implementations: Anthropic, OpenAI. Model policy per agent (`auto` routes by task class);
keys come from `credential` slots or deploy config.

## Deploy config bundling

One declarative file, `metalcraft.toml`: Postgres URL, model keys (env refs), enabled extensions +
versions, installed packs, surface config (Slack app, web host), sandbox carrier, spend defaults.
`metalcraft bundle` produces a runnable artifact (OCI image + pinned config + lockfile) — the same
bundle installs OSS, on-prem, or hosted.

## Example extensions (the API's acceptance tests)

| Extension | Points it exercises |
|---|---|
| Agent-guided education / onboarding | onboarding, tools, packs |
| Scheduled tasks (cron / one-time) | jobs, invoke, tools |
| GH code review on PR + auto-merge | routes (webhook), credentials, invoke, tools |
| Service self-improvement / bug-fixing from o11y | sources (o11y), jobs, invoke |
| Security review | tools, subagents, packs |
| gbrain / CRM / ATS | connectors, sources, triggers, tools, packs |
| Websites | tools (sandbox serving), routes |

Skill packs (content, not code): **assistant** (deep research, wide research/browse, browser
subagent, office docs), **startup** (onboarding, YC document questions, bookface search, deals,
fundraising docs, marketing/ad loops), **support bot** (onboarding: connect knowledgebase + keys;
intercom-style website plugin via the websites extension).

## Non-goals (core, now)

- No Kubernetes, CRDs, operators, or RLS multi-tenancy (the `workspace_id` column is the only
  concession to the future).
- No object store, Redis, DBOS, or router services.
- No self-improvement machinery in core.
- No second representation of any fact: one transcript store, one schema source, one config file.
- No tool that another tool or `bash` subsumes.

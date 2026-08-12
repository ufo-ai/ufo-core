# RFCs

Design records for ufo, numbered and status-tracked (React/Rust-style). Each file carries
`rfc`/`title`/`status`/`date` frontmatter. This directory holds RFCs realized on `main` (plus the
audit working record); proposals never implemented on `main` live in [`archive/`](archive/) with
their reasoning intact — nothing is deleted. Start from [`0000-template.md`](0000-template.md).

**Status:** `proposed` (open) · `accepted` (agreed, unbuilt) · `implemented` (built + merged) ·
`superseded` (built, then replaced by a later RFC) · `rejected` · `withdrawn`.

| # | Title | Status |
|---|---|---|
| [0006](0006-gbrain-graph-extraction.md) | gbrain graph extraction | superseded |
| [0008](0008-durable-recovery.md) | Durable recovery — sub-turn checkpointing, `wide_*` + browser resume | implemented |
| [0009](0009-context-compression.md) | Structured context compression — a pipeline, not one summarize call | implemented |
| [0010](0010-degraded-features-audit.md) | Degraded-features audit — what was silently crippled | accepted |
| [0011](0011-ufo-hosted-service.md) | ufo — the merged hosted service: one repo, one database, RLS | implemented |
| [0012](0012-turn-speaker-and-private-handoffs.md) | Turn speaker and private handoffs | implemented |
| [0016](0016-self-improvement-eval-impact.md) | Self-improvement — the loop's exit, its impact meter, and a human signal | proposed |
| [0017](0017-workspace-objects.md) | Workspace objects — registered kinds, YAML CRUD in chat | implemented |
| [0018](0018-model-spec-single-source.md) | Model spec — one record per model, the single source of truth | accepted |
| [0019](0019-shared-brain.md) | Shared brain — scope and audience | proposed |
| [0020](0020-hosted-sites.md) | Hosted sites — sandbox ingress and the access-controlled frame | proposed |
| [0022](0022-self-describing-actions.md) | Self-describing actions — one declaration a panel, a form, and a model all read | proposed |
| [0023](0023-portal-architecture.md) | Portal architecture — a view kernel, declared views, and one design system | proposed |
| [0024](0024-conversation-slots.md) | Conversation slots — extension details beside a thread | proposed |
| [0026](0026-terminal-as-sandbox.md) | Terminal as sandbox — the CLI member's own directory is the workspace | proposed |

`0006` shipped and `0019` replaced its member-facing surface with `memory_search`; the record stays
for its reasoning. `0010` is an audit, not a proposal — `accepted` marks its findings as the working
record.

## Archive — not implemented on `main`

| # | Title | Status |
|---|---|---|
| [0001](archive/0001-capability-model.md) | Capability model — core capabilities gated on backends | proposed |
| [0002](archive/0002-deploy-service.md) | Deploy service — `selfhost deploy` + a Heroku-style hosted layer | proposed |
| [0003](archive/0003-k8s-layer.md) | selfhost-core as the backend runtime for the `selfhost-k8s` control plane | proposed |
| [0004](archive/0004-deploy-via-k8s.md) | `selfhost deploy` on the `selfhost-k8s` backend | proposed |
| [0005](archive/0005-palantir-foundry-parity.md) | Palantir AIP / Foundry parity (inbound API, ontology vs gbrain) | proposed |
| [0007](archive/0007-github-extension-sources.md) | Install extensions from GitHub URLs (public + private) | proposed |
| [0013](archive/0013-workspace-resources.md) | Workspace resources — governed CRUD, revisions, settings, pack agents, agent messaging | withdrawn |
| [0015](archive/0015-extension-host.md) | Extension host — a sandboxed, JS-only channel for third-party extensions | accepted |
| [0025](archive/0025-deep-work.md) | Deep work — control over what may proceed, and insight into whether it is converging | rejected |

`0002`–`0004` were settled by 0011 (repo topology; the Postgres tier) and never shipped as written.
`0025` is the one rejected on measurement rather than on argument: both closure forms it proposed that
an agent performs scored below no discipline at all on HANDBOOK.md, and the record is kept for the
reason why.

# RFCs

Design proposals and audits for selfhost-core, numbered and status-tracked (React/Rust-style). Each
file carries `rfc`/`title`/`status`/`date` frontmatter. A rejected or withdrawn RFC **stays here**
with its status set — nothing is deleted, so the reasoning survives. Start from
[`0000-template.md`](0000-template.md).

**Status:** `proposed` (open) · `accepted` (agreed, unbuilt) · `implemented` (built + merged) ·
`rejected` · `withdrawn`.

| # | Title | Status |
|---|---|---|
| [0001](0001-capability-model.md) | Capability model — core capabilities gated on backends | proposed |
| [0002](0002-deploy-service.md) | Deploy service — `selfhost deploy` + a Heroku-style hosted layer | proposed |
| [0003](0003-k8s-layer.md) | selfhost-core as the backend runtime for the `selfhost-k8s` control plane | proposed |
| [0004](0004-deploy-via-k8s.md) | `selfhost deploy` on the `selfhost-k8s` backend | proposed |
| [0005](0005-palantir-foundry-parity.md) | Palantir AIP / Foundry parity (inbound API, ontology vs gbrain) | proposed |
| [0006](0006-gbrain-graph-extraction.md) | gbrain graph extraction — the `knowledge_graph` extension | implemented |
| [0007](0007-github-extension-sources.md) | Install extensions from GitHub URLs (public + private) | proposed |
| [0008](0008-durable-recovery.md) | Durable recovery — sub-turn checkpointing, `wide_*` + browser resume | proposed |
| [0009](0009-context-compression.md) | Structured context compression — a pipeline, not one summarize call | proposed |
| [0010](0010-degraded-features-audit.md) | Degraded-features audit — what was silently crippled | accepted |
| [0011](0011-ufo-hosted-service.md) | ufo — the merged hosted service: one repo, one database, RLS | accepted |
| [0012](0012-turn-speaker-and-private-handoffs.md) | Turn speaker and private handoffs | proposed |

Note: `0006` is `implemented` for its deterministic tier + graph substrate (the extension is on
`main`); its LLM prose tier lands with the metered extension model client. `0010` is an audit, not a
proposal — `accepted` marks its findings as the working record.

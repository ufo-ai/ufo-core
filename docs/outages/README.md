# Outages

Postmortems, numbered and status-tracked like [RFCs](../rfcs/README.md). Each file carries
`outage`/`title`/`date`/`envs`/`impact`/`status` frontmatter. Every incident that pages a human or
loses a capability (telemetry, deploys, a tenant's data path) gets one, written while the evidence
is still on the nodes. Start from [`0000-template.md`](0000-template.md).

**Status:** `open` (follow-ups remain) · `closed` (every follow-up landed or explicitly dropped).
A postmortem is blameless and evidence-first: every claim in the root-cause chain cites what proved
it (a log line, a trace, a dump path). Follow-ups name their PR when they land — the postmortem is
the index of what the incident taught, and it stays `open` until the lessons are code.

| # | Title | Date | Envs | Status |
|---|---|---|---|---|
| [0001](0001-testing-ena-panic-dns-blackout.md) | ENA kernel panic → CoreDNS blackout → 6h telemetry blind spot | 2026-07-10 | testing | open |
| [0002](0002-terraform-blanked-testing-runtime-secrets.md) | Aged-out secret version → terraform re-creates its placeholder → testing runtime credentials blank | 2026-08-21 | testing | open |

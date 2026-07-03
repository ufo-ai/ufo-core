# selfhost

An agent runtime you can run, read, and extend: a hard-to-vary core — sandboxed agent loop, memory,
Slack/CLI/web surfaces, accounting, Anthropic/OpenAI model abstraction — plus an extension system
for everything else (connectors, data sources, triggers, tools, subagents, onboarding).

- `spec.md` — source of truth: doctrine, fixed decisions, workspace model, non-goals.
- `docs/contracts.md` — core contracts per unit (sections delete as code lands).
- `docs/plan.md` — build order. `docs/salvage.md` — file-level port map from the previous repo.
- `core/` — the axiomatic unit. `extensions/` — first-party extensions. `packs/` — skill packs.

Requires Postgres and Docker.

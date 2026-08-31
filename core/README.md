# Core

| Path | Owns |
|---|---|
| `src/ufo/harness/` | Agent execution: the stdlib-only engine core (gated), model providers, sandbox protocol and carriers, replay-safe durability, observability plumbing. |
| `src/ufo/runtime/` | The durable tenant host and the platform contracts: the turn workflow and its harness adapters; access, auth, billing, seats, workspace scope, turns, surfaces, sources, kinds, media; the extension API, tool contract, prompts, skills, and the override rail. |
| `src/ufo/host/` | The environment provider above the runtime: extension discovery, builtin tools, the per-turn binding a composition root injects, and the dev host. |
| `src/ufo/onboard/`, `src/ufo/sdk/`, `src/ufo/schema/` | Onboarding, the extension SDK, shared persistence. |
| `harness/tests/` | Engine-core contract tests — standard library only, no database. |
| `tests/` | Durable host, environment, and domain tests. |

The packages run in one Python process. Their seams are import and ownership boundaries: the
engine core imports only the standard library and its own modules, and extensions import only
`ufo.sdk` — both gated.

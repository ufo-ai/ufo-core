# ufo

ufo is a tenant agent runtime. You can run it, read it, and extend it.

`ufo.harness` supplies product-neutral agent execution. The host supplies:

- workspace, member, and agent identity
- durable turn execution and sandbox authorization
- the Slack, CLI, and web surfaces
- grants, credentials, and accounting
- model access: Anthropic, OpenAI, Bedrock Mantle

Extensions supply all other functions: connectors, data sources, tools, subagents, and onboarding.

`ufo.harness` has no host dependencies. Its concrete `AgentEngine` owns canonical messages,
model requests, tool exchanges, finish/recovery behavior, and round budgets. Runtime and local
extension hosts configure its immutable agent definition and effect ports before a run; the engine
knows neither. Its other public modules own context management, sandbox mechanics, reply
interpretation, and untrusted-content framing. `core/harness/tests` verifies that API directly.

## Quick start

This mode runs as one process. It uses SQLite and local files. It does not need Docker.

1. Run `make install`. This installs the Python and pnpm dependencies and the git hooks. It runs
   through uv, so install uv first if this machine has none: `pip install uv`.
2. Run `make setup`. This creates `.env`. It does not change an existing `.env`.
3. Set `UFO_ANTHROPIC_API_KEY` and `UFO_OPENAI_API_KEY` in `.env`.
4. Run `make init EMAIL=you@example.com`. This writes `ufo.toml`.
5. Run `make build`. This builds the web portal, the app pages, the page SDK they are built
   against, and the debugger pages.
6. Run `make serve`. This starts the surfaces and the workers.
7. In a second terminal, run `make portal`. The portal opens in your browser, signed in.

Run `make` to see all targets, with `check`, `test`, `fmt`, and `reinstall`.

Do not put `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` in `.env`. Each tool that reads `.env` gets all
its values, so `.env` refuses the bare names. If you export a bare name in your shell, ufo uses it
as a fallback.

This node has no sign-in page. `make portal` sends the CLI token of this machine to a local
one-time page. The page posts the token to the portal, the same as the hosted sign-in card. You do
not type the token. The token is not part of a URL.

## Where to read

| Path | Contents |
| --- | --- |
| `spec.md` | The source of truth for design: doctrine, fixed decisions, the workspace model, non-goals. |
| `docs/contracts.md` | The core contracts for each unit. A section goes away when its code lands. |
| `docs/plan.md` | The build order. |
| `docs/salvage.md` | The files that port from the previous repo. |
| `docs/handbook/` | A generated reference to the harness. Start at `overview.md`. |
| `content/docs/` | The member documentation and blog served under ufo.ai. |
| `evals/README.md` | The eval suites, stacks, ablations, and GEPA. |
| `evals/swebench/README.md` | SWE-bench Verified: local generation and official grading. |
| `evals/terminal_bench/README.md` | Terminal-Bench 2.1: the pinned official roster through the native client in Harbor. |
| `core/src/ufo/harness/` | Product-neutral agent execution. |
| `core/src/ufo/runtime/` | Durable agent host and harness adapters. |
| `core/src/ufo/onboard/` | Workspace onboarding. |
| `extensions/` | First-party extensions. |
| `packs/` | Skill packs. |

## Hosted stack

Use this mode to run the full hosted topology on your machine. One command starts:

- Postgres
- the Rust `ufo-control` onboarding gateway (`/login`)
- the serve fleet: surfaces, DBOS workers, and the egress-control RPC
- the front: one nginx origin per slot, split as the hosted ingress splits the app host. `/login`,
  `/logout`, `/join`, `/v1/onboard`, and `/ufo` go to the gateway; every other path goes to serve.
- the sandbox ingress for hosted sites
- the `web` dev servers: the portal shell and the app pages, served from source and reloading on
  every frontend change
- the ufo-egress data plane: the Rust egress proxy, in the network namespace of serve

```bash
make stack STACK=1             # http://ufo-1.localhost:18080/login
make stack STACK=2             # http://ufo-2.localhost:18180/login
make signin STACK=1            # seats UFO_DEV_EMAIL with the console code and opens the portal in Chrome
```

`make signin` walks the gateway's onboarding wire with the address `UFO_DEV_EMAIL` in `.env` names
(`make stack` requires the key, like every key in `.env.template`),
and the dev code `000000`. The first walk founds the workspace; each later walk joins it. It then
opens the portal signed in through the same browser handoff `ufoctl portal` uses. Set `BROWSER` to
open another browser.

One image (`dev/Dockerfile`) holds the whole workspace, through uv. It runs as four roles
(`dev/entrypoint.sh`):

- `init` runs the migrations. It shapes the `ufo_control` gateway ledgers (`ufo-control migrate`).
  It runs `rls-bootstrap`, which creates the `ufo_serve` role and the RLS policies.
- `gateway` serves `/login` behind the front. The sign-in code goes to the log
  (`UFO_CONTROL_EMAIL_MODE=console`).
- `serve` runs the shared fleet on :8710. It uses the `assistant` pack and local backends:
  filesystem blobs, the in-process hub, and the built-in `local` sandbox carrier.
- `ingress` serves hosted sites on :8100 in the serve network namespace.

### Slots

`STACK` accepts the values 1 through 5. Each slot has its own Compose project, image, network,
volumes, and ports. Each slot keeps its workspaces in `.local/ufo-N/workspaces`. Each slot has its
own browser origin, `ufo-N.localhost`, so the session cookies of the slots stay separate.

Slot 1 uses the front :18080, serve :18710, ingress :18100, Postgres :15541, and Redis :15543. Each
slot after slot 1 adds 100 to each port. The browser uses the front only. To change the host ports,
set `UFO_PG_PORT`, `UFO_STACK_PORT_HOST`, `UFO_SERVE_PORT_HOST`, `UFO_INGRESS_PORT_HOST`, and
`UFO_REDIS_PORT`.

### Commands

- `make stack STACK=N` builds the image of the slot from the current code. Then it starts the slot.
  The portal shell and the app pages are served from source: a frontend change reloads the open
  page and every open app frame. `SHELL_NAME=lanes` serves the lanes shell in place of the sidebar
  shell. A backend change needs `make stack` again.
- `make stack-logs STACK=N` shows the logs of the slot, with the sign-in code.
- `make stack-down STACK=N` stops the slot. The image, the volumes, and the workspaces stay.
- To remove the schema and the data of the previous branch, run
  `docker volume rm ufo-N_pgdata ufo-N_blobs && rm -rf .local/ufo-N`.

### Notes

- Always start a slot with `make stack`, not with `docker compose up`. pnpm builds the portal, and
  git does not track the output. `make stack` does that build first. A direct `docker compose up`
  does not, and it does not apply the validated local-secret boundary.
- A worktree needs a copy of `.env` from the repo root.
- `UFO_DEV_PACK` selects the pack that the serve config names. The `assistant_billing` pack adds
  Metronome, so you can test the billing choice of the owner locally. See `docs/onboarding.md`.
- The dev config (`dev/ufo.toml`) replaces the hosted cloud backends with local backends. Four
  parts are not in this single node: the Turbopuffer index, Perplexity search, the multi-replica
  Redis hub, and the Docker sandbox carrier. Each needs its extension in a local pack.
- The tests use this Postgres, or an existing instance, for the Postgres half of the test matrix.
  `make db` starts only that service. Sandboxes (U2 and later) need Docker.
- Live browser tests require the pinned Chrome for Testing: `npx playwright@$(uv run python -c
  'from ufo.harness.sandbox.session import PLAYWRIGHT_VERSION; print(PLAYWRIGHT_VERSION)') install chromium`.

## Evals

Start `ufoctl serve` with a test workspace. You can delete the workspace after the run. Then:

```bash
uv run python -m evals --workspace <workspace-id> --label baseline
uv run python -m evals --remote --workspace <workspace-id> --label remote
uv run python -m evals --view
uv run python -m evals --share <current-run> <baseline-run>
```

`evals/README.md` describes the rest: the run records and the viewer, multi-suite stacks
(`evals.stack`), prompt-change ablations (`evals.ablate`), and prompt search (`evals.gepa`).

Public benchmarks retain their official harness output. `python -m evals --terminal-bench --remote`
runs the pinned Terminal-Bench 2.1 roster concurrently in Harbor's remote graded environments;
`evals/terminal_bench/README.md` covers setup, case selection, and results.

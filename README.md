# ufo

ufo is an agent runtime. You can run it, read it, and extend it.

The core supplies:

- the agent loop, in a sandbox
- memory
- the Slack, CLI, and web surfaces
- accounting
- model access: Anthropic, OpenAI, Bedrock Mantle

Extensions supply all other functions: connectors, data sources, tools, subagents, and onboarding.

## Quick start

This mode runs as one process. It uses SQLite and local files. It does not need Docker.

1. Run `make install`. This installs the Python and pnpm dependencies and the git hooks.
2. Run `make setup`. This creates `.env`. It does not change an existing `.env`.
3. Set `UFO_ANTHROPIC_API_KEY` and `UFO_OPENAI_API_KEY` in `.env`.
4. Run `make init EMAIL=you@example.com`. This writes `ufo.toml`.
5. Run `make build`. This builds the web portal, the five app pages, the page SDK they are built
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
| `evals/README.md` | The eval suites, stacks, ablations, and GEPA. |
| `evals/swebench/README.md` | SWE-bench Verified: local generation and official grading. |
| `core/` | The base unit. |
| `extensions/` | First-party extensions. |
| `packs/` | Skill packs. |

## Hosted stack

Use this mode to run the full hosted topology on your machine. One command starts:

- Postgres
- the onboarding gateway (`/login`)
- the serve fleet: surfaces, DBOS workers, and the egress-control RPC
- the ufo-egress data plane: the Rust egress proxy, in the network namespace of serve

```bash
make stack STACK=1             # http://ufo-1.localhost:18080/login
make stack STACK=2             # http://ufo-2.localhost:18180/login
```

One image (`dev/Dockerfile`) holds the whole workspace, through uv. It runs as three roles
(`dev/entrypoint.sh`):

- `init` runs the migrations. It shapes the `ufo_control` gateway ledgers (`ufo-control migrate`).
  It runs `rls-bootstrap`, which creates the `ufo_serve` role and the RLS policies.
- `gateway` serves `/login` on the host port of the slot. The sign-in code goes to the log
  (`UFO_CONTROL_EMAIL_MODE=console`).
- `serve` runs the shared fleet on :8710. It uses the `assistant` pack and local backends:
  filesystem blobs, the in-process hub, and the built-in `local` sandbox carrier.

### Slots

`STACK` accepts the values 1 through 5. Each slot has its own Compose project, image, network,
volumes, and ports. Each slot keeps its workspaces in `.local/ufo-N/workspaces`. Each slot has its
own browser origin, `ufo-N.localhost`, so the session cookies of the slots stay separate.

Slot 1 uses gateway :18080, serve :18710, Postgres :15541, and Redis :15543. Each slot after slot 1
adds 100 to each port. To change the host ports, set `UFO_PG_PORT`, `UFO_GATEWAY_PORT_HOST`,
`UFO_SERVE_PORT_HOST`, and `UFO_REDIS_PORT`.

### Commands

- `make stack STACK=N` builds the image of the slot from the current code. Then it starts the slot.
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
  'from ufo.sandbox.session import PLAYWRIGHT_VERSION; print(PLAYWRIGHT_VERSION)') install chromium`.

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

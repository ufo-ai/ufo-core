<div align="center">

```
▗▟█▙▖     ▗▟█▙▖         ▐██▘     ▐██▌  ▐███████▘    ▄▟██████▙▄
  █▓███     ███▓█         ▝██       ██   ▐██        ▄█▛▀     ▝▜██▄
   ▝▀█▀▘     ▝▀█▀▘          ██       ██   ▐██       ▟█▛         ▝██▌
                            ██       ██   ▐██▄▄▄▄▄  ██▘          ▐██
                            ██       ██   ▐██▀▀▀▀▀  ██▌          ▐██
        ▗▟█▙▖               ██       ██   ▐██       ▜██▖         ▟█▌
       ▓████               ▜█▙▖   ▄▟██   ▐██        ▀██▄▖     ▄██▀
     ▝▀█▀▘                ▀█████▛▀██▌  ▐██▖         ▀▜██████▛▀
```

**Run AI agents for your team on your own infrastructure.**<br>
The same runtime powers [UFO's hosted service](https://ufo.ai).

[![CI](https://img.shields.io/github/actions/workflow/status/ufo-ai/ufo-core/ci.yaml?branch=main&style=flat-square&label=CI)](https://github.com/ufo-ai/ufo-core/actions/workflows/ci.yaml)
[![License](https://img.shields.io/github/license/ufo-ai/ufo-core?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue?style=flat-square)](pyproject.toml)
[![Hosted](https://img.shields.io/badge/hosted-ufo.ai-black?style=flat-square)](https://ufo.ai)

[Self-host](#quick-start) · [How it works](#how-it-works) · [Use hosted](#use-hosted) ·
[Security model](#security-model) · [Extend it](#extend-it) · [Spec](spec.md) ·
[Documentation](https://ufo.ai/docs/) · [Blog](https://ufo.ai/blog/)

</div>

## Overview

UFO is an open source runtime for AI agents. Team members give agents work in chat. Agents can read
files and run commands to complete that work.

[UFO's hosted service](https://ufo.ai) runs this code. You can also run it yourself with your own
model keys. The `ufo` terminal client connects to either. This repository contains the server,
client, SDK, and extensions.

| | |
| --- | --- |
| **One process.** `ufoctl serve` runs the agent loop, turn queue, sandbox, surfaces, and jobs. | **Laptop to fleet.** SQLite and local files alone, or Postgres, S3, and Redis for more instances. One bundle serves both. |
| **Durable turns.** Each turn is a DBOS workflow that survives a crash. | **Grants in chat.** A member grants an account to an agent in chat. Agents never borrow the speaker's identity. |
| **Workspace-scoped.** Every row carries its workspace id. | **Everything is an extension.** Tools, connectors, subagents, surfaces, model providers, sandbox carriers. |

## Quick start

To host UFO yourself, run one process on SQLite and local files, with no Docker. Install
[uv](https://docs.astral.sh/uv/), then:

```bash
make install
cp .env.template .env   # set the three model API keys
make build
make init EMAIL=email@work.com
make serve
```

`make init` creates the workspace with you as admin and writes a CLI token to `~/.ufoctl/token`.
`make serve` listens on `http://localhost:8710`. In a second terminal:

```bash
mkdir -p ~/.ufo && install -m 600 ~/.ufoctl/token ~/.ufo/credentials
echo http://localhost:8710 > ~/.ufo/workspace
./client/target/debug/ufo "what can you do?"
```

The agent loop and conversation run in `ufoctl serve` in both modes. The flag selects where the
agent reads and edits files and runs commands when you start a conversation:

| | `ufo` (default) | `ufo --remote` |
| --- | --- | --- |
| File reads, edits, and commands | The connected client runs them on your machine as your user. | They run in the workspace's configured sandbox. |
| Working files | Your current directory when you start `ufo`. | The sandbox's `/workspace`, separate from your current directory. |
| Client connection | Keep `ufo` running until the turn ends; those steps need that connection. | The turn can continue after `ufo` exits. Use `--wait SECONDS` to leave early and `--resume ID` to read the rest. |
| Execution boundary | Your machine and your user account. | The configured sandbox carrier: `local` on the server by default, or a carrier such as Docker or E2B. |

A resumed conversation keeps the execution location it already has. See `client/README.md` for
client options.

`.env` refuses the bare names `ANTHROPIC_API_KEY` and `OPENAI_API_KEY`, because every tool that
reads `.env` gets all of its values.

<details>
<summary>Postgres</summary>

`make db` starts Postgres on `127.0.0.1:5541` (user, password, and database `ufo`). Before
`make init`, write a `ufo.toml` that points at it:

```bash
make db
uv run python -c 'from ufo.cli import DEFAULT_CONFIG; print(DEFAULT_CONFIG, end="")' \
  | sed 's#sqlite+aiosqlite:///ufo.db#postgresql+asyncpg://ufo:ufo@127.0.0.1:5541/ufo#' > ufo.toml
```

</details>

<details>
<summary>Browser tools on macOS</summary>

`ufo --remote` browser tools drive Chrome's headless shell from the PATH of `make serve`. Put its
directory on the PATH, not a symlink to the binary:

```bash
npx playwright@$(uv run python -c 'from ufo.sdk.sandbox import PLAYWRIGHT_VERSION; print(PLAYWRIGHT_VERSION)') install chromium-headless-shell
PATH="<dir>/chrome-headless-shell-mac-arm64:$PATH" make serve
```

`<dir>` is the location Playwright reports (`--dry-run` prints it). Intel Macs use
`chrome-headless-shell-mac-x64`.

</details>

<details>
<summary>Sites and the session debugger</summary>

| Command | What it adds |
| --- | --- |
| `uv run ufoctl ingress` | Serves agent-built sites on subdomains of `http://ufo.localhost:8100`. Private sites need a sign-in gateway (`[serve] sign_in_path`). |
| `make debugger`, then `uv run ufoctl debugger` | Opens turns, steps, transcripts, and memory in a browser. Needs pnpm; run `make debugger` before `make serve`. |

</details>

## How it works

<img src="assets/how-it-works.png" alt="The UFO terminal client sends a message through surfaces and a durable turn queue to the agent loop. The loop can run file and shell work through the terminal client or a sandbox carrier. The server also runs background jobs and uses a database and file storage.">

| Package | Owns |
| --- | --- |
| `ufo.harness` | Product-neutral agent execution: messages, model requests, tool calls, budgets, compaction, the sandbox protocol. |
| `ufo.runtime` | The durable host: identity, authorization, turns, surfaces, accounting, the extension API. |
| `ufo.host` | Extension discovery, built-in tools, and per-turn prompt and tool assembly. |

A **pack** names the extensions a deploy activates. `assistant` is the default.

## Use hosted

[Create a workspace](https://ufo.ai/login?signup=1) with your work email. The hosted service runs
agents and scheduled tasks on UFO's infrastructure and provides model access. Your team can
use the [web app](https://app.ufo.ai) or Slack without running `ufoctl serve` or setting model API
keys.

Connect Slack during workspace setup. To use the same workspace from your terminal on macOS or Linux,
install the client:

```bash
curl -fsSL https://ufo.ai/ufo | sh
```

Run `ufo` and sign in. Start it from a directory to let the agent work on its files, or run
`ufo --remote` to use the hosted sandbox. The table in [Quick start](#quick-start) compares the two
modes. See the [terminal guide](https://ufo.ai/docs/work/terminal/) for more detail.

## Security model

| Boundary | What holds it |
| --- | --- |
| Agent access | A member's account reaches an agent only through a connector grant made in chat; the granting turn is the audit record. The workspace's own keyed accounts reach only the main agent until a member grants them to another. |
| Workspace scope | Every request, turn, and job binds one workspace, and every query filters on it. |
| Keys | The Rust services under `servers/` hold no customer keys; every secret stays in the runtime. |
| Sandbox | The default `local` carrier confines writes with Seatbelt or Landlock, but can read the whole host, and the kernel does not enforce its egress. For untrusted input or several workspaces, use `docker` or `e2b`. |

## Extend it

An extension is a Python package that imports only `ufo.sdk` and declares one entry point:

```toml
[project.entry-points."ufo.extension"]
acme = "ufo_ext_acme.manifest:manifest"
```

`extensions/sample` exercises every manifest point. `ufoctl ext search | install | remove` manages
installed extensions. `spec.md` §Extension system lists each point's contract.

The [Screenpipe example](examples/screenpipe/README.md) connects local recording search through
the existing MCP extension.

## Deploy it

`ufoctl bundle` freezes a deploy — image recipe, client, pinned config, lockfile — into one
artifact. It serves a single node, an on-prem install, or a fleet; scale-out adds instances,
Postgres, S3, and the Redis hub.

## Test it

`make` lists every target. `make check` runs the static gates, `make test` the parallel suite,
`make test-one FILE=…` one file, and `make test-integration` the serial and Docker pass. `make db`
starts the Postgres and Redis they use. Browser tests need Chrome for Testing:

```bash
npx playwright@$(uv run python -c 'from ufo.sdk.sandbox import PLAYWRIGHT_VERSION; print(PLAYWRIGHT_VERSION)') install chromium chromium-headless-shell
```

## Where to read

| Path | Contents |
| --- | --- |
| `spec.md` | The design source of truth. |
| `AGENTS.md` | Working rules: code style, testing, what lands together. |
| `core/src/ufo/` | `harness`, `runtime`, `host`, `onboard`, and `sdk`. |
| `extensions/`, `packs/` | Shipped extensions and activation bundles. |
| `client/` | The `ufo` terminal client. |
| `servers/`, `sandbox/` | The Rust services and the sandbox image. |

## Contributing

Read `spec.md` before you propose a structural change, and `AGENTS.md` before you open a pull
request.

## License

Apache-2.0. See `LICENSE` and `NOTICE`.

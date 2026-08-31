---
rfc: 0028
title: "Rust terminal client — one native binary renders the directive wire"
status: proposed
date: 2026-08-13
---

# Rust terminal client

> The terminal client is a 1090-line POSIX sh renderer plus ~1600 lines of JavaScript op programs
> injected at serve time and run under `osascript` or `node`. That stack exists because sh cannot
> hold bytes. A native binary can, so this replaces the client with one static Rust executable per
> platform (`client/`), speaking the same directive wire, with every op implemented in-process.
> Speed is the point: one persistent connection instead of a curl handshake per request, and op
> dispatch in microseconds instead of a ~180 ms interpreter spawn per op.

## Current state

| Fact | Where |
|---|---|
| The sh client renders directives and relays ops; 1090 lines | `servers/control/src/ufo_control/client/ufo` |
| One `curl` process — TCP + TLS handshake — per request, ~1/85 s plus one per op reply | `servers/control/src/ufo_control/client/ufo:654` |
| Op logic is ~1600 lines of JS in 7 programs × 2 runtimes, injected into the served script | `core/src/ufo/harness/sandbox/terminal.py:754`, `servers/control/src/ufo_control/gateway.py:106` |
| `osascript` spawn per op is 10–180 ms; params relay through `op.json` temp files | RFC 0026, measured |
| exec replies carry stdout/stderr hex-doubled because a shell variable cannot hold NUL | `core/src/ufo/harness/sandbox/client/exec.js` |
| A held stream lasts 85 s, then `poll`; the tail resumes from the `since` cursor | `extensions/ufo/ufo_ext_ufo/surface.py:70`, `:364` |
| A machine with neither `osascript` nor `node` runs no ops; Windows runs nothing at all | `servers/control/src/ufo_control/client/ufo:1016` |

## Proposal

One Rust crate, `client/`, builds the `ufo` binary for five targets: `aarch64-apple-darwin`,
`x86_64-apple-darwin`, `x86_64-unknown-linux-musl` and `aarch64-unknown-linux-musl` (static),
`x86_64-pc-windows-msvc`. No runtime dependencies — no curl, no JS engine, no libc below musl on
Linux. The wire is unchanged: the same tab-separated directives down, the same headers up, so the
server drives the already-installed sh clients identically until the version gate converts them.

The op contract stays server-named. A `run` directive still carries `kind`/`name`/`params`; the
client implements each op natively (exec with process-group kill, staged-rename write, windowed
read, grep/glob/edit/changes against the same walk listings) and answers the same JSON shapes the
JS programs produce, pinned by the crate's own tests. What changes is delivery: op logic no longer
rides the served script, so a new op is a client release on the same `install` channel rather than
a re-fetched script — and the client reports its version in `x-ufo-script`, so the server can
refuse or upgrade a stale one.

### Efficiency

| Cost today | With the binary |
|---|---|
| TCP + TLS handshake per request (every 85 s hold, every op reply, every poll) | One keep-alive connection reused across the whole session |
| ~10–180 ms interpreter spawn + `op.json` temp-file relay per op | In-process dispatch; params parsed from the directive line |
| stdout/stderr hex-doubled through the shell | Bytes held natively; the exec reply is `stdout_b64`/`stderr_b64` |
| `poll` sleep + fresh connection to resume a live turn | Immediate reply-chaining after `run`; reconnect is one round trip on the pooled connection |

### Delivery

CI (`.github/workflows/client.yml`) gates every `client/**` change across five targets — the four
originals plus `aarch64-unknown-linux-musl`. The deploy builds the same five in a matrix beside
the bundle image, stages them into the gateway build context as `clientbin/<target>/<binary>`,
and the image carries them under `UFO_CLIENT_BIN_DIR=/app/clientbin`; the gateway serves
`GET /ufo/bin/{target}`, and the deploy's origin gate curls one binary to prove the chain. A local
image carries the empty directory and the route answers 404.

`/ufo` serves a small bootstrap: detect the platform, download the binary into `$UFO_HOME/bin`,
add it to PATH, exec it. `UFO_CLIENT_VERSION` — read by terraform from `client/Cargo.toml`, the
same source the binaries build from — rides both the gateway and serve pods' env, and a request
whose `x-ufo-script` names any other version is told to `install`: the binary re-downloads its
target; a still-installed sh client re-fetches `/ufo`, lands the bootstrap, and converts on its
own restart path. Production promotes the tested images, so the binaries and version ride the
promotion unchanged.

### Compatibility

`$UFO_HOME/credentials`, `session`, and `workspace` are shared byte-for-byte, so a member can move
between clients mid-conversation. Unknown directives are dropped. The exec reply is
`stdout_b64`/`stderr_b64`. A downloaded binary's integrity rests on TLS and the response length —
no separate checksum; the deploy that serves the binary is the deploy being trusted with the
conversation. Windows gets the binary, chat, and native
ops; server-composed walk enumerations assume POSIX `find`/`stat`/`git` and `exec` argvs assume
`sh` — a named limit of the server's composition, not of this client.

## Doctrine fit

**Core doctrine** — untouched: the client is a served artifact like the script it replaces, driven
entirely by the server. **Both ends** — the binary route ships with its producer (the deploy's
build matrix) and its consumers (the bootstrap and the client's install flow); the route refuses
rather than half-serving when the deploy carries no binaries. **Fail loud** — a missing binary is
a 404 sentence, never a fallback to a mismatched target. **Tear-outs land whole** — the sh client,
the seven JS op programs, the bundle injection, and the `osascript`/`node` probe left in one
change, with the binary path gated in the same deploy.

## Alternatives

| Option | Why not |
|---|---|
| Keep the sh + JS client and optimize it | The interpreter spawn and the per-request curl are the cost; both are structural |
| Go instead of Rust | Either works; Rust's musl static story and zero-runtime binaries are the tightest fit, and `fancy-regex` covers the lookarounds models write |
| A TUI framework (ratatui) over the whole screen | The client is a transcript plus a repainted bottom region, not an alternate-screen app; crossterm alone keeps the binary and the code small |
| Ship the op programs to the binary as JS (embed an engine) | The engine is the weight; the op shapes are pinned by tests either way |
| Change the wire to binary framing now | The wins land without it; a framing change would strand the sh client before its tear-out |
| Fold the `write`/`read` op kinds into the `run` arm | Each carries relay behavior a program name would only re-encode — `write` fetches its staged bytes off the wire, `read` answers the file as the reply body; a fixed kind is the honest shape |

## Build order

| # | Unit | Estimate |
|---|---|---|
| 1 | Crate: wire, renderer, session loop, native ops, tests | landed |
| 2 | Gateway binary route + CI build matrix | landed |
| 3 | Deploy bakes five targets; bootstrap serves the binary; sh client + JS injection torn out whole; version-gated `install`; the exec reply is `stdout_b64` alone — no client in the field speaks hex | this change |

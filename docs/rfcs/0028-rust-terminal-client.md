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
| The sh client renders directives and relays ops; 1090 lines | `control/src/ufo_control/client/ufo` |
| One `curl` process — TCP + TLS handshake — per request, ~1/85 s plus one per op reply | `control/src/ufo_control/client/ufo:654` |
| Op logic is ~1600 lines of JS in 7 programs × 2 runtimes, injected into the served script | `core/src/ufo/sandbox/terminal.py:754`, `control/src/ufo_control/gateway.py:106` |
| `osascript` spawn per op is 10–180 ms; params relay through `op.json` temp files | RFC 0026, measured |
| exec replies carry stdout/stderr hex-doubled because a shell variable cannot hold NUL | `core/src/ufo/sandbox/client/exec.js` |
| A held stream lasts 85 s, then `poll`; the tail resumes from the `since` cursor | `extensions/ufo/ufo_ext_ufo/surface.py:70`, `:364` |
| A machine with neither `osascript` nor `node` runs no ops; Windows runs nothing at all | `control/src/ufo_control/client/ufo:1016` |

## Proposal

One Rust crate, `client/`, builds the `ufo` binary for four targets: `aarch64-apple-darwin`,
`x86_64-apple-darwin`, `x86_64-unknown-linux-musl` (static), `x86_64-pc-windows-msvc`. No runtime
dependencies — no curl, no JS engine, no libc below musl on Linux. The wire is unchanged: the same
tab-separated directives down, the same headers up, so the server drives both clients identically
while both exist.

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
| stdout/stderr hex-doubled through the shell | Bytes held natively; `stdout_hex` stays on the wire for compatibility, and `stdout_b64` is the follow-up server change — both consumers are ours |
| `poll` sleep + fresh connection to resume a live turn | Immediate reply-chaining after `run`; reconnect is one round trip on the pooled connection |

### Delivery

CI (`.github/workflows/client.yml`) builds the four release binaries on every `client/**` change
and uploads each as `ufo-<target>`. The deploy bakes them into the gateway image under
`UFO_CLIENT_BIN_DIR/<target>/ufo`, and the gateway serves `GET /ufo/bin/{target}` — an
unconfigured deploy answers 404 and nothing else changes. The client's `install` directive
self-copies the running binary into `$UFO_HOME/bin` and adds it to PATH, exactly where the script
installed itself.

The sh client remains the `curl | sh` bootstrap until the binary path is proven in a deploy; the
bootstrap then becomes a stub that detects the platform, downloads the binary, and executes it.
The sh client, the JS programs, the bundle injection, and the `osascript`/`node` probe tear out
whole in that change, not before.

### Compatibility

`$UFO_HOME/credentials`, `session`, and `workspace` are shared byte-for-byte, so a member can move
between clients mid-conversation. Unknown directives are dropped, as the sh client drops them.
Windows gets the binary, chat, and native ops; server-composed walk enumerations assume POSIX
`find`/`stat`/`git` and `exec` argvs assume `sh` — a named limit of the server's composition, not
of this client.

## Doctrine fit

**Core doctrine** — untouched: the client is a served artifact like the script it replaces, driven
entirely by the server. **Both ends** — the binary route ships with its producer (the CI matrix)
and its consumer (the client's install flow and this RFC's bootstrap plan); the route refuses
rather than half-serving when the deploy carries no binaries. **Fail loud** — a missing binary is
a 404 sentence, never a fallback to a mismatched target. **Tear-outs land whole** — the sh client
and the JS layer leave in one change once the binary is the served path.

## Alternatives

| Option | Why not |
|---|---|
| Keep the sh + JS client and optimize it | The interpreter spawn and the per-request curl are the cost; both are structural |
| Go instead of Rust | Either works; Rust's musl static story and zero-runtime binaries are the tightest fit, and `fancy-regex` covers the lookarounds models write |
| A TUI framework (ratatui) over the whole screen | The client is a transcript plus a repainted bottom region, not an alternate-screen app; crossterm alone keeps the binary and the code small |
| Ship the op programs to the binary as JS (embed an engine) | The engine is the weight; the op shapes are pinned by tests either way |
| Change the wire to binary framing now | The wins land without it; a framing change would strand the sh client before its tear-out |

## Build order

| # | Unit | Estimate |
|---|---|---|
| 1 | Crate: wire, renderer, session loop, native ops, tests | this change |
| 2 | Gateway binary route + CI build matrix | this change |
| 3 | Deploy bakes binaries; bootstrap serves them; sh client + JS injection tear out whole | after a proven deploy |
| 4 | `stdout_b64` replaces `stdout_hex` on the op reply once the sh client is gone | with 3 |

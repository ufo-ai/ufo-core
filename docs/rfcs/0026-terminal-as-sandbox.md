---
rfc: 0026
title: "Terminal as sandbox — the CLI member's own directory is the workspace"
status: proposed
date: 2026-08-12
---

# Terminal as sandbox

> A member who runs `ufo` in a project directory expects the agent to work on the files in front of
> them. Today that directory is invisible: the turn's `/workspace` is a directory the deploy owns,
> and the member's files reach it only as attachments. This proposes a fourth carrier, `client`,
> whose `/workspace` is the member's `$PWD` and whose commands run as subprocesses on the member's
> machine, driven over the directive stream the shell client already renders. It is the default for
> a conversation born in a connected terminal.

## Current state

| Fact | Where |
|---|---|
| The carrier seam: create/attach/exec/write/read/dial | `core/src/ufo/sandbox/session.py:206` |
| Every file tool goes through one in-sandbox CLI | `core/src/ufo/sandbox/session.py:382` (`run_sbxfs`) |
| That CLI is 1190 lines of Python — pdf/pptx/image reads, multi-edit, `changes` over git | `core/src/ufo/sandbox/image/sbxfs` |
| One carrier per process, chosen by `[sandbox] backend` | `core/src/ufo/sandbox/select.py:13` |
| The turn builds its session off that one carrier | `core/src/ufo/loop/queue.py:367` |
| A conversation's workspace is `workspace_root/<conversation-id>` | `core/src/ufo/sandbox/conversation.py:121` |
| The durable handle already carries its backend: `<backend>:<id>` | `core/src/ufo/sandbox/session.py:176` |
| The shell client is a pure directive renderer; the server drives every screen | `servers/control/src/ufo_control/client/ufo:617` |
| The one out-of-band POST: a privately entered secret, answered by header | client `:486`, surface `:310` |
| A held stream lasts 85s, then `poll` and the client reconnects | `extensions/ufo/ufo_ext_ufo/surface.py:54` |

The gap: nothing carries the member's directory to the deploy, and no carrier serves a workspace
that is not the deploy's own storage.

**The constraint that shapes everything below:** the client must run on a stock macOS desktop.
`/usr/bin/python3` and `/usr/bin/git` there are Command Line Tools shims (measured: both link
`libxcselect`; running one can raise the developer-tools installer), so `sbxfs` cannot ship to the
member as Python. What a stock Mac does have is JavaScriptCore, reachable two ways, both measured
byte-exact on NUL and missing-trailing-newline fixtures: the framework's own `jsc` shell
(undocumented path that has moved between releases; no rename, stat, readdir, or subprocess; 6 MB
read+write in an 8 ms process) and `osascript -l JavaScript` (`/usr/bin/osascript`, documented and
stable; the ObjC bridge adds rename, stat, readdir and atomic writes; 10–30 ms startup; 6 MB
through the byte-preserving Latin-1 string bridge in a 180 ms process). The op logic runs under
`osascript`: both engines are the same JavaScriptCore and both are fast against a network round
trip, so the stable path and the filesystem API decide it. The one mark against `osascript` — EDR
and MDM on managed Macs watch it as an automation vector more than an obscure framework helper —
is noted, not disqualifying: both are stock binaries run as the member.

## Proposal

### The three primitives, and only three

The shell client implements exactly the primitives the `Carrier` protocol already names, in ~15
fixed lines each. Every op's logic — parsing, matching, windowing, result shaping — lives in
JavaScript, run under `osascript -l JavaScript`. The program is authored server-side and injected
into the served client, so the client holds its own copy and the op directive names a program
rather than shipping one; only the op's params ride the wire. The shell pipes; the runner thinks;
the server decides. A new op is a server release the client picks up on its next install, never a
hand-written client update.

| Primitive | Runs | Implementation |
|---|---|---|
| `exec(argv, env, cwd, timeout)` | member's machine | `set -m`, a watchdog, `kill -- -$pid` — the process-group semantics `local.py:156` already documents; the argv arrives as a line the runner already shell-quoted, `eval "set -- …"` |
| `write(path, bytes)` | member's machine | `curl` the op's body into a staged temp, `mv` onto the target |
| `read(path)` | member's machine | the reply POST's body is the file, `curl --data-binary @-` |
| `file_op(op, params)` | member's machine, under the runner | the op's bundled JS program, its params landed as `op.json`: byte-exact `readFile(path, "binary")`/`writeFile`, JSON result on stdout |
| `dial(port)` | — | `SandboxUnreachable`, as the local carrier already answers |

`Carrier` grows one method, `file_op(handle, op, params) -> dict`. Docker, e2b and local delegate to
a shared `sbxfs_file_op(...)` holding today's `("sbxfs", op, json)` exec; `SandboxSession.run_sbxfs`
calls the carrier instead of composing that argv itself. The client carrier answers per op:

- **read, edit, write, grep, glob** — a JS program per op in `core/src/ufo/sandbox/client/`, the
  same result shapes and caps `sbxfs` returns, held to them by a differential test against the real
  `sbxfs` over one fixture tree. Nothing crosses but the op's own result: a `read` sends back only
  its window, an `edit` runs whole on the member's machine — byte-exact on `Uint8Array`, staged
  temp and `mv`, mode preserved — and JS `RegExp` takes the `\d`/`\w`/`\s` and lookarounds the
  models write, which BSD `grep -E` never would. The runner has no subprocess, so enumeration
  arrives as input: the carrier runs `find` through the same `exec`
  primitive and hands its output to the JS. `changes` composes from `git` the same way, and reports
  nothing when git is absent (it is CLT-gated) rather than failing the turn.
- **pdf, pptx and image reads** — the one pull: poppler and libreoffice live on the server, so the
  file's bytes cross once (capped at 10 MB, refused in `sbxfs`'s own message shape) and the real
  `sbxfs` renders against a scratch directory.

### Why the op logic is JavaScript

The evidence chain, all measured on this stock-tool set:

1. **Raw bytes cannot live in the shell.** A variable cannot hold NUL, `$(…)` strips trailing
   newlines by specification, awk re-emits `ORS`: three of four hostile fixtures corrupt, on
   write-back, after the model was told the edit applied.
2. **Hex-encoding through `xxd` (base OS, verified not a shim) fixes correctness but not cost.** A
   30-line `sh`+awk edit agreed with `sbxfs` byte-for-byte on 10/10 fixtures — and took 1.3 s on a
   6 MB file, O(n²) worst case, with the occurrence count, multi-edit sequence and snippet still
   unbuilt.
3. **JavaScriptCore fixes both.** Byte-exact binary I/O measured on both of its stock faces,
   real regex, native JSON — and the program (a shared Foundation prelude, the op's JS, one `run`
   entry that reads `op.json`) is authored in core and injected into the served client, so the op
   contract's one home stays server-side and the client's copy is byte-identical, covered by the
   script's own version hash.

The one divergence a byte-exact edit keeps: `sbxfs` edits text decoded `errors="replace"`, so on
invalid UTF-8 it aliases every bad byte to U+FFFD and can count matches byte-exactness refuses.
The differential test pins the divergence as intended rather than hiding it.

`osascript` sits at a documented path, so the relay's probe is one `command -v`; a machine
without it binds no terminal, the conversation falls to the deploy's carrier, and the member is
told. A Linux client, when one matters, is the same probe finding `python3` and running `sbxfs`
itself — verbatim, the file the image already bakes — which is why the op seam is a server-authored
program the client merely runs, not a contract the client implements.

### Selection

`ConversationSandbox` becomes the one owner of which carrier serves a conversation: `open` and
`existing` return a `SandboxSession` rather than a bare handle, and `runtime.sandboxes.carrier` stops
being read at `queue.py:367`. The `ufo` surface registers a live binding — the realpath from a new
`x-ufo-cwd` header — for the life of its held POST. The durable record is the `sandbox_handle`
column, `client:<realpath>`, split by the `sandbox_handle_id` that exists. No migration.

Decided once, on first open, then recorded:

| Stored handle | Terminal bound | Result |
|---|---|---|
| none | `cwd=P` | client carrier at P; the row claims `client:P` |
| none | — | the deploy's carrier, unchanged |
| `client:P` | `cwd=P` | client carrier at P |
| `client:P` | `cwd=Q` | refuse, naming both |
| `client:P` | — | the turn refuses; `existing()` answers None |
| `e2b:…` | `cwd=P` | the deploy's carrier — this conversation's workspace is already elsewhere |

A conversation resumed from a different directory refuses rather than rebinding: the transcript
names files at P, and a workspace that moves under a thread makes every earlier line a lie.

### Wire

Server to client, down the held stream: `run <op_id> <kind> <name> <timeout> <arg> <params>`. `kind`
picks the ~15-line primitive arm; `name` names the op's program, which the client runs from its own
bundle (`$UFO_HOME/programs/<name>.js`); `params` is one escaped field **the shell never parses** —
it lands as `op.json` in a temp dir and the program's `run` entry reads it (a `file_op` runs as
`osascript -l JavaScript $UFO_HOME/programs/<name>.js run <workdir>`; an `exec` runs the same
program's `emit` mode to produce the shell-quoted argv line the arm `eval`s into `set --`), so a new
op grows the params without the client changing its arms. The program never rides the wire: the
gateway injects every op program into the served client at install, and the script's own version
hash covers the bundle, so an edited program moves the version and the client re-fetches. Path logic
stays out of the shell the same way: the carrier rewrites `/workspace/…` against the bound directory
before composing the params, as the local carrier already rewrites against its host path.

**The reply is the next request.** `run` is a `NEXT` state in the client's existing loop, exactly as
`sendfile` was: the client runs the op, then re-enters `post` with the result as the body and
`x-ufo-op: <op_id>` as the header, and the same held-stream handler resolves the op and resumes
tailing the turn. No second call site in the shell, no separate reply endpoint, and the client is
never left without a next step — a stream that dies at the 85s hold while an op runs is simply
succeeded by the reply POST. An op reply admits no turn and reaches no transcript, the way a secret
does not. Two gates decide who may answer: the op id (32 hex, unguessable, single-use) and the
bearer, which must name the member the binding was made under.

Bytes never ride the directive line. A write's body is served at `GET /surface/ufo/op/<op_id>` — an
authenticated read projection on the surface the client already holds a bearer for, gated the same
two ways — and the client `curl`s it straight into the staged temp file, so a 100 MB copy-in streams
to disk instead of entering a shell variable, and the line stays a few hundred bytes whatever the
file's size.

The rendezvous is `Terminals` in core, per conversation and **never lossy** — the hub drops frames on
a full subscriber by design, and a dropped op result hangs a turn. Ops are serial, because the engine
awaits one tool call at a time, so a conversation's state is one slot: the current op, its waiter,
its staged bytes. The slot is keyed by conversation and survives reconnects — the client's stream
dies at every 85s hold, so reconnect-mid-op is the normal case, and a slot scoped to one connection
would strand the waiting turn on the old connection's state. Every send carries a deadline derived
from the op it carries — the op's own `timeout_s` plus slack, not one constant — so a five-minute
build under the bash tool is waited out and a dead client still fails the turn with a reason.
In-process default, admissible exactly where the hub is in-process — and the same two-loops shape
the hub already handles: DBOS runs a turn's workflow on its own loop thread, so the rendezvous
locks its state and wakes every waiter on that waiter's own loop.

One consequence of the reply-is-the-next-request shape is named because it recurs: work the turn
runs after its answer capped — the workspace-changes scan — finds the member back at their prompt,
not connected. The scan's op waits out the arrival grace and fails, logged, never failing the turn;
a member who keeps talking reconnects inside the grace and the scan lands on the next stream.

### Session

A launch is a conversation. The channel is 16 bytes from `/dev/urandom`; the queue key is already
`<email>:<channel>`, so the server is unchanged. `ufo --resume <id>` names an existing one, and every
exit path prints the id and the line that resumes it. `$UFO_HOME/session` is untouched — that is the
onboarding session, not the conversation.

The member is told where the agent is working, once, as a `note`: `Workspace: /Users/you/src/proj` —
or `Workspace: server-side.` when the deploy did not bind. A member standing in a directory has to
know whether the agent can see it.

## Doctrine fit / implications

**No isolation, stated rather than implied.** The agent runs as the member, in their project.
`containment.py` guards the container carriers, where it is load-bearing; on the client it guards
nothing that `cat > /etc/hosts` does not already defeat, so the client's `write` does not pretend
to, and the op programs walk symlinks the way the member's own shell would — the runner has no
`O_NOFOLLOW`, and a boundary that cannot be held is not drawn. This is a `spec.md` change, not a
footnote.

**Egress metering becomes cooperative** for a client-bound turn: a command that ignores `HTTP_PROXY`
reaches the member's own network. Model spend is unaffected — the sentinel model keys are never
exported on this carrier, so there is nothing for a proxy to swap and nothing dressed as a
credential. The proxy env itself always rides, carrying the run token: at the deploy's
`proxy_public_url` when one is named, else at the client's own loopback, where nothing answers — a
command that honours the env fails closed rather than leaking unmetered, and the re-authorization
every exec runs keeps its invariant that the proxy env carries the turn's token.

**Fail loud, never a silent elsewhere.** A client-bound conversation with no connected terminal
refuses the turn and reads as empty; nothing is quietly written into a server-side directory the
member will never see. Scheduled tasks in such a conversation refuse for the same reason.

**Both ends.** `run` ships with its producer (the carrier) and its consumer (the client's `NEXT`
arm); `x-ufo-cwd` and `x-ufo-op` each ship with the surface code that reads them. `file_op` ships with
all four carriers implementing it.

**Core, not an extension.** The carrier seam is core's, `select_carrier` already composes extension
carriers beside the built-in, and the reverse channel is the same kind of object as the hub.

What is lost for these conversations: `dial`, and with it `sandbox_chrome` CDP and ingress site
previews (hosted already defaults to `browserbase`); the portal's file browser, which lists nothing
and says the workspace is the member's own machine at P.

What it costs: one round trip per op, and only the op's result crosses — a read sends its window, an
edit its outcome, never the file. A skill mount is one write per file. Measure before batching.

## Alternatives

| Option | Why not |
|---|---|
| Ship `sbxfs` to the member as Python | `python3` is a CLT shim on a stock Mac (measured); it stays the Linux answer, where `python3` is real |
| Rewrite the op contract in `sh` + `awk` over `xxd` hex | Built and measured: byte-exact but 1.3 s at 6 MB, O(n²) worst case, and a client-resident second implementation that can drift; JavaScriptCore is two orders faster and the program stays server-side |
| Compose walks from BSD `grep`/`find`/`stat` | POSIX ERE drops the `\d`/`\w`/`\s` models write — a translation layer per dialect, per OS; JS `RegExp` takes them natively |
| A local `ufoctl serve` on the member's machine | A different product — their own database, their own workspace, no shared brain |
| Sync the directory to a server-side workspace | Two answers to where a file is, and a conflict model to invent |
| Carry exec results on the hub | Lossy on a full subscriber; a dropped result hangs a turn |
| A separate out-of-band reply POST beside the main loop | A second call site in the shell and a client that can end mid-op with no next step; the reply as the next request needs neither |
| One conversation per directory | The member asked for a launch to be a conversation, resumable by id |

## Open decisions

None. Confinement (none), session model (fresh per launch, `--resume`), absent-terminal behaviour
(refuse), and directory mismatch (refuse) are settled.

## Build order

| # | Unit | Estimate |
|---|---|---|
| 1 | `ConversationSandbox` answers a `SandboxSession`; one carrier owner | 1h |
| 2 | `Terminals`, the client carrier's three primitives, the surface wire, the relay — proven end to end through the shipped script | 1d |
| 3 | `file_op` seam; the per-op JS programs under the runner — held against the real `sbxfs` by a differential test | 1–1.5d |
| 4 | A launch is a conversation: `--resume`, the printed id | 2h |
| 5 | `spec.md`, `README.md`, copy | 1h |

Tests follow the patterns in place: the relay arm lifted verbatim out of the shipped client, as
`test_ext_ufo.py` already lifts the `file` arm, and the chain proof — the real script against a real
`serve`, leaving a file in a real directory.

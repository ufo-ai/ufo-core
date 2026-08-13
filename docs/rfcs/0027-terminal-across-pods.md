# RFC 0027 — Terminal across pods

## Problem

Terminal-as-sandbox (RFC 0026) binds a conversation's workspace to the member's own directory by a
rendezvous (`core/src/ufo/sandbox/terminal.py` `Terminals`) that is correct only while **one
process** serves both the member's held connection and their turn's workflow. So it is gated off
wherever that does not hold: `serve.py` sets `terminals_admissible = config.hub.backend ==
IN_PROCESS_BACKEND`, and the ufo surface drops `x-ufo-cwd` when terminals are not admissible.

The hosted fleet is a shared `ufo-serve` Deployment (`replicas: 2`) with a Redis-Streams hub. A
member's held connection lands on one pod; their turn's DBOS workflow can run on the other. So on
hosted, terminals are never admissible and every CLI member falls to the deploy's e2b sandbox — the
feature does not work where the CLI actually connects.

## Non-goals

- Sticky sessions or pod addressing. Every path stays pod-agnostic.
- Changing the fleet topology (per-workspace pods). The shared Deployment stays.
- Large op bytes through Redis. They ride the blob store the fleet already runs.

## Shape

The rendezvous becomes a **transport** the deploy selects, exactly as the hub is (`Manifest.hubs`
→ new `Manifest.terminal_transports`). Core ships the in-process transport (today's `Terminals`,
unchanged behaviour). The `redis_hub` extension also registers a Redis-Streams transport, so a fleet
already running the Redis hub gets a Redis terminal transport over the same `config.hub.url`. The
gate flips: `terminals_admissible` is true whenever a transport is configured — always — because the
deploy configures the transport that fits its topology.

`TerminalTransport` is the Protocol the surface and carrier already call on `Terminals`:

    connect / disconnect / workspace / arrived   — the binding and its liveness
    send                                          — the turn's side (awaits the reply)
    next_op                                       — the held stream's side (renders the directive)
    staged                                        — the copy-in body the op read projection serves
    resolve                                       — the reply route (wakes the sender)
    in_flight                                     — an operator read

`ConversationSandbox` and `SurfaceContext` hold a `TerminalTransport`, not the concrete `Terminals`.
No call site changes; only the object behind the seam does.

## The three cross-pod hops (Redis transport)

A turn on pod **T** runs an op; the member's connection is held on pod **C**; the client's reply
POST and copy-in GET land on any pod **R/G** (the load balancer chooses). Redis Streams carry the
control path (reliable, cursor-replayable — the `redis_hub` `stream_hub.py` pattern), the blob store
carries the bytes.

| Hop | Mechanism | Key |
|---|---|---|
| Binding + liveness (T reads what C published) | Redis key with a TTL heartbeat C refreshes while it holds the stream; never deleted — the TTL carries it, so a disconnect cannot race the next pod's publish | `term:bind:{conversation}` → `{cwd, member_id}` |
| Inflight binding pin (a long op stays reachable) | `send` pins the binding for the op's window, so a concurrent accessor finds the terminal after C's stream ended and the liveness key expired | `term:inflight:{conversation}` → `{cwd, member_id}` |
| Op T → C | Redis stream; `send` XADDs, `next_op` reads-and-claims atomically (one Lua) from the connection pod | `term:op:{conversation}` |
| Reply R → T | Redis stream; `resolve` XADDs, `send` XREADs while awaiting | `term:reply:{op_id}` |
| One-op-at-a-time | Redis lock per conversation held across `send`; the next op cannot enter the op stream until the current reply releases it, so C renders serially without tracking completion | `term:lock:{conversation}` |
| Copy-in body (G serves what T staged) | blob keyed by op id; `send(body=…)` puts, `staged` gets, deleted when the op clears | `term/op/{op_id}` |
| Large copy-out reply | reply body over the blob, not the stream; the reply record names the blob key | `term/reply/{op_id}` |

Small replies (exec, every `sbxfs` file op) are JSON and ride the reply stream directly; only the
two copy primitives (read-out, write-in) touch the blob, and they already keep bytes off the
directive line, so nothing new crosses that did not before.

**Reconnect mid-op across pods.** The client's stream ends every 85s and may reconnect to a
different pod, and two held streams can share one conversation (`ufo --resume` on the same channel).
The op stream is keyed by conversation, so whichever pod holds a connection reads-and-claims it in
one atomic Lua script (scan on Redis's own clock, reap entries past their window, claim the first
unclaimed one). The claim — `term:deliv:{op_id}`, set for the op's whole window, strictly past the
sender's own deadline — is what makes an `exec` run at most once: no second stream can re-render an
op the first is still executing, and once the window passes no turn may still await it, so nothing
is left that could hand it out again. The trade is that a claim that never reached the client
strands until the sender's own `TerminalGone` deadline — strictly better than a double `exec`. The
reply stream and lock are keyed by `op_id` / conversation, independent of which pod any request
lands on; a client's wait always ends because the sender's XREAD blocks until the reply record
appears or the op's own deadline elapses.

## Wire / config

- `config.hub` gains nothing; a new `config.terminal` block carries `backend` (default the
  in-process transport) and reuses `hub.url` for Redis, so the hosted `ufo.toml` selects the Redis
  transport beside the Redis hub. Both ends land in the same commit as the consumer.
- `terminals_admissible` becomes "a transport is configured" (always true). The ufo surface's
  `x-ufo-cwd` drop path is removed; the transport, not the surface, owns whether a terminal can be
  served, and both transports can.

## Testing

- The in-process transport keeps every existing `test_sandbox_terminal.py` proof, now run through
  the Protocol.
- A Redis integration suite (real Redis, real blob) exercises each hop and the reconnect-to-a-
  different-pod case with two transport instances sharing one Redis — the two-pod fleet in a test.
- The `test_terminal_chain.py` end-to-end runs once per transport: in-process, and Redis with two
  serve instances behind one client, proving a turn admitted on the pod that does **not** hold the
  connection still lands the op on the member's machine.

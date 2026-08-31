---
rfc: 0043
title: "Agent harness and tenant runtime boundary"
status: accepted
date: 2026-08-30
---

# Agent harness and tenant runtime boundary

> Agent execution is a reusable package. The durable agent host composes workspace identity,
> authorization, scheduling, accounting, extensions, and product surfaces around it. The Rust
> `ufo-control` service remains the hosted identity and onboarding gateway; it is not the tenant
> runtime.

## Names

| Name | Meaning |
|---|---|
| `ufo.harness` | Agent execution inside the Python `ufo` distribution: the stdlib-only engine core, model provider clients, sandbox carriers, the replay serializer, observability plumbing. A Rust port uses the crate name `ufo-harness`. |
| `ufo.runtime` | The durable tenant host: the turn workflow with its harness adapters, the tenant domains it composes — access, auth, billing, seats, workspace scope, turns, surfaces, sources, kinds, media — and the platform's contracts and machinery: the extension API (`runtime.ext`), the tool contract (`runtime.tools`), prompts, skills, the environment override rail. A Rust port uses the crate name `ufo-runtime`. |
| `ufo.host` | The environment provider above the runtime: extension discovery (entry points, lockfile, catalog), the first-party builtin tools, the default per-turn environment binding (`HostEnvironment`), and the dev host. |
| Sibling `ufo` packages | Onboarding, the extension SDK, and shared persistence (`onboard/`, `sdk/`, `schema/`). |
| `ufo-control` | The existing Rust HTTP gateway in `servers/control`: WorkOS, sessions, invites, signup ledgers, binary delivery, and onboarding RPC calls. |
| `ufo-egress` | The existing Rust wire proxy. It carries traffic and asks the runtime for scoped policy and credentials. |

The Python sources live under `core/src/ufo/`. `harness/`, `runtime/`, and `host/` are ordinary
source packages in the one `ufo` distribution, layered one way: host imports runtime and harness,
runtime imports harness, and neither the runtime nor the harness imports `ufo.host` (gated; the
sandbox ingress boot module is the one named exception). Host contributions reach the runtime as
injected values — `Runtime.manifests` carries the discovered declarations and
`Runtime.environment` the per-turn binding port (`TurnEnvironment`) — bound at a composition root
(`serve.py`, `cli.py`), the same way VS Code's core defines the extension API and its extension
host codes against it from above.

`control` therefore names one deployed service, not every product policy. Calling the tenant layer
`runtime` keeps the gateway's narrow SQL and credential boundary legible.

## Topology

```text
browser / terminal
       |
       v
Rust ufo-control gateway
       |  /internal/onboard/*
       v
ufo.runtime <-- Runtime.environment / Runtime.manifests, injected at boot -- ufo.host
       |  concrete in-process adapters
       v
ufo.harness AgentEngine

sandbox <-> Rust ufo-egress <-> /internal/egress/* on the ufo serve process
```

No harness/runtime HTTP boundary exists. A turn stays one DBOS workflow in one runtime process.
The separation is a package and dependency boundary, so it does not add a network failure domain or
split the transaction that commits terminal state and usage.

## Ownership

| Concern | `ufo.harness` | `ufo.runtime` / `ufo.host` | Rust `ufo-control` |
|---|:---:|:---:|:---:|
| Canonical messages, model requests, tool definitions/calls/results | owns | converts at the durable boundary | — |
| Round loop, model stream collection, tool scheduling and result assembly | owns | implements effect ports | — |
| Compaction algorithm and context-window policy | owns | stores artifacts and meters calls | — |
| Sandbox command/file protocol and path containment | owns | selects and authorizes a carrier | — |
| Reply interpretation and untrusted-content framing | owns | supplies delivery and tool effects | — |
| Workspace, member, agent, conversation, turn identity | — | owns | holds signed session claims only |
| Seats, grants, credentials, spend caps, billing | — | owns | — |
| DBOS workflow, replay records, queues, terminal commit | — | owns | — |
| Extension API, tool contract, capability contexts, prompts, skills, override rail | — | `ufo.runtime` owns | — |
| Extension discovery, builtin tools, per-turn environment binding, dev host | — | `ufo.host` owns, injected at a composition root | — |
| Object verbs | — | owns | — |
| Surfaces, portal projections, jobs, source sync | — | owns | — |
| WorkOS, invitations, signup ledgers, session cookies | — | onboarding RPC target | owns |
| Egress policy and secret resolution | — | owns | — |

The engine accepts concrete immutable in-memory values and structural ports. Its core modules
(`gates.py` names them in `ENGINE_CORE_FILES`) import only the standard library and one another —
no other `ufo` package, DBOS, SQLAlchemy, FastAPI, billing code, workspace scope, or extension
loading — so an outside runner executes the engine with no stack. The rest of `ufo.harness` is
execution machinery with real dependencies: provider clients, carriers, the replay serializer.
Engine dataclasses stop at the runtime adapter; no engine type is written to a DBOS operation
output or an application table.

## Runtime ports

The engine depends on four capabilities. The runtime binds them after establishing workspace and
agent scope from a durable turn record.

| Port | Engine operation | Runtime implementation |
|---|---|---|
| `AgentModel` | Stream one complete `ModelRequest`. | Provider selection, DBOS model step, usage, overflow compaction. |
| `AgentTools` | Supply the exact round's definitions, prepare an engine-selected call segment, execute each call, fold its round. | Extension/object resolution, requester authorization, sandbox, metering, audit. |
| `AgentConversation` | Prepare a round, checkpoint an exchange, prepare exhaustion. | Arrival admission, durable transcript, context compaction, spend enforcement. |
| `AgentEvents` | Publish marked replies and observe exhaustion. | Audience-scoped hub/delivery frames, metrics, incomplete reason. |

The runtime compiles product state into one invocation. The harness cannot look up a workspace,
member, grant, balance, or credential. A tool executor receives the active requester binding; the
harness cannot manufacture one.

## The environment rail

`AgentDefinition` is the immutable prompt and execution configuration: system prompt, round and
parallel-call limits, and engine feedback prompts. `AgentTools.definitions()` is the exact tool
offer for a round. Environment configuration therefore finishes before `AgentEngine` is
constructed, and `ufo.runtime.environment` is the rail that finalizes it from outside the process:

```text
ufoctl dev-host overrides.json          x-ufo-environment (with x-ufo-model)
        ^                                        |
        | POST /environment                      v
runtime queue -- default assembly (prompt, authorized tool offer) --> apply overrides --+
                                                                                        |
                                                                                        v
                                                                          AgentEngine.run(messages)
```

A turn admitted with `TurnRuntimeConfig.environment_host` pinned has its default assembly — the
rendered system prompt and the tool offer the runtime already authorized — sent to that host, and
the answer applied: a replacement system prompt, rewritten descriptions for offered tools,
withheld tools. `apply_environment_overrides` narrows by construction: it can rewrite text the
model reads and remove from the offer, a name outside the offer fails the turn, and every
surviving tool keeps the handler, capability context, and authorization the runtime bound — so no
override reaches anything the member could not already use. The fetch fails loud too: an eval arm
whose overrides did not apply must never measure as the default. The rail is refused at admission
and at execution unless the deployment sets `environment.dev_host_allowed` — a dev or eval stack,
never a hosted fleet.

`ufoctl dev-host` serves one overrides document to every turn that pins it: one arm of an ablation
is one document against a shared stack, instead of a stack per prompt variant. The engine never
starts a host, reads a manifest, opens a process channel, or asks which extension provided a tool.
Tools implemented by a remote host process — contribution, not narrowing — stay outside this rail
and keep RFC 0015's process, trust, scope, and transport questions.

## Durable compatibility

DBOS serializes Python step arguments and outputs with pickle. A rolling deploy can replay values
written by the release it replaces. Therefore:

- DBOS decorators and workflow entry points stay in `ufo.runtime`.
- Persisted classes keep their qualified names; a module move lands with its old→new entry in
  `MOVED_MODULES` (`ufo.harness.durability`), the wire codec deserialization resolves every
  recorded module path through. An entry lives for as long as recordings naming it replay.
- Runtime steps convert an engine result into the existing runtime boundary type before returning.

This makes package movement independent of queue draining and preserves cross-release recovery.

## Rust

RFC 0041 uses the same boundary. Its agent crate is `ufo-harness`; its tenant host crate is
`ufo-runtime`. The port may replace both Python implementations together after its declared drain,
but it does not absorb `servers/control` or `servers/egress`.

The Rust gateway keeps its RFC 0036 rule: its SQL reaches the `ufo_control` schema and nothing else.
Workspace choices, seat creation, signup credit, and membership remain `/internal/onboard/*` calls
into `ufo-runtime`. The egress binary likewise keeps its RFC 0035 rule: policy, credentials, and
metering remain `/internal/egress/*` calls into `ufo-runtime`.

## Implementation

| Harness module | Owns | Runtime adapter | Proof |
|---|---|---|---|
| `ufo.harness.rounds` / `tools` | Provider-stream collection, ordered call assembly, safe-call segmentation. | `ufo.runtime.engine._stream_once` converts to the replay-stable `StreamResult`; resolved tools supply the safety predicate. | Stream, error, reasoning, pacing, and concurrent-dispatch tests. |
| `ufo.harness.agent` | Concrete messages, definitions, requests and results; round progression, reply interpretation, parallel-safe tool scheduling, ordered result assembly, structured finish correction, model recovery, and budget exhaustion. | `TurnEngine._model_round` converts durable messages at the seam and supplies `AgentModel`, `AgentTools`, `AgentConversation`, and `AgentEvents` adapters. | Isolated engine scenarios plus tool, arrival, finish-contract, interruption, truncation, and exhaustion runtime tests. |
| `ufo.harness.context` | Whole-round window selection, token policy, bounded overflow recovery, anchor retry sequencing, replacement verification, and checkpoint ordering. | `ufo.runtime.compaction.Compaction._compact` supplies model summaries, typed anchors, hooks, usage, and blob checkpoints inside its existing DBOS step. | Compaction records and replay-stable boundary types plus the isolated policy suite. |
| `ufo.harness.sandbox.protocol` / `containment` | Supervised command argv, structured file-operation wire protocol, and symlink-safe path operations. | `ufo.harness.sandbox.session` supplies the authorized carrier, handle, and runtime bootstrap. | Protocol and containment tests plus Local, Docker, E2B, terminal, and client carrier suites. |
| `ufo.harness.replies` / `untrusted` | Addressed-reply parsing, stream redaction, and untrusted-content framing. | The turn engine supplies delivery and extension tool effects. | Isolated parser/framing tests plus runtime delivery tests. |
| `core/harness/tests` | Engine API and outside-host configuration contract. | None. | Tests configure prompts and tools, then run scripted agents through only the harness ports; the wheel gate verifies the source packages ship in the one distribution. |
| `ufo.runtime.environment` / `devhost` | Environment request/overrides wire shapes, the narrowing apply, the reference host. | The turn workflow sends the default assembly to the pinned host and applies the answer before `AgentEngine` is built, gated on `environment.dev_host_allowed`. | Apply and reference-host unit tests, header admission and refusal tests, and a lifecycle turn run against a live dev host. |

No parallel loop or adapter alias exists. The engine-core import gate has no exceptions.

## Rejected shapes

| Shape | Reason |
|---|---|
| Rename the tenant layer `control` | Collides with the deployed Rust gateway and invites tenant-table SQL into it. |
| Put billing or authorization hooks in the harness | Makes agent execution tenant-aware and lets callers omit a policy path. |
| Run the harness as another service | Adds latency and a partial-failure boundary inside every round without an isolation requirement. |
| Move DBOS boundary types with their algorithms | Breaks pickle replay across a rolling deploy. |
| Let runtime implement harness stages as whole-round callbacks | Leaves execution policy in the tenant adapter and makes the harness only a callback scheduler. |
| Keep a second loop in runtime while extracting | Creates two answers to the execution semantics and lets them drift. |

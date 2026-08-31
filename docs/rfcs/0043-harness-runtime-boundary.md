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
| `ufo.runtime` | The durable tenant host: the turn workflow with its harness adapters, the tenant domains it composes — access, auth, billing, seats, workspace scope, turns, surfaces, sources, kinds, media — and the platform's contracts and machinery: the extension API (`runtime.ext`), the tool contract (`runtime.tools`), prompts, skills, access policy. A Rust port uses the crate name `ufo-runtime`. |
| `ufo.host` | The environment provider above the runtime: extension discovery (entry points, lockfile, catalog), the first-party builtin tools, per-turn assembly of prompt, tools, and skills (`HostEnvironment.assemble`), and the environment-document schema and application. |
| Sibling `ufo` packages | Onboarding, the extension SDK, and shared persistence (`onboard/`, `sdk/`, `schema/`). |
| `ufo-control` | The existing Rust HTTP gateway in `servers/control`: WorkOS, sessions, invites, signup ledgers, binary delivery, and onboarding RPC calls. |
| `ufo-egress` | The existing Rust wire proxy. It carries traffic and asks the runtime for scoped policy and credentials. |

The Python sources live under `core/src/ufo/`. `harness/`, `runtime/`, and `host/` are ordinary
source packages in the one `ufo` distribution, layered one way: host imports runtime and harness,
runtime imports harness, and neither the runtime nor the harness imports `ufo.host` (gated; the
sandbox ingress boot module is the one named exception). Host contributions reach the runtime as
injected values — `Runtime.manifests` carries the discovered declarations and
`Runtime.environment` the per-turn assembly port (`TurnEnvironment.assemble`) — bound at a composition root
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
| Extension API, tool contract, capability contexts, prompts, skills, access policy | — | `ufo.runtime` owns | — |
| Extension discovery, builtin tools, per-turn assembly, environment documents | — | `ufo.host` owns, injected at a composition root | — |
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

## Environment documents

`AgentDefinition` is the immutable prompt and execution configuration: system prompt, round and
parallel-call limits, and engine feedback prompts. `AgentTools.definitions()` is the exact tool
offer for a round. Environment configuration therefore finishes before `AgentEngine` is
constructed, and the host owns all of it: the runtime resolves the turn's model and hands the
facts to `TurnEnvironment.assemble` (`ufo.host.assemble`), which composes the prompt, the
authorized tool offer, and the skills for every turn — and, when the turn pins a document,
reshapes them through it:

```text
ufo --environment arm.yaml -- upload once --> blob (content-addressed)
                                                    |
x-ufo-environment: sha256:… (each header independent)| load by digest
                    |                                v
runtime queue -- facts --> host assemble (prompt, offer, skills) -- document applied --+
                                                                                       v
                                                                    AgentEngine.run(messages)
```

A document (JSON or YAML, canonicalized so one content is one digest, stored through the terminal
surface's `environment/document` endpoint and replayed byte-identical on recovery) addresses each
target in the turn tree — the same digest rides every spawned child. The recorded ablation arms
are the vocabulary's proof; each `[arm.files]` worktree becomes one document against a shared
stack:

| Recorded arm | Was | Document form |
|---|---|---|
| waiting `child-prompt` | `subagent_coding.md` swap | `profiles.coding.prompt.replace` |
| waiting `bash-desc` | `builtins.py` swap (one field description) | `tools.bash.parameters.background` |
| validation-scope variants ×5 | `[[arm.replacements]]` on the profile prompt | `profiles.coding.prompt.replace` |
| head-tax `inline`/`route*`/`trim` | coding `SKILL.md` swaps (+ prompt in `route3`) | `skills.coding` (+ `profiles.coding.prompt.text`) |
| report-digest, sandbox-cli, app-chat-home, pre-fix | `SKILL.md` swaps | `skills.<name>` |

The waiting experiment's two arms, verbatim:

```yaml
# arm "child-prompt": one bullet into the coding profile's prompt, that target only
profiles:
  coding:
    prompt:
      replace:                # any number of edits, in order; each `old` matches exactly once
        - old: "reserve `bash` for shell-only operations (git, builds, running tests)."
          new: |-
            reserve `bash` for shell-only operations (git, builds, running tests).
            - Run a long command in the foreground with a `timeout` that covers it — never pad
              with `sleep`. A command that must outlive the wait takes `background: true`; the
              task's `exit_file` appearing is the completion signal.
```

```yaml
# arm "bash-desc": one parameter's description, wherever any turn's offer holds `bash`
tools:
  bash:
    parameters:
      background: >-
        Run the command detached and return at once with its task id, log path, and pid …
        Never pad a wait with sleep; the exit file appearing is the completion signal.
```

A skill arm pastes the arm's whole `SKILL.md` under its name: a name the deploy holds is replaced
(its bundled files kept, its image copy skipped so the sandbox mounts this content), and an
unknown name adds a skill — same shape either way, the head-tax and report-digest arms verbatim:

```yaml
# head-tax arm "route3", the skill half: the coding skill's routing rewritten
skills:
  coding: |
    ---
    name: coding
    description: Substantial code work — implementing issues, making pull requests, cloning or
      exploring a repository … call spawn(target="coding") immediately with the member's ask
      verbatim plus the repo URL; never explore a repository yourself.
    ---
    # Coding Subagent Routing

    **Scope:** Spawn a coding subagent when the task requires navigating a repository …
  triage-notes: |            # a name no tier holds: the document adds this skill whole
    ---
    name: triage-notes
    description: Load when the member asks to triage a failing run.
    ---
    # Triage notes
    …
```

A section ablation edits the live `SKILL.md` in place instead of pasting it — frontmatter
included, so a routing-description arm is one edit — and stays anchored: the arm fails loud when
the skill drifts under it, where a pasted body would silently measure the stale text (the
app-chat-home `no-*` family's shape):

```yaml
skills:
  app-chat-home:
    replace:
      - old: |
          It builds the page against the deploy's own kit and hosts what the build wrote — do
          not run a build yourself, and do not pass a `dist` directory.
        new: ""
```

A profile prompt written whole (a GEPA candidate) is `profiles.<name>.prompt.text`. Beyond the recorded arms, a block's `model` pins its target's
model — `profiles.coding.model` runs the coding child on one model while the parent keeps its
own, outranking the tree-wide `x-ufo-model` pin for that target (an own-account profile ignores
both; the runtime resolves, validates, and bills it through `TurnEnvironment.environment_model`,
staying document-blind) — `main.prompt.replace` edits the member agent's rendered prompt,
`enabled: false` withholds a tool, a tool entry with `run` defines the tool as a command in
the turn's sandbox, and `files` seeds content-addressed files into every turn's sandbox — an
authored document names local paths and the client uploads each through `environment/file`,
rewriting the entry to its digest, so a stored document holds only digests and an eval's 66
case archives dedupe naturally (live per-conversation copies are `ufo cp`'s different job):

```yaml
tools:
  count_lines:
    description: Count lines in a file.
    input: {file: {type: string, description: Path to the file.}}
    run: wc -l < "$INPUT_FILE"
files:
  data/case.tar: ./case.tar   # authored: a local path; stored: its sha256 digest
```

Application narrows by construction: rewrites touch only text the model reads, removals only
shrink the offer, a scoped name outside the target's offer fails the turn loud, and every
surviving tool keeps the handler, capability context, and authorization the platform bound. A
`run` tool is the one addition — its implementation is a command in the turn's own sandbox
(inputs arrive as `INPUT_<NAME>` variables, stdout is the result), which grants nothing the
sandbox's shell does not already grant. A missing document fails the turn: an eval arm whose
overrides did not apply must never measure as the default. No deployment gate: a member can
already create an agent with an arbitrary prompt, so a per-turn document grants nothing new, and
the turn row's digest is the audit.

The `ufo` client is the whole developer surface: `--environment` takes a file (uploaded once,
pinned as its digest) or a digest, independent of `--model` and `--remote`. An eval run pins a
document per arm — `[[run]] environment = "arm.yaml"` in an `evals.stack` matrix — local or
remote, so an ablation is one document per arm against a shared stack, with zero host processes.
The engine never starts a host, reads a manifest, opens a process channel, or asks which extension
provided a tool. Tools implemented by a remote host process — live contribution, not a stored
document — stay outside this rail and keep RFC 0015's process, trust, scope, and transport
questions.

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
| `ufo.host.environment` / `ufo.host.assemble` | Document wire shape and digest codec; per-turn assembly of prompt, offer, and skills with the document applied. | The turn workflow resolves the model and calls `TurnEnvironment.assemble`; the engine is built from the returned bundle. | Codec and application unit tests, header admission and refusal tests, and lifecycle turns driven by stored digests. |

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

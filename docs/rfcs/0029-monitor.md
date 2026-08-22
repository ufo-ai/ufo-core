---
rfc: 0029
title: "Monitor — a durable watch that fires once on change"
status: implemented
date: 2026-08-14
---

# Monitor — a durable watch that fires once on change

> Adds `monitor` beside `pause_and_wait`: the agent arms a durable watcher — a shell probe run
> in the conversation's sandbox on an interval, under a required deadline — and ends its turn. A
> change in the probe's output, a probe failure, or the deadline fires **exactly one** arrival
> back into the conversation; quiet ticks post nothing; continuing to watch is an explicit re-arm
> in the fired turn. `pause_and_wait` keeps the other waiting posture — a blocked workflow
> converging on the next member message or its timer — and the two do not overlap: a monitor
> always probes. One new core seam (off-turn sandbox exec under a probe-scoped egress token);
> everything else is the `monitors` extension. The substrate then consolidates: the pause
> re-arms as an extension row fired through the same runner — deleting core's `@pause`/`@once`
> name protocol, admission consumption, and timer-turn takeover for one guarded `invoke`
> parameter — and the schedule store and table follow it out of core. Prior art surveyed:
> Claude Code, codex, pi, openclaw — every one of which keeps pause and watch separate
> primitives.

## Current state

`pause_and_wait` (`extensions/scheduled_tasks/ufo_ext_scheduled_tasks/tools.py:438`) is a passive
pause: it writes a hidden `@pause:{conversation_id}` row (`ScheduleStore.pause`,
`core/src/ufo/scheduling.py:357`) and the turn ends. The workflow resumes on whichever comes
first — a member message (admission consumes the row, `core/src/ufo/surfaces/admission.py:702`;
a queued timer turn is rewritten in place by a member takeover, `admission.py:373`) or the timer
(the per-minute runner claims the row and fires it as a scheduled turn,
`extensions/scheduled_tasks/ufo_ext_scheduled_tasks/runner.py:36`).

The resumed turn must re-check whatever it was waiting on itself. Watching external state — a CI
run, a deploy, an inbox — forces a choice between a long wait (slow reaction) and short waits
(one full model turn per poll, each paying admission, recall, and model rounds to usually find
nothing changed). There is no mechanism anywhere in the codebase that turns background output into
a turn: the Carrier protocol is strictly request/response exec
(`core/src/ufo/sandbox/session.py:235`), the SDK exposes no off-turn sandbox exec
(`ConversationFiles` deliberately carries only `write`/`prune`,
`core/src/ufo/ext/context.py:388`), and sandbox egress tokens require their named turn to remain
running (spec.md §Sandboxing) — so a process left running in the sandbox loses network the moment
its arming turn ends, and carriers suspend or stop idle sandboxes regardless.

What does exist, and what this design rides:

| Machinery | Where | What it gives a monitor |
|---|---|---|
| Durable row + per-minute lease-claimed job + `invoke` with deterministic idempotency key | `runner.py`, `core/src/ufo/runtime/jobs.py` | the fire vehicle — the shipped pattern for every "background thing → turn" (`@pause`, PR review routing, source-subscription alerts) |
| Arrival folding | `admission.py:446`, `engine.py:1396` | fires into a busy conversation fold into the live turn at a round boundary; into an idle one they found the next turn — coalescing for free |
| Delivery-then-stamp ordering | `SubagentResult.deliver`, `loop/subagents.py:591` | crash-safe exactly-once fire: invoke under a deterministic key, then retire the row; a re-post admits nothing |
| Fan-out collapse + cooldown | `loop/delivery.py:37` | the precedent for producer-side batching (60s per-conversation cooldown) |
| Per-conversation sandbox, reused across turns | `sandbox/conversation.py:93` | a probe runs where the agent's files are; E2B auto-resumes, Docker restarts on touch |

## Prior art

| | ufo today | Claude Code | codex | pi | openclaw |
|---|---|---|---|---|---|
| Watch primitive | timer only | Monitor: background script, each stdout line → event; WebSocket mode | none pushed — unified exec is pull-only (`write_stdin` with empty input polls, destructive drain) | none | none — a periodic heartbeat wake over a member-authored checklist; the model does the checking |
| On-change semantics | model re-checks | the script's job (`comm -13` idiom) | runner-side: world-state `render_diff` returns `None` when unchanged → nothing enters the conversation | one example ext holds a last-value guard, UI-only | post-hoc: enqueue-time content dedup + 24h identical-output collapse — no stored value a probe is diffed against |
| Delivery into conversation | scheduled turn | events queue, delivered between turns, never mid-tool | mailbox with `trigger_turn: bool`; child completion uses `false` — push the signal, pull the payload | steer / followUp / nextTurn queues, drained one-per-turn by default | session event queue drained by heartbeat turns; one coalescing wake bus, never mid-tool |
| Volume bound | one resume | none — script's responsibility | `watch::send_replace` collapses N signals into one wakeup; spill-to-file over 2.5K tokens | rate-limit at the drain, not the producer | bounds everywhere: 20-event queue, 5 wakes/60s, 30s spacing, quiet-success drop, 3 commitments/day |
| Timer | durable `@pause` | ScheduleWakeup + cron with jitter | `clock.sleep` (≤12h), ends early on new input | none | durable cron (`at`/`every`/`cron`): backoff, failure alerts, throttled restart catch-up |

Decisions adopted: runner-side change detection (codex's `render_diff → None`, not Claude Code's
script-owned dedup — a stateless probe cannot lie about what changed); nothing-changed posts
nothing; spill-don't-truncate with the full output at a named workspace path (codex hooks, pi
bash); delivery at safe boundaries only (pi's transcript-pairing lesson — ufo's arrival machinery
already guarantees this); fire-then-stamp exactly-once ordering (ufo's own `SubagentResult`);
suppress at the cheapest layer, with volume policy behind one entrypoint (openclaw's heartbeat,
whose best suppressions never reach the model). Decision rejected: Claude Code's unbounded
line-per-event multi-fire — see Alternatives.

### Claude Code's execution model, and where it streams

Claude Code separates execution into layers by who sees output when:

- **Foreground bash** — the user's terminal streams live; the model receives one complete result
  at command exit. Partial output of a still-running tool never reaches the model.
- **Background bash** — `run_in_background: true`, or a foreground command reaching its timeout
  is *moved* to the background rather than killed (never for `sleep`, `git`, or compounds the
  parser cannot follow). The command becomes a **task**: an id plus an output file, both returned
  at start. Bulk output is pulled — the model `Read`s the file when it cares (the dedicated
  incremental-output tool is deprecated in favor of that) — and completion is pushed: a task
  notification re-invokes the model when the command exits. Signal pushed, payload pulled — the
  same split as codex's mailbox.
- **Monitor** — the one line-granular surface: a resident watch process (or WebSocket) whose
  every stdout line (or text frame) is delivered to the model as an event. Same task registry —
  a monitor has a task id and dies by TaskStop like any background command.
- **Background subagents and workflows** — a child agent's transcript goes to an output file; the
  parent gets only the completion notification and final summary. A workflow is the extreme:
  agents' intermediate results stay in script variables and the orchestrating model is re-invoked
  once, at the end. Cross-session teammate messages ride the same delivery discipline.
- **Time** — ScheduleWakeup and cron loops re-invoke an idle session; hooks (Notification,
  SubagentStop, TaskCompleted) observe from the side and wake the model only via an explicit
  async-rewake.

Two rules unify the system. **One registry**: everything long-running — background command,
monitor, background subagent, workflow — is a task (`/tasks` lists, TaskStop kills), with
ownership lifecycles (a foreground subagent's background commands end with its final response;
the main conversation's persist to session end). **One delivery discipline**: every notification,
monitor line, and teammate message lands at a safe boundary — between tool calls of a live turn,
or founding a new turn when the session is idle — never mid-tool.

ufo's mapping: the safe-boundary discipline is the arrival machinery (fold at round boundaries,
found when idle) — already built; the one-registry readability becomes the `monitor` object kind;
signal-pushed/payload-pulled becomes the fire body naming a workspace path for over-cap output.
The layer ufo does not copy is the resident line-granular watch — see Alternatives.

### codex's mailbox

One queue per session (`InputQueue`, `codex-rs/core/src/session/input_queue.rs`): a deque of
`InterAgentCommunication` mail — author, recipient, content, and one bit, `trigger_turn` — beside
a `tokio::sync::watch` channel that carries only "activity happened". Enqueue is push then
`send_replace`, so N mails landing while nobody looks collapse into one wakeup, and the waiter
drains the deque itself: the channel is the signal, the deque is the payload.

The one bit is the whole delivery policy — `send_message` and `followup_task` share one
submission path and differ only in it:

- `trigger_turn: false` (queue-only): the mail waits for the recipient's next sampling. A child's
  completion sends exactly this — **a finishing subagent never wakes its parent**; a parent that
  wants to block calls `wait_agent`, which waits on the same activity channel. Push the signal,
  never push a turn.
- `trigger_turn: true`: an idle recipient gets a turn started with *empty input* — the mail rides
  the normal pending-input drain, so waking needs no special turn body. While trigger-turn mail
  is pending, new user input is refused (`NotSubmittedReason::PendingTriggerTurn`): the queue
  drains before the human speaks again.

A recipient mid-turn drains mail at the top of each loop iteration, before the next model request
and never mid-tool, and pending mail alone keeps the loop alive (`needs_follow_up =
model_needs_follow_up || has_pending_input`) — ufo's terminal-refuses-to-close-over-a-pending-
arrival guard in different clothes. Queue-only child mail is deliberately deferred to the next
turn so a finished model is not re-sampled for it. A **durable sleep** (`SleepItem` on the
thread) flips the wake rule: a thread that declared itself asleep wakes on *any* mail, trigger
bit or not — recipient-declared readiness, ufo's `@pause` row in different clothes (each
converges "wake me on whatever comes" onto one resume). Mail renders as assistant-role messages
with typed headers (`NEW_TASK`/`MESSAGE`/`FINAL_ANSWER`); the injection API returns items back
to the caller on failure rather than dropping them.

What the monitor takes from this: a fire is `trigger_turn: true` mail — the runner decides the
wake because a change is precisely what the agent armed itself to be woken for; a quiet tick is
the `send_replace` collapse taken to its limit (no signal at all). ufo keeps its always-wake
default deliberately: codex's per-message bit exists because arbitrary peers message arbitrary
peers and most mail is not urgent — a topology ufo does not have; here the arming turn chose the
urgency once, at arm time, and one-shot retirement makes a second bit redundant.

### pi's delivery lanes

pi builds no execution substrate at all: bash is synchronous with no default timeout, there is no
background process, timer, or watch tool, and its `detached` spawn flag exists for process-group
kill, not backgrounding. What it builds instead is the most articulated delivery layer of the
three — every outside event enters the conversation through a lane the sender picks:

- `steer` — injected between the current round's tool results and the next model call: reaches a
  running agent mid-work.
- `followUp` — held until the agent would otherwise stop; a pending follow-up restarts the loop
  instead of ending it.
- `nextTurn` — buffered until the member's own next prompt and injected beside it: never wakes
  anything, the one lane none of the other systems have.

An extension send may add `triggerTurn: true` to start a turn on an idle session (codex's bit
again). Both live queues drain **one message per round** by default, however many are pending —
the volume bound lives at the drain, not the producer. Content arriving mid-stream is buffered to
a safe transcript boundary first, because injecting between a `tool_use` and its `tool_result`
corrupts the pairing — the invariant ufo's round-boundary absorption encodes structurally.
Injected mail is a `custom`-role message rewritten to user-role for the model, with a
`display: false` variant that enters context without appearing in the UI.

Watching is therefore left to session-resident extension code: `fs.watch` or `setInterval` armed
at `session_start`, torn down at `session_shutdown`, dead with the process. The repo's one
only-on-change guard is a hand-rolled last-value comparison in an example extension, and its one
file-watcher example has no debounce and re-fires on its own clearing write. pi is the
counterexample shaping this RFC's core seam: with no durable substrate, a watcher lives exactly
as long as a session, and dedup is whatever each author remembers to write.

What the monitor takes from pi: the drain-side volume bound (ufo's analog is absorption at round
boundaries) and the safe-boundary rule. `nextTurn` — a fire that never wakes, waiting for the
member — deliberately has no analog here: a monitor's deadline must end the wait, and the fire
body restates its own context wherever it lands.

### openclaw's heartbeat

openclaw (a gateway daemon, not a CLI harness) answers watching with a periodic model wake and
layered suppression of the *output*, rather than any probe: it has no monitor tool, no file
watcher, and no stored last-value a probe is diffed against — the model itself is the diff
engine, and the machinery's craft is making its nothing-changed runs cost little and post
nothing. Two substrates carry everything:

- a per-session **system-event queue** (in-memory, 20 entries): every producer — cron fires,
  exec exits, webhooks, task completions, subagent progress — enqueues a text event,
  deduplicated at enqueue on content plus a context key, consumed *selectively* so an unread
  event waits for the next wake;
- one coalescing **wake bus**: a single entrypoint for every producer, 250ms coalescing window,
  priority-resolved, with cooldown policy in exactly one module — written after a bug where the
  gate lived on one of two dispatch paths — an intent matrix (manual / immediate / scheduled /
  event), a 30s minimum wake spacing, and a 5-per-60s flood guard.

The heartbeat (default every 30 minutes, per-agent deterministic phase offset so fleets never
stampede) reads a member-authored `HEARTBEAT.md` checklist and drains queued events. Its no-op
suppression is five layers deep, ordered by cost: pre-model short-circuits (empty checklist, no
sub-task due, alerts disabled — zero tokens spent), a structured `heartbeat_respond(notify:
false)` tool, the `HEARTBEAT_OK` text sentinel with a 300-character slack window (models rarely
emit a bare token), a 24-hour identical-output collapse ("prevents nagging when nothing changed
but the model repeats the same items"), and per-channel visibility toggles. Background exec exits
post a machine-parseable summary line filtered twice — the producer drops quiet successes
(completed, no output → silence) and the consumer independently decides relayability under an
8KB cap. Cron is durable SQLite with failure alerts (after 2 consecutive, 1-hour cooldown),
error backoff, and a throttled restart catch-up (at most 5 missed jobs, staggered) — burst-firing
missed work at boot is a failure mode they hit and fixed.

What the monitor takes from openclaw: suppress at the cheapest layer — its best suppressions
never reach the model, and the probe diff takes that to the limit (a quiet tick costs one sandbox
exec, zero model rounds, zero posts); one entrypoint for every fire so volume policy has one home
(here, every fire is an `invoke` through admission); and restart discipline — an overdue monitor
probes once, never a backlog. What ufo inverts: openclaw diffs output *after* the model has run;
the monitor diffs input *before* any model is invoked — the per-tick change detection this RFC
adds is exactly the piece openclaw's machinery shows is missing everywhere.

### Delivery, contrasted

| | codex mailbox | pi queues | Claude Code tasks | openclaw heartbeat | ufo arrivals |
|---|---|---|---|---|---|
| Wake decision | per-message bit, sender's choice; completions default to *don't wake* | sender picks a lane (steer / followUp / nextTurn); `triggerTurn` wakes an idle session | completion always notifies and re-invokes | every producer posts to one coalescing bus; intent drives policy; quiet successes post nothing | always wakes: founds a turn when idle, folds when live (60s sweep cooldown producer-side) |
| Signal vs payload | watch channel is signal only, `send_replace` collapses; deque holds payload | no signal channel — drain points poll the queues | notification carries the signal + summary; bulk output pulled from files | bus is signal (250ms coalesce, priority-resolved); payload waits in the event queue, consumed selectively | the arrival row is both; folding collapses at the round boundary |
| Delivery point | top of loop iteration, never mid-tool | between a round's tool results and the next model call; aside-buffered past stream boundaries | between tool calls, or a new turn when idle | heartbeat turn drains the queue; steering drains at model boundaries, never mid-tool | round boundary (`_absorb_arrivals`), never mid-round |
| Volume bound | wakeup collapse via `send_replace` | one message per round per queue, drain-side | queue capped at 100, oldest dropped | 20-event queue + enqueue dedup; 5 wakes/60s; 30s spacing; 24h duplicate collapse | all pending absorbed per round; one turn per idle fire |
| Keep-alive on pending | pending mail alone forces another round | pending follow-ups restart the loop at would-have-stopped | a message to an idle session starts a turn | unconsumed events stay queued for the next wake | terminal commit refuses to close over a pending arrival |
| Recipient-declared wait | durable sleep: any mail wakes | none — no timer or sleep primitives | ScheduleWakeup | `sessions_yield`: end the turn, results arrive as the next message; polling banned by prompt | `@pause`: member message or timer, one resume, takeover rewrites the timer turn |
| Rendering | assistant-role, typed headers | `custom` role rewritten to user; `display: false` = in context, out of UI | sender-named plain text / system reminder | machine-parseable event lines, re-filtered consumer-side, 8KB cap | user-role `<context>`-tagged message |

## Subagents here vs Claude Code's task system

The user-visible gap this RFC fills is the watch primitive. The rest of Claude Code's task
mechanism was compared against `loop/subagents.py` to check nothing else is missing:

| Capability | Claude Code | ufo |
|---|---|---|
| Delegate | Agent tool: ad-hoc prompt, per-call model/effort override | `spawn_subagent`: boot-frozen typed profiles, schema-validated payload, no per-call overrides |
| Background completion | task notification re-invokes the parent between turns | child terminal → keyed arrival: folds into the live parent turn or founds the next; sweep + 60s cooldown collapses fan-outs (`delivery.py`) |
| Await | foreground call; notifications otherwise | no wait tool, by design — "waiting is never how a result is collected" (`subagents.py:262`); host-side `wait` exists for tools that bound their own hold |
| Message a running child | SendMessage reaches it mid-task | `message_subagent` admits the child's *next* turn; never mid-turn |
| Child → parent mid-run | SendMessage back | none; the terminal handback is the one channel |
| Stop | TaskStop | `cancel_subagent` + cancel reconciler cascade |
| List/status mid-run | TaskList, agent view | none model-facing; portal reads only |
| Session task list | TaskCreate/TaskUpdate | none (todos/objectives are separate object kinds) |
| Watch external state | Monitor | this RFC |
| Cross-session teammates | ListAgents/SendMessage | none — profiles are not addressable identities |

Conclusion: delivery, collapse, and cancellation parity already exist through the arrival
machinery; the deliberate absences (no wait tool, no mid-turn injection, no task list) stay
deliberate. Teammates/cross-conversation messaging is out of scope here. The one structural gap is
the watch primitive, and it composes with the existing delivery machinery rather than adding a
parallel one.

## Proposal

### The tool

One new tool, `monitor`, in its own `monitors` extension, beside `scheduled_tasks`'s
`pause_and_wait` — which keeps the
blocked-workflow pause exactly as it is. The two partition waiting: a pause converges on the
next member message or its timer (one per conversation, a hidden workflow internal); a monitor
always probes, watches independently of member chatter, and reads back as an object. Every
surveyed system keeps these separate (Claude Code: ScheduleWakeup beside Monitor; codex:
`clock.sleep` and durable sleep beside exec polling; openclaw: cron beside heartbeat), and a
merged optional-probe tool would flip convergence, multiplicity, visibility, and fire path on a
single argument — see Alternatives.

| Arg | Type | Meaning |
|---|---|---|
| `ai_response` | str | message shown while the watch is armed |
| `reason`, `next_steps`, `metadata` | str, str, dict? | state the fired turn needs, restated in the fire body |
| `user_description` | str | activity timeline line |
| `deadline_minutes` | int, 1..10080 | the watch's ceiling; always fires if nothing else did |
| `command` | str | shell command run in the conversation's sandbox each interval |
| `interval_minutes` | int ≥ 1, default 5 | spacing between probes |
| `name` | slug | the member's word for the watch, unique per conversation; the object name qualifies it with the conversation (`<conv8>-<slug>`, the artifact kind's identity from RFC 0017) so the kind can resolve a name workspace-wide |

The tool runs the probe once inline, in the arming turn (the turn is live, so sandbox and egress
are simply the `bash` tool's own machinery): a failing command fails the tool call loudly
instead of dying silently in a job later, and the captured stdout seeds the baseline and returns
in the tool result — the agent sees the starting state it is watching from. Then a `monitor` row
persists: command, interval, deadline, baseline, `created_by_member_id` from the arming turn's
acting member, conversation, agent.

### Rows and runner

Monitor rows live in the `monitors` extension's own table (own migration, like memory's
`memory_item`) — never in the scheduled-task table, whose waiting-on-time machinery stays
single-purpose. A per-minute extension job (the same cadence as every runner in the codebase) claims due rows under a lease
and, per row:

1. `exec` the command in the conversation's sandbox through the new core seam (below).
2. Exit 0: compare captured stdout byte-for-byte against the stored baseline. Equal → quiet tick:
   update `last_probe_at`, increment the quiet streak, post nothing. Different → **fire**
   `cause=changed`, body carries the new output; the row retires.
3. Nonzero exit: increment the failure streak; at 3 consecutive → **fire** `cause=failed`, body
   carries the exit code and a stderr tail; the row retires. A success resets the streak.
4. Sandbox unreachable (client carrier with no connected terminal): skipped tick, counted,
   never a failure — the eventual fire reports how many probes could not run.
5. Deadline passed: **fire** `cause=deadline` whatever the streaks say.

An overdue row — a deploy roll, a stalled runner — probes once and compares once: the interval is
a minimum spacing between probes, never a backlog to replay, so a restart cannot burst-fire
(openclaw hit exactly this and retrofitted caps).

### The fire

A fire is `ExtensionContext.invoke(conversation_id, agent_id, body, "monitor-fired:{row_id}",
on_behalf_of_member_id=…, holds_work_already_done=True)` — an internal arrival, so it folds into
a live turn at the next round boundary or founds the next turn, exactly like a background
subagent result, and a spend breach parks it rather than discarding metered work. Invoke first,
retire the row second, both guarded (the `SubagentResult.deliver` ordering): a crash between the
two re-posts under the same key and admits nothing.

Body shape:

```
<monitor_fired name="ci-run" cause="changed">
reason: …
next_steps: …
metadata: {…}
probes_run: 14  quiet_ticks: 13  skipped: 0
</monitor_fired>
<untrusted …>
{new probe output, bounded}
</untrusted>
```

Probe output is command output — walled as untrusted, the same fence subagent
`untrusted_output` uses. The fire body restates `reason`/`next_steps`/`metadata` whole, so a fire
landing after a compaction needs no earlier transcript (the same property today's resume prompt
has).

**One-shot per arm.** A fire retires the monitor; watching further is the fired turn calling
`monitor` again. Every fire is a model turn anyway, so multi-fire would save nothing — and
one-shot makes two hard properties structural:

- *Volume*: a monitor can never produce more turns than the agent explicitly armed. No cooldown
  tuning, no thundering monitor.
- *Self-caused fires are impossible* (the CLAUDE.md invariant): the fired turn's own effects can
  only be observed by a re-arm, whose inline probe bakes those effects into the new baseline.

### Bounds

| Bound | Value |
|---|---|
| Probe stdout capture | 16 KiB, tail-biased, head/tail with an explicit `… N bytes omitted …` marker |
| Over-cap fire payload | full output written to the conversation's `workspace/` via `ConversationFiles.write`, path named in the body |
| Probe timeout | 60s default, 120s max |
| Interval floor | 1 minute (the runner's own cadence) |
| Deadline | ≤ 10080 minutes |
| Armed monitors per conversation | 5; the tool refuses the sixth |
| Metering | probe egress joins the turn path's `egress` dimension on rows naming no turn — the workspace owes them, where a background job's spend is owed; per-agent and per-member rollups join through `turn` and so exclude them |

### The `monitor` object kind

Armed monitors read back as a kind: `list`/`get`/`delete`; `apply` refuses and names the
tool — arming requires a live turn because the baseline is seeded there. Spec: command, interval,
deadline, reason. Status: `armed_at`, `deadline_at`, `last_probe_at`, `probes_run`, quiet streak,
failure streak, skipped count, baseline excerpt. Delete disarms — "stop watching" is the member
saying so in chat and the agent deleting the object. Visibility follows the conversation it
watches, the `scheduled_task` kind's rule. `@pause` rows stay off the object surface as today.

The kind resolves names across the workspace while a slug is unique only within its
conversation, so the stored name is the slug qualified by the conversation's hex prefix and a
unique index over `(workspace_id, name)` holds that rather than trusting it. Unqualified, two
conversations each holding a `ci-run` would let a delete disarm whichever row sorted first, and
a member whose namesake sorted behind another member's private one could never stop their own.

### The core seam: off-turn probe exec

The one capability extensions cannot express (this RFC's answer to the core-doctrine question):
executing a command in a conversation's sandbox outside any turn, with egress. Two parts:

- `ExtensionContext.probes.run(conversation_id, command, timeout_s) -> ExecResult` — wired **only
  into the jobs-role context** (beside `invoker`, `model`, `files`), absent from tool and hook
  contexts. Opens the existing per-conversation sandbox handle (E2B auto-resumes; Docker restarts on
  touch), runs `bash -lc`, bounded by the caps above. Within the existing trust band: a job already
  holds `invoker` and `model`, and an exec it runs is scoped to a conversation of its own workspace.
- A **probe token** at the sandbox proxy: deployment-signed, naming workspace, conversation, probe
  run id and the member the probe acts as; liveness is a TTL equal to the probe timeout instead of
  "named turn still running". Scoping, credential injection, connector forwarding, and metering
  derive as they do for a turn token, reached through the conversation's agent rather than the
  turn's, and the member's private connectors forward exactly as a scheduled fire keeps its
  initiator's (RFC 0023's access-control rule) — so a `gh run view` probe reaches the account the
  arming member connected, and the inline arm probe and every probe after it agree. A watcher armed
  by nobody forwards only what is shared with the workspace, which is also what a turn's own sandbox
  open carries before a tool call re-authorizes it. A keyed provider's sentinel is exported exactly
  as a turn exports it, so a watch on one — "is this Datadog monitor alerting?" — authenticates;
  withholding the variable while the proxy still held the matching injection rule would leave that
  rule inert and 401 every probe.

  One narrowing stands: a probe resolves **no deployment model-key injection**. The platform key's
  sentinel rides every carrier's base environment, so the withholding is made true at the proxy
  rather than left to what a sandbox happens to carry, and an unattended exec is not the
  deployment's model spend to make. That narrowing is complete because a workspace's own BYOK model
  key needs none: those slots declare no injection target at all and are read in-process by the
  model registry, so no model key of either kind reaches a probe. Its egress meters under the
  existing `egress` dimension on a row naming no turn.

The sample extension's job exercises the seam — a probe reads back what its own off-turn write left
in the conversation's workspace — and hand-written conformance tests prove the role split: a
jobs-role context carries `probes`, a tool's and a turn hook's carry none. `ExtensionContext`
capabilities are not Manifest points, so no gate spells this out.

### The pause on the same substrate

`pause_and_wait` keeps its name, arguments, and member semantics — the two-tool split stands, and
the tools still cannot express each other — but its machinery moves onto the monitor's substrate,
deleting the `@pause` hack from core. Today the pause is a name-protocol row
(`@pause:{conversation}`, schedule `@once`) special-cased everywhere it passes: `ScheduleStore`
refuses the prefixes in every verb, admission consumes the row on member ingress
(`admission.py:702`), a member takeover rewrites a queued timer turn in place
(`admission.py:373`), and the engine deletes the row inside turn claim (`engine.py:222`). All of
it exists to answer one question race-free: *has a member message arrived since the pause was
armed?*

The replacement asks admission that question directly. `invoke` grows `unless_member_since=<seq>`:
under the conversation lock admission already takes, the turn is refused — answering
`superseded` — when a member turn past that seq exists or an unconsumed member inbound is queued.
The pause becomes an extension-owned row (conversation, `resume_at`, `origin_seq`, resume
payload, creator; one per conversation); the same runner fires it when due and retires it on
either outcome. The convergence cases, replayed:

| Today | On the substrate |
|---|---|
| member ingress consumes the row; the member turn is the resume | the fire is refused `superseded` at `resume_at`; the member turn already resumed the workflow (its transcript holds the pause's `next_steps`) |
| a member message takes over a queued timer turn — one turn, not two | the member message folds into the queued fired turn as an arrival — one turn, not two |
| `pause()` arms nothing when a member inbound is already queued | the row arms; the same check refuses the fire; the tool's second directive is deleted |
| a crash between fire and enqueue re-fires the same identity | idempotent invoke key + fire-then-retire (the `SubagentResult.deliver` ordering) |

Core deletions: `pause`/`_upsert_pause` and the `@once`/`@pause:` guards in `ScheduleStore`, the
admission consumption and takeover blocks, the `_claim_turn` row deletion, and the
`origin_seq`/`resume_turn_id` columns — replaced by one guarded admission parameter.

### Scheduling moves out of core

Nothing left in core's scheduling surface is core-only. The `scheduled_task` table and
`ScheduleStore` move into the extension (its migration carries the live rows), the runner claims
over them through `owner_candidates`, and `invoke_scheduled` is deleted — a recurring fire is
`invoke(..., as_scheduled=True, on_behalf_of_member_id=creator, idempotency_key=firing_key)` with
the `<scheduled_task>` wrapper composed by the extension that already owns the prompt.
`as_scheduled` (jobs-role only) stamps `admission_source='scheduled'`, which keeps every core
behavior keyed on that label working unchanged: seat gating on the on-behalf member
(`seats.py:50`), the scheduled system-prompt shaping (`engine.py:938`) and per-round seat check
(`engine.py:1707`), never-fold, and the portal's machine-turn classification
(`ext/surface.py:3075`). The schedule authority gate follows the store. Core keeps the *meanings*
of a scheduled turn; it stops owning the storage and the fire loop.

Fire stampings, uniform by shape: a pause or cron fire is `as_scheduled` — a prompt to run, its
own turn, seat-gated on its creator. A monitor fire is internal with `on_behalf_of_member_id` and
`holds_work_already_done` — work already performed and metered, delivered like a subagent result:
it folds, and a spend breach parks it rather than discarding it.

spec.md's tool list, §Agent loop, and the `scheduled_task` table row update in the implementing
commits.

## Change set, wins, costs

Four independently reviewable units, in order; each brings the `invoke` parameters it consumes:

| Unit | Where | Ships | Proof |
|---|---|---|---|
| 1 | core | `ExtensionContext.probes.run` on the jobs-role context; a probe token minted per exec, TTL = timeout, same derived proxy rules minus the model key; a NULL-turn `egress` ledger row per batch | sample extension: a job's probe reads back what its own off-turn write left in the conversation sandbox; tool and hook contexts carry no `probes`; the proxy refuses an expired, forged or foreign-workspace probe token, and resolves it no model-key injection |
| 2 | new `monitors` extension + `invoke` gains `on_behalf_of_member_id`, `holds_work_already_done` | `monitor` tool, monitor table + migration, runner job, `monitor` kind | end-to-end: arm seeds the baseline inline → quiet tick posts nothing → changed output fires, folding into a live turn and founding one when idle → row retired; failure and deadline fires; a member's "stop watching" deletes; a spend breach parks the fire |
| 3 | core + `scheduled_tasks`; `invoke` gains `unless_member_since`, `as_scheduled` | the pause row moves into the extension and `pause_and_wait` re-arms on it; core sheds `pause`/`_upsert_pause`, the name guards, admission consumption + takeover, the `_claim_turn` deletion, and the `origin_seq`/`resume_turn_id` columns | member-then-fire refuses `superseded`; fire-then-member folds to one turn; a crash between fire and retire admits once; the tool's behavioral assertions hold on the new substrate |
| 4 | core → `scheduled_tasks` | `scheduled_task` table, store, and live rows migrate into the extension; `invoke_scheduled` and core `ScheduleStore` deleted; the authority gate follows the store | a recurring fire end-to-end: wrapper, on-behalf capabilities, unseated-creator refusal, expiry; the portal's machine-turn classification unchanged |

Wins over the current tools:

| Waiting posture | today | with `monitor` |
|---|---|---|
| React to external change | `wait_minutes` at best, then a full model turn re-checks by hand | `interval_minutes` at best, zero model rounds until the change |
| Watch across member chatter | a pause converges on one resume — waiting ends at the next message | the watch is independent; fires fold into whatever turn is live |
| A quiet day's cost | recurring `scheduled_task`: a full model turn per tick, admission and recall included | one bounded sandbox exec per tick, model untouched |
| Survive a deploy roll | an in-turn bash poll dies with its turn | row + runner resume; an overdue row probes once |
| Core surface | `scheduling.py` + `invoke_scheduled` + pause special-cases threaded through admission and the engine | four jobs-role `invoke` parameters; the stores and fire loops live in the extension |

Costs, named:

- A second proxy token class — the one security-sensitive change; bounded by per-probe minting,
  TTL, and the same derived scoping/injection/metering rules turn tokens get.
- Probes keep the sandbox warm-ish: E2B resumes per tick (~110–360ms, billed while awake); the
  5-minute default interval is the economics knob.
- One-minute granularity floor (the runner's cadence); sub-minute watching is out of scope.
- Byte-equality diffing is honest but blunt: a clock or counter in probe output makes every tick
  a change. One-shot bounds the damage to a single fire — the agent sees the noisy output and
  re-arms with a deterministic probe (sort, strip timestamps); the tool's guidance says so.
- `invoke` becomes the one seam carrying admission semantics outward — four jobs-role-only
  parameters (`as_scheduled`, `on_behalf_of_member_id`, `unless_member_since`,
  `holds_work_already_done`); the price of core not owning the fire loops.
- Unit 4 moves live `scheduled_task` rows in a data migration.

## Doctrine fit

- **Core/extension**: core gains one narrow seam (off-turn exec + probe token) that only core can
  provide — it lives at the carrier and proxy enforcement points — plus four parameters on the
  invoke seam it already owns, and in exchange sheds the pause machinery, `invoke_scheduled`, and
  the schedule store. Tools, rows, runners, kinds, and fire policy are all extension code; core
  ends up smaller than it started.
- **Batch-at-interval, no self-fire**: probes are interval-batched by construction; one-shot +
  arm-time baseline makes firing on self-caused events structurally impossible.
- **Fail loud**: the probe command is validated by running it in the arming turn; persistent
  probe failure fires rather than silently idling; an unreachable client sandbox is reported, not
  retried into noise.
- **A client's wait always ends**: the deadline is required and bounded; every armed monitor ends
  in exactly one fire.
- **No tool bash subsumes**: bash cannot survive the turn (egress token, sandbox suspension) —
  this is precisely the capability gap.
- **One shape**: two waiting postures, two tools, no overlap — a monitor always probes, so
  neither tool can express the other; pause rows keep one meaning (a converging workflow pause),
  monitor rows one meaning (an independent watch), each in its own typed table. "Two tools where
  one would do is a defect" does not bite: one would not do — see Alternatives.
- **Enforce, don't encode in names**: the `@pause:`/`@once` string protocol — meaning parsed out
  of row names, guarded by refusals in every store verb — is replaced by typed extension tables
  and one guarded admission parameter.
- **Both ends**: the seam ships with its consumer (the runner) and its proof (sample extension +
  end-to-end fire test) in the same unit.

## Alternatives

- **One tool subsuming the pause** (this RFC's first draft: `monitor` with `probe` optional,
  `pause_and_wait` deleted). The optional argument flips four contracts at once — convergence
  with member ingress, one-per-conversation vs five, hidden row vs object kind, timer-turn
  takeover vs arrival fire — a hidden fork behind one field, plus a rename of a proven tool for
  no mechanical gain. Every surveyed system keeps pause and watch as separate primitives
  (ScheduleWakeup beside Monitor; `clock.sleep`/durable sleep beside exec polling; cron beside
  heartbeat). Rejected in review; probe-required is what keeps the two tools non-overlapping.
- **Multi-fire armed monitors** (Claude Code parity: stay armed, fire each change). Saves one
  tool call per continuation, costs the two structural guarantees above — volume then needs a
  cooldown knob (the `DeliverySweep` 60s precedent) and self-caused fires need real analysis.
  Every fire is a model turn either way, so parity buys nothing measurable. Rejected.
- **Resident watcher process in the sandbox, lines streamed out** (Claude Code's literal
  mechanism). A detached process is hostable — nothing kills it at turn end — but it lacks the
  three things a watcher needs. *Network*: its egress token names the arming turn and every
  CONNECT requires that turn to still be running (spec.md §Sandboxing), so once the turn commits
  it can poll nothing external. *Scheduling*: it runs only while something outside keeps the
  sandbox alive — an E2B pause freezes it losslessly (`ufo_ext_e2b.py:35`) and `auto_resume` is
  inbound-only, so staying live is a renewal loop and a billed slot for up to seven days; the
  Docker carrier's idle-reclaim stops the container, and a client-carrier process dies with the
  member's terminal. *A voice*: no path from inside the sandbox reaches admission — the Carrier
  protocol is bounded request/response exec (`sandbox/session.py:235`) and the sandbox holds no
  credential that reaches a surface — so something outside must exec in to read what it found,
  which is the probe again with extra machinery under it. Rejected as the watcher; it composes
  with the monitor as the *workload* — start a long build detached and arm a monitor whose probe
  tails its log. The probe is the read-out; the process never needs to notify.
- **Watcher as a background subagent looping bash+sleep**. Holds a DBOS workflow, a worker seat,
  and a warm sandbox for the whole watch; a deploy drain must wait on or kill it; violates
  "waiting is never how a result is collected". Rejected.
- **A recurring `scheduled_task` as the watcher** (expressible today). Every tick is a full model
  turn that re-checks manually — the exact cost profile this RFC removes; and the task's fires
  do not fold into live turns. Rejected as the mechanism, remains fine for genuinely periodic
  work.
- **Monitor rows inside core's scheduled-task table.** Couples probe columns to the `@pause`
  machinery and the `ScheduleStore` authority gates for no gain; the extension owning its table
  is the memory precedent. Rejected.

## Decisions taken in the build

Every open decision closed as proposed: name `monitor`; interval 5m default / 1m floor, 3-failure
threshold, cap 5; fire carries new output only; unit 4 landed with this RFC; `as_scheduled: bool`.
Deltas the implementation forced, each argued in its unit:

- The seam is `probes.run(conversation_id, command, timeout_s, acting_member_id)` returning the
  existing `ExecResult`; the probe token carries the arming member, so a probe forwards that
  member's connector grants exactly as their turn would. The deployment's model key is the one
  narrowing, withheld at the proxy — a workspace's own BYOK model key declares no injection
  target, so no model key of either kind reaches a probe.
- Probe egress meters as NULL-turn rows in the existing `egress` dimension — no new dimension,
  no ledger schema change; probe counts live on the monitor row.
- The relocated `ScheduleStore` takes its `ExtensionContext` (`ScheduleStore(ctx)`), and three
  narrow SDK reads replaced schema reach-through: an `object_agent_id` re-export,
  `conversation_facts` (audience + surface label, batched), `turn_outcomes` (status + terminal
  text, batched). A conversation absent from the facts map is skipped, never defaulted — the
  reachable case is a borrowed cross-workspace id, so the skip is tenant isolation, not
  robustness.
- The fire-time claim revalidation moved into the store (`claim_holds`) rather than being deleted:
  idempotency collapses repeat deliveries but never asks whether the task still exists, and a
  cancel inside the lease window must not fire. The residual two-statement race is documented on
  the store.
- The `scheduled_task` table was adopted in place — same physical table, ownership moved, no data
  migration; in-flight `@pause` rows at roll time are dropped by migration 0085 (transient,
  stated in its docstring).
- Test databases became per-pytest-process (pid-suffixed, self-dropping) so concurrent runners
  stop resetting one shared name out from under each other.

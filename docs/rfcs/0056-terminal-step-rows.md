---
rfc: 0056
title: "Terminal step rows — one row per member-visible tool call, drawn from the turn record"
status: accepted
date: 2026-09-16
---

# Terminal step rows

> A turn that made a PR drew ~22 tool blocks in the terminal. Ten were the engine's own probes and
> log reads, every header was a raw shell string clipped mid-word, one command was mangled by the
> env-assignment stripper, a timeout read as `exit 137`, nothing marked failure but a red `exit 1`
> line, and while a 60 s command ran the transcript was frozen and the turn clock was gone. This
> gives the terminal the row grammar every peer converged on — a dot that is the state, the agent's
> label over the command, a bounded tail, one fold marker — hides the ops the member did not ask
> for, binds a label to its op with one id every surface now carries, and draws the rows from the
> turn record RFC 0055 gave every surface, so the terminal no longer keeps a step model of its own.

## Current state (before this change)

Evidence is the PR-creation turn of 2026-09-15 (22 blocks, 100 columns).

| Defect | Rows | Cause |
|---|---|---|
| Engine plumbing drawn as agent steps | 10 of 22 | `TASK_SCAN`/`LOG_TAIL` (`background_tasks.py`), `TASK_PROBE` (`tools/tasks.py`), walks and runtime writes ride the same ops; the client drew every op |
| Mangled command | 1 | `is_env_assignment` ate `pid=$(cat` as an env assignment |
| `cd <workspace> && ` on every command | 9 | model text; the client knew its cwd and never compared |
| Headers clipped mid-token, no ellipsis | all long commands | `clipped()` cut at width |
| Timeout read as `exit 137` | 1 | `exec_tail` ignored the reply's `timed_out` |
| No status per step | all | header and body shared one style; the dot never changed |
| Frozen transcript during a long op, turn clock gone | 2 | the running op lived only in the bottom line, replacing the status row |
| Label rows detached from their op | — | the label is a model-generated 3–8 words published async; the only binding (`running <tool>: …`) had no producer |
| Rows drawn off the frame dispatch | — | #3738 folded the record beside the rows for the count and faults only |

## What the peers do

Surveyed 2026-09-16: codex (`codex-rs/tui`), Claude Code 2.1.273, pi, oh-my-pi, openclaw.

| | codex | Claude Code | pi | oh-my-pi | openclaw |
|---|---|---|---|---|---|
| Header | `• Ran <cmd>`, bold verb | `⏺ Bash(<cmd>)`, 2 lines / 160 chars | `$ <cmd>`, wraps | `<icon> Title: description` | `<emoji> Title (running)` |
| Status | dot colour | dot: dim blinking → green/red | background tint | border colour | background tint |
| Output cap | 5 head + 5 tail | 3 rows + `… +N lines (ctrl+o to expand)` | last 5 wrapped rows | viewport-sized tail | 11 rows + `…` |
| Exit code | never in viewport | appended below the fold | text, block turns red | `⟦Exit: 1⟧` | text |
| Per-call clock | never | running only | `Elapsed` → `Took` | compact rows only | never |
| Grouping | reads/searches → `Explored` | none | none | reads → `Read (N)` | none |
| Plumbing | hooks fold to one line | `Ran N hooks (1.2s)` | — | benign skips neutral | subagent internals gated |

Convergences taken: status in the dot, one gutter level, tail plus a hidden-line count, a running row
that settles in place. The header stays the agent's label, ufo's own decision (`prompts/activity.md`).

## Design

### Which ops are steps

An op is a **step** (drawn) or **plumbing** (never drawn). The rule reads two fields already on the
wire and never the text.

| Op | Drawn | Why |
|---|---|---|
| `exec` with `safety_argv` | yes | the model wrote the command (`terminal.py`, `model_command`) |
| `exec` without | no | probes, log tails, file walks, change scans, member-declared environment tools |
| `fileop` naming its call | yes | the model's file tools run inside a dispatch and carry the call they serve |
| `fileop` naming none | no | the change scan the runtime runs once the turn has ended |
| `write`, `read` (copy primitives), `skills` | no | runtime files, output offloads, skill archives, share reads |

Plumbing still runs and holds the wire; it draws nothing. The bottom line keeps the turn spinner and
the latest label, so a member waiting on a backgrounded command sees the clock move.

### `call_id`, on every surface

`Activity.call_id` names the tool call a label is for; `ToolStep.call_id` carries it on the shared
record, folded alike by the reference fold, the portal and the Rust client and pinned by the
conformance fixture. The `run` directive gains a seventh field, the call the op serves, stamped by
the terminal carrier from `TOOL_CALL_ID`, a `ContextVar` the engine sets around each dispatch; an op
issued outside a dispatch names none.

| Arrival order | Row |
|---|---|
| label, then op | the op draws where the label stood, headed by it |
| op, then label | the row starts headed by the command; the label re-heads it in place |
| label, no drawn op | bare label row |
| op, no label ever | header stays the command |

A call's first op binds; a later op of the same call heads its own row.

### Sources cross uniformly

The terminal surface emits the `sources` frame like every other frame; the client folds it into the
record and draws nothing of it. Withholding is the surface's, never the server's.

### Rows derived from the record

The transcript holds one `Segment` entry per stretch of a turn — `steps[from..to)` of its record and
the ops that started among them — and draws it from the record on every change. A turn is one
segment until it drains a member's message; the message then stands between what came before and
what followed, as RFC 0055 lays the portal out. A rolled segment reads `Completed N steps ▸` over
its answer. While the turn runs the answer is the trailing text of the segment, drawn
committed-blocks-first with the open tail live; once the done frame lands its text is the answer and
stands in that passage's place, as RFC 0055's `answerOf` has it — so the surface never says a done
frame's text, and an answer whose stream an op or a resume cut before the frame reads once. `N`
counts labelled tool steps, the words that preceded one, unbound drawn ops, and runs that stated a
step. Blocks inside a segment stand a blank row apart. A bare command — an exec op no label heads —
stands until the next bare command follows, which takes its place; `N` still counts every one. Runs
draw under the last segment once the turn has ended, as `label ▸` rows that open by click; while the
turn runs, the bottom line alone carries a run's latest step. Rolling a turn up closes its words, so a
turn ended by a detach or a stop keeps them.

### The row

```
⏺ <label>[ · <suffix>]              dot = state; label = activity, else the act
  $ <command>                       only under a label; dim; first line; clipped with …
  … +N lines                        muted; hidden lines are above the tail
  <tail, ≤ 5 lines>                 dim; each clipped with …
  exit N                            red; absent when the op timed out
```

| Element | Rule |
|---|---|
| Dot | running: accent, blinking on the status tick; done: `tool_title`; failed (exit ≠ 0, refusal, op error): `error`; timed out or stopped: `warning` |
| Header without a label | exec: `$ <command>`; fileop: `read`, `edit`, `write`, `grep <pattern> <path>`, `glob <pattern>`, `changes`, paths relative to the terminal's cwd |
| Command text | `safety_argv`'s command as the model wrote it: first line, `…` when more follow, a leading `cd <cwd> && ` dropped by path equality |
| Suffix | ` · 12s` while running once ≥ 2 s, ticking; kept on completion when ≥ 2 s; ` · timed out after 60s` from the reply's `timed_out` and the op's `timeout_s`; ` · stopped` when the turn ends over a running op |
| Edit header | `edit <path> +A −R` from the same diff the body draws |
| Bottom line | always the turn spinner, the latest label and the turn clock, under one blank row |

### Transitions

```
⏺ Running the extension tests · 4s                 running: accent dot blinking, clock ticking
  $ make test-one FILE=extensions/ufo/tests/test_ext_ufo.py 2>&1 | tail -25

⏺ Running the extension tests · 21s                done: dot settles, tail drawn under the fold
  $ make test-one FILE=extensions/ufo/tests/test_ext_ufo.py 2>&1 | tail -25
  … +6 lines
  ......................................................................   [100%]
  70 passed in 13.43s

⏺ $ uv run pytest core/tests/test_turn.py -q · 2s   op finished before its label: the command heads the row
⏺ Checking the failing test · 2s                    the label lands and re-heads it in place
  $ uv run pytest core/tests/test_turn.py -q
  1 failed, 3 passed in 0.42s
  exit 1                                            red dot, red line

⏺ Committing and pushing the branch · timed out after 60s     amber; the probes that follow draw nothing
  $ git checkout -b ufo/1c034400-drop-terminal-sources-note && git add extensions/ufo/…

⏺ Removing the unused import · surface.py −1        edit: relative path, counts, diff
  -from urllib.parse import urlsplit

⏺ Waiting for the push to finish · stopped          Esc pressed: amber, no reply drawn
```

## Doctrine fit

Nothing here reads English: step selection is an op field, the `cd` strip is path equality, the
header fallback is the shell prompt glyph. No prompt text changes. `call_id` ships with its consumers
in one change: the three folds, the fixture, and the terminal's binding. The one reversed decision:
a labelled call used to hide its command; on a connected terminal the op is a subprocess on the
member's own machine, so the label stands first and the command dim beneath it.

## Not taken

| Alternative | Why not |
|---|---|
| Fold consecutive reads into one row (codex, oh-my-pi) | the label is already the compaction; a fold would hide labels |
| Background tint or box border as the status | two rows per step or a full-width paint; the dot already exists |
| Exit code as dot colour only (codex) | `exit N` is what the member types next |
| Wrap tool output instead of clipping | one long grep line becomes a screen |
| A muted `waiting on a background command` row for plumbing | the member cannot act on it; the timed-out row's suffix and the agent's words carry the state |

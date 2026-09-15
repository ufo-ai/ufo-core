---
rfc: 0055
title: "The turn record — one typed fold of a turn's frames, drawn by one layout"
status: proposed
date: 2026-09-15
---

# The turn record — one typed fold of a turn's frames, drawn by one layout

> The portal folds fifteen SSE events into one grab-bag object by mutating a field per event, and
> fixes the reading order afterwards with splices and two bookkeeping lists. What a member sees
> depends on which of a frame and a POST response landed first. This replaces the grab-bag with a
> typed record of the turn — the shape the debugger stores — built by a pure reducer from the live
> stream and from the transcript read alike, and drawn by one layout that names every step it
> withholds and the one place it reorders.

## Current state

| Where | What |
|---|---|
| `extensions/web/frontend/src/lib/turnStream.ts:236-540` | Fifteen listeners each mutate `LiveTurn`. `absorbed` (`:351`) splices a wordless bubble into `messages`, clears `arrival_id` and `queued` off bubbles by a pairing heuristic, and resets the live turn. `reply` (`:329`) splices a bubble before the first waiting message. `resumed` (`:421`) moves the turn's words into a note. `terminal` (`:479`) replaces the words with the frame's. `record()` (`:236`) folds the live turn into a bubble at three call sites. |
| `extensions/web/frontend/src/lib/chatStore.ts:63-64` | `absorbed` and `spoken` exist to survive two races: a drain frame beating the send's response, and a replayed reply frame meeting the bubble it already drew. |
| `extensions/web/frontend/src/kernel/messages.tsx:457` | The live row draws the step line above the streamed words. A settled bubble never draws its `events`. |
| `core/src/ufo/runtime/ext/surface.py:386` | `member_message_said` strips `<context>`, `<injected_context>` and `<agent_detail>` off a member's inbound; the portal never learns anything was folded. |
| `core/src/ufo/runtime/ext/surface.py:273`, `:933` | The silence sentinel `<response></response>` is checked by durable surfaces before they post and by nothing on the web path: the sentinel reaches the portal as the reply's text, and the Markdown renderer happens to draw an unknown element as nothing. |
| #3716, merged and reverted 2026-09-15 | Added `interstitial` and `passages` to `LiveTurn` — the third and fourth fields holding one turn's words. |
| `extensions/debugger/ufo_ext_debugger` | Stores the turn row with its `TerminalFrame`, the DBOS steps (`kind: model \| tool \| workflow`, in order), the transcript's content blocks, and the raw frame log (`Tail.tsx`). |

Three defects fall out of the shape rather than of any one handler: words appear during a round
and vanish at its end; a label survives a drain only as a detached DOM node the test harness
catches mid-render; and a message's place in the log is decided by a splice whose outcome depends
on frame order.

## Proposal

One record per turn, built the same way from the stream and from the transcript read, drawn by
one pure layout. All of it lives in `src/lib/turnRecord.ts`.

| Layer | Type | Rule |
|---|---|---|
| Boundary | `Frame`, one member per SSE event name in `EVENT_KINDS` | `decodeFrame(kind, data)` validates the payload. An unknown event or a malformed payload logs a fault and is dropped — nothing downstream reads a shape the boundary did not admit. |
| Record | `TurnRecord { id, steps, runs, meter, end }`, and `LiveTurn` adding what the page alone knows: `model`, `files`, `apps`, `connect`, `reconnecting` | `fold(record, frame)` is a pure reducer. A `Step` is `text`, `tool` (label and sources), `reply`, `comment`, `drain` or `resumed`, kept in arrival order. `end` is the terminal frame, a park, or a lost stream. |
| Fault | `fault(kind, detail)` in `rum.ts` | A frame after `end`, a second `end`, a run frame naming a parent the record does not hold, a reply with no words: each logs to the console and to RUM, and the record is left as it was. |
| Settle | `settled(record): Message[]` | Steps are cut at each `drain`. Every segment but the last is a wordless reply carrying its steps as `events` (`note` for words, `activity` for a tool step) — the shape `_rendered_messages` emits for the same turn on reload. The last segment states the answer. |
| Answer | `answerOf(text)`: `words \| silence \| none` | A done terminal's text is the answer, else the closing passage. The silence sentinel is one constant mirrored from Python and pinned by `gates.py`; a reply that is silence and carries nothing else draws no row at all. |
| Layout | `layout(messages, live): Row[]` | Rows in reading order. Member messages the live turn drained stand after the segment that drained them; the rest stand under the live row. Each live row carries the steps it withholds as `folded`, so what the screen hides is a readable count on the element. |
| Hidden inbound | `Message.hidden: "agent_detail"[]` | `member_message_said` names every element it folded (`MemberSaid.folded`); the bubble reports the one that is news — an extension's fold, not the engine's envelope on every message — and carries it as `data-hidden`. |

### The contract

The record's types are pydantic first: `ufo.runtime.turns.record` (`ufo.sdk.record`) declares
`TurnRecord`, the `Step` and `TurnEnd` unions, `SubagentRun`, `ActivityEvent` and `Meter`, reaching
core's `TerminalFrame` and the hub's `SourceRef`. `ufo_testsupport.contract` renders their JSON
schema as `extensions/web/frontend/src/lib/contract.ts` — one `export type` per model, a
discriminator always required, a defaulted field optional — and dumps one instance holding every
variant to `tests/fixtures/record.json`; `test_contract.py` fails when either file is stale, and
the portal's `contract.test.ts` settles and lays that instance out. `turnRecord.ts` imports its
types from the rendering and adds only what the page alone knows (`LiveTurn`). The web surface's
run node is the same `SubagentRun`, dumped at the wire. A Slack or terminal fold adopts the
contract by rendering the same schema into its language; the fixture is the conformance case.

### The transitions

| Frame | Effect on `steps` | Fault |
|---|---|---|
| `message` | Appends to the open text step; else closes the open step and opens a text step. | after `end` |
| `activity` | Closes the open step; opens a tool step with the label. | after `end` |
| `sources` | Adds to the open tool step; with none open, closes the open step and opens an unlabelled tool step. | after `end` |
| `subagent_activity` | `applyRunFrame` on `runs`. | parent unknown; after `end` |
| `subagent` | Replaces the run of that conversation, or appends it. | — |
| `reply`, `comment` | Appends, unless the id is already held (a replay). | empty words |
| `absorbed` | Appends a `drain` naming the arrivals not yet drained; none new, no step. | — |
| `resumed` | Closes the open step; appends `resumed`. | — |
| `cost`, `files`, `apps`, `connect` | Sets the field. | after `end` |
| `terminal`, `parked` | Closes the open step; sets `end`. | a second `end` |

### The one reordering

Within the live row the working line — the current segment's last tool label, its open step's
sources, and the runs still going — is drawn above the words streaming under it, although in
arrival order the words came first. `layout` states this in one place. A drained segment carries
no label into the next: a drain is a round boundary, and the tool the label named has returned.

### Derived, not kept

A member message waits while the live turn holds no `drain` naming its arrival. A send whose
response has not landed claims, in order, a drained arrival no bubble carries — the same pairing
the `absorbed` handler ran, now a pure read. So the drain-beats-response race resolves to one
answer whichever lands first, and `absorbed`, `spoken`, `turn`, `answering` and
`handoffs.question` leave the store: the live record carries its own id and its terminal's
question.

### The proof

`settled(fold(frames))` equals the `messages` the server projects for the same turn, asserted on a
hand-built scenario of interstitial words, two tool steps, a drain and an answer. Every fault row
above has a test that emits the frame and asserts the log line. `gates.py` reads `EVENT_KINDS` as
the one listener set and pins `SILENCE_SENTINEL` across the two languages.

## Doctrine fit / implications

Nothing crosses the wire unchecked: the boundary decodes every SSE payload, where today every
handler casts `JSON.parse` output. One shape: a turn's words are text steps, in one list, and the
answer is a rule over them rather than a fourth field. Both ends: `hidden` is produced by
`_member_bubble` and consumed by the bubble's attribute and the record type. The terminal client
keeps its own step model and is untouched; the server projection is untouched, since the client's
settled form is held equal to it rather than replacing it.

Two visible changes. Passages a turn streams between tool steps join with a blank line instead of
running together. A silent turn draws nothing: no bubble and no meta line.

## Alternatives

| Not taken | Why |
|---|---|
| The server streams record patches instead of frames | The debugger stores frames and the tail resumes from a frame cursor; a patch stream would be a second wire beside the one the hub already replays. |
| A turn-shaped transcript payload (`turns: []`) | `_rendered_messages` feeds the earlier pages and its 35 tests; holding the client's settled form equal to it costs one test and no wire change. The transcript's `Message` joins the contract when the Slack fold adopts it. |
| Hand-mirrored TypeScript types | Every drift found on this path was a hand mirror; rendering the schema costs one module and one freshness test, and the same renderer serves Rust. |
| Keep the raw frame log in the store | The fold loses nothing a screen or a test reads, and the debugger's Tail already is that log. |
| Draw interstitial passages above the working line | #3716 did, and was reverted the same day. The record keeps them as steps; where a screen draws them is the layout's call. |

## Open decisions

None. Every rule above is derivable from the wire's own docstrings and the server projection.

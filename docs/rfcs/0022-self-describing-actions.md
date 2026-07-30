---
rfc: 0022
title: "Self-describing actions — one declaration a panel, a form, and a model all read"
status: proposed
date: 2026-07-30
---

# Self-describing actions — one declaration a panel, a form, and a model all read

> A tool or object kind declares once that a member may invoke it outside chat, in what shape, and
> with what label; the intent lane resolves that declaration instead of a hand-maintained union
> inside the web extension, and the portal renders the control from it instead of hand-written
> markup per mutation. The engine stops naming three tools at five call sites and asks the registry
> which payload the round's final tool declares, over the same text parse it uses today. Same doctrine — mutations remain
> audited turns dispatched verbatim, authority stays in the callee; only the duplication goes.

## Current state

Wave 2 of the portal (#624) landed eight mutating panels through the prepared-intent lane
(#859 built the lane, #898/#899/#902/#904/#909/#910 filled it). It works, and the parts that work
are not in question:

- A panel submit is a **typed envelope dispatched verbatim** to a named tool or object verb — no
  model round, no synthesized message. `submit_intent`
  (`extensions/web/ufo_ext_web/panels.py:194`) admits it as a turn on the member's durable intent
  conversation (`ctx.admit`, `:227`), so the turn is the audit record and the per-conversation
  partition serializes one member's submits; the engine's `run_intent`
  (`core/src/ufo/loop/engine.py:868`) dispatches the stored envelope after admission.
- **Every authority gate is the callee's own** — `AgentObjects`' admin gate, `connector_grant`'s
  owner gate, `memory_update`'s subject rule, `request_credentials`' admin + fillable-slot rule —
  and a refusal reaches the panel as the callee's own words. The surface adds two *non-authority*
  prechecks, both consulting deploy state no input model can see: a model the registry cannot serve
  refuses before admission (`panels.py:209-212`, because a stored unknown id "would wedge the
  agent's every later turn at setup", `:198-201`, pinned at
  `extensions/web/tests/test_ext_web.py:3029`), and a member-facing credential slot name resolves
  against `list_credential_slots()` — the member-fillable set only (`panels.py:214-220`, pinned at
  `test_ext_web.py:2748` and `:2777`).
- **Object kinds already describe themselves** for the model's benefit. `ObjectKind`
  (`core/src/ufo/objects.py:576`) carries `name`, `description`, `guidance`, `spec_model`,
  `list_fields`, `agent_target_verbs`; `object_explain` returns `spec_model.model_json_schema()`
  (`:898`). `object_registry` (`:603`) is the boot gate over the whole set.
- **Tools already describe themselves**: `ToolDef` (`core/src/ufo/tools/registry.py:32`) carries
  `name`, `description`, `input_model`, `handler`, and behavioural flags, and `schema()` projects
  the input model to the wire.

Three duplications sit on top of that, all introduced by wave 2 and all in the wrong place:

1. **The web extension keeps a second copy of the callable set.** `PanelIntent` (`panels.py:94`) is
   a discriminated union of four models — `ApplyIntent | AudienceIntent | CorrectionIntent |
   CredentialIntent` — whose `Literal`s enumerate seven verbs across the four (`:44`, `:68`, `:78`,
   `:88`) and eight kinds (the seven at `:45-47` plus `CorrectionIntent`'s `memory` at `:89`), plus
   two cross-field validators (`_verb_pairs_with_its_kind`, `:51-60`). `ToolIntent.tool`
   (`core/src/ufo/schema/records.py:40-49`) carries the same closed set again in core, where
   `run_intent` re-validates the stored row after the database round-trip.
2. **The envelope→call translation is a hand-written `match`.** `_tool_intent` (`panels.py:102-171`)
   maps each intent shape to a `ToolIntent`, composing tool inputs — every `user_description`, and
   for credentials a whole `reason` sentence — in the web layer. That is callee copy authored in
   the caller.
3. **The engine names each out-of-band payload.** `_final_act` (`engine.py:518`) has **three**
   `(tool name, payload model)` pairs across **five** call sites: the intent path lifts
   `ConnectRequest` (`:935`) and `CredentialRequest` (`:941`); the model-round path lifts
   `AskUserInput` (`:1160`), `CredentialRequest` (`:1161`), and `ConnectRequest` (`:1164`).
   `TerminalFrame` carries all three as typed fields (`records.py:165-167`).

The cost is not aesthetic. A new panel mutation today edits: the kind or tool, a `Literal` and a
`match` arm in `panels.py`, `ToolIntent`'s `Literal` in core, a renderer in `portal.html`, and — if
it hands something back — `_final_act`. Six places, three of them in a surface that should know
nothing about the callee.

## Proposal

One declaration per action, read by every consumer.

### The declaration

`ToolDef` and `ObjectKind` each gain one optional field holding a **tuple** of member actions —
empty means chat only, the safe default, and no existing declaration changes meaning. A tuple, not
one action, because a callee routinely offers several: the portal renders Resync, Share, and Remove
on the single `source` kind (`portal.html:1171-1175`), and Make private / Share with agent / Revoke
on `connector_grant` (`:1906`, `:1923`). One action per callee cannot express wave 2, which unit 1
ports.

```python
@dataclass(frozen=True)
class MemberAction:
    """One thing a member may do to this callee outside a chat message."""

    intent: str                          # the wire verb a panel submits; one deploy-wide namespace
    label: str                           # the control's words, spartan, imperative ("Resync")
    submit_model: type[BaseModel]        # the member-supplied fields ONLY
    compose: Callable[[BaseModel, ActionContext], BaseModel]   # → the callee's input model
    confirm: str | None = None           # a destructive action's confirmation sentence
```

Two consequences of the tuple. Actions on one callee differ in more than their label — Remove maps
to the kind's delete verb while Resync and Share map to apply with different specs — so `compose`
is per action, which the shape above already gives. And "one declaration names one callee" becomes
*one action names one callee verb*, which is what makes the cross-field pairing rules unnecessary;
the RFC's later claim rests on that, not on one-action-per-callee.

`submit_model` is deliberately **not** the callee's input model. Those models carry fields the
member must never supply: `ObjectApplyInput` and `ObjectDeleteInput` both carry `agent`
(`objects.py:712-734`), and the cross-agent gate that guards it (`objects.py:1007-1014`) raises on
three conditions — the turn's agent not main, a typed subagent, or no speaker — none of which a
portal intent on the main agent's lane trips, and it consults no web audience, while `_panel_gate`
walls only the *path* agent. A member-supplied `agent` on the main agent's lane would therefore
mutate an agent the 404 wall excludes. Both models also require `user_description` (`:722`, `:732`), which is the audit line the callee authors, not a
text box a member fills. So the member submits its own narrow model, and `compose` — authored by
the callee — builds the callee's input from it plus an `ActionContext` carrying the server-held
values (the path agent, the acting member, the resolved slot). One producer per field, and no
callee field reaches the browser unless its declaration puts it there.

Constraints and where each is enforced — none of them free:

| constraint | site |
|---|---|
| `intent` unique across tools *and* kinds | `validate_ext_tools` (`core/src/ufo/ext/loader.py:648`) — the only deploy-boot site that sees both sets; `object_registry` sees only kinds (`objects.py:603`) and `ToolRegistry.__post_init__` only tools (`registry.py:62-71`), and the latter is rebuilt per turn |
| `submit_model` JSON-representable, secret-free, `extra="forbid"` | a new check; `_validate_spec_model` (`objects.py:629`) is kind-only, takes `(owner, kind)`, and additionally cross-checks `list_fields`, so it is a model to copy, not a function to reuse. The tool registry has no JSON or secret check at all |
| `label` and `confirm` non-empty when present | the same boot site |

A declaration whose callee has no member-reachable authority gate is not detectable at boot; the
callee's tests own that, as today.

### The lane

`PanelIntent` collapses to `intent: str` and `input: dict[str, JsonValue]` (the parameterized form
`ApplyIntent.spec` and `ToolIntent.input` already use — `AGENTS.md` bans bare `dict[str, Any]`
across a module boundary). Validation becomes: resolve `intent` in the registry, validate `input`
against its `submit_model`, `compose` the callee input, dispatch verbatim.

Three things this must carry:

- **The two non-authority prechecks stay in the lane**, named. Neither is input validation: the
  model check consults the deploy's registry and the slot check consults the member-fillable set,
  and both must refuse *before* admission — the first because a stored unknown model wedges every
  later turn. The slot lookup also feeds `compose` (the resolved `CredentialSlotView`), which is
  why `ActionContext` carries it. Whether the model check belongs in the callee instead is an open
  decision below.
- **The closed admissible set stays enforced in core after the round-trip.** `ToolIntent.tool`'s
  `Literal` is re-validated by `run_intent` on the stored row (`engine.py:910`), and
  `records.py` is the boundary-records module that imports no registry — a `tool: str` checked only
  in the surface would widen what an admitted row can reach. The `Literal` therefore stays; what
  goes is the surface's *duplicate* of it. Cutting it too is an open decision, not a claim.
- **The two cross-field rules disappear by construction, and their proofs must be replaced.** One
  action names one callee verb, so `connect`×`connection` pairing and "a credential intent never
  carries a spec" cannot be expressed wrongly. But four tests assert those refusals through the
  envelope — `test_ext_web.py:2679`, `:2706`, `:2826`, `:2853` — and a `{intent, input}` envelope
  has no verb×kind pair to refuse. Their replacements must carry three properties a
  status code alone does not. **No turn is created** — all four assert `turns == 0` (`:2703`,
  `:2725`, `:2850`, `:2868`), and "refuses before any turn exists" is the named property, distinct
  from the status: the model precheck answers 200 with `{"applied": False}` and also creates no
  turn. **A submitted secret never echoes in the refusal** — `:2845` asserts `"s3cr3t"` is absent
  from the response body, and this is the case the collapse most endangers, because a
  `submit_model` `ValidationError` renders offending input values by default; it needs its own
  proof rather than a generic 400. And **one of the four ports as-is**: `:2853` submits an unknown
  *verb* with no pair in play, which the unknown-`intent` replacement covers one-for-one, so
  "cannot be ported" overstates it for that test.

Unchanged: admission as a turn, the durable intent conversation, per-conversation serialization,
the callee's authority and refusal text, and `_panel_gate`'s 404 on a walled path agent — which
holds only because `agent` is not a member-supplied field (above).

### The payload channel

`ToolDef` gains `final_act_model: type[BaseModel] | None`, and the engine asks the registry which
model the round's final tool declares instead of naming three tools at five call sites. What that
deletes is precisely the tool-name argument — and no more. The payload does not stop at
`_final_act`: each call assigns a named local threaded into a distinctly typed keyword through
`_commit` and `_commit_once` (`engine.py:1897-1899`, `:1950-1952`) onto the three typed fields at
`records.py:165-167`. A registry lookup returns `BaseModel | None` rather than today's generic
`PayloadT`, so something must still route payload type → frame field, and this RFC keeps the three
fields deliberately (below). The triple therefore *moves* rather than disappears, and unit 2 owns
specifying where the routing lives and what `_final_act`'s signature becomes; a fourth handoff tool
still edits `TerminalFrame` and the commit path. The honest gain is one lookup instead of five
hardcoded tool names, not the end of per-payload code.

The parse stays text-based, deliberately. `_final_act` reads the payload out of the *handler's
result text* (`engine.py:518-536`) so that "a pre_tool_use hook that folded the args is honored …
A result a post hook rewrote past recognition carries no payload; the reply's prose still asks" —
and `post_tool_use`'s `ModifyOutput` replaces that text wholesale
(`core/src/ufo/ext/manifest.py:417-420`). Two tests pin the suppression directly
(`core/tests/test_engine.py:1866-1867`, `:2086-2087`). A typed field on `ToolResult` is not part of the text a hook replaces, so a redacted `connect_account` result
would still emit its OAuth handoff. Making the *lookup* declarative
while leaving the *parse* where it is keeps the property and still deletes the hardcoded triple.
`ask_user` is no exception to that reasoning and does not support it either: its payload *is*
handler-structured (`ask_user_handler` builds the dict and serializes it after the directive,
`core/src/ufo/tools/builtins.py:618-627`), exactly as connect and credential are, so a handler-set
field could have carried it. The hook-suppression property is the whole argument, and it stands on
its own.

`TerminalFrame`'s three typed fields stay. Surfaces render a question, a credential prompt, and a
connect link differently, so each field has a distinct consumer; collapsing them into one
polymorphic slot would trade a named contract for a cast.

### Generated UI

The portal stops hand-writing a control per mutation. A view renders, for each row and each
declaration:

- a control labelled `label`, disabled when the viewer's audience does not reach the target;
- on activation, a form derived from `submit_model.model_json_schema()`;
- `confirm` as a confirmation step when present;
- on submit, one POST carrying `{intent, input}`;
- the callee's outcome text verbatim beside the control. A returned **credential** prompt renders
  through the existing prompt form — `_outcome` returns exactly that field (`panels.py:175-191`).
  A **connect** handoff does not arrive here: the authorization URL is minted per stream-reading
  member (`ctx.connect_url(turn_id, member_id)`) and rendered off the SSE stream, "never in a
  transcript or an intent response" (`ApplyIntent`'s docstring, `panels.py:38-41`). The generator
  must not render a handoff from the POST body.

What the generator inherits from today's Overview form:
`specField` (`portal.html:1440-1467`) builds a `<select>` only when its *caller* passes an options
list — the caller passes `data.models` for the `model` key (`:1742-1746`), a separate payload field
`agent_overview` supplies, because `AgentSpec.model` is a bare `str`. It never reads `prop.enum`,
never reads `prop.description`, and renders the raw key as the label. So the generator inherits the
boolean→checkbox and fallback→text branches and must **build**: enum→select (needing a declared
source for runtime-valued enums like the model list), description→hint, and a humane label.

It must also express a per-verb field exclusion. The Overview form derives from `_update_schema()`
(`panels.py:259-267`), which strips create-only `prompt` because "a field the update verb refuses
must not render on the update form" (`AgentObjects.apply` raises on any prompt, `agents.py:162`).
Under this design that exclusion is free — the update action's `submit_model` simply omits
`prompt`, and the create action's includes it — which is an argument for per-action submit models
rather than one model per kind.

Hand-written renderers do not all disappear, and should not. A view whose value is *layout* — the
artifact viewer's pinned panel, the conversations mini-debugger's subagent tree, the memory
listing's paging — stays hand-written; generation covers the repetitive case, a labelled control
over a typed input against a row.

## Doctrine fit / implications

**What goes in core, and why an extension cannot express it.** `MemberAction` sits on `ToolDef` and
`ObjectKind`, which are core types, because the namespace must be deploy-wide: a kind-declared
`intent` colliding with a tool-declared one is a member submit routed to a callee with a different
authority gate, and only `validate_ext_tools` (`loader.py:648`) sees both sets. An extension cannot
own that check. Nothing named `MemberAction` or `intent` reaches core at run time — `run_intent`
re-validates a `ToolIntent` whose `input` is already the composed callee input
(`engine.py:910`, `records.py:40-49`) — so resolution and `compose` both run in the surface, before
`ctx.admit` (`panels.py:227`), which is also what `ActionContext`'s contents imply.

**That resolution site is a new seam, and unit 1 owns it.** `SurfaceContext` carries
`_objects: Mapping[str, BoundKind]` and no tool registry (`core/src/ufo/ext/surface.py:526-543`),
while five of the seven callees the lane serves are tools, not kinds — `connect_account`,
`request_credentials`, `memory_update`, `grant_web_access`, `revoke_web_access`. An
intent→declaration index spanning both sets has to be built at boot, where `validate_ext_tools`
already sees both, and threaded into `SurfaceContext`. That is unit 1's largest change and its
riskiest, since it widens a context every surface holds.

**It must be exportable.** Half the callees in scope are extension-owned —
`grant_web_access`/`revoke_web_access` are `ToolDef`s in `extensions/web/ufo_ext_web/audience.py`,
and the `connection`/`connector_grant` kinds live in `extensions/connectors` — and extensions import
only `ufo.sdk` (gated at `gates.py:158`). So unit 1 adds a public SDK export, `ufo.sdk.actions`,
carrying `MemberAction` and `ActionContext`. That is a new public surface and should be reviewed as
one.

**Is a label web presentation in a core type?** `label` and `confirm` are member-facing words on a
core dataclass, which is the objection to answer. They belong there for the same reason
`ObjectKind.description` and `guidance` already do: they are the callee's account of itself, and
the alternative — a table in the web extension mapping intents to labels — is precisely the
duplication this RFC removes, reintroduced one field later. The line held here is that nothing
*layout* enters core: no widget, no order, no grouping, no CSS hook. If a second surface wants
different words, that is a translation concern and a real one, recorded below.

**One shape.** `describe` is gone: `user_description` has exactly one producer, `compose`, and the
member never sees the field. `submit_model` is the one thing the browser posts and the one thing
validated.

**Both ends or neither.** `intent` and `submit_model` ship with the lane and `compose` with the
first ported mutation, all in unit 1. `label` and `confirm` have no reader until the generator in
unit 3 — between the units their only consumer would be the boot check that they are non-empty,
which renders nothing — so they land with unit 3, not with the dataclass, and the non-empty
constraint row lands with them. `confirm` additionally needs a named destructive action to justify
it, which unit 3 is where one exists. A `surfaces` field is not declared — its only value would be
`{"web"}` (Slack's member actions are chat messages; Debug and Memory explorer admit nothing per
`spec.md`), and a knob whose filter is constant-true is declared for later.

**Fail loud.** Every constraint has a named site above; a declaration that violates one fails boot,
not a member's submit.

## Alternatives

- **Leave it.** Defensible: it is duplication, not incorrectness, bounded by how many panel
  mutations exist. Rejected on trajectory — every future kind pays the six-place tax, and the
  `_final_act` pair list grows with it.
- **Direct HTTP endpoints per mutation** ("pull the API out and call it"). Rejected: it discards the
  audit record, the per-member serialization, and single authority — the three things the turn lane
  buys — and re-creates the bespoke-CRUD surface `spec.md` forbids. The underlying capability is
  already factored out of the chat path (the sealing lives in `CredentialRequests.seal`, called by
  the `request_credentials` handler at `core/src/ufo/tools/builtins.py:686`, and the panel reaches
  it by dispatching that tool), so what the lane adds is audit and ordering, not indirection.
- **A typed `handoff` field on `ToolResult`.** Rejected on evidence, as above: it bypasses the
  post-hook suppression two tests pin, and cannot carry `ask_user`'s input-model payload.
- **Make every tool member-invocable by default.** Rejected: most tools are the agent's own reach
  (bash, browse, file writes), and a member-facing control over them is a different product with a
  different threat model. Opt-in per callee is the safe default.
- **A UI schema language** (widget/order/group metadata). Rejected: no second consumer has
  demonstrated the need, and a speculative layout language is the generic machinery this repo bans
  until a real matrix demands it.

## Open decisions

1. **Does `ToolIntent.tool`'s `Literal` stay?** Keeping it means core re-validates the admitted row
   against a closed set, and a new action edits one core line. Cutting it means the registry is the
   only inventory, and an admitted row may name any registered tool — including ones no declaration
   exposes. This RFC keeps it; the opposite is arguable and the deciding evidence is whether any
   path can admit a `ToolIntent` that did not come through `submit_intent`.
2. **Where does the model-servability precheck belong?** It exists because `AgentSpec.model` is a
   bare `str` (`agents.py:61`) and an unservable value wedges later turns. Validating it in the
   callee — or typing the field against the registry — would delete a surface precheck and improve
   chat too, but it widens the RFC into the model-spec work of `0018`.
3. **Does the generated form need a declared enum source?** The model list reaches the Overview
   form as a separate payload field, not through any schema. Either declarations carry an options
   source, or runtime-valued selects stay hand-written. Unit 3 answers this against a real view.
4. **Label translation.** If a second surface ever renders these actions, `label` and `confirm`
   become strings needing per-surface or per-locale variance. Nothing here plans for that; it is
   recorded so the first surface that needs it does not discover the field is a dead end.

## Units

1. **Declaration + lane.** `MemberAction`/`ActionContext` in core and exported through `ufo.sdk`,
   boot validation at `validate_ext_tools`, `PanelIntent` collapsed to `{intent, input}`,
   `_tool_intent` deleted, every wave-2 mutation ported declaration-by-declaration. The portal keeps
   its hand-written controls and posts the new envelope, so the lane is proven before any markup
   moves. **Its true cost:** the wire body today *is* the
   inner intent (`PanelIntent.model_validate({"submitted": …})`, `panels.py:206`), so every existing
   submit body changes — roughly 33 in `test_ext_web.py`, 4 in `test_memory_correction.py`, 15 in
   `portal_smoke.mjs` plus its stub discriminator, and the portal's own 12 submit sites. The four
   cross-field refusal tests above cannot be ported and need their named replacements. This is not
   a unit that ships with existing tests untouched.
2. **Payload channel.** `final_act_model` on `ToolDef`; the five call sites stop naming tools and
   ask the registry instead, over the existing text parse, so the hook-suppression tests stay green
   unchanged because the parse does not move; `ask_user`, `connect_account`, and
   `request_credentials` all declare their model. `TerminalFrame`'s three fields stay, so this unit
   must also specify the payload-type → frame-field routing that replaces the three named locals,
   and `_final_act`'s signature once it is no longer generic on a caller-supplied `PayloadT`. That
   routing is the unit's real design work; the tool-name deletion is the easy half.
3. **Generated controls.** One generator rendering label → schema-derived form → confirm → submit →
   outcome, including the enum-source and description-as-hint work it does not inherit; the wave-2
   controls delete in favour of it, each keeping an envelope-level test; layout-bearing views stay
   hand-written by design.

Unit 2 is independent of 1 and 3 and could land first. Unit 3 depends on 1. The risk concentrated in
3 is testing shape: today's proof is a mutation-red walk over hand-written markup, and generated
markup is harder to mutate-test per string, so the generator gets that treatment once (label,
disabled state, confirm step, submit envelope, outcome render) and per-declaration tests assert the
composed callee input rather than the pixels. Getting that wrong would trade duplication for test
blindness.

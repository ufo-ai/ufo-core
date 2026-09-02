---
rfc: 0042
title: "Object-bound actions — progressive capability discovery through workspace objects"
status: proposed
date: 2026-08-27
---

# Object-bound actions — progressive capability discovery through workspace objects

> Keep the five generic workspace-object verbs, add one `object_action` dispatcher, and let an
> extension attach typed collection or instance actions to any registered object kind. A capability
> lives where its subject lives: `object_list` and `object_get` return the relevant actions only
> after the model chooses a domain. Twenty-six named tools become object actions while one new
> kind, `surface`, gives Slack and iMessage their existing installation object. The hosted pack's
> member-facing schema registry falls from 67 to 43 before runtime connector expansion, the lazy
> tool catalog is deleted once the inventory lands — object discovery becomes the one deferral
> mechanism — and authority, audit, idempotency, hooks, and final-act behavior do not change.

## Decision

| Question | Decision |
|---|---|
| What stays globally listed? | Cross-cutting execution, conversation control, research, connector discovery, and the six object verbs in [Retained global tools](#retained-global-tools). |
| What moves? | The 26 names in [Action inventory](#action-inventory) — a starting assignment: a family that fails its eval gate returns to the global set, updating the inventory tables in that change. |
| What happens to the lazy tool catalog? | Deleted in the final unit. Object discovery is the one deferral mechanism; retained global tools are always on the wire. |
| How are moved capabilities found? | Collection actions on `object_list(kind)`; instance actions on `object_get(ref)`. `object_explain(kind)` returns both sets. |
| How are they called? | One generic `object_action` tool with a structured target and an action-specific `input` mapping. |
| May an extension attach to another extension's or core's kind? | Yes. Kind ownership controls object reads; action ownership controls the handler and its `ExtensionContext`. |
| How many kinds are added? | One: `surface`. Every other action binds to an existing kind. |
| Are action references grants? | No. Invocation re-resolves the action, object, agent target, speaker, and authority. |
| Are actions dynamically hidden by role or state? | No. Discovery reports structural applicability. The handler remains the single authority and state gate. |
| Do agent allowlists grant the generic dispatcher? | No. They grant canonical action ids individually. The dispatcher appears when at least one action is granted. |
| Do connector/MCP tools change? | No. `list_external_tools`, `describe_external_tools`, `search_connector_tools`, `call_external_tool`, `list_mcp_tools`, and `call_mcp_tool` stay global; provider tool discovery stays as-is. |

This RFC withdraws RFC 0022. Its useful parts land here on the object action itself: one typed
declaration serves model discovery and prepared intents, and final-act metadata follows the
semantic action through the generic dispatcher. Its separate `MemberAction` declaration and
tool-or-kind dual attachment would create a second callable inventory.

## Evidence

The design follows established resource-action conventions, with one deliberate adaptation for a
model tool transport.

| Source | Established pattern | Consequence here |
|---|---|---|
| [OData actions and functions](https://learn.microsoft.com/en-us/odata/webapi-8/fundamentals/actions-functions) and [action routing](https://learn.microsoft.com/en-us/odata/webapi-8/fundamentals/action-routing) | Non-CRUD behavior binds to either an entity or a collection; side-effecting actions and side-effect-free functions retain distinct execution semantics. | UFO has instance and collection bindings. One dispatcher carries both reads and writes, while `side_effecting` remains an action property; a second `object_function` wire tool would add a distinction the model transport does not use. |
| [Google AIP-130](https://google.aip.dev/130) and [AIP-136](https://google.aip.dev/136) | Prefer standard methods; use custom methods only when CRUD is dishonest. Bind them to a resource or collection whenever possible. | `object_apply` remains the path for authored state. OAuth handoffs, rebuilds, searches, and state machines become actions rather than simulated fields. |
| [Google AIP-164](https://google.aip.dev/164) | A soft-deleted resource remains gettable; undelete is a method on that resource. | Archived agents stay readable through `object_get`; `restore_application` binds instance. |
| [Stripe PaymentIntent cancellation](https://docs.stripe.com/api/payment_intents/cancel) | An operation is object-local even when only some object states accept it; invocation returns the state refusal. | Discovery does not maintain a second `available` flag. An action may be listed while its handler refuses the current state. |
| [Siren](https://github.com/kevinswiber/siren#actions) | An entity representation can carry named actions and their input controls. Action names are unique within that entity's action set. | Object projections return action name, description, JSON Schema, and a pre-bound invocation template. Uniqueness is per kind, not deploy-wide short name. |
| [Kubernetes RBAC subresources](https://kubernetes.io/docs/reference/access-authn-authz/rbac/#referring-to-resources) | A resource operation has its own authorization identity; possessing general resource access does not imply every subresource verb. | Agent allowlists and hooks address each action by canonical id. Granting `object_action` alone grants nothing. |
| [OWASP API1:2023](https://owasp.org/API-Security/editions/2023/en/0xa1-broken-object-level-authorization/) | Every operation receiving an object id must perform object-level authorization on that request. | A discovered invocation template is not trusted. Dispatch resolves visibility and authority again under the live call. |
| [OpenAI tool search results](https://openai.com/index/introducing-gpt-5-4/#tool-use) | Deferring large tool definitions reduces repeated context cost; OpenAI reports 47% lower total tokens at equal accuracy on its cited 250-task MCP Atlas comparison. | UFO progressively reveals actions through the object vocabulary it already exposes. The external result motivates measurement, not an assumed UFO gain; this RFC defines UFO-specific evals below. |

The result is intentionally closer to a resource-oriented custom method than to a hidden command
palette. The model first names the part of the workspace it is reasoning about, receives the
capabilities local to that part, and invokes one through a stable generic transport.

## Current state

### The static inventory

`turn_tools()` in `core/src/ufo/host/ext/loader.py` combines:

1. 17 core `BUILTIN_TOOLS`;
2. every active extension `ToolDef` and connector-provider `ToolDef`;
3. five `ObjectVerbs` from `core/src/ufo/runtime/objects.py`.

`_agent_tools()` in `core/src/ufo/runtime/queue.py` then removes `profile_only` entries or intersects
that tuple with an agent allowlist. In `assistant_hosted`, the resulting member-facing static
registry is 67 schemas before any provider-specific connector tools: 17 core builtins + 45
extension tools + five object verbs. Seventeen additional raw browser and application-builder
primitives are already `profile_only` and are not part of that 67.

The lazy tool catalog (#2585) currently thins what that registry puts on the wire: above
`DIRECT_TOOL_LIMIT`, a request offers the eager names, the tools the transcript already used, and
a `tool_search` tool over the rest. It is a deferral stopgap with no domain locality — a flat
keyword search over the same global inventory — and running it beside object discovery would give
one member ask two discovery grammars. This RFC's final unit deletes it; until that unit lands it
keeps running unchanged, the counts here are registry counts, and the billed comparison is
measured against production with the catalog live.

Twenty-three names in the hosted set are object-local. `monitor` is the same shape in the separate
monitors extension, making 24 repository-wide. Moving the hosted 23 and adding `object_action`
changes the static member-facing registry from 67 to 45, a net reduction of 22 schemas. This count
does not claim a token saving: descriptions and JSON Schemas differ in size, a static schema rides
the cached prompt prefix while a discovered one is per-conversation transcript tokens, and a moved
action is still read when its object is explored. The measurement gate uses serialized bytes,
billed input, cache behavior, tool yields, and task accuracy.

### The object substrate

RFC 0017 already supplies the required discovery vocabulary:

- `ObjectKind` is a deploy-wide kind with a typed spec and handlers;
- `object_list("")` lists registered kinds;
- `object_list(kind)` lists instances;
- `object_get(ref)` returns spec, status, links, timestamps, and generation;
- `object_explain(kind)` returns guidance and schema;
- `object_apply` and `object_delete` are the generic mutations.

The registry is already extension-spanning and workspace-scoped. The gap is imperative behavior.
`request_credentials`, `slack_connect`, `skill_search`, and `deploy_website` are not honest CRUD,
but globally listing each one gives every model round their full schemas even when the member's task
has nothing to do with credentials, Slack, skills, or sites.

### Constraints a generic dispatcher must preserve

The current named `ToolDef` does more than expose a schema:

| Concern | Current consumer |
|---|---|
| Agent and subagent grants | `_agent_tools()` / `_subagent_tools()` select by tool name. |
| `requested_by` authority | The engine removes and verifies the message ref before input validation. |
| Parallel scheduling | `_dispatch_segments()` reads `parallel_safe` before dispatch. |
| Replay and deduplication | `side_effecting` determines the call idempotency key and replay behavior. |
| Trust wall | `untrusted` walls provider/member-authored output. |
| Hooks | `pre_tool_use`, `post_tool_use`, and `post_tool_use_failure` match and report the tool name. |
| Final acts | Ask, credential, connect, and provider handoffs are parsed from the final result text. |
| Activity and telemetry | Activity summaries, spans, logs, and meters attribute a named call. |
| Prepared intents | `ToolIntent` stores a closed, verbatim call and the speaker's turn is its audit record. |

An implementation that merely makes `object_action` call the old handler would flatten every row
of this table onto one name. It would widen allowlists, bypass targeted hooks, serialize reads with
writes, lose untrusted walls, weaken deduplication identity, and obscure audit data. Semantic
resolution is therefore an engine boundary, not handler glue.

## Proposal

### 1. One declaration, two bindings

An action is a `ToolDef` bound to a kind's collection or to one visible object instance.

```python
ActionBinding = Literal["collection", "instance"]


@dataclass(frozen=True)
class ObjectBinding:
    kind: str
    binding: ActionBinding
    name: str | None = None  # pins an instance action to one named row


@dataclass(frozen=True)
class ObjectActionTarget:
    kind: str
    name: str | None
    agent: ObjectAgent | None
    generation: UUID | None


@dataclass(frozen=True)
class ToolDef[InputT: BaseModel]:
    name: str
    description: str
    input_model: type[InputT]
    handler: Callable[[ToolContext, InputT], Awaitable[ToolResult]]
    bound: ObjectBinding | None = None
    untrusted: bool = False
    side_effecting: bool = False
    subagent_default: bool = False
    parallel_safe: bool = False
    profile_only: bool = False
    agent_targetable: bool = False
    final_act_model: type[BaseModel] | None = None
    presentation: ActionPresentation | None = None
```

There is one callable declaration. A `ToolDef` whose `bound` is `None` is a global tool; one
carrying an `ObjectBinding` is an object action and never enters the wire registry. Both flow
through the same collision gate, allowlist selection, hook matching, idempotency keying, and
telemetry — nothing normalizes a second declaration form into a first, because there is no second
form. `ask_user` and `connect_account` carry `final_act_model` on the same field an action does.
Requester and target resolution produce one `EffectiveCall` per wire call; hooks and execution
consume only that complete type.

The handler signature does not change. A bound handler reads its resolved `ObjectActionTarget`
from the `ToolContext`, exactly as a side-effecting handler already reads its per-call idempotency
key; a global call carries none. The target never enters `input_model`, so a member cannot smuggle
`kind`, `name`, `agent`, or `generation` through action input, and a portal does not need RFC
0022's `compose` callback to strip server-owned fields. The existing registry gate that refuses an
input model reserving `requested_by` covers actions for free. `agent_targetable` has three
declared consumers — `grant_web_access`, `revoke_web_access`, and `read_private_transcript` — and
dispatch resolves their target under the same gate the object verbs use for cross-agent reads. An
empty `agent` means the current turn's agent, which is these handlers' whole behavior today, so
cross-agent targeting is strictly additive.

Collection binding is used when an action creates or searches instances or spans the collection.
Instance binding is used only when one existing visible `kind/name` is the target. This is the
same division OData and AIP-136 make.

Actions remain imperative. An extension must use `object_apply` when the operation is faithfully an
authored desired state. An action is warranted for at least one of:

- an external handoff or state machine;
- an operation over derived state;
- a search whose semantics exceed object listing;
- generated output with a durable destination;
- a transition that cannot be represented as a spec replacement.

### 2. Contribution is separate from kind ownership

A bound `ToolDef` registers through the existing `tools` manifest point — one declaration, one
registration path — and the loader partitions on `bound`. It builds two bindings:

- `BoundKind`: kind owner plus the context its store reads under;
- `BoundAction`: contributing extension plus the context its handler runs under.

An action may name any kind registered by the complete deploy. The loader validates actions only
after collecting all manifests, so extension order cannot make a valid cross-extension attachment
fail. This is required for:

- web attaching `grant_web_access` to core's `member`;
- coding attaching `connect_github` to core's `credential`;
- metronome attaching `manage_billing` to core's `workspace`;
- Slack and iMessage attaching to core's `surface`;
- web attaching `read_private_transcript` to core's `conversation`;
- sites attaching `set_homepage` to core's `agent`;
- OpenRouter attaching generation to core's `artifact`;
- memory attaching `rebuild_page_facts` to sources' `page` — extension-to-extension, so the loader
  supports every direction, not only extension-to-core.

Object visibility is evaluated by the kind owner's store. The action then runs with the
contributor's `ExtensionContext`, preserving its own credential slots, store, blob namespace,
surface installation capability, and egress declarations. Neither extension receives the other's
context.

Boot fails on:

| Invalid declaration | Failure |
|---|---|
| Missing target kind | Names action, contributor, and missing kind. |
| Invalid kind or action name | Uses the existing kind/tool naming grammars. |
| Duplicate `(kind, action)` | Names both contributors; a short name never repeats within a kind, across either binding. |
| Input model admits extra fields, secrets, or non-JSON values | Uses the object-spec model gate over the action model. |
| Empty presentation label or confirmation | Refused when `presentation` is present. |
| Presentation on a `profile_only` action | Refused because prepared member intents never reach profile-only calls. |
| Final-act model not registered to a terminal-frame field | Refused at boot. |

There is no `admin_only`, `available`, `enabled`, or accepted-state declaration. Those would be
static claims beside the handler's live decision. The existing handler gate remains the one answer.

### 3. Canonical identity

Every action has a canonical identity:

```
action:<kind>:<name>
```

Examples:

```
action:member:add_member
action:member:grant_web_access
action:surface:slack_connect
```

The short `name` appears in object results and `object_action.input.action`. The canonical id is
used anywhere a deploy-wide identity is required:

- agent and subagent allowlists;
- hook selectors and hook events;
- idempotency keys;
- replay policy;
- activity input;
- telemetry and meters;
- boot collision reports.

An action short name may repeat across kinds, never within one: binding is a declared attribute,
not identity, so an action that migrates between collection and instance keeps its allowlists,
hook selectors, and dashboards, and no kind can carry one name on both bindings. A canonical
action id cannot collide with a global tool name because the `action:` prefix is reserved.

### 4. Discovery projections

`object_list` and `object_get` keep their existing object data and add actions at the envelope,
never copied into each row.

`object_list(kind)` returns collection actions:

```json
{
  "kind": "member",
  "actions": [
    {
      "name": "add_member",
      "description": "Add someone to this workspace by email.",
      "input_schema": {"type": "object", "properties": {}},
      "call": {
        "kind": "member",
        "action": "add_member",
        "input": {}
      }
    }
  ],
  "objects": []
}
```

`object_get(ref)` returns instance actions with the target pre-bound:

```yaml
ref: member/94f3b3d4-4f4a-4f7c-90d0-b4b0a6403db9
spec:
  admin: false
  seated: true
status:
  email: member@example.com
actions:
  - name: grant_web_access
    description: Give this member access to an agent in the web portal.
    input_schema:
      type: object
      properties: {}
    call:
      kind: member
      name: 94f3b3d4-4f4a-4f7c-90d0-b4b0a6403db9
      action: grant_web_access
      input: {}
```

If the detail has a generation, the invocation template carries it. If the object read was
agent-targeted, it carries that stable agent name, and the read publishes only the actions that
declare `agent_targetable`: dispatch refuses an agent target on any other action, and the same
template without the agent would resolve the calling agent's object rather than the one the read
returned. Action order is lexical by short name.

`object_explain(kind)` returns the kind's existing fields plus separate `collection_actions` and
`instance_actions`. `object_list("")` continues to list kinds only, adding at most
`has_actions: true`; it does not recreate a deploy-wide action catalog.

Discovery is structural, not personalized capability minting:

- a collection action appears when the caller can list the kind and the executing agent/profile is
  granted its canonical id;
- an instance action appears only after the kind's own `get` returned that object and the executing
  agent/profile is granted its canonical id, and an agent-targeted read publishes only its
  `agent_targetable` actions, because every other template it could publish is uninvocable;
- schemas contain no secret defaults or values;
- role and state do not remove an action;
- invocation repeats every gate.

Prepared-intent projections use the action's `presentation` declaration rather than the model
allowlist, preserving the existing rule that a member-submitted panel act bypasses an agent's model
tool set while retaining the profile-only wall.

An instance listing does not attach actions to every row. Doing so would multiply schema tokens,
invite N+1 state checks, and imply row-specific availability metadata the handler does not own.

### 5. The `object_action` wire tool

```python
class ObjectActionInput(BaseModel):
    kind: str
    action: str
    name: str = ""
    agent: str = ""
    generation: UUID | None = None
    input: dict[str, JsonValue] = Field(default_factory=dict)
```

Dispatch is fixed:

1. Look up `action` on `kind`; reject an unknown action with the actions registered for that kind.
2. Check arity against the action's declared binding: an instance action without `name`, or a
   collection action with one, is a loud pre-dispatch error — never a fallback to the other
   binding.
3. Enforce the executing agent's grant for the canonical action id.
4. Resolve `requested_by` exactly as a global tool call does.
5. Validate `input` with the action's `input_model`.
6. Resolve an `agent` target only when the action declares `agent_targetable`; apply the existing
   main-agent, live-speaker, subagent, visibility, and workspace checks.
7. For an instance action, call the kind owner's `get` and `status` under its context, rechecking
   visibility and existence; the live generation and any the call supplied ride the target.
8. Produce `EffectiveCall` with the contributor's context and semantic action id.
9. Run the ordinary hook, handler, result bound/offload, trust wall, final-act, activity, ledger,
   and replay paths.

A malformed target or action input is a pre-dispatch error and fires no hooks, matching a malformed
global tool call. A handler refusal fires `post_tool_use_failure` only when it returns or raises as
an ordinary dispatched failure, matching current behavior.

The invocation is structured rather than an opaque action token. Opaque tokens would need expiry,
replay, transcript-restoration, and cache semantics while providing no authority benefit: OWASP's
object-level rule still requires resolving the live object and caller on invocation.

### 6. Engine semantics survive the wire-name collapse

The engine resolves the one declaration before any behavior that reads it, then resolves the
call-specific `EffectiveCall` before hooks and handler execution.

| Engine behavior | Required action behavior |
|---|---|
| Schema exposure | The model sees only `object_action` globally; discovery returns the action schema. |
| Allowlists | A no-allowlist agent receives every non-`profile_only` action. An allowlisted agent receives only canonical ids it names, plus `subagent_default` actions under the existing rule. Naming `object_action` itself is invalid. |
| Dispatcher visibility | The `object_action` schema is included when the effective action set is non-empty. |
| Parallel segments | Resolve the action before `_dispatch_segments`; use its `parallel_safe`. Unknown or malformed calls remain barriers. |
| Side effects | Use the action's `side_effecting`; key as `<turn>/<canonical-action-id>/<call-id>`. |
| Replay | Use semantic action flags, never the outer wire name. |
| Extension context | Use the action contributor's context. Kind reads use the kind owner's context before normalization. |
| Hooks | Match canonical action id. Hook payloads carry `wire_tool="object_action"`, `call=<canonical id>`, typed action input, and target. A hook over every call remains unfiltered. |
| Untrusted output | Wall with the canonical action id when the action or `ToolResult` is untrusted. `slack_channels`, `imessage_connect`, and external generation preserve their current declarations. |
| Activity | Summarize the semantic action, kind, target summary, and bounded action input; do not ask the activity model to infer work from `object_action`. |
| Telemetry | Keep `tool=object_action` as wire transport and add `call=<canonical id>`, `kind`, `binding`, and contributor. Semantic dashboards group on `call`. |
| Final acts | Read `final_act_model` from `EffectiveCall` and keep parsing the post-hook result text, preserving hook suppression. |
| Images and offload | Unchanged after semantic resolution. |

This design rejects a dispatcher implemented as an ordinary handler inside the existing engine.
Resolution must precede segmentation, requester policy, hook selection, and idempotency-key
construction; those consumers sit outside a handler today.

### 7. Authority and safety

An action reference says what can be attempted, not who may succeed. Invocation re-establishes:

| Boundary | Check |
|---|---|
| Tenancy | Workspace context and RLS bind both kind resolution and action execution. |
| Agent capability | Canonical action id is present in the effective action registry for that agent/profile. |
| Human authority | `requested_by` resolves a live active message; prepared intents bind the submitting member. |
| Object visibility | Instance target resolves through the kind owner's store under the acting member and audience. |
| Cross-agent target | Existing main-agent/live-speaker/no-subagent/visibility gate. |
| Domain authority | The existing action handler checks speaking member, admin role, owner, current state, and provider conditions. |
| External wire | Contributor manifest and grants still determine egress and credentials. |

The dispatcher does not precompute admin availability. For example, every member who can read a
credential slot may discover `request_credentials`; a non-admin invocation reaches the existing
admin refusal. This preserves RFC 0017's rule that domain refusal lives in executable behavior and
avoids an authorization result cached in a transcript.

Instance actions re-read the target before execution for visibility and existence. A supplied
generation is not refused ahead of the handler: dispatch passes both it and the live generation on
`ObjectActionTarget`, and an action that mutates the target fences its own write atomically — the
only place a fence is correct, because a dispatch interrupted after its write and resumed by the
durable step would otherwise re-read the row at its new generation and refuse the very dedup its
idempotency key exists for. Actions against associated state still perform their own atomic
existence/state check, as `set_homepage` and Stripe-backed billing do now.

The object change journal remains CRUD-only. An action is already audited by its turn, semantic
call id, target, input, and result; inventing a generic before/after spec for an imperative action
would be false. Domain stores keep any audit rows they already own.

### 8. Prepared intents and portal controls

`ActionPresentation` is an optional consumer declaration on any callable:

```python
@dataclass(frozen=True)
class ActionPresentation:
    label: str
    confirm: str | None = None
    frame: bool = False
```

Absence means chat/model only. Presence admits the action through the prepared-intent lane and lets
a portal render a schema-derived control. The fence is enforced at the one dispatch point: the
intent path refuses a dispatcher intent whose resolved action declares no presentation, so no
route can widen the lane the closed set used to curate. It is not an authority grant: the turn
carries the submitting member and the handler decides. A presented action has a member-shaped
input by construction — a model-facing input (a class, a confidence) is never presented; the
member's act is its own declaration writing what the model's tool writes (the memory view's
`record_correction` beside the model's `memory_update`).

The route binds the target. A presented action is posted to
`POST agents/{agent_id}/actions/{kind}/{action}` (collection) or
`POST agents/{agent_id}/actions/{kind}/{name}/{action}` (instance): the body is the action's
`input_model` alone and a body naming an envelope field is refused before a turn exists; the route
writes `kind`, `name`, and `action` from its path and the agent from its lane into one
`ToolIntent(tool="object_action", input=...)` and admits it verbatim. `ToolIntent`'s closed set
therefore needs one action dispatcher entry rather than every moved tool name. A caller cannot
submit a different agent or target.

The same serializable `ActionView` feeds model object results and portal projections. Model results
include description and call template; portal views additionally include label, confirmation, and
frame admission. A portal projects an instance action's view from the declaration beside the target
its route holds (`GET actions/{kind}/{name}`) — a member row read is optional enrichment, never the
gate, so a target the portal offers no row for (an archived app, the workspace, another member's
private conversation) still draws its controls, and dispatch's instance recheck and the handler
stay the authority. An instance action pinned to one row (`ObjectBinding.name`) projects on that
row alone and dispatch refuses any other target.

An embedded app page speaks with the viewer's whole session, so it posts only callables whose
presentation says `frame`: the portal shell marks every forwarded call and the lane checks the
declaration. The set is `connect_account`, `rebuild_report_digest`, and `rebuild_page_facts`. Every
portal act rides one of the two lanes — the first run's iMessage offer posts `imessage_connect` on
the action lane like any other presented action; no hand-written envelope stands beside them. No
layout, widget, grouping, order, or CSS metadata enters core.

The credential and connect final acts retain their typed `TerminalFrame` fields. The effective
action declaration supplies the final-act model; a mapping beside `TerminalFrame` binds each model
to its frame field and its parse rule — a question reads from the round's last call, a pending
handoff from anywhere in the round and persists — so the declaration replaces the engine's name
constants without flattening that distinction. Parsing remains after `post_tool_use` so a hook
that replaces the result also suppresses the handoff. Global `ask_user` and `connect_account` use
the same final-act declaration on their global call definitions. One admission carve-out re-keys:
a spent workspace admits exactly the billing intent today by tool name, and afterwards by the
dispatcher intent whose input names the billing action.

Because the declaration is one shape, `presentation` is binding-agnostic: a retained global tool
with a portal control declares it on the same field, the route prepares `ToolIntent(tool=<name>,
...)` directly, and the moved hand-written action maps go with it. One declaration feeds chat,
model discovery, and every portal control; a second member-action registry never exists.

### 9. Sandbox bridge

The bridge adds `object_action` to its tool set (today the five object verbs plus the four
connector-gateway tools). It never lists bound actions as top-level tools. A sandbox client
follows the same flow:

1. describe/call `object_list`, `object_get`, or `object_explain`;
2. read the action schema and invocation template;
3. call `object_action`.

The bridge's own name gate treats `object_action` as available exactly when the executing agent's
effective action set is non-empty — the dispatcher can never appear in an allowlist, so a literal
name check would lock every allowlisted agent and subagent profile out of actions entirely.
Per-action enforcement stays in the child turn's granted set: a speakerless bridge intent remains
inside the executing agent's canonical action allowlist, and an admin or granting action still
refuses without a live speaker. `ufo tool --describe` therefore does not become an authority
bypass or a hidden global action catalog.

## The `surface` kind

There is no `surface` object kind today. `SurfaceSpec` registrations and
`surface_installation` rows already form the object; the missing piece is a read projection.

Core registers one read-only `surface` kind because no extension can see the complete manifest set
or own the shared `surface_installation` routing table. It adds no table and accepts no apply or
delete.

| Projection | Value |
|---|---|
| Address | `surface/<SurfaceSpec.name>`; one row for every registered surface, installed or not. |
| Spec | Declaring extension, addressed vs installation-routed, live vs durable delivery, and whether it is the home surface. |
| Status | Whether an installation row is bound, its agent when bound, and whether it routes ingress. No provider secret, external installation id, or bearer. Provider-specific readiness remains an action result. |
| Summary | Factual bound/not-bound installation state and bound agent. |
| Visibility | Internal workspace conversations and admin portal reads; foreign audiences receive no rows. |
| Mutations | `object_apply` and `object_delete` refuse. Setup lives in contributed actions. |

Slack contributes three instance actions to `surface/slack`; iMessage contributes one to
`surface/imessage`. The kind does not import either extension and does not know their action names.
Web access stays on `member` because it grants one person access to one agent, not configures the
web transport. Transcript acknowledgement stays on `conversation` because it opens one transcript.

This is the only new kind.

## Action inventory

Action names remain unchanged. Their tuned descriptions and input schemas move with them, which
minimizes prompt-semantic change and makes activity/audit searches recognizable. Binding supplies
the locality; renaming is not needed to achieve it.

| Object anchor | Binding | Actions removed from the global listing | Why this anchor |
|---|---|---|---|
| `member` | collection | `add_member` | Creates a member; role/seat updates already use the member kind. |
| `member/<id>` | instance | `grant_web_access`, `revoke_web_access` | The grant subject is the member. The action may target a visible agent through the existing cross-agent gate. The member kind's list/get widen to a speaking admin on any internal audience (today the whole roster shows only on the main agent) — the grant means something only on child agents, so discovery and the instance recheck must work exactly there. |
| `agent/<archived-id>` | instance | `restore_application` | An archived agent becomes a gettable row (AIP-164), addressed by its durable archived row name with the original exposed as a field; restore is its undelete, and its input shrinks to the new live name — the target supplies the identity `app_id` carries today. |
| `credential` | collection | `request_credentials` | One handoff may request several slots. |
| `credential/github_app_installation` | instance | `connect_github` | The action authorizes the exact non-member-filled slot the callback fills. |
| `conversation/<id>` | instance | `read_private_transcript` | The conversation kind widens for a speaking admin on internal audiences only — never a foreign audience: get serves another member's private row as metadata (no status, no transcript read), and list accepts an explicit private filter. The transcript itself stays behind acknowledgement, which is this action, and its handler records the access. |
| `skill` | collection | `skill_search` | Searches the complete loadable catalog, beyond CRUD listing of member-authored skill objects. The declaration moves into the kind's owner (skill_create) — core cannot bind to an optional extension's kind without failing deploys that lack it — and the allowlist pairing that grants search alongside `load_skill` re-expresses over the canonical id. |
| `surface/slack` | instance | `slack_connect`, `slack_app_manifest`, `slack_channels` | Install state, manifest, credentials, and remote channels belong to the Slack surface. |
| `surface/imessage` | instance | `imessage_connect` | Provider binding and phone proof configure the iMessage surface. |
| `workspace/<workspace-id>` | instance | `manage_billing` | Balance, payment method, and auto-refill are workspace-wide. |
| `page` | collection | `rebuild_page_facts` | Marks the workspace's page-derived facts due as one collection operation. |
| `report` | collection | `rebuild_report_digest` | Rebuilds derived entries over the current report window. |
| `memory` | collection | `record_correction`, `record_first_run` | The portal's correction and first-run memory: presented actions with member-shaped inputs, writing what the model's global `memory_update` writes. `memory_search` and `memory_update` stay global tools on every round. |
| `monitor` | collection | `monitor` | Arms a monitor after probing; the existing monitor kind lists and deletes armed rows. |
| `site` | collection | `deploy_website`, `publish_website`, `build_website`, `build_ufo_application`, `design_ufo_application` | These create, publish, build, or design site/application output rather than update an existing site spec. |
| `agent/<name>` | instance | `set_homepage` | The homepage is the agent's property; the site is plain input. The speakerless seed turn targets its own agent row, and the handler's deployed-this-turn carve-out is unchanged. |
| `artifact` | collection | `generate_image`, `generate_video` | Generation writes media into the workspace for `share_file` to turn into an artifact; the artifact collection is the durable destination without inventing a media-job kind. |

Count: 26 named tools, 17 anchor/binding rows, 12 existing kinds plus the one new `surface` kind.
The hosted pack contains 25 because it does not activate the monitors extension.

### Authority-sensitive actions

Object placement does not change the current gates:

| Action | Current gate retained |
|---|---|
| `add_member` | Speaking admin on the main agent, internal conversation. |
| `restore_application` | Speaking owner or admin. |
| `request_credentials` | Speaking admin; only declared member-fillable slots. |
| `grant_web_access`, `revoke_web_access` | Speaking admin; member exists; target-agent rules. |
| `read_private_transcript` | Speaking admin; named private conversation belongs to the selected agent; rooms and foreign conversations remain closed. |
| `connect_github` | Speaking admin and configured GitHub App. |
| `slack_connect` | Admin for link minting/identity derivation; status remains readable. |
| `imessage_connect` | Admin establishes provider binding; each member proves their own phone. |
| `manage_billing` | Speaking admin. |
| `rebuild_page_facts`, `rebuild_report_digest` | Speaking admin. |

Every speakerless scheduled fire, subagent, monitor arrival, and sandbox bridge call continues to
fail a granting or admin action even if its agent allowlist contains the canonical action id.

## Retained global tools

These 43 schemas remain in the hosted member-facing set after `object_action` lands and the lazy
tool catalog is deleted. Connector/MCP provider expansion remains separate.

| Domain | Global tools | Reason |
|---|---|---|
| Object substrate | `object_list`, `object_get`, `object_explain`, `object_apply`, `object_delete`, `object_action` | The uniform discovery and dispatch vocabulary. |
| Workspace execution | `bash`, `read`, `write`, `edit`, `glob`, `grep` | High-frequency sandbox primitives over paths, not durable workspace objects. |
| Conversation control | `ask_user`, `pause_and_wait` | Final acts whose target is the current interaction, not discoverable durable state. |
| Delegation | `spawn` | High-frequency computation primitive; the target catalog is already in the prompt/registry. |
| Conversation work | `cancel_spawn`, `message_spawn`, `update_todo_list`, `update_todo_status`, `plan_objective`, `run_independent_steps`, `record_step`, `read_objective`, `report_problem` | Their target is the executing turn's own work — the current-interaction criterion that keeps `ask_user` and `pause_and_wait` global — and the spawn lifecycle stays on one transport. The conversation collection would anchor them to rows they never touch. |
| Skills | `load_skill` | Executes a known skill. Tail discovery moves to `skill_search` on the skill collection. |
| Artifacts | `share_file` | Delivers an existing workspace path; it is the bridge from sandbox bytes to a durable artifact. |
| Ephemeral site runtime | `website`, `start_server` | Starts or inspects the current sandbox server; durable site lifecycle moves to site actions. |
| Research | `search_web`, `fetch_url`, `search_vertical`, `wide_research` | Stateless, high-frequency information access without a workspace-object target. |
| Computation | `js_repl`, `xlsx_repl` | Stateful scratch execution scoped to the current conversation workspace. |
| Browser delegation | `browser_task`, `wide_browse` | High-frequency delegation to a profile, not durable browser configuration. |
| Account connection | `connect_account` | Common provider-agnostic chat entry point whose provider and sharing scope are the input; connected results remain `connection` objects. |
| Connector discovery/call | `list_external_tools`, `describe_external_tools`, `search_connector_tools`, `call_external_tool` | Existing progressive discovery over external provider catalogs; unchanged by this RFC. |
| MCP discovery/call | `list_mcp_tools`, `call_mcp_tool` | Existing progressive discovery over configured MCP servers; unchanged by this RFC. |

A global tool satisfies at least one of:

- it is a frequent cross-domain primitive;
- its target is a path, transient computation, current interaction, or provider not known before
  the call;
- it is already a progressive-discovery gateway over a larger remote inventory.

“Could be attached somewhere” is insufficient. Putting `bash` on `workspace`, `ask_user` on
`conversation`, or every connector gateway on `connection` would hide ambient primitives behind an
object ceremony without improving conceptual locality.

## Prompt and model behavior

Moved descriptions are not deleted; they become action descriptions and are returned only with
their anchor. Prompt sections and skills stop instructing the model to call a moved name directly
without discovery context. They teach one rule only where evals prove it necessary:

> Inspect the relevant object kind for actions when CRUD does not express the requested operation.

That wording is illustrative, not approved prompt text. Any prompt or skill edit follows the
repository's ablation gate. The action schema itself is trusted first-party model context, while
action results retain their current trust classification.

Direct invocation remains valid once the model knows an action from the current transcript. The
system does not require a fresh list/get call as a security ritual. Progressive discovery is a
context-loading mechanism; forcing repeated reads would add latency and still provide no authority.

## Evaluation and proof

### Static gates

| Proof | Assertion |
|---|---|
| Hosted inventory | The static member-facing registry is exactly the names in [Retained global tools](#retained-global-tools) — a names gate, not a count. |
| Candidate placement | All 23 hosted candidates and `monitor` register as actions; none enters model schemas as a named tool. |
| Catalog removal | `tool_search`, `EAGER_TOOL_NAMES`, and `DIRECT_TOOL_LIMIT` are gone with the final unit; the source-shape grep finds no remnant. |
| Collisions | Duplicate action tuple, missing kind, reserved canonical prefix, and global/action identity errors fail boot. |
| SDK boundary | Extensions register actions through `ufo.sdk`; extension imports of core internals remain gated. |
| Source shape | No candidate has a global `ToolDef` registration. References in action values, tests, descriptions, audit records, and member copy remain legitimate. |

### Dispatch integration

The sample extension is the seam probe. It contributes:

- a collection action to a core kind it does not own;
- an instance action to its own kind;
- one side-effecting action with an idempotency key;
- one untrusted read action;
- one profile-only action;
- one hook-targeted action.

Tests exercise realistic input through model dispatch and prepared intent to durable state, then
read the result through public object/surface projections. They prove:

1. list/get/explain return the correct action views;
2. kind-owner visibility runs before contributor code;
3. contributor context reaches only the contributor's capabilities;
4. allowlists grant individual action ids and never the dispatcher wholesale;
5. `requested_by`, speakerless refusal, admin gates, and cross-agent targets hold, and an
   interrupted self-mutating instance action resumes to its handler's fence, not a pre-dispatch
   refusal;
6. side-effecting replay receives one stable semantic key;
7. parallel-safe actions segment correctly;
8. pre/post/failure hooks match canonical ids and retain modified-input/output semantics;
9. untrusted output is walled under the semantic action identity;
10. final-act payloads survive the dispatcher and remain suppressible by output hooks;
11. activity, spans, logs, meters, and stored intent input name the semantic action and target;
12. bridge calls obey the same action allowlist and speaker rules.

Each moved action keeps its focused handler tests and gains one registry/dispatch test. Each family
gets an end-to-end proof at its real dependency boundary: Slack/iMessage provider flow, credential
handoff, memory index, site store, monitor probe, or transcript access.

### Model evals

**Suites move with their tools.** `required_tools_scorer` and its siblings grade `call.name` from
the transcript, and existing suites assert moved names (`member_add_notify` requires `add_member`,
`credential_handoff` requires `request_credentials`,
`app_home_change`, `new_application`, and five more). Each family unit updates those suites'
required trajectories to the dispatched form in the same change — a case keeps asserting the same
member outcome, expressed in each arm's own vocabulary, and the paired verdict stays per-case pass
counts. No mapping layer enters the harness.

**Sourcing.** Cases are real queries, known failures, and neighbor confusion — enumerated, never
invented to fit:

- A positive brief is the founding member ask of a production turn that called the moved tool,
  scrubbed of workspace identifiers. An authored brief is admitted only for an action with no
  production invocation yet, and is marked authored. Production-sourced wording is the leak gate:
  a paraphrase of the action description is not a case, and no brief contains the action name.
- Known failures — production or nightly turns where the model chose a neighboring tool — enter as
  claimed cases.
- Each action's confusion set is derived from the inventory, not curated: `object_apply` on its
  kind, its sibling actions, and its nearest retained global.
- Call frequency from the same production records weights the aggregate cost and latency claims;
  per-action accuracy gates stay unweighted.
- Family arms extend the existing suites and run whole under the ablation gate; no parallel matrix
  is authored.

**Coverage is per action, not per family.** Every moved action has a cold case (no prior discovery
in the transcript), a warm case (the action known from the transcript and invoked directly), and
one claimed neighbor negative from its derived confusion set. Each family adds one task spanning
two anchors. Three cross-cutting arms: a subagent arm under a canonical-id allowlist; a
post-compaction case where an action discovered before compaction is used after it; and the
catalog-deletion arm gating unit 7 — the final static wire against the catalog-live control on the
whole matrix, since deleting the catalog changes every request's schema bytes.

Family suites at minimum:

| Suite | Extends | Positive cases | Neighbor confusion |
|---|---|---|---|
| Membership/access | `member_add_notify` | invite, seat change, web grant, transcript acknowledgement | `object_apply member` vs `add_member`; member grant vs surface setup |
| Credentials/surfaces | `credential_handoff`, `connector_connections`, `slack_*` | fill slot, GitHub App, Slack OAuth/manifest/status/channels, iMessage phone | `connect_account` vs credential/surface action |
| Memory/skills/monitor | `tool_calling`, `skill_routing` | durable recall, explicit record, tail skill search, monitor arm | `object_list` lexical query vs semantic search; `load_skill` vs search; `pause_and_wait` vs monitor |
| Derived rebuilds | `report_digest` | page facts and report digest | one-row correction vs collection rebuild |
| Sites/media | `site_build`, `new_application`, `app_home_change` | build, preview, deploy, publish, homepage, image/video | ephemeral `website`/`start_server` vs durable site action; `share_file` vs generation |

Acceptance requires:

- no suite-level or claimed-case regression under the existing paired verdict gates;
- every moved action passes its cold, warm, and neighbor cases;
- negative cases do not invoke a neighboring action more often than control;
- all prompt/skill wording retained only when an ablation proves it load-bearing.

### Cost and latency measurements

For the same eval samples, record:

- serialized tool-schema bytes and provider-billed input tokens on every model request;
- cache-read/write tokens and cache hit rate;
- model tool yields and calls;
- time to first correct semantic call;
- total task cost and wall time;
- accuracy.

The registry gate is 67 → 45, and the count proves nothing by itself: a static schema is cached
prompt prefix while a discovered action is per-conversation transcript tokens, so the comparison
lives in billed and cache tokens against the control — current production, lazy tool catalog live.
A performance claim ships only from those measurements. A moved action may add at most one
discovery yield on a cold task; tasks already reading the target object should add none. A family
that cannot hold accuracy or bounded discovery latency stays global until its failure has a
different, measured design.

## Doctrine fit / implications

### Core

Core owns only what extensions cannot express:

- the deploy-wide kind/action registry and collision gate;
- the generic `object_action` wire schema;
- per-call resolution into `EffectiveCall` before engine policy;
- action projections on generic object reads;
- the `surface` projection over core-owned manifest and installation state.

Core does not own Slack, iMessage, billing, memory, sites, media, or portal-specific action logic.
Their extensions own the declarations and handlers.

### One shape

- CRUD is five object verbs; every callable is one `ToolDef`, bound or global; imperative object
  behavior is that declaration bound to a kind, dispatched through one wire tool.
- One discovery mechanism: object reads. The lazy tool catalog goes with the final unit.
- Model and portal consume one serializable action view.
- Capability identity is canonical once and threaded through every engine consumer.
- Authority lives in executable gates, not discovery metadata.
- No named-tool alias remains beside a moved action.

### Both ends

An action declaration lands only with:

- a discovery producer;
- a dispatcher consumer;
- its handler;
- permission, allowlist, hook, telemetry, and replay wiring;
- a focused test.

A moved named tool is removed in that same unit. An action presentation lands only with its portal
control and prepared-intent consumer.

### Failure domains

Action resolution and validation fail before the handler. Handler failure remains one terminal tool
result inside the turn's existing try/commit boundary. No retry, timeout, or fallback is introduced
for internal dispatch. Provider uncertainty retains only the retry policy already local to that
external call.

## Alternatives

### Keep every named tool global

Simple and strongly typed on the model wire. Rejected because schema cost grows with every
extension and every round, conceptual locality is absent, and the object registry already provides
the progressive namespace. The control arm remains necessary to prove the new path.

### Keep the lazy tool catalog beside object actions

The catalog (#2585) already defers non-eager schemas behind a flat `tool_search`. Rejected as the
end state: it is a keyword search over the same global inventory, with no domain locality, no
typed identity, and no prepared-intent story, and running it beside object discovery gives one
member ask two discovery grammars. It keeps production cheap until the inventory lands; the final
unit deletes it.

### Add one kind per tool family

Seven candidate kinds were `web_access`, `billing`, `todo`, `objective`, `spawn`, `media`, and
`surface`. Rejected except `surface`: `web_access` and `billing` already have their durable
subject in `member` and `workspace`; `todo`, `objective`, and `spawn` have no durable subject at
all — their tools stay global — and a kind for them would create fake objects; media's durable
subject is the `artifact` collection. `surface` alone has real manifest and installation identity
with no object projection.

### Put setup actions on `extension`

Rejected. `extension` is packaging and readiness; Slack/iMessage are transports, GitHub setup fills
a credential, and billing belongs to the workspace. An extension object would be a miscellaneous
drawer rather than the acted-on domain.

### Put every action on `workspace`

Rejected. It minimizes kinds but destroys locality, recreating the global command list one level
down.

### Encode actions as `object_apply`

Rejected. An OAuth handoff, remote channel search, rebuild, or monitor arm is
not a desired spec replacement. A fake spec would persist commands, introduce ambiguous replay,
and violate AIP-136's standard-method-first rule by contorting CRUD.

### Inject named tool schemas dynamically after discovery

This preserves per-action function-call validation. Rejected because a response would mutate the
next model request's tool registry, cache key, agent allowlist, replay state, and transcript
restoration. The generic dispatcher keeps a stable wire registry and validates action input
server-side against the same JSON Schema the model just read. Evals decide whether that weaker wire
typing is acceptable.

### Make `object_action` the allowlist grant

Rejected. It would turn every future action into an automatic grant for every agent already holding
the dispatcher, the wildcard risk Kubernetes documents for resource verbs. Canonical action ids
are individually granted.

### Return state- or role-filtered actions

Rejected. It requires an availability callback that duplicates handler logic, races with live
state, and turns a cached transcript into an apparent capability token. Structural discovery plus
live refusal has one answer.

### Add `object_function`

OData distinguishes side-effect-free functions from actions. Rejected for the wire surface: UFO
already carries `side_effecting` and `parallel_safe` as execution semantics, while a second generic
tool would make the model choose transport based on an implementation property. One dispatcher can
preserve the distinction internally.

### Use opaque invocation tokens

Rejected. Tokens need lifetime, replay, persistence, and signing rules and still cannot replace
live object authorization. Structured target fields are inspectable in audit records and stable
across a turn replay.

### Keep RFC 0022's separate `MemberAction`

Rejected. It declares member-callability beside model-callability, needs a `compose` function to
remove server-owned fields, and lets a kind and a tool both claim an action. `ActionPresentation`
is optional metadata on the one executable declaration, and a bound handler's input already
excludes the target.

## Implementation units

Each unit is independently reviewable and removes every global registration it replaces. A family
unit also prunes its names from the live catalog's eager set (unit 1 adds `object_action` there so
dispatch needs no search round while the catalog survives), ships a migration rewriting stored
`agent.tools` allowlist entries to canonical ids, and corrects a moved tool's mis-declared
execution flags deliberately — `deploy_website`, `publish_website`, `set_homepage`, and
`slack_connect` gain `side_effecting` (each a durable write); `memory_search`, retained on the
wire, gains `parallel_safe`; `request_credentials` and `connect_github` deliberately keep `side_effecting`
false, since a handoff seal is not an external write and the flip would only forfeit guidance
preemption — each named in its unit.

A `ToolIntent` is persisted in the turn it admits and re-parsed at execution, and a parked intent
can outlive a deploy. No compatibility machinery bridges that: a family unit removes its moved
names from the `ToolIntent` closed set outright, and a persisted intent naming one fails loudly at
execution — the member repeats the act on the new panel. An in-flight turn whose un-executed
dispatch names a moved wire tool likewise recovers on the new image as one visible invalid-call
refusal; the turn terminates and the model retries through discovery.

1. **Registry and dispatch.** `ObjectBinding` on the one declaration, boot validation, canonical
   ids, `EffectiveCall`, `object_action`, object projections, SDK export, bridge exposure, and
   sample-extension proof. No production action moves in this unit.
2. **Surface object.** Core `surface` read projection; Slack and iMessage actions; their four named
   tool registrations removed; provider integration tests and portal first-run intents updated.
3. **Authority actions.** Member add/web grants, agent restore over gettable archived rows,
   credential request/GitHub install, workspace billing, transcript acknowledgement as a
   conversation instance action, and rebuild actions. Prepared intents move with each action;
   admin and speaker gates remain handler tests.
4. **Knowledge actions.** Skill search, memory search/update, and monitor arm. Retrieval and monitor
   evals prove the discovery round and neighboring-tool choice.
5. **Site and media actions.** Build, preview, deploy, publish, homepage on the agent, and
   image/video generation; application-builder profile grants and hooks use canonical action ids.
6. **Portal generation.** `ActionPresentation`, schema-derived controls, generic prepared-intent
   envelope, and removal of the moved hand-written action maps. Retained global tools with portal
   controls declare `presentation` on their own definitions.
7. **Inventory gate, catalog removal, and docs.** The retained global names are enforced; the lazy
   tool catalog is deleted behind the catalog-deletion arm — the final static wire paired against
   the catalog-live control on the whole matrix; `spec.md`, permission docs, skills, and operator
   documentation describe the one action path.

Unit 1 changes no model prompt. Every later prompt or skill wording change carries its own ablation.
The RFC itself is documentation and requires no model-text eval.

## Open decisions

None. The implementation may change private class factoring, and the
[Action inventory](#action-inventory) is a starting assignment, not a promise: a family that fails
its eval gate returns to the global set by the rule above, updating the inventory tables in that
change. The bindings, one new kind, canonical identity, discovery shapes, authority rules, and
proof gates are fixed by this proposal.

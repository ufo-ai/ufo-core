---
rfc: 0044
title: "Notification app: a back channel from any agent turn to one member"
status: proposed
date: 2026-09-03
---

# Notification app: a back channel from any agent turn to one member

> An agent has an inbox: rows keyed by the agent they are for and the member they concern, folded
> by subject the way a source folds pages by identity, drained by the clock into one turn on that
> agent's own lane the way a source trigger's `current` delivery wakes a conversation. Any agent
> turn can put a message in it. The first and only reader is the Notification app, an
> extension-shipped agent that decides what a person should be interrupted for and is the only
> agent holding the verb that reaches a member outside the conversation they are in. The producer
> is a metered tool today and can grow a zero-round `<notify>` span later on the same store. Core
> gains one read and nothing else. Radar is untouched.

## Current state

| Fact | Where |
|---|---|
| A turn already speaks through hidden markup: `<reply-to message=…>` spans are parsed out of a round and redacted from the live stream. It is the only tag grammar and the only stream filter. | `core/src/ufo/harness/replies.py`, `core/src/ufo/runtime/engine.py` (`ReplyRedaction` as the `TextFilter`) |
| An observe-only `stop` hook already fires with the closing answer, right before the terminal commits. It can fire twice in one turn: when arrivals recycle the answer the loop `continue`s and fires again. | `core/src/ufo/runtime/ext/manifest.py` (`Stop`), `core/src/ufo/runtime/engine.py:1666` |
| An extension can open a stable member-private conversation of an agent and admit an internal turn into it. This is how `source_trigger per_page` runs. | `ExtensionContext.open_conversation`, `ExtensionContext.invoke` in `core/src/ufo/runtime/ext/context.py` |
| Admission registers a `writeback` for any turn entering a durable-surface conversation, whoever admitted it. Slack posts a turn that answers no member message at the DM top level. | `core/src/ufo/runtime/surfaces/admission.py`, `extensions/slack/ufo_ext_slack/surface.py:24` |
| No turn-less delivery exists: `writeback` and `mid_turn_reply` are keyed on a turn; `open_conversation` hardcodes `surface = <extension>`; no `member_id → DM channel` lookup exists on any surface. | `core/src/ufo/schema/tables.py`, `core/src/ufo/runtime/ext/context.py` |
| A `profile_only` tool reaches only a provision or profile whose allowlist names it; `object_apply agent` writes no allowlist. | spec.md `tools` point, `core/src/ufo/runtime/queue.py:_agent_tools` |
| `report_problem` is the in-tree model of "a report written in another voice for a reader who is not the member". It ships without `profile_only`, so every agent holds it. | `extensions/debugger/ufo_ext_debugger/report.py` |
| A source folds by identity: a page has a provider identity and a body digest, and a new body on the same identity advances the revision instead of adding a row. A source trigger's `current` delivery invokes one turn per batch. Only a shared source carries a trigger, and `per_page` is one turn per page. | spec.md `source`, `source_trigger`; `extensions/sources/ufo_ext_sources/tools.py:_fire_trigger` |
| Memory recall is injected into every turn on a `user_prompt_submit` hook, scoped to the conversation's audience subjects. A member-private lane recalls that member's own items. | spec.md Memory, `extensions/memory/ufo_ext_memory/manifest.py` |
| The gap: a background turn (source trigger, scheduled fire, monitor, subagent) that learns something a person should hear has no way to say so except in its own conversation, which nobody may be reading. |

## Proposal

### One shape

```
producer turn ──notify(subject, body)──▶ inbox row (for one agent, about one member, folded by subject)
                                              │  per-minute drain, one turn per (agent, member) lane
                                              ▼
                              Notification agent, member's own lane (surface app_notification)
                                              │  action:notification:deliver  (profile_only)
                                              ▼
                        invoke into the member's newest durable conversation (Slack DM, iMessage)
                                              │  admission registers the writeback
                                              ▼
                              the member reads it in the thread they already use, and replies there
```

| Layer | Owns |
|---|---|
| harness | nothing |
| runtime (core) | `ExtensionContext.member_reach(member_id)`, a read projection |
| host | passes the deploy's `durable_surfaces(manifests)` into the extension context |
| `extensions/app_notification` | one table, its migration, the `notify` tool, the `notification` kind, the `deliver` action, the drain job, the `notification` agent provision, its prompt, the evals |

### Data model

One table, extension-owned, own migration, `workspace_id` on every row. Borrowed from sources: a row
is keyed by the agent it is for, the way a page is keyed by the source that holds it, and folded by
`subject` the way a page is folded by identity. `subject` is the identity, `body` the latest
revision, `occurrences` the revision count. Today every row is for the Notification agent and
`notify` takes no recipient; the shape is general so a second reader is one tool argument and a
column default, not a migration.

```python
notification = sa.Table(
    "notification", _metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("to_agent_id", sa.Uuid, nullable=False),      # the inbox this row is in
    sa.Column("member_id", sa.Uuid, nullable=False),        # the member it concerns; the lane
    sa.Column("subject", sa.Text, nullable=False),          # identity: a ref where one exists
    sa.Column("body", sa.Text, nullable=False),             # latest revision
    sa.Column("occurrences", sa.Integer, nullable=False),   # revision count
    sa.Column("produced_by_agent_id", sa.Uuid, nullable=False),
    sa.Column("produced_by_turn_id", sa.Uuid, nullable=False),
    sa.Column("produced_in_conversation_id", sa.Uuid, nullable=False),
    sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),  # unit 2
    sa.Column("triaged_turn_id", sa.Uuid, nullable=True),   # unit 2: the drain turn that read it
    sa.Column("delivered_turn_id", sa.Uuid, nullable=True), # unit 3: the relay turn; the loop fence
    sa.Column("delivered_surface", sa.Text, nullable=True), # unit 3: null = read on the portal only
    sa.Column("created_at", ...), sa.Column("updated_at", ...),
    sa.Index("notification_open_subject", "workspace_id", "to_agent_id", "member_id", "subject",
             unique=True, postgresql_where=OPEN, sqlite_where=OPEN),  # OPEN = triaged_turn_id is null
    sa.Index("notification_delivered_turn", "workspace_id", "delivered_turn_id"),
)
```

The table above is the shape the three units reach together. Each column and index lands in the
unit that first writes and reads it, in that unit's own migration: unit 1 ships the row and the
total unique index `notification_subject`, unit 2 adds the claim and the triage stamp and makes
that index partial on the open row, unit 3 adds the delivery stamps. A migration adds only the
columns its unit wires.

One kind, `notification`, on `MemberOwnedObjects`, owner `ObjectOwner(member_id, shared=False)`:
list and get; apply refuses naming the tool; delete dismisses. `list_fields = {subject, occurrences,
triaged, delivered_surface, created_at}`. Links: `created_in` (the producing conversation),
`scoped_to` (the producing agent). It is the portal's read projection and the chat answer to "what
did you not tell me". Rows are private to `member_id`; a workspace admin inspects, as for every
member-owned kind.

### Producer: the `notify` tool

Every agent holds it through the member-facing set. It writes one row under the turn's authority.

```python
class NotifyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    subject: str = Field(max_length=120, description=(
        "The stable thing this is about, as a ref where one exists (`source/<name>`, `issue/1801`). "
        "Two messages naming one subject fold into one."))
    body: str = Field(max_length=1000, description=(
        "What happened and why it matters, in your own words. State the fact and its consequence."))


async def notify(ctx: ToolContext, args: NotifyInput) -> ToolResult:
    member_id = authority_member_id(ctx.authority)
    if member_id is None:
        return _error(NOTIFY_NEEDS_A_MEMBER)          # a notification with no recipient is not one
    ext = _require_ext(ctx)
    store = NotificationStore(ext)
    if await store.is_delivery_turn(ctx.turn.id):
        return _error(NOTIFY_INSIDE_A_DELIVERY)       # loop fence: exact turn id, no window
    inbox = await ext.agent_named(NOTIFICATION_AGENT_NAME)   # the one reader today
    if inbox is None or inbox.id == ctx.agent.id:
        return _error(NOTIFY_NO_INBOX)                # no reader, or an app mailing itself
    match await store.post(inbox.id, member_id, args.subject, args.body,
                           ctx.agent.id, ctx.turn.id, ctx.turn.conversation_id):
        case Refused(reason=reason):
            return _error(reason)                     # the model reads why and raises altitude
        case Posted(occurrences=n):
            return ToolResult(content=(TextContent(text=NOTIFY_QUEUED.format(n=n)),))
```

`store.post` is one `INSERT … ON CONFLICT (workspace_id, to_agent_id, member_id, subject) WHERE
triaged_turn_id IS NULL DO UPDATE SET body = excluded.body, occurrences = occurrences + 1`: the
page-revision rule, applied to a message.

**Constraint 2, source syncing stays quiet.** Every fence is code and talks back to the model:

| Fence | Mechanism |
|---|---|
| fold | the partial unique index: 400 changed pages under `source/<name>` is one row saying 400 |
| altitude | more than `NOTIFY_SUBJECTS_PER_TURN` (8) distinct subjects in one turn is refused with the reason |
| loop | a turn recorded in `delivered_turn_id` cannot post; the producing agent cannot post to its own inbox |
| batch | the drain wakes one turn per lane per tick, whatever arrived |
| triage | the Notification agent is where the rest of the noise dies, and a member's "stop telling me about X" is a memory item that recall injects into its turn |

Nothing is hidden because nothing is spoken: a tool call never enters a delivered reply. The portal's
activity list shows the word `notify` and no content, as it does for `report_problem`. What the model
sees back is one line, plus the refusal reason when a fence bites.

**Tool description** (ships only ablated, see Evals):

> Put one message in the notification queue for the member this turn runs for. Use it for a
> business or operational fact in their own data that they would want to know without asking: a
> churn or failed-payment spike in Stripe, a deploy or CI run that failed on main in GitHub, an
> email that needs a decision or is going unanswered, a customer or investor turning. It is not for
> ufo's own condition: a connection that stopped authenticating or a task that faults is
> `report_problem` and a line in your reply, never a notification. Nothing answers back here and the
> member may never see it: one agent reads every notification and decides. One call per subject,
> whatever the batch size; two different things are two calls. Do not raise what this turn already
> told them or a fault you can repair.

The line is drawn at whose condition it is. A notification carries what the member's own data says
about their business: revenue moving, production down, a person waiting on them. ufo's own faults
already have a reader, the engineers on the turns board, and a member reads them as a line in the
reply of the turn that met them.

### Consumer: the Notification app

```python
NOTIFICATION_AGENT = AgentProvision(
    name="notification",
    spec=AgentSpec(
        prompt=NOTIFICATION_PROMPT,                # ships only ablated
        purpose="Decides which of the things your agents noticed are worth interrupting you for.",
        model="auto", reasoning="medium", internet_access_allowed=False, visibility="workspace",
    ),
    tools=("object_list", "object_get", "object_explain", "action:notification:deliver"),
    icon="bell",
)
```

**Constraint 1, one agent reaches members.** `deliver` is `profile_only=True`, so the member-facing
set, every prepared intent, and every unnamed subagent profile withhold it. The only allowlist naming
it is this provision's, and an allowlist is declared, never typed. A source-grant fence is the wrong
tool: a grant is a member-extendable edge, which is exactly what must be impossible here. The
allowlist also omits `notify` and every sandbox tool, so the app cannot raise a notification and a
triage turn is a read of its inbound and one verb. Backstop against a second extension's provision
naming the same id: the handler compares the calling agent's `provisioned_by` to this extension and
fails loud.

**Its lane.** One conversation per `(agent, member)`, opened by the drain on the first message and
never at activation: `ctx.open_conversation(agent.id, f"member:{member_id.hex}", member_id=member_id)`.
Surface `app_notification`, audience `member:<uuid>`, own queue partition, own sandbox, off every
rail. It lists in the app's index lane, so a member can open it and talk to it. Its audience is the
member's own, so memory recall injects that member's items into every triage turn, and "stop telling
me about the Ashby source", said to any agent, is a memory write that reaches the triage. This is
"every app subscribes to its own inbox by default" with no provision field: the subscription is the
lane, and the lane exists once something is in it.

**The drain.** A per-minute `JobSpec` over the extension's own table (`owner_candidates`), the
`monitors` cadence, and the `_fire_trigger` `current` shape: one batch, one invoke.

```python
DRAIN_COOLDOWN_SECONDS = 300
DRAIN_CLAIM_LEASE_SECONDS = 300
DRAIN_BATCH = 25                 # 25 rows at a 1000-char body cap rides the inbound inline


@dataclass(frozen=True)
class InboxDrain:
    """Fold each lane's untriaged notifications into one turn on that lane. Fires on the clock,
    never on a row it writes."""

    ctx: ExtensionContext

    async def run(self) -> None:
        store = NotificationStore(self.ctx)
        for lane in await store.lanes_with_untriaged(DRAIN_COOLDOWN_SECONDS):
            batch = await store.claim(lane, DRAIN_BATCH, DRAIN_CLAIM_LEASE_SECONDS)
            if not batch:
                continue
            conversation_id = await self.ctx.open_conversation(
                lane.agent_id, f"member:{lane.member_id.hex}", member_id=lane.member_id
            )
            with suppress(AgentArchived):
                turn_id = await self.ctx.invoke(
                    conversation_id, lane.agent_id, _drain_message(batch),
                    idempotency_key=f"notify-drain:{lane.agent_id.hex}:{lane.member_id.hex}:{batch[-1].id.hex}",
                    authority=MemberAuthority(lane.member_id),
                    holds_work_already_done=True, standalone=True,
                )
            if turn_id is not None:
                await store.mark_triaged(batch, turn_id)
```

`Lane` is `(agent_id, member_id)`. Today every lane's agent is the Notification agent; the drain does
not know that and does not need to. `_drain_message` renders the batch inline: one block per row
with its ref, subject, occurrences, producing agent, and body, walled as data.

| Property | How |
|---|---|
| batched | one turn per lane per tick; the batch is the inbound |
| idempotent | key on lane and newest row; a redelivery across a roll settles on the admitted turn; `mark_triaged` only on an accepted id, a lapsed lease is the retry |
| cooldown | a lane that had a turn inside `DRAIN_COOLDOWN_SECONDS` is skipped (the `DeliverySweep` bound) |
| cannot fire on what it caused | cron, never event-fired; `notify` refuses inside a relay turn and refuses a row for the producer's own inbox |
| parks on spend refusal | `holds_work_already_done=True`, as `_fire_trigger` does |
| unseated member | `invoke` seat-gates on the on-behalf member; rows stay claimed and lapse back |

**The delivery action.**

```python
class DeliverInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    refs: tuple[str, ...] = Field(max_length=DRAIN_BATCH, description="The notification refs this message covers.")
    text: str = Field(max_length=1200, description="What the member reads, in your words: what happened and what it means for them.")


DELIVER = ToolDef(
    name="deliver", description=DELIVER_DESCRIPTION, input_model=DeliverInput, handler=deliver,
    profile_only=True, side_effecting=True,
    bound=ObjectBinding(kind=NOTIFICATION_KIND, binding="collection"),
)


async def deliver(ctx: ToolContext, args: DeliverInput) -> ToolResult:
    ext = _require_ext(ctx)
    await _require_notification_agent(ext, ctx.agent)
    store = NotificationStore(ext)
    if await store.delivered_from(ctx.turn.id):
        return _error(ONE_DELIVERY_PER_TURN)          # one message per wake, enforced
    rows = await store.triaged_by(ctx.turn.id, args.refs)
    member_id = _one_member(rows)
    reach = await ext.member_reach(member_id)
    if not reach:
        await store.mark_delivered(rows, turn_id=None, surface=None)
        return ToolResult(content=(TextContent(text=DELIVERED_TO_PORTAL_ONLY),))
    chosen = reach[0]
    turn_id = await ext.invoke(
        chosen.conversation_id, chosen.agent_id, wall("notification", args.text) + RELAY_INSTRUCTION,
        idempotency_key=f"notify-deliver:{rows[0].id.hex}",
        authority=MemberAuthority(member_id), as_scheduled=True, holds_work_already_done=True,
    )
    if turn_id is None:
        return _error(NOT_DELIVERED)
    await store.mark_delivered(rows, turn_id=turn_id, surface=chosen.surface)
    return ToolResult(content=(TextContent(text=f"Delivered on {chosen.surface}."),))
```

The handler picks the channel, not the model. `reach[0]` is the newest durable conversation the
member personally spoke in: the founders' "best channel by last usage", as a sort. The relay turn
founded there is bound to the agent that conversation binds (normally main), so the member reads it
from the agent they already talk to, in the thread they already use, and replies there like anything
else. Slack posts it at DM top level; iMessage posts it on the proved line. A member with no durable
conversation is not pushed; the row stays readable on the portal and the lane sits in their rail.
Nothing opens a DM a member never opened.

The push rate is bounded by structure, not counters: one `deliver` per triage turn, one triage turn
per lane per `DRAIN_COOLDOWN_SECONDS`.

**Mute and adjust, in chat.** "Stop telling me about the Ashby source" is a memory write by whatever
agent heard it. The lane's audience is the member's own, so recall injects it into the next triage
turn, and the prompt says to honour it. A muted subject still costs a row and a read; that is
accepted until volume says otherwise.

### The core addition

```python
@dataclass(frozen=True)
class MemberReach:
    """One durable-surface conversation this member already speaks in: the conversation, the agent
    it binds, the surface that posts it, and when they last spoke there."""

    surface: str
    conversation_id: UUID
    agent_id: UUID
    last_turn_at: datetime


# ExtensionContext
durable_surfaces: frozenset[str] = frozenset()        # host passes durable_surfaces(manifests)

async def member_reach(self, member_id: UUID, limit: int = 4) -> tuple[MemberReach, ...]:
    """Newest first. Only conversations whose audience is this member's own and in which this
    member spoke (`turn.speaker_member_id`). Identity and routing only: no title, no body."""
```

Gated on `member_context_read` like the other member projections, so a third-party extension cannot
read it. Why core: `conversation`, `turn`, and the deploy's durable-surface set are core's and
unreachable from `ufo.sdk`; the read adds no capability, only a projection of state admission
already uses to decide the same question. `open_conversation` stays hardcoded to the extension's own
surface.

### Radar and the neighbouring idioms

Radar does not move. It is a place you go; the Notification app is a thing that finds you.

| Idiom | Collapses onto the inbox? |
|---|---|
| `report_problem` | never: its reader is an engineer, not a member |
| subagent result delivery, `message_spawn` | never: a parent's own arrival, keyed on the child turn |
| `scheduled_task` fires | never; a fire's report may become a `notify` call |
| `source_trigger per_page` | round two, and the real prize: one turn per changed page is the 400-turn version of the same problem. A third delivery mode that raises instead of firing a turn is one row per page, folded by subject, drained in one turn. Needs the inbox to earn its keep first. |
| every app's inbox | the table is already keyed by recipient. A second app becomes a reader when it has a prompt paragraph that reads its lane; then `notify` gains a `to` argument and nothing else changes. Not before: a message that lands where nothing reads it is a consumer with no producer, inverted. |
| recall of past notifications | later, as a `PageFeed` producer over delivered rows, so "what did you tell me last week" reaches memory and the wiki. Delivered rows only: raised chatter never derives. |
| app exceptions | later; no producer exists, so no type is declared now |

Sources stay upstream of the inbox. A source trigger turn calls `notify`; the inbox is never itself
a source, because a shared source is the only kind that carries a trigger, a page has no triage
state, and every page derives into memory.

One paragraph on Radar, as permitted: if the pane earns its place, Radar's feed and the notification
list are two lanes of one page reading two kinds through one route. A portal change, after the triage
eval is stable, not before.

## Rough implementation

| File | Contents |
|---|---|
| `core/src/ufo/runtime/ext/context.py` | `MemberReach`, `durable_surfaces`, `member_reach` |
| `core/src/ufo/serve.py`, `core/src/ufo/runtime/jobs.py` | pass `durable_surfaces(manifests)` into `context_for` |
| `core/tests/test_ext_member_reach.py` | the projection against Postgres and sqlite |
| `extensions/app_notification/ufo_ext_app_notification/store.py` | the table, `Lane`, `NotificationStore` (post, lanes_with_untriaged, claim, mark_triaged, triaged_by, delivered_from, mark_delivered, is_delivery_turn) |
| `.../migrations/0001_notification.py` | the table and both indexes (`revision="notification_0001"`, `branch_labels=("notification",)`, `depends_on="0001"`, the `memory` layout) |
| `.../notify_tool.py` | `NotifyInput`, `notify`, description arm |
| `.../drain.py` | `InboxDrain`, `_drain_message` |
| `.../deliver.py` | `DeliverInput`, `deliver`, `_require_notification_agent`, `RELAY_INSTRUCTION` |
| `.../kind.py` | `notification` on `MemberOwnedObjects` |
| `.../manifest.py` | `Manifest(tools=(NOTIFY, DELIVER), objects=(NOTIFICATION_OBJECT,), jobs=(DRAIN,), agents=(NOTIFICATION_AGENT,), member_context_read=True)` |
| `pyproject.toml` | the `ufo.extension` entry point |
| `evals/suites/notify_raise.py`, `evals/suites/notify_triage.py`, `evals/notify-text.toml` | the two suites and the ablation experiment |
| `spec.md` | the table row, the kind row, one Surfaces line on where an app's reach comes from |

**Tests, one per new function.**

| Unit | Test |
|---|---|
| `store.post` | two posts on one subject are one row with `occurrences=2` and the second body; a post after triage opens a new row (mutation-check the partial predicate); the same subject for two members is two rows; the ninth subject in a turn refuses; a relay turn refuses; the Notification agent's own turn refuses |
| `member_reach` | a live-surface conversation is absent; another member's private conversation is absent; a conversation the member never spoke in is absent; newest first |
| `InboxDrain.run` | twenty-five rows for one lane produce one turn whose inbound carries all twenty-five; two members are two lanes and two turns; a lane inside the cooldown is skipped and rows stay untriaged; a failed invoke leaves the lease to lapse; the lane conversation is opened on the first batch and reused on the second |
| `deliver` | picks the newest reach; records the relay turn on the rows; a second `deliver` in the same triage turn refuses; a second call on the same rows settles on the same relay turn through the key; no reach marks portal-only |
| the loop test (load-bearing) | seed, drain, deliver, then have the relay turn call `notify` and assert refusal, and that a second tick founds no turn. Mutation-check by deleting the `delivered_turn_id` lookup. |
| seam, via `extensions/sample` | an agent with no allowlist is not offered `action:notification:deliver`; naming it in an `object_apply agent` spec grants nothing; only one installed provision names it |

**Evals and ablation.** Two model-read texts, and no existing suite measures either, so the suites
ship first (the `problem_report.py` shape: opposing pairs, deterministic scorers).

| Suite | Cases |
|---|---|
| `notify_raise` | A founder with Gmail, GitHub, and Stripe connected. Signal: a churn spike, a deploy failed on main, an investor deadline unanswered, a customer turning, a failed-payment cluster (pass = one call, the source's subject), and churn plus a failed deploy in one report (pass = exactly two calls, two subjects). Restraint: green CI, newsletters and receipts, one small refund, a member reading the answer now, the agent's own failed step (pass = zero calls), and a sync whose connection broke (pass = `report_problem`, never `notify`). |
| `notify_triage` | one batch of twelve rows of which three merit a member; pass = one `deliver` naming exactly those three, text carrying the load-bearing fact. A second case seeds a recalled "stop telling me about source/ashby" memory item and two Ashby rows; pass = neither delivered. |

`notify-text.toml` arms: control; `notify` description with and without the "one call per subject"
sentence (the SQL fold already makes it true, so it must prove itself); the triage prompt with and
without; each beside the `report_problem` description, the neighbouring text teaching the same
restraint.

**PR units, each with both ends, each shippable alone.**

1. `add notification queue`: the table, its migration, `notify` with its fences, the read-only
   `notification` kind, `notify_raise` and its ablation. Rows collect and a member reads them.
2. `drain notifications`: the provision (without `deliver`), the lane, `InboxDrain`, the triage
   prompt after `notify_triage` and its ablation, `triaged_turn_id`.
3. `deliver notifications`: core `member_reach`, `deliver`, `delivered_turn_id`, the loop fence.
   The core read lands with its only consumer.

Measured later, not scheduled: a `notification_rule` kind applied in SQL at raise if memory-based
mute proves weak; a model pin on the relay turn (`ExtensionContext.invoke` forwarding
`runtime_config`, which `TurnInvoker.invoke` already takes) if the relay's cost shows on the bill;
the `<notify>` span producer on the same store (generalize `replies.py` to a declared tag set, route
non-reply spans onto `Stop.spans`, ids `uuid5(turn, round, span)` so the double `stop` fire writes
one row) if `notify_raise` shows the tool underfires; the app's homepage pane.

## Doctrine fit / implications

- **Core doctrine.** Everything but one read is an extension: the table, the tools, the kind, the
  job, the agent, the prompt. `member_reach` is a named gap an extension cannot express
  (`conversation`, `turn`, the durable set).
- **Every member action happens in chat.** Muting, dismissing, and replying are chat acts through
  memory, object verbs, and the relay thread. No endpoint, no setting, no slash command.
- **One shape.** One table, one write path (`store.post`), one fold key (`subject`), one delivery
  seam (a turn in a durable conversation), one loop fence (`delivered_turn_id`). The row is keyed by
  recipient because an inbox is an agent's, the way a page is a source's; that is the shape, not a
  feature. No `type` column, no message registry, no dispatcher, no `to` argument: one reader does
  not demand a general surface. The generality the founders hope for is earned when a second app
  reads its lane, not declared now.
- **Borrowed from sources, not built on them.** Fold by identity, the `current` delivery's
  one-batch-one-invoke, park on an unseated member, and a lane that opens on first content. The
  inbox is not a source: sources are upstream producers of it.
- **Enforce, don't document.** Fold, altitude, loop, one-delivery-per-turn, and isolation are a
  partial index, a count, a column lookup, and an allowlist. No time-window counters. The prompts
  carry only what the ablation keeps.
- **Fail loud.** A notification with no member refuses; a second provision naming `deliver`
  crashes the call; an unwired invoker raises.
- **Event-fired work cannot fire on what it caused.** Cron drain, self-mail refusal, and an exact
  turn-id fence.
- **Both ends or neither.** Each unit's producer ships with its consumer; the core read lands inside
  unit 3.

## Alternatives

| Not taken | Why |
|---|---|
| **Tag first** (`<notify>` in the round text, the founders' sketch). Zero extra round, invisible by the proven `reply-to` mechanism, and not a grant. | It needs the harness's one tag grammar generalized (`AgentDefinition.span_tags`, `Stop.spans`, a `Manifest` point, a `_close` branch for a round that empties to a span or the engine raises "empty response twice"), for one consumer. Decisively for constraint 2: a tag has no back-channel, so every fence drops silently and the model cannot raise its altitude mid-turn; an unclosed tag loses the whole notification with no recovery. Kept as a later producer on the same store, gated on measurement. |
| **A general inbox bus now** (`type` column, a `to` argument, collapsing subagent delivery, scheduled results, source triggers). | One writer, one reader. Subagent results and `message_spawn` never fit (they are a parent's arrival). The recipient-keyed shape ships; the `to` argument lands with the second reader's prompt. |
| **The inbox as a source, every app subscribed to its own by default.** Reuses fold-by-identity, the trigger wake, source grants, and memory recall. | Only a shared source carries a trigger, so a private per-member inbox cannot wake anything, and an ownerless app inbox is shared with every member the app is visible to. A page has no raised-triaged-delivered state, so the state lands in a second table. Every page derives into memory and the wiki, so operational chatter reaches recall. A source is a pull on its own clock, so the write becomes a fake provider synced a tick later. A default subscription needs a provision field and activation code in core for seven apps nobody writes to. What survives is taken: the fold rule, the `current` delivery shape, the lane on first content, and recall as a later `PageFeed` producer over delivered rows. |
| **A turn-less delivery seam** (a member-addressed row the `WritebackPoller` posts through `speak`). | A new table and poller path and two surface changes, duplicating at-least-once delivery, recovery, and idempotency, and it breaks "the turn is the audit record". The relay turn reuses all of it and gives the member a thread to reply in. Named as round two if the relay's cost or voice bites. |
| **A source-grant style fence for the delivery tool.** | A grant is a member-created, member-extendable edge in chat. The fence must be one no member can widen; the provision allowlist plus `profile_only` is exactly that. |
| **A `notification_rule` kind, mute in SQL at raise.** | A second table and kind for a rule the triage turn can read from memory today. Deterministic and free per muted row, but not yet needed. Returns when volume shows a memory-based mute leaking or costing. |
| **A `notification_delivery` table, hourly and daily caps, a change-log file, a relay model pin.** | Each bounded something nobody has measured. The fence moved onto the row (`delivered_turn_id`); the rate is bounded by one delivery per triage turn and the drain cooldown; the batch rides the inbound at 25 rows; the relay runs the default model until the bill says otherwise. |
| **Replace Radar.** | Constraint 3. Different question, different shape. |
| **`member_id` null routed to the earliest seated admin.** | The inbox is per member and private. A notification nobody is the recipient of is refused at raise. |

## Open decisions

1. **Producer order: tool now, tag later, or tag now?** Recommendation: tool now. The fences need a
   reply channel, and the harness change should land on evidence that the tool underfires.
2. **Relay voice.** The relay runs the member's own agent with a walled text and a one-line "say
   this and stop", on the default model. Recommendation: measure drift in `notify_triage` before
   pinning a model or replacing the prompt through an environment document.
3. **Constants.** `NOTIFY_SUBJECTS_PER_TURN=8`, `DRAIN_BATCH=25`, `DRAIN_COOLDOWN_SECONDS=300`.
   Recommendation: ship these and read them off the eval and the first week of rows before tuning.
4. **Portal pane.** The kind reads through the existing object projections at once. Recommendation:
   wait for the mechanism to earn a page.
5. **A `weight`/`urgency` field on the producer.** A model-authored claim the consumer would have to
   honour or ignore. Recommendation: no field; make it an arm in `notify-text.toml` and add the
   column only if the arm proves triage needs it.
6. **When `notify` gains `to`.** Recommendation: with the first second reader, in the same unit as
   that app's inbox-reading prompt paragraph and its eval case, and with a visibility gate (a private
   app's inbox takes rows only from turns its owner or an admin authorized). Not before.

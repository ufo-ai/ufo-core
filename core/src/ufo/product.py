"""Where each workspace stands in the product funnel, counted onto the fleet's metrics every tick.

Every stage is already a row — a connector grant, an invited member, a charged purchase — so
nothing here is stored and no event has to be caught as it happens. A stage redefined next month
re-derives itself over the whole history on the next tick, which is the property that makes the
funnel worth deriving rather than shipping as events.

The census runs once per workspace per tick, under the workspace the dispatcher bound, so the number
of workspaces at a stage is what one tick's increments add up to. The board therefore counts a stage
out of one bucket `PRODUCT_CENSUS_SECONDS` wide, which holds exactly one tick, and never out of a
bucket Datadog sizes for itself, which counts one workspace once per tick it covers;
`_census_period_failures` in the gates holds the two ends of that arithmetic together. Every query
names `workspace_id` itself rather than leaning on RLS to filter it, as every other core read does:
one workspace's rows counted under another's binding would multiply every number on the board rather
than fail.

Stages are a ladder: a workspace that paid also counts as seated, so each stage's total is at most
the one above it and the whole funnel reads off one series. What a workspace has attached is counted
beside it as a `kind` and a `name` taken straight off the column — the surface, the credential slot,
the connector's provider, the app's name — so core counts Slack, GitHub, iMessage and every
bring-your-own-key connector without holding one of their names. A surface that is bound and a
surface a member can actually be reached on are different facts, so a proved address is its own kind
rather than the installation's.

Beside the funnel, the same tick counts each setup step a workspace took and how long the step took
to arrive. A stage says a workspace got there; a step says when, so the board reads where a slow
setup stalls a team rather than only how many teams stalled. A milestone step is derived exactly
like a stage — the earliest row that marks it — and counted on the one tick its first row lands in,
which is what makes one workspace's step one count forever rather than one per tick since. A step
that leaves no row cannot be derived, so `record_onboarding_step` counts the two that do not: an
extension's onboarding step failing under `ufoctl init`, and a first-run screen a member skipped or
walked out of. Neither a member nor a workspace is a tag anywhere here — a step, its status, the
surface it happened on, and the provider it attached are the whole tag set, so the series stays one
per step whatever the fleet's size. The member-frequency count also stays aggregate: core combines
direct turns and folded arrivals because extensions cannot read either runtime-owned table, then
exports only the workspace's qualifying count."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import sqlalchemy as sa

from ufo.db import workspace_tx
from ufo.harness.o11y import emit_histogram, emit_metric
from ufo.runtime.access.credentials import MEMBER_SLOT_INFIX
from ufo.runtime.workspace import ws_current
from ufo.schema import tables
from ufo.schema.records import INTENT_ADMISSION, MEMBER_ADMISSION

PRODUCT_CENSUS_JOB = "product_census"
PRODUCT_CENSUS_SECONDS = 600
PRODUCT_CENSUS_SCHEDULE = "0 */10 * * * *"
PRODUCT_STAGE_METRIC = "product_stage_total"
PRODUCT_ATTACH_METRIC = "product_attach_total"
PRODUCT_ACTIVE_MEMBER_2D_7D_METRIC = "product_active_member_2d_7d_total"
ONBOARDING_STEP_METRIC = "onboarding_step_total"
ONBOARDING_LATENCY_HISTOGRAM = "onboarding_step_latency_ms"
STEP_COMPLETED = "completed"
STEP_FAILED = "failed"
CENSUS_SURFACE = "census"
INIT_SURFACE = "init"
NO_PROVIDER = ""
WORKSPACE_CREATED_STEP = "workspace_created"
MEMBER_CHATTED_STEP = "member_chatted"
CONNECTOR_ATTACHED_STEP = "connector_attached"
SURFACE_INSTALLED_STEP = "surface_installed"
TEAMMATE_INVITED_STEP = "teammate_invited"
APP_BUILT_STEP = "app_built"
INVITED_MEMBER_CHATTED_STEP = "invited_member_chatted"
ACTIVE_DAY_DAYS = 1
ACTIVE_WEEK_DAYS = 7
ACTIVE_MEMBER_DAYS = 2
SURFACE_KIND = "surface"
ADDRESS_KIND = "address"
CREDENTIAL_KIND = "credential"
CONNECTOR_KIND = "connector"
APP_KIND = "app"


def _member_turn(workspace_id: UUID) -> sa.ColumnElement[bool]:
    """The workspace's turns a member started, which is what `stage:chatted` and the chatted steps
    both count. One definition, so redefining a member turn moves the funnel and the steps
    together."""
    return sa.and_(
        tables.turn.c.workspace_id == workspace_id,
        tables.turn.c.admission_source == MEMBER_ADMISSION,
        tables.turn.c.parent_turn_id.is_(None),
    )


async def product_census() -> None:
    """Count the bound workspace's funnel stages and everything it has attached.

    One round trip each: the stages are scalar `EXISTS` subqueries selected together, and the
    attachments one union over the five columns that name them."""
    workspace_id = ws_current().workspace_id
    now = datetime.now(UTC)
    member_turn = _member_turn(workspace_id)
    activity = sa.union_all(
        sa.select(
            tables.turn.c.speaker_member_id.label("member_id"),
            sa.func.date(tables.turn.c.created_at).label("day"),
        ).where(
            tables.turn.c.workspace_id == workspace_id,
            tables.turn.c.admission_source.in_((MEMBER_ADMISSION, INTENT_ADMISSION)),
            tables.turn.c.speaker_member_id.is_not(None),
            tables.turn.c.parent_turn_id.is_(None),
            tables.turn.c.created_at > now - timedelta(days=ACTIVE_WEEK_DAYS),
        ),
        sa.select(
            tables.inbound_message.c.speaker_member_id,
            sa.func.date(tables.inbound_message.c.created_at),
        ).where(
            tables.inbound_message.c.workspace_id == workspace_id,
            tables.inbound_message.c.admission_source == MEMBER_ADMISSION,
            tables.inbound_message.c.speaker_member_id.is_not(None),
            tables.inbound_message.c.created_at > now - timedelta(days=ACTIVE_WEEK_DAYS),
        ),
    ).subquery()
    active_members = (
        sa.select(activity.c.member_id)
        .group_by(activity.c.member_id)
        .having(sa.func.count(sa.distinct(activity.c.day)) >= ACTIVE_MEMBER_DAYS)
        .subquery()
    )
    stages = sa.select(
        sa.exists(
            sa.select(tables.member.c.id).where(
                tables.member.c.workspace_id == workspace_id,
                tables.member.c.seated_at.is_not(None),
            )
        ).label("seated"),
        sa.exists(
            sa.select(tables.connector_grant.c.id).where(
                tables.connector_grant.c.workspace_id == workspace_id
            )
        ).label("connector"),
        sa.exists(
            sa.select(tables.member.c.id).where(
                tables.member.c.workspace_id == workspace_id,
                tables.member.c.invited_at.is_not(None),
            )
        ).label("invited"),
        sa.exists(
            # An owner is the one mark of an app the workspace made for itself: a create refuses
            # without a speaking member, while the main agent signup writes and every
            # `AgentProvision` the fleet applies leave the column null. Provenance says the
            # opposite of what it looks like here — `app_chat` provisions the main agent of every
            # hosted workspace, so a provisioned row is the fleet's mark and stands on all of them.
            sa.select(tables.agent.c.id).where(
                tables.agent.c.workspace_id == workspace_id,
                tables.agent.c.owner_member_id.is_not(None),
            )
        ).label("app"),
        sa.exists(sa.select(tables.turn.c.id).where(member_turn)).label("chatted"),
        sa.exists(
            sa.select(tables.turn.c.id).where(
                member_turn,
                tables.turn.c.created_at > now - timedelta(days=ACTIVE_DAY_DAYS),
            )
        ).label("active_1d"),
        sa.exists(
            sa.select(tables.turn.c.id).where(
                member_turn,
                tables.turn.c.created_at > now - timedelta(days=ACTIVE_WEEK_DAYS),
            )
        ).label("active_7d"),
        sa.exists(
            sa.select(tables.balance_purchase.c.id).where(
                tables.balance_purchase.c.workspace_id == workspace_id,
                tables.balance_purchase.c.charged_micro_usd > 0,
            )
        ).label("paid"),
        sa.select(sa.func.count())
        .select_from(active_members)
        .scalar_subquery()
        .label(PRODUCT_ACTIVE_MEMBER_2D_7D_METRIC),
    )
    attached = sa.union_all(
        sa.select(
            sa.literal(SURFACE_KIND).label("kind"),
            tables.surface_installation.c.surface.label("name"),
        )
        .where(tables.surface_installation.c.workspace_id == workspace_id)
        .distinct(),
        sa.select(sa.literal(ADDRESS_KIND), tables.surface_address.c.surface)
        .where(
            tables.surface_address.c.workspace_id == workspace_id,
            tables.surface_address.c.proved_by.is_not(None),
        )
        .distinct(),
        sa.select(sa.literal(CREDENTIAL_KIND), tables.credential.c.slot)
        .where(
            tables.credential.c.workspace_id == workspace_id,
            ~tables.credential.c.slot.contains(MEMBER_SLOT_INFIX),
        )
        .distinct(),
        sa.select(sa.literal(CONNECTOR_KIND), tables.connection.c.provider)
        .select_from(
            tables.connection.join(
                tables.connector_grant,
                tables.connector_grant.c.connection_id == tables.connection.c.id,
            )
        )
        .where(tables.connection.c.workspace_id == workspace_id)
        .distinct(),
        sa.select(sa.literal(APP_KIND), tables.agent.c.provisioned_name)
        .where(
            tables.agent.c.workspace_id == workspace_id,
            tables.agent.c.provisioned_name.is_not(None),
        )
        .distinct(),
    )
    async with workspace_tx() as connection:
        reached = dict((await connection.execute(stages)).mappings().one())
        holdings = (await connection.execute(attached)).all()
    active_member_count = reached.pop(PRODUCT_ACTIVE_MEMBER_2D_7D_METRIC)
    for stage, arrived in reached.items():
        emit_metric(PRODUCT_STAGE_METRIC, int(arrived), stage=stage)
    emit_metric(PRODUCT_ACTIVE_MEMBER_2D_7D_METRIC, active_member_count)
    for holding in holdings:
        emit_metric(PRODUCT_ATTACH_METRIC, kind=holding.kind, name=holding.name)


def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _elapsed_ms(created_at: datetime, at: datetime) -> int:
    return max(0, int((_utc(at) - _utc(created_at)).total_seconds() * 1000))


def _emit_step(step: str, status: str, surface: str, provider: str, latency_ms: int | None) -> None:
    emit_metric(
        ONBOARDING_STEP_METRIC, step=step, status=status, surface=surface, provider=provider
    )
    if latency_ms is not None:
        emit_histogram(
            ONBOARDING_LATENCY_HISTOGRAM, latency_ms, step=step, status=status, surface=surface
        )


async def record_onboarding_step(
    workspace_id: UUID, step: str, status: str, *, surface: str, provider: str = NO_PROVIDER
) -> None:
    """Count one onboarding step the moment it happens, with the time since the workspace was
    founded.

    This is for a step whose outcome no row records: a skip, a member walking out of the first run,
    an extension's onboarding step that raised. Anything the schema already marks is derived by
    `onboarding_census` instead, which needs no call site and re-derives itself when the step is
    redefined. The founding time is read here rather than passed in, so a caller holding a request
    and a member cannot report a latency measured from anything else."""
    async with workspace_tx() as connection:
        created_at = (
            await connection.execute(
                sa.select(tables.workspace.c.created_at).where(
                    tables.workspace.c.id == workspace_id
                )
            )
        ).scalar_one_or_none()
    _emit_step(
        step,
        status,
        surface,
        provider,
        None if created_at is None else _elapsed_ms(created_at, datetime.now(UTC)),
    )


async def onboarding_census() -> None:
    """Count the bound workspace's setup steps that first arrived in the tick just gone, each with
    the time the workspace took to reach it.

    The earliest row marking a step is what the step's time is, so a step is counted on the one tick
    that row lands in and never again — the same increment-per-tick arithmetic the funnel reads,
    except that a step happens once. A window rather than stored state is what makes it once: a tick
    that does not run drops a step's count rather than double-counting it, which is the direction
    that leaves the board wrong about one workspace instead of wrong about all of them.

    One round trip: every step's first moment, and the provider and surface the first attachment
    named, are scalar subqueries selected together."""
    workspace_id = ws_current().workspace_id
    now = datetime.now(UTC)
    member_turn = _member_turn(workspace_id)
    granted = tables.connector_grant.join(
        tables.connection, tables.connector_grant.c.connection_id == tables.connection.c.id
    )
    moments = sa.select(
        sa.select(tables.workspace.c.created_at)
        .where(tables.workspace.c.id == workspace_id)
        .scalar_subquery()
        .label("created"),
        sa.select(sa.func.min(tables.turn.c.created_at))
        .where(member_turn)
        .scalar_subquery()
        .label("chatted"),
        sa.select(sa.func.min(tables.connector_grant.c.created_at))
        .where(tables.connector_grant.c.workspace_id == workspace_id)
        .scalar_subquery()
        .label("connector"),
        sa.select(tables.connection.c.provider)
        .select_from(granted)
        .where(tables.connector_grant.c.workspace_id == workspace_id)
        .order_by(tables.connector_grant.c.created_at)
        .limit(1)
        .scalar_subquery()
        .label("connector_provider"),
        sa.select(sa.func.min(tables.surface_installation.c.created_at))
        .where(tables.surface_installation.c.workspace_id == workspace_id)
        .scalar_subquery()
        .label("surface"),
        sa.select(tables.surface_installation.c.surface)
        .where(tables.surface_installation.c.workspace_id == workspace_id)
        .order_by(tables.surface_installation.c.created_at)
        .limit(1)
        .scalar_subquery()
        .label("surface_name"),
        sa.select(sa.func.min(tables.member.c.invited_at))
        .where(
            tables.member.c.workspace_id == workspace_id,
            tables.member.c.invited_at.is_not(None),
        )
        .scalar_subquery()
        .label("invited"),
        sa.select(sa.func.min(tables.agent.c.created_at))
        .where(
            tables.agent.c.workspace_id == workspace_id,
            tables.agent.c.owner_member_id.is_not(None),
        )
        .scalar_subquery()
        .label("app"),
        sa.select(sa.func.min(tables.turn.c.created_at))
        .select_from(
            tables.turn.join(tables.member, tables.member.c.id == tables.turn.c.speaker_member_id)
        )
        .where(
            member_turn,
            tables.member.c.workspace_id == workspace_id,
            tables.member.c.invited_at.is_not(None),
        )
        .scalar_subquery()
        .label("invited_chatted"),
    )
    async with workspace_tx() as connection:
        first = (await connection.execute(moments)).mappings().one()
    created_at = first["created"]
    if created_at is None:
        return
    steps = (
        (WORKSPACE_CREATED_STEP, created_at, NO_PROVIDER),
        (MEMBER_CHATTED_STEP, first["chatted"], NO_PROVIDER),
        (CONNECTOR_ATTACHED_STEP, first["connector"], first["connector_provider"] or NO_PROVIDER),
        (SURFACE_INSTALLED_STEP, first["surface"], first["surface_name"] or NO_PROVIDER),
        (TEAMMATE_INVITED_STEP, first["invited"], NO_PROVIDER),
        (APP_BUILT_STEP, first["app"], NO_PROVIDER),
        (INVITED_MEMBER_CHATTED_STEP, first["invited_chatted"], NO_PROVIDER),
    )
    since = now - timedelta(seconds=PRODUCT_CENSUS_SECONDS)
    for step, at, provider in steps:
        if at is None or _utc(at) <= since:
            continue
        _emit_step(step, STEP_COMPLETED, CENSUS_SURFACE, provider, _elapsed_ms(created_at, at))

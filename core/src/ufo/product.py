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
rather than the installation's."""

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from ufo.db import workspace_tx
from ufo.harness.o11y import emit_metric
from ufo.runtime.access.credentials import MEMBER_SLOT_INFIX
from ufo.runtime.workspace import ws_current
from ufo.schema import tables
from ufo.schema.records import MEMBER_ADMISSION

PRODUCT_CENSUS_JOB = "product_census"
PRODUCT_CENSUS_SECONDS = 600
PRODUCT_CENSUS_SCHEDULE = "0 */10 * * * *"
PRODUCT_STAGE_METRIC = "product_stage_total"
PRODUCT_ATTACH_METRIC = "product_attach_total"
ACTIVE_DAY_DAYS = 1
ACTIVE_WEEK_DAYS = 7
SURFACE_KIND = "surface"
ADDRESS_KIND = "address"
CREDENTIAL_KIND = "credential"
CONNECTOR_KIND = "connector"
APP_KIND = "app"


async def product_census() -> None:
    """Count the bound workspace's funnel stages and everything it has attached.

    One round trip each: the stages are scalar `EXISTS` subqueries selected together, and the
    attachments one union over the five columns that name them."""
    workspace_id = ws_current().workspace_id
    now = datetime.now(UTC)
    member_turn = sa.and_(
        tables.turn.c.workspace_id == workspace_id,
        tables.turn.c.admission_source == MEMBER_ADMISSION,
        tables.turn.c.parent_turn_id.is_(None),
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
        reached = (await connection.execute(stages)).mappings().one()
        holdings = (await connection.execute(attached)).all()
    for stage, arrived in reached.items():
        if arrived:
            emit_metric(PRODUCT_STAGE_METRIC, stage=stage)
    for holding in holdings:
        emit_metric(PRODUCT_ATTACH_METRIC, kind=holding.kind, name=holding.name)

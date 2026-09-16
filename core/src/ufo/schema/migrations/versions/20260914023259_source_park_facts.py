"""What a park is, beside when it last happened.

`parked_at` is the instant of the last refusal, and the driver rewrites it every hour a stream stays
refused. Anything that must act once per break cannot key on it. `parked_since` is the first refusal
still standing: it is written once and held until a successful read clears the whole park.

`parked_awaits_grant` is the fact only the raiser knew — whether a member re-granting is the one
repair. A throttle, a rate-limited org and a plan gate all park and all clear themselves, and
nothing else on the row tells them apart; `parked_reason` is the backend's own prose, which is not
a signal to branch on.

The row does say it in one place, and the backfill reads it there. `_skip` holds a refusal that
clears itself for `SOURCE_PARK_RETRY_SECONDS` and one that waits on a grant for
`SOURCE_PARK_HOLD_SECONDS`, so a parked row whose next sync is a year out is one only a member can
repair. Waking those rows instead would record nothing: the migrate job finishes before the fleet
rolls, so the outgoing image — which names neither column — serves the refusal and holds the row
for another year.

A break a member must repair is dated from this revision rather than from `parked_at`, which is the
last refusal and, for a park held a year, an instant long past every window a notice bounds itself
by. The break is still standing, and this is the first image that can say so. A park that clears
itself keeps `parked_at`, because its next hourly refusal rewrites `parked_since` anyway.
"""

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from alembic import op

revision: str = "20260914023259"
down_revision: str | None = "20260916050538"
branch_labels: str | None = None
depends_on: str | None = None

HELD_FOR_A_GRANT = timedelta(days=1)
"""How far out a parked row's next sync must sit for the park to be one only a grant lifts. It
separates an hour from a year, so the only rows it reads wrongly are a grant park already 364 days
old, and one a resync woke while it was parked — which is due now and re-parks in the new image's
own shape within the minute."""


def upgrade() -> None:
    op.add_column(
        "source",
        sa.Column("parked_since", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "source",
        sa.Column(
            "parked_awaits_grant",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    source = sa.table(
        "source",
        sa.column("parked_at", sa.DateTime(timezone=True)),
        sa.column("parked_since", sa.DateTime(timezone=True)),
        sa.column("parked_awaits_grant", sa.Boolean()),
        sa.column("next_sync_at", sa.DateTime(timezone=True)),
    )
    now = datetime.now(UTC)
    awaits_grant = source.c.next_sync_at > now + HELD_FOR_A_GRANT
    op.execute(
        source.update()
        .where(source.c.parked_at.is_not(None))
        .values(
            parked_awaits_grant=awaits_grant,
            parked_since=sa.case((awaits_grant, now), else_=source.c.parked_at),
        )
    )


def downgrade() -> None:
    op.drop_column("source", "parked_awaits_grant")
    op.drop_column("source", "parked_since")

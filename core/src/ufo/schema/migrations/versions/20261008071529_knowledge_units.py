"""Memory and sources units in the ledger.

`ledger_dimension` admits `writes`, `searches`, `pages` and `gib_months`, and
`ledger_service_dimension` admits the memory service its writes and searches and the sources
service its pages and GiB-months. No row changes, so the image this revision replaces reads every
row it leaves. The CHECKs are re-added `NOT VALID` and validated after the transaction commits, so
nothing scans the ledger under a lock its writers wait on. SQLite rebuilds the table, which drops
its triggers, so `ledger_fill_service` is read before the rebuild and created again after it.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20261008071529"
down_revision: str | None = "20261006230332"
branch_labels: str | None = None
depends_on: str | None = None

LEDGER_DIMENSION = (
    "dimension in ('tokens', 'egress', 'sandbox_tokens', 'images', 'videos', 'requests', 'gib', "
    "'writes', 'searches', 'pages', 'gib_months')"
)
LEDGER_SERVICE_DIMENSION = (
    "service is null or (service = 'models' and dimension in ('tokens', 'sandbox_tokens', "
    "'images', 'videos')) or (service = 'proxy' and dimension in ('egress', 'requests', 'gib')) "
    "or (service = 'memory' and dimension in ('writes', 'searches')) "
    "or (service = 'sources' and dimension in ('pages', 'gib_months'))"
)
DOWNGRADE_LEDGER_DIMENSION = (
    "dimension in ('tokens', 'egress', 'sandbox_tokens', 'images', 'videos', 'requests', 'gib')"
)
DOWNGRADE_LEDGER_SERVICE_DIMENSION = (
    "service is null or (service = 'models' and dimension in ('tokens', 'sandbox_tokens', "
    "'images', 'videos')) or (service = 'proxy' and dimension in ('egress', 'requests', 'gib'))"
)

LEDGER_TRIGGERS = sa.text(
    "select sql from sqlite_master where type = 'trigger' and tbl_name = 'ledger'"
)


def _checks(checks: tuple[tuple[str, str], ...]) -> None:
    bind = op.get_bind()
    triggers = (
        [] if bind.dialect.name == "postgresql" else bind.execute(LEDGER_TRIGGERS).scalars().all()
    )
    with op.batch_alter_table("ledger") as batch:
        for name, _condition in checks:
            batch.drop_constraint(name, type_="check")
        for name, condition in checks:
            batch.create_check_constraint(name, condition, postgresql_not_valid=True)
    for trigger in triggers:
        op.execute(trigger)
    if bind.dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        for name, _condition in checks:
            op.execute(f"alter table ledger validate constraint {name}")


def upgrade() -> None:
    _checks(
        (
            ("ledger_dimension", LEDGER_DIMENSION),
            ("ledger_service_dimension", LEDGER_SERVICE_DIMENSION),
        )
    )


def downgrade() -> None:
    _checks(
        (
            ("ledger_dimension", DOWNGRADE_LEDGER_DIMENSION),
            ("ledger_service_dimension", DOWNGRADE_LEDGER_SERVICE_DIMENSION),
        )
    )

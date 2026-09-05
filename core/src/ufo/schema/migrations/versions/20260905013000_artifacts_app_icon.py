"""the artifacts app takes the stacked-squares mark"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260905013000"
down_revision: str | None = "20260904230515"
branch_labels: str | None = None
depends_on: str | None = None

PROVISIONED_BY = "app_artifacts"
PROVISIONED_NAME = "artifacts"
WAS = "books"
NOW = "stack-2"


def _agent() -> sa.TableClause:
    return sa.table(
        "agent",
        sa.column("provisioned_by", sa.Text()),
        sa.column("provisioned_name", sa.Text()),
        sa.column("icon", sa.Text()),
    )


def _move(was: str, now: str) -> None:
    agent = _agent()
    op.execute(
        agent.update()
        .where(
            agent.c.provisioned_by == PROVISIONED_BY,
            agent.c.provisioned_name == PROVISIONED_NAME,
            agent.c.icon == was,
        )
        .values(icon=now)
    )


def upgrade() -> None:
    _move(WAS, NOW)


def downgrade() -> None:
    _move(NOW, WAS)

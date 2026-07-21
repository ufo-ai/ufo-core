"""chunk workspace scoping"""

from alembic import op

revision: str = "index_default_0002"
down_revision: str | None = "index_default_0001"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

ADD_WORKSPACE_ID = (
    "alter table chunk drop constraint chunk_pkey",
    "alter table chunk add column workspace_id uuid not null",
    "alter table chunk add primary key (workspace_id, chunk_digest)",
)
DROP_WORKSPACE_ID = (
    "alter table chunk drop constraint chunk_pkey",
    "alter table chunk drop column workspace_id",
    "alter table chunk add primary key (chunk_digest)",
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for statement in ADD_WORKSPACE_ID:
        op.execute(statement)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for statement in DROP_WORKSPACE_ID:
        op.execute(statement)

"""hosted site homepage binding"""

import sqlalchemy as sa
from alembic import op

revision: str = "sites_0003"
down_revision: str | None = "sites_0002"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("hosted_site", sa.Column("homepage_agent_id", sa.Uuid(), nullable=True))
    op.create_index(
        "hosted_site_homepage_agent",
        "hosted_site",
        ["workspace_id", "homepage_agent_id"],
        unique=True,
        postgresql_where=sa.text("homepage_agent_id is not null"),
        sqlite_where=sa.text("homepage_agent_id is not null"),
    )


def downgrade() -> None:
    op.drop_index("hosted_site_homepage_agent", "hosted_site")
    with op.batch_alter_table("hosted_site") as batch:
        batch.drop_column("homepage_agent_id")

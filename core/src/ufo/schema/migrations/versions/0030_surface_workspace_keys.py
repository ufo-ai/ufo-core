"""workspace-qualified surface delivery"""

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | None = None
depends_on: str | None = None

CONSTRAINT_NAMES = {
    "pk": "%(table_name)s_pkey",
    "uq": "%(table_name)s_%(column_0_N_name)s_key",
}


def upgrade() -> None:
    op.create_table(
        "surface_installation",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("surface", sa.Text(), nullable=False),
        sa.Column("installation_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("installation_id <> ''", name="surface_installation_id_nonempty"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
        sa.PrimaryKeyConstraint("workspace_id", "surface"),
        sa.UniqueConstraint(
            "surface",
            "installation_id",
            name="surface_installation_surface_installation_id_key",
        ),
    )
    with op.batch_alter_table("surface_identity", naming_convention=CONSTRAINT_NAMES) as batch:
        batch.drop_constraint("surface_identity_pkey", type_="primary")
        batch.create_primary_key(
            "surface_identity_pkey", ["workspace_id", "surface", "external_id"]
        )
    with op.batch_alter_table("conversation", naming_convention=CONSTRAINT_NAMES) as batch:
        batch.drop_constraint("conversation_surface_queue_key_key", type_="unique")
        batch.create_unique_constraint(
            "conversation_workspace_surface_queue_key_key",
            ["workspace_id", "surface", "queue_key"],
        )
    op.create_index(
        "writeback_due",
        "writeback",
        ["workspace_id", "created_at"],
        postgresql_where=sa.text("status in ('pending', 'claimed')"),
        sqlite_where=sa.text("status in ('pending', 'claimed')"),
    )


def downgrade() -> None:
    op.drop_index("writeback_due", table_name="writeback")
    with op.batch_alter_table("conversation", naming_convention=CONSTRAINT_NAMES) as batch:
        batch.drop_constraint("conversation_workspace_surface_queue_key_key", type_="unique")
        batch.create_unique_constraint(
            "conversation_surface_queue_key_key", ["surface", "queue_key"]
        )
    with op.batch_alter_table("surface_identity", naming_convention=CONSTRAINT_NAMES) as batch:
        batch.drop_constraint("surface_identity_pkey", type_="primary")
        batch.create_primary_key("surface_identity_pkey", ["surface", "external_id"])
    op.drop_table("surface_installation")

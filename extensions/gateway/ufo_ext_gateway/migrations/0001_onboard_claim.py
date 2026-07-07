"""onboard_claim"""

import sqlalchemy as sa
from alembic import op

revision: str = "gateway_0001"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = ("gateway",)
depends_on: str | None = "0001"


def upgrade() -> None:
    op.create_table(
        "onboard_claim",
        sa.Column("claim_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("email_domain", sa.Text(), nullable=False),
        sa.Column("code_hash", sa.Text(), nullable=False),
        sa.Column("surface", sa.Text(), nullable=False),
        sa.Column("surface_ref", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tenant_name", sa.Text(), nullable=True),
        sa.Column("resulting_workspace_id", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("claim_id"),
        sa.CheckConstraint(
            "attempts >= 0 and attempts <= max_attempts", name="onboard_claim_attempts"
        ),
    )
    op.create_index(
        "onboard_claim_live_session",
        "onboard_claim",
        ["workspace_id", "surface", "surface_ref"],
        unique=True,
        postgresql_where=sa.text("completed_at IS NULL"),
        sqlite_where=sa.text("completed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("onboard_claim_live_session", table_name="onboard_claim")
    op.drop_table("onboard_claim")

"""Separate reusable member scope from the exact request that granted it."""

import sqlalchemy as sa
from alembic import op

revision: str = "20260913193910"
down_revision: str | None = "20260913183147"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("member_authorization", sa.Column("scope_digest", sa.Text(), nullable=True))
    op.add_column("member_authorization", sa.Column("scope", sa.JSON(), nullable=True))
    op.add_column("member_authorization", sa.Column("binding_digest", sa.Text(), nullable=True))
    op.add_column("member_authorization", sa.Column("request_summary", sa.Text(), nullable=True))
    op.add_column("member_authorization", sa.Column("scope_summary", sa.Text(), nullable=True))
    op.add_column("member_permission", sa.Column("scope_digest", sa.Text(), nullable=True))
    op.add_column("member_permission", sa.Column("scope", sa.JSON(), nullable=True))
    with op.batch_alter_table("member_authorization") as batch:
        batch.create_check_constraint(
            "member_authorization_scope_pair",
            "(scope_digest is null) = (scope is null)",
        )
        batch.create_check_constraint(
            "member_authorization_binding_scope_pair",
            "(binding_digest is null) = (scope_digest is null)",
        )
    with op.batch_alter_table("member_permission") as batch:
        batch.create_check_constraint(
            "member_permission_scope_pair",
            "(scope_digest is null) = (scope is null)",
        )
    op.create_index(
        "member_permission_scope_identity",
        "member_permission",
        ["workspace_id", "member_id", "agent_id", "call", "scope_digest"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("member_permission_scope_identity", table_name="member_permission")
    with op.batch_alter_table("member_permission") as batch:
        batch.drop_constraint("member_permission_scope_pair", type_="check")
    with op.batch_alter_table("member_authorization") as batch:
        batch.drop_constraint("member_authorization_binding_scope_pair", type_="check")
        batch.drop_constraint("member_authorization_scope_pair", type_="check")
    op.drop_column("member_permission", "scope")
    op.drop_column("member_permission", "scope_digest")
    op.drop_column("member_authorization", "scope_summary")
    op.drop_column("member_authorization", "request_summary")
    op.drop_column("member_authorization", "binding_digest")
    op.drop_column("member_authorization", "scope")
    op.drop_column("member_authorization", "scope_digest")

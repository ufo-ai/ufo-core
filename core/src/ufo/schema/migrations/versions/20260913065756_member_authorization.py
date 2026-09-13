"""Member decisions over exact agent effects."""

import sqlalchemy as sa
from alembic import op

revision: str = "20260913065756"
down_revision: str | None = "20260913112228"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "member_authorization",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("call", sa.Text(), nullable=False),
        sa.Column("effect_digest", sa.Text(), nullable=False),
        sa.Column("effect", sa.JSON(), nullable=False),
        sa.Column("request_key", sa.Text(), nullable=False),
        sa.Column("decision_key", sa.Text(), nullable=True),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decision", sa.Text(), nullable=True),
        sa.Column("basis", sa.Text(), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("call <> ''", name="member_authorization_call_nonempty"),
        sa.CheckConstraint("request_key <> ''", name="member_authorization_request_key_nonempty"),
        sa.CheckConstraint(
            "decision is null or decision in ('allow', 'always', 'deny', 'revoke', 'superseded')",
            name="member_authorization_decision",
        ),
        sa.CheckConstraint(
            "(decision is null) = (decided_by is null) and "
            "(decision is null) = (decision_key is null) and "
            "(decision is null) = (basis is null) and "
            "(decision is null) = (evidence is null)",
            name="member_authorization_decided",
        ),
        sa.CheckConstraint(
            "basis is null or basis in ('selected_message', 'pending_answer', 'standing')",
            name="member_authorization_basis",
        ),
        sa.CheckConstraint("effect_digest <> ''", name="member_authorization_digest_nonempty"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "agent_id"],
            ["agent.workspace_id", "agent.id"],
            name="member_authorization_agent_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "conversation_id"],
            ["conversation.workspace_id", "conversation.id"],
            name="member_authorization_conversation_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "member_id"],
            ["member.workspace_id", "member.id"],
            name="member_authorization_member_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "decision_key",
            name="member_authorization_decision_key",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "request_key",
            name="member_authorization_request_key",
        ),
    )
    op.create_index(
        "member_authorization_pending",
        "member_authorization",
        ["workspace_id", "conversation_id", "member_id"],
        unique=True,
        postgresql_where=sa.text("decision is null"),
        sqlite_where=sa.text("decision is null"),
    )
    op.create_table(
        "member_permission",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("call", sa.Text(), nullable=False),
        sa.Column("effect_digest", sa.Text(), nullable=False),
        sa.Column("effect", sa.JSON(), nullable=False),
        sa.Column("granted_by", sa.Uuid(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("call <> ''", name="member_permission_call_nonempty"),
        sa.CheckConstraint("effect_digest <> ''", name="member_permission_digest_nonempty"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "agent_id"],
            ["agent.workspace_id", "agent.id"],
            name="member_permission_agent_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "member_id"],
            ["member.workspace_id", "member.id"],
            name="member_permission_member_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "member_id",
            "agent_id",
            "call",
            "effect_digest",
            name="member_permission_identity",
        ),
    )
    op.create_index(
        "member_permission_member",
        "member_permission",
        ["workspace_id", "member_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("member_permission_member", table_name="member_permission")
    op.drop_table("member_permission")
    op.drop_index("member_authorization_pending", table_name="member_authorization")
    op.drop_table("member_authorization")

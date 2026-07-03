"""The one schema, dialect-neutral: SQLite (dev) and Postgres (deploys) from one metadata."""

import sqlalchemy as sa

metadata = sa.MetaData()

workspace = sa.Table(
    "workspace",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)

member = sa.Table(
    "member",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=False),
    sa.Column("email", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("workspace_id", "email"),
)

surface_identity = sa.Table(
    "surface_identity",
    metadata,
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=False),
    sa.Column("member_id", sa.Uuid, sa.ForeignKey("member.id"), nullable=False),
    sa.Column("surface", sa.Text, primary_key=True),
    sa.Column("external_id", sa.Text, primary_key=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("surface in ('cli')", name="surface_identity_surface"),
)

agent = sa.Table(
    "agent",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("prompt", sa.Text, nullable=False),
    sa.Column("model", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("workspace_id", "name"),
)

conversation = sa.Table(
    "conversation",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=False),
    sa.Column("surface", sa.Text, nullable=False),
    sa.Column("queue_key", sa.Text, nullable=False),
    sa.Column("member_id", sa.Uuid, sa.ForeignKey("member.id"), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("surface", "queue_key"),
    sa.CheckConstraint("surface in ('cli')", name="conversation_surface"),
)

turn = sa.Table(
    "turn",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=False),
    sa.Column("conversation_id", sa.Uuid, sa.ForeignKey("conversation.id"), nullable=False),
    sa.Column("agent_id", sa.Uuid, sa.ForeignKey("agent.id"), nullable=False),
    sa.Column("seq", sa.Integer, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("inbound", sa.Text, nullable=False),
    sa.Column("terminal", sa.JSON(none_as_null=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("conversation_id", "seq"),
    sa.CheckConstraint("seq >= 1", name="turn_seq"),
    sa.CheckConstraint(
        "status in ('queued', 'running', 'done', 'failed', 'cancelled')", name="turn_status"
    ),
    sa.CheckConstraint(
        "(status in ('queued', 'running')) = (terminal is null)", name="turn_terminal"
    ),
)

ledger = sa.Table(
    "ledger",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("turn_id", sa.Uuid, sa.ForeignKey("turn.id"), nullable=False),
    sa.Column("dimension", sa.Text, nullable=False),
    sa.Column("amount", sa.BigInteger, nullable=False),
    sa.Column("priced_micro_usd", sa.BigInteger, nullable=False),
    sa.Column("model", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("dimension in ('tokens')", name="ledger_dimension"),
    sa.CheckConstraint("amount > 0", name="ledger_amount"),
    sa.CheckConstraint("priced_micro_usd >= 0", name="ledger_priced"),
    sa.Index("ledger_turn", "turn_id"),
)

credential = sa.Table(
    "credential",
    metadata,
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), primary_key=True),
    sa.Column("slot", sa.Text, primary_key=True),
    sa.Column("ciphertext", sa.LargeBinary, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)

ext_store = sa.Table(
    "ext_store",
    metadata,
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), primary_key=True),
    sa.Column("extension", sa.Text, primary_key=True),
    sa.Column("key", sa.Text, primary_key=True),
    sa.Column("value", sa.JSON, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)

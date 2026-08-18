"""The one schema, dialect-neutral: SQLite (dev) and Postgres (deploys) from one metadata."""

from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.engine.default import DefaultExecutionContext

from ufo.audience import conversation_audience
from ufo.schema.records import DEFAULT_AGENT_ICON


def _conversation_audience(context: DefaultExecutionContext) -> str:
    return str(conversation_audience(context.get_current_parameters().get("member_id")))


metadata = sa.MetaData()

workspace = sa.Table(
    "workspace",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("page_revision", sa.BigInteger, nullable=False, server_default="0"),
    sa.Column("egress_rules_generation", sa.BigInteger, nullable=False, server_default="0"),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)

member = sa.Table(
    "member",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=False),
    sa.Column("email", sa.Text, nullable=False),
    sa.Column("is_admin", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("seated_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
    sa.Column("timezone", sa.Text, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("workspace_id", "email"),
    sa.UniqueConstraint("workspace_id", "id", name="member_workspace_identity"),
)

sa.Index("member_email", member.c.email)

surface_identity = sa.Table(
    "surface_identity",
    metadata,
    sa.Column(
        "workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), primary_key=True, nullable=False
    ),
    sa.Column("member_id", sa.Uuid, sa.ForeignKey("member.id"), nullable=False),
    sa.Column("surface", sa.Text, primary_key=True),
    sa.Column("external_id", sa.Text, primary_key=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)

surface_installation = sa.Table(
    "surface_installation",
    metadata,
    sa.Column(
        "workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), primary_key=True, nullable=False
    ),
    sa.Column("surface", sa.Text, primary_key=True),
    sa.Column("installation_id", sa.Text, nullable=False),
    sa.Column("agent_id", sa.Uuid, sa.ForeignKey("agent.id"), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint(
        "surface",
        "installation_id",
        name="surface_installation_surface_installation_id_key",
    ),
    sa.CheckConstraint("installation_id <> ''", name="surface_installation_id_nonempty"),
)

agent = sa.Table(
    "agent",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("icon", sa.Text, nullable=False, server_default=sa.text(f"'{DEFAULT_AGENT_ICON}'")),
    sa.Column("prompt", sa.Text, nullable=False),
    sa.Column("model", sa.Text, nullable=False),
    sa.Column("reasoning", sa.Text, nullable=False, server_default=sa.text("'auto'")),
    sa.Column("is_main", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("visibility", sa.Text, nullable=False, server_default=sa.text("'private'")),
    sa.Column("internet_access_allowed", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("sandbox_size", sa.Text, nullable=False, server_default=sa.text("'small'")),
    sa.Column("tools", sa.JSON, nullable=True),
    sa.Column("input_schema", sa.JSON(none_as_null=True), nullable=True),
    sa.Column("output_schema", sa.JSON(none_as_null=True), nullable=True),
    sa.Column("owner_member_id", sa.Uuid, nullable=True),
    sa.Column("provisioned_by", sa.Text, nullable=True),
    sa.Column("provisioned_name", sa.Text, nullable=True),
    sa.Column("provisioned_version", sa.Text, nullable=True),
    sa.Column("setup", sa.JSON, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "reasoning in ('auto', 'off', 'low', 'medium', 'high')", name="agent_reasoning"
    ),
    sa.CheckConstraint("sandbox_size in ('small', 'medium', 'large')", name="agent_sandbox_size"),
    sa.CheckConstraint("visibility in ('private', 'workspace')", name="agent_visibility"),
    sa.CheckConstraint(
        "(provisioned_by is null) = (provisioned_name is null) "
        "and (provisioned_by is null) = (provisioned_version is null)",
        name="agent_provenance",
    ),
    sa.UniqueConstraint(
        "workspace_id", "provisioned_by", "provisioned_name", name="agent_provision_identity"
    ),
    sa.UniqueConstraint("workspace_id", "name"),
    sa.UniqueConstraint("workspace_id", "id", name="agent_workspace_identity"),
    sa.Index(
        "agent_workspace_main",
        "workspace_id",
        unique=True,
        postgresql_where=sa.text("is_main"),
        sqlite_where=sa.text("is_main"),
    ),
)

conversation = sa.Table(
    "conversation",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=False),
    sa.Column("agent_id", sa.Uuid, sa.ForeignKey("agent.id"), nullable=False),
    sa.Column("surface", sa.Text, nullable=False),
    sa.Column("queue_key", sa.Text, nullable=False),
    sa.Column("surface_label", sa.Text, nullable=True),
    sa.Column("title", sa.Text, nullable=True),
    sa.Column("title_summarized", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("member_id", sa.Uuid, sa.ForeignKey("member.id"), nullable=True),
    sa.Column(
        "audience",
        sa.Text,
        nullable=False,
        default=_conversation_audience,
        server_default="shared",
    ),
    sa.Column("sandbox_conversation_id", sa.Uuid, nullable=True),
    sa.Column("sandbox_handle", sa.Text, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint(
        "workspace_id",
        "surface",
        "queue_key",
        name="conversation_workspace_surface_queue_key_key",
    ),
    sa.UniqueConstraint("workspace_id", "id", name="conversation_workspace_identity"),
    sa.Index("conversation_workspace", "workspace_id"),
    sa.CheckConstraint(
        "audience = 'shared' or audience like 'member:%' or "
        "audience like 'room:%:%' or audience like 'foreign:%:%'",
        name="conversation_audience",
    ),
    sa.CheckConstraint(
        "(member_id is null and audience not like 'member:%') or "
        "(member_id is not null and audience like 'member:%')",
        name="conversation_audience_member",
    ),
    sa.Index(
        "conversation_sandbox",
        "workspace_id",
        postgresql_where=sa.text("sandbox_handle is not null"),
        sqlite_where=sa.text("sandbox_handle is not null"),
    ),
    sa.Index(
        "conversation_awaiting_title",
        "workspace_id",
        postgresql_where=sa.text("not title_summarized"),
        sqlite_where=sa.text("not title_summarized"),
    ),
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
    sa.Column("admission_source", sa.Text, nullable=False, server_default="internal"),
    sa.Column("speaker_member_id", sa.Uuid, sa.ForeignKey("member.id"), nullable=True),
    sa.Column("on_behalf_of_member_id", sa.Uuid, sa.ForeignKey("member.id"), nullable=True),
    sa.Column("connect_authorization_url", sa.Text, nullable=True),
    sa.Column("connect_authorized_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("context", sa.JSON(none_as_null=True), nullable=True),
    sa.Column("terminal", sa.JSON(none_as_null=True), nullable=True),
    sa.Column("parent_turn_id", sa.Uuid, nullable=True),
    sa.Column("subagent_profile", sa.Text, nullable=True),
    sa.Column("subagent_name", sa.Text, nullable=True),
    sa.Column("byok", sa.Boolean, nullable=True),
    sa.Column("byok_attempt", sa.Text, nullable=True),
    sa.Column("result_delivery", sa.Text, nullable=True),
    sa.Column("traceparent", sa.Text, nullable=True),
    sa.Column("idempotency_key", sa.Text, nullable=True),
    sa.Column("running_attempt", sa.Text, nullable=True),
    sa.Column("dispatch_enqueued_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("conversation_id", "seq"),
    sa.CheckConstraint("seq >= 1", name="turn_seq"),
    sa.CheckConstraint(
        "status in ('queued', 'running', 'parked', 'done', 'failed', 'cancelled')",
        name="turn_status",
    ),
    sa.CheckConstraint(
        "admission_source in ('member', 'internal', 'scheduled', 'intent')",
        name="turn_admission_source",
    ),
    sa.CheckConstraint(
        "(status in ('queued', 'running', 'parked')) = (terminal is null)", name="turn_terminal"
    ),
    sa.CheckConstraint(
        "(connect_authorization_url is null) = (connect_authorized_at is null)",
        name="turn_connect_authorization",
    ),
    sa.CheckConstraint("result_delivery in ('pending', 'delivered')", name="turn_result_delivery"),
    sa.Index("turn_idempotency_key", "workspace_id", "idempotency_key", unique=True),
    sa.Index("turn_conversation_activity", "conversation_id", "updated_at"),
    sa.Index(
        "turn_parked",
        "workspace_id",
        postgresql_where=sa.text("status = 'parked'"),
        sqlite_where=sa.text("status = 'parked'"),
    ),
    sa.Index(
        "turn_spoken",
        "workspace_id",
        "conversation_id",
        "speaker_member_id",
        postgresql_where=sa.text("speaker_member_id is not null"),
        sqlite_where=sa.text("speaker_member_id is not null"),
    ),
    sa.Index(
        "turn_parent",
        "parent_turn_id",
        postgresql_where=sa.text("parent_turn_id is not null"),
        sqlite_where=sa.text("parent_turn_id is not null"),
    ),
    sa.Index(
        "turn_result_pending",
        "workspace_id",
        postgresql_where=sa.text("result_delivery = 'pending'"),
        sqlite_where=sa.text("result_delivery = 'pending'"),
    ),
)

inbound_message = sa.Table(
    "inbound_message",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=False),
    sa.Column("conversation_id", sa.Uuid, sa.ForeignKey("conversation.id"), nullable=False),
    sa.Column("seq", sa.Integer, nullable=False),
    sa.Column("body", sa.Text, nullable=False),
    sa.Column("admission_source", sa.Text, nullable=False),
    sa.Column("context", sa.JSON(none_as_null=True), nullable=True),
    sa.Column("speaker_member_id", sa.Uuid, sa.ForeignKey("member.id"), nullable=True),
    sa.Column("idempotency_key", sa.Text, nullable=True),
    sa.Column("admitted_turn_id", sa.Uuid, sa.ForeignKey("turn.id"), nullable=False),
    sa.Column("consumed_turn_id", sa.Uuid, sa.ForeignKey("turn.id"), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("conversation_id", "seq"),
    sa.CheckConstraint(
        "admission_source in ('member', 'internal')", name="inbound_message_admission_source"
    ),
    sa.Index("inbound_message_idempotency_key", "workspace_id", "idempotency_key", unique=True),
    sa.Index(
        "inbound_message_pending",
        "conversation_id",
        postgresql_where=sa.text("consumed_turn_id is null"),
        sqlite_where=sa.text("consumed_turn_id is null"),
    ),
)

ledger = sa.Table(
    "ledger",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("turn_id", sa.Uuid, sa.ForeignKey("turn.id"), nullable=True),
    sa.Column("dimension", sa.Text, nullable=False),
    sa.Column("amount", sa.BigInteger, nullable=False),
    sa.Column("prompt_tokens", sa.BigInteger, nullable=False, server_default=sa.text("0")),
    sa.Column("input_tokens", sa.BigInteger, nullable=False, server_default=sa.text("0")),
    sa.Column("output_tokens", sa.BigInteger, nullable=False, server_default=sa.text("0")),
    sa.Column("cache_read_tokens", sa.BigInteger, nullable=False, server_default=sa.text("0")),
    sa.Column("cache_write_5m_tokens", sa.BigInteger, nullable=False, server_default=sa.text("0")),
    sa.Column("cache_write_30m_tokens", sa.BigInteger, nullable=False, server_default=sa.text("0")),
    sa.Column("cache_write_1h_tokens", sa.BigInteger, nullable=False, server_default=sa.text("0")),
    sa.Column("byok", sa.Boolean, nullable=True),
    sa.Column("token_classes_complete", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("priced_micro_usd", sa.BigInteger, nullable=False),
    sa.Column("debited_micro_usd", sa.BigInteger, nullable=False, server_default=sa.text("0")),
    sa.Column("model", sa.Text, nullable=False),
    sa.Column("price_digest", sa.Text, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "dimension in ('tokens', 'egress', 'sandbox_tokens', 'images', 'videos')",
        name="ledger_dimension",
    ),
    sa.CheckConstraint("amount > 0", name="ledger_amount"),
    sa.CheckConstraint("priced_micro_usd >= 0", name="ledger_priced"),
    sa.CheckConstraint(
        "input_tokens >= 0 and output_tokens >= 0 and cache_read_tokens >= 0 "
        "and cache_write_5m_tokens >= 0 and cache_write_30m_tokens >= 0 "
        "and cache_write_1h_tokens >= 0",
        name="ledger_token_classes_nonnegative",
    ),
    sa.CheckConstraint(
        "dimension not in ('tokens', 'sandbox_tokens') or amount = input_tokens + output_tokens "
        "+ cache_read_tokens + cache_write_5m_tokens + cache_write_30m_tokens "
        "+ cache_write_1h_tokens",
        name="ledger_token_classes_total",
    ),
    sa.CheckConstraint(
        "dimension not in ('tokens', 'sandbox_tokens') or prompt_tokens = input_tokens "
        "+ cache_read_tokens + cache_write_5m_tokens + cache_write_30m_tokens "
        "+ cache_write_1h_tokens",
        name="ledger_prompt_classes_total",
    ),
    sa.CheckConstraint("not byok or dimension = 'tokens'", name="ledger_byok_dimension"),
    sa.Index("ledger_turn", "turn_id"),
    sa.Index("ledger_workspace_created", "workspace_id", "created_at"),
)

ledger_export = sa.Table(
    "ledger_export",
    metadata,
    sa.Column("consumer", sa.Text, nullable=False),
    sa.Column("ledger_id", sa.Uuid, sa.ForeignKey("ledger.id"), nullable=False),
    sa.Column("from_amount", sa.BigInteger, nullable=False),
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("to_amount", sa.BigInteger, nullable=False),
    sa.Column("from_micro_usd", sa.BigInteger, nullable=False),
    sa.Column("to_micro_usd", sa.BigInteger, nullable=False),
    sa.Column("byok", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("acked_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint("consumer", "ledger_id", "from_amount"),
    sa.CheckConstraint("to_amount > from_amount", name="ledger_export_delta"),
    sa.Index(
        "ledger_export_pending",
        "consumer",
        "workspace_id",
        postgresql_where=sa.text("acked_at is null"),
        sqlite_where=sa.text("acked_at is null"),
    ),
)

spend_cap = sa.Table(
    "spend_cap",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=False),
    sa.Column("scope", sa.Text, nullable=False),
    sa.Column("subject_id", sa.Uuid, nullable=True),
    sa.Column("window_seconds", sa.Integer, nullable=False),
    sa.Column("limit_micro_usd", sa.BigInteger, nullable=False),
    sa.Column("on_breach", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("scope in ('workspace', 'member', 'agent')", name="spend_cap_scope"),
    sa.CheckConstraint("(scope = 'workspace') = (subject_id is null)", name="spend_cap_subject"),
    sa.CheckConstraint("window_seconds > 0", name="spend_cap_window"),
    sa.CheckConstraint("limit_micro_usd > 0", name="spend_cap_limit"),
    sa.CheckConstraint("on_breach in ('park', 'reject')", name="spend_cap_on_breach"),
    sa.UniqueConstraint(
        "workspace_id", "scope", "subject_id", "window_seconds", name="spend_cap_identity"
    ),
    sa.Index("spend_cap_workspace", "workspace_id"),
)

balance_purchase = sa.Table(
    "balance_purchase",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=False),
    sa.Column("granted_micro_usd", sa.BigInteger, nullable=False),
    sa.Column("charged_micro_usd", sa.BigInteger, nullable=False),
    sa.Column("reference", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("granted_micro_usd <> 0", name="balance_purchase_granted"),
    sa.UniqueConstraint("workspace_id", "reference", name="balance_purchase_reference"),
    sa.Index("balance_purchase_workspace", "workspace_id"),
)

workspace_balance = sa.Table(
    "workspace_balance",
    metadata,
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), primary_key=True),
    sa.Column("balance_micro_usd", sa.BigInteger, nullable=False),
    sa.Column("reserve_micro_usd", sa.BigInteger, nullable=False, server_default=sa.text("0")),
    sa.Column("auto_topup_micro_usd", sa.BigInteger, nullable=True),
    sa.Column("auto_topup_threshold_micro_usd", sa.BigInteger, nullable=True),
    sa.Column("topup_verified_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
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

connection = sa.Table(
    "connection",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=False),
    sa.Column("provider", sa.Text, nullable=False),
    sa.Column("account_id", sa.Text, nullable=False),
    sa.Column("host", sa.Text, nullable=False),
    sa.Column("owner_member_id", sa.Uuid, nullable=False),
    sa.Column("conversation_id", sa.Uuid, nullable=False),
    sa.Column("shared", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("account_label", sa.Text, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("workspace_id", "provider", "account_id", name="connection_identity"),
    sa.UniqueConstraint("workspace_id", "id", name="connection_workspace_identity"),
    sa.UniqueConstraint(
        "workspace_id",
        "id",
        "owner_member_id",
        name="connection_owner_identity",
    ),
    sa.ForeignKeyConstraint(
        ["workspace_id", "owner_member_id"],
        ["member.workspace_id", "member.id"],
    ),
    sa.ForeignKeyConstraint(
        ["workspace_id", "conversation_id"],
        ["conversation.workspace_id", "conversation.id"],
    ),
)

connector_grant = sa.Table(
    "connector_grant",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=False),
    sa.Column("agent_id", sa.Uuid, nullable=False),
    sa.Column("connection_id", sa.Uuid, nullable=False),
    sa.Column("conversation_id", sa.Uuid, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint(
        "workspace_id", "agent_id", "connection_id", name="connector_grant_identity"
    ),
    sa.ForeignKeyConstraint(
        ["workspace_id", "connection_id"],
        ["connection.workspace_id", "connection.id"],
        ondelete="CASCADE",
    ),
    sa.ForeignKeyConstraint(
        ["workspace_id", "agent_id"],
        ["agent.workspace_id", "agent.id"],
    ),
    sa.ForeignKeyConstraint(
        ["workspace_id", "conversation_id"],
        ["conversation.workspace_id", "conversation.id"],
    ),
)

proposal = sa.Table(
    "proposal",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=False),
    sa.Column("agent_id", sa.Uuid, sa.ForeignKey("agent.id"), nullable=False),
    sa.Column("extension", sa.Text, nullable=False),
    sa.Column("from_digest", sa.Text, nullable=False),
    sa.Column("to_digest", sa.Text, nullable=False),
    sa.Column("body", sa.JSON, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status in ('pending', 'approved', 'rejected')", name="proposal_status"),
)

writeback = sa.Table(
    "writeback",
    metadata,
    sa.Column("turn_id", sa.Uuid, sa.ForeignKey("turn.id"), primary_key=True),
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("reply_ref", sa.Text, nullable=True),
    sa.Column("claimed_by", sa.Text, nullable=True),
    sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("last_error", sa.Text, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "status in ('pending', 'claimed', 'delivered', 'failed')", name="writeback_status"
    ),
    sa.Index(
        "writeback_due",
        "workspace_id",
        "created_at",
        postgresql_where=sa.text("status in ('pending', 'claimed')"),
        sqlite_where=sa.text("status in ('pending', 'claimed')"),
    ),
)

mid_turn_reply = sa.Table(
    "mid_turn_reply",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=False),
    sa.Column("turn_id", sa.Uuid, sa.ForeignKey("turn.id"), nullable=False),
    sa.Column("round_index", sa.Integer, nullable=False),
    sa.Column("span_index", sa.Integer, nullable=False),
    sa.Column("message_ref", sa.Uuid, nullable=True),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("reply_ref", sa.Text, nullable=True),
    sa.Column("claimed_by", sa.Text, nullable=True),
    sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("last_error", sa.Text, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "status in ('pending', 'claimed', 'delivered', 'failed')", name="mid_turn_reply_status"
    ),
    sa.Index(
        "mid_turn_reply_due",
        "workspace_id",
        "created_at",
        postgresql_where=sa.text("status in ('pending', 'claimed')"),
        sqlite_where=sa.text("status in ('pending', 'claimed')"),
    ),
)

shared_artifact = sa.Table(
    "shared_artifact",
    metadata,
    sa.Column("turn_id", sa.Uuid, sa.ForeignKey("turn.id"), primary_key=True),
    sa.Column("blob_key", sa.Text, primary_key=True),
    sa.Column("id", sa.Uuid, nullable=False, unique=True, default=uuid4),
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=False),
    sa.Column("filename", sa.Text, nullable=False),
    sa.Column("subject", sa.Text, nullable=True),
    sa.Column("media_type", sa.Text, nullable=False),
    sa.Column("size_bytes", sa.BigInteger, nullable=False),
    sa.Column("preview_blob_key", sa.Text, nullable=True),
    sa.Column("preview_media_type", sa.Text, nullable=True),
    sa.Column("preview_size_bytes", sa.BigInteger, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("size_bytes >= 0", name="shared_artifact_size"),
    sa.CheckConstraint(
        "(preview_blob_key IS NULL) = (preview_media_type IS NULL) "
        "AND (preview_blob_key IS NULL) = (preview_size_bytes IS NULL) "
        "AND (preview_size_bytes IS NULL OR preview_size_bytes >= 0)",
        name="shared_artifact_preview",
    ),
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
    sa.Index("ext_store_key", "extension", "key"),
)

runtime_instance = sa.Table(
    "runtime_instance",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=True),
    sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Index("runtime_instance_live", "workspace_id", "heartbeat_at"),
)

surface_listener_claim = sa.Table(
    "surface_listener_claim",
    metadata,
    sa.Column("surface", sa.Text, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=True),
    sa.Column(
        "owner_id",
        sa.Uuid,
        sa.ForeignKey("runtime_instance.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("owner_token", sa.Uuid, nullable=False),
    sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("surface <> ''", name="surface_listener_claim_surface_nonempty"),
)

source = sa.Table(
    "source",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=False),
    sa.Column("backend", sa.Text, nullable=False),
    sa.Column("config", sa.JSON, nullable=False),
    sa.Column("subject", sa.Text, nullable=False, server_default="shared"),
    sa.Column("owner_member_id", sa.Uuid, nullable=True),
    sa.Column("connection_id", sa.Uuid, nullable=True),
    sa.Column("cursor", sa.Text, nullable=True),
    sa.Column("next_sync_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("consecutive_errors", sa.Integer, nullable=False, server_default="0"),
    sa.Column("claimed_by", sa.Text, nullable=True),
    sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Index("source_due", "next_sync_at"),
    sa.UniqueConstraint("workspace_id", "id", name="source_workspace_identity"),
    sa.CheckConstraint("subject = 'shared' or subject like 'member:%'", name="source_subject"),
    sa.CheckConstraint(
        "connection_id is null or owner_member_id is not null",
        name="source_connection_owner",
    ),
    sa.ForeignKeyConstraint(
        ["workspace_id", "owner_member_id"],
        ["member.workspace_id", "member.id"],
    ),
    sa.ForeignKeyConstraint(
        ["workspace_id", "connection_id", "owner_member_id"],
        ["connection.workspace_id", "connection.id", "connection.owner_member_id"],
    ),
)

source_grant = sa.Table(
    "source_grant",
    metadata,
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), primary_key=True),
    sa.Column("source_id", sa.Uuid, primary_key=True),
    sa.Column("agent_id", sa.Uuid, primary_key=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(
        ["workspace_id", "source_id"],
        ["source.workspace_id", "source.id"],
        ondelete="CASCADE",
    ),
    sa.ForeignKeyConstraint(
        ["workspace_id", "agent_id"],
        ["agent.workspace_id", "agent.id"],
    ),
)

transcript_access = sa.Table(
    "transcript_access",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=False),
    sa.Column("conversation_id", sa.Uuid, nullable=False),
    sa.Column("reader_member_id", sa.Uuid, nullable=False),
    sa.Column("subject_member_id", sa.Uuid, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(
        ["workspace_id", "conversation_id"],
        ["conversation.workspace_id", "conversation.id"],
    ),
    sa.ForeignKeyConstraint(
        ["workspace_id", "reader_member_id"],
        ["member.workspace_id", "member.id"],
    ),
    sa.ForeignKeyConstraint(
        ["workspace_id", "subject_member_id"],
        ["member.workspace_id", "member.id"],
    ),
    sa.Index("transcript_access_conversation", "workspace_id", "conversation_id"),
)

page = sa.Table(
    "page",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=False),
    sa.Column("source_id", sa.Uuid, sa.ForeignKey("source.id"), nullable=False),
    sa.Column("digest", sa.Text, nullable=False),
    sa.Column("body_ref", sa.Text, nullable=False),
    sa.Column("stream", sa.Text, nullable=False),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("record_created_at", sa.Text, nullable=True),
    sa.Column("record_updated_at", sa.Text, nullable=True),
    sa.Column("subject", sa.Text, nullable=False),
    sa.Column("revision", sa.BigInteger, nullable=False, server_default="0"),
    sa.Column("tombstone", sa.Boolean, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("subject = 'shared' or subject like 'member:%'", name="page_subject"),
    sa.Index("page_feed", "workspace_id", "revision", "id"),
    sa.Index("page_source", "source_id"),
)

conversation_change = sa.Table(
    "conversation_change",
    metadata,
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=False),
    sa.Column("conversation_id", sa.Uuid, nullable=False),
    sa.Column("scan", sa.JSON(none_as_null=True), nullable=False),
    sa.ForeignKeyConstraint(
        ["workspace_id", "conversation_id"],
        ["conversation.workspace_id", "conversation.id"],
        ondelete="CASCADE",
    ),
    sa.PrimaryKeyConstraint("workspace_id", "conversation_id"),
)

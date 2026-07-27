"""The one schema, dialect-neutral: SQLite (dev) and Postgres (deploys) from one metadata."""

import sqlalchemy as sa

metadata = sa.MetaData()

workspace = sa.Table(
    "workspace",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("seat_limit", sa.Integer, nullable=True),
    sa.Column("included_seats", sa.Integer, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("seat_limit is null or seat_limit > 0", name="workspace_seat_limit"),
    sa.CheckConstraint(
        "included_seats is null or included_seats > 0", name="workspace_included_seats"
    ),
)

member = sa.Table(
    "member",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=False),
    sa.Column("email", sa.Text, nullable=False),
    sa.Column("seated_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("workspace_id", "email"),
)

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
    sa.Column("prompt", sa.Text, nullable=False),
    sa.Column("model", sa.Text, nullable=False),
    sa.Column("internet_access_allowed", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("workspace_id", "name"),
)

conversation = sa.Table(
    "conversation",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=False),
    sa.Column("agent_id", sa.Uuid, sa.ForeignKey("agent.id"), nullable=False),
    sa.Column("surface", sa.Text, nullable=False),
    sa.Column("queue_key", sa.Text, nullable=False),
    sa.Column("member_id", sa.Uuid, sa.ForeignKey("member.id"), nullable=True),
    sa.Column("sandbox_handle", sa.Text, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint(
        "workspace_id",
        "surface",
        "queue_key",
        name="conversation_workspace_surface_queue_key_key",
    ),
    sa.Index("conversation_workspace", "workspace_id"),
    sa.Index(
        "conversation_sandbox",
        "workspace_id",
        postgresql_where=sa.text("sandbox_handle is not null"),
        sqlite_where=sa.text("sandbox_handle is not null"),
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
        "admission_source in ('member', 'internal', 'scheduled')", name="turn_admission_source"
    ),
    sa.CheckConstraint(
        "(status in ('queued', 'running', 'parked')) = (terminal is null)", name="turn_terminal"
    ),
    sa.CheckConstraint(
        "(connect_authorization_url is null) = (connect_authorized_at is null)",
        name="turn_connect_authorization",
    ),
    sa.Index("turn_idempotency_key", "workspace_id", "idempotency_key", unique=True),
    sa.Index("turn_conversation_activity", "conversation_id", "updated_at"),
    sa.Index(
        "turn_parked",
        "workspace_id",
        postgresql_where=sa.text("status = 'parked'"),
        sqlite_where=sa.text("status = 'parked'"),
    ),
    sa.Index(
        "turn_parent",
        "parent_turn_id",
        postgresql_where=sa.text("parent_turn_id is not null"),
        sqlite_where=sa.text("parent_turn_id is not null"),
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
    sa.Column("priced_micro_usd", sa.BigInteger, nullable=False),
    sa.Column("model", sa.Text, nullable=False),
    sa.Column("price_digest", sa.Text, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "dimension in ('tokens', 'egress', 'sandbox_tokens')", name="ledger_dimension"
    ),
    sa.CheckConstraint("amount > 0", name="ledger_amount"),
    sa.CheckConstraint("priced_micro_usd >= 0", name="ledger_priced"),
    sa.Index("ledger_turn", "turn_id"),
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

credential = sa.Table(
    "credential",
    metadata,
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), primary_key=True),
    sa.Column("slot", sa.Text, primary_key=True),
    sa.Column("ciphertext", sa.LargeBinary, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)

grant = sa.Table(
    "grant",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=False),
    sa.Column("agent_id", sa.Uuid, sa.ForeignKey("agent.id"), nullable=False),
    sa.Column("provider", sa.Text, nullable=False),
    sa.Column("account_id", sa.Text, nullable=False),
    sa.Column("host", sa.Text, nullable=False),
    sa.Column("grantor_member_id", sa.Uuid, sa.ForeignKey("member.id"), nullable=False),
    sa.Column("conversation_id", sa.Uuid, sa.ForeignKey("conversation.id"), nullable=False),
    sa.Column("shared", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint(
        "workspace_id", "agent_id", "provider", "account_id", name="grant_identity"
    ),
    sa.Index("grant_workspace", "workspace_id"),
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

shared_artifact = sa.Table(
    "shared_artifact",
    metadata,
    sa.Column("turn_id", sa.Uuid, sa.ForeignKey("turn.id"), primary_key=True),
    sa.Column("blob_key", sa.Text, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=False),
    sa.Column("filename", sa.Text, nullable=False),
    sa.Column("subject", sa.Text, nullable=True),
    sa.Column("media_type", sa.Text, nullable=False),
    sa.Column("size_bytes", sa.BigInteger, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("size_bytes >= 0", name="shared_artifact_size"),
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

source = sa.Table(
    "source",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=False),
    sa.Column("backend", sa.Text, nullable=False),
    sa.Column("config", sa.JSON, nullable=False),
    sa.Column("subject", sa.Text, nullable=False, server_default="shared"),
    sa.Column("owner_member_id", sa.Uuid, sa.ForeignKey("member.id"), nullable=True),
    sa.Column("cursor", sa.Text, nullable=True),
    sa.Column("next_sync_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("consecutive_errors", sa.Integer, nullable=False, server_default="0"),
    sa.Column("claimed_by", sa.Text, nullable=True),
    sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Index("source_due", "next_sync_at"),
    sa.CheckConstraint("subject = 'shared' or subject like 'member:%'", name="source_subject"),
)

scheduled_task = sa.Table(
    "scheduled_task",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=False),
    sa.Column("conversation_id", sa.Uuid, sa.ForeignKey("conversation.id"), nullable=False),
    sa.Column("agent_id", sa.Uuid, sa.ForeignKey("agent.id"), nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("created_by_member_id", sa.Uuid, sa.ForeignKey("member.id"), nullable=True),
    sa.Column("schedule", sa.Text, nullable=False),
    sa.Column("prompt", sa.Text, nullable=False),
    sa.Column("description", sa.Text, nullable=False),
    sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("origin_seq", sa.Integer, nullable=True),
    sa.Column("resume_turn_id", sa.Uuid, nullable=True),
    sa.Column("last_turn_id", sa.Uuid, nullable=True),
    sa.Column("claimed_by", sa.Text, nullable=True),
    sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("workspace_id", "name", name="scheduled_task_name"),
    sa.Index("scheduled_task_due", "next_run_at"),
    sa.Index(
        "scheduled_task_pause",
        "workspace_id",
        "conversation_id",
        unique=True,
        postgresql_where=sa.text("schedule = '@once'"),
        sqlite_where=sa.text("schedule = '@once'"),
    ),
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
    sa.Column("tombstone", sa.Boolean, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("subject = 'shared' or subject like 'member:%'", name="page_subject"),
    sa.Index("page_feed", "workspace_id", "updated_at", "id"),
    sa.Index("page_source", "source_id"),
)

"""coding tools"""

import hashlib
import json
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "coding_0004"
down_revision: str | None = "coding_0003"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

REVIEW_QUEUE_PREFIX = "code-review:"
SOURCES_SURFACE = "sources"
PULL_REQUESTS_STREAM = "pull_requests"


def _binding_name(provider: str, config: object) -> str:
    if not isinstance(config, dict):
        raise ValueError("a code review source config must be an object")
    match (config.get("account"), config.get("stream"), config.get("base_url")):
        case (str(account), str(stream), None) if stream == PULL_REQUESTS_STREAM:
            base_url = None
        case (str(account), str(stream), str(base_url)) if stream == PULL_REQUESTS_STREAM:
            pass
        case _:
            raise ValueError("a code review source must be a pull_requests connector source")
    digest = hashlib.sha256(
        json.dumps(
            {"account": account, "base_url": base_url, "provider": provider}, sort_keys=True
        ).encode()
    ).hexdigest()[:8]
    return f"{provider.replace('_', '-')}-{digest}"


def _carry_review_inboxes() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if not inspector.has_table("source_trigger"):
        return
    if "delivery" not in {column["name"] for column in inspector.get_columns("source_trigger")}:
        return
    inbox = sa.table(
        "coding_review_inbox",
        sa.column("workspace_id", sa.Uuid()),
        sa.column("source_id", sa.Uuid()),
        sa.column("agent_id", sa.Uuid()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    source = sa.table(
        "source",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("backend", sa.Text()),
        sa.column("config", sa.JSON()),
        sa.column("subject", sa.Text()),
        sa.column("removed_at", sa.DateTime(timezone=True)),
    )
    conversation = sa.table(
        "conversation",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("agent_id", sa.Uuid()),
        sa.column("surface", sa.Text()),
        sa.column("queue_key", sa.Text()),
        sa.column("member_id", sa.Uuid()),
        sa.column("audience", sa.Text()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    trigger = sa.table(
        "source_trigger",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("conversation_id", sa.Uuid()),
        sa.column("agent_id", sa.Uuid()),
        sa.column("binding", sa.Text()),
        sa.column("delivery", sa.Text()),
        sa.column("created_by_member_id", sa.Uuid()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    rows = (
        connection.execute(
            sa.select(
                inbox.c.workspace_id,
                inbox.c.source_id,
                inbox.c.agent_id,
                inbox.c.created_at,
                inbox.c.updated_at,
                source.c.backend,
                source.c.config,
            )
            .select_from(
                inbox.join(
                    source,
                    sa.and_(
                        source.c.workspace_id == inbox.c.workspace_id,
                        source.c.id == inbox.c.source_id,
                    ),
                )
            )
            .where(source.c.subject == "shared", source.c.removed_at.is_(None))
        )
        .mappings()
        .all()
    )
    for row in rows:
        binding = _binding_name(row["backend"], row["config"])
        present = connection.execute(
            sa.select(trigger.c.id).where(
                trigger.c.workspace_id == row["workspace_id"],
                trigger.c.agent_id == row["agent_id"],
                trigger.c.binding == binding,
                trigger.c.delivery == "per_page",
            )
        ).one_or_none()
        if present is not None:
            continue
        conversation_id = uuid4()
        connection.execute(
            sa.insert(conversation).values(
                id=conversation_id,
                workspace_id=row["workspace_id"],
                agent_id=row["agent_id"],
                surface=SOURCES_SURFACE,
                queue_key=f"{REVIEW_QUEUE_PREFIX}{row['source_id'].hex}",
                member_id=None,
                audience="shared",
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        )
        connection.execute(
            sa.insert(trigger).values(
                id=uuid4(),
                workspace_id=row["workspace_id"],
                conversation_id=conversation_id,
                agent_id=row["agent_id"],
                binding=binding,
                delivery="per_page",
                created_by_member_id=None,
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        )


def upgrade() -> None:
    _carry_review_inboxes()
    op.drop_table("coding_review_run")
    op.drop_table("coding_review_inbox")
    with op.batch_alter_table("turn") as batch:
        batch.drop_constraint("coding_turn_workspace_identity", type_="unique")


def downgrade() -> None:
    with op.batch_alter_table("turn") as batch:
        batch.create_unique_constraint("coding_turn_workspace_identity", ("workspace_id", "id"))
    op.create_table(
        "coding_review_inbox",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("baseline_revision", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "source_id"],
            ["source.workspace_id", "source.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "agent_id"],
            ["agent.workspace_id", "agent.id"],
        ),
        sa.PrimaryKeyConstraint("workspace_id", "source_id"),
    )
    op.create_table(
        "coding_review_run",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("repository", sa.Text(), nullable=False),
        sa.Column("pull_request_number", sa.Integer(), nullable=False),
        sa.Column("base_sha", sa.Text(), nullable=False),
        sa.Column("head_sha", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=True),
        sa.Column("review_conversation_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "source_id"],
            ["source.workspace_id", "source.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "conversation_id"],
            ["conversation.workspace_id", "conversation.id"],
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "agent_id"],
            ["agent.workspace_id", "agent.id"],
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "turn_id"],
            ["turn.workspace_id", "turn.id"],
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "review_conversation_id"],
            ["conversation.workspace_id", "conversation.id"],
        ),
        sa.PrimaryKeyConstraint(
            "workspace_id",
            "repository",
            "pull_request_number",
            "base_sha",
            "head_sha",
        ),
        sa.UniqueConstraint("workspace_id", "run_id", name="coding_review_run_identity"),
    )

import sqlalchemy as sa
from alembic import op

revision: str = "20260820015537"
down_revision: str | None = "20260823223019"
branch_labels: str | None = None
depends_on: str | None = None

ARCHIVE_SCOPE = "archived_at is null or not is_main"

AGENT_WITH_NAME_CONSTRAINT = sa.Table(
    "agent",
    sa.MetaData(),
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("icon", sa.Text, nullable=False, server_default=sa.text("'propylon'")),
    sa.Column("prompt", sa.Text, nullable=False),
    sa.Column("model", sa.Text, nullable=False),
    sa.Column("reasoning", sa.Text, nullable=False, server_default=sa.text("'auto'")),
    sa.Column("is_main", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("visibility", sa.Text, nullable=False, server_default=sa.text("'private'")),
    sa.Column("internet_access_allowed", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("use_workspace_skills", sa.Boolean, nullable=False, server_default=sa.true()),
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


def upgrade() -> None:
    with op.batch_alter_table("agent", copy_from=AGENT_WITH_NAME_CONSTRAINT) as batch:
        batch.add_column(sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_check_constraint("agent_archive_scope", ARCHIVE_SCOPE)


def downgrade() -> None:
    pass

"""agent-owned user skills"""

import sqlalchemy as sa
from alembic import op

revision: str = "skill_create_0002"
down_revision: str | None = "skill_create_0001"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = "0051"

EARLIEST_AGENT = (
    "(select a.id from agent a where a.workspace_id = user_skill.workspace_id "
    "order by a.created_at, a.id limit 1)"
)
SQLITE_PK = {"pk": "pk_%(table_name)s"}


def upgrade() -> None:
    with op.batch_alter_table("user_skill") as batch:
        batch.add_column(sa.Column("agent_id", sa.Uuid(), nullable=True))
    op.execute(f"update user_skill set agent_id = {EARLIEST_AGENT} where agent_id is null")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("alter table user_skill alter column agent_id set not null")
        op.execute("alter table user_skill drop constraint user_skill_pkey")
        op.execute("alter table user_skill add primary key (workspace_id, agent_id, name)")
        op.execute(
            "alter table user_skill add constraint user_skill_agent_id_fkey "
            "foreign key (agent_id) references agent(id)"
        )
        return
    with op.batch_alter_table("user_skill", naming_convention=SQLITE_PK) as batch:
        batch.alter_column("agent_id", nullable=False, existing_type=sa.Uuid())
        batch.drop_constraint("pk_user_skill", type_="primary")
        batch.create_primary_key("user_skill_pkey", ["workspace_id", "agent_id", "name"])
        batch.create_foreign_key("user_skill_agent_id_fkey", "agent", ["agent_id"], ["id"])


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("alter table user_skill drop constraint user_skill_agent_id_fkey")
        op.execute("alter table user_skill drop constraint user_skill_pkey")
        op.execute("alter table user_skill add primary key (workspace_id, name)")
        op.execute("alter table user_skill drop column agent_id")
        return
    with op.batch_alter_table("user_skill", naming_convention=SQLITE_PK) as batch:
        batch.drop_constraint("user_skill_agent_id_fkey", type_="foreignkey")
        batch.drop_constraint("pk_user_skill", type_="primary")
        batch.create_primary_key("user_skill_pkey", ["workspace_id", "name"])
        batch.drop_column("agent_id")

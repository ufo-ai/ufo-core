"""workspace-owned user skills

Every saved skill of a workspace is one set the portal's workspace page manages, keyed
`(workspace_id, name)`. The `(workspace_id, agent_id, name)` key of `skill_create_0002` let two
agents each hold a different skill under one name; here the newest save of each name (latest
`updated_at`, latest `agent_id` on a tie) becomes the workspace's one row and the rows it shadowed
are dropped with it — one name is one skill, never a renamed copy. `generation` stamps every
surviving row for the save fence; `agents` starts empty (no targeting) and is rewritten from
frontmatter on the next save. Index chunks were embedded under per-agent owner ids and a per-agent
subject, both unreachable by the workspace-scoped retrieval this key establishes, so clearing
`indexed_digest` hands each surviving row to the next tick of the index job, which re-embeds it
under its name.
"""

import logging
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "skill_create_0004"
down_revision: str | None = "skill_create_0003"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = "20260822090000"

logger = logging.getLogger(__name__)

EARLIEST_AGENT = (
    "(select a.id from agent a where a.workspace_id = user_skill.workspace_id "
    "order by a.created_at, a.id limit 1)"
)


def _drop_shadowed_rows(connection: sa.Connection) -> int:
    rows = connection.execute(
        sa.text(
            "select workspace_id, agent_id, name from user_skill "
            "order by workspace_id, name, updated_at desc, agent_id desc"
        )
    ).all()
    kept: set[tuple[object, str]] = set()
    dropped = 0
    for row in rows:
        if (row.workspace_id, row.name) not in kept:
            kept.add((row.workspace_id, row.name))
            continue
        connection.execute(
            sa.text(
                "delete from user_skill where workspace_id = :workspace_id "
                "and agent_id = :agent_id and name = :name"
            ),
            {
                "workspace_id": row.workspace_id,
                "agent_id": row.agent_id,
                "name": row.name,
            },
        )
        dropped += 1
    return dropped


def upgrade() -> None:
    connection = op.get_bind()
    dropped = _drop_shadowed_rows(connection)
    logger.info(
        "skill_create.shadowed_skills_dropped",
        extra={"dropped": dropped},
    )
    with op.batch_alter_table("user_skill") as batch:
        batch.add_column(sa.Column("generation", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("agents", sa.Text(), nullable=True))
    op.execute("update user_skill set agents = '[]', indexed_digest = null")
    for row in connection.execute(sa.text("select workspace_id, name from user_skill")).all():
        connection.execute(
            sa.text(
                "update user_skill set generation = :generation "
                "where workspace_id = :workspace_id and name = :name"
            ),
            {
                "generation": uuid4().hex,
                "workspace_id": row.workspace_id,
                "name": row.name,
            },
        )
    if connection.dialect.name == "postgresql":
        op.execute("alter table user_skill alter column generation set not null")
        op.execute("alter table user_skill alter column agents set not null")
        op.execute("alter table user_skill drop constraint user_skill_pkey")
        op.execute("alter table user_skill drop constraint user_skill_agent_id_fkey")
        op.execute("alter table user_skill drop column agent_id")
        op.execute("alter table user_skill add primary key (workspace_id, name)")
        return
    with op.batch_alter_table("user_skill") as batch:
        batch.alter_column("generation", nullable=False, existing_type=sa.Uuid())
        batch.alter_column("agents", nullable=False, existing_type=sa.Text())
        batch.drop_constraint("user_skill_pkey", type_="primary")
        batch.drop_constraint("user_skill_agent_id_fkey", type_="foreignkey")
        batch.drop_column("agent_id")
        batch.create_primary_key("user_skill_pkey", ["workspace_id", "name"])


def downgrade() -> None:
    connection = op.get_bind()
    with op.batch_alter_table("user_skill") as batch:
        batch.add_column(sa.Column("agent_id", sa.Uuid(), nullable=True))
    op.execute(f"update user_skill set agent_id = {EARLIEST_AGENT}, indexed_digest = null")
    if connection.dialect.name == "postgresql":
        op.execute("alter table user_skill alter column agent_id set not null")
        op.execute("alter table user_skill drop constraint user_skill_pkey")
        op.execute("alter table user_skill drop column generation")
        op.execute("alter table user_skill drop column agents")
        op.execute("alter table user_skill add primary key (workspace_id, agent_id, name)")
        op.execute(
            "alter table user_skill add constraint user_skill_agent_id_fkey "
            "foreign key (agent_id) references agent(id)"
        )
        return
    with op.batch_alter_table("user_skill") as batch:
        batch.alter_column("agent_id", nullable=False, existing_type=sa.Uuid())
        batch.drop_constraint("user_skill_pkey", type_="primary")
        batch.drop_column("generation")
        batch.drop_column("agents")
        batch.create_primary_key("user_skill_pkey", ["workspace_id", "agent_id", "name"])
        batch.create_foreign_key("user_skill_agent_id_fkey", "agent", ["agent_id"], ["id"])

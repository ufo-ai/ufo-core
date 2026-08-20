"""skill routing cards"""

import base64
import json

import sqlalchemy as sa
import yaml
from alembic import op

revision: str = "skill_create_0003"
down_revision: str | None = "skill_create_0002"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

FRONTMATTER_FENCE = "---\n"


def _card(content: str) -> tuple[str, str]:
    try:
        files = json.loads(content)["files"]
        raw = base64.b64decode(files["SKILL.md"]).decode()
        if not raw.startswith(FRONTMATTER_FENCE):
            return "", "[]"
        metadata, fence, _ = raw[len(FRONTMATTER_FENCE) :].partition(f"\n{FRONTMATTER_FENCE}")
        if not fence:
            return "", "[]"
        front = yaml.safe_load(metadata) or {}
        description = str(front["description"])
        depends = json.dumps([str(name) for name in front.get("metadata", {}).get("depends", ())])
    except (ValueError, KeyError, TypeError, AttributeError, yaml.YAMLError):
        return "", "[]"
    return description, depends


def upgrade() -> None:
    with op.batch_alter_table("user_skill") as batch:
        batch.add_column(sa.Column("description", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("depends", sa.Text(), nullable=False, server_default="[]"))
        batch.add_column(
            sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("indexed_digest", sa.Text(), nullable=True))
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("select workspace_id, agent_id, name, content from user_skill")
    ).all()
    for row in rows:
        description, depends = _card(row.content)
        if (description, depends) == ("", "[]"):
            continue
        connection.execute(
            sa.text(
                "update user_skill set description = :description, depends = :depends "
                "where workspace_id = :workspace_id and agent_id = :agent_id and name = :name"
            ),
            {
                "description": description,
                "depends": depends,
                "workspace_id": row.workspace_id,
                "agent_id": row.agent_id,
                "name": row.name,
            },
        )


def downgrade() -> None:
    with op.batch_alter_table("user_skill") as batch:
        batch.drop_column("indexed_digest")
        batch.drop_column("pinned")
        batch.drop_column("depends")
        batch.drop_column("description")

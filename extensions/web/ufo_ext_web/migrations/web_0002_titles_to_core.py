"""the chat row stops naming the conversation

A portal chat's name lived in this extension's own store row, where the query that lists
conversations could not reach it. It is the conversation's now, so each stored name is carried onto
the row it names and struck from the value it was kept in — the two in one migration, because the
store row is this extension's and nothing else will ever go back for it.

The carry runs after `0086`, which adds the column and fills it with the words each conversation
opened with; a name written here stands over that, which is the same order the title job and the
opening turn stand in.
"""

import json
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision: str = "web_0002"
down_revision: str | None = "web_0001"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = "0086"

CHAT_STORE_PREFIX = "chat/"
TITLE = "title"

conversation = sa.table(
    "conversation",
    sa.column("id", sa.Uuid()),
    sa.column("workspace_id", sa.Uuid()),
    sa.column("title", sa.Text()),
)

ext_store = sa.table(
    "ext_store",
    sa.column("workspace_id", sa.Uuid()),
    sa.column("extension", sa.Text()),
    sa.column("key", sa.Text()),
    sa.column("value", sa.JSON()),
    sa.column("updated_at", sa.DateTime()),
)


def _chat_rows(bind: sa.engine.Connection) -> list[tuple[UUID, str, dict[str, object]]]:
    """Every chat row this extension holds, each value as the object it was written as. A driver
    that hands the JSON column back as text is why the parse is here rather than assumed: read as a
    string, a row states no title and would be carried over in silence."""
    listed = bind.execute(
        sa.select(ext_store.c.workspace_id, ext_store.c.key, ext_store.c.value).where(
            ext_store.c.extension == "web",
            ext_store.c.key.startswith(CHAT_STORE_PREFIX, autoescape=True),
        )
    ).all()
    return [
        (
            row.workspace_id,
            row.key,
            row.value if isinstance(row.value, dict) else json.loads(row.value),
        )
        for row in listed
    ]


def upgrade() -> None:
    bind = op.get_bind()
    for workspace_id, key, value in _chat_rows(bind):
        named = value.get(TITLE)
        if not isinstance(named, str) or not named:
            continue
        bind.execute(
            sa.update(conversation)
            .where(
                conversation.c.workspace_id == workspace_id,
                conversation.c.id == UUID(key.removeprefix(CHAT_STORE_PREFIX)),
            )
            .values(title=named)
        )
        bind.execute(
            sa.update(ext_store)
            .where(
                ext_store.c.workspace_id == workspace_id,
                ext_store.c.extension == "web",
                ext_store.c.key == key,
            )
            .values(
                value={field: held for field, held in value.items() if field != TITLE},
                updated_at=sa.func.now(),
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    for workspace_id, key, value in _chat_rows(bind):
        named = bind.execute(
            sa.select(conversation.c.title).where(
                conversation.c.workspace_id == workspace_id,
                conversation.c.id == UUID(key.removeprefix(CHAT_STORE_PREFIX)),
            )
        ).scalar_one_or_none()
        bind.execute(
            sa.update(ext_store)
            .where(
                ext_store.c.workspace_id == workspace_id,
                ext_store.c.extension == "web",
                ext_store.c.key == key,
            )
            .values(value={**value, TITLE: named or ""}, updated_at=sa.func.now())
        )

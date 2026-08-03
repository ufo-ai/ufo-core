"""chat_rows"""

import sqlalchemy as sa
from alembic import op

revision: str = "web_0001"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = ("web",)
depends_on: str | None = "0051"

CHAT_STORE_PREFIX = "chat/"

conversation = sa.table(
    "conversation",
    sa.column("id", sa.Uuid()),
    sa.column("workspace_id", sa.Uuid()),
    sa.column("agent_id", sa.Uuid()),
    sa.column("member_id", sa.Uuid()),
    sa.column("surface", sa.Text()),
    sa.column("queue_key", sa.Text()),
)

member = sa.table("member", sa.column("id", sa.Uuid()), sa.column("email", sa.Text()))

agent = sa.table("agent", sa.column("id", sa.Uuid()), sa.column("name", sa.Text()))

ext_store = sa.table(
    "ext_store",
    sa.column("workspace_id", sa.Uuid()),
    sa.column("extension", sa.Text()),
    sa.column("key", sa.Text()),
    sa.column("value", sa.JSON()),
    sa.column("created_at", sa.DateTime()),
    sa.column("updated_at", sa.DateTime()),
)


def _bare_key_email(queue_key: str, agent_id: object, member_email: str) -> str | None:
    """The session email a bare `agent/email` queue key carries, or None for any other key shape —
    `intent/` lanes miss the agent prefix, and a minted `agent/email/hex` key is not the member's
    email, slash-bearing local parts included. The key was written from the verified session
    email, which folds case; the member row keeps the email as typed — so the match folds case on
    both sides and the row stores the key's own spelling, the one the chat gate compares
    against."""
    prefix = f"{agent_id}/"
    if not queue_key.startswith(prefix):
        return None
    suffix = queue_key[len(prefix) :]
    if suffix.lower() != member_email.strip().lower():
        return None
    return suffix


def upgrade() -> None:
    bind = op.get_bind()
    keyed = bind.execute(
        sa.select(
            conversation.c.id,
            conversation.c.workspace_id,
            conversation.c.agent_id,
            conversation.c.queue_key,
            member.c.email,
            agent.c.name,
        )
        .select_from(
            conversation.join(member, member.c.id == conversation.c.member_id).join(
                agent, agent.c.id == conversation.c.agent_id
            )
        )
        .where(conversation.c.surface == "web")
    ).all()
    for row in keyed:
        email = _bare_key_email(row.queue_key, row.agent_id, row.email)
        if email is None:
            continue
        bind.execute(
            sa.insert(ext_store).values(
                workspace_id=row.workspace_id,
                extension="web",
                key=f"{CHAT_STORE_PREFIX}{row.id}",
                value={"agent_id": str(row.agent_id), "email": email, "title": row.name},
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    keyed = bind.execute(
        sa.select(
            conversation.c.id,
            conversation.c.workspace_id,
            conversation.c.agent_id,
            conversation.c.queue_key,
            member.c.email,
        )
        .select_from(conversation.join(member, member.c.id == conversation.c.member_id))
        .where(conversation.c.surface == "web")
    ).all()
    for row in keyed:
        if _bare_key_email(row.queue_key, row.agent_id, row.email) is None:
            continue
        bind.execute(
            sa.delete(ext_store).where(
                ext_store.c.workspace_id == row.workspace_id,
                ext_store.c.extension == "web",
                ext_store.c.key == f"{CHAT_STORE_PREFIX}{row.id}",
            )
        )

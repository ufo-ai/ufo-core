"""the web surface keeps no chat row of its own

`ext_store` held one `chat/<conversation id>` row per portal chat for the `web` extension, the
(agent, member) binding the surface gated its chat on. The conversation row states both — its
`agent_id`, and its `audience` naming the member — and the surface now reads them there, so the
rows are a second record of a fact with one home.

The downgrade writes nothing back: every row it would restore is derivable from `conversation`, and
the release this replaces read the row only to confirm what the conversation already said.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "web_0003"
down_revision: str | None = "web_0002"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    ext_store = sa.table(
        "ext_store", sa.column("extension", sa.Text()), sa.column("key", sa.Text())
    )
    op.execute(
        ext_store.delete().where(ext_store.c.extension == "web", ext_store.c.key.like("chat/%"))
    )


def downgrade() -> None:
    pass

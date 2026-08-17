"""a conversation records whether a summary has named it

What a conversation is called starts as the words its opening turn arrived with, and the titling
job replaces that with a summary of the opening exchange. Which conversations still await that
summary was the web extension's own bookkeeping — one `chat_title_pending/<id>` row per portal chat
it had opened — so no conversation any other surface holds could ever be reached by the job, and a
Slack thread or a CLI session kept its first sentence as its name forever.

The column is that fact, on the row the title itself is on: false until the summarizer has had its
go, and true afterwards whether or not the model wrote a usable name, so an excerpt nothing can
name is attempted once rather than every tick.

Every conversation that exists stands as awaiting a summary, which is how the ones already open end
up named — including a portal chat the job has already named, which is named once more for the cost
of one summary. The other reading of the extension's own rows, that a `chat/<id>` row with no
`chat_title_pending/<id>` beside it is a chat the job finished with, also carries over every chat
opened before that pending key space existed, and a conversation wrongly recorded as summarized
keeps the raw first message it was named with for good.

The pending rows go, because this column is the state they held. Core owns `ext_store`, and nothing
else would ever delete the key space this column replaces.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0096"
down_revision: str | None = "0095"
branch_labels: str | None = None
depends_on: str | None = None

WEB_EXTENSION = "web"
PENDING_PREFIX = "chat_title_pending/"

ext_store = sa.table(
    "ext_store",
    sa.column("extension", sa.Text()),
    sa.column("key", sa.Text()),
)


def upgrade() -> None:
    op.add_column(
        "conversation",
        sa.Column("title_summarized", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "conversation_awaiting_title",
        "conversation",
        ["workspace_id"],
        postgresql_where=sa.text("not title_summarized"),
        sqlite_where=sa.text("not title_summarized"),
    )
    op.get_bind().execute(
        sa.delete(ext_store).where(
            ext_store.c.extension == WEB_EXTENSION,
            ext_store.c.key.startswith(PENDING_PREFIX, autoescape=True),
        )
    )


def downgrade() -> None:
    op.drop_index("conversation_awaiting_title", table_name="conversation")
    with op.batch_alter_table("conversation") as batch:
        batch.drop_column("title_summarized")

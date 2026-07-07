"""graph_entity + graph_edge"""

import sqlalchemy as sa
from alembic import op

revision: str = "knowledge_graph_0001"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = ("knowledge_graph",)
depends_on: str | None = "0001"


def upgrade() -> None:
    op.create_table(
        "graph_entity",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("is_stub", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "entity_type in ('person', 'company', 'organization', 'topic')",
            name="graph_entity_type",
        ),
        sa.CheckConstraint(
            "subject = 'shared' or subject like 'member:%'", name="graph_entity_subject"
        ),
    )
    op.create_index(
        "graph_entity_lookup", "graph_entity", ["workspace_id", "subject", "normalized_name"]
    )
    op.create_table(
        "graph_edge",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("edge_type", sa.Text(), nullable=False),
        sa.Column("from_entity", sa.Uuid(), nullable=False),
        sa.Column("to_entity", sa.Uuid(), nullable=False),
        sa.Column("source_page_id", sa.Uuid(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("extracted_digest", sa.Text(), nullable=False),
        sa.Column("tombstone", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["from_entity"], ["graph_entity.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["to_entity"], ["graph_entity.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "edge_type in ('mentions', 'derived_from', 'works_at', 'founded', "
            "'invested_in', 'advises', 'attended', 'reports_to')",
            name="graph_edge_type",
        ),
        sa.CheckConstraint(
            "subject = 'shared' or subject like 'member:%'", name="graph_edge_subject"
        ),
    )
    op.create_index("graph_edge_from", "graph_edge", ["workspace_id", "from_entity", "tombstone"])
    op.create_index("graph_edge_to", "graph_edge", ["workspace_id", "to_entity", "tombstone"])
    op.create_index("graph_edge_page", "graph_edge", ["source_page_id"])


def downgrade() -> None:
    op.drop_index("graph_edge_page", "graph_edge")
    op.drop_index("graph_edge_to", "graph_edge")
    op.drop_index("graph_edge_from", "graph_edge")
    op.drop_table("graph_edge")
    op.drop_index("graph_entity_lookup", "graph_entity")
    op.drop_table("graph_entity")

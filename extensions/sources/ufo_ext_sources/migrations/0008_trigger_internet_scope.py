"""Source-trigger internet scope."""

import sqlalchemy as sa
from alembic import op

revision: str = "sources_0008"
down_revision: str | None = "sources_0007"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

POSTGRES_FUNCTION = "source_trigger_internet_scope"
POSTGRES_TRIGGER = "source_trigger_internet_scope"


def upgrade() -> None:
    with op.batch_alter_table("source_trigger") as batch:
        batch.add_column(sa.Column("internet_access", sa.Boolean(), nullable=True))
    op.execute(sa.text("UPDATE source_trigger SET internet_access = true"))
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            f"""
            CREATE FUNCTION {POSTGRES_FUNCTION}()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.internet_access IS NULL THEN
                    SELECT COALESCE(
                        bool_and(
                            COALESCE((runtime_config ->> 'internet_access')::boolean, true)
                        ),
                        false
                    )
                    INTO NEW.internet_access
                    FROM turn
                    WHERE workspace_id = NEW.workspace_id
                      AND conversation_id = NEW.conversation_id
                      AND status = 'running';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER {POSTGRES_TRIGGER}
            BEFORE INSERT ON source_trigger
            FOR EACH ROW EXECUTE FUNCTION {POSTGRES_FUNCTION}()
            """
        )
    with op.batch_alter_table("source_trigger") as batch:
        batch.alter_column("internet_access", nullable=False)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP TRIGGER {POSTGRES_TRIGGER} ON source_trigger")
        op.execute(f"DROP FUNCTION {POSTGRES_FUNCTION}()")
    with op.batch_alter_table("source_trigger") as batch:
        batch.drop_column("internet_access")

"""pause capability scope"""

import sqlalchemy as sa
from alembic import op

revision: str = "scheduled_tasks_0004"
down_revision: str | None = "scheduled_tasks_0003"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

POSTGRES_FUNCTION = "pause_generation_scope_reset"
POSTGRES_TRIGGER = "pause_generation_scope"
POSTGRES_INSERT_FUNCTION = "pause_internet_scope"
POSTGRES_INSERT_TRIGGER = "pause_internet_scope"
PAUSE_CLAIM_GUC = "app.scope_preserving_pause_claim"


def upgrade() -> None:
    with op.batch_alter_table("pause") as batch:
        batch.add_column(sa.Column("internet_access", sa.Boolean(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE pause SET internet_access = true, claimed_by = NULL, claim_expires_at = NULL"
        )
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            f"""
            CREATE FUNCTION {POSTGRES_INSERT_FUNCTION}()
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
            CREATE TRIGGER {POSTGRES_INSERT_TRIGGER}
            BEFORE INSERT ON pause
            FOR EACH ROW EXECUTE FUNCTION {POSTGRES_INSERT_FUNCTION}()
            """
        )
        op.execute(
            f"""
            CREATE FUNCTION {POSTGRES_FUNCTION}()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.id IS DISTINCT FROM OLD.id THEN
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
                IF NEW.claimed_by IS NOT NULL
                   AND NEW.claimed_by IS DISTINCT FROM OLD.claimed_by
                   AND current_setting('{PAUSE_CLAIM_GUC}', true) IS DISTINCT FROM 'true'
                THEN
                    RAISE EXCEPTION 'pause claim requires current scope support'
                        USING ERRCODE = '42501';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER {POSTGRES_TRIGGER}
            BEFORE UPDATE OF claimed_by ON pause
            FOR EACH ROW EXECUTE FUNCTION {POSTGRES_FUNCTION}()
            """
        )
    with op.batch_alter_table("pause") as batch:
        batch.alter_column("internet_access", nullable=False)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP TRIGGER {POSTGRES_TRIGGER} ON pause")
        op.execute(f"DROP FUNCTION {POSTGRES_FUNCTION}()")
        op.execute(f"DROP TRIGGER {POSTGRES_INSERT_TRIGGER} ON pause")
        op.execute(f"DROP FUNCTION {POSTGRES_INSERT_FUNCTION}()")
    with op.batch_alter_table("pause") as batch:
        batch.drop_column("internet_access")

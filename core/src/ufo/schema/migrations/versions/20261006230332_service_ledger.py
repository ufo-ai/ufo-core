"""Service rows in the ledger.

A row names the service that metered it and the unit it counts (`dimension`), beside the backend,
token, session, labels, resource and attempt a service record carries; `ledger_service_dimension`
admits each service its units. `ledger_job_day` gains the service key the fold keeps, and the
folded rows take the service their dimension belongs to.

A row inserted with no service is filled by `ledger_fill_service`: `tokens`, `images` and `videos`
go under `models`; `sandbox_tokens` becomes `(models, tokens)`, platform-paid, labelled `via: proxy`
and its turn; `egress` becomes `(proxy, requests)` labelled with its turn. Postgres assigns the row
before insert from one `plpgsql` function; SQLite cannot assign to `new`, so its trigger updates the
row after insert. The rows the ledger already holds are filled by the `ledger_service_backfill` job,
batch by batch, rather than by one statement here that would lock them all until this commits.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "20261006230332"
down_revision: str | None = "20260927025054"
branch_labels: str | None = None
depends_on: str | None = None

LEDGER_DIMENSION = (
    "dimension in ('tokens', 'egress', 'sandbox_tokens', 'images', 'videos', 'requests', 'gib')"
)
LEDGER_SERVICE_DIMENSION = (
    "service is null or (service = 'models' and dimension in ('tokens', 'sandbox_tokens', "
    "'images', 'videos')) or (service = 'proxy' and dimension in ('egress', 'requests', 'gib'))"
)
LEDGER_BYOK_DIMENSION = "not byok or dimension in ('tokens', 'requests', 'gib')"
DOWNGRADE_LEDGER_DIMENSION = (
    "dimension in ('tokens', 'egress', 'sandbox_tokens', 'images', 'videos')"
)
DOWNGRADE_LEDGER_BYOK_DIMENSION = "not byok or dimension = 'tokens'"
JOB_DAY_SERVICES = (
    "update ledger_job_day set service = 'models' "
    "where dimension in ('tokens', 'images', 'videos')",
    "update ledger_job_day set service = 'models', dimension = 'tokens' "
    "where dimension = 'sandbox_tokens'",
    "update ledger_job_day set service = 'proxy', dimension = 'requests' "
    "where dimension = 'egress'",
)
FILL_LEDGER_SERVICE = """
create function fill_ledger_service() returns trigger as $$
begin
    if new.dimension = 'sandbox_tokens' then
        new.service := 'models';
        new.dimension := 'tokens';
        new.byok := false;
        new.labels := jsonb_strip_nulls(jsonb_build_object('via', 'proxy', 'turn', new.turn_id));
    elsif new.dimension = 'egress' then
        new.service := 'proxy';
        new.dimension := 'requests';
        new.labels := jsonb_strip_nulls(jsonb_build_object('turn', new.turn_id));
    else
        new.service := 'models';
    end if;
    return new;
end;
$$ language plpgsql
"""
POSTGRES_LEDGER_FILL_SERVICE = (
    "create trigger ledger_fill_service before insert on ledger for each row "
    "when (new.service is null) execute function fill_ledger_service()"
)
SQLITE_TURN_TEXT = (
    "substr(new.turn_id, 1, 8) || '-' || substr(new.turn_id, 9, 4) || '-' || "
    "substr(new.turn_id, 13, 4) || '-' || substr(new.turn_id, 17, 4) || '-' || "
    "substr(new.turn_id, 21)"
)
SQLITE_LEDGER_FILL_SERVICE = """
create trigger ledger_fill_service
after insert on ledger
when new.service is null
begin
    update ledger
    set service = case new.dimension when 'egress' then 'proxy' else 'models' end,
        dimension = case new.dimension
            when 'sandbox_tokens' then 'tokens'
            when 'egress' then 'requests'
            else new.dimension
        end,
        byok = case new.dimension when 'sandbox_tokens' then 0 else new.byok end,
        labels = case new.dimension
            when 'sandbox_tokens'
                then json_patch(json_object(), json_object('via', 'proxy', 'turn', {turn}))
            when 'egress' then json_patch(json_object(), json_object('turn', {turn}))
            else new.labels
        end
    where id = new.id;
end
"""


def upgrade() -> None:
    with op.batch_alter_table("ledger") as batch:
        batch.add_column(sa.Column("service", sa.Text(), nullable=True))
        batch.add_column(sa.Column("backend", sa.Text(), nullable=True))
        batch.add_column(sa.Column("token_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("session_id", sa.Uuid(), nullable=True))
        batch.add_column(
            sa.Column(
                "labels",
                sa.JSON().with_variant(JSONB(), "postgresql"),
                server_default=sa.text("'{}'"),
                nullable=False,
            )
        )
        batch.add_column(sa.Column("resource_id", sa.Text(), nullable=True))
        batch.add_column(sa.Column("attempt", sa.Text(), nullable=True))
        batch.drop_constraint("ledger_dimension", type_="check")
        batch.drop_constraint("ledger_byok_dimension", type_="check")
        batch.create_check_constraint("ledger_dimension", LEDGER_DIMENSION)
        batch.create_check_constraint("ledger_service_dimension", LEDGER_SERVICE_DIMENSION)
        batch.create_check_constraint("ledger_byok_dimension", LEDGER_BYOK_DIMENSION)
    op.create_index(
        "ledger_workspace_token",
        "ledger",
        ["workspace_id", "token_id", "created_at"],
        unique=False,
        postgresql_where=sa.text("token_id is not null"),
        sqlite_where=sa.text("token_id is not null"),
    )
    op.create_index(
        "ledger_workspace_session",
        "ledger",
        ["workspace_id", "session_id"],
        unique=False,
        postgresql_where=sa.text("session_id is not null"),
        sqlite_where=sa.text("session_id is not null"),
    )
    op.create_index(
        "ledger_unserviced",
        "ledger",
        ["workspace_id"],
        unique=False,
        postgresql_where=sa.text("service is null"),
        sqlite_where=sa.text("service is null"),
    )
    with op.batch_alter_table("ledger_job_day") as batch:
        batch.add_column(sa.Column("service", sa.Text(), nullable=True))
        batch.add_column(sa.Column("backend", sa.Text(), nullable=True))
        batch.add_column(sa.Column("token_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("byok", sa.Boolean(), nullable=True))
    for statement in JOB_DAY_SERVICES:
        op.execute(statement)
    if op.get_bind().dialect.name == "postgresql":
        op.execute(FILL_LEDGER_SERVICE)
        op.execute(POSTGRES_LEDGER_FILL_SERVICE)
        return
    op.execute(SQLITE_LEDGER_FILL_SERVICE.format(turn=SQLITE_TURN_TEXT))


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("drop trigger ledger_fill_service on ledger")
        op.execute("drop function fill_ledger_service()")
    else:
        op.execute("drop trigger ledger_fill_service")
    with op.batch_alter_table("ledger_job_day") as batch:
        batch.drop_column("byok")
        batch.drop_column("token_id")
        batch.drop_column("backend")
        batch.drop_column("service")
    op.drop_index(
        "ledger_unserviced",
        table_name="ledger",
        postgresql_where=sa.text("service is null"),
        sqlite_where=sa.text("service is null"),
    )
    op.drop_index(
        "ledger_workspace_session",
        table_name="ledger",
        postgresql_where=sa.text("session_id is not null"),
        sqlite_where=sa.text("session_id is not null"),
    )
    op.drop_index(
        "ledger_workspace_token",
        table_name="ledger",
        postgresql_where=sa.text("token_id is not null"),
        sqlite_where=sa.text("token_id is not null"),
    )
    with op.batch_alter_table("ledger") as batch:
        batch.drop_constraint("ledger_byok_dimension", type_="check")
        batch.drop_constraint("ledger_service_dimension", type_="check")
        batch.drop_constraint("ledger_dimension", type_="check")
        batch.create_check_constraint("ledger_dimension", DOWNGRADE_LEDGER_DIMENSION)
        batch.create_check_constraint("ledger_byok_dimension", DOWNGRADE_LEDGER_BYOK_DIMENSION)
        batch.drop_column("attempt")
        batch.drop_column("resource_id")
        batch.drop_column("labels")
        batch.drop_column("session_id")
        batch.drop_column("token_id")
        batch.drop_column("backend")
        batch.drop_column("service")

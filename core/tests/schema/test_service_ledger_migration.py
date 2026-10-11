import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection
from ufo_testsupport.plugin import drop_postgres_database, reset_postgres_database

from ufo.db import MIGRATIONS_DIR, dispose_db, init_db, workspace_tx
from ufo.runtime.billing.accounting import ServiceBackfill
from ufo.schema import tables

SERVICE_LEDGER = "20261006230332"
LEDGER_INDEXES = {"ledger_workspace_token", "ledger_workspace_session", "ledger_unserviced"}
OUTGOING_SERVICE_OF_DIMENSION = {
    "tokens": "models",
    "egress": "proxy",
    "sandbox_tokens": "models",
    "images": "models",
    "videos": "models",
}
UNSERVICED_INSERT = sa.text(
    "insert into ledger (id, workspace_id, turn_id, dimension, amount, priced_micro_usd, model, "
    "created_at, updated_at) values (:id, :workspace_id, :turn_id, :dimension, 1, 0, '', :now, "
    ":now)"
).bindparams(
    sa.bindparam("id", type_=sa.Uuid()),
    sa.bindparam("workspace_id", type_=sa.Uuid()),
    sa.bindparam("turn_id", type_=sa.Uuid()),
    sa.bindparam("now", type_=sa.DateTime(timezone=True)),
)
JOB_DAY_INSERT = sa.text(
    "insert into ledger_job_day (id, workspace_id, day, dimension, model, amount, "
    "priced_micro_usd, first_used_at, created_at, updated_at) values (:id, :workspace_id, :day, "
    ":dimension, '', 1, 0, :now, :now, :now)"
).bindparams(
    sa.bindparam("id", type_=sa.Uuid()),
    sa.bindparam("workspace_id", type_=sa.Uuid()),
    sa.bindparam("day", type_=sa.Date()),
    sa.bindparam("now", type_=sa.DateTime(timezone=True)),
)


async def _turn(connection: AsyncConnection) -> tuple[UUID, UUID]:
    workspace_id, agent_id, conversation_id, turn_id = uuid4(), uuid4(), uuid4(), uuid4()
    await connection.execute(
        sa.insert(tables.workspace).values(
            id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
        )
    )
    await connection.execute(
        sa.insert(tables.agent).values(
            id=agent_id,
            workspace_id=workspace_id,
            name="assistant",
            prompt="p",
            model="claude-opus-4-8",
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    await connection.execute(
        sa.insert(tables.conversation).values(
            id=conversation_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            surface="cli",
            queue_key="session",
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    await connection.execute(
        sa.insert(tables.turn).values(
            id=turn_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            seq=1,
            status="queued",
            inbound="hi",
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    return workspace_id, turn_id


@pytest.mark.parametrize("database_url", ["sqlite", "postgres"], indirect=True)
async def test_a_row_inserted_with_no_service_takes_the_service_of_its_dimension(
    db: None,
) -> None:
    now = datetime.now(UTC)
    tokens, sandbox, egress, probe, images, videos = (uuid4() for _ in range(6))
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _turn(connection)
        for ledger_id, turn, dimension in (
            (tokens, None, "tokens"),
            (sandbox, turn_id, "sandbox_tokens"),
            (egress, turn_id, "egress"),
            (probe, None, "egress"),
            (images, turn_id, "images"),
            (videos, turn_id, "videos"),
        ):
            await connection.execute(
                UNSERVICED_INSERT,
                {
                    "id": ledger_id,
                    "workspace_id": workspace_id,
                    "turn_id": turn,
                    "dimension": dimension,
                    "now": now,
                },
            )
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    tables.ledger.c.id,
                    tables.ledger.c.service,
                    tables.ledger.c.dimension,
                    tables.ledger.c.labels,
                    tables.ledger.c.byok,
                ).where(tables.ledger.c.workspace_id == workspace_id)
            )
        ).all()
    assert {row.id: tuple(row)[1:] for row in rows} == {
        tokens: ("models", "tokens", {}, None),
        sandbox: ("models", "sandbox_tokens", {"via": "proxy", "turn": str(turn_id)}, None),
        egress: ("proxy", "egress", {"turn": str(turn_id)}, None),
        probe: ("proxy", "egress", {}, None),
        images: ("models", "images", {}, None),
        videos: ("models", "videos", {}, None),
    }
    assert all(OUTGOING_SERVICE_OF_DIMENSION[row.dimension] == row.service for row in rows)


@pytest.fixture
def scratch_urls(database_url: str, tmp_path: Path) -> Iterator[tuple[str, str]]:
    source = make_url(database_url)
    if source.get_backend_name() == "sqlite":
        path = tmp_path / "ledger.db"
        yield f"sqlite+aiosqlite:///{path}", f"sqlite:///{path}"
        return
    name = f"{source.database}_service_ledger_{uuid4().hex[:8]}"
    asyncio.run(reset_postgres_database(name))
    try:
        yield (
            source.set(database=name).render_as_string(hide_password=False),
            source.set(drivername="postgresql+psycopg", database=name).render_as_string(
                hide_password=False
            ),
        )
    finally:
        asyncio.run(drop_postgres_database(name))


def _migrated_before(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    before = ScriptDirectory.from_config(config).get_revision(SERVICE_LEDGER).down_revision
    assert isinstance(before, str)
    command.upgrade(config, before)
    return config


def _filed(
    connection: sa.Connection, table: sa.Table, written: dict[UUID, str]
) -> dict[str, tuple[str | None, str]]:
    return {
        written[row.id]: (row.service, row.dimension)
        for row in connection.execute(sa.select(table.c.id, table.c.service, table.c.dimension))
    }


async def _backfill(url: str, workspace_id: UUID) -> int:
    init_db(url)
    try:
        async with workspace_tx() as connection:
            return await ServiceBackfill(workspace_id).roll(connection)
    finally:
        await dispose_db()


@pytest.mark.parametrize("database_url", ["sqlite", "postgres"], indirect=True)
def test_the_upgrade_keeps_every_dimension_the_outgoing_image_reads_and_the_ledger_checks(
    scratch_urls: tuple[str, str],
) -> None:
    migration_url, sync_url = scratch_urls
    config = _migrated_before(migration_url)
    workspace_id, now = uuid4(), datetime.now(UTC)
    rows = {uuid4(): dimension for dimension in OUTGOING_SERVICE_OF_DIMENSION}
    days = {uuid4(): dimension for dimension in OUTGOING_SERVICE_OF_DIMENSION}
    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        for row_id, dimension in rows.items():
            connection.execute(
                UNSERVICED_INSERT,
                {
                    "id": row_id,
                    "workspace_id": workspace_id,
                    "turn_id": None,
                    "dimension": dimension,
                    "now": now,
                },
            )
        for day_id, dimension in days.items():
            connection.execute(
                JOB_DAY_INSERT,
                {
                    "id": day_id,
                    "workspace_id": workspace_id,
                    "day": now.date(),
                    "dimension": dimension,
                    "now": now,
                },
            )
        connection.commit()
    engine.dispose()
    command.upgrade(config, SERVICE_LEDGER)
    with engine.connect() as connection:
        unserviced = _filed(connection, tables.ledger, rows)
        folded = _filed(connection, tables.ledger_job_day, days)
    engine.dispose()
    moved = asyncio.run(_backfill(migration_url, workspace_id))
    with engine.connect() as connection:
        backfilled = _filed(connection, tables.ledger, rows)
        with pytest.raises(sa.exc.IntegrityError, match="ledger_amount"):
            connection.execute(
                sa.insert(tables.ledger).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    service="models",
                    dimension="tokens",
                    amount=0,
                    priced_micro_usd=0,
                    model="",
                    created_at=now,
                    updated_at=now,
                )
            )
    engine.dispose()
    filed = {
        "tokens": ("models", "tokens"),
        "egress": ("proxy", "egress"),
        "sandbox_tokens": ("models", "sandbox_tokens"),
        "images": ("models", "images"),
        "videos": ("models", "videos"),
    }
    assert unserviced == {dimension: (None, dimension) for dimension in filed}
    assert folded == filed
    assert (moved, backfilled) == (len(rows), filed)
    assert {
        (OUTGOING_SERVICE_OF_DIMENSION[dimension], dimension)
        for _service, dimension in (*unserviced.values(), *folded.values(), *backfilled.values())
    } == set(filed.values())


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
def test_the_upgrade_scans_the_ledger_only_after_its_transaction_commits(
    scratch_urls: tuple[str, str],
) -> None:
    migration_url, sync_url = scratch_urls
    config = _migrated_before(migration_url)
    now = datetime.now(UTC)
    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        for dimension in ("tokens", "egress"):
            connection.execute(
                sa.text(
                    "insert into ledger (id, workspace_id, dimension, amount, priced_micro_usd, "
                    "model, created_at, updated_at) values (:id, :workspace_id, :dimension, 1, 0, "
                    "'', :now, :now)"
                ).bindparams(
                    sa.bindparam("id", type_=sa.Uuid()),
                    sa.bindparam("workspace_id", type_=sa.Uuid()),
                    sa.bindparam("now", type_=sa.DateTime(timezone=True)),
                ),
                {"id": uuid4(), "workspace_id": uuid4(), "dimension": dimension, "now": now},
            )
        connection.commit()
    engine.dispose()
    statements: list[tuple[bool, str]] = []

    def capture(
        connection: sa.Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        autocommit = connection.get_execution_options().get("isolation_level") == "AUTOCOMMIT"
        statements.append((autocommit, " ".join(statement.lower().split())))

    sa.event.listen(sa.Engine, "before_cursor_execute", capture)
    try:
        command.upgrade(config, SERVICE_LEDGER)
    finally:
        sa.event.remove(sa.Engine, "before_cursor_execute", capture)
    with engine.connect() as connection:
        validated = dict(
            connection.execute(
                sa.text(
                    "select conname, convalidated from pg_constraint "
                    "where conrelid = 'ledger'::regclass and contype = 'c'"
                )
            ).all()
        )
        valid = dict(
            connection.execute(
                sa.text(
                    "select c.relname, i.indisvalid from pg_index i "
                    "join pg_class c on c.oid = i.indexrelid where i.indrelid = 'ledger'::regclass"
                )
            ).all()
        )
        unserviced = connection.execute(
            sa.text("select count(*) from ledger where service is null")
        ).scalar_one()
    engine.dispose()
    added = [
        sql
        for autocommit, sql in statements
        if not autocommit and sql.startswith("alter table ledger add constraint")
    ]
    scans = [
        autocommit
        for autocommit, sql in statements
        if "validate constraint" in sql or (sql.startswith("create index") and " on ledger " in sql)
    ]
    assert len(added) == 3
    assert all(sql.endswith(" not valid") for sql in added)
    assert scans == [True] * 6
    assert all(validated.values())
    assert LEDGER_INDEXES <= {name for name, ready in valid.items() if ready}
    assert unserviced == 2

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR
from ufo.runtime.sources.sync import SOURCE_PARK_HOLD_SECONDS, SOURCE_PARK_RETRY_SECONDS

BEFORE = "20260916035845"
REVISION = "20260914023259"

NOW = datetime.now(UTC).replace(tzinfo=None)


def _stamp(moment: datetime) -> str:
    return moment.isoformat(sep=" ")


def _config(database_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


def _seed(engine: sa.Engine, *, parked_ago: timedelta | None, held: timedelta) -> str:
    uid = uuid4().hex
    parked_at = None if parked_ago is None else NOW - parked_ago
    due = NOW + held if parked_at is None else parked_at + held
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "insert into source (uid, workspace_id, backend, config, feed_handle, "
                "connection_id, next_sync_at, consecutive_refusals, parked_at, created_at, "
                "updated_at) values (:uid, :ws, 'gmail', '{}', '{}', :conn, :due, 5, :parked_at, "
                ":now, :now)"
            ),
            {
                "uid": uid,
                "ws": uuid4().hex,
                "conn": uuid4().hex,
                "due": _stamp(due),
                "parked_at": None if parked_at is None else _stamp(parked_at),
                "now": _stamp(NOW),
            },
        )
        connection.commit()
    return uid


def _row(engine: sa.Engine, uid: str) -> sa.Row[tuple[str | None, int, str]]:
    with engine.connect() as connection:
        return connection.execute(
            sa.text(
                "select parked_since, parked_awaits_grant, next_sync_at "
                "from source where uid = :uid"
            ),
            {"uid": uid},
        ).one()


def test_the_hold_a_park_carries_says_whether_a_member_has_to_repair_it(tmp_path: Path) -> None:
    """Only the raiser knew whether a park awaits a grant, but it wrote the answer into the hold:
    an hour for a refusal that clears itself, a year for one a member must repair. Reading it there
    is what makes the notice possible at all — waking the rows instead hands them to the outgoing
    image, which names neither column and parks them for another year."""
    database = tmp_path / "source_park_facts.db"
    config = _config(database)
    command.upgrade(config, BEFORE)
    engine = sa.create_engine(f"sqlite:///{database}")
    throttled = _seed(
        engine,
        parked_ago=timedelta(hours=2),
        held=timedelta(seconds=SOURCE_PARK_RETRY_SECONDS),
    )
    withdrawn = _seed(
        engine,
        parked_ago=timedelta(days=30),
        held=timedelta(seconds=SOURCE_PARK_HOLD_SECONDS),
    )
    running = _seed(engine, parked_ago=None, held=timedelta(minutes=1))

    command.upgrade(config, REVISION)

    clears_itself = _row(engine, throttled)
    assert clears_itself.parked_awaits_grant == 0
    assert clears_itself.parked_since == _stamp(NOW - timedelta(hours=2)), (
        "the next hourly refusal rewrites it, so the last refusal is the honest seed"
    )

    awaits_grant = _row(engine, withdrawn)
    assert awaits_grant.parked_awaits_grant == 1
    assert awaits_grant.parked_since > _stamp(NOW - timedelta(days=3)), (
        "a break held a year is dated from this revision, or it is already outside every window a "
        "notice bounds itself by and its owner is never told"
    )

    healthy = _row(engine, running)
    assert healthy.parked_since is None
    assert healthy.parked_awaits_grant == 0
    assert healthy.next_sync_at == _stamp(NOW + timedelta(minutes=1)), (
        "a row that never parked is left where it was"
    )

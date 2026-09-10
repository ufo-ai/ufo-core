"""The Datadog feed key moves from the slots the last two releases wrote into the two this reads.

The registrar removes a keyed connection once none of its provider's slots is filled, so a workspace
whose Datadog secret stays in `datadog_api_key` and `datadog_application_key` — the pair the release
being replaced reads — or in the older `datadog` JSON row loses its feed, its synced pages and its
grants on the first tick after the roll. The keyed pair carries slot to slot as the sealed bytes it
is, which needs no key in the environment; each JSON value is split into `datadog_feed_api_key` and
`datadog_feed_application_key` — the slots only the host-side sync reads, never the ones the keyed
connector injects on the sandbox wire — sealed under the same fleet Fernet. Every row read stays
standing for the image being replaced to keep reading for its last minute. The keyed pair wins where
a workspace holds both shapes, a slot the workspace already filled itself is not overwritten, a
value that is not the JSON the release wrote is skipped, and a database holding a JSON row to carry
with no key in the environment stops the roll rather than losing the secret.
"""

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet

from ufo.db import MIGRATIONS_DIR

BEFORE = "20260909195911"
REVISION = "20260909203000"
NOW = "2026-09-09 20:00:00+00:00"
KEY_ENV = "UFO_CREDENTIAL_KEY"
JSON_SLOT = "datadog"
KEYED_API_SLOT = "datadog_api_key"
KEYED_APPLICATION_SLOT = "datadog_application_key"
API_SLOT = "datadog_feed_api_key"
APPLICATION_SLOT = "datadog_feed_application_key"
API_KEY = "dd-api-key-from-the-json-row"
APPLICATION_KEY = "dd-application-key-from-the-json-row"
KEYED_API_KEY = "dd-api-key-from-the-keyed-slot"
KEYED_APPLICATION_KEY = "dd-application-key-from-the-keyed-slot"
ADMIN_FILLED_APPLICATION_KEY = "dd-application-key-the-admin-filled"
INSERT_WORKSPACE = "insert into workspace (id, created_at, updated_at) values (:id, :now, :now)"
INSERT_CREDENTIAL = (
    "insert into credential (workspace_id, slot, ciphertext, created_at, updated_at) "
    "values (:ws, :slot, :ciphertext, :now, :now)"
)


def _config(database: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database}")
    return config


def _fleet_fernet(monkeypatch: pytest.MonkeyPatch) -> Fernet:
    """The one key the migrate container opens the rows with, exported exactly as the Job does."""
    key = Fernet.generate_key()
    monkeypatch.setenv(KEY_ENV, key.decode())
    return Fernet(key)


def _json_row(fernet: Fernet) -> bytes:
    return fernet.encrypt(
        json.dumps({"api_key": API_KEY, "application_key": APPLICATION_KEY}).encode()
    )


def _seed(database: Path, rows: dict[UUID, dict[str, bytes]]) -> Config:
    config = _config(database)
    command.upgrade(config, BEFORE)
    engine = sa.create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        for workspace_id, slots in rows.items():
            connection.execute(sa.text(INSERT_WORKSPACE), {"id": workspace_id.hex, "now": NOW})
            for slot, ciphertext in slots.items():
                connection.execute(
                    sa.text(INSERT_CREDENTIAL),
                    {"ws": workspace_id.hex, "slot": slot, "ciphertext": ciphertext, "now": NOW},
                )
        connection.commit()
    engine.dispose()
    return config


def _slots(database: Path, workspace_id: UUID) -> dict[str, bytes]:
    engine = sa.create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        rows = connection.execute(
            sa.text("select slot, ciphertext from credential where workspace_id = :ws"),
            {"ws": workspace_id.hex},
        ).all()
    engine.dispose()
    return {row[0]: row[1] for row in rows}


def test_the_json_row_lands_in_both_new_slots_and_stays_where_it_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fernet = _fleet_fernet(monkeypatch)
    database = tmp_path / "carry.db"
    workspace_id = uuid4()
    config = _seed(database, {workspace_id: {JSON_SLOT: _json_row(fernet)}})

    command.upgrade(config, REVISION)

    held = _slots(database, workspace_id)
    assert set(held) == {JSON_SLOT, API_SLOT, APPLICATION_SLOT}
    assert fernet.decrypt(held[API_SLOT]).decode() == API_KEY
    assert fernet.decrypt(held[APPLICATION_SLOT]).decode() == APPLICATION_KEY
    assert json.loads(fernet.decrypt(held[JSON_SLOT]).decode()) == {
        "api_key": API_KEY,
        "application_key": APPLICATION_KEY,
    }


def test_the_slots_the_outgoing_release_reads_carry_without_the_fleet_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shape the image being replaced actually writes: one row per key, under the keyed
    connector's own slot names. Each carries into the feed slot beside it as the bytes it already
    is, so the roll keeps the feed of a workspace that never held the JSON row, and both source
    rows stay standing for the outgoing image's last minute."""
    fernet = Fernet(Fernet.generate_key())
    monkeypatch.delenv(KEY_ENV, raising=False)
    database = tmp_path / "keyed.db"
    workspace_id = uuid4()
    config = _seed(
        database,
        {
            workspace_id: {
                KEYED_API_SLOT: fernet.encrypt(KEYED_API_KEY.encode()),
                KEYED_APPLICATION_SLOT: fernet.encrypt(KEYED_APPLICATION_KEY.encode()),
            }
        },
    )

    command.upgrade(config, REVISION)

    held = _slots(database, workspace_id)
    assert set(held) == {KEYED_API_SLOT, KEYED_APPLICATION_SLOT, API_SLOT, APPLICATION_SLOT}
    assert fernet.decrypt(held[API_SLOT]).decode() == KEYED_API_KEY
    assert fernet.decrypt(held[APPLICATION_SLOT]).decode() == KEYED_APPLICATION_KEY


def test_the_keyed_slot_wins_and_the_json_row_fills_what_it_leaves_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A workspace that filled the JSON row and then, on the release being replaced, one key of the
    keyed pair. The keyed value is the one that release reads, so it carries; the JSON row reaches
    only the slot the pair left empty."""
    fernet = _fleet_fernet(monkeypatch)
    database = tmp_path / "both.db"
    workspace_id = uuid4()
    config = _seed(
        database,
        {
            workspace_id: {
                JSON_SLOT: _json_row(fernet),
                KEYED_API_SLOT: fernet.encrypt(KEYED_API_KEY.encode()),
            }
        },
    )

    command.upgrade(config, REVISION)

    held = _slots(database, workspace_id)
    assert fernet.decrypt(held[API_SLOT]).decode() == KEYED_API_KEY
    assert fernet.decrypt(held[APPLICATION_SLOT]).decode() == APPLICATION_KEY


def test_a_slot_the_workspace_already_filled_is_left_as_it_stands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither shape overwrites the slot: the keyed application key and the JSON row both name the
    application slot the workspace filled itself, and the value the member set stands."""
    fernet = _fleet_fernet(monkeypatch)
    database = tmp_path / "held.db"
    workspace_id = uuid4()
    config = _seed(
        database,
        {
            workspace_id: {
                JSON_SLOT: _json_row(fernet),
                KEYED_APPLICATION_SLOT: fernet.encrypt(KEYED_APPLICATION_KEY.encode()),
                APPLICATION_SLOT: fernet.encrypt(ADMIN_FILLED_APPLICATION_KEY.encode()),
            }
        },
    )

    command.upgrade(config, REVISION)

    held = _slots(database, workspace_id)
    assert fernet.decrypt(held[API_SLOT]).decode() == API_KEY
    assert fernet.decrypt(held[APPLICATION_SLOT]).decode() == ADMIN_FILLED_APPLICATION_KEY


def test_a_row_that_is_not_the_json_the_release_wrote_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fernet = _fleet_fernet(monkeypatch)
    database = tmp_path / "junk.db"
    carried, skipped = uuid4(), uuid4()
    config = _seed(
        database,
        {
            carried: {JSON_SLOT: _json_row(fernet)},
            skipped: {JSON_SLOT: fernet.encrypt(b"not-the-json-shape")},
        },
    )

    command.upgrade(config, REVISION)

    assert set(_slots(database, carried)) == {JSON_SLOT, API_SLOT, APPLICATION_SLOT}
    assert set(_slots(database, skipped)) == {JSON_SLOT}


def test_a_database_with_no_json_row_needs_no_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(KEY_ENV, raising=False)
    database = tmp_path / "empty.db"
    workspace_id = uuid4()
    config = _seed(database, {workspace_id: {}})

    command.upgrade(config, REVISION)

    assert _slots(database, workspace_id) == {}


def test_a_row_to_carry_with_no_key_stops_the_roll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`backoffLimit: 0` turns this raise into a halted deploy, which is the safe end: the outgoing
    image keeps reading the JSON row, and no secret is lost to a key that cannot open it."""
    fernet = Fernet(Fernet.generate_key())
    monkeypatch.delenv(KEY_ENV, raising=False)
    database = tmp_path / "keyless.db"
    workspace_id = uuid4()
    config = _seed(database, {workspace_id: {JSON_SLOT: _json_row(fernet)}})

    with pytest.raises(RuntimeError, match=KEY_ENV):
        command.upgrade(config, REVISION)

    assert set(_slots(database, workspace_id)) == {JSON_SLOT}

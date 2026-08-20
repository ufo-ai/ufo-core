from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR

NOW = datetime(2026, 8, 20, tzinfo=UTC)
BEFORE = "20260819175749"
AFTER = "20260820052830"


def _config(path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path}")
    return config


def _seed(connection: sa.Connection, workspace_id: str, agent_id: str, member_id: str) -> None:
    connection.execute(
        sa.text("insert into workspace (id, created_at, updated_at) values (:id, :now, :now)"),
        {"id": workspace_id, "now": NOW},
    )
    connection.execute(
        sa.text(
            "insert into agent (id, workspace_id, name, prompt, model, created_at, updated_at) "
            "values (:id, :workspace_id, 'assistant', 'p', 'claude-opus-4-8', :now, :now)"
        ),
        {"id": agent_id, "workspace_id": workspace_id, "now": NOW},
    )
    connection.execute(
        sa.text(
            "insert into member (id, workspace_id, email, created_at, updated_at) "
            "values (:id, :workspace_id, :email, :now, :now)"
        ),
        {
            "id": member_id,
            "workspace_id": workspace_id,
            "email": f"{member_id}@example.com",
            "now": NOW,
        },
    )


def test_a_linked_phone_and_its_stream_position_move_to_the_fleet(tmp_path: Path) -> None:
    """The workspace holding the project keeps its members' phones — as fleet rows now — and the
    cursor it was storing becomes the stream's. A claim nobody proved is worth half an hour, so it
    goes with its receipt."""
    database = tmp_path / "surface-address.db"
    config = _config(database)
    command.upgrade(config, BEFORE)
    holder, agent_id, member_id = str(uuid4()), str(uuid4()), str(uuid4())
    stranded, stranded_agent, stranded_member = str(uuid4()), str(uuid4()), str(uuid4())
    engine = sa.create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        _seed(connection, holder, agent_id, member_id)
        _seed(connection, stranded, stranded_agent, stranded_member)
        for workspace_id, surface, installation_id, bound_agent in (
            (holder, "imessage", "project:one", agent_id),
            (holder, "slack", "team-a", agent_id),
        ):
            connection.execute(
                sa.text(
                    "insert into surface_installation (workspace_id, surface, installation_id, "
                    "agent_id, created_at, updated_at) values (:workspace_id, :surface, "
                    ":installation_id, :agent_id, :now, :now)"
                ),
                {
                    "workspace_id": workspace_id,
                    "surface": surface,
                    "installation_id": installation_id,
                    "agent_id": bound_agent,
                    "now": NOW,
                },
            )
        for workspace_id, owner, surface, external_id in (
            (holder, member_id, "imessage", "+14155550123"),
            (stranded, stranded_member, "imessage", "+16505550123"),
            (holder, member_id, "slack", "U1"),
        ):
            connection.execute(
                sa.text(
                    "insert into surface_identity (workspace_id, member_id, surface, external_id, "
                    "created_at, updated_at) values (:workspace_id, :member_id, :surface, "
                    ":external_id, :now, :now)"
                ),
                {
                    "workspace_id": workspace_id,
                    "member_id": owner,
                    "surface": surface,
                    "external_id": external_id,
                    "now": NOW,
                },
            )
        for extension, key, value in (
            ("imessage", "stream:shared:cursor", "41"),
            ("imessage", "phone-claim:abc", '"claim"'),
            ("imessage", "phone-receipt:abc", '"receipt"'),
            ("memory", "stream:shared:cursor", "7"),
        ):
            connection.execute(
                sa.text(
                    "insert into ext_store (workspace_id, extension, key, value, created_at, "
                    "updated_at) values (:workspace_id, :extension, :key, :value, :now, :now)"
                ),
                {
                    "workspace_id": holder,
                    "extension": extension,
                    "key": key,
                    "value": value,
                    "now": NOW,
                },
            )
        connection.commit()
    engine.dispose()

    command.upgrade(config, AFTER)

    engine = sa.create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        addresses = connection.execute(
            sa.text(
                "select surface, address, workspace_id, member_id, claim_expires_at, proved_by "
                "from surface_address"
            )
        ).all()
        identities = connection.execute(
            sa.text("select surface, external_id from surface_identity")
        ).all()
        cursors = connection.execute(
            sa.text(
                "select surface, installation_id, sequence, workspace_id from surface_stream_cursor"
            )
        ).all()
        store = connection.execute(sa.text("select extension, key from ext_store")).all()
        installations = connection.execute(
            sa.text("select surface, installation_id, routes_ingress from surface_installation")
        ).all()
    engine.dispose()

    assert addresses == [("imessage", "+14155550123", holder, member_id, None, None)]
    assert identities == [("slack", "U1")]
    assert cursors == [("imessage", "project:one", 41, None)]
    assert set(store) == {("memory", "stream:shared:cursor")}
    assert sorted(installations) == [("imessage", "project:one", 0), ("slack", "team-a", 1)]


def test_the_installation_identity_stays_unique_only_where_it_routes(tmp_path: Path) -> None:
    """A customer's own account belongs to one workspace and the index still says so. The deploy's
    own project routes nothing, so every workspace binds the same one."""
    database = tmp_path / "surface-address-uniqueness.db"
    config = _config(database)
    command.upgrade(config, AFTER)
    first, second = str(uuid4()), str(uuid4())
    engine = sa.create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        agents = {}
        for workspace_id in (first, second):
            agent_id, member_id = str(uuid4()), str(uuid4())
            agents[workspace_id] = agent_id
            _seed(connection, workspace_id, agent_id, member_id)

        def bind(workspace_id: str, surface: str, installation_id: str, routes: int) -> None:
            connection.execute(
                sa.text(
                    "insert into surface_installation (workspace_id, surface, installation_id, "
                    "agent_id, routes_ingress, created_at, updated_at) values (:workspace_id, "
                    ":surface, :installation_id, :agent_id, :routes, :now, :now)"
                ),
                {
                    "workspace_id": workspace_id,
                    "surface": surface,
                    "installation_id": installation_id,
                    "agent_id": agents[workspace_id],
                    "routes": routes,
                    "now": NOW,
                },
            )

        bind(first, "imessage", "project:one", 0)
        bind(second, "imessage", "project:one", 0)
        bind(first, "slack", "team-a", 1)
        with pytest.raises(sa.exc.IntegrityError):
            bind(second, "slack", "team-a", 1)
    engine.dispose()

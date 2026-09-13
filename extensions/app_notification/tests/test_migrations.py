"""The extension's migrations carry each release's declaration onto the `notification` agent rows a
workspace already holds: a provisioning pass writes setup and purpose alone, so without this the
prompt and the allowlist a release declares reach new workspaces only. The prompt moves where the
row still says what the release before said — a member's own wording stands — the allowlist moves on
every live shipped row, and an archived row and another extension's row stand."""

import json
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from ufo_ext_app_notification.manifest import (
    NAME,
    NOTIFICATION_AGENT,
    NOTIFICATION_AGENT_PROMPT,
    VERSION,
)
from ufo_ext_app_notification.store import notification as notification_table
from ufo_ext_app_notification.store import notification_delivery as notification_delivery_table

from ufo.db import MIGRATIONS_DIR
from ufo.host.ext.loader import migration_locations
from ufo.schema import tables
from ufo.schema.records import TurnRuntimeConfig

CORE_HEAD = (MIGRATIONS_DIR / "versions" / "HEAD").read_text().strip()
RELEASED = "notification_0001"
TRIAGE = "notification_0002"
DELIVERY = "notification_0003"
CARRY = "notification_0004"
SPAWN = "notification_0005"
RECONNECT = "notification_0006"
BRIEF = "notification_0007"
SCOPE = "notification_0008"
EDITED_PROMPT = "only tell me about churn"


def _config(database_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option(
        "version_locations",
        "\n".join((str(MIGRATIONS_DIR / "versions"), *migration_locations())),
    )
    config.set_main_option("path_separator", "newline")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


def _revision(revision: str) -> ModuleType:
    return ScriptDirectory.from_config(_config(Path("unused.db"))).get_revision(revision).module


TRIAGE_MODULE = _revision(TRIAGE)
DELIVERY_MODULE = _revision(DELIVERY)
SPAWN_MODULE = _revision(SPAWN)
RECONNECT_MODULE = _revision(RECONNECT)
BRIEF_MODULE = _revision(BRIEF)
RELEASED_PROMPT: str = TRIAGE_MODULE.RELEASED_PROMPT
RELEASED_VERSION: str = TRIAGE_MODULE.RELEASED_VERSION
TRIAGE_PROMPT: str = DELIVERY_MODULE.PREVIOUS_PROMPT
TRIAGE_VERSION: str = DELIVERY_MODULE.PREVIOUS_VERSION
TRIAGE_TOOLS: list[str] = list(DELIVERY_MODULE.PREVIOUS_TOOLS)
DELIVERY_PROMPT: str = SPAWN_MODULE.PREVIOUS_PROMPT
DELIVERY_VERSION: str = SPAWN_MODULE.PREVIOUS_VERSION
DELIVERY_TOOLS: list[str] = list(SPAWN_MODULE.PREVIOUS_TOOLS)
SPAWN_PROMPT: str = RECONNECT_MODULE.PREVIOUS_PROMPT
SPAWN_VERSION: str = RECONNECT_MODULE.PREVIOUS_VERSION
RECONNECT_PROMPT: str = BRIEF_MODULE.PREVIOUS_PROMPT
RECONNECT_VERSION: str = BRIEF_MODULE.PREVIOUS_VERSION
LIVE_TOOLS = list(NOTIFICATION_AGENT.tools or ())


def _seed(database_path: Path, prompt: str, version: str, tools: list[str] | None) -> sa.Engine:
    """One workspace holding the shipped row as the release left it, one whose member rewrote the
    prompt, one whose shipped row is archived, and another extension's row saying the same words."""
    engine = sa.create_engine(f"sqlite:///{database_path}")
    encoded = None if tools is None else json.dumps(tools)
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "insert into workspace (id, created_at, updated_at) values "
                "('w', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('x', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('y', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            sa.text(
                "insert into agent (id, workspace_id, name, prompt, tools, model, is_main, "
                "visibility, provisioned_by, provisioned_name, provisioned_version, created_at, "
                "updated_at) values (:id, :ws, :name, :prompt, :tools, 'auto', 0, 'workspace', "
                ":by, :declared, :version, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            [
                {
                    "id": "shipped",
                    "ws": "w",
                    "name": "notification",
                    "prompt": prompt,
                    "tools": encoded,
                    "by": NAME,
                    "declared": "notification",
                    "version": version,
                },
                {
                    "id": "edited",
                    "ws": "x",
                    "name": "notification-app-notification",
                    "prompt": EDITED_PROMPT,
                    "tools": encoded,
                    "by": NAME,
                    "declared": "notification",
                    "version": version,
                },
                {
                    "id": "other",
                    "ws": "w",
                    "name": "radar",
                    "prompt": prompt,
                    "tools": encoded,
                    "by": "app_radar",
                    "declared": "radar",
                    "version": version,
                },
            ],
        )
        connection.execute(
            sa.text(
                "insert into agent (id, workspace_id, name, archived_name, archived_at, prompt, "
                "tools, model, is_main, visibility, provisioned_by, provisioned_name, "
                "provisioned_version, created_at, updated_at) values "
                "('archived', 'y', '~archived-a', 'notification', CURRENT_TIMESTAMP, :prompt, "
                ":tools, 'auto', 0, 'workspace', :by, 'notification', :version, CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP)"
            ),
            {"prompt": prompt, "tools": encoded, "by": NAME, "version": version},
        )
        connection.commit()
    return engine


def _rows(engine: sa.Engine) -> dict[str, tuple[str, str, list[str] | None]]:
    with engine.connect() as connection:
        return {
            row.id: (
                row.prompt,
                row.provisioned_version,
                None if row.tools is None else json.loads(row.tools),
            )
            for row in connection.execute(
                sa.text("select id, prompt, provisioned_version, tools from agent")
            ).all()
        }


def test_the_triage_prompt_reaches_the_shipped_row_and_no_other(tmp_path: Path) -> None:
    database_path = tmp_path / "notification.db"
    config = _config(database_path)
    command.upgrade(config, CORE_HEAD)
    command.upgrade(config, RELEASED)
    engine = _seed(database_path, RELEASED_PROMPT, RELEASED_VERSION, None)
    command.upgrade(config, TRIAGE)
    after = _rows(engine)
    command.downgrade(config, RELEASED)
    restored = _rows(engine)

    assert after["shipped"] == (NOTIFICATION_AGENT_PROMPT, VERSION, None)
    assert after["edited"] == (EDITED_PROMPT, RELEASED_VERSION, None)
    assert after["other"] == (RELEASED_PROMPT, RELEASED_VERSION, None)
    assert after["archived"] == (RELEASED_PROMPT, RELEASED_VERSION, None)
    assert restored["shipped"] == (RELEASED_PROMPT, RELEASED_VERSION, None)


def test_the_deliver_grant_reaches_every_live_shipped_row_and_the_prompt_the_unedited_one(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "notification.db"
    config = _config(database_path)
    command.upgrade(config, CORE_HEAD)
    command.upgrade(config, TRIAGE)
    engine = _seed(database_path, TRIAGE_PROMPT, TRIAGE_VERSION, TRIAGE_TOOLS)
    command.upgrade(config, DELIVERY)
    after = _rows(engine)
    command.downgrade(config, TRIAGE)
    restored = _rows(engine)

    assert "action:notification:deliver" in DELIVERY_TOOLS
    assert "action:notification:deliver" not in TRIAGE_TOOLS
    assert after["shipped"] == (NOTIFICATION_AGENT_PROMPT, VERSION, LIVE_TOOLS)
    assert after["edited"] == (EDITED_PROMPT, VERSION, LIVE_TOOLS)
    assert after["other"] == (TRIAGE_PROMPT, TRIAGE_VERSION, TRIAGE_TOOLS)
    assert after["archived"] == (TRIAGE_PROMPT, TRIAGE_VERSION, TRIAGE_TOOLS)
    assert restored["shipped"] == (TRIAGE_PROMPT, TRIAGE_VERSION, TRIAGE_TOOLS)


def test_the_carry_reaches_a_row_the_roll_window_created_behind_the_release(
    tmp_path: Path,
) -> None:
    """A workspace whose first turn landed on an outgoing pod after the migrate Job holds a row at
    an earlier release's prompt and allowlist — at that release's version, or at the current one
    where a provisioning pass moved the version without touching the prompt. The carry finds both
    by what they say, and a member's own wording, other extensions' rows and archived rows stand."""
    database_path = tmp_path / "notification.db"
    config = _config(database_path)
    command.upgrade(config, CORE_HEAD)
    command.upgrade(config, DELIVERY)
    engine = _seed(database_path, RELEASED_PROMPT, RELEASED_VERSION, TRIAGE_TOOLS)
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "insert into workspace (id, created_at, updated_at) values "
                "('z', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            sa.text(
                "insert into agent (id, workspace_id, name, prompt, tools, model, is_main, "
                "visibility, provisioned_by, provisioned_name, provisioned_version, created_at, "
                "updated_at) values ('versioned', 'z', 'notification', :prompt, :tools, 'auto', 0, "
                "'workspace', :by, 'notification', :version, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {
                "prompt": TRIAGE_PROMPT,
                "tools": json.dumps(TRIAGE_TOOLS),
                "by": NAME,
                "version": VERSION,
            },
        )
        connection.commit()
    command.upgrade(config, CARRY)
    after = _rows(engine)

    assert after["shipped"] == (NOTIFICATION_AGENT_PROMPT, VERSION, LIVE_TOOLS)
    assert after["versioned"] == (NOTIFICATION_AGENT_PROMPT, VERSION, LIVE_TOOLS)
    assert after["edited"] == (EDITED_PROMPT, VERSION, LIVE_TOOLS)
    assert after["other"] == (RELEASED_PROMPT, RELEASED_VERSION, TRIAGE_TOOLS)
    assert after["archived"] == (RELEASED_PROMPT, RELEASED_VERSION, TRIAGE_TOOLS)


def test_the_spawn_grant_reaches_every_live_shipped_row(tmp_path: Path) -> None:
    """The release that lets the app put work where it belongs adds no verb and no column: it
    grants the spawn every turn already has. Every live shipped row takes the allowlist, since no
    member can write one, and the prompt moves where the row still reads as any earlier release
    wrote it."""
    database_path = tmp_path / "notification.db"
    config = _config(database_path)
    command.upgrade(config, CORE_HEAD)
    command.upgrade(config, CARRY)
    engine = _seed(database_path, DELIVERY_PROMPT, DELIVERY_VERSION, DELIVERY_TOOLS)
    command.upgrade(config, SPAWN)
    after = _rows(engine)
    command.downgrade(config, CARRY)
    restored = _rows(engine)

    assert "spawn" in LIVE_TOOLS
    assert "spawn" not in DELIVERY_TOOLS
    assert not any(tool.endswith(":hand_off") for tool in LIVE_TOOLS)
    assert after["shipped"] == (NOTIFICATION_AGENT_PROMPT, VERSION, LIVE_TOOLS)
    assert after["edited"] == (EDITED_PROMPT, VERSION, LIVE_TOOLS)
    assert after["other"] == (DELIVERY_PROMPT, DELIVERY_VERSION, DELIVERY_TOOLS)
    assert after["archived"] == (DELIVERY_PROMPT, DELIVERY_VERSION, DELIVERY_TOOLS)
    assert restored["shipped"] == (DELIVERY_PROMPT, DELIVERY_VERSION, DELIVERY_TOOLS)


def test_the_spawn_grant_reaches_a_row_two_releases_behind(tmp_path: Path) -> None:
    """Each release leaves rows behind it, so the roll window that put a row at the release before
    last is still open when this one runs: the carry that was meant to move it had already run. The
    prompt moves off every earlier text, or the row runs the old instructions under this version."""
    database_path = tmp_path / "notification.db"
    config = _config(database_path)
    command.upgrade(config, CORE_HEAD)
    command.upgrade(config, CARRY)
    engine = _seed(database_path, TRIAGE_PROMPT, TRIAGE_VERSION, TRIAGE_TOOLS)
    command.upgrade(config, SPAWN)
    after = _rows(engine)

    assert after["shipped"] == (NOTIFICATION_AGENT_PROMPT, VERSION, LIVE_TOOLS)
    assert after["edited"] == (EDITED_PROMPT, VERSION, LIVE_TOOLS)
    assert after["other"] == (TRIAGE_PROMPT, TRIAGE_VERSION, TRIAGE_TOOLS)
    assert after["archived"] == (TRIAGE_PROMPT, TRIAGE_VERSION, TRIAGE_TOOLS)


def test_the_reconnect_prompt_reaches_the_shipped_row_and_no_other(tmp_path: Path) -> None:
    """The release that sends a member's broken account to them carries the consumer for it: the
    triage prompt that passes such a row on instead of dropping it as a routine sync. The prompt
    moves where the row still reads as any earlier release wrote it, a member's own wording stands,
    and other extensions' rows and archived rows stand."""
    database_path = tmp_path / "notification.db"
    config = _config(database_path)
    command.upgrade(config, CORE_HEAD)
    command.upgrade(config, SPAWN)
    engine = _seed(database_path, SPAWN_PROMPT, SPAWN_VERSION, LIVE_TOOLS)
    command.upgrade(config, RECONNECT)
    after = _rows(engine)
    command.downgrade(config, SPAWN)
    restored = _rows(engine)

    assert "reconnecting it is a thing only they can do" in NOTIFICATION_AGENT_PROMPT
    assert "reconnecting it" not in SPAWN_PROMPT
    assert after["shipped"] == (NOTIFICATION_AGENT_PROMPT, VERSION, LIVE_TOOLS)
    assert after["edited"] == (EDITED_PROMPT, VERSION, LIVE_TOOLS)
    assert after["other"] == (SPAWN_PROMPT, SPAWN_VERSION, LIVE_TOOLS)
    assert after["archived"] == (SPAWN_PROMPT, SPAWN_VERSION, LIVE_TOOLS)
    assert restored["shipped"] == (SPAWN_PROMPT, SPAWN_VERSION, LIVE_TOOLS)


def test_the_reconnect_carry_repairs_an_allowlist_left_two_releases_behind(tmp_path: Path) -> None:
    """A row the roll window left on an older release holds that release's allowlist, and no
    provisioning pass repairs one — `_fill` writes setup and purpose alone. Stamping the version
    without the allowlist would leave such a row reporting this release while holding an allowlist
    with no `spawn`, running a prompt that orders it."""
    database_path = tmp_path / "notification.db"
    config = _config(database_path)
    command.upgrade(config, CORE_HEAD)
    command.upgrade(config, SPAWN)
    engine = _seed(database_path, DELIVERY_PROMPT, DELIVERY_VERSION, TRIAGE_TOOLS)
    command.upgrade(config, RECONNECT)
    after = _rows(engine)

    assert "spawn" not in TRIAGE_TOOLS
    assert after["shipped"] == (NOTIFICATION_AGENT_PROMPT, VERSION, LIVE_TOOLS)
    assert after["edited"] == (EDITED_PROMPT, VERSION, LIVE_TOOLS)
    assert after["other"] == (DELIVERY_PROMPT, DELIVERY_VERSION, TRIAGE_TOOLS)
    assert after["archived"] == (DELIVERY_PROMPT, DELIVERY_VERSION, TRIAGE_TOOLS)


def test_the_reconnect_prompt_reaches_a_row_two_releases_behind(tmp_path: Path) -> None:
    """Each release leaves rows behind it, so a row the roll window put at the release before last
    is still there when this one runs. It moves off every earlier text, or it runs the older
    instructions under this version."""
    database_path = tmp_path / "notification.db"
    config = _config(database_path)
    command.upgrade(config, CORE_HEAD)
    command.upgrade(config, SPAWN)
    engine = _seed(database_path, DELIVERY_PROMPT, DELIVERY_VERSION, DELIVERY_TOOLS)
    command.upgrade(config, RECONNECT)
    after = _rows(engine)

    assert after["shipped"] == (NOTIFICATION_AGENT_PROMPT, VERSION, LIVE_TOOLS)
    assert after["edited"] == (EDITED_PROMPT, VERSION, LIVE_TOOLS)
    assert after["other"] == (DELIVERY_PROMPT, DELIVERY_VERSION, DELIVERY_TOOLS)


def test_the_brief_prompt_reaches_the_shipped_row_and_no_other(tmp_path: Path) -> None:
    """`deliver` hands its text to the member's own agent, which says it in its own voice, so the
    release asks the app for a brief rather than the message a person reads. The prompt moves where
    the row still reads as any earlier release wrote it, a member's own wording stands, and other
    extensions' rows and archived rows stand."""
    database_path = tmp_path / "notification.db"
    config = _config(database_path)
    command.upgrade(config, CORE_HEAD)
    command.upgrade(config, RECONNECT)
    engine = _seed(database_path, RECONNECT_PROMPT, RECONNECT_VERSION, LIVE_TOOLS)
    command.upgrade(config, BRIEF)
    after = _rows(engine)
    command.downgrade(config, RECONNECT)
    restored = _rows(engine)

    assert "puts it to them in its own voice" in NOTIFICATION_AGENT_PROMPT
    assert "in your own words" in RECONNECT_PROMPT
    assert "in your own words" not in NOTIFICATION_AGENT_PROMPT
    assert after["shipped"] == (NOTIFICATION_AGENT_PROMPT, VERSION, LIVE_TOOLS)
    assert after["edited"] == (EDITED_PROMPT, VERSION, LIVE_TOOLS)
    assert after["other"] == (RECONNECT_PROMPT, RECONNECT_VERSION, LIVE_TOOLS)
    assert after["archived"] == (RECONNECT_PROMPT, RECONNECT_VERSION, LIVE_TOOLS)
    assert restored["shipped"] == (RECONNECT_PROMPT, RECONNECT_VERSION, LIVE_TOOLS)


def test_the_brief_prompt_reaches_a_row_two_releases_behind(tmp_path: Path) -> None:
    """The roll window leaves rows behind: one created by the release before last still holds that
    release's prompt when this carry runs, so the earlier texts are the whole set, not the last."""
    database_path = tmp_path / "notification.db"
    config = _config(database_path)
    command.upgrade(config, CORE_HEAD)
    command.upgrade(config, RECONNECT)
    engine = _seed(database_path, SPAWN_PROMPT, SPAWN_VERSION, LIVE_TOOLS)
    command.upgrade(config, BRIEF)
    after = _rows(engine)

    assert after["shipped"] == (NOTIFICATION_AGENT_PROMPT, VERSION, LIVE_TOOLS)
    assert after["other"] == (SPAWN_PROMPT, SPAWN_VERSION, LIVE_TOOLS)


def test_notification_runtime_backfill_and_outgoing_writes_fail_closed(tmp_path: Path) -> None:
    database_path = tmp_path / "notification.db"
    config = _config(database_path)
    command.upgrade(config, CORE_HEAD)
    command.upgrade(config, BRIEF)
    engine = sa.create_engine(f"sqlite:///{database_path}")
    workspace_id, member_id, producer_id, inbox_id, conversation_id = (uuid4() for _ in range(5))
    scoped_turn, ordinary_turn, relay_turn, connection_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    now = datetime.now(UTC)
    with engine.connect() as connection:
        connection.execute(
            sa.insert(tables.workspace).values(id=workspace_id, created_at=now, updated_at=now)
        )
        connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="member@example.com",
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            sa.insert(tables.agent),
            [
                {
                    "id": producer_id,
                    "workspace_id": workspace_id,
                    "name": "assistant",
                    "prompt": "p",
                    "model": "claude-opus-4-8",
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": inbox_id,
                    "workspace_id": workspace_id,
                    "name": "notification",
                    "prompt": "p",
                    "model": "claude-opus-4-8",
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        )
        connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=producer_id,
                surface="cli",
                queue_key="migration",
                member_id=member_id,
                audience=f"member:{member_id}",
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            sa.insert(tables.turn),
            [
                {
                    "id": scoped_turn,
                    "workspace_id": workspace_id,
                    "conversation_id": conversation_id,
                    "agent_id": producer_id,
                    "seq": 1,
                    "status": "running",
                    "inbound": "scoped",
                    "idempotency_key": None,
                    "on_behalf_of_member_id": None,
                    "speaker_member_id": member_id,
                    "runtime_config": TurnRuntimeConfig(
                        internet_access=False, connections=(connection_id,)
                    ).model_dump(mode="json"),
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": ordinary_turn,
                    "workspace_id": workspace_id,
                    "conversation_id": conversation_id,
                    "agent_id": producer_id,
                    "seq": 2,
                    "status": "running",
                    "inbound": "ordinary",
                    "idempotency_key": None,
                    "on_behalf_of_member_id": None,
                    "speaker_member_id": member_id,
                    "runtime_config": None,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": relay_turn,
                    "workspace_id": workspace_id,
                    "conversation_id": conversation_id,
                    "agent_id": producer_id,
                    "seq": 3,
                    "status": "running",
                    "inbound": "relay",
                    "runtime_config": None,
                    "speaker_member_id": None,
                    "on_behalf_of_member_id": member_id,
                    "idempotency_key": f"notify-deliver:{ordinary_turn.hex}",
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        )
        connection.execute(
            sa.insert(notification_table),
            [
                {
                    "id": uuid4(),
                    "workspace_id": workspace_id,
                    "to_agent_id": inbox_id,
                    "member_id": member_id,
                    "subject": "source/scoped",
                    "body": "scoped",
                    "occurrences": 1,
                    "produced_by_agent_id": producer_id,
                    "produced_by_agent_name": "assistant",
                    "produced_by_turn_id": scoped_turn,
                    "produced_in_conversation_id": conversation_id,
                    "delivered_turn_id": relay_turn,
                    "delivered_surface": "slack",
                    "last_raised_at": now,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": uuid4(),
                    "workspace_id": workspace_id,
                    "to_agent_id": inbox_id,
                    "member_id": member_id,
                    "subject": "source/folded",
                    "body": "latest body with uncertain provenance",
                    "occurrences": 2,
                    "produced_by_agent_id": producer_id,
                    "produced_by_agent_name": "assistant",
                    "produced_by_turn_id": ordinary_turn,
                    "produced_in_conversation_id": conversation_id,
                    "delivered_turn_id": None,
                    "delivered_surface": None,
                    "last_raised_at": now,
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        )
        connection.commit()

    command.upgrade(config, SCOPE)
    with engine.connect() as connection:
        migrated = {
            row.subject: row
            for row in connection.execute(
                sa.select(
                    notification_table.c.subject,
                    notification_table.c.runtime_config,
                    notification_table.c.occurrences,
                    notification_table.c.scope_occurrences,
                )
            )
        }
        [delivery] = connection.execute(sa.select(notification_delivery_table)).all()
        connection.execute(
            sa.insert(notification_table).values(
                id=uuid4(),
                workspace_id=workspace_id,
                to_agent_id=inbox_id,
                member_id=member_id,
                subject="source/outgoing",
                body="written without runtime config",
                occurrences=1,
                produced_by_agent_id=producer_id,
                produced_by_agent_name="assistant",
                produced_by_turn_id=ordinary_turn,
                produced_in_conversation_id=conversation_id,
                last_raised_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            sa.update(notification_table)
            .where(notification_table.c.subject == "source/scoped")
            .values(occurrences=notification_table.c.occurrences + 1)
        )
        rolling = {
            row.subject: row
            for row in connection.execute(
                sa.select(
                    notification_table.c.subject,
                    notification_table.c.runtime_config,
                    notification_table.c.occurrences,
                    notification_table.c.scope_occurrences,
                )
            )
        }
        connection.commit()

    assert TurnRuntimeConfig.model_validate(
        json.loads(migrated["source/scoped"].runtime_config)
    ) == TurnRuntimeConfig(internet_access=False, connections=(connection_id,))
    assert migrated["source/scoped"].scope_occurrences == 1
    assert delivery.delivery_key == f"notify-deliver:{ordinary_turn.hex}"
    assert delivery.request_digest is None
    assert delivery.conversation_id == conversation_id
    assert delivery.agent_id == producer_id
    assert delivery.surface == "slack"
    assert delivery.relay_turn_id == relay_turn
    assert TurnRuntimeConfig.model_validate(
        json.loads(migrated["source/folded"].runtime_config)
    ) == TurnRuntimeConfig(internet_access=False, connections=())
    assert migrated["source/folded"].scope_occurrences == 2
    assert rolling["source/outgoing"].runtime_config is None
    assert rolling["source/outgoing"].scope_occurrences is None
    assert rolling["source/scoped"].occurrences == 2
    assert rolling["source/scoped"].scope_occurrences == 1

    command.downgrade(config, BRIEF)
    columns = {column["name"] for column in sa.inspect(engine).get_columns("notification")}
    assert "runtime_config" not in columns
    assert "scope_occurrences" not in columns
    assert "notification_delivery" not in sa.inspect(engine).get_table_names()

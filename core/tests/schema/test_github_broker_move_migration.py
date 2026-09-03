"""GitHub changes brokers, so every connection made through the old one is disconnected.

A `connection` row names an account by the id of the broker that minted it. `github` moves from
Composio to Pipedream, and the registry resolves a provider to exactly one connector, so every row
that survived would point at an account the new broker 404s on: the portal would keep listing
GitHub as connected, no token would reach the wire, and a member who reconnected would seat a
second account beside the stale one — two accounts a static `GH_TOKEN` cannot name, so the sandbox
would export none and the member would need someone to delete a row by hand.

Removing them is `ConnectionStore.disconnect` in SQL, and the proof is that the rows left behind
match what that verb leaves. Nothing else in the schema may move: another provider's connection,
its grant, its source, its pages, and a credential slot the release keeps all stay exactly as they
were.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR

PARENT = "20260902223926"
REVISION = "20260902225627"
MOMENT = datetime(2026, 9, 2, tzinfo=UTC)
CONNECT_GITHUB_ACTION = "action:credential:connect_github"


def _config(path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path}")
    return config


class _Seeded:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.config = _config(path)
        self.workspace_id = uuid4()
        self.member_id = uuid4()
        self.agent_id = uuid4()
        self.conversation_id = uuid4()
        self.github_connection = uuid4()
        self.gmail_connection = uuid4()
        self.github_grant = uuid4()
        self.gmail_grant = uuid4()
        self.github_source = uuid4()
        self.gmail_source = uuid4()
        self.github_page = uuid4()
        self.gmail_page = uuid4()


def _seed(path: Path) -> _Seeded:
    """A workspace one release before the move: GitHub connected through Composio with everything a
    connection accumulates — an agent's grant, a registered source, that source's pages and its own
    grant — beside a Gmail connection carrying the same shape, and the GitHub App's own rows."""
    seeded = _Seeded(path)
    command.upgrade(seeded.config, PARENT)
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        connection.execute(
            sa.text("insert into workspace (id, created_at, updated_at) values (:id, :at, :at)"),
            {"id": seeded.workspace_id.hex, "at": MOMENT},
        )
        connection.execute(
            sa.text(
                "insert into member (id, workspace_id, email, created_at, updated_at) "
                "values (:id, :ws, 'owner@x.test', :at, :at)"
            ),
            {"id": seeded.member_id.hex, "ws": seeded.workspace_id.hex, "at": MOMENT},
        )
        connection.execute(
            sa.text(
                "insert into agent (id, workspace_id, name, prompt, model, "
                "internet_access_allowed, tools, created_at, updated_at) "
                "values (:id, :ws, 'ufo', 'p', 'm', true, :tools, :at, :at)"
            ),
            {
                "id": seeded.agent_id.hex,
                "ws": seeded.workspace_id.hex,
                "tools": json.dumps([CONNECT_GITHUB_ACTION, "search"]),
                "at": MOMENT,
            },
        )
        connection.execute(
            sa.text(
                "insert into conversation (id, workspace_id, agent_id, surface, queue_key, "
                "member_id, audience, created_at, updated_at) "
                "values (:id, :ws, :agent, 'cli', 'session', :member, :audience, :at, :at)"
            ),
            {
                "id": seeded.conversation_id.hex,
                "ws": seeded.workspace_id.hex,
                "agent": seeded.agent_id.hex,
                "member": seeded.member_id.hex,
                "audience": f"member:{seeded.member_id}",
                "at": MOMENT,
            },
        )
        for connection_id, provider, account, host in (
            (seeded.github_connection, "github", "ca_composio_github", "api.github.com"),
            (seeded.gmail_connection, "gmail", "ca_composio_gmail", "gmail.googleapis.com"),
        ):
            connection.execute(
                sa.text(
                    "insert into connection (id, workspace_id, provider, account_id, host, "
                    "owner_member_id, conversation_id, shared, created_at, updated_at) "
                    "values (:id, :ws, :provider, :account, :host, :member, :conversation, "
                    "true, :at, :at)"
                ),
                {
                    "id": connection_id.hex,
                    "ws": seeded.workspace_id.hex,
                    "provider": provider,
                    "account": account,
                    "host": host,
                    "member": seeded.member_id.hex,
                    "conversation": seeded.conversation_id.hex,
                    "at": MOMENT,
                },
            )
        for grant_id, connection_id in (
            (seeded.github_grant, seeded.github_connection),
            (seeded.gmail_grant, seeded.gmail_connection),
        ):
            connection.execute(
                sa.text(
                    "insert into connector_grant (id, workspace_id, agent_id, connection_id, "
                    "conversation_id, created_at, updated_at) "
                    "values (:id, :ws, :agent, :connection, :conversation, :at, :at)"
                ),
                {
                    "id": grant_id.hex,
                    "ws": seeded.workspace_id.hex,
                    "agent": seeded.agent_id.hex,
                    "connection": connection_id.hex,
                    "conversation": seeded.conversation_id.hex,
                    "at": MOMENT,
                },
            )
        for source_id, backend, connection_id in (
            (seeded.github_source, "github", seeded.github_connection),
            (seeded.gmail_source, "gmail", seeded.gmail_connection),
        ):
            connection.execute(
                sa.text(
                    "insert into source (id, workspace_id, backend, config, subject, "
                    "owner_member_id, connection_id, next_sync_at, claimed_by, "
                    "claim_expires_at, created_at, updated_at) "
                    "values (:id, :ws, :backend, '{}', :subject, :member, :connection, :at, "
                    "'jobs-1', :at, :at, :at)"
                ),
                {
                    "id": source_id.hex,
                    "ws": seeded.workspace_id.hex,
                    "backend": backend,
                    "subject": f"member:{seeded.member_id}",
                    "member": seeded.member_id.hex,
                    "connection": connection_id.hex,
                    "at": MOMENT,
                },
            )
            connection.execute(
                sa.text(
                    "insert into source_grant (workspace_id, source_id, agent_id, "
                    "created_at, updated_at) values (:ws, :source, :agent, :at, :at)"
                ),
                {
                    "ws": seeded.workspace_id.hex,
                    "source": source_id.hex,
                    "agent": seeded.agent_id.hex,
                    "at": MOMENT,
                },
            )
        for page_id, source_id in (
            (seeded.github_page, seeded.github_source),
            (seeded.gmail_page, seeded.gmail_source),
        ):
            connection.execute(
                sa.text(
                    "insert into page (id, workspace_id, source_id, digest, body_ref, subject, "
                    "tombstone, created_at, updated_at) "
                    "values (:id, :ws, :source, 'd', 'b', :subject, false, :at, :at)"
                ),
                {
                    "id": page_id.hex,
                    "ws": seeded.workspace_id.hex,
                    "source": source_id.hex,
                    "subject": f"member:{seeded.member_id}",
                    "at": MOMENT,
                },
            )
        for slot in ("github_app_installation", "github_git_token", "datadog_api_key"):
            connection.execute(
                sa.text(
                    "insert into credential (workspace_id, slot, ciphertext, "
                    "created_at, updated_at) values (:ws, :slot, :cipher, :at, :at)"
                ),
                {
                    "ws": seeded.workspace_id.hex,
                    "slot": slot,
                    "cipher": b"sealed",
                    "at": MOMENT,
                },
            )
            connection.execute(
                sa.text(
                    "insert into credential_fulfillment (workspace_id, request_id, slot, "
                    "member_id, fulfilled_at) values (:ws, :request, :slot, :member, :at)"
                ),
                {
                    "ws": seeded.workspace_id.hex,
                    "request": uuid4().hex,
                    "slot": slot,
                    "member": seeded.member_id.hex,
                    "at": MOMENT,
                },
            )
        connection.commit()
    engine.dispose()
    return seeded


def test_the_move_disconnects_every_composio_github_connection(tmp_path: Path) -> None:
    seeded = _seed(tmp_path / "github-move.db")

    command.upgrade(seeded.config, REVISION)

    engine = sa.create_engine(f"sqlite:///{seeded.path}")
    with engine.connect() as connection:
        connections = connection.execute(sa.text("select id, provider from connection")).all()
        grants = connection.execute(sa.text("select id from connector_grant")).all()
        sources = connection.execute(
            sa.text(
                "select id, connection_id, removed_at, claimed_by, claim_expires_at from source"
            )
        ).all()
        source_grants = connection.execute(sa.text("select source_id from source_grant")).all()
        pages = connection.execute(sa.text("select id, tombstone from page")).all()
        slots = connection.execute(sa.text("select slot from credential")).all()
        fulfillments = connection.execute(sa.text("select slot from credential_fulfillment")).all()
        tools = connection.execute(sa.text("select tools from agent")).scalar_one()
    engine.dispose()

    assert [(UUID(row.id), row.provider) for row in connections] == [
        (seeded.gmail_connection, "gmail")
    ]
    assert [UUID(row.id) for row in grants] == [seeded.gmail_grant]
    detached = next(row for row in sources if UUID(row.id) == seeded.github_source)
    assert detached.connection_id is None
    assert detached.removed_at is not None
    assert detached.claimed_by is None
    assert detached.claim_expires_at is None
    kept = next(row for row in sources if UUID(row.id) == seeded.gmail_source)
    assert UUID(kept.connection_id) == seeded.gmail_connection
    assert kept.removed_at is None
    assert kept.claimed_by == "jobs-1"
    assert [UUID(row.source_id) for row in source_grants] == [seeded.gmail_source]
    assert {UUID(row.id): bool(row.tombstone) for row in pages} == {
        seeded.github_page: True,
        seeded.gmail_page: False,
    }
    assert [row.slot for row in slots] == ["datadog_api_key"]
    assert [row.slot for row in fulfillments] == ["datadog_api_key"]
    assert json.loads(tools) == ["search"]

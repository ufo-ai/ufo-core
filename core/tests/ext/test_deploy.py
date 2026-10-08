import asyncio
import logging
import shutil
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_sample.manifest as sample
import ufo_pack_sample as sample_pack
from click.testing import CliRunner
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url
from starlette.responses import Response
from ufo_ext_sample.deploy import (
    FLAGS_COMMAND,
    FLEET_PATH,
    MEMBERSHIPS_COMMAND,
    REFUSED_SEAT,
    SEAT_KEY,
    SEAT_PATH,
)
from ufo_ext_sample.flags import FLAG_BACKEND, PROBE_FLAG
from ufo_ext_sample.provisioning import FOUNDING_NOTE

from ufo import cli
from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.config import CONFIG_PATH_ENV
from ufo.db import dispose_db, init_db, owner_tx, workspace_tx
from ufo.harness.models.catalog import ANTHROPIC_KEY_SLOT
from ufo.host.ext import loader
from ufo.host.ext.loader import FIRST_PARTY_ENV, discovered, load_manifests
from ufo.runtime.access.credentials import CredentialStore, member_slot
from ufo.runtime.context_boundary import CONTEXT_ROLLOVER_FLAG
from ufo.runtime.ext.context import ScopedStore
from ufo.runtime.ext.deploy import (
    CROSS_WORKSPACE_READ,
    DeployContext,
    FirstMember,
    Membership,
)
from ufo.runtime.ext.manifest import Manifest
from ufo.runtime.member_profiles import read_profile
from ufo.runtime.provisioning import Provisioning
from ufo.runtime.seats import Seats, create_member
from ufo.runtime.workspace import init_workspace_credentials, ws
from ufo.schema import tables
from ufo.serve import _mount_deploy_routes

TOKEN = "sample-deploy-token"
BEARER = {"authorization": f"Bearer {TOKEN}"}
SEAT_ROUTE = f"/internal/{sample.NAME}/{SEAT_PATH}"
FLEET_ROUTE = f"/internal/{sample.NAME}/{FLEET_PATH}"
FOUNDER = "founder@acme.test"
TEAMMATE = "teammate@acme.test"
BASE = datetime(2026, 9, 1, tzinfo=UTC)


def _sample() -> Manifest:
    return next(manifest for manifest in load_manifests() if manifest.name == sample.NAME)


def _blob(root: Path) -> WorkspaceBlobStore:
    return WorkspaceBlobStore(backend=FilesystemBlobStore(root))


def _context(root: Path) -> DeployContext:
    return DeployContext(
        extension=sample.NAME,
        route="test",
        blob=_blob(root),
        provisioning=Provisioning(founded=()),
        flag_backend=None,
        flag_keys=frozenset(),
    )


def _naive(stamp: datetime) -> datetime:
    return stamp.replace(tzinfo=None)


def _second(seconds: int) -> datetime:
    return BASE + timedelta(seconds=seconds)


async def _found(workspace_id: UUID, *members: tuple[str, datetime]) -> dict[str, UUID]:
    seated: dict[str, UUID] = {}
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.workspace).values(
                    id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
                )
            )
            for email, created_at in members:
                seated[email] = await create_member(connection, workspace_id, email)
                await connection.execute(
                    sa.update(tables.member)
                    .values(created_at=created_at)
                    .where(tables.member.c.id == seated[email])
                )
    return seated


async def _workspace_ids() -> set[UUID]:
    async with owner_tx() as connection:
        return set((await connection.execute(sa.select(tables.workspace.c.id))).scalars())


def _cross_workspace_reads(caplog: pytest.LogCaptureFixture) -> list[tuple[str, str]]:
    return [
        (record.ufo["route"], record.ufo["extension"])
        for record in caplog.records
        if record.getMessage() == CROSS_WORKSPACE_READ
    ]


@pytest.fixture
def deploy_client(db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncClient:
    monkeypatch.setenv(sample.DEPLOY_TOKEN_ENV, TOKEN)
    manifest = _sample()
    app = FastAPI()
    _mount_deploy_routes(
        app, (manifest,), _blob(tmp_path), Provisioning(founded=manifest.workspace_founded), None
    )
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://deploy")


async def test_a_deploy_route_refuses_every_request_but_its_bearer(
    deploy_client: AsyncClient,
) -> None:
    ask = {"workspace_id": str(uuid4()), "email": FOUNDER}
    async with deploy_client as client:
        refused = [
            await client.post(SEAT_ROUTE, json=ask, headers=headers)
            for headers in ({}, {"authorization": "Bearer wrong"}, {"authorization": TOKEN})
        ]
    assert [response.status_code for response in refused] == [401, 401, 401]
    assert await _workspace_ids() == set()


async def test_a_seat_through_a_deploy_route_founds_the_workspace_and_binds_it(
    deploy_client: AsyncClient,
) -> None:
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    workspace_id = uuid4()
    seat = {"workspace_id": str(workspace_id)}
    async with deploy_client as client:
        founding = await client.post(
            SEAT_ROUTE,
            json={**seat, "email": FOUNDER, "name": "Rae Whitlock", "model_key": "sk-ant-seeded"},
            headers=BEARER,
        )
        founder = UUID(founding.json()["member_id"])
        with ws(workspace_id):
            founded = await ScopedStore(extension=sample.NAME).get(SEAT_KEY)
        joining = await client.post(SEAT_ROUTE, json={**seat, "email": TEAMMATE}, headers=BEARER)
    teammate = UUID(joining.json()["member_id"])
    with ws(workspace_id):
        joined = await ScopedStore(extension=sample.NAME).get(SEAT_KEY)
        profile = await read_profile(workspace_id, founder)
        key = await store.get(workspace_id, member_slot(ANTHROPIC_KEY_SLOT, founder))
    note = FOUNDING_NOTE.format(member_id=founder, email=FOUNDER)
    assert (founding.status_code, founding.json()["admin"]) == (200, True)
    assert (joining.status_code, joining.json()["admin"]) == (200, False)
    assert founded == {"member_id": str(founder), "founded": True, "note": note}
    assert joined == {"member_id": str(teammate), "founded": False, "note": note}
    assert profile is not None
    assert (profile.name, profile.name_source) == ("Rae Whitlock", "signin")
    assert key == "sk-ant-seeded"


async def test_a_seat_the_route_refuses_inside_the_seat_writes_nothing(
    deploy_client: AsyncClient,
) -> None:
    async with deploy_client as client:
        refused = await client.post(
            SEAT_ROUTE,
            json={"workspace_id": str(uuid4()), "email": REFUSED_SEAT},
            headers=BEARER,
        )
    assert refused.status_code == 409
    assert refused.json() == {"detail": f"{REFUSED_SEAT} is refused"}
    assert await _workspace_ids() == set()


async def test_a_deploy_route_logs_only_the_cross_workspace_reads_it_audits(
    deploy_client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    async with deploy_client as client:
        for email in (FOUNDER, "founder@globex.test"):
            seated = await client.post(
                SEAT_ROUTE, json={"workspace_id": str(uuid4()), "email": email}, headers=BEARER
            )
            assert seated.status_code == 200
        with caplog.at_level(logging.WARNING, logger="ufo"):
            fleet = await client.get(FLEET_ROUTE, headers=BEARER)
    assert fleet.json() == {"workspaces": 2, "notes": 2}
    assert _cross_workspace_reads(caplog) == [(FLEET_ROUTE, sample.NAME)]


@pytest.mark.parametrize(
    ("changes", "refusal"),
    [
        ({}, "UFO_SAMPLE_DEPLOY_TOKEN is unset"),
        ({"deploy_keys": ()}, "leaves UFO_SAMPLE_DEPLOY_TOKEN out of deploy_keys"),
        ({"deploy_bearer_env": None}, "serves deploy routes but names no bearer env"),
        ({"deploy_routes": ()}, "names UFO_SAMPLE_DEPLOY_TOKEN but serves no deploy routes"),
        ({"name": "egress"}, "collides with core's /internal/"),
    ],
)
def test_serve_refuses_a_deploy_route_it_cannot_guard(
    changes: dict[str, object],
    refusal: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if changes:
        monkeypatch.setenv(sample.DEPLOY_TOKEN_ENV, TOKEN)
    else:
        monkeypatch.delenv(sample.DEPLOY_TOKEN_ENV, raising=False)
    app = FastAPI()
    app.add_route(
        "/internal/egress/tool-bridge/request", lambda request: Response(), methods=["POST"]
    )
    with pytest.raises(RuntimeError, match=refusal):
        _mount_deploy_routes(
            app, (replace(_sample(), **changes),), _blob(tmp_path), Provisioning(founded=()), None
        )


@pytest.mark.parametrize("point", ["deploy_routes", "commands"])
def test_a_third_party_extension_cannot_declare_deploy_routes_or_commands(
    point: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    declared = replace(Manifest(name="acme", version="0"), **{point: getattr(_sample(), point)})
    acme = SimpleNamespace(
        dist=SimpleNamespace(name="acme"), module="acme_ext", load=lambda: lambda: declared
    )
    monkeypatch.setattr(loader, "entry_points", lambda group: (acme,))
    monkeypatch.delenv(FIRST_PARTY_ENV, raising=False)
    with pytest.raises(ValueError, match="cannot declare privileged capabilities"):
        discovered()


async def test_memberships_name_every_workspace_an_address_joined_in_joining_order(
    db: None, tmp_path: Path
) -> None:
    acme, globex, other = UUID(int=1), UUID(int=2), UUID(int=3)
    await _found(acme, (FOUNDER, _second(0)), ("shared@acme.test", _second(2)))
    await _found(globex, ("founder@globex.test", _second(0)), ("shared@acme.test", _second(1)))
    await _found(other, ("founder@other.test", _second(0)))
    with ws(acme):
        async with workspace_tx() as connection:
            await Seats(workspace_id=acme).revoke(connection, "shared@acme.test")

    found = await _context(tmp_path).memberships(" Shared@Acme.test", audit=False)

    assert [(m.workspace_id, m.first_email, _naive(m.joined_at)) for m in found] == [
        (globex, "founder@globex.test", _naive(_second(1))),
        (acme, FOUNDER, _naive(_second(2))),
    ]
    assert all(isinstance(membership, Membership) for membership in found)


async def test_workspaces_by_first_domain_read_the_first_member_alone(
    db: None, tmp_path: Path
) -> None:
    acme, second_acme, mixed, globex, lookalike = uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    await _found(acme, (FOUNDER, _second(0)))
    await _found(second_acme, ("second@acme.test", _second(0)), ("x@globex.test", _second(1)))
    seated = await _found(mixed, ("mixed@acme.test", _second(0)))
    await _found(globex, ("founder@globex.test", _second(0)), ("joiner@acme.test", _second(1)))
    await _found(lookalike, ("founder@notacme.test", _second(0)))
    with ws(mixed):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.member)
                .values(email="Mixed@ACME.test")
                .where(tables.member.c.id == seated["mixed@acme.test"])
            )
    context = _context(tmp_path)

    found = await context.workspaces_by_first_domain("Acme.test", audit=False)

    assert found == tuple(
        sorted(
            (
                FirstMember(acme, FOUNDER),
                FirstMember(second_acme, "second@acme.test"),
                FirstMember(mixed, "Mixed@ACME.test"),
            ),
            key=lambda first: first.workspace_id,
        )
    )
    assert await context.workspaces_by_first_domain("ac_e.test", audit=False) == ()


async def test_first_member_emails_answer_each_named_workspace_that_has_a_member(
    db: None, tmp_path: Path
) -> None:
    acme, globex, empty, unnamed = uuid4(), uuid4(), uuid4(), uuid4()
    await _found(acme, (TEAMMATE, _second(1)), (FOUNDER, _second(0)))
    await _found(globex, ("founder@globex.test", _second(0)))
    await _found(empty)
    await _found(unnamed, ("founder@unnamed.test", _second(0)))
    context = _context(tmp_path)

    found = await context.first_member_emails((acme, globex, empty, uuid4()), audit=False)

    assert found == {acme: FOUNDER, globex: "founder@globex.test"}
    assert await context.first_member_emails((), audit=False) == {}
    assert await context.workspace_count(audit=False) == 4


async def test_workspace_ids_name_every_workspace_a_member_or_none(
    db: None, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    context = _context(tmp_path)
    assert await context.workspace_ids(audit=False) == frozenset()
    acme, empty = uuid4(), uuid4()
    await _found(acme, (FOUNDER, _second(0)))
    await _found(empty)

    with caplog.at_level(logging.WARNING, logger="ufo"):
        assert await context.workspace_ids(audit=True) == {acme, empty}
    assert _cross_workspace_reads(caplog) == [("test", sample.NAME)]


async def test_seated_members_walk_every_seat_one_page_at_a_time(db: None, tmp_path: Path) -> None:
    acme, globex = uuid4(), uuid4()
    acme_seats = await _found(
        acme,
        ("admin@acme.test", _second(0)),
        ("leaver@acme.test", _second(1)),
        ("shared@acme.test", _second(2)),
    )
    globex_seats = await _found(
        globex, ("founder@globex.test", _second(2)), ("shared@acme.test", _second(3))
    )
    tied = sorted(
        (
            (acme_seats["shared@acme.test"], acme, "shared@acme.test"),
            (globex_seats["founder@globex.test"], globex, "founder@globex.test"),
        )
    )
    with ws(acme):
        async with workspace_tx() as connection:
            await Seats(workspace_id=acme).revoke(connection, "leaver@acme.test")
    context = _context(tmp_path)

    whole = await context.seated_members(None, 10, audit=False)
    walked = list(await context.seated_members(None, 1, audit=False))
    while page := await context.seated_members(
        (walked[-1].created_at, walked[-1].member_id), 1, audit=False
    ):
        walked.extend(page)

    assert [(member.workspace_id, member.email) for member in whole] == [
        (acme, "admin@acme.test"),
        *((workspace_id, email) for _, workspace_id, email in tied),
        (globex, "shared@acme.test"),
    ]
    assert walked == list(whole)
    with pytest.raises(ValueError, match="limit must be positive"):
        await context.seated_members(None, 0, audit=False)


async def test_a_member_model_key_for_an_unserved_provider_is_refused(
    db: None, tmp_path: Path
) -> None:
    async with _context(tmp_path).bound(uuid4()) as bound:
        with pytest.raises(ValueError, match="provider must be one of"):
            await bound.put_member_model_key(uuid4(), "openrouter", "sk-or-unserved")


def _cli_config(tmp_path: Path, url: str) -> Path:
    config = tmp_path / "ufo.toml"
    config.write_text(
        f'[database]\nurl = "{url}"\n\n'
        f'[blob]\nbackend = "filesystem"\nroot = "{tmp_path / "blobs"}"\n\n'
        f'[pack]\nname = "{sample_pack.NAME}"\n'
    )
    return config


async def _seed(url: str, *workspaces: tuple[UUID, tuple[tuple[str, datetime], ...]]) -> None:
    init_db(url)
    try:
        for workspace_id, members in workspaces:
            await _found(workspace_id, *members)
    finally:
        await dispose_db()


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
def test_an_extension_command_runs_as_a_ufoctl_verb(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    private = tmp_path / "ufo.db"
    shutil.copy(make_url(database_url).database or "", private)
    url = f"sqlite+aiosqlite:///{private}"
    acme, globex = uuid4(), uuid4()
    asyncio.run(
        _seed(
            url,
            (acme, ((FOUNDER, _second(0)), ("shared@acme.test", _second(2)))),
            (globex, (("founder@globex.test", _second(0)), ("shared@acme.test", _second(1)))),
        )
    )
    monkeypatch.setenv(CONFIG_PATH_ENV, str(_cli_config(tmp_path, url)))
    runner = CliRunner()
    verb = [sample.NAME, MEMBERSHIPS_COMMAND]

    with runner.isolated_filesystem(temp_dir=tmp_path), caplog.at_level(logging.WARNING, "ufo"):
        listed = runner.invoke(cli.main, ["--help"])
        found = runner.invoke(cli.main, [*verb, "--email", "shared@acme.test"])
        refused = runner.invoke(cli.main, [*verb, "--email", "nobody@acme.test"])
        unnamed = runner.invoke(cli.main, verb)

    assert sample.NAME in listed.output
    assert (found.exit_code, found.output) == (0, f"{globex} {acme}\n")
    assert refused.exit_code == 1
    assert "nobody@acme.test is a member of no workspace." in refused.output
    assert unnamed.exit_code == 2 and "--email" in unnamed.output
    assert _cross_workspace_reads(caplog) == [(f"ufoctl {' '.join(verb)}", sample.NAME)] * 2


def test_a_command_reads_the_flag_backend_and_every_flag_the_deploy_declares(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _cli_config(tmp_path, f"sqlite+aiosqlite:///{tmp_path / 'ufo.db'}")
    config.write_text(f'{config.read_text()}\n[flags]\nbackend = "{FLAG_BACKEND}"\n')
    monkeypatch.setenv(CONFIG_PATH_ENV, str(config))
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        read = runner.invoke(cli.main, [sample.NAME, FLAGS_COMMAND])

    assert (read.exit_code, read.output) == (
        0,
        f"{FLAG_BACKEND} {' '.join(sorted((CONTEXT_ROLLOVER_FLAG, PROBE_FLAG)))}\n",
    )


def test_an_extension_whose_commands_take_a_ufoctl_verbs_name_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        CONFIG_PATH_ENV, str(_cli_config(tmp_path, f"sqlite+aiosqlite:///{tmp_path / 'ufo.db'}"))
    )
    monkeypatch.setattr(cli, "load_manifests", lambda pack: (replace(_sample(), name="serve"),))
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        refused = runner.invoke(cli.main, ["--help"])
    assert refused.exit_code == 1
    assert "extension 'serve' declares commands under a ufoctl verb's name" in refused.output

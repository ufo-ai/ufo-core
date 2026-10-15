"""Serve's end of the site report: what founds a turn in a site's own conversation, and what does
not.

The token is the whole gate here, so these are the gate's cases — nothing signed, a visit's own two
kinds, and a conversation the signed workspace does not hold. The ingress end and the round trip
between them live beside the ingress, in `test_ingress_serve.py`, where the carrier a dead site is
dialed through already stands.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from ufo_testsupport.invoker import RecordingInvoker

from ufo.db import workspace_tx
from ufo.harness.auth.bearer import UFO_TOKEN_SECRET_ENV
from ufo.harness.sandbox.ingress_token import (
    INGRESS_SESSION_KIND,
    INGRESS_VIEW_KIND,
    SITE_REPORT_KIND,
    IngressClaims,
    IngressTokenKind,
    mint_ingress_token,
)
from ufo.harness.sandbox.session import RunToken
from ufo.harness.sandbox.site_report import (
    SITE_REPORT_PATH,
    SITE_REPORT_TTL_SECONDS,
    SiteReports,
)
from ufo.runtime.access.egress_resolver import PerAgentRules
from ufo.runtime.access.egress_rules import InternetRule, ServiceRule
from ufo.runtime.access.grants import GrantStore
from ufo.runtime.agent_scope import agent
from ufo.runtime.ext.context import TurnInvoker
from ufo.runtime.surfaces.admission import Admission, AdmissionInvoker
from ufo.runtime.tools.bridge import TOOL_BRIDGE_HOST
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import TurnRuntimeConfig

SECRET = "s3cret"
SERVE_BASE_URL = "https://app.example.test"
PORT = 8000
HOSTED_SITE_ROW = sa.table(
    "hosted_site",
    sa.column("workspace_id", sa.Uuid()),
    sa.column("conversation_id", sa.Uuid()),
    sa.column("name", sa.Text()),
    sa.column("port", sa.Integer()),
    sa.column("visibility", sa.Text()),
    sa.column("creator_member_id", sa.Uuid()),
    sa.column("generation", sa.Uuid()),
    sa.column("deploy_generation", sa.BigInteger()),
    sa.column("source_manifest", sa.Text()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)
pytestmark = pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)


def report_token(workspace_id: UUID, conversation_id: UUID, kind: IngressTokenKind) -> str:
    return mint_ingress_token(
        IngressClaims(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            port=PORT,
            expires_at=int(datetime.now(UTC).timestamp()) + SITE_REPORT_TTL_SECONDS,
        ),
        kind,
    )


async def seed_conversation() -> tuple[UUID, UUID, UUID, UUID]:
    workspace_id, agent_id, conversation_id, creator_member_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=creator_member_id,
                workspace_id=workspace_id,
                email="site-owner@example.test",
                seated_at=sa.func.now(),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
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
                queue_key=uuid4().hex,
                member_id=None,
                sandbox_handle="stub:sbx-1",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(HOSTED_SITE_ROW).values(
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                name="status",
                port=PORT,
                visibility="private",
                creator_member_id=creator_member_id,
                generation=uuid4(),
                deploy_generation=1,
                source_manifest=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, agent_id, conversation_id, creator_member_id


async def _post(invoker: TurnInvoker, bearer: str) -> httpx.Response:
    app = FastAPI()
    app.include_router(SiteReports(invoker_for=lambda _workspace_id: invoker).router())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=SERVE_BASE_URL
    ) as client:
        return await client.post(SITE_REPORT_PATH, headers={"authorization": bearer})


class _Queued:
    async def enqueue_async(self, options: dict[str, str], workspace_id: str, turn_id: str) -> None:
        pass


async def test_a_signed_report_founds_a_turn_in_the_conversation_it_names(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The conversation names its own agent, so the report carries no agent of its own and cannot
    fire into another one."""
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, SECRET)
    workspace_id, agent_id, conversation_id, _creator_member_id = await seed_conversation()
    invoker = RecordingInvoker()

    posted = await _post(
        invoker, f"Bearer {report_token(workspace_id, conversation_id, SITE_REPORT_KIND)}"
    )

    assert posted.status_code == 204
    assert [(turn.conversation_id, turn.agent_id) for turn in invoker.turns] == [
        (conversation_id, agent_id)
    ]
    assert invoker.turns[0].standalone is True
    assert invoker.turns[0].runtime_config == TurnRuntimeConfig(internet_access=False)


@pytest.mark.parametrize("kind", [INGRESS_VIEW_KIND, INGRESS_SESSION_KIND])
async def test_a_visits_own_token_posted_as_a_report_founds_nothing(
    db, kind, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, SECRET)
    workspace_id, _agent_id, conversation_id, _creator_member_id = await seed_conversation()
    invoker = RecordingInvoker()

    posted = await _post(invoker, f"Bearer {report_token(workspace_id, conversation_id, kind)}")

    assert posted.status_code == 401
    assert invoker.turns == []


@pytest.mark.parametrize("bearer", ["", "Bearer", "Bearer nonsense", "Bearer a.b"])
async def test_a_report_nothing_signed_founds_nothing(
    db, bearer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """This route founds turns, so nothing but the deploy secret admits it: an absent, truncated,
    or unsigned bearer is refused before a row is read."""
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, SECRET)
    invoker = RecordingInvoker()

    posted = await _post(invoker, bearer)

    assert posted.status_code == 401
    assert invoker.turns == []


async def test_a_report_naming_no_conversation_of_its_workspace_founds_nothing(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A validly signed report whose conversation the workspace does not hold answers 404 rather
    than founding anything."""
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, SECRET)
    workspace_id, _agent_id, _conversation_id, _creator_member_id = await seed_conversation()
    invoker = RecordingInvoker()

    posted = await _post(invoker, f"Bearer {report_token(workspace_id, uuid4(), SITE_REPORT_KIND)}")

    assert posted.status_code == 404
    assert invoker.turns == []


async def test_a_report_cannot_take_a_site_creator_from_another_workspace(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, SECRET)
    workspace_id, _agent_id, conversation_id, _creator_member_id = await seed_conversation()
    other_workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=other_workspace_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.update(HOSTED_SITE_ROW)
            .where(
                HOSTED_SITE_ROW.c.workspace_id == workspace_id,
                HOSTED_SITE_ROW.c.conversation_id == conversation_id,
            )
            .values(workspace_id=other_workspace_id)
        )
    invoker = RecordingInvoker()

    posted = await _post(
        invoker, f"Bearer {report_token(workspace_id, conversation_id, SITE_REPORT_KIND)}"
    )

    assert posted.status_code == 204
    assert invoker.turns == []


async def test_a_static_site_report_founds_no_recovery_turn(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, SECRET)
    workspace_id, _agent_id, conversation_id, _creator_member_id = await seed_conversation()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(HOSTED_SITE_ROW)
            .where(
                HOSTED_SITE_ROW.c.workspace_id == workspace_id,
                HOSTED_SITE_ROW.c.conversation_id == conversation_id,
            )
            .values(source_manifest='{"root":"sites/status/","files":{}}')
        )
    invoker = RecordingInvoker()

    posted = await _post(
        invoker, f"Bearer {report_token(workspace_id, conversation_id, SITE_REPORT_KIND)}"
    )

    assert posted.status_code == 204
    assert invoker.turns == []


async def test_a_report_persists_a_turn_that_reaches_no_ambient_egress(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, SECRET)
    workspace_id, agent_id, conversation_id, member_id = await seed_conversation()
    with ws(workspace_id), agent(agent_id):
        await GrantStore().record(
            provider="sample",
            account_id="ambient-account",
            host="api.sample.test",
            grantor_member_id=member_id,
            shared=False,
        )
    ambient_turn_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=ambient_turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="running",
                inbound="ambient work",
                speaker_member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    invoker = AdmissionInvoker(
        admission=Admission(dbos=_Queued(), durable_surfaces=frozenset()),
        workspace_id=workspace_id,
    )

    posted = await _post(
        invoker, f"Bearer {report_token(workspace_id, conversation_id, SITE_REPORT_KIND)}"
    )

    assert posted.status_code == 204
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(
                sa.select(tables.turn.c.id, tables.turn.c.runtime_config)
                .where(tables.turn.c.conversation_id == conversation_id)
                .order_by(tables.turn.c.seq)
            )
        ).all()
        assert len(turns) == 2
        assert turns[0].id == ambient_turn_id
        assert turns[0].runtime_config is None
        turn = turns[1]
        await connection.execute(
            sa.update(tables.turn)
            .where(tables.turn.c.id == ambient_turn_id)
            .values(status="done", terminal={"status": "done"})
        )
        await connection.execute(
            sa.update(tables.turn).where(tables.turn.c.id == turn.id).values(status="running")
        )
    assert TurnRuntimeConfig.model_validate(turn.runtime_config) == TurnRuntimeConfig(
        internet_access=False
    )
    rules = await PerAgentRules(base=(), grants=GrantStore(), internet=(InternetRule(),)).resolve(
        RunToken(workspace_id, turn.id)
    )
    assert rules == (ServiceRule(host=TOOL_BRIDGE_HOST),)

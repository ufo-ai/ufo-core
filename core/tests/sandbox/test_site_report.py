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
from ufo.harness.sandbox.site_report import (
    SITE_REPORT_PATH,
    SITE_REPORT_TTL_SECONDS,
    SiteReports,
)
from ufo.schema import tables

SECRET = "s3cret"
SERVE_BASE_URL = "https://app.example.test"
PORT = 8000
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


async def seed_conversation() -> tuple[UUID, UUID, UUID]:
    workspace_id, agent_id, conversation_id = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
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
                queue_key=uuid4().hex,
                member_id=None,
                sandbox_handle="stub:sbx-1",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, agent_id, conversation_id


async def _post(invoker: RecordingInvoker, bearer: str) -> httpx.Response:
    app = FastAPI()
    app.include_router(SiteReports(invoker_for=lambda _workspace_id: invoker).router())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=SERVE_BASE_URL
    ) as client:
        return await client.post(SITE_REPORT_PATH, headers={"authorization": bearer})


async def test_a_signed_report_founds_a_turn_in_the_conversation_it_names(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The conversation names its own agent, so the report carries no agent of its own and cannot
    fire into another one. The turn is not standalone, so a report arriving while the agent is
    already repairing folds into that turn instead of founding a second beside it."""
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, SECRET)
    workspace_id, agent_id, conversation_id = await seed_conversation()
    invoker = RecordingInvoker()

    posted = await _post(
        invoker, f"Bearer {report_token(workspace_id, conversation_id, SITE_REPORT_KIND)}"
    )

    assert posted.status_code == 204
    assert [(turn.conversation_id, turn.agent_id) for turn in invoker.turns] == [
        (conversation_id, agent_id)
    ]
    assert invoker.turns[0].standalone is False


@pytest.mark.parametrize("kind", [INGRESS_VIEW_KIND, INGRESS_SESSION_KIND])
async def test_a_visits_own_token_posted_as_a_report_founds_nothing(
    db, kind, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A report is its own kind, so neither token a visit hands a browser can found a turn here: a
    session cookie a site read off its own request log posts nothing, and a view link pasted here
    opens nothing. Both are refused exactly as an unsigned bearer is."""
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, SECRET)
    workspace_id, _agent_id, conversation_id = await seed_conversation()
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
    than founding anything. A shipped app page has that shape by construction — its origin is a
    synthetic per-workspace anchor with no conversation row behind it — so there is no agent to
    tell, and the report is dropped on this side too."""
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, SECRET)
    workspace_id, _agent_id, _conversation_id = await seed_conversation()
    invoker = RecordingInvoker()

    posted = await _post(invoker, f"Bearer {report_token(workspace_id, uuid4(), SITE_REPORT_KIND)}")

    assert posted.status_code == 404
    assert invoker.turns == []

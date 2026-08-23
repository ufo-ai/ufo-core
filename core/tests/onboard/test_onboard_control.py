"""The internal onboarding RPC the Rust control plane calls.

These are the seat, candidate, membership, and fleet behaviours that used to live in
`servers/control/tests/test_rls.py` against `SharedWorkspaces`. They moved here with the code:
control holds no privilege on a core table any more, so what a workspace *is* — the seat, the
balance grant, the default agent, the walled intake prompt — is core's to prove.
"""

import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ufo.billing.balance import read_balance
from ufo.db import owner_tx, workspace_tx
from ufo.onboard.onboard_control import (
    CROSS_WORKSPACE_READ,
    SIGNUP_GRANT_MICRO_USD,
    SIGNUP_RESERVE_MICRO_USD,
    OnboardControl,
    SignupProfile,
    agent_prompt,
    deterministic_workspace_id,
)
from ufo.onboard.onboarding import DEFAULT_AGENT_MODEL, DEFAULT_AGENT_PROMPT
from ufo.schema import tables
from ufo.schema.records import DEFAULT_AGENT_NAME
from ufo.seats import create_member
from ufo.workspace import ws

CONTROL_TOKEN = "onboard-control-token"


@pytest.fixture
def onboard_client(db: None) -> AsyncClient:
    app = FastAPI()
    app.include_router(OnboardControl(control_token=CONTROL_TOKEN).router())
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://control",
        headers={"authorization": f"Bearer {CONTROL_TOKEN}"},
    )


async def test_the_guard_refuses_a_request_carrying_no_token(db: None) -> None:
    app = FastAPI()
    app.include_router(OnboardControl(control_token=CONTROL_TOKEN).router())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://control") as client:
        assert (await client.get("/internal/onboard/fleet")).status_code == 401
        wrong = await client.get(
            "/internal/onboard/fleet", headers={"authorization": "Bearer wrong"}
        )
        assert wrong.status_code == 401


async def test_seat_creates_the_workspace_its_member_and_its_main_agent(
    onboard_client: AsyncClient,
) -> None:
    workspace_id = deterministic_workspace_id("acme.com")
    async with onboard_client as client:
        response = await client.post(
            "/internal/onboard/seat",
            json={
                "workspace_id": str(workspace_id),
                "domain": "acme.com",
                "email": "founder@acme.com",
            },
        )
    assert response.status_code == 200
    assert response.json() == {
        "workspace_id": str(workspace_id),
        "admin": True,
        "founding": True,
    }

    with ws(workspace_id):
        async with workspace_tx() as connection:
            members = (
                await connection.execute(
                    sa.select(tables.member.c.email, tables.member.c.is_admin).where(
                        tables.member.c.workspace_id == workspace_id
                    )
                )
            ).all()
            agents = (
                await connection.execute(
                    sa.select(
                        tables.agent.c.name,
                        tables.agent.c.prompt,
                        tables.agent.c.model,
                        tables.agent.c.is_main,
                        tables.agent.c.visibility,
                    ).where(tables.agent.c.workspace_id == workspace_id)
                )
            ).all()
    assert members == [("founder@acme.com", True)], "the first member administers"
    # `visibility` is stated rather than defaulted. The column defaults to `private`, and a main
    # agent owned by nobody would then be invisible to every non-admin member of its own workspace —
    # they would open the portal to an empty agent list. `ufoctl init` states the same value, so the
    # two paths that found a workspace agree.
    assert agents == [
        (DEFAULT_AGENT_NAME, DEFAULT_AGENT_PROMPT, DEFAULT_AGENT_MODEL, True, "workspace")
    ]


async def test_seat_grants_the_signup_balance_once(onboard_client: AsyncClient) -> None:
    workspace_id = deterministic_workspace_id("acme.com")
    body = {
        "workspace_id": str(workspace_id),
        "domain": "acme.com",
        "email": "founder@acme.com",
    }
    async with onboard_client as client:
        assert (await client.post("/internal/onboard/seat", json=body)).status_code == 200
        with ws(workspace_id):
            async with workspace_tx() as connection:
                after_create = await read_balance(connection, workspace_id)
        assert after_create is not None
        assert after_create.granted_micro_usd == SIGNUP_GRANT_MICRO_USD
        assert after_create.reserve_micro_usd == SIGNUP_RESERVE_MICRO_USD

        # A teammate joining must never fund the workspace a second time.
        joined = await client.post(
            "/internal/onboard/seat",
            json={**body, "email": "teammate@acme.com"},
        )
        assert joined.status_code == 200
        assert joined.json()["admin"] is False
        # The first run belongs to the sign-in that seated the first member, exactly as the signup
        # balance does — a teammate joining a workspace that already stands earns neither.
        assert joined.json()["founding"] is False

    with ws(workspace_id):
        async with workspace_tx() as connection:
            after_join = await read_balance(connection, workspace_id)
    assert after_join is not None
    assert after_join.granted_micro_usd == SIGNUP_GRANT_MICRO_USD, "the grant is once per founding"


async def test_seat_refuses_a_workspace_its_domain_no_longer_names(
    onboard_client: AsyncClient,
) -> None:
    workspace_id = deterministic_workspace_id("acme.com")
    async with onboard_client as client:
        await client.post(
            "/internal/onboard/seat",
            json={
                "workspace_id": str(workspace_id),
                "domain": "acme.com",
                "email": "founder@acme.com",
            },
        )
        # A different domain arriving at a workspace whose first member is someone else's is a
        # refusal, not a join: it would hand that customer's workspace to an outsider.
        refused = await client.post(
            "/internal/onboard/seat",
            json={
                "workspace_id": str(workspace_id),
                "domain": "other.com",
                "email": "founder@other.com",
            },
        )
    assert refused.status_code == 409
    assert "no longer belongs to other.com" in refused.json()["detail"]


async def test_the_intake_profile_opens_the_main_agents_prompt(
    onboard_client: AsyncClient,
) -> None:
    workspace_id = deterministic_workspace_id("acme.com")
    async with onboard_client as client:
        await client.post(
            "/internal/onboard/seat",
            json={
                "workspace_id": str(workspace_id),
                "domain": "acme.com",
                "email": "founder@acme.com",
                "profile": {"business": "we sell widgets", "goals": "answer support mail"},
            },
        )
    with ws(workspace_id):
        async with workspace_tx() as connection:
            prompt = (
                await connection.execute(
                    sa.select(tables.agent.c.prompt).where(
                        tables.agent.c.workspace_id == workspace_id
                    )
                )
            ).scalar_one()
    assert prompt.startswith(DEFAULT_AGENT_PROMPT)
    assert "we sell widgets" in prompt
    assert "answer support mail" in prompt
    assert "the intake form" in prompt, "the answers arrive attributed and walled"


def test_a_form_answer_cannot_brick_the_workspace_it_describes() -> None:
    """`render_system_prompt` substitutes against an empty mapping, so one live `{{var}}` in the
    prompt raises on every turn. A public form must not be able to plant one."""
    from ufo.loop.prompts.render import PROMPT_VAR_RE

    for hostile in ["{{name}}", "{{{{name}}}}", "{{{{{{deep}}}}}}", "a {{b}} c {{d}}"]:
        prompt = agent_prompt(SignupProfile(business=hostile, goals=hostile))
        assert not PROMPT_VAR_RE.search(prompt), f"{hostile} survived as a prompt var"
        # The braces thin rather than vanish, so the answer still reads as what they typed.
        assert "name" in prompt or "deep" in prompt or "b" in prompt


def test_an_absent_profile_leaves_the_core_default_untouched() -> None:
    assert agent_prompt(None) == DEFAULT_AGENT_PROMPT


async def test_choices_offers_the_domain_workspace_and_every_exact_membership(
    onboard_client: AsyncClient, database_url: str
) -> None:
    if not database_url.startswith("postgresql"):
        pytest.skip("the candidate read is postgres SQL, for a fleet sqlite never serves")
    domain_workspace = deterministic_workspace_id("acme.com")
    other_workspace = uuid4()
    for workspace_id, email in (
        (domain_workspace, "founder@acme.com"),
        (other_workspace, "founder@other.com"),
    ):
        with ws(workspace_id):
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.insert(tables.workspace).values(
                        id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
                    )
                )
                await create_member(connection, workspace_id, email, is_admin=True)
    # An outside address added to someone else's workspace resolves to it as an exact membership.
    with ws(other_workspace):
        async with workspace_tx() as connection:
            await create_member(connection, other_workspace, "contractor@acme.com")

    async with onboard_client as client:
        response = await client.get(
            "/internal/onboard/choices",
            params={"email": "contractor@acme.com", "domain": "acme.com"},
        )
    assert response.status_code == 200
    by_id = {choice["workspace_id"]: choice for choice in response.json()["choices"]}
    assert by_id[str(other_workspace)]["member"] is True
    assert by_id[str(other_workspace)]["label"] == "other.com"
    assert by_id[str(domain_workspace)]["member"] is False
    assert by_id[str(domain_workspace)]["label"] == "acme.com"


async def test_choices_refuses_a_domain_that_maps_to_two_workspaces(
    onboard_client: AsyncClient, database_url: str
) -> None:
    if not database_url.startswith("postgresql"):
        pytest.skip("the candidate read is postgres SQL, for a fleet sqlite never serves")
    for _ in range(2):
        workspace_id = uuid4()
        with ws(workspace_id):
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.insert(tables.workspace).values(
                        id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
                    )
                )
                await create_member(connection, workspace_id, "founder@acme.com", is_admin=True)

    async with onboard_client as client:
        refused = await client.get(
            "/internal/onboard/choices",
            params={"email": "someone@acme.com", "domain": "acme.com"},
        )
    assert refused.status_code == 409
    assert "maps to 2 workspaces" in refused.json()["detail"]


async def test_two_workspaces_sharing_a_label_are_told_apart(
    onboard_client: AsyncClient, database_url: str
) -> None:
    if not database_url.startswith("postgresql"):
        pytest.skip("the candidate read is postgres SQL, for a fleet sqlite never serves")
    ids = []
    for _ in range(2):
        workspace_id = uuid4()
        ids.append(workspace_id)
        # Each member lands in its own transaction. `first_member` orders by `created_at` and falls
        # back to a random uuid, and `now()` is transaction time — two members written together
        # would tie, and the founder's own domain would be the label only by coin flip.
        with ws(workspace_id):
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.insert(tables.workspace).values(
                        id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
                    )
                )
                await create_member(connection, workspace_id, "first@acme.com", is_admin=True)
        with ws(workspace_id):
            async with workspace_tx() as connection:
                await create_member(connection, workspace_id, "contractor@zeta.com")

    async with onboard_client as client:
        response = await client.get(
            "/internal/onboard/choices",
            params={"email": "contractor@zeta.com", "domain": "zeta.com"},
        )
    assert response.status_code == 200, response.json()
    labels = {choice["label"] for choice in response.json()["choices"]}
    # Both workspaces label as `acme.com`, so neither may be offered bare — the member has to be
    # able to tell them apart.
    assert labels == {f"acme.com ({str(workspace_id)[:8]})" for workspace_id in ids}


async def test_choices_is_empty_for_an_address_nothing_holds(
    onboard_client: AsyncClient, database_url: str
) -> None:
    if not database_url.startswith("postgresql"):
        pytest.skip("the candidate read is postgres SQL, for a fleet sqlite never serves")
    async with onboard_client as client:
        response = await client.get(
            "/internal/onboard/choices",
            params={"email": "nobody@nowhere.com", "domain": "nowhere.com"},
        )
    assert response.json() == {"choices": []}


async def test_membership_reads_the_admin_flag_and_refuses_a_removed_member(
    onboard_client: AsyncClient,
) -> None:
    workspace_id = uuid4()
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.workspace).values(
                    id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
                )
            )
            await create_member(connection, workspace_id, "founder@acme.com", is_admin=True)
            await create_member(connection, workspace_id, "teammate@acme.com")

    async with onboard_client as client:
        for email, admin in (("founder@acme.com", True), ("teammate@acme.com", False)):
            response = await client.get(
                "/internal/onboard/membership",
                params={"workspace_id": str(workspace_id), "email": email},
            )
            assert response.status_code == 200
            assert response.json() == {"admin": admin}

        gone = await client.get(
            "/internal/onboard/membership",
            params={"workspace_id": str(workspace_id), "email": "never@acme.com"},
        )
    assert gone.status_code == 404
    assert "no longer a member" in gone.json()["detail"]


async def test_the_fleet_counts_every_workspace(onboard_client: AsyncClient) -> None:
    async with onboard_client as client:
        before = (await client.get("/internal/onboard/fleet")).json()["craft"]
        for _ in range(3):
            workspace_id = uuid4()
            with ws(workspace_id):
                async with workspace_tx() as connection:
                    await connection.execute(
                        sa.insert(tables.workspace).values(
                            id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
                        )
                    )
        after = (await client.get("/internal/onboard/fleet")).json()["craft"]
    assert after == before + 3


def test_the_workspace_derivation_matches_the_rust_contract() -> None:
    """`servers/control/tests/onboard_contract.json` holds the same vectors the Rust client
    asserts. One domain has to derive one workspace on both ends, or a customer signing in through
    the gateway would be seated in a workspace the portal never shows them."""
    import json
    from pathlib import Path

    contract = Path(__file__).parents[3] / "servers" / "control" / "tests" / "onboard_contract.json"
    vectors: dict[str, str] = json.loads(contract.read_text())
    assert len(vectors) >= 4
    for domain, expected in vectors.items():
        assert deterministic_workspace_id(domain) == UUID(expected), domain


STAMP = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)


async def _stamp(member_id: UUID, invited_by: UUID, at: datetime) -> None:
    async with owner_tx() as connection:
        await connection.execute(
            sa.text("update member set invited_at = :at, invited_by = :by where id = :id"),
            {"at": at, "by": invited_by, "id": member_id},
        )


async def test_invitations_lists_the_teammates_an_admin_added(
    onboard_client: AsyncClient, database_url: str
) -> None:
    if not database_url.startswith("postgresql"):
        pytest.skip("the invitation read is postgres SQL, for a fleet sqlite never serves")
    workspace_id = deterministic_workspace_id("acme.com")
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.workspace).values(
                    id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
                )
            )
            admin = await create_member(connection, workspace_id, "admin@acme.com", is_admin=True)
            teammate = await create_member(
                connection, workspace_id, "teammate@acme.com", invited_by=admin
            )
            await create_member(connection, workspace_id, "walkin@acme.com")
    await _stamp(teammate, admin, STAMP)
    async with onboard_client as client:
        response = await client.get("/internal/onboard/invitations")

    assert response.status_code == 200
    listed = response.json()["invitations"]
    assert [row["email"] for row in listed] == ["teammate@acme.com"], (
        "a member who arrived by themselves carries no stamp and earns no invitation"
    )
    assert listed[0]["workspace_id"] == str(workspace_id)
    assert listed[0]["invited_by"] == "admin@acme.com"
    assert listed[0]["workspace_label"] == "acme.com"
    assert datetime.fromisoformat(listed[0]["invited_at"]) == STAMP


def _cursor(row: dict[str, str]) -> dict[str, str]:
    return {
        "after_invited_at": row["invited_at"],
        "after_workspace_id": row["workspace_id"],
        "after_email": row["email"],
    }


async def test_the_invitation_page_cursor_walks_past_a_shared_stamp(
    onboard_client: AsyncClient, database_url: str
) -> None:
    # Two teammates stamped in one instant. The cursor is the whole ordering key, so the second is
    # reachable: a cursor of the stamp alone would either repeat the first forever or hide the
    # second behind it.
    if not database_url.startswith("postgresql"):
        pytest.skip("the invitation read is postgres SQL, for a fleet sqlite never serves")
    workspace_id = deterministic_workspace_id("acme.com")
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.workspace).values(
                    id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
                )
            )
            admin = await create_member(connection, workspace_id, "admin@acme.com", is_admin=True)
            first = await create_member(connection, workspace_id, "first@acme.com")
            second = await create_member(connection, workspace_id, "second@acme.com")
    await _stamp(first, admin, STAMP)
    await _stamp(second, admin, STAMP)
    async with onboard_client as client:
        whole = (await client.get("/internal/onboard/invitations")).json()["invitations"]
        after_first = await client.get("/internal/onboard/invitations", params=_cursor(whole[0]))
        after_second = await client.get("/internal/onboard/invitations", params=_cursor(whole[1]))

    assert [row["email"] for row in whole] == ["first@acme.com", "second@acme.com"]
    assert [row["email"] for row in after_first.json()["invitations"]] == ["second@acme.com"]
    assert after_second.json()["invitations"] == []


async def test_a_page_cursor_missing_part_of_the_ordering_key_is_refused(
    onboard_client: AsyncClient,
) -> None:
    async with onboard_client as client:
        refused = await client.get(
            "/internal/onboard/invitations", params={"after_email": "teammate@acme.com"}
        )
    assert refused.status_code == 422


def _warned_routes(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.ufo["route"]
        for record in caplog.records
        if record.getMessage() == CROSS_WORKSPACE_READ
    ]


async def test_the_invitation_page_logs_no_cross_workspace_read_warning(
    onboard_client: AsyncClient, database_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    """RFC 0036 logs the two reads a person drives and nothing a sweep drives. This page is read by
    `InviteDeliveries`, which polls forever, so a record per call would bury those two."""
    if not database_url.startswith("postgresql"):
        pytest.skip("the invitation read is postgres SQL, for a fleet sqlite never serves")
    async with onboard_client as client:
        with caplog.at_level(logging.WARNING, logger="ufo"):
            page = await client.get("/internal/onboard/invitations")
            swept = _warned_routes(caplog)
            await client.get("/internal/onboard/fleet")
            await client.get(
                "/internal/onboard/choices",
                params={"email": "founder@acme.com", "domain": "acme.com"},
            )

    assert page.status_code == 200
    assert swept == []
    assert _warned_routes(caplog) == ["fleet", "choices"]

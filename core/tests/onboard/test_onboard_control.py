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
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ufo.db import owner_tx, workspace_tx
from ufo.harness.models.catalog import ANTHROPIC_KEY_SLOT
from ufo.onboard.onboard_control import (
    CROSS_WORKSPACE_READ,
    SIGNUP_GRANT_MICRO_USD,
    SIGNUP_RESERVE_MICRO_USD,
    OnboardControl,
    SignupProfile,
    agent_prompt,
)
from ufo.onboard.onboarding import DEFAULT_AGENT_MODEL, DEFAULT_AGENT_PROMPT
from ufo.runtime.access.credentials import CredentialStore, member_slot
from ufo.runtime.authority import MemberAuthority
from ufo.runtime.billing.balance import read_balance
from ufo.runtime.seats import Seats, create_member, signup_workspace_id
from ufo.runtime.workspace import init_workspace_credentials, ws, ws_current
from ufo.schema import tables
from ufo.schema.records import DEFAULT_AGENT_NAME

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


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
async def test_the_guard_refuses_a_request_carrying_no_token(db: None) -> None:
    app = FastAPI()
    app.include_router(OnboardControl(control_token=CONTROL_TOKEN).router())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://control") as client:
        assert (await client.get("/internal/onboard/fleet")).status_code == 401
        wrong = await client.get(
            "/internal/onboard/fleet", headers={"authorization": "Bearer wrong"}
        )
        assert wrong.status_code == 401


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
async def test_seat_creates_the_workspace_its_member_and_its_main_agent(
    onboard_client: AsyncClient,
) -> None:
    workspace_id = signup_workspace_id("acme.com")
    async with onboard_client as client:
        response = await client.post(
            "/internal/onboard/seat",
            json={
                "workspace_id": str(workspace_id),
                "domain": "acme.com",
                "email": "founder@acme.com",
                "signup_subject": "acme.com",
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
    # `visibility` is stated rather than defaulted: the column defaults to `private`, and a main
    # agent owned by nobody would then be invisible to every non-admin member of its own workspace.
    assert agents == [
        (DEFAULT_AGENT_NAME, DEFAULT_AGENT_PROMPT, DEFAULT_AGENT_MODEL, True, "workspace")
    ]


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
async def test_seat_grants_the_signup_balance_once(onboard_client: AsyncClient) -> None:
    workspace_id = signup_workspace_id("acme.com")
    body = {
        "workspace_id": str(workspace_id),
        "domain": "acme.com",
        "email": "founder@acme.com",
        "signup_subject": "acme.com",
    }
    async with onboard_client as client:
        assert (await client.post("/internal/onboard/seat", json=body)).status_code == 200
        with ws(workspace_id):
            async with workspace_tx() as connection:
                after_create = await read_balance(connection, workspace_id)
                await connection.execute(
                    sa.update(tables.agent)
                    .where(tables.agent.c.is_main.is_(True))
                    .values(name="assistant")
                )
        assert after_create is not None
        assert after_create.granted_micro_usd == SIGNUP_GRANT_MICRO_USD
        assert after_create.reserve_micro_usd == SIGNUP_RESERVE_MICRO_USD

        joined = await client.post(
            "/internal/onboard/seat",
            json={**body, "email": "teammate@acme.com"},
        )
        assert joined.status_code == 200
        assert joined.json()["admin"] is False
        assert joined.json()["founding"] is False

    with ws(workspace_id):
        async with workspace_tx() as connection:
            after_join = await read_balance(connection, workspace_id)
    assert after_join is not None
    assert after_join.granted_micro_usd == SIGNUP_GRANT_MICRO_USD, "the grant is once per founding"


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
async def test_seat_stores_a_member_model_key_where_the_connect_flow_does(
    onboard_client: AsyncClient,
) -> None:
    """The seed lands in `member_slot(slot, member_id)` — the exact row the browser connect flow
    writes — so `member_holds_own_model_key`, the predicate the coding subagent's spawn gate reads,
    holds for the seated member. A provider no member slot serves is refused before any write."""
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    try:
        workspace_id = signup_workspace_id("acme.com")
        async with onboard_client as client:
            seated = await client.post(
                "/internal/onboard/seat",
                json={
                    "workspace_id": str(workspace_id),
                    "domain": "acme.com",
                    "email": "founder@acme.com",
                    "signup_subject": "acme.com",
                    "model_key": {"provider": "anthropic", "key": "sk-ant-seeded"},
                },
            )
            refused = await client.post(
                "/internal/onboard/seat",
                json={
                    "workspace_id": str(workspace_id),
                    "domain": "acme.com",
                    "email": "teammate@acme.com",
                    "signup_subject": "acme.com",
                    "model_key": {"provider": "openrouter", "key": "sk-or-unserved"},
                },
            )
        assert seated.status_code == 200
        assert refused.status_code == 422
        with ws(workspace_id):
            async with workspace_tx() as connection:
                member_id = (
                    await connection.execute(
                        sa.select(tables.member.c.id).where(
                            tables.member.c.workspace_id == workspace_id
                        )
                    )
                ).scalar_one()
            stored = await store.get(workspace_id, member_slot(ANTHROPIC_KEY_SLOT, member_id))
            assert stored == "sk-ant-seeded"
            assert await ws_current().member_holds_own_model_key(MemberAuthority(member_id))
    finally:
        init_workspace_credentials(None)


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
async def test_seat_refuses_a_workspace_its_domain_no_longer_names(
    onboard_client: AsyncClient,
) -> None:
    workspace_id = signup_workspace_id("acme.com")
    async with onboard_client as client:
        await client.post(
            "/internal/onboard/seat",
            json={
                "workspace_id": str(workspace_id),
                "domain": "acme.com",
                "email": "founder@acme.com",
                "signup_subject": "acme.com",
            },
        )
        refused = await client.post(
            "/internal/onboard/seat",
            json={
                "workspace_id": str(workspace_id),
                "domain": "other.com",
                "email": "founder@other.com",
                "signup_subject": "other.com",
            },
        )
    assert refused.status_code == 409
    assert "no longer belongs to other.com" in refused.json()["detail"]


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
async def test_seat_refuses_an_identity_with_a_mismatched_domain(
    onboard_client: AsyncClient,
) -> None:
    async with onboard_client as client:
        seat = await client.post(
            "/internal/onboard/seat",
            json={
                "workspace_id": str(signup_workspace_id("acme.com")),
                "domain": "acme.com",
                "email": "founder@other.com",
                "signup_subject": "acme.com",
            },
        )
        choices = await client.get(
            "/internal/onboard/choices",
            params={
                "email": "founder@other.com",
                "domain": "acme.com",
                "signup_subject": "acme.com",
            },
        )
    for refused in (seat, choices):
        assert refused.status_code == 422
        assert refused.json()["detail"] == "domain must match the verified email"


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
async def test_the_intake_profile_opens_the_main_agents_prompt(
    onboard_client: AsyncClient,
) -> None:
    workspace_id = signup_workspace_id("acme.com")
    async with onboard_client as client:
        await client.post(
            "/internal/onboard/seat",
            json={
                "workspace_id": str(workspace_id),
                "domain": "acme.com",
                "email": "founder@acme.com",
                "signup_subject": "acme.com",
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
    from ufo.runtime.prompts.render import PROMPT_VAR_RE

    for hostile in ["{{name}}", "{{{{name}}}}", "{{{{{{deep}}}}}}", "a {{b}} c {{d}}"]:
        prompt = agent_prompt(SignupProfile(business=hostile, goals=hostile))
        assert not PROMPT_VAR_RE.search(prompt), f"{hostile} survived as a prompt var"
        assert "name" in prompt or "deep" in prompt or "b" in prompt


def test_an_absent_profile_leaves_the_core_default_untouched() -> None:
    assert agent_prompt(None) == DEFAULT_AGENT_PROMPT


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
async def test_choices_offers_the_domain_workspace_and_every_exact_membership(
    onboard_client: AsyncClient, database_url: str
) -> None:
    if not database_url.startswith("postgresql"):
        pytest.skip("the candidate read is postgres SQL, for a fleet sqlite never serves")
    domain_workspace = signup_workspace_id("acme.com")
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
    with ws(other_workspace):
        async with workspace_tx() as connection:
            await create_member(connection, other_workspace, "contractor@acme.com")

    async with onboard_client as client:
        response = await client.get(
            "/internal/onboard/choices",
            params={
                "email": "contractor@acme.com",
                "domain": "acme.com",
                "signup_subject": "acme.com",
            },
        )
    assert response.status_code == 200
    by_id = {choice["workspace_id"]: choice for choice in response.json()["choices"]}
    assert by_id[str(other_workspace)]["member"] is True
    assert by_id[str(other_workspace)]["label"] == "other.com"
    assert by_id[str(domain_workspace)]["member"] is False
    assert by_id[str(domain_workspace)]["label"] == "acme.com"


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
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
            params={
                "email": "someone@acme.com",
                "domain": "acme.com",
                "signup_subject": "acme.com",
            },
        )
    assert refused.status_code == 409
    assert "maps to 2 workspaces" in refused.json()["detail"]


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
async def test_two_workspaces_sharing_a_label_are_told_apart(
    onboard_client: AsyncClient, database_url: str
) -> None:
    if not database_url.startswith("postgresql"):
        pytest.skip("the candidate read is postgres SQL, for a fleet sqlite never serves")
    ids = []
    for _ in range(2):
        workspace_id = uuid4()
        ids.append(workspace_id)
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
            params={
                "email": "contractor@zeta.com",
                "domain": "zeta.com",
                "signup_subject": "zeta.com",
            },
        )
    assert response.status_code == 200, response.json()
    labels = {choice["label"] for choice in response.json()["choices"]}
    assert labels == {f"acme.com ({str(workspace_id)[:8]})" for workspace_id in ids}


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
async def test_personal_mail_addresses_create_separate_workspaces(
    onboard_client: AsyncClient, database_url: str
) -> None:
    if not database_url.startswith("postgresql"):
        pytest.skip("the candidate read is postgres SQL, for a fleet sqlite never serves")
    first = "first@gmail.com"
    second = "second@gmail.com"
    async with onboard_client as client:
        created = await client.post(
            "/internal/onboard/seat",
            json={
                "workspace_id": str(signup_workspace_id(first)),
                "domain": "gmail.com",
                "email": first,
                "signup_subject": first,
            },
        )
        assert created.status_code == 200, created.json()
        unrelated = await client.get(
            "/internal/onboard/choices",
            params={
                "email": second,
                "domain": "gmail.com",
                "signup_subject": second,
            },
        )
        assert unrelated.json() == {"choices": []}
        second_created = await client.post(
            "/internal/onboard/seat",
            json={
                "workspace_id": str(signup_workspace_id(second)),
                "domain": "gmail.com",
                "email": second,
                "signup_subject": second,
            },
        )
    assert second_created.status_code == 200, second_created.json()
    assert signup_workspace_id(first) != signup_workspace_id(second)


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
async def test_a_personal_mail_member_can_enter_the_workspace_they_were_added_to(
    onboard_client: AsyncClient, database_url: str
) -> None:
    if not database_url.startswith("postgresql"):
        pytest.skip("the candidate read is postgres SQL, for a fleet sqlite never serves")
    workspace_id = signup_workspace_id("acme.com")
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.workspace).values(
                    id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
                )
            )
            await create_member(connection, workspace_id, "founder@acme.com", is_admin=True)
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await create_member(connection, workspace_id, "member@gmail.com")
    async with onboard_client as client:
        response = await client.get(
            "/internal/onboard/choices",
            params={
                "email": "member@gmail.com",
                "domain": "gmail.com",
                "signup_subject": "member@gmail.com",
            },
        )
    assert response.json() == {
        "choices": [{"workspace_id": str(workspace_id), "label": "acme.com", "member": True}]
    }


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
async def test_the_previous_gateway_shape_is_still_answered_across_a_rollout(
    onboard_client: AsyncClient, database_url: str
) -> None:
    """A migration meets the image it replaces. The migrate Job commits before either Deployment
    rolls and both roll with no unavailable pods, so a gateway pod from the release being replaced
    asks with `domain` and `email` alone for the whole window. Both routes take the verified domain
    as the subject then — the rule `ufo_control.fill_signup_subject` applies to the rows that same
    pod writes — rather than refusing the body and the query with 422."""
    if not database_url.startswith("postgresql"):
        pytest.skip("the candidate read is postgres SQL, for a fleet sqlite never serves")
    workspace_id = signup_workspace_id("acme.com")
    async with onboard_client as client:
        seated = await client.post(
            "/internal/onboard/seat",
            json={
                "workspace_id": str(workspace_id),
                "domain": "acme.com",
                "email": "founder@acme.com",
            },
        )
        assert seated.status_code == 200, seated.json()
        assert seated.json()["workspace_id"] == str(workspace_id)
        offered = await client.get(
            "/internal/onboard/choices",
            params={"email": "teammate@acme.com", "domain": "acme.com"},
        )
    assert offered.status_code == 200, offered.json()
    assert offered.json() == {
        "choices": [{"workspace_id": str(workspace_id), "label": "acme.com", "member": False}]
    }


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
async def test_the_previous_gateway_shape_continues_a_personal_claim(
    onboard_client: AsyncClient, database_url: str
) -> None:
    """A pod of this release files a personal claim's exact address in the column the gateway pod
    being replaced reads as `domain`. When that pod continues the session against this release's
    serve endpoint, the exact address is accepted as the subject and never as provider authority."""
    if not database_url.startswith("postgresql"):
        pytest.skip("the candidate read is postgres SQL, for a fleet sqlite never serves")
    founder = "carol@gmail.com"
    workspace_id = signup_workspace_id(founder)
    async with onboard_client as client:
        seated = await client.post(
            "/internal/onboard/seat",
            json={
                "workspace_id": str(workspace_id),
                "domain": founder,
                "email": founder,
            },
        )
        offered = await client.get(
            "/internal/onboard/choices",
            params={"email": founder, "domain": founder},
        )
        stranger = await client.get(
            "/internal/onboard/choices",
            params={"email": "dave@gmail.com", "domain": "dave@gmail.com"},
        )
    assert seated.status_code == 200, seated.json()
    assert seated.json() == {
        "workspace_id": str(workspace_id),
        "admin": True,
        "founding": True,
    }
    assert offered.json() == {
        "choices": [{"workspace_id": str(workspace_id), "label": founder, "member": True}]
    }
    assert stranger.json() == {"choices": []}


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
async def test_the_previous_gateway_shape_reaches_no_personal_mail_workspace(
    onboard_client: AsyncClient, database_url: str
) -> None:
    """The two images serve the same window, so a workspace a new pod founds for one exact address
    is already there while an old pod still sends the provider domain as the whole identity. That
    domain is nobody's authority over it: the founder's address is the workspace's own subject, so
    the stranger is offered nothing and seating them into it is refused."""
    if not database_url.startswith("postgresql"):
        pytest.skip("the candidate read is postgres SQL, for a fleet sqlite never serves")
    founder = "first@gmail.com"
    personal = signup_workspace_id(founder)
    async with onboard_client as client:
        created = await client.post(
            "/internal/onboard/seat",
            json={
                "workspace_id": str(personal),
                "domain": "gmail.com",
                "email": founder,
                "signup_subject": founder,
            },
        )
        assert created.status_code == 200, created.json()
        offered = await client.get(
            "/internal/onboard/choices",
            params={"email": "second@gmail.com", "domain": "gmail.com"},
        )
        refused = await client.post(
            "/internal/onboard/seat",
            json={
                "workspace_id": str(personal),
                "domain": "gmail.com",
                "email": "second@gmail.com",
            },
        )
    assert offered.json() == {"choices": []}
    assert refused.status_code == 409
    assert f"workspace {personal} no longer belongs to gmail.com" in refused.json()["detail"]


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
async def test_a_personal_mail_call_states_its_exact_identity(
    onboard_client: AsyncClient, database_url: str
) -> None:
    """This release's gateway states the signup subject under both wire names. A serve pod of the
    release being replaced reads `domain` as the whole identity, so an exact personal address lets
    it derive that address's workspace without ever receiving the shared provider. Core derives the
    verified domain from the email and offers no workspace to a stranger at that provider."""
    if not database_url.startswith("postgresql"):
        pytest.skip("the candidate read is postgres SQL, for a fleet sqlite never serves")
    founder = "carol@gmail.com"
    personal = signup_workspace_id(founder)
    async with onboard_client as client:
        created = await client.post(
            "/internal/onboard/seat",
            json={
                "workspace_id": str(personal),
                "domain": founder,
                "email": founder,
                "signup_subject": founder,
            },
        )
        assert created.status_code == 200, created.json()
        offered = await client.get(
            "/internal/onboard/choices",
            params={"email": founder, "domain": founder, "signup_subject": founder},
        )
        stranger = await client.get(
            "/internal/onboard/choices",
            params={
                "email": "dave@gmail.com",
                "domain": "dave@gmail.com",
                "signup_subject": "dave@gmail.com",
            },
        )
    assert created.json() == {"workspace_id": str(personal), "admin": True, "founding": True}
    assert offered.json() == {
        "choices": [{"workspace_id": str(personal), "label": founder, "member": True}]
    }
    assert stranger.json() == {"choices": []}


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
async def test_a_call_stating_no_domain_still_refuses_an_address_that_is_not_one(
    onboard_client: AsyncClient,
) -> None:
    """The domain check is what holds a malformed address out of a member row, so a call stating no
    domain must not pass it by default. The derivation answers the empty domain for anything that is
    not one `local@domain`, and the empty domain is refused."""
    async with onboard_client as client:
        seated = await client.post(
            "/internal/onboard/seat",
            json={"workspace_id": str(uuid4()), "email": "carol@gmail.com@evil.com"},
        )
        offered = await client.get("/internal/onboard/choices", params={"email": "not an address"})
    assert seated.status_code == 422
    assert offered.status_code == 422


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
async def test_choices_is_empty_for_an_address_nothing_holds(
    onboard_client: AsyncClient, database_url: str
) -> None:
    if not database_url.startswith("postgresql"):
        pytest.skip("the candidate read is postgres SQL, for a fleet sqlite never serves")
    async with onboard_client as client:
        response = await client.get(
            "/internal/onboard/choices",
            params={
                "email": "nobody@nowhere.com",
                "domain": "nowhere.com",
                "signup_subject": "nowhere.com",
            },
        )
    assert response.json() == {"choices": []}


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
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


def test_the_signup_subject_derivation_matches_the_rust_contract() -> None:
    """`servers/control/tests/onboard_contract.json` holds the same vectors the Rust client
    asserts. One subject has to derive one workspace on both ends, or a customer signing in through
    the gateway would be seated in a workspace the portal never shows them."""
    import json
    from pathlib import Path

    contract = Path(__file__).parents[3] / "servers" / "control" / "tests" / "onboard_contract.json"
    vectors: dict[str, str] = json.loads(contract.read_text())
    assert len(vectors) >= 4
    for subject, expected in vectors.items():
        assert signup_workspace_id(subject) == UUID(expected), subject


STAMP = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)


async def _stamp(member_id: UUID, invited_by: UUID, at: datetime) -> None:
    async with owner_tx() as connection:
        await connection.execute(
            sa.text("update member set invited_at = :at, invited_by = :by where id = :id"),
            {"at": at, "by": invited_by, "id": member_id},
        )


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
async def test_invitations_lists_the_teammates_an_admin_added(
    onboard_client: AsyncClient, database_url: str
) -> None:
    if not database_url.startswith("postgresql"):
        pytest.skip("the invitation read is postgres SQL, for a fleet sqlite never serves")
    workspace_id = signup_workspace_id("acme.com")
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


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
async def test_the_invitation_page_cursor_walks_past_a_shared_stamp(
    onboard_client: AsyncClient, database_url: str
) -> None:
    if not database_url.startswith("postgresql"):
        pytest.skip("the invitation read is postgres SQL, for a fleet sqlite never serves")
    workspace_id = signup_workspace_id("acme.com")
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


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
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
                params={
                    "email": "founder@acme.com",
                    "domain": "acme.com",
                    "signup_subject": "acme.com",
                },
            )

    assert page.status_code == 200
    assert swept == []
    assert _warned_routes(caplog) == ["fleet", "choices"]


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
async def test_recipients_page_every_seat_in_the_fleet_once_per_member_row(
    onboard_client: AsyncClient, database_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    if not database_url.startswith("postgresql"):
        pytest.skip("the recipient read crosses workspaces, which sqlite never serves")
    for domain in ("acme.com", "globex.com"):
        workspace_id = signup_workspace_id(domain)
        with ws(workspace_id):
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.insert(tables.workspace).values(
                        id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
                    )
                )
                await create_member(connection, workspace_id, f"founder@{domain}", is_admin=True)
                await create_member(connection, workspace_id, "shared@acme.com")
    async with onboard_client as client:
        with caplog.at_level(logging.WARNING, logger="ufo"):
            whole = (await client.get("/internal/onboard/recipients")).json()["recipients"]
            after_first = await client.get(
                "/internal/onboard/recipients",
                params={
                    "after_created_at": whole[0]["created_at"],
                    "after_member_id": whole[0]["member_id"],
                },
            )
            refused = await client.get(
                "/internal/onboard/recipients", params={"after_member_id": whole[0]["member_id"]}
            )

    assert sorted(row["email"] for row in whole) == [
        "founder@acme.com",
        "founder@globex.com",
        "shared@acme.com",
        "shared@acme.com",
    ], "one row per seat, so the caller is what holds a repeated address to one message"
    assert len({row["workspace_id"] for row in whole}) == 2
    assert len(after_first.json()["recipients"]) == len(whole) - 1
    assert refused.status_code == 422
    assert _warned_routes(caplog) == ["recipients", "recipients"], (
        "an operator building a mailing list out of every workspace is a read to record, and a "
        "refused cursor never reaches one"
    )


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
async def test_recipients_drop_a_member_whose_seat_an_admin_revoked(
    onboard_client: AsyncClient, database_url: str
) -> None:
    """A revoked seat keeps its member row with `seated_at` cleared. Someone an admin removed from
    the product is not someone a campaign may mail, and the campaign freezes whatever this page
    answers, so the filter belongs here rather than in the caller."""
    if not database_url.startswith("postgresql"):
        pytest.skip("the recipient read crosses workspaces, which sqlite never serves")
    workspace_id = signup_workspace_id("acme.com")
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.workspace).values(
                    id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
                )
            )
            await create_member(connection, workspace_id, "admin@acme.com", is_admin=True)
            await create_member(connection, workspace_id, "leaver@acme.com")
        async with workspace_tx() as connection:
            await Seats(workspace_id=workspace_id).revoke(connection, "leaver@acme.com")
    async with onboard_client as client:
        listed = (await client.get("/internal/onboard/recipients")).json()["recipients"]

    assert [row["email"] for row in listed] == ["admin@acme.com"]

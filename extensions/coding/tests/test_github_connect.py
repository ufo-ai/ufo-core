"""The connect flow's proof: what a return leg from GitHub is allowed to bind.

The seal decides which workspace a browser redirect belongs to; GitHub decides whether the member
behind it reaches the installation being claimed. Both halves are exercised here, including the
attack the flow exists to stop — a workspace admin who holds a perfectly valid link for their own
workspace returning with somebody else's installation id. GitHub stands in as a transport; the
assertions are on what we send and what we do with the answer, never on the fake."""

import json
import time
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
import ufo_ext_coding.connect as connect
from cryptography.fernet import Fernet
from starlette.datastructures import QueryParams
from ufo_ext_coding.manifest import manifest as coding_manifest

from ufo.db import workspace_tx
from ufo.host.ext.loader import turn_workspace_facts
from ufo.runtime.access.credentials import (
    CREDENTIAL_REQUEST_PURPOSE,
    CREDENTIAL_REQUEST_TTL_SECONDS,
    INSTALLATION_BINDING_PURPOSE,
    CredentialRequestInvalid,
    CredentialRequests,
    CredentialRequestState,
    CredentialStore,
    install_credential_requests,
    open_credential_request,
    open_installation,
    seal_credential_request,
)
from ufo.runtime.ext.context import CredentialAccess
from ufo.runtime.workspace import init_workspace_credentials, ws
from ufo.schema import tables
from ufo.sdk.audience import SHARED_AUDIENCE
from ufo.sdk.callback_page import CLOSE_THIS_PAGE, CONNECT_LOGO_PATH
from ufo.sdk.http import Response

APP_ID = "4396470"
OURS = "149082716"
THEIRS = "149082717"


class _Request:
    """A return leg as the route sees it: query params and nothing else."""

    def __init__(self, **params: str) -> None:
        self.query_params = QueryParams(params)


class _Credentials:
    """Records what the route bound, so a refusal can be proved by absence."""

    def __init__(self) -> None:
        self.bound: list[tuple[str, str]] = []

    async def bind_installation(self, slot: str, installation_id: str) -> None:
        self.bound.append((slot, installation_id))


HOME_URL = "https://app.ufo.test/surface/web"


class _Ctx:
    def __init__(self, home: str | None = HOME_URL) -> None:
        self.credentials = _Credentials()
        self.home = home

    def home_url(self, fragment: str = "") -> str | None:
        return None if self.home is None else f"{self.home}{fragment}"


def _github(*, reachable: tuple[str, ...], token: str | None = "user-token") -> httpx.MockTransport:
    """GitHub as the return leg meets it: the code exchange, then the member's own installations."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_token"):
            body = {"access_token": token} if token else {"error": "bad_verification_code"}
            return httpx.Response(200, json=body)
        assert request.headers["Authorization"] == f"Bearer {token}"
        return httpx.Response(
            200,
            json={"installations": [{"id": int(one), "app_id": int(APP_ID)} for one in reachable]},
        )

    return httpx.MockTransport(handle)


def _exchange(**kwargs: object) -> connect.GitHubInstallExchange:
    return connect.GitHubInstallExchange(
        client_id="Iv1", client_secret="secret", app_id=APP_ID, **kwargs
    )


def _sealed(fernet: Fernet, workspace_id: UUID, slots: tuple[str, ...], payload: str) -> str:
    return seal_credential_request(
        fernet,
        CredentialRequestState(
            workspace_id=workspace_id, member_id=uuid4(), slots=slots, payload=payload
        ),
    )


def test_identify_resolves_a_workspace_only_from_this_deploys_seal_for_this_slot() -> None:
    """`identify` is the whole reason a redirect carrying no session finds a workspace. It answers
    for this deploy's own seal naming this slot and purpose and for nothing else — not a forged
    blob, and not a seal minted to authorize some other slot, which would otherwise let one
    authorization stand in for another."""
    fernet = Fernet(Fernet.generate_key())
    install_credential_requests(
        CredentialRequests(
            fernet=fernet,
            declared=frozenset({connect.GIT_INSTALLATION_SLOT}),
            fillable=frozenset(),
        )
    )
    workspace_id = uuid4()
    slot, payload = connect.GIT_INSTALLATION_SLOT, connect.INSTALL_PAYLOAD

    assert (
        connect.install_workspace(_Request(state=_sealed(fernet, workspace_id, (slot,), payload)))
        == workspace_id
    )
    assert connect.install_workspace(_Request(state="not-a-seal")) is None
    assert connect.install_workspace(_Request(state="")) is None
    assert connect.install_workspace(_Request()) is None
    assert (
        connect.install_workspace(
            _Request(state=_sealed(fernet, workspace_id, ("other_slot",), payload))
        )
        is None
    )
    assert (
        connect.install_workspace(
            _Request(state=_sealed(fernet, workspace_id, (slot,), "other-purpose"))
        )
        is None
    )


def test_an_installation_binding_does_not_replay_as_an_authorization_seal() -> None:
    """The purpose check in isolation. This seal differs from a valid one in nothing but purpose —
    same deploy key, same workspace, same slot, same payload — so it is refused only because it was
    sealed to bind an installation rather than to authorize one. A binding is a value already at
    rest in the slot, so without this the return leg could be driven from what is stored."""
    fernet = Fernet(Fernet.generate_key())
    install_credential_requests(
        CredentialRequests(
            fernet=fernet,
            declared=frozenset({connect.GIT_INSTALLATION_SLOT}),
            fillable=frozenset(),
        )
    )
    workspace_id = uuid4()
    replayed = seal_credential_request(
        fernet,
        CredentialRequestState(
            workspace_id=workspace_id,
            member_id=uuid4(),
            slots=(connect.GIT_INSTALLATION_SLOT,),
            payload=connect.INSTALL_PAYLOAD,
            purpose=INSTALLATION_BINDING_PURPOSE,
        ),
    )

    assert connect.install_workspace(_Request(state=replayed)) is None


def test_a_binding_outlives_the_request_ttl_it_was_never_bound_by() -> None:
    """A binding is opened with no TTL because an installation outlives the prompt that bound it.
    Sealed at a backdated timestamp well past the request window, it must still open — a regression
    that applied the request TTL here would break git for every already-connected workspace fifteen
    minutes after it connected, and only a backdated seal catches that."""
    fernet = Fernet(Fernet.generate_key())
    workspace_id = uuid4()
    slot = connect.GIT_INSTALLATION_SLOT
    aged = fernet.encrypt_at_time(
        CredentialRequestState(
            workspace_id=workspace_id,
            member_id=uuid4(),
            slots=(slot,),
            payload="149082716",
            purpose=INSTALLATION_BINDING_PURPOSE,
        )
        .model_dump_json()
        .encode(),
        int(time.time()) - CREDENTIAL_REQUEST_TTL_SECONDS * 4,
    ).decode()

    assert open_installation(fernet, workspace_id, slot, aged) == "149082716"


def test_a_schema_invalid_payload_is_refused_rather_than_escaping_as_a_validation_error() -> None:
    """Every open is total: ciphertext this deploy can decrypt but whose plaintext is not a state
    raises the credential fault, not a raw pydantic error. It is the branch a corrupted or
    foreign-shaped stored value reaches, and it must look like every other refusal to its caller."""
    fernet = Fernet(Fernet.generate_key())
    install_credential_requests(
        CredentialRequests(
            fernet=fernet,
            declared=frozenset({connect.GIT_INSTALLATION_SLOT}),
            fillable=frozenset(),
        )
    )
    wrong_shape = fernet.encrypt(json.dumps({"not": "a state"}).encode()).decode()

    with pytest.raises(CredentialRequestInvalid):
        open_credential_request(fernet, wrong_shape, purpose=CREDENTIAL_REQUEST_PURPOSE)

    assert connect.install_workspace(_Request(state=wrong_shape)) is None


def test_a_seal_from_another_deploy_key_resolves_no_workspace() -> None:
    """A seal is only ever this deploy's own: one minted under a different key opens nothing, so a
    redirect carrying it reaches no workspace to bind."""
    install_credential_requests(
        CredentialRequests(
            fernet=Fernet(Fernet.generate_key()),
            declared=frozenset({connect.GIT_INSTALLATION_SLOT}),
            fillable=frozenset(),
        )
    )
    foreign = _sealed(
        Fernet(Fernet.generate_key()),
        uuid4(),
        (connect.GIT_INSTALLATION_SLOT,),
        connect.INSTALL_PAYLOAD,
    )
    assert connect.install_workspace(_Request(state=foreign)) is None


async def test_an_installation_the_member_reaches_is_bound() -> None:
    """The happy path: GitHub confirms the authorizing member reaches the installation that came
    back on the redirect, so the workspace binds it."""
    ctx = _Ctx()
    exchange = _exchange(transport=_github(reachable=(OURS,)))
    assert await exchange.reaches("code", OURS) is True
    response = await _route(ctx, exchange, code="code", installation_id=OURS)
    assert response.status_code == 200
    assert ctx.credentials.bound == [(connect.GIT_INSTALLATION_SLOT, OURS)]
    # Nothing is left for the member here, so the page draws the mark, offers the one way back into
    # the conversation, and takes its own tab away where the browser allows it.
    page = response.body.decode()
    assert CONNECT_LOGO_PATH in page and "window.close()" in page
    assert f'<a href="{HOME_URL}">{connect.RETURN_LABEL}</a>' in page


async def test_a_deploy_with_no_browser_home_says_to_close_the_tab() -> None:
    """A link needs somewhere to go. On a deploy that installs no browser surface there is no such
    place, so the finished page tells the member the one thing left instead of rendering a link
    into nothing."""
    ctx = _Ctx(home=None)
    exchange = _exchange(transport=_github(reachable=(OURS,)))
    response = await _route(ctx, exchange, code="code", installation_id=OURS)
    page = response.body.decode()

    assert response.status_code == 200
    assert "<a " not in page
    assert CLOSE_THIS_PAGE in page
    # An App install leaves a conversation to install on an organization, so the page promises none.
    assert "conversation" not in page


async def test_an_installation_the_member_does_not_reach_is_refused() -> None:
    """The attack this flow exists to stop. The seal is genuine and names this workspace — its own
    owner minted it — but the id on the redirect belongs to an organization the authorizing member
    has nothing to do with. GitHub does not list it, so nothing is bound and no token can ever be
    minted against it."""
    ctx = _Ctx()
    exchange = _exchange(transport=_github(reachable=(OURS,)))
    assert await exchange.reaches("code", THEIRS) is False
    response = await _route(ctx, exchange, code="code", installation_id=THEIRS)
    assert response.status_code == 403
    assert ctx.credentials.bound == []


async def test_an_installation_of_another_app_is_refused() -> None:
    """A member may hold installations of other GitHub Apps entirely. Only this deploy's own App
    counts, or a member could bind an id this deploy's key cannot mint against."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_token"):
            return httpx.Response(200, json={"access_token": "user-token"})
        return httpx.Response(200, json={"installations": [{"id": int(OURS), "app_id": 999}]})

    assert await _exchange(transport=httpx.MockTransport(handle)).reaches("code", OURS) is False


async def test_a_declined_code_binds_nothing() -> None:
    """No member identity, no binding: a code GitHub refuses leaves the flow with nothing that
    proves who authorized, so it reports the failure rather than trusting the id it was handed."""
    ctx = _Ctx()
    exchange = _exchange(transport=_github(reachable=(OURS,), token=None))
    with pytest.raises(connect.GitHubAuthorizationError):
        await exchange.reaches("code", OURS)
    response = await _route(ctx, exchange, code="code", installation_id=OURS)
    assert response.status_code == 502
    assert ctx.credentials.bound == []
    # The retry is the whole point of the page, so this one stays open to be read.
    assert "window.close()" not in response.body.decode()
    assert connect.ASK_UFO_AGAIN in response.body.decode()


async def test_a_return_leg_without_an_authorization_binds_nothing() -> None:
    """GitHub returns here after a bare install too, with no code to exchange. Without one there is
    no member identity to check the id against, so the flow asks for the authorizing pass instead
    of binding what it cannot verify."""
    ctx = _Ctx()
    exchange = _exchange(transport=_github(reachable=(OURS,)))
    for params in ({"installation_id": OURS}, {"code": "code"}, {}):
        response = await _route(ctx, exchange, **params)
        assert response.status_code == 400
    assert ctx.credentials.bound == []


async def _route(ctx: _Ctx, exchange: connect.GitHubInstallExchange, **params: str) -> Response:
    """Drive the real handler with the deploy's exchange stood in for."""
    original = connect.install_exchange
    connect.install_exchange = lambda: exchange  # type: ignore[assignment]
    try:
        return await connect.github_installed(ctx, _Request(**params))  # type: ignore[arg-type]
    finally:
        connect.install_exchange = original  # type: ignore[assignment]


def test_the_exchange_sends_the_deploy_identity_and_the_returned_code() -> None:
    """What GitHub is asked is the point: the App's own client id and secret with the code from the
    redirect. A member token is only ever obtained by presenting all three."""
    seen: list[dict[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_token"):
            seen.append(dict(pair.split("=", 1) for pair in request.content.decode().split("&")))
            return httpx.Response(200, json={"access_token": "user-token"})
        return httpx.Response(200, json={"installations": []})

    import asyncio

    asyncio.run(_exchange(transport=httpx.MockTransport(handle)).reaches("the-code", OURS))
    assert seen == [{"client_id": "Iv1", "client_secret": "secret", "code": "the-code"}]
    assert json.dumps(seen)  # the recorded call is plain data, not a mock object


@pytest.mark.parametrize("installed", [True, False])
async def test_the_prompt_states_the_github_app_this_workspace_installed(
    db: None, installed: bool
) -> None:
    """The fact the connector listing has no row for. Installing the App binds a credential seal and
    writes no connector grant, so an agent reading the listing alone answered that GitHub was
    unconnected on the turn after the first run installed it. The seal is written by the same
    `bind_installation` the return leg calls, and the declared fact is read the way the turn reads
    it. A workspace that bound no installation states nothing, which keeps the offer connect_github
    is."""
    fernet = Fernet(Fernet.generate_key())
    store = CredentialStore(fernet=fernet)
    slot = connect.GIT_INSTALLATION_SLOT
    install_credential_requests(
        CredentialRequests(fernet=fernet, declared=frozenset({slot}), fillable=frozenset({slot}))
    )
    init_workspace_credentials(store)
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    with ws(workspace_id):
        if installed:
            await CredentialAccess(declared=frozenset({slot})).bind_installation(slot, OURS)
        lines = await turn_workspace_facts((coding_manifest(),), store, audience=SHARED_AUDIENCE)
    assert lines == ((connect.GITHUB_APP_INSTALLED_LINE,) if installed else ())

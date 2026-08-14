"""The minting mechanism itself: the JWT this deploy signs, the exchange it makes, and the binding
it refuses. GitHub stands in as a transport — the assertions are on what we send and what we do with
what comes back, never on the fake."""

import asyncio
from datetime import UTC, datetime, timedelta
from json import dumps, loads
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa
from ufo_ext_coding.github_app import GitHubAppTokens

from ufo.credentials import (
    CredentialMintFailed,
    CredentialRequestInvalid,
    CredentialRequests,
    CredentialSlotUnset,
    CredentialStore,
    install_credential_requests,
    seal_installation,
)
from ufo.db import workspace_tx
from ufo.ext.context import CredentialAccess
from ufo.ext.manifest import CredentialSlot, InjectionTarget
from ufo.sandbox.proxy.rules import InjectionRule, ScopeRule, derive_credential_rules
from ufo.schema import tables
from ufo.workspace import init_workspace_credentials, ws

APP_ID = "4396470"
INSTALLATION = "149082716"
SLOT = "github_app_installation"


def _tokens(**kwargs: object) -> GitHubAppTokens:
    return GitHubAppTokens(
        app_id=APP_ID,
        private_key=rsa.generate_private_key(public_exponent=65537, key_size=2048),
        installation_slot=SLOT,
        **kwargs,
    )


VALUES: dict[tuple[UUID, str], str] = {}


class _Store(CredentialStore):
    """A credential store over a dict — the DB is not what these assert."""

    async def get(self, workspace_id: UUID, slot: str) -> str:
        try:
            return VALUES[(workspace_id, slot)]
        except KeyError:
            raise CredentialSlotUnset(slot) from None


async def test_an_unbound_workspace_mints_nothing_so_the_stored_token_answers() -> None:
    """No installation, no mint — which is what lets a member's own token remain the fallback for a
    repository outside any organization that installed the App."""
    VALUES.clear()

    assert await _tokens().secret(uuid4(), _Store(fernet=Fernet(Fernet.generate_key()))) is None


async def test_a_hand_typed_installation_id_is_refused_rather_than_minted_against() -> None:
    """The attack the seal exists for: an id typed straight into the slot through the ordinary
    credential prompt. It never opens, so no token is ever minted against a foreign installation."""
    workspace_id = uuid4()
    VALUES.clear()
    VALUES.update({(workspace_id, SLOT): INSTALLATION})
    store = _Store(fernet=Fernet(Fernet.generate_key()))
    with pytest.raises(CredentialRequestInvalid, match="tampered or expired"):
        await _tokens().secret(workspace_id, store)


async def test_another_workspaces_binding_is_refused() -> None:
    """A binding lifted from another workspace names that workspace, so it cannot be replayed here
    even though the value is one this deploy really did seal."""
    fernet = Fernet(Fernet.generate_key())
    mine, theirs = uuid4(), uuid4()
    VALUES.clear()
    VALUES.update({(mine, SLOT): seal_installation(fernet, theirs, SLOT, INSTALLATION)})
    with pytest.raises(CredentialRequestInvalid, match="another workspace"):
        await _tokens().secret(mine, _Store(fernet=fernet))


async def test_an_unopenable_binding_withholds_the_git_host_and_no_other() -> None:
    """The incident, end to end through the real source and the real derivation. The stored value is
    the production one: a blob the credential Fernet decrypts but that is not a sealed state — a
    shape no `seal_installation` produces. github.com alone is withheld, while every other slot's
    injection and scope survive the same call, which is what keeps the workspace's internet and
    grant rules from unwinding with it."""
    fernet = Fernet(Fernet.generate_key())
    workspace_id = uuid4()
    other_host = "api.datadoghq.com"
    VALUES.clear()
    VALUES.update(
        {
            (workspace_id, SLOT): fernet.encrypt(
                dumps({"workspace_id": str(workspace_id), "installation_id": INSTALLATION}).encode()
            ).decode(),
            (workspace_id, "datadog_api_key"): "dd-api-real",
        }
    )
    slots = (
        CredentialSlot(
            name="github_git_token",
            description="git",
            source=_tokens(),
            injection=InjectionTarget(
                host="github.com",
                header="Authorization",
                sentinel="UFO_SENTINEL_GIT_GITHUB",
                git_basic_user="x-access-token",
            ),
        ),
        CredentialSlot(
            name="datadog_api_key",
            description="datadog",
            injection=InjectionTarget(
                host=other_host, header="DD-API-KEY", sentinel="UFO_SENTINEL_DATADOG"
            ),
        ),
    )

    rules = await derive_credential_rules(slots, workspace_id, _Store(fernet=fernet))

    assert [rule.host for rule in rules if isinstance(rule, InjectionRule)] == [other_host]
    assert ScopeRule(allowed_hosts=frozenset({other_host})) in rules
    assert not [
        rule for rule in rules if isinstance(rule, ScopeRule) and "github.com" in rule.allowed_hosts
    ]


async def test_a_bound_workspace_mints_and_reuses_the_token_until_it_nears_expiry() -> None:
    """One mint serves the conversation: the token is cached against GitHub's own stated expiry, so
    a second turn reuses it rather than exchanging a fresh JWT for every clone."""
    fernet = Fernet(Fernet.generate_key())
    workspace_id = uuid4()
    VALUES.clear()
    VALUES.update(
        {(workspace_id, SLOT): seal_installation(fernet, workspace_id, SLOT, INSTALLATION)}
    )
    calls: list[httpx.Request] = []
    expires = (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")

    async def github(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(201, json={"token": "ghs_minted", "expires_at": expires})

    tokens = _tokens(transport=httpx.MockTransport(github))
    store = _Store(fernet=fernet)

    assert await tokens.secret(workspace_id, store) == "ghs_minted"
    assert await tokens.secret(workspace_id, store) == "ghs_minted"

    assert len(calls) == 1
    (call,) = calls
    assert call.url.path == f"/app/installations/{INSTALLATION}/access_tokens"
    assert loads(call.content)["permissions"] == {"contents": "write"}
    scheme, _, jwt = call.headers["authorization"].partition(" ")
    assert scheme == "Bearer"
    assert len(jwt.split(".")) == 3


async def test_concurrent_cache_misses_share_one_installation_token_mint() -> None:
    fernet = Fernet(Fernet.generate_key())
    workspace_id = uuid4()
    VALUES.clear()
    VALUES.update(
        {(workspace_id, SLOT): seal_installation(fernet, workspace_id, SLOT, INSTALLATION)}
    )
    expires = (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    calls = 0

    async def github(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return httpx.Response(201, json={"token": "ghs_shared", "expires_at": expires})

    tokens = _tokens(transport=httpx.MockTransport(github))
    store = _Store(fernet=fernet)
    results = await asyncio.gather(*(tokens.secret(workspace_id, store) for _ in range(20)))
    assert results == ["ghs_shared"] * 20
    assert calls == 1


async def test_a_cancelled_waiter_does_not_cancel_the_shared_mint() -> None:
    fernet = Fernet(Fernet.generate_key())
    workspace_id = uuid4()
    VALUES.clear()
    VALUES.update(
        {(workspace_id, SLOT): seal_installation(fernet, workspace_id, SLOT, INSTALLATION)}
    )
    expires = (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    entered = asyncio.Event()
    release = asyncio.Event()

    async def github(request: httpx.Request) -> httpx.Response:
        entered.set()
        await release.wait()
        return httpx.Response(201, json={"token": "ghs_shared", "expires_at": expires})

    tokens = _tokens(transport=httpx.MockTransport(github))
    store = _Store(fernet=fernet)
    owner = asyncio.create_task(tokens.secret(workspace_id, store))
    await entered.wait()
    waiter = asyncio.create_task(tokens.secret(workspace_id, store))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    release.set()
    assert await owner == "ghs_shared"
    assert await tokens.secret(workspace_id, store) == "ghs_shared"


async def test_a_failed_mint_clears_the_singleflight_for_the_next_call() -> None:
    fernet = Fernet(Fernet.generate_key())
    workspace_id = uuid4()
    VALUES.clear()
    VALUES.update(
        {(workspace_id, SLOT): seal_installation(fernet, workspace_id, SLOT, INSTALLATION)}
    )
    expires = (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    calls = 0

    async def github(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, text="Unavailable")
        return httpx.Response(201, json={"token": "ghs_recovered", "expires_at": expires})

    tokens = _tokens(transport=httpx.MockTransport(github))
    store = _Store(fernet=fernet)
    with pytest.raises(RuntimeError, match="minted no token"):
        await tokens.secret(workspace_id, store)
    assert await tokens.secret(workspace_id, store) == "ghs_recovered"
    assert calls == 2


async def test_a_refused_exchange_raises_rather_than_falling_back() -> None:
    """A workspace that installed the App and then cannot mint must not quietly answer with the
    member's own token: that would authenticate as an identity the organization never granted."""
    fernet = Fernet(Fernet.generate_key())
    workspace_id = uuid4()
    VALUES.clear()
    VALUES.update(
        {(workspace_id, SLOT): seal_installation(fernet, workspace_id, SLOT, INSTALLATION)}
    )

    async def refused(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Not Found")

    with pytest.raises(CredentialMintFailed, match="minted no token"):
        await _tokens(transport=httpx.MockTransport(refused)).secret(
            workspace_id, _Store(fernet=fernet)
        )


async def test_an_unreachable_provider_arrives_as_the_declared_mint_failure() -> None:
    """A network fault is the same external uncertainty as a refusal, so it must reach the
    derivation as the one type it catches. Raw `httpx` escaping here would abort the whole rule
    derivation and fail a turn that never touches git, instead of withholding this host."""
    fernet = Fernet(Fernet.generate_key())
    workspace_id = uuid4()
    VALUES.clear()
    VALUES.update(
        {(workspace_id, SLOT): seal_installation(fernet, workspace_id, SLOT, INSTALLATION)}
    )

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(CredentialMintFailed, match="unreachable"):
        await _tokens(transport=httpx.MockTransport(unreachable)).secret(
            workspace_id, _Store(fernet=fernet)
        )


async def test_a_201_with_an_unreadable_body_arrives_as_the_declared_mint_failure() -> None:
    """GitHub answering 201 is not the same as GitHub answering a token. A body missing the token, a
    timestamp that will not parse, a timestamp that is not a string, or a body that is not an object
    at all is still the provider being unreliable — so each must reach the derivation as the type it
    catches, not as a raw KeyError, ValueError or TypeError that would abort every host's rules for
    a turn that never touches git."""
    fernet = Fernet(Fernet.generate_key())
    workspace_id = uuid4()
    VALUES.clear()
    VALUES.update(
        {(workspace_id, SLOT): seal_installation(fernet, workspace_id, SLOT, INSTALLATION)}
    )

    for body in (
        {"expires_at": "2026-07-26T12:00:00Z"},
        {"token": "ghs_x", "expires_at": "soon"},
        {"token": "ghs_x", "expires_at": 1800},
        ["not", "an", "object"],
    ):

        async def answered(request: httpx.Request, body: object = body) -> httpx.Response:
            return httpx.Response(201, json=body)

        with pytest.raises(CredentialMintFailed, match="unreadable token"):
            await _tokens(transport=httpx.MockTransport(answered)).secret(
                workspace_id, _Store(fernet=fernet)
            )


async def test_rebinding_to_another_installation_mints_against_the_new_one() -> None:
    """A workspace can move to a different installation — reinstalling on another organization
    rewrites the slot. The cached token must not survive that: keyed by workspace alone it would
    keep authenticating as the old installation for the rest of its hour, which is exactly the
    mismatch between what the slot names and what the wire carries that the seal exists to stop."""
    fernet = Fernet(Fernet.generate_key())
    workspace_id = uuid4()
    other = "149082999"
    VALUES.clear()
    VALUES.update(
        {(workspace_id, SLOT): seal_installation(fernet, workspace_id, SLOT, INSTALLATION)}
    )
    expires = (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    minted: list[str] = []

    async def github(request: httpx.Request) -> httpx.Response:
        installation = request.url.path.split("/")[3]
        minted.append(installation)
        return httpx.Response(201, json={"token": f"ghs_{installation}", "expires_at": expires})

    tokens = _tokens(transport=httpx.MockTransport(github))
    store = _Store(fernet=fernet)

    assert await tokens.secret(workspace_id, store) == f"ghs_{INSTALLATION}"
    VALUES[(workspace_id, SLOT)] = seal_installation(fernet, workspace_id, SLOT, other)
    assert await tokens.secret(workspace_id, store) == f"ghs_{other}"
    assert minted == [INSTALLATION, other]


async def test_a_binding_written_by_the_route_is_what_the_minter_opens(db: None) -> None:
    """The join the two halves meet at, with nothing stood in for: `bind_installation` writes the
    real slot through the real seal, and `GitHubAppTokens.secret` reads that same stored value back
    and mints against the installation it names. Tests that seal by hand would pass even if the two
    sides disagreed about what a bound slot contains."""
    fernet = Fernet(Fernet.generate_key())
    store = CredentialStore(fernet=fernet)
    install_credential_requests(
        CredentialRequests(fernet=fernet, declared=frozenset({SLOT}), fillable=frozenset({SLOT}))
    )
    init_workspace_credentials(store)
    async with workspace_tx() as connection:
        workspace_id = uuid4()
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    expires = (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")

    async def github(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/app/installations/{INSTALLATION}/access_tokens"
        return httpx.Response(201, json={"token": "ghs_joined", "expires_at": expires})

    with ws(workspace_id):
        await CredentialAccess(declared=frozenset({SLOT})).bind_installation(SLOT, INSTALLATION)

    stored = await store.get(workspace_id, SLOT)
    assert INSTALLATION not in stored
    tokens = _tokens(transport=httpx.MockTransport(github))
    assert await tokens.secret(workspace_id, store) == "ghs_joined"


async def test_bound_reports_an_unbound_workspace_without_reaching_github() -> None:
    """The sandbox-open contributors ask only whether a slot is filled, so an unbound workspace
    must answer False off the store alone — a transport that would fail proves nothing was sent."""
    VALUES.clear()

    tokens = _tokens(transport=httpx.MockTransport(lambda _request: pytest.fail("minted")))

    assert await tokens.bound(uuid4(), _Store(fernet=Fernet(Fernet.generate_key()))) is False


async def test_bound_reports_a_binding_this_deploy_can_open() -> None:
    """A seal this deploy wrote is a binding, and answering it still mints nothing: the whole point
    of the check is that a turn which never touches git does not reach GitHub."""
    VALUES.clear()
    fernet = Fernet(Fernet.generate_key())
    workspace_id = uuid4()
    VALUES[(workspace_id, SLOT)] = seal_installation(fernet, workspace_id, SLOT, INSTALLATION)

    tokens = _tokens(transport=httpx.MockTransport(lambda _request: pytest.fail("minted")))

    assert await tokens.bound(workspace_id, _Store(fernet=fernet)) is True


async def test_bound_refuses_a_seal_this_deploy_cannot_open_exactly_as_secret_does() -> None:
    """A stored value that is not this deploy's own seal for this workspace and slot is not a
    binding — and it refuses the same way in both roles.

    `bound` answers for the sandbox export and `secret` for the wire. Answering `False` here while
    `secret` raises is what exports the member's own token into a sandbox whose proxy then refuses
    the host: git configured to authenticate against a host that admits nothing. An unset slot is
    still plainly `False`, because that is a workspace with no installation rather than one holding
    a value nobody can use."""
    VALUES.clear()
    fernet = Fernet(Fernet.generate_key())
    workspace_id = uuid4()
    store = _Store(fernet=fernet)

    assert await _tokens().bound(workspace_id, store) is False

    for unopenable in (
        seal_installation(Fernet(Fernet.generate_key()), workspace_id, SLOT, INSTALLATION),
        seal_installation(fernet, uuid4(), SLOT, INSTALLATION),
        INSTALLATION,
    ):
        VALUES[(workspace_id, SLOT)] = unopenable
        with pytest.raises(CredentialRequestInvalid):
            await _tokens().bound(workspace_id, store)
        with pytest.raises(CredentialRequestInvalid):
            await _tokens().secret(workspace_id, store)

    VALUES[(workspace_id, SLOT)] = seal_installation(fernet, workspace_id, SLOT, INSTALLATION)

    assert await _tokens().bound(workspace_id, store) is True

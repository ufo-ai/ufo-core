"""The sandbox-side GitHub credential the pipedream extension declares, end to end: the manifest
attaches a `CliCredential` to the one connector whose token the sandbox rides, `account_token` is
the read that hands this deploy the provider token — under the same ownership assertion every other
account read makes — and `PipedreamGrantSecret` holds what it read so one turn's rule derivations
share a read. Pipedream's account read is mocked with `httpx.MockTransport` — no live API, no
credentials — so the real client and secret run against canned Connect responses."""

from collections.abc import Callable, Iterator
from uuid import UUID, uuid4

import httpx
import pytest
import ufo_ext_pipedream.client as pipedream
import ufo_ext_pipedream.manifest as pipedream_manifest
from ufo_ext_pipedream.provider import PipedreamOAuthProvider
from ufo_ext_pipedream.token import (
    GITHUB_USER_URL,
    GithubAccount,
    PipedreamGrantSecret,
    account_identity,
    cli_credential,
)

from ufo.host.ext.loader import connector_clis
from ufo.sdk.connectors import CliCredential, CommitIdentity, GitWire, GrantUnusable

ACCOUNT = "apn_github_1"
OTHER_ACCOUNT = "apn_github_2"


@pytest.fixture(autouse=True)
def _pipedream_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(pipedream.PIPEDREAM_CLIENT_ID_ENV, f"cid_{uuid4().hex}")
    monkeypatch.setenv(pipedream.PIPEDREAM_CLIENT_SECRET_ENV, "csecret")
    monkeypatch.setenv(pipedream.PIPEDREAM_PROJECT_ID_ENV, "proj_test")
    yield


def _token(account_id: str) -> str:
    return f"gho_{account_id}"


def _handler(
    owner: str,
    *,
    healthy: bool = True,
    releases_token: bool = True,
    reads: list[tuple[str, httpx.QueryParams]] | None = None,
    github_user: object = None,
    github_status: int = 200,
    bearers: list[str] | None = None,
    label: str | None = None,
) -> Callable[[httpx.Request], httpx.Response]:

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if str(request.url) == GITHUB_USER_URL:
            if bearers is not None:
                bearers.append(request.headers.get("authorization", ""))
            return httpx.Response(github_status, json=github_user)
        if path == "/v1/oauth/token":
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        if request.method == "GET" and "/accounts/" in path:
            account_id = path.rsplit("/", 1)[-1]
            if reads is not None:
                reads.append((account_id, request.url.params))
            record: dict[str, object] = {
                "id": account_id,
                "external_id": owner,
                "healthy": healthy,
                "app": {"name_slug": "github"},
            }
            if label is not None:
                record["name"] = label
            if releases_token:
                record["credentials"] = {"oauth_access_token": _token(account_id)}
            return httpx.Response(200, json={"data": record})
        return httpx.Response(404, json={})

    return handle


def _install(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    client = pipedream.PipedreamClient(
        client_id=f"cid_{uuid4().hex}",
        client_secret="s",
        project_id="proj_test",
        transport=httpx.MockTransport(handler),
    )
    monkeypatch.setattr(pipedream, "pipedream_client", lambda: client)


def _owner(workspace_id: UUID) -> str:
    return pipedream.connection_user_id(workspace_id, "github")


def test_manifest_declares_the_cli_credential_for_github_alone() -> None:
    assert connector_clis((pipedream_manifest.manifest(),)) == {
        "github": CliCredential(
            env="GH_TOKEN",
            header="authorization",
            secret=PipedreamGrantSecret(),
            git=GitWire(
                host="github.com", basic_user="x-access-token", helper="!gh auth git-credential"
            ),
        )
    }
    assert cli_credential(pipedream.CONNECTORS["gmail"]) is None


def test_manifest_tells_the_agent_gh_rides_the_connected_account() -> None:
    manifest = pipedream_manifest.manifest()
    assert [section.name for section in manifest.prompt_sections] == [
        pipedream_manifest.SECTION_NAME
    ]
    body = manifest.prompt_sections[0].body
    assert connector_clis((manifest,))["github"].env in body
    assert "`gh`" in body
    assert "connect_account" in body


async def test_account_token_reads_the_credential_of_a_workspace_owned_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    reads: list[tuple[str, httpx.QueryParams]] = []
    _install(monkeypatch, _handler(_owner(workspace_id), reads=reads))

    token = await pipedream.pipedream_client().account_token(ACCOUNT, workspace_id)

    assert token == _token(ACCOUNT)
    assert [(account, params["include_credentials"]) for account, params in reads] == [
        (ACCOUNT, "true")
    ]


async def test_account_token_refuses_an_account_another_workspace_owns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, _handler(_owner(uuid4())))
    with pytest.raises(pipedream.PipedreamError, match="not owned by workspace") as raised:
        await pipedream.pipedream_client().account_token(ACCOUNT, uuid4())
    assert raised.value.status == 403


async def test_account_token_refuses_an_unhealthy_account_as_a_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    _install(monkeypatch, _handler(_owner(workspace_id), healthy=False))
    with pytest.raises(GrantUnusable, match="reconnect the account") as raised:
        await pipedream.pipedream_client().account_token(ACCOUNT, workspace_id)
    assert not isinstance(raised.value, pipedream.PipedreamError)


async def test_account_token_fails_loud_when_pipedream_releases_no_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An account connected on Pipedream's shared OAuth client answers the read without
    `credentials`. That is a deploy fault, never an empty grant."""
    workspace_id = uuid4()
    _install(monkeypatch, _handler(_owner(workspace_id), releases_token=False))
    with pytest.raises(pipedream.PipedreamError, match="released no token") as raised:
        await pipedream.pipedream_client().account_token(ACCOUNT, workspace_id)
    assert raised.value.status == 502


async def test_grant_secret_reads_each_account_once_within_the_cache_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    reads: list[tuple[str, httpx.QueryParams]] = []
    _install(monkeypatch, _handler(_owner(workspace_id), reads=reads))
    secret = PipedreamGrantSecret()

    first = await secret.secret(workspace_id, ACCOUNT)
    again = await secret.secret(workspace_id, ACCOUNT)
    other = await secret.secret(workspace_id, OTHER_ACCOUNT)

    assert (first, again, other) == (_token(ACCOUNT), _token(ACCOUNT), _token(OTHER_ACCOUNT))
    assert [account for account, _params in reads] == [ACCOUNT, OTHER_ACCOUNT]


GITHUB_SPEC = pipedream.CONNECTORS["github"]
GMAIL_SPEC = pipedream.CONNECTORS["gmail"]
PROFILE = {"id": 12345, "login": "alexg-ufo", "name": "Alex Graveley"}


async def test_account_identity_is_the_accounts_own_noreply_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GitHub links a commit to an account by its author address, and the address that does it
    for every account — email privacy on or off — is `<id>+<login>@users.noreply.github.com`."""
    workspace_id = uuid4()
    bearers: list[str] = []
    _install(monkeypatch, _handler(_owner(workspace_id), github_user=PROFILE, bearers=bearers))

    named = await account_identity(GITHUB_SPEC, ACCOUNT, workspace_id)

    assert named == GithubAccount(
        subject="12345",
        commit=CommitIdentity(
            name="Alex Graveley", email="12345+alexg-ufo@users.noreply.github.com"
        ),
    )
    assert bearers == [f"Bearer {_token(ACCOUNT)}"]


async def test_account_identity_names_the_login_when_the_profile_names_nobody(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A profile with no display name still commits under a name git accepts: its login."""
    workspace_id = uuid4()
    _install(
        monkeypatch,
        _handler(_owner(workspace_id), github_user={"id": 7, "login": "alexg-ufo", "name": None}),
    )

    named = await account_identity(GITHUB_SPEC, ACCOUNT, workspace_id)

    assert named == GithubAccount(
        subject="7",
        commit=CommitIdentity(name="alexg-ufo", email="7+alexg-ufo@users.noreply.github.com"),
    )


async def test_account_identity_raises_when_github_refuses_the_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    _install(monkeypatch, _handler(_owner(workspace_id), github_user={}, github_status=401))

    with pytest.raises(pipedream.PipedreamError, match="refused the identity read") as raised:
        await account_identity(GITHUB_SPEC, ACCOUNT, workspace_id)
    assert raised.value.status == 401


async def test_account_identity_raises_on_an_answer_naming_no_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An identity guessed from a partial answer attributes the member's commits to the wrong
    account or to none, so a missing id or login is a fault rather than a fallback."""
    workspace_id = uuid4()
    _install(monkeypatch, _handler(_owner(workspace_id), github_user={"login": "alexg-ufo"}))

    with pytest.raises(pipedream.PipedreamError, match="named no account") as raised:
        await account_identity(GITHUB_SPEC, ACCOUNT, workspace_id)
    assert raised.value.status == 502


async def test_account_identity_is_none_for_a_connector_that_signs_no_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the connector whose token this deploy holds has an identity to read; every other
    account's token stays with Pipedream, so no provider call is made for it at all."""
    workspace_id = uuid4()
    bearers: list[str] = []
    _install(monkeypatch, _handler(_owner(workspace_id), github_user=PROFILE, bearers=bearers))

    assert await account_identity(GMAIL_SPEC, ACCOUNT, workspace_id) is None
    assert bearers == []


async def test_exchange_records_the_commit_identity_beside_the_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The connect leg is where the identity is read, so the connection holds it before any
    sandbox opens."""
    workspace_id = uuid4()
    _install(
        monkeypatch,
        _handler(_owner(workspace_id), github_user=PROFILE, label="alexg-ufo"),
    )

    account = await PipedreamOAuthProvider("github", "api.github.com", "github").exchange(
        ACCOUNT, "https://ufo.example.com/callback", workspace_id, "github"
    )

    assert account.account_id == ACCOUNT
    assert account.account_label == "alexg-ufo"
    assert account.commit == CommitIdentity(
        name="Alex Graveley", email="12345+alexg-ufo@users.noreply.github.com"
    )
    assert account.identity == "12345"


async def test_exchange_fails_when_the_commit_identity_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A GitHub connection that cannot commit is not usable, so the consent leg refuses rather
    than record it — the member connects again."""
    workspace_id = uuid4()
    _install(
        monkeypatch,
        _handler(_owner(workspace_id), github_user={}, github_status=503, label="alexg-ufo"),
    )

    with pytest.raises(pipedream.PipedreamError, match="refused the identity read"):
        await PipedreamOAuthProvider("github", "api.github.com", "github").exchange(
            ACCOUNT, "https://ufo.example.com/callback", workspace_id, "github"
        )


async def test_account_identity_falls_back_to_the_login_for_a_name_git_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    _install(
        monkeypatch,
        _handler(
            _owner(workspace_id),
            github_user={"id": 12345, "login": "alexg-ufo", "name": "<<>>"},
        ),
    )

    named = await account_identity(GITHUB_SPEC, ACCOUNT, workspace_id)

    assert named == GithubAccount(
        subject="12345",
        commit=CommitIdentity(name="alexg-ufo", email="12345+alexg-ufo@users.noreply.github.com"),
    )

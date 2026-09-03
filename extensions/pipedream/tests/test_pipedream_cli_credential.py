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
from ufo_ext_pipedream.token import PipedreamGrantSecret, cli_credential

from ufo.host.ext.loader import connector_clis
from ufo.sdk.connectors import CliCredential, GitWire, GrantUnusable

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
) -> Callable[[httpx.Request], httpx.Response]:
    """A Pipedream mock: the OAuth token grant and the account read, which reports `owner` as every
    account's `external_id`, carries the account's token under `credentials` only where the account
    was connected on the deploy's own client (`releases_token`), and records each account id read
    with its query params into `reads`."""

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
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
    """`gh` reads `GH_TOKEN`, git smart-HTTP takes the same token as the password half of
    `x-access-token:<token>` on `github.com`, and `gh auth git-credential` is the helper that
    answers git's prompt from that variable. Every other connector's token stays with Pipedream."""
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
    """The project token can read any account in the project, so ownership is asserted from the
    record's `external_id` before the token it carries is returned — a foreign account id yields
    nothing."""
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

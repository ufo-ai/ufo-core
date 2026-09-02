"""The Flagship flag backend's proof: the deploy's three keys become one HTTP provider, or none.

No Cloudflare account is reachable here, so what is asserted is everything decided before a request:
the backend name and deploy keys core selects on, the evaluate endpoint the three keys compose, the
one-second no-retry bound a flag read holds, the response-cache window `[flags] cache_ttl_seconds`
sets, and the unkeyed answer — no provider, so every flag resolves to its call site's default. The
vendor provider publishes none of that, so the endpoint and bounds are read off the client it built.
"""

import logging
from collections.abc import Callable
from dataclasses import replace

import httpx
import pytest
import ufo_ext_flagship as flagship
from flagship import FlagshipServerProvider

from ufo.flags import FLAG_TIMEOUT_SECONDS

APP_ID = "flagship-app-7"
ACCOUNT_ID = "cf-account-42"
AUTH_TOKEN = "cf-live-secret-0xdeadbeef"
CACHE_TTL_SECONDS = 30.0


@pytest.fixture(autouse=True)
def unkeyed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every case states its own keys, so a deploy env leaking in cannot decide a test."""
    for name in (flagship.APP_ID_ENV, flagship.ACCOUNT_ID_ENV, flagship.AUTH_TOKEN_ENV):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"UFO_{name}", raising=False)


def _keyed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(flagship.APP_ID_ENV, APP_ID)
    monkeypatch.setenv(flagship.ACCOUNT_ID_ENV, ACCOUNT_ID)
    monkeypatch.setenv(flagship.AUTH_TOKEN_ENV, AUTH_TOKEN)


def test_manifest_registers_the_flagship_backend_and_its_three_deploy_keys() -> None:
    manifest = flagship.manifest()
    assert manifest.name == "flagship"
    assert manifest.deploy_keys == (
        "CLOUDFLARE_FLAGSHIP_APP_ID",
        "CLOUDFLARE_ACCOUNT_ID",
        "CLOUDFLARE_FLAGSHIP_TOKEN",
    )
    (spec,) = manifest.flag_providers
    assert spec.backend == "flagship"
    assert spec.build is flagship.build
    assert manifest.credentials == ()


def test_the_keyed_deploy_gets_a_provider_aimed_at_its_own_flagship_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The three keys are the whole configuration: two of them compose the evaluate URL and the
    third authorizes it, so a keyed deploy reads its own app and no other."""
    _keyed(monkeypatch)
    provider = flagship.build(CACHE_TTL_SECONDS)
    assert isinstance(provider, FlagshipServerProvider)
    client = provider._client
    assert client.endpoint == (
        f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}"
        f"/flagship/apps/{APP_ID}/evaluate"
    )
    assert client.timeout == flagship.REQUEST_TIMEOUT_SECONDS
    assert client.retries == flagship.REQUEST_RETRIES
    assert provider._cache.ttl == CACHE_TTL_SECONDS


def test_the_request_bound_stays_inside_the_ceiling_a_flag_read_holds() -> None:
    """A flag read waits `ufo.flags.FLAG_TIMEOUT_SECONDS` at most, so one unanswered request plus
    its retries must finish inside that or the ceiling is what cuts the read off."""
    attempts = flagship.REQUEST_RETRIES + 1
    assert flagship.REQUEST_TIMEOUT_SECONDS * attempts < FLAG_TIMEOUT_SECONDS


@pytest.mark.parametrize(
    "unseeded",
    ["CLOUDFLARE_FLAGSHIP_APP_ID", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_FLAGSHIP_TOKEN"],
)
def test_a_key_the_deploy_projects_empty_reads_as_unkeyed(
    monkeypatch: pytest.MonkeyPatch, unseeded: str
) -> None:
    """The hosted deploy projects all three keys by name whether or not an operator seeded them, so
    the shape a deploy carrying no Flagship app actually presents is an empty value, not an absent
    one. It answers like the absent key: no provider, every flag on its code default."""
    _keyed(monkeypatch)
    monkeypatch.setenv(unseeded, "")
    assert flagship.build(CACHE_TTL_SECONDS) is None


@pytest.mark.parametrize(
    "missing",
    ["CLOUDFLARE_FLAGSHIP_APP_ID", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_FLAGSHIP_TOKEN"],
)
def test_a_deploy_missing_any_one_key_gets_no_provider(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, missing: str
) -> None:
    """Boot does not fail on a missing key — flags resolve to their code defaults instead — so the
    warning naming which keys are set is the operator's only signal."""
    _keyed(monkeypatch)
    monkeypatch.delenv(missing, raising=False)
    with caplog.at_level(logging.WARNING, logger="ufo"):
        assert flagship.build(CACHE_TTL_SECONDS) is None
    record = caplog.records[-1]
    assert record.message == "flagship.unkeyed"
    assert record.ufo == {
        "app_id_set": missing != flagship.APP_ID_ENV,
        "account_id_set": missing != flagship.ACCOUNT_ID_ENV,
        "auth_token_set": missing != flagship.AUTH_TOKEN_ENV,
    }


WRITE_TOKEN = "cf-write-secret-0xfeedface"
FLAGS_URL = (
    f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/flagship/apps/{APP_ID}/flags"
)
HELD_FLAG = {
    "key": "enable-wiki-app",
    "flag_key": "enable-wiki-app",
    "type": "boolean",
    "description": "written by whoever made this flag",
    "enabled": True,
    "default_variation": "on",
    "variations": {"on": "true", "off": "false"},
    "rules": [{"priority": 1, "serve_variation": "on"}],
}
ANSWERED_FLAG = {**HELD_FLAG, "updated_at": "2026-08-27T22:00:00Z", "updated_by": "someone"}
# One entry of the app's flag collection, as Cloudflare answers it: the collection names a flag
# `key` and carries no `flag_key`, which the single-flag read above does.
LISTED_FLAG = {
    "key": "enable-wiki-app",
    "type": "string",
    "default_variation": "on",
    "variations": {"on": "true", "off": "false"},
    "rules": [],
    "description": None,
    "enabled": True,
    "updated_at": "2026-09-02T21:55:43.468Z",
    "updated_by": "unknown",
}


def _admin(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> flagship.FlagshipAdmin:
    """The write client, keyed, answering in this process through the vendor's own stand-in
    transport — so a case reads the request this code composed, not a log of its own."""
    _keyed(monkeypatch)
    monkeypatch.setenv(flagship.WRITE_TOKEN_ENV, WRITE_TOKEN)
    return replace(
        flagship.build_admin(), client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_serving_a_variation_the_flag_does_not_hold_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flag someone built with other variations is not one this verb can state on and off for, and
    saying so beats a Cloudflare 400 an operator has to decode."""
    other = {**ANSWERED_FLAG, "variations": {"beta": "true", "control": "false"}}
    admin = _admin(
        monkeypatch, lambda request: httpx.Response(200, json={"success": True, "result": other})
    )
    with pytest.raises(RuntimeError, match="no 'off' variation"):
        admin.serve("enable-wiki-app", on=False)


def test_a_refusal_carries_what_cloudflare_said(monkeypatch: pytest.MonkeyPatch) -> None:
    """The operator ran one verb and is owed the reason it wrote nothing."""
    admin = _admin(
        monkeypatch,
        lambda request: httpx.Response(
            403, json={"success": False, "errors": [{"message": "Authentication error"}]}
        ),
    )
    with pytest.raises(RuntimeError, match="Authentication error"):
        admin.serve("enable-wiki-app", on=True)


def test_the_listing_reads_the_app_collection_and_sorts_what_it_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sweep asks the service what it holds rather than the repo what it declares, so the
    request is the app's own flag collection and the answer is every key on it — a key nobody
    declared included, which is the whole reason to ask."""
    asked: list[tuple[str, str]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append((request.method, str(request.url)))
        return httpx.Response(
            200,
            json={
                "success": True,
                "result": [
                    {**LISTED_FLAG, "key": "enable-wiki-app"},
                    {**LISTED_FLAG, "key": "enable-code-app"},
                ],
            },
        )

    admin = _admin(monkeypatch, answer)

    assert admin.list() == ("enable-code-app", "enable-wiki-app")
    assert asked == [
        (
            "GET",
            f"{flagship.API_BASE_URL}/accounts/{ACCOUNT_ID}/flagship/apps/{APP_ID}/flags",
        )
    ]


def test_a_listing_the_account_refuses_is_not_read_as_an_empty_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty answer and a refused one differ by everything: read a refusal as an empty app and
    the sweep concludes every declared flag is missing and every tombstone is finished."""
    admin = _admin(
        monkeypatch,
        lambda request: httpx.Response(
            403, json={"success": False, "errors": [{"message": "Authentication error"}]}
        ),
    )
    with pytest.raises(RuntimeError, match="Authentication error"):
        admin.list()


def test_a_success_carrying_no_list_is_refused_rather_than_read_as_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = _admin(
        monkeypatch,
        lambda request: httpx.Response(200, json={"success": True, "result": None}),
    )
    with pytest.raises(RuntimeError, match="no readable flag list"):
        admin.list()

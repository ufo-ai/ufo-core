"""The Flagship flag backend's proof: the deploy's three keys become one HTTP provider, or none.

No Cloudflare account is reachable here, so what is asserted is everything decided before a request:
the backend name and deploy keys core selects on, the evaluate endpoint the three keys compose, the
one-second no-retry bound a flag read holds, the response-cache window `[flags] cache_ttl_seconds`
sets, and the unkeyed answer — no provider, so every flag resolves to its call site's default. The
vendor provider publishes none of that, so the endpoint and bounds are read off the client it built.
"""

import json
import logging
from collections.abc import Callable
from dataclasses import replace

import httpx
import pytest
import ufo_ext_flagship as flagship
from flagship import FlagshipServerProvider

from ufo.flags import FLAG_TIMEOUT_SECONDS
from ufo.sdk.manifest import FlagState

APP_ID = "flagship-app-7"
ACCOUNT_ID = "cf-account-42"
AUTH_TOKEN = "cf-live-secret-0xdeadbeef"
ADMIN_TOKEN = "cf-admin-secret-0xfeedface"
CACHE_TTL_SECONDS = 30.0
FLAGS_URL = (
    f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/flagship/apps/{APP_ID}/flags"
)


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


def test_a_zero_cache_window_evaluates_every_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """`cache_ttl_seconds = 0` is the operator asking for no caching at all, which the vendor
    provider spells as no cache rather than a zero-length one."""
    _keyed(monkeypatch)
    provider = flagship.build(0.0)
    assert isinstance(provider, FlagshipServerProvider)
    assert provider._cache is None


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


def _admin(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> flagship.FlagshipAdmin:
    """The administration client, keyed, answering in this process through the vendor's own
    stand-in transport — so a case reads the request this code composed, not a log of its own."""
    _keyed(monkeypatch)
    monkeypatch.setenv(flagship.ADMIN_TOKEN_ENV, ADMIN_TOKEN)
    built = flagship.build_admin()
    assert isinstance(built, flagship.FlagshipAdmin)
    return replace(built, client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_administration_reads_a_write_token_of_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    """Serve's token may evaluate and nothing else, so the write verb reads its own key and names it
    rather than failing at Cloudflare with a permission error an operator has to decode."""
    _keyed(monkeypatch)
    monkeypatch.delenv(flagship.ADMIN_TOKEN_ENV, raising=False)
    with pytest.raises(RuntimeError, match=flagship.ADMIN_TOKEN_ENV):
        flagship.build_admin()


def test_a_written_flag_carries_its_state_as_the_default_variation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On and off are the variation the app serves, not the `enabled` switch: with no targeting
    rules `default_variation` answers every evaluation, so it is the whole of what a flag says here.
    `enabled` stays true, which is what leaves a rule someone adds in the dashboard able to matter.
    """
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"success": True, "result": {}})

    admin = _admin(monkeypatch, handler)
    admin.create("enable-wiki-app", on=True)

    assert [(request.method, str(request.url)) for request in sent] == [("POST", FLAGS_URL)]
    assert json.loads(sent[0].content) == {
        "key": "enable-wiki-app",
        "enabled": True,
        "default_variation": "on",
        "variations": {"on": True, "off": False},
        "rules": [],
    }
    assert sent[0].headers["authorization"] == f"Bearer {ADMIN_TOKEN}"


def test_setting_a_flag_carries_back_every_rule_the_app_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The API's `PUT` takes the whole flag, so a body built from scratch would delete the targeting
    rules that are how a feature reaches one workspace at a time — a rollout someone built, that
    nothing here could restore and no listing would show had gone. The write reads the flag first
    and changes one field of what it read."""
    rule = {"priority": 1, "variation": "on", "condition": {"attribute": "targetingKey"}}
    held = {
        "key": "enable-wiki-app",
        "enabled": True,
        "default_variation": "on",
        "variations": {"on": True, "off": False},
        "rules": [rule],
    }
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"success": True, "result": held})

    admin = _admin(monkeypatch, handler)
    admin.set("enable-wiki-app", on=False)

    assert [(request.method, str(request.url)) for request in sent] == [
        ("GET", f"{FLAGS_URL}/enable-wiki-app"),
        ("PUT", f"{FLAGS_URL}/enable-wiki-app"),
    ]
    assert json.loads(sent[1].content) == {**held, "default_variation": "off"}


def test_setting_a_flag_that_serves_no_such_variation_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flag someone built with other variations is not one this verb can state on and off for, and
    saying so beats a Cloudflare 400 an operator has to decode."""
    admin = _admin(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json={
                "success": True,
                "result": {
                    "key": "enable-wiki-app",
                    "enabled": True,
                    "default_variation": "beta",
                    "variations": {"beta": True, "control": False},
                    "rules": [],
                },
            },
        ),
    )
    with pytest.raises(RuntimeError, match="no 'off' variation"):
        admin.set("enable-wiki-app", on=False)


def test_the_listing_reads_every_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cloudflare pages this listing. A deploy whose app holds more flags than one page would
    otherwise read the rest as missing, and `ufoctl flags sync` would try to create them again —
    which the API refuses, so the verb would fail at the first flag past the page."""
    pages = [
        {
            "success": True,
            "result": [{"key": "enable-wiki-app", "default_variation": "off", "rules": []}],
            "result_info": {"after": "cursor-2"},
        },
        {
            "success": True,
            "result": [
                {
                    "key": "enable-memory-tab",
                    "default_variation": "on",
                    "rules": [{"priority": 1, "variation": "off"}],
                }
            ],
            "result_info": {"after": None},
        },
    ]
    admin = _admin(monkeypatch, lambda request: httpx.Response(200, json=pages.pop(0)))

    assert admin.listing() == (
        FlagState(key="enable-wiki-app", on=False, targeted=False),
        FlagState(key="enable-memory-tab", on=True, targeted=True),
    )


def test_a_refusal_carries_what_cloudflare_said(monkeypatch: pytest.MonkeyPatch) -> None:
    """The operator ran one verb and is owed the reason it wrote nothing."""
    admin = _admin(
        monkeypatch,
        lambda request: httpx.Response(
            403, json={"success": False, "errors": [{"message": "Authentication error"}]}
        ),
    )
    with pytest.raises(RuntimeError, match="Authentication error"):
        admin.create("enable-wiki-app", on=True)

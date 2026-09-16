"""The one seam core reaches a member by email through, held against the contract the gateway
reads it by. `servers/control/tests/contract.rs` deserializes the same fixture into the route's own
types, so a field renamed on either side goes red on both.

The transport stands in for the gateway; what is asserted is the request this composes and the
answer it reads back, never the stand-in."""

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from ufo.runtime.email import (
    CONTROL_EMAIL_TOKEN_ENV,
    CONTROL_EMAIL_URL_ENV,
    PREFERENCE_PATH,
    PRODUCT_NEWS,
    SEND_PATH,
    TRANSACTIONAL,
    EmailRefused,
    EmailSends,
    EmailUnanswered,
    email_sends_from_env,
)

CONTRACT = json.loads(
    (
        Path(__file__).parents[3] / "servers" / "control" / "tests" / "email_send_contract.json"
    ).read_text()
)
BASE = "http://ufo-gateway"
TOKEN = "onboard-control-token"

Handler = Callable[[httpx.Request], object]


def seam(handler: Handler) -> EmailSends:
    return EmailSends(base_url=BASE, token=TOKEN, transport=httpx.MockTransport(handler))


async def send_the_contract(sends: EmailSends) -> str:
    asked = CONTRACT["send_request"]
    return await sends.send(
        address=asked["email"],
        kind=asked["kind"],
        topic=asked["topic"],
        subject=asked["subject"],
        body=asked["body"],
        action_label=asked["action_label"],
        action_url=asked["action_url"],
    )


async def test_a_send_posts_the_contract_body_and_reads_the_message_id() -> None:
    seen: list[httpx.Request] = []

    def gateway(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=CONTRACT["send_response"])

    message_id = await send_the_contract(seam(gateway))

    assert message_id == CONTRACT["send_response"]["message_id"]
    (request,) = seen
    assert request.method == "POST"
    assert request.url.path == CONTRACT["send_path"] == SEND_PATH
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert json.loads(request.content) == CONTRACT["send_request"]


async def test_a_delivery_reads_back_under_the_send_it_names() -> None:
    answers = [CONTRACT["unreported_response"], CONTRACT["reported_response"]]
    seen: list[httpx.Request] = []

    def gateway(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=answers[len(seen) - 1])

    sends = seam(gateway)
    message_id = CONTRACT["send_response"]["message_id"]

    assert await sends.delivery(message_id) is None, "silence is not yet, never failed"
    assert await sends.delivery(message_id) == "delivered"
    assert [request.url.path for request in seen] == [CONTRACT["delivery_path"]] * 2
    assert {request.method for request in seen} == {"GET"}


async def test_a_refusal_carries_the_gateways_own_sentence() -> None:
    def gateway(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "member@acme.com has bounced"})

    with pytest.raises(EmailRefused, match="has bounced"):
        await send_the_contract(seam(gateway))


async def test_an_unreachable_gateway_is_unanswered_not_refused() -> None:
    """The two failures mean opposite things to a caller. A refusal is decided, and repeating it
    changes nothing; an unanswered send may have reached SES, so nothing may repeat it."""

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(EmailUnanswered):
        await send_the_contract(seam(unreachable))

    def faulted(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "unavailable"})

    with pytest.raises(EmailUnanswered):
        await send_the_contract(seam(faulted))


def test_a_deploy_with_no_control_service_wires_no_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CONTROL_EMAIL_URL_ENV, raising=False)
    monkeypatch.setenv(CONTROL_EMAIL_TOKEN_ENV, TOKEN)
    assert email_sends_from_env() is None

    monkeypatch.setenv(CONTROL_EMAIL_URL_ENV, BASE)
    assert email_sends_from_env() == EmailSends(base_url=BASE, token=TOKEN)

    monkeypatch.delenv(CONTROL_EMAIL_TOKEN_ENV, raising=False)
    with pytest.raises(RuntimeError, match=CONTROL_EMAIL_TOKEN_ENV):
        email_sends_from_env()


async def test_a_preference_posts_the_contract_body_under_its_own_path() -> None:
    """A member's own address and the one topic they may silence, carried to the place suppression
    is applied rather than kept beside the extension that heard it."""
    seen: list[httpx.Request] = []

    def gateway(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=CONTRACT["preference_response"])

    asked = CONTRACT["preference_request"]
    await seam(gateway).silence(asked["email"], asked["topic"], silenced=asked["silenced"])

    (request,) = seen
    assert request.method == "POST"
    assert request.url.path == CONTRACT["preference_path"] == PREFERENCE_PATH
    assert json.loads(request.content) == asked
    assert asked["topic"] == PRODUCT_NEWS
    assert asked["topic"] != TRANSACTIONAL, "the one topic a member cannot silence"


async def test_preferences_read_the_whole_silenceable_set_under_the_address() -> None:
    seen: list[httpx.Request] = []

    def gateway(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=CONTRACT["preferences_response"])

    held = await seam(gateway).preferences("member@acme.com")

    assert held == {"product_news": True, "founder_updates": False}
    (request,) = seen
    assert request.method == "GET"
    assert request.url.path == CONTRACT["preferences_path"].replace("%40", "@"), (
        "httpx unquotes the path it dials; the contract holds the encoded form the route matches"
    )

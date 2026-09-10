"""The Slack surface's shared 429 ladder: what a rate-limited call waits, when it gives up, which
calls take the ladder, and which answer at once because Slack's event ack or a member's tool call is
waiting on them."""

import httpx
import pytest
import ufo_ext_slack.surface as slack

REAL_ASYNC_CLIENT = httpx.AsyncClient
USERS_INFO_OK = {"ok": True, "user": {"id": "U1", "is_email_confirmed": True}}
RATE_LIMITED = {"ok": False, "error": "ratelimited"}
LIMITED_UNSTATED: dict[str, object] = {"status_code": 429, "json": RATE_LIMITED}
LIMITED_FOR_5: dict[str, object] = {
    "status_code": 429,
    "headers": {"Retry-After": "5"},
    "json": RATE_LIMITED,
}
OAUTH_OK = {
    "ok": True,
    "access_token": "xoxb-1",
    "team": {"id": "T0BG8632NDS"},
    "bot_user_id": "U0BG8632NDS",
    "app_id": "A0BG8632NDS",
}


def _replies(
    specs: list[dict[str, object]],
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    """Slack's answers in order, the last one repeating for every further attempt. Each is built
    per request, because one `httpx.Response` cannot be read twice."""
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(**specs[min(len(sent) - 1, len(specs) - 1)])

    return httpx.MockTransport(handler), sent


def _recorded_sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(slack.asyncio, "sleep", sleep)
    return slept


def _patch_httpx(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    def factory(**kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return REAL_ASYNC_CLIENT(transport=transport, **kwargs)

    monkeypatch.setattr(slack.httpx, "AsyncClient", factory)


async def test_a_rate_limited_call_waits_the_retry_after_slack_stated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every Slack call goes through the ladder, not just the reply post: a 429 on `users.info` is
    waited out in process and the caller gets the answer it asked for."""
    slept = _recorded_sleeps(monkeypatch)
    transport, sent = _replies([LIMITED_FOR_5, {"status_code": 200, "json": USERS_INFO_OK}])

    async with REAL_ASYNC_CLIENT(transport=transport) as client:
        payload = await slack._slack_ok(lambda: client.get(slack.SLACK_USERS_INFO_URL))

    assert payload == USERS_INFO_OK
    assert len(sent) == 2
    assert 5.0 <= slept[0] <= 7.5


async def test_a_rate_limit_with_no_stated_wait_falls_back_to_the_backoff_ladder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slack states nothing, so the wait is the doubling ladder from one second, jittered up to
    half again so calls sharing one quota do not all come back in the same instant."""
    slept = _recorded_sleeps(monkeypatch)
    transport, sent = _replies(
        [LIMITED_UNSTATED, LIMITED_UNSTATED, {"status_code": 200, "json": USERS_INFO_OK}]
    )

    async with REAL_ASYNC_CLIENT(transport=transport) as client:
        payload = await slack._slack_ok(lambda: client.get(slack.SLACK_USERS_INFO_URL))

    assert payload == USERS_INFO_OK
    assert len(sent) == 3
    assert 1.0 <= slept[0] <= 1.5
    assert 2.0 <= slept[1] <= 3.0


async def test_an_unreadable_retry_after_falls_back_to_the_backoff_ladder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A float, an HTTP date, or a digit run too long to be a real delay is no statement."""
    slept = _recorded_sleeps(monkeypatch)
    transport, _ = _replies(
        [
            {"status_code": 429, "headers": {"Retry-After": "1.5"}, "json": RATE_LIMITED},
            {"status_code": 429, "headers": {"Retry-After": "9" * 4300}, "json": RATE_LIMITED},
            {"status_code": 200, "json": USERS_INFO_OK},
        ]
    )

    async with REAL_ASYNC_CLIENT(transport=transport) as client:
        await slack._slack_ok(lambda: client.get(slack.SLACK_USERS_INFO_URL))

    assert 1.0 <= slept[0] <= 1.5
    assert 2.0 <= slept[1] <= 3.0


async def test_a_call_slack_never_stops_limiting_raises_after_its_attempt_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slept = _recorded_sleeps(monkeypatch)
    transport, sent = _replies([LIMITED_UNSTATED])

    async with REAL_ASYNC_CLIENT(transport=transport) as client:
        with pytest.raises(slack.SlackRateLimitedError, match=r"users\.info"):
            await slack._slack_ok(lambda: client.get(slack.SLACK_USERS_INFO_URL))

    assert len(sent) == slack.SLACK_RETRY_MAX_ATTEMPTS
    assert len(slept) == slack.SLACK_RETRY_MAX_ATTEMPTS - 1


async def test_the_ladder_stops_at_its_wait_budget_before_its_attempt_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A minute stated every time spends the whole budget in two waits, so the third 429 raises
    rather than holding the call for another minute."""
    slept = _recorded_sleeps(monkeypatch)
    transport, sent = _replies(
        [{"status_code": 429, "headers": {"Retry-After": "60"}, "json": RATE_LIMITED}]
    )

    async with REAL_ASYNC_CLIENT(transport=transport) as client:
        with pytest.raises(slack.SlackRateLimitedError):
            await slack._slack_ok(lambda: client.get(slack.SLACK_USERS_INFO_URL))

    assert len(sent) == 3
    assert sum(slept) == slack.SLACK_RETRY_BUDGET_SECONDS


async def test_a_read_the_event_ack_waits_on_takes_one_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ingest` and `interactive` await `_slack_user`, so a 429 there answers None at once. Sleeping
    the ladder would hold the route past Slack's three-second ack and earn a redelivery."""
    slept = _recorded_sleeps(monkeypatch)
    transport, sent = _replies([LIMITED_FOR_5])
    _patch_httpx(monkeypatch, transport)

    assert await slack._slack_user("xoxb-1", "U1") is None
    assert len(sent) == 1
    assert slept == []


async def test_a_channel_read_the_event_ack_waits_on_takes_one_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slept = _recorded_sleeps(monkeypatch)
    transport, sent = _replies([LIMITED_UNSTATED])
    _patch_httpx(monkeypatch, transport)

    assert await slack._channel_info("xoxb-1", "C1") is None
    assert len(sent) == 1
    assert slept == []


async def test_the_conversation_search_list_takes_one_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`slack_channels` runs the whole search inside a member's tool call — one read per list page
    and one per group DM — so a rate-limited read raises at once. A ladder per read would hold that
    tool call, and the member's turn behind it, for hours."""
    slept = _recorded_sleeps(monkeypatch)
    transport, sent = _replies([LIMITED_FOR_5])
    _patch_httpx(monkeypatch, transport)

    with pytest.raises(slack.SlackRateLimitedError, match=r"conversations\.list"):
        await slack.SlackConversationSearch(bot_token="xoxb-1", bot_user_id="U0", query="").run()

    assert len(sent) == 1
    assert slept == []


async def test_the_conversation_search_members_read_takes_one_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The members read is the one the search makes up to SLACK_PEOPLE_RESOLVE_MAX times in
    sequence, so it is where a ladder each would add up: the first 429 raises instead."""
    slept = _recorded_sleeps(monkeypatch)
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        if request.url.path.endswith("conversations.list"):
            return httpx.Response(
                200, json={"ok": True, "channels": [{"id": "G1", "is_mpim": True}]}
            )
        return httpx.Response(**LIMITED_UNSTATED)

    _patch_httpx(monkeypatch, httpx.MockTransport(handler))

    with pytest.raises(slack.SlackRateLimitedError, match=r"conversations\.members"):
        await slack.SlackConversationSearch(bot_token="xoxb-1", bot_user_id="U0", query="").run()

    assert [request.url.path for request in sent] == [
        "/api/conversations.list",
        "/api/conversations.members",
    ]
    assert slept == []


async def test_a_rate_limited_oauth_exchange_waits_and_installs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The install exchange takes the same ladder as every other call: an onboarding owner who hits
    a 429 on connect waits out the stated delay and lands installed."""
    monkeypatch.setenv(slack.SLACK_CLIENT_ID_ENV, "1.1")
    monkeypatch.setenv(slack.SLACK_CLIENT_SECRET_ENV, "secret")
    slept = _recorded_sleeps(monkeypatch)
    transport, sent = _replies([LIMITED_FOR_5, {"status_code": 200, "json": OAUTH_OK}])
    _patch_httpx(monkeypatch, transport)

    install = await slack.slack_oauth_exchange("code", "https://ufo.test/slack/callback")

    assert install.bot_token == "xoxb-1"
    assert len(sent) == 2
    assert 5.0 <= slept[0] <= 7.5


async def test_an_oauth_exchange_slack_never_stops_limiting_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(slack.SLACK_CLIENT_ID_ENV, "1.1")
    monkeypatch.setenv(slack.SLACK_CLIENT_SECRET_ENV, "secret")
    _recorded_sleeps(monkeypatch)
    transport, sent = _replies([LIMITED_UNSTATED])
    _patch_httpx(monkeypatch, transport)

    with pytest.raises(slack.SlackRateLimitedError, match=r"oauth\.v2\.access"):
        await slack.slack_oauth_exchange("code", "https://ufo.test/slack/callback")

    assert len(sent) == slack.SLACK_RETRY_MAX_ATTEMPTS


async def test_an_oauth_exchange_slack_answers_still_installs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(slack.SLACK_CLIENT_ID_ENV, "1.1")
    monkeypatch.setenv(slack.SLACK_CLIENT_SECRET_ENV, "secret")
    transport, sent = _replies([{"status_code": 200, "json": OAUTH_OK}])
    _patch_httpx(monkeypatch, transport)

    install = await slack.slack_oauth_exchange("code", "https://ufo.test/slack/callback")

    assert install.bot_token == "xoxb-1"
    assert install.team_id == "T0BG8632NDS"
    assert len(sent) == 1

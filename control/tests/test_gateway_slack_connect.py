"""The signup Slack Connect delivery against a real Postgres and a real HTTP Slack stand-in.

The stand-in is a local server, not a fake client: every proof drives the production
`SlackConnectClient` — its URLs, its bounded payloads, its bearer header, and its transient/terminal
classifier — and asserts the durable row the workflow left behind. The one thing the stand-in is
asserted on is the calls it did *not* receive, because "never a blind duplicate invitation" is
exactly a claim about calls."""

import asyncio
import socket
import subprocess
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import asyncpg
import pytest
import uvicorn
from click.testing import CliRunner
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, Response
from uvicorn._types import ASGIApplication, ASGIReceiveCallable, ASGISendCallable, Scope

from ufo_control import gateway_slack_connect
from ufo_control.gateway_slack_connect import (
    BOT_TOKEN_ENV,
    ENABLED_ENV,
    ERROR_CHARS,
    MAX_ATTEMPTS,
    MAX_RETRY_AFTER_SECONDS,
    REDACTION,
    RETRY_BACKOFF_SECONDS,
    TABLE,
    TEAM_ID_ENV,
    SlackConnectClient,
    SlackConnectInviter,
    SlackTransientError,
    rearm_failed_delivery,
    slack_connect_from_env,
)
from ufo_control.gateway_store import OnboardStore
from ufo_control.main import main

REPO = Path(__file__).parents[2]

TEAM_ID = "T0PERATOR"
APEX_HOST = "flyingobject.ai"
BOT_TOKEN = "xoxb-operator-token"
CHANNEL_ID = "C0CUSTOMER"
INVITE_ID = "I0INVITE"
SERVER_START_TICKS = 500
TICK_SECONDS = 0.01
SERVER_STOP_GRACE_SECONDS = 5
SERVER_STOP_WAIT_SECONDS = 20.0
STOP_FLOOR_SECONDS = 3.0
STOP_DEADLINE_SECONDS = 8.0
REGISTERED_WAIT_SECONDS = 5.0

SlackHandler = Callable[[dict[str, str]], Awaitable[tuple[int, Any]]]


def responds(payload: Any, status: int = 200) -> SlackHandler:
    async def handler(form: dict[str, str]) -> tuple[int, Any]:
        return status, payload

    return handler


def _default_handlers() -> dict[str, SlackHandler]:
    return {
        "auth.test": responds({"ok": True, "team_id": TEAM_ID}),
        "conversations.create": responds({"ok": True, "channel": {"id": CHANNEL_ID}}),
        "conversations.inviteShared": responds({"ok": True, "invite_id": INVITE_ID}),
        "chat.postMessage": responds({"ok": True, "ts": "1700000000.000100"}),
    }


@dataclass
class SlackStub:
    """A local Slack Web API: one form-encoded POST per method, scripted per test."""

    handlers: dict[str, SlackHandler] = field(default_factory=_default_handlers)
    headers: dict[str, dict[str, str]] = field(default_factory=dict)
    calls: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    authorizations: set[str] = field(default_factory=set)

    def app(self) -> FastAPI:
        app = FastAPI()

        @app.post("/{method}")
        async def method_call(method: str, request: Request) -> Response:
            form = {key: str(value) for key, value in (await request.form()).items()}
            self.calls.append((method, form))
            self.authorizations.add(request.headers.get("authorization", ""))
            handler = self.handlers.get(method)
            if handler is None:
                return JSONResponse({"ok": False, "error": "unknown_method"})
            status, payload = await handler(form)
            return JSONResponse(payload, status_code=status, headers=self.headers.get(method))

        return app

    def forms(self, method: str) -> list[dict[str, str]]:
        return [form for name, form in self.calls if name == method]


@asynccontextmanager
async def _serving(
    app: ASGIApplication | Callable[..., Any] | str,
) -> AsyncIterator[uvicorn.Server]:
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=0,
            log_config=None,
            lifespan="off",
            timeout_graceful_shutdown=SERVER_STOP_GRACE_SECONDS,
        )
    )
    serving = asyncio.create_task(server.serve())
    for _ in range(SERVER_START_TICKS):
        if server.started:
            break
        await asyncio.sleep(TICK_SECONDS)
    assert server.started, "the Slack stand-in never bound a port"
    try:
        yield server
    finally:
        server.should_exit = True
        await asyncio.wait_for(serving, SERVER_STOP_WAIT_SECONDS)


@pytest.fixture
async def slack(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[SlackStub]:
    """`log_config=None` for the same reason `ufo-control gateway` passes it: uvicorn's default
    dictConfig stops `uvicorn.error` from propagating to the root logger, process-wide."""
    stub = SlackStub()
    async with _serving(stub.app()) as server:
        port = server.servers[0].sockets[0].getsockname()[1]
        monkeypatch.setattr(gateway_slack_connect, "SLACK_API_BASE", f"http://127.0.0.1:{port}")
        yield stub


async def test_a_connection_still_open_at_stop_does_not_hold_the_stand_in() -> None:
    entered = asyncio.Event()

    async def never_answers(
        scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:
        entered.set()
        await asyncio.Event().wait()

    loop = asyncio.get_running_loop()
    held = socket.socket()
    try:
        async with _serving(never_answers) as server:
            held.connect(("127.0.0.1", server.servers[0].sockets[0].getsockname()[1]))
            held.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
            await asyncio.wait_for(entered.wait(), REGISTERED_WAIT_SECONDS)
            started = loop.time()
        waited = loop.time() - started
    finally:
        held.close()
    assert waited >= SERVER_STOP_GRACE_SECONDS, waited
    assert STOP_FLOOR_SECONDS <= waited < STOP_DEADLINE_SECONDS, waited


def _inviter(pool: asyncpg.Pool, worker_id: str = "worker-a") -> SlackConnectInviter:
    return SlackConnectInviter(
        pool=pool,
        slack=SlackConnectClient(bot_token=BOT_TOKEN),
        team_id=TEAM_ID,
        apex_host=APEX_HOST,
        worker_id=worker_id,
        poll_interval=TICK_SECONDS,
    )


async def _granted_domain(
    pool: asyncpg.Pool,
    email: str,
    *,
    consumed: bool = False,
    age: timedelta = timedelta(),
) -> str:
    """One `ufo-control invite` grant, seeded as the verb mints it. The object number comes off the
    ledger's own maximum, so a test may grant several domains without colliding on the live-object
    index."""
    domain = email.split("@")[1]
    await pool.execute(
        "insert into ufo_control.invite_code"
        "  (id, object_number, email, email_domain, expires_at, consumed_at, created_at)"
        " select $1, coalesce(max(object_number), 0) + 1, $2, $3,"
        "   now() + interval '14 days', $4, now() - $5::interval"
        " from ufo_control.invite_code",
        uuid4(),
        email,
        domain,
        datetime.now(UTC) if consumed else None,
        age,
    )
    return domain


async def _row(pool: asyncpg.Pool, email_domain: str) -> asyncpg.Record:
    row = await pool.fetchrow(f"select * from {TABLE} where email_domain = $1", email_domain)
    assert row is not None, "no delivery row was materialized"
    return row


async def _domains(pool: asyncpg.Pool) -> list[str]:
    rows = await pool.fetch(f"select email_domain from {TABLE} order by created_at")
    return [row["email_domain"] for row in rows]


async def test_a_granted_domain_earns_one_channel_and_one_invitation(
    slack: SlackStub, store: OnboardStore
) -> None:
    """A domain re-granted after its first grant lapsed is still one customer: the second grant
    finds the row its own domain already keys and sends nothing."""
    pool = store.pool
    founder = await _granted_domain(
        pool, "founder@acme.io", consumed=True, age=timedelta(minutes=5)
    )
    await _granted_domain(pool, "second@acme.io")

    inviter = _inviter(pool)
    assert await inviter.poll() is True
    assert await inviter.poll() is False

    assert await _domains(pool) == [founder]
    row = await _row(pool, founder)
    channel_name = "ext-acme-flyingobject"
    assert row["channel_name"] == channel_name
    assert "founder" not in row["channel_name"]
    assert row["state"] == "delivered"
    assert row["channel_id"] == CHANNEL_ID
    assert row["slack_invitation_id"] == INVITE_ID
    assert row["invite_attempted_at"] is not None
    assert row["delivered_at"] is not None
    assert row["worker_id"] is None
    assert row["attempts"] == 1

    assert slack.authorizations == {f"Bearer {BOT_TOKEN}"}
    assert slack.forms("conversations.create") == [{"name": channel_name}]
    assert slack.forms("conversations.inviteShared") == [
        {"channel": CHANNEL_ID, "emails": "founder@acme.io", "external_limited": "false"}
    ]
    assert slack.forms("conversations.listConnectInvites") == []
    assert slack.forms("chat.postMessage") == [
        {
            "channel": CHANNEL_ID,
            "text": "This channel is shared with ufo. Anyone at acme.io can sign in at "
            f"https://{APEX_HOST}/login, or from a terminal with "
            f"`curl -fsSL https://{APEX_HOST}/ufo | sh`.",
        }
    ]
    assert row["greeted_at"] is not None


async def test_a_greeting_that_slack_refused_is_retried_and_then_never_repeated(
    slack: SlackStub, store: OnboardStore
) -> None:
    """The one act that accepts a duplicate rather than a silent miss: the marker is written after
    Slack answers, so a refused post is retried, and a row that already carries the marker posts
    nothing on any later sweep."""
    pool = store.pool
    founder = await _granted_domain(pool, "founder@greetco.io")
    slack.handlers["chat.postMessage"] = responds({"ok": False, "error": "ratelimit"})

    assert await _inviter(pool).poll() is True
    row = await _row(pool, founder)
    assert row["state"] == "pending", "a refused greeting must not settle the row"
    assert row["greeted_at"] is None
    assert row["slack_invitation_id"] == INVITE_ID, "the invitation still stands"

    slack.handlers["chat.postMessage"] = responds({"ok": True, "ts": "1700000000.000100"})
    await pool.execute(
        f"update {TABLE} set next_attempt_at = null where email_domain = $1", founder
    )
    assert await _inviter(pool).poll() is True
    assert (await _row(pool, founder))["state"] == "delivered"
    assert len(slack.forms("chat.postMessage")) == 2, "the refused post was never retried"
    assert slack.forms("conversations.inviteShared") == [
        {"channel": CHANNEL_ID, "emails": "founder@greetco.io", "external_limited": "false"}
    ], "the retry re-invited the customer"

    await pool.execute(
        f"update {TABLE} set state = 'pending', next_attempt_at = null where email_domain = $1",
        founder,
    )
    assert await _inviter(pool).poll() is True
    assert len(slack.forms("chat.postMessage")) == 2, "a settled row greeted the customer twice"


async def _signed_up(pool: asyncpg.Pool, email: str, *, created: bool = True) -> str:
    """A finished signup that burned no grant — what every signup looks like once
    `UFO_INVITE_REQUIRED` is false. `created` is False for a join: a contractor, an advisor, or
    operator staff landing in a workspace their own domain does not name."""
    domain = email.split("@")[1]
    claim_id = uuid4()
    await pool.execute(
        "insert into ufo_control.onboard_claim (id, email, email_domain, surface, surface_ref,"
        "  expires_at, verified_at, resulting_workspace_id, created_workspace)"
        " values ($1, $2, $3, 'ufo', $4, now() + interval '1 hour', now(), $5, $6)",
        claim_id,
        email,
        domain,
        str(claim_id),
        str(uuid4()),
        created,
    )
    return domain


async def test_a_join_earns_no_channel_and_no_invitation(
    slack: SlackStub, store: OnboardStore
) -> None:
    """An advisor seated at another email domain, or operator staff, finishes a claim against a
    workspace their own domain does not name. Counting that would open a public channel in the
    operator workspace for a domain that is no customer, invite someone who is not one, and greet
    them with a promise the invite gate would refuse."""
    pool = store.pool
    await _signed_up(pool, "advisor@outsideco.dev", created=False)

    assert await _inviter(pool).poll() is False
    assert await _domains(pool) == []
    assert slack.calls == []


async def test_a_grant_that_lapsed_unredeemed_earns_nothing(
    slack: SlackStub, store: OnboardStore
) -> None:
    """A grant nobody redeemed is a dead lead. Opening its channel would promise that anyone at
    that domain can sign in, and the invite gate would refuse them with InviteExpired. It also
    decides the first sweep against a database of existing grants: only the live and the redeemed
    earn a channel, never every domain ever approached."""
    pool = store.pool
    await _granted_domain(pool, "founder@lapsedco.io")
    await pool.execute(
        "update ufo_control.invite_code set expires_at = now() - interval '1 day'"
        " where email_domain = $1",
        "lapsedco.io",
    )

    assert await _inviter(pool).poll() is False
    assert await _domains(pool) == []
    assert slack.calls == []


async def test_a_redeemed_grant_still_earns_its_channel(
    slack: SlackStub, store: OnboardStore
) -> None:
    """Redeemed is the other half of the same test: that customer signed up, so an expiry long
    past is no reason to leave them without a channel."""
    pool = store.pool
    founder = await _granted_domain(pool, "founder@redeemedco.io", consumed=True)
    await pool.execute(
        "update ufo_control.invite_code set expires_at = now() - interval '1 day'"
        " where email_domain = $1",
        founder,
    )

    assert await _inviter(pool).poll() is True
    assert (await _row(pool, founder))["state"] == "delivered"


async def test_a_signup_with_no_grant_still_earns_its_channel(
    slack: SlackStub, store: OnboardStore
) -> None:
    """Once the invite gate is off there is no grant to key on, and this feature has to survive
    that. A signup that created a workspace is the other durable fact it fires from."""
    pool = store.pool
    founder = await _signed_up(pool, "founder@openco.io")

    assert await _inviter(pool).poll() is True

    row = await _row(pool, founder)
    assert row["state"] == "delivered"
    assert row["channel_name"] == "ext-openco-flyingobject"
    assert slack.forms("conversations.inviteShared") == [
        {"channel": CHANNEL_ID, "emails": "founder@openco.io", "external_limited": "false"}
    ]


async def test_a_grant_and_its_signup_are_one_customer_and_one_invitation(
    slack: SlackStub, store: OnboardStore
) -> None:
    """Both triggers fire for a granted customer who then signs up. The domain is the key, not the
    fact, so they land on one row — otherwise every invite-gated customer would be invited twice."""
    pool = store.pool
    founder = await _granted_domain(pool, "founder@bothco.io", consumed=True)
    await _signed_up(pool, "founder@bothco.io")

    for _ in range(3):
        if not await _inviter(pool).poll():
            break

    assert await _domains(pool) == [founder]
    assert len(slack.forms("conversations.inviteShared")) == 1
    assert len(slack.forms("conversations.create")) == 1


async def test_a_shared_domain_label_gives_exactly_one_customer_the_channel(
    slack: SlackStub, store: OnboardStore
) -> None:
    """The name carries no workspace id, so `acme.io` and `acme.com` derive one name. That is an
    accepted readability trade, and these are the invariants it must still hold: exactly one of the
    two gets the channel — never both, or they would share it — and an unrelated customer is
    untouched, because `on conflict do nothing` keeps one collision from aborting the batch. Which
    of the two wins is defined: `MATERIALIZE` orders by the channel name then `created_at`, so the
    older grant takes it. The loser needs a channel by hand."""
    pool = store.pool
    twins = [
        await _granted_domain(pool, "founder@acme.io", age=timedelta(minutes=5)),
        await _granted_domain(pool, "founder@acme.com"),
    ]
    bystander = await _granted_domain(pool, "founder@bystanderco.io")

    inviter = _inviter(pool)
    for _ in range(4):
        if not await inviter.poll():
            break

    landed = await pool.fetch(
        f"select email_domain, channel_name, state from {TABLE}"
        " where email_domain = any($1::text[])",
        twins,
    )
    assert len(landed) == 1, "two customers must never share one channel"
    assert landed[0]["email_domain"] == "acme.io", "the older grant takes the shared name"
    assert landed[0]["channel_name"] == "ext-acme-flyingobject"
    assert landed[0]["state"] == "delivered"
    assert (await _row(pool, bystander))["state"] == "delivered", (
        "an unrelated customer was stalled"
    )


async def test_two_pollers_never_claim_the_same_row(slack: SlackStub, store: OnboardStore) -> None:
    pool = store.pool
    founder = await _granted_domain(pool, "founder@raceco.io")

    claimed = await asyncio.gather(
        *(_inviter(pool, f"worker-{index}").poll() for index in range(4))
    )

    assert sum(claimed) == 1, "exactly one replica may claim a row"
    assert len(slack.forms("conversations.inviteShared")) == 1
    row = await _row(pool, founder)
    assert row["state"] == "delivered"
    assert row["attempts"] == 1


async def test_an_expired_lease_is_recoverable_by_another_replica(
    slack: SlackStub, store: OnboardStore
) -> None:
    pool = store.pool
    founder = await _granted_domain(pool, "founder@leaseco.io")
    await pool.execute(gateway_slack_connect.MATERIALIZE)
    await pool.execute(
        f"update {TABLE} set state = 'claimed', worker_id = 'gone', attempts = 3,"
        "  claim_expires_at = now() - interval '1 minute'"
    )

    live = _inviter(pool, "holder")
    assert await live.poll() is True
    row = await _row(pool, founder)
    assert row["state"] == "delivered"
    assert row["attempts"] == 4
    assert await _inviter(pool, "gone").poll() is False


async def test_a_stolen_lease_abandons_the_writeback(slack: SlackStub, store: OnboardStore) -> None:
    pool = store.pool
    founder = await _granted_domain(pool, "founder@stealco.io")

    async def steal(form: dict[str, str]) -> tuple[int, dict[str, Any]]:
        await pool.execute(f"update {TABLE} set worker_id = 'thief'")
        return 200, {"ok": True, "channel": {"id": CHANNEL_ID}}

    slack.handlers["conversations.create"] = steal

    assert await _inviter(pool).poll() is True
    row = await _row(pool, founder)
    assert row["worker_id"] == "thief"
    assert row["state"] == "claimed"
    assert row["channel_id"] is None
    assert slack.forms("conversations.inviteShared") == []


async def test_a_crash_after_channel_creation_recovers_the_exact_channel(
    slack: SlackStub, store: OnboardStore
) -> None:
    pool = store.pool
    founder = await _granted_domain(pool, "founder@retryco.io")
    channel_name = "ext-retryco-flyingobject"
    pages = {
        "": {
            "ok": True,
            "channels": [{"id": "C0NEARMISS", "name": f"{channel_name}-2"}],
            "response_metadata": {"next_cursor": "page-2"},
        },
        "page-2": {"ok": True, "channels": [{"id": CHANNEL_ID, "name": channel_name}]},
    }

    async def listing(form: dict[str, str]) -> tuple[int, dict[str, Any]]:
        return 200, pages[form.get("cursor", "")]

    slack.handlers["conversations.create"] = responds({"ok": False, "error": "name_taken"})
    slack.handlers["conversations.list"] = listing

    assert await _inviter(pool).poll() is True
    row = await _row(pool, founder)
    assert row["state"] == "delivered"
    assert row["channel_id"] == CHANNEL_ID
    assert slack.forms("conversations.inviteShared") == [
        {"channel": CHANNEL_ID, "emails": "founder@retryco.io", "external_limited": "false"}
    ]


async def test_an_ambiguous_invitation_reconciles_without_a_second_invitation(
    slack: SlackStub, store: OnboardStore
) -> None:
    pool = store.pool
    founder = await _granted_domain(pool, "founder@ambigco.io")
    await pool.execute(gateway_slack_connect.MATERIALIZE)
    await pool.execute(
        f"update {TABLE} set channel_id = $1, invite_attempted_at = now()", CHANNEL_ID
    )
    slack.handlers["conversations.listConnectInvites"] = responds(
        {
            "ok": True,
            "invites": [
                {
                    "channel": {"id": "C0STRANGER"},
                    "invite": {"id": "I0STRANGER"},
                    "status": "sent",
                },
                {"channel": {"id": CHANNEL_ID}, "invite": {"id": INVITE_ID}, "status": "sent"},
            ],
        }
    )

    assert await _inviter(pool).poll() is True
    row = await _row(pool, founder)
    assert row["state"] == "delivered"
    assert row["slack_invitation_id"] == INVITE_ID
    assert slack.forms("conversations.create") == []
    assert slack.forms("conversations.inviteShared") == []


async def test_a_dead_invitation_is_never_mistaken_for_a_live_one(
    slack: SlackStub, store: OnboardStore
) -> None:
    """Slack keeps listing an invite after it dies — archiving a channel flips it to `revoked` and
    leaves it in the list. Matching the channel alone would settle a customer who never got a
    working invite, so a dead status means invite again."""
    pool = store.pool
    founder = await _granted_domain(pool, "founder@revokedco.io")
    await pool.execute(gateway_slack_connect.MATERIALIZE)
    await pool.execute(
        f"update {TABLE} set channel_id = $1, invite_attempted_at = now()", CHANNEL_ID
    )
    slack.handlers["conversations.listConnectInvites"] = responds(
        {
            "ok": True,
            "invites": [
                {"channel": {"id": CHANNEL_ID}, "invite": {"id": "I0DEAD"}, "status": "revoked"}
            ],
        }
    )
    slack.handlers["conversations.info"] = responds({"ok": True, "channel": {}})

    assert await _inviter(pool).poll() is True
    row = await _row(pool, founder)
    assert row["state"] == "delivered"
    assert row["slack_invitation_id"] == INVITE_ID
    assert len(slack.forms("conversations.inviteShared")) == 1


async def test_an_unrecognized_invite_status_fails_for_operator_review(
    slack: SlackStub, store: OnboardStore
) -> None:
    pool = store.pool
    founder = await _granted_domain(pool, "founder@oddstatusco.io")
    await pool.execute(gateway_slack_connect.MATERIALIZE)
    await pool.execute(
        f"update {TABLE} set channel_id = $1, invite_attempted_at = now()", CHANNEL_ID
    )
    slack.handlers["conversations.listConnectInvites"] = responds(
        {
            "ok": True,
            "invites": [
                {"channel": {"id": CHANNEL_ID}, "invite": {"id": "I0ODD"}, "status": "sideways"}
            ],
        }
    )

    assert await _inviter(pool).poll() is True
    row = await _row(pool, founder)
    assert row["state"] == "failed"
    assert "sideways" in row["last_error"]
    assert slack.forms("conversations.inviteShared") == []


async def test_an_externally_shared_channel_settles_an_ambiguous_invitation(
    slack: SlackStub, store: OnboardStore
) -> None:
    pool = store.pool
    founder = await _granted_domain(pool, "founder@sharedco.io")
    await pool.execute(gateway_slack_connect.MATERIALIZE)
    await pool.execute(
        f"update {TABLE} set channel_id = $1, invite_attempted_at = now()", CHANNEL_ID
    )
    slack.handlers["conversations.listConnectInvites"] = responds({"ok": True, "invites": []})
    slack.handlers["conversations.info"] = responds(
        {"ok": True, "channel": {"is_pending_ext_shared": True}}
    )

    assert await _inviter(pool).poll() is True
    row = await _row(pool, founder)
    assert row["state"] == "delivered"
    assert row["slack_invitation_id"] is None
    assert slack.forms("conversations.inviteShared") == []


async def test_unreconcilable_ambiguity_fails_instead_of_inviting_again(
    slack: SlackStub, store: OnboardStore
) -> None:
    pool = store.pool
    founder = await _granted_domain(pool, "founder@opaqueco.io")
    await pool.execute(gateway_slack_connect.MATERIALIZE)
    await pool.execute(
        f"update {TABLE} set channel_id = $1, invite_attempted_at = now()", CHANNEL_ID
    )
    slack.handlers["conversations.listConnectInvites"] = responds(
        {"ok": False, "error": "missing_scope"}
    )

    assert await _inviter(pool).poll() is True
    row = await _row(pool, founder)
    assert row["state"] == "failed"
    assert row["last_error"] == "conversations.listConnectInvites: missing_scope"
    assert slack.forms("conversations.inviteShared") == []


async def test_transient_and_terminal_slack_errors_take_distinct_durable_paths(
    slack: SlackStub, store: OnboardStore
) -> None:
    pool = store.pool
    founder = await _granted_domain(pool, "founder@errorco.io")
    slack.handlers["conversations.inviteShared"] = responds({"ok": False, "error": "ratelimit"})

    assert await _inviter(pool).poll() is True
    row = await _row(pool, founder)
    assert row["state"] == "pending"
    assert row["worker_id"] is None
    assert row["channel_id"] == CHANNEL_ID
    assert row["invite_attempted_at"] is not None
    assert row["next_attempt_at"] > datetime.now(UTC)
    assert row["last_error"] == "conversations.inviteShared: ratelimit"
    assert await _inviter(pool).poll() is False

    await pool.execute(f"update {TABLE} set next_attempt_at = now() - interval '1 second'")
    slack.handlers["conversations.listConnectInvites"] = responds({"ok": True, "invites": []})
    slack.handlers["conversations.info"] = responds({"ok": True, "channel": {}})
    slack.handlers["conversations.inviteShared"] = responds({"ok": False, "error": "invalid_email"})

    assert await _inviter(pool).poll() is True
    row = await _row(pool, founder)
    assert row["state"] == "failed"
    assert row["next_attempt_at"] is None
    assert row["last_error"] == "conversations.inviteShared: invalid_email"
    assert len(slack.forms("conversations.inviteShared")) == 2
    assert slack.forms("conversations.create") == [{"name": "ext-errorco-flyingobject"}]
    assert await _inviter(pool).poll() is False


async def test_repeated_transient_failures_stop_at_the_attempt_ceiling(
    slack: SlackStub, store: OnboardStore
) -> None:
    """The only place a transient error becomes terminal. One attempt short of the ceiling the row
    still reschedules; the attempt that reaches it lands `failed` instead of waiting again."""
    pool = store.pool
    founder = await _granted_domain(pool, "founder@ceilingco.io")
    slack.handlers["conversations.inviteShared"] = responds(
        {"ok": False, "error": "service_unavailable"}
    )
    slack.handlers["conversations.listConnectInvites"] = responds({"ok": True, "invites": []})
    slack.handlers["conversations.info"] = responds({"ok": True, "channel": {}})
    await pool.execute(gateway_slack_connect.MATERIALIZE)
    await pool.execute(f"update {TABLE} set attempts = $1", MAX_ATTEMPTS - 2)

    assert await _inviter(pool).poll() is True
    row = await _row(pool, founder)
    assert row["attempts"] == MAX_ATTEMPTS - 1
    assert row["state"] == "pending"

    await pool.execute(f"update {TABLE} set next_attempt_at = now() - interval '1 second'")
    assert await _inviter(pool).poll() is True
    row = await _row(pool, founder)
    assert row["attempts"] == MAX_ATTEMPTS
    assert row["state"] == "failed"
    assert row["next_attempt_at"] is None
    assert "service_unavailable" in row["last_error"]


async def test_a_malformed_channel_object_is_classified_terminal(
    slack: SlackStub, store: OnboardStore
) -> None:
    """`_text` narrows a wrong-shaped payload into a terminal error rather than an AttributeError,
    so this lands `failed` through the classified branch. The catch-all is proven separately."""
    pool = store.pool
    founder = await _granted_domain(pool, "founder@oddshapeco.io")
    slack.handlers["conversations.create"] = responds({"ok": True, "channel": "not-an-object"})

    assert await _inviter(pool).poll() is True
    row = await _row(pool, founder)
    assert row["state"] == "failed"
    assert row["worker_id"] is None
    assert row["last_error"]
    assert await _inviter(pool).poll() is False


async def test_a_null_channel_on_another_invite_never_crashes_reconciliation(
    slack: SlackStub, store: OnboardStore
) -> None:
    """`entry.get("channel", {})` only defaults when the key is absent — an explicit null would have
    raised AttributeError, which is unclassified. A junk entry for some other channel is skipped."""
    pool = store.pool
    founder = await _granted_domain(pool, "founder@nullchanco.io")
    await pool.execute(gateway_slack_connect.MATERIALIZE)
    await pool.execute(
        f"update {TABLE} set channel_id = $1, invite_attempted_at = now()", CHANNEL_ID
    )
    slack.handlers["conversations.listConnectInvites"] = responds(
        {
            "ok": True,
            "invites": [
                {"channel": None, "invite": {"id": "I0JUNK"}, "status": "sent"},
                {"channel": {"id": CHANNEL_ID}, "invite": {"id": INVITE_ID}, "status": "sent"},
            ],
        }
    )

    assert await _inviter(pool).poll() is True
    row = await _row(pool, founder)
    assert row["state"] == "delivered"
    assert row["slack_invitation_id"] == INVITE_ID
    assert slack.forms("conversations.inviteShared") == []


async def test_http_rate_limit_and_server_errors_are_transient_with_their_interval(
    slack: SlackStub,
) -> None:
    """The status-code branches, not the documented-error-string one: a real 429 with a real
    `Retry-After`, the clamp that bounds what Slack can ask us to wait, a header we cannot parse,
    and a 5xx."""
    client = SlackConnectClient(bot_token=BOT_TOKEN)
    slack.handlers["conversations.inviteShared"] = responds({"ok": False}, status=429)

    slack.headers["conversations.inviteShared"] = {"Retry-After": "5"}
    with pytest.raises(SlackTransientError) as asked:
        await client.invite_shared(CHANNEL_ID, "founder@rateco.io")
    assert asked.value.retry_after == 5.0

    slack.headers["conversations.inviteShared"] = {"Retry-After": "99999"}
    with pytest.raises(SlackTransientError) as clamped:
        await client.invite_shared(CHANNEL_ID, "founder@rateco.io")
    assert clamped.value.retry_after == MAX_RETRY_AFTER_SECONDS

    slack.headers["conversations.inviteShared"] = {"Retry-After": "whenever"}
    with pytest.raises(SlackTransientError) as unparsable:
        await client.invite_shared(CHANNEL_ID, "founder@rateco.io")
    assert unparsable.value.retry_after is None

    slack.headers.pop("conversations.inviteShared")
    slack.handlers["conversations.inviteShared"] = responds({"ok": False}, status=503)
    with pytest.raises(SlackTransientError) as unavailable:
        await client.invite_shared(CHANNEL_ID, "founder@rateco.io")
    assert unavailable.value.retry_after is None


async def test_a_rate_limited_delivery_waits_the_interval_slack_asked_for(
    slack: SlackStub, store: OnboardStore
) -> None:
    """`Retry-After` has to reach the durable schedule, not just the exception."""
    pool = store.pool
    founder = await _granted_domain(pool, "founder@waitco.io")
    slack.handlers["conversations.inviteShared"] = responds({"ok": False}, status=429)
    slack.headers["conversations.inviteShared"] = {"Retry-After": "5"}

    assert await _inviter(pool).poll() is True
    row = await _row(pool, founder)
    assert row["state"] == "pending"
    assert row["next_attempt_at"] > datetime.now(UTC)
    assert row["next_attempt_at"] < datetime.now(UTC) + timedelta(seconds=RETRY_BACKOFF_SECONDS)


async def test_an_over_long_recipient_never_reaches_slack(
    slack: SlackStub, store: OnboardStore
) -> None:
    pool = store.pool
    founder = await _granted_domain(pool, "l" * 250 + "@toolongco.io")

    assert await _inviter(pool).poll() is True
    row = await _row(pool, founder)
    assert row["state"] == "failed"
    assert "254" in row["last_error"]
    assert slack.forms("conversations.inviteShared") == []


async def test_a_long_domain_is_truncated_inside_slacks_channel_name_limit(
    slack: SlackStub, store: OnboardStore
) -> None:
    """The docstring claims 77 characters inside Slack's 80. Prove it at the boundary."""
    pool = store.pool
    founder = await _granted_domain(pool, "founder@" + "d" * 80 + ".io")

    assert await _inviter(pool).poll() is True
    row = await _row(pool, founder)
    label = "d" * gateway_slack_connect.MAX_DOMAIN_LABEL_CHARS
    expected = f"ext-{label}-flyingobject"
    assert row["channel_name"] == expected
    assert len(row["channel_name"]) == 77
    assert slack.forms("conversations.create") == [{"name": expected}]


async def test_a_persisted_error_is_bounded_and_never_carries_the_token(
    slack: SlackStub, store: OnboardStore
) -> None:
    pool = store.pool
    founder = await _granted_domain(pool, "founder@leakco.io")
    slack.handlers["conversations.create"] = responds(
        {"ok": False, "error": f"refused {BOT_TOKEN} {'x' * (ERROR_CHARS * 2)}"}, status=400
    )

    assert await _inviter(pool).poll() is True
    row = await _row(pool, founder)
    assert row["state"] == "failed"
    assert BOT_TOKEN not in row["last_error"]
    assert REDACTION in row["last_error"]
    assert len(row["last_error"]) == ERROR_CHARS


async def test_a_token_from_another_team_fails_the_row_and_keeps_the_poller_alive(
    slack: SlackStub, store: OnboardStore
) -> None:
    """A wrong-team token mutates nothing and takes the row down with it — but never the poller: a
    retired poller would strand every later signup behind a green `/healthz`. Correcting the deploy
    is enough for the next signup, and `slack-connect-retry` re-arms the rows the mistake hit."""
    pool = store.pool
    founder = await _granted_domain(pool, "founder@wrongteamco.io")
    slack.handlers["auth.test"] = responds({"ok": True, "team_id": "T0IMPOSTOR"})

    inviter = _inviter(pool)
    assert await inviter.poll() is True
    assert slack.forms("conversations.create") == []
    row = await _row(pool, founder)
    assert row["state"] == "failed"
    assert row["channel_id"] is None
    assert row["last_error"] == (f"{BOT_TOKEN_ENV} belongs to team T0IMPOSTOR, not {TEAM_ID}")

    later = await _granted_domain(pool, "founder@nextco.io")
    slack.handlers["auth.test"] = responds({"ok": True, "team_id": TEAM_ID})
    assert await inviter.poll() is True
    assert (await _row(pool, later))["state"] == "delivered"
    assert await rearm_failed_delivery(pool, founder) is not None
    assert await inviter.poll() is True
    assert (await _row(pool, founder))["state"] == "delivered"


def test_the_app_manifest_declares_a_scope_for_every_method_the_workflow_calls() -> None:
    """A missing scope fails nowhere but production, against a real customer's channel. The
    manifest is the operator's copy-paste source for api.slack.com, so it is asserted against the
    methods this module actually calls."""
    manifest = (REPO / "control/slack-connect-app.yaml").read_text()
    scopes = {
        "auth.test": None,
        "conversations.create": "channels:manage",
        "conversations.list": "channels:read",
        "conversations.info": "channels:read",
        "conversations.listConnectInvites": "conversations.connect:manage",
        "conversations.inviteShared": "conversations.connect:write",
        "chat.postMessage": "chat:write",
    }
    source = (REPO / "control/src/ufo_control/gateway_slack_connect.py").read_text()
    for method, scope in scopes.items():
        assert f'"{method}"' in source, f"{method} is no longer called"
        if scope is not None:
            assert f"      - {scope}\n" in manifest, f"{method} needs {scope}"


async def test_no_repr_can_print_the_bot_token(store: OnboardStore) -> None:
    client = SlackConnectClient(bot_token=BOT_TOKEN)
    inviter = _inviter(store.pool)
    assert BOT_TOKEN not in repr(client)
    assert BOT_TOKEN not in repr(inviter)
    assert BOT_TOKEN not in repr(SlackConnectClient(bot_token=BOT_TOKEN, timeout=1.0))
    assert client.bot_token == BOT_TOKEN


async def test_a_non_object_response_reaches_the_unclassified_catch_all(
    slack: SlackStub, store: OnboardStore
) -> None:
    """A JSON array where Slack documents an object: `payload.get("ok")` raises AttributeError,
    which no classifier claims. It must land `failed` rather than leaving the row `claimed` to be
    re-claimed every lease forever, recording nothing and alerting nobody."""
    pool = store.pool
    founder = await _granted_domain(pool, "founder@arrayco.io")
    slack.handlers["conversations.create"] = responds([{"not": "an object"}])

    assert await _inviter(pool).poll() is True
    row = await _row(pool, founder)
    assert row["state"] == "failed"
    assert row["worker_id"] is None
    assert row["last_error"]
    assert await _inviter(pool).poll() is False


async def test_a_junk_channel_entry_never_crashes_the_name_lookup(
    slack: SlackStub, store: OnboardStore
) -> None:
    pool = store.pool
    founder = await _granted_domain(pool, "founder@junklistco.io")
    channel_name = "ext-junklistco-flyingobject"
    slack.handlers["conversations.create"] = responds({"ok": False, "error": "name_taken"})
    slack.handlers["conversations.list"] = responds(
        {"ok": True, "channels": ["not-an-object", {"id": CHANNEL_ID, "name": channel_name}]}
    )

    assert await _inviter(pool).poll() is True
    row = await _row(pool, founder)
    assert row["state"] == "delivered"
    assert row["channel_id"] == CHANNEL_ID


async def test_the_polling_loop_delivers_and_stops_on_cancellation(
    slack: SlackStub, store: OnboardStore
) -> None:
    pool = store.pool
    founder = await _granted_domain(pool, "founder@loopco.io")
    inviter = _inviter(pool)
    running = asyncio.create_task(inviter.run())
    for _ in range(SERVER_START_TICKS):
        row = await pool.fetchrow(f"select state from {TABLE} where email_domain = $1", founder)
        if row is not None and row["state"] == "delivered":
            break
        await asyncio.sleep(TICK_SECONDS)

    assert (await _row(pool, founder))["state"] == "delivered"
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running


async def test_a_broken_sweep_is_reported_and_never_stops_the_poller(
    slack: SlackStub, store: OnboardStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An internal fault before any row is claimed must not retire the poller — that would strand
    every later signup behind a healthy `/healthz`. It is reported, not masked, and recovery needs
    no restart."""
    pool = store.pool
    founder = await _granted_domain(pool, "founder@sweepco.io")
    materialize = gateway_slack_connect.MATERIALIZE
    monkeypatch.setattr(gateway_slack_connect, "MATERIALIZE", "select this_column_does_not_exist")
    inviter = _inviter(pool)

    with pytest.raises(asyncpg.PostgresError):
        await inviter.poll()

    running = asyncio.create_task(inviter.run())
    await asyncio.sleep(TICK_SECONDS * 5)
    assert not running.done(), "the poller retired on an internal fault"

    monkeypatch.setattr(gateway_slack_connect, "MATERIALIZE", materialize)
    for _ in range(SERVER_START_TICKS):
        row = await pool.fetchrow(f"select state from {TABLE} where email_domain = $1", founder)
        if row is not None and row["state"] == "delivered":
            break
        await asyncio.sleep(TICK_SECONDS)
    assert (await _row(pool, founder))["state"] == "delivered"
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running


async def test_an_unbounded_channel_walk_fails_instead_of_guessing(
    slack: SlackStub, store: OnboardStore
) -> None:
    """`name_taken` promises the channel exists, so never finding it inside the page bound is
    inconsistent channel state, not a channel to invent."""
    pool = store.pool
    founder = await _granted_domain(pool, "founder@endlessco.io")

    async def endless(form: dict[str, str]) -> tuple[int, dict[str, Any]]:
        return 200, {
            "ok": True,
            "channels": [{"id": "C0OTHER", "name": "unrelated"}],
            "response_metadata": {"next_cursor": "always-more"},
        }

    slack.handlers["conversations.create"] = responds({"ok": False, "error": "name_taken"})
    slack.handlers["conversations.list"] = endless

    assert await _inviter(pool).poll() is True
    row = await _row(pool, founder)
    assert row["state"] == "failed"
    assert "no channel carries it" in row["last_error"]
    assert row["channel_id"] is None
    assert len(slack.forms("conversations.list")) == gateway_slack_connect.MAX_PAGES


async def test_an_unbounded_invite_walk_is_ambiguous_and_never_reinvites(
    slack: SlackStub, store: OnboardStore
) -> None:
    """Running out of pages means we do not know whether an invitation is live, and the one thing
    that must never follow an unknown is a second invitation."""
    pool = store.pool
    founder = await _granted_domain(pool, "founder@endlessinviteco.io")
    await pool.execute(gateway_slack_connect.MATERIALIZE)
    await pool.execute(
        f"update {TABLE} set channel_id = $1, invite_attempted_at = now()", CHANNEL_ID
    )

    async def endless(form: dict[str, str]) -> tuple[int, dict[str, Any]]:
        return 200, {
            "ok": True,
            "invites": [
                {"channel": {"id": "C0OTHER"}, "invite": {"id": "I0OTHER"}, "status": "sent"}
            ],
            "response_metadata": {"next_cursor": "always-more"},
        }

    slack.handlers["conversations.listConnectInvites"] = endless

    assert await _inviter(pool).poll() is True
    row = await _row(pool, founder)
    assert row["state"] == "failed"
    assert "bounded walk" in row["last_error"]
    assert slack.forms("conversations.inviteShared") == []
    assert len(slack.forms("conversations.listConnectInvites")) == gateway_slack_connect.MAX_PAGES


async def test_the_operator_surface_rearms_only_a_failed_row(store: OnboardStore) -> None:
    pool = store.pool
    failed = await _granted_domain(pool, "founder@failedco.io")
    delivered = await _granted_domain(pool, "founder@doneco.io")
    await pool.execute(gateway_slack_connect.MATERIALIZE)
    await pool.execute(
        f"update {TABLE} set state = 'failed', worker_id = 'gone', attempts = 4,"
        "  last_error = 'conversations.inviteShared: invalid_email'"
        " where email_domain = $1",
        failed,
    )
    await pool.execute(
        f"update {TABLE} set state = 'delivered', delivered_at = now() where email_domain = $1",
        delivered,
    )

    assert await rearm_failed_delivery(pool, failed) is not None
    row = await _row(pool, failed)
    assert row["state"] == "pending"
    assert row["attempts"] == 0
    assert row["worker_id"] is None
    assert row["last_error"] is None
    assert await rearm_failed_delivery(pool, failed) is None
    assert await rearm_failed_delivery(pool, delivered) is None
    assert (await _row(pool, delivered))["state"] == "delivered"


async def test_the_configuration_switch_is_strict(
    store: OnboardStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = store.pool
    monkeypatch.delenv(ENABLED_ENV, raising=False)
    assert slack_connect_from_env(pool) is None
    monkeypatch.setenv(ENABLED_ENV, "false")
    assert slack_connect_from_env(pool) is None

    monkeypatch.setenv(ENABLED_ENV, "yes")
    with pytest.raises(RuntimeError, match=ENABLED_ENV):
        slack_connect_from_env(pool)

    monkeypatch.setenv(ENABLED_ENV, "true")
    with pytest.raises(RuntimeError, match=BOT_TOKEN_ENV):
        slack_connect_from_env(pool)
    monkeypatch.setenv(BOT_TOKEN_ENV, BOT_TOKEN)
    with pytest.raises(RuntimeError, match=TEAM_ID_ENV):
        slack_connect_from_env(pool)

    monkeypatch.setenv(TEAM_ID_ENV, TEAM_ID)
    inviter = slack_connect_from_env(pool)
    assert inviter is not None
    assert inviter.team_id == TEAM_ID
    assert inviter.slack.bot_token == BOT_TOKEN


def test_only_the_gateway_deployment_receives_the_slack_connect_token() -> None:
    hosted = (REPO / "infra/templates/hosted.yaml.tpl").read_text()
    carrying = [document for document in hosted.split("\n---\n") if BOT_TOKEN_ENV in document]
    assert len(carrying) == 1
    gateway_deployment = carrying[0]
    assert "kind: Deployment" in gateway_deployment
    assert "name: ufo-gateway" in gateway_deployment
    assert "envFrom" not in gateway_deployment
    assert "secretKeyRef: {name: ufo-gateway-slack-connect, key: bot-token}" in gateway_deployment

    services = (REPO / "infra/templates/cluster-services.yaml.tpl").read_text()
    platform_secrets = [
        document
        for document in services.split("\n---\n")
        if "name: ufo-platform-secrets" in document
    ]
    assert len(platform_secrets) == 1
    assert "slack-connect" not in platform_secrets[0]

    for environment in ("testing", "prod"):
        assert BOT_TOKEN_ENV not in (REPO / f"infra/envs/{environment}/ufo.tf").read_text()

    tracked = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, text=True, check=True
    )
    projections = [
        name
        for name in tracked.stdout.split("\0")
        if name
        and name.split("/")[0] in {"core", "extensions", "packs", "evals"}
        and BOT_TOKEN_ENV in (REPO / name).read_text(errors="ignore")
    ]
    assert projections == []


async def _failed_and_delivered(dsn: str) -> tuple[str, str]:
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        failed = await _granted_domain(pool, "founder@clico.io")
        delivered = await _granted_domain(pool, "founder@clidoneco.io")
        await pool.execute(gateway_slack_connect.MATERIALIZE)
        await pool.execute(
            f"update {TABLE} set state = 'failed', worker_id = 'gone', attempts = 4,"
            "  last_error = 'conversations.inviteShared: invalid_email'"
            " where email_domain = $1",
            failed,
        )
        await pool.execute(
            f"update {TABLE} set state = 'delivered', delivered_at = now() where email_domain = $1",
            delivered,
        )
        return failed, delivered
    finally:
        await pool.close()


async def _state(dsn: str, email_domain: str) -> str:
    connection = await asyncpg.connect(dsn)
    try:
        return str(
            await connection.fetchval(
                f"select state from {TABLE} where email_domain = $1", email_domain
            )
        )
    finally:
        await connection.close()


def test_the_retry_command_rearms_one_failed_row_and_nothing_else(gateway_postgres: str) -> None:
    """The operator verb through its real entry point: Click parsing, the owner-DSN pool it opens
    for itself, and the non-zero exit a no-op re-arm has to report."""
    failed, delivered = asyncio.run(_failed_and_delivered(gateway_postgres))
    runner = CliRunner()

    rearmed = runner.invoke(main, ["slack-connect-retry", failed])
    assert rearmed.exit_code == 0, rearmed.output
    assert f"slack connect delivery for {failed} re-armed, failed since " in rearmed.output
    assert asyncio.run(_state(gateway_postgres, failed)) == "pending"

    again = runner.invoke(main, ["slack-connect-retry", failed])
    assert again.exit_code != 0
    assert f"no failed slack connect delivery for {failed}" in again.output

    on_delivered = runner.invoke(main, ["slack-connect-retry", delivered])
    assert on_delivered.exit_code != 0
    assert asyncio.run(_state(gateway_postgres, delivered)) == "delivered"

    malformed = runner.invoke(main, ["slack-connect-retry", "not-a-uuid"])
    assert malformed.exit_code != 0
    assert "not-a-uuid" in malformed.output

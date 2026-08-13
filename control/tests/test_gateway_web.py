"""The web renderer of the onboarding machine: the JSON directive wire, the sign-in page's
self-containment, and the full walk from the `auth` hop through the callback to a signed-in card,
over the real `onboard_claim` table and `SharedWorkspaces`. Both terminal shapes are asserted, since
the page can only end on one of them: the signed-in card and a gate refusal that ends on `exit`. The
`debugger` directive is asserted at both poles: emitted with the exact URL for an operator-domain
email, absent for everyone else. Workspace resolution is asserted at all three of its poles too: a
domain that creates a workspace, an exact membership at another domain, and a verified member
choosing among multiple workspaces."""

import asyncio
import uuid
from urllib.parse import parse_qs, urlparse
from uuid import NAMESPACE_DNS, UUID, uuid5

import asyncpg
import pytest
from fake_workos import FakeVerifier
from fastapi.testclient import TestClient
from starlette.routing import Route
from ufo.bearer import verify_token
from ufo.ext.surface import OPERATOR_EMAIL_DOMAIN
from ufo.serve import RESERVED_HOST_PREFIXES

import ufo_control.gateway as gateway
from ufo_control.gateway import (
    INVITE_REQUIRED_ENV,
    TOKEN_SECRET_ENV,
    WORKSPACE_BASE_URL_ENV,
    gateway_app,
)
from ufo_control.gateway_directives import PROMPT, directive, render
from ufo_control.gateway_invite import InviteCodes
from ufo_control.gateway_shared import SERVE_DSN_ENV
from ufo_control.gateway_web import LOGIN_PAGE, ONBOARD_SESSION_COOKIE, parse_directives
from ufo_control.gateway_workos import AUTH_CALLBACK_PATH, AUTH_START_PATH

TOKEN_SECRET = "web-token-secret"
WORKSPACE_URL = "https://app.testing.flyingobject.ai"
SIGNED_IN = "Signed in: "

EXIT_IS_TERMINAL = """  else if (directive.verb === 'exit') {
    if (arg !== '0') line('Failed — reload to retry.', 'error');
    finished = true;
    promptRow.style.display = 'none';
  }"""
"""The whole `exit` arm, exactly. The freeze it closes came from making the terminal effect
conditional on the argument, and every way of reintroducing that — nesting these two statements in
the `arg !== '0'` branch, dropping either one, guarding the arm — changes these bytes. A structural
assertion (does the arm mention `finished`?) passes on the nested form and would not have caught the
bug it exists for."""

REFUSAL_REPLACES_THE_WALK = """  log.appendChild(again);
} else {
  advance('');
}"""
"""A refusal arrives as a page load, so it is the boot that must branch. Advancing the machine
alongside the sentence takes the `auth` directive back — the page leaves for the sign-in again and
the member never reads why they were refused, which is a bounce rather than a screen. The `else`
is what keeps the two exclusive."""


def _configure(monkeypatch: pytest.MonkeyPatch, gateway_postgres: str) -> None:
    monkeypatch.setenv(
        SERVE_DSN_ENV, gateway_postgres.replace("postgresql://", "postgresql+asyncpg://")
    )
    monkeypatch.setenv(TOKEN_SECRET_ENV, TOKEN_SECRET)
    monkeypatch.setenv(WORKSPACE_BASE_URL_ENV, WORKSPACE_URL)


def _verifies(monkeypatch: pytest.MonkeyPatch, verifier: FakeVerifier) -> FakeVerifier:
    monkeypatch.setattr(gateway, "workos_verifier_from_env", lambda: verifier)
    return verifier


def _client() -> TestClient:
    """https, so the jar carries the `Secure` onboarding cookie between requests the way a browser
    does — the page names no session, so that cookie is the whole of what identifies it."""
    return TestClient(gateway_app(), base_url="https://testserver")


def test_parse_directives_round_trips_the_wire_escaping() -> None:
    payload = render(
        directive("say", "tab\there"),
        directive("say", "line\nbreak and back\\slash"),
        directive("ask", "Enter your work email:"),
    )
    assert parse_directives(payload) == [
        {"verb": "say", "fields": ["tab\there"]},
        {"verb": "say", "fields": ["line\nbreak and back\\slash"]},
        {"verb": "ask", "fields": ["Enter your work email:"]},
    ]


def test_login_page_is_self_contained_and_targets_the_web_wire() -> None:
    assert LOGIN_PAGE.startswith("<!doctype html>")
    assert "<script src=" not in LOGIN_PAGE
    assert "<link" not in LOGIN_PAGE
    assert "//cdn" not in LOGIN_PAGE
    assert "https://" not in LOGIN_PAGE
    assert "/v1/onboard/web" in LOGIN_PAGE


def test_login_page_member_copy_is_the_fixed_copy() -> None:
    """The sign-in page's fixed member strings, each pinned present — the page is source, so the
    rendered sweep in test_every_word_a_member_reads_carries_no_ufo_metaphor cannot judge its
    typography. Presence proves each listed string, never that no unlisted string exists."""
    for copy in (
        "<title>ufo</title>",
        "<header>ufo</header>",
        "<h1>Sign in</h1>",
        ">Continue</button>",
        "<h1>Signed in</h1>",
        ">Open your workspace</button>",
        "From your terminal:",
        ">Session debugger</button>",
        "'Sign in again'",
        "'Network error — retrying…'",
        "'Error ' + res.status + ' — try again.'",
        "'Failed — reload to retry.'",
    ):
        assert copy in LOGIN_PAGE, copy


def test_login_page_asks_for_the_email_once() -> None:
    """The ask is the machine's own directive, rendered as the prompt label. The page words it no
    second time, so a member reads one instruction and answers one field."""
    assert "work email" not in LOGIN_PAGE


def test_login_page_leaves_for_the_start_path_the_auth_directive_names() -> None:
    """Sign-in is a hop the page never spells: the `auth` directive carries the start path, so the
    route can move without the page changing. The page names no session on the way out or back — the
    start path mints it and binds it as the cookie every POST carries, so neither the page nor a URL
    can decide which session a verified email lands under. The conversation and artifact carry ride
    the hop's query."""
    assert "directive.verb === 'auth'" in LOGIN_PAGE
    assert "location.assign(arg + (q.size ? '?' + q : ''))" in LOGIN_PAGE
    assert "if (target) q.set('c', target[0]);" in LOGIN_PAGE
    assert "if (artifact) q.set('a', artifact);" in LOGIN_PAGE
    assert AUTH_START_PATH not in LOGIN_PAGE
    assert "sessionStorage" not in LOGIN_PAGE
    assert "crypto.randomUUID" not in LOGIN_PAGE
    assert "x-ufo-session" not in LOGIN_PAGE
    assert "params.get('session')" not in LOGIN_PAGE


def test_login_page_states_a_refusal_and_offers_the_walk_again() -> None:
    """The callback hands a refusal back as `/login?error=<sentence>`, so the page reads its own
    query for it, states it, and offers the one act left — carrying the conversation and artifact
    forward, since a refused member still came here for something."""
    assert "params.get('error')" in LOGIN_PAGE
    assert "line(fault, 'error')" in LOGIN_PAGE
    assert "again.href = '/login' + (q.size ? '?' + q : '')" in LOGIN_PAGE
    assert REFUSAL_REPLACES_THE_WALK in LOGIN_PAGE


def test_login_page_reads_the_signed_in_email_off_the_machines_own_line() -> None:
    """The web member types no email — WorkOS holds the address — so the card's email is the
    machine's own `Signed in:` line, the one
    test_web_channel_walks_the_auth_hop_to_the_signed_in_card asserts it emits."""
    assert f"const SIGNED_IN = '{SIGNED_IN}';" in LOGIN_PAGE
    assert "if (arg.startsWith(SIGNED_IN)) email = arg.slice(SIGNED_IN.length);" in LOGIN_PAGE


def test_login_page_renders_a_workspace_choice() -> None:
    assert '<select id="choice"' in LOGIN_PAGE
    assert "directive.verb === 'choose'" in LOGIN_PAGE
    assert "prompt(arg, directive.fields.slice(1))" in LOGIN_PAGE


def test_login_page_hands_the_token_off_by_post_after_the_whole_batch() -> None:
    """The debugger handoff is a form POST (the bearer never rides a URL), and the signed-in card
    completes only after every directive in a batch has been handled — `debugger` arrives after
    `token`/`workspace`, so a per-directive completion check would hide the link."""
    assert '<form id="debugger-row" method="post">' in LOGIN_PAGE
    assert 'name="token"' in LOGIN_PAGE
    assert "token=" not in LOGIN_PAGE
    batch = "for (const directive of payload.directives) handle(directive);"
    completion = "if (token && workspace && !finished) complete();"
    assert LOGIN_PAGE.index(completion) > LOGIN_PAGE.index(batch)
    assert LOGIN_PAGE.count("complete()") == 2


def test_login_page_carries_a_clicked_conversation_onto_the_portal_action() -> None:
    """The portal sends a signed-out click here as `/login?c=<uuid>`, and the card posts the token
    to that conversation, so the member lands on the conversation they clicked rather than a new
    chat. Only a uuid shape rides along, so nothing else in the query reaches the action."""
    assert "new URLSearchParams(location.search).get('c')" in LOGIN_PAGE
    assert (
        "(target ? '?c=' + target[0] : artifact ? '?a=' + encodeURIComponent(artifact) : '')"
        in (LOGIN_PAGE)
    )
    assert "artifactRaw.startsWith('/artifacts/')" in LOGIN_PAGE


def _grant(dsn: str, object_number: int, email: str) -> None:
    async def _mint() -> None:
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=1)
        try:
            await InviteCodes(pool=pool).mint(object_number, email)
        finally:
            await pool.close()

    asyncio.run(_mint())


def _add_member(dsn: str, workspace_id: UUID, email: str) -> None:
    async def _insert() -> None:
        connection = await asyncpg.connect(dsn)
        try:
            await connection.execute(
                "insert into member (id, workspace_id, email, seated_at, created_at, updated_at) "
                "values ($1, $2, $3, now(), now(), now())",
                uuid.uuid4(),
                workspace_id,
                email,
            )
        finally:
            await connection.close()

    asyncio.run(_insert())


def _workspace_exists(dsn: str, workspace_id: UUID) -> bool:
    async def _read() -> bool:
        connection = await asyncpg.connect(dsn)
        try:
            return bool(
                await connection.fetchval("select 1 from workspace where id = $1", workspace_id)
            )
        finally:
            await connection.close()

    return asyncio.run(_read())


def _sign_in(client: TestClient, code: str, carry: dict[str, str] | None = None) -> str:
    """The hop the `auth` directive sends the page on, driven the way a browser drives it: the start
    path, which mints the session and binds it to this client's jar, the state the hosted page hands
    back, and the callback that stamps the claim verified. The returned `location` is the URL the
    page reloads itself with."""
    start = client.get(AUTH_START_PATH, params=carry or {}, follow_redirects=False)
    assert start.status_code == 302
    (packed,) = parse_qs(urlparse(start.headers["location"]).query)["state"]
    landed = client.get(
        AUTH_CALLBACK_PATH, params={"code": code, "state": packed}, follow_redirects=False
    )
    assert landed.status_code == 303
    return landed.headers["location"]


def _advance(client: TestClient, body: str) -> list[dict[str, object]]:
    response = client.post("/v1/onboard/web", content=body)
    assert response.status_code == 200
    return response.json()["directives"]


def _fields(directives: list[dict[str, object]], verb: str) -> list[str]:
    collected: list[str] = []
    for entry in directives:
        if entry["verb"] != verb:
            continue
        fields = entry["fields"]
        assert isinstance(fields, list)
        if fields:
            collected.append(str(fields[0]))
    return collected


def _directive_fields(directives: list[dict[str, object]], verb: str) -> list[str]:
    matching = [entry for entry in directives if entry["verb"] == verb]
    assert len(matching) == 1
    fields = matching[0]["fields"]
    assert isinstance(fields, list)
    return [str(field) for field in fields]


def test_web_channel_walks_the_auth_hop_to_the_signed_in_card(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page's whole walk: a claimless session is answered with `auth` and nothing else, the hop
    lands the verified claim, and the reload the callback names resolves the workspace and caps the
    card. The conversation the member was redirected here with rides out on the hop's query and
    comes back on the return, so the card the page builds can still carry it."""
    _configure(monkeypatch, gateway_postgres)
    email = "boss@webco.io"
    conversation = str(uuid.uuid4())
    _grant(gateway_postgres, 71, email)
    _verifies(monkeypatch, FakeVerifier(exchanges={"landed": email}))
    with _client() as client:
        opening = _advance(client, "")
        assert opening == [{"verb": "auth", "fields": [AUTH_START_PATH]}]

        returned = _sign_in(client, "landed", {"c": conversation})
        assert returned == f"/login?c={conversation}"
        assert client.cookies[ONBOARD_SESSION_COOKIE] not in returned

        signed_in = _advance(client, "")

    assert _fields(signed_in, "say") == [f"{SIGNED_IN}{email}"]
    (token,) = _fields(signed_in, "token")
    (workspace,) = _fields(signed_in, "workspace")
    workspace_id = UUID(str(uuid5(NAMESPACE_DNS, "webco.io")))
    assert verify_token(token, workspace_id) == email
    assert workspace == WORKSPACE_URL
    assert not _fields(signed_in, "debugger")
    assert all(token not in text for text in _fields(signed_in, "say"))
    assert not _fields(signed_in, "choose")
    assert _fields(signed_in, "ask") == [PROMPT]


def test_web_channel_lets_a_member_choose_between_an_exact_membership_and_their_domain(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch, gateway_postgres)
    host_founder = "boss@addedco.io"
    own_founder = "boss@bigco.io"
    alice = "alice@bigco.io"
    _grant(gateway_postgres, 74, host_founder)
    _grant(gateway_postgres, 75, own_founder)
    _verifies(
        monkeypatch,
        FakeVerifier(exchanges={"host": host_founder, "own": own_founder, "alice": alice}),
    )
    host_id = UUID(str(uuid5(NAMESPACE_DNS, "addedco.io")))
    own_id = UUID(str(uuid5(NAMESPACE_DNS, "bigco.io")))
    with _client() as client:
        _sign_in(client, "host")
        _advance(client, "")

        _add_member(gateway_postgres, host_id, alice)

        _sign_in(client, "own")
        _advance(client, "")

        _sign_in(client, "alice")
        offered = _advance(client, "")
        signed_in = _advance(client, "bigco.io")

    assert _directive_fields(offered, "choose") == [
        gateway.WORKSPACE_PROMPT,
        "addedco.io",
        "bigco.io",
    ]
    (token,) = _fields(signed_in, "token")
    assert verify_token(token, own_id) == alice
    assert verify_token(token, host_id) is None
    assert not any("invite" in text for text in _fields(signed_in, "say"))


def test_web_channel_signs_in_an_exact_member_without_a_grant_for_their_domain(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch, gateway_postgres)
    founder = "boss@gatedco.io"
    advisor = "advisor@outsideco.dev"
    _grant(gateway_postgres, 76, founder)
    _verifies(monkeypatch, FakeVerifier(exchanges={"founder": founder, "advisor": advisor}))
    workspace_id = UUID(str(uuid5(NAMESPACE_DNS, "gatedco.io")))
    with _client() as client:
        _sign_in(client, "founder")
        _advance(client, "")

        _add_member(gateway_postgres, workspace_id, advisor)

        _sign_in(client, "advisor")
        signed_in = _advance(client, "")

    (token,) = _fields(signed_in, "token")
    assert verify_token(token, workspace_id) == advisor
    assert not _fields(signed_in, "exit")
    assert not _workspace_exists(gateway_postgres, uuid5(NAMESPACE_DNS, "outsideco.dev"))


def test_web_channel_lets_an_added_member_use_a_grant_to_create_their_domain_workspace(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch, gateway_postgres)
    founder = "boss@opengateco.io"
    advisor = "advisor@openadvisor.dev"
    _grant(gateway_postgres, 77, founder)
    _grant(gateway_postgres, 78, advisor)
    _verifies(monkeypatch, FakeVerifier(exchanges={"founder": founder, "advisor": advisor}))
    workspace_id = UUID(str(uuid5(NAMESPACE_DNS, "opengateco.io")))
    own_id = UUID(str(uuid5(NAMESPACE_DNS, "openadvisor.dev")))
    with _client() as client:
        _sign_in(client, "founder")
        _advance(client, "")

        _add_member(gateway_postgres, workspace_id, advisor)

        _sign_in(client, "advisor")
        offered = _advance(client, "")
        signed_in = _advance(client, "Create openadvisor.dev workspace")

    assert _directive_fields(offered, "choose") == [
        gateway.WORKSPACE_PROMPT,
        "opengateco.io",
        "Create openadvisor.dev workspace",
    ]
    (token,) = _fields(signed_in, "token")
    assert verify_token(token, own_id) == advisor
    assert verify_token(token, workspace_id) is None
    assert not _fields(signed_in, "exit")
    assert _workspace_exists(gateway_postgres, own_id)


def test_web_channel_refusal_ends_the_page_instead_of_stranding_it(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refusal carries no prompt, so `exit` is the only thing that can end the web session. The
    renderer must treat it as terminal for both arguments: the submit handler has already hidden the
    prompt row and disabled the button, so an `exit` the page ignores leaves the reason on screen
    above a dead form that looks exactly like a hang."""
    _configure(monkeypatch, gateway_postgres)
    email = "founder@ungrantedweb.io"
    _verifies(monkeypatch, FakeVerifier(exchanges={"ungranted": email}))
    with _client() as client:
        _sign_in(client, "ungranted")
        refused = _advance(client, "")

    assert "ungrantedweb.io has no invite." in _fields(refused, "say")
    assert _fields(refused, "exit") == ["0"]
    assert not _fields(refused, "ask")
    assert not _fields(refused, "token")
    assert EXIT_IS_TERMINAL in LOGIN_PAGE


def test_disabled_invite_gate_opens_a_new_workspace_without_a_grant(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch, gateway_postgres)
    monkeypatch.setenv(INVITE_REQUIRED_ENV, "false")
    email = "founder@nogate.io"
    _verifies(monkeypatch, FakeVerifier(exchanges={"opened": email}))
    with _client() as client:
        _sign_in(client, "opened")
        signed_in = _advance(client, "")
    assert not any("invite" in text for text in _fields(signed_in, "say"))
    (token,) = _fields(signed_in, "token")
    assert verify_token(token, UUID(str(uuid5(NAMESPACE_DNS, "nogate.io")))) == email


def test_web_and_terminal_sessions_never_share_a_claim(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One id, two channels: the claim the callback stamps is keyed by the web channel, so a
    terminal handed the same id starts its own walk at the email prompt rather than inheriting a
    verified address."""
    _configure(monkeypatch, gateway_postgres)
    _verifies(monkeypatch, FakeVerifier(exchanges={"landed": "pilot@twochannel.io"}))
    with _client() as client:
        _sign_in(client, "landed")
        session = client.cookies[ONBOARD_SESSION_COOKIE]
        terminal = client.post(
            "/v1/onboard/ufo",
            headers={"x-ufo-session": session, "x-ufo-installed": "1"},
            content="",
        )
        assert "Enter your work email:" in terminal.text


def test_terminal_expired_code_returns_to_email_prompt(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch, gateway_postgres)
    verifier = _verifies(monkeypatch, FakeVerifier())
    session = str(uuid.uuid4())
    email = "pilot@expired.io"
    headers = {"x-ufo-session": session, "x-ufo-installed": "1"}

    with TestClient(gateway_app()) as client:
        client.post("/v1/onboard/ufo", headers=headers, content="")
        coded = client.post("/v1/onboard/ufo", headers=headers, content=email)
        assert "Enter the code:" in coded.text
        assert verifier.begun == [email]
        code = verifier.codes[email]
        incorrect = client.post("/v1/onboard/ufo", headers=headers, content="000000")
        assert "The verification code is incorrect." in incorrect.text
        assert "Enter the code:" in incorrect.text

        async def expire() -> None:
            connection = await asyncpg.connect(gateway_postgres)
            try:
                await connection.execute(
                    "update ufo_control.onboard_claim set expires_at = now() - interval '1 second' "
                    "where surface = 'ufo' and surface_ref = $1",
                    session,
                )
            finally:
                await connection.close()

        asyncio.run(expire())
        expired = client.post("/v1/onboard/ufo", headers=headers, content=code)
        restarted = client.post("/v1/onboard/ufo", headers=headers, content=email)

    assert "The verification code expired. Start onboarding again." in expired.text
    assert "Enter your work email:" in expired.text
    assert "Enter the code:" not in expired.text
    assert "Enter the code:" in restarted.text


def test_terminal_workos_fault_returns_to_email_prompt(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A code WorkOS refuses is one to retype, since it cannot say whether the digits were wrong or
    the code had died. A fault is different: nothing graded the digits, so the claim ends rather
    than standing for a retype it has no answer for, and the next address starts a fresh one."""
    _configure(monkeypatch, gateway_postgres)
    email = "pilot@faulted.io"
    verifier = _verifies(monkeypatch, FakeVerifier(faults={email}))
    session = str(uuid.uuid4())
    headers = {"x-ufo-session": session, "x-ufo-installed": "1"}

    with TestClient(gateway_app()) as client:
        client.post("/v1/onboard/ufo", headers=headers, content="")
        client.post("/v1/onboard/ufo", headers=headers, content=email)
        faulted = client.post("/v1/onboard/ufo", headers=headers, content=verifier.codes[email])
        assert "Sign-in failed. Try again." in faulted.text
        assert "Enter your work email:" in faulted.text
        assert "Enter the code:" not in faulted.text
        restarted = client.post("/v1/onboard/ufo", headers=headers, content=email)

    assert "Enter the code:" in restarted.text


def test_debugger_directive_lands_only_for_the_operator_domain(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch, gateway_postgres)
    email = f"alex@{OPERATOR_EMAIL_DOMAIN}"
    _grant(gateway_postgres, 72, email)
    _verifies(monkeypatch, FakeVerifier(exchanges={"operator": email}))
    with _client() as client:
        _sign_in(client, "operator")
        signed_in = _advance(client, "")
    assert _fields(signed_in, "debugger") == [f"{WORKSPACE_URL}/surface/debug"]


def test_debugger_directive_never_lands_outside_the_operator_domain(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch, gateway_postgres)
    email = "pilot@customerco.io"
    _grant(gateway_postgres, 73, email)
    _verifies(monkeypatch, FakeVerifier(exchanges={"customer": email}))
    with _client() as client:
        _sign_in(client, "customer")
        signed_in = _advance(client, "")
    assert _fields(signed_in, "token")
    assert not _fields(signed_in, "debugger")


def test_login_page_serves_itself_and_a_post_with_no_cookie_gets_the_hop(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A page that holds no onboarding cookie holds no session, so the machine finds no claim behind
    it and answers with the hop that mints one — the same answer a fresh page gets."""
    _configure(monkeypatch, gateway_postgres)
    _verifies(monkeypatch, FakeVerifier())
    with _client() as client:
        page = client.get("/login")
        assert page.status_code == 200
        assert page.headers["content-type"].startswith("text/html")
        assert page.text == LOGIN_PAGE
        cookieless = client.post("/v1/onboard/web", content="")
        assert cookieless.status_code == 200
        assert cookieless.json()["directives"] == [{"verb": "auth", "fields": [AUTH_START_PATH]}]


def test_gateway_serves_every_reserved_host_prefix() -> None:
    """The single-app-host ingress routes the reserved prefixes to this gateway (`RESERVED_HOST_
    PREFIXES`, the same constant the serve fleet forbids). The gateway must serve a route under
    each, or the split aims a path at a 404."""
    paths = [route.path for route in gateway_app().routes if isinstance(route, Route)]
    for prefix in RESERVED_HOST_PREFIXES:
        assert any(path.startswith(prefix) for path in paths), prefix

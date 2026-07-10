"""The web presentation of onboarding: the directive→JSON translation round-trips the wire's
escaping, the portal page is self-contained, and the same machine the terminal drives signs a web
session in with the tenant setup link the page hands off to — over the real `onboard_claim` table
and a mock apiserver, exactly like the terminal-channel tests."""

import json
from typing import Any

import httpx

from ufo_control.gateway import Onboarding
from ufo_control.gateway_claim import ClaimWorkflow
from ufo_control.gateway_directives import directive, render
from ufo_control.gateway_email import LoggingEmailSender, WorkEmailPolicy
from ufo_control.gateway_invite import InviteCodes
from ufo_control.gateway_provision import DeployTarget, JoinOrProvision
from ufo_control.gateway_store import OnboardStore
from ufo_control.gateway_token import verify_token
from ufo_control.gateway_web import PORTAL_PAGE, WEB_CHANNEL, parse_directives
from ufo_control.kube import KubeClient

BUNDLE = "ghcr.io/metalcraftai/ufo@sha256:" + "a" * 64
TARGET = DeployTarget(base_domain="flyingobject.ai", bundle_image=BUNDLE)
SECRET = "s3cret"


def test_parse_directives_round_trips_the_wire_escaping() -> None:
    wire = render(
        directive("say", "tabs\tand\nnewlines\\and backslashes"),
        directive("status", "provisioning (pending)…"),
        directive("poll", "2"),
        directive("install"),
    )
    assert parse_directives(wire) == [
        {"verb": "say", "fields": ["tabs\tand\nnewlines\\and backslashes"]},
        {"verb": "status", "fields": ["provisioning (pending)…"]},
        {"verb": "poll", "fields": ["2"]},
        {"verb": "install", "fields": []},
    ]


def test_portal_page_is_self_contained_and_targets_the_web_wire() -> None:
    assert PORTAL_PAGE.startswith("<!doctype html>")
    assert "<script src=" not in PORTAL_PAGE
    assert "<link " not in PORTAL_PAGE
    assert "//cdn" not in PORTAL_PAGE
    assert "https://" not in PORTAL_PAGE  # every URL is built from the wire or location.origin
    assert "/v1/onboard/web" in PORTAL_PAGE
    assert "/surface/setup?token=" in PORTAL_PAGE


class FakeCluster:
    """A mock apiserver driving the real `KubeClient` — the same shape the terminal-channel tests
    use: `list_items` answers the tenant list, `status_sequence` is walked by successive reads."""

    def __init__(
        self, list_items: list[dict[str, Any]], status_sequence: list[dict[str, Any]]
    ) -> None:
        self.list_items = list_items
        self.status_sequence = status_sequence
        self.applied: list[dict[str, Any]] = []
        self.status_index = 0

    def kube(self) -> KubeClient:
        return KubeClient(
            http=httpx.AsyncClient(
                transport=httpx.MockTransport(self._handle), base_url="https://kube.test"
            )
        )

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "PATCH":
            body = json.loads(request.content)
            self.applied.append(body)
            return httpx.Response(200, json=body)
        if path.endswith("/tenants"):
            return httpx.Response(200, json={"items": self.list_items})
        name = path.rsplit("/", 1)[1]
        status = self.status_sequence[min(self.status_index, len(self.status_sequence) - 1)]
        self.status_index += 1
        return httpx.Response(200, json={"metadata": {"name": name}, "status": status})


def _flow(store: OnboardStore, sender: LoggingEmailSender, kube: KubeClient) -> Onboarding:
    return Onboarding(
        claims=ClaimWorkflow(store=store, email_policy=WorkEmailPolicy(), email_sender=sender),
        store=store,
        resolver=JoinOrProvision(kube=kube, target=TARGET),
        invites=InviteCodes(pool=store.pool),
        token_secret=SECRET,
        apex_host="flyingobject.ai",
    )


async def _advance_web(flow: Onboarding, session: str, body: str) -> list[dict[str, Any]]:
    """The web endpoint's exact composition: the shared machine with no install preamble, its
    directives parsed to JSON."""
    return parse_directives(await flow.advance(WEB_CHANNEL, session, body, b""))


def _verb(directives: list[dict[str, Any]], verb: str) -> str | None:
    for entry in directives:
        if entry["verb"] == verb:
            return str(entry["fields"][0]) if entry["fields"] else ""
    return None


async def test_web_channel_walks_email_code_provision_to_the_setup_handoff(
    store: OnboardStore,
) -> None:
    sender = LoggingEmailSender()
    cluster = FakeCluster(
        list_items=[],
        status_sequence=[
            {"phase": "Provisioning"},
            {"phase": "Ready", "workspaceId": "ws-web"},
        ],
    )
    kube = cluster.kube()
    flow = _flow(store, sender, kube)
    opening = await _advance_web(flow, "web-session", "")
    assert "email" in (_verb(opening, "ask") or "")
    await _advance_web(flow, "web-session", "me@acme.com")
    code = sender.last_code("me@acme.com")
    gated = await _advance_web(flow, "web-session", code)
    assert "invite" in (_verb(gated, "ask") or "")
    invite = await InviteCodes(pool=store.pool).mint()
    provisioning = await _advance_web(flow, "web-session", invite)
    assert _verb(provisioning, "poll") == "2"
    assert _verb(provisioning, "status") is not None
    assert len(cluster.applied) == 1
    assert _verb(await _advance_web(flow, "web-session", ""), "poll") == "2"
    signed_in = await _advance_web(flow, "web-session", "")
    await kube.http.aclose()
    token = _verb(signed_in, "token")
    workspace = _verb(signed_in, "workspace")
    assert token is not None
    assert verify_token(token, SECRET)["ws"] == "ws-web"
    name = cluster.applied[0]["spec"]["tenant"]["name"]
    assert workspace == f"https://{name}.flyingobject.ai"
    # The token travels only in the machine-consumed `token` directive; it is never echoed in a
    # human-visible `say` (the page builds the tokened /surface/setup link client-side).
    assert all(token not in "".join(e["fields"]) for e in signed_in if e["verb"] == "say")


async def test_web_and_terminal_sessions_never_share_a_claim(store: OnboardStore) -> None:
    sender = LoggingEmailSender()
    cluster = FakeCluster(list_items=[], status_sequence=[{"phase": "Provisioning"}])
    kube = cluster.kube()
    flow = _flow(store, sender, kube)
    await _advance_web(flow, "s1", "")
    await _advance_web(flow, "s1", "me@acme.com")
    # The same session id on the terminal channel starts fresh: it asks for an email, not a code.
    opening = await flow.advance("ufo", "s1", "", b"")
    await kube.http.aclose()
    assert "email" in opening.decode()

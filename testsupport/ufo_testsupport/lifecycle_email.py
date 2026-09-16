"""The gateway's other end, and the scoped context that reaches it.

Control's route is proved by `servers/control/tests/email_send_it.rs` and the body both ends read
by `servers/control/tests/email_send_contract.json`; this stands in for the transport so a test can
assert what the extension composed and what its own rows then say."""

import json

import httpx

from ufo.runtime.email import PREFERENCE_PATH, SEQUENCES_PATH, EmailSends
from ufo.runtime.ext.context import ExtensionContext, context_for

BASE_URL = "https://app.ufo.ai"
HOME_SURFACE = "web"
EXTENSION = "lifecycle_email"


class Gateway:
    """What control was asked to send, what SES has said about each message so far, the drip
    sequences an operator has approved, and what a member asked to stop hearing."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.reported: dict[str, str | None] = {}
        self.approved: list[dict] = []
        self.preferences: list[dict] = []
        self.refuse: int | None = None
        self.unreachable = False
        self.next_id = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._answer)

    def _answer(self, request: httpx.Request) -> httpx.Response:
        if self.unreachable:
            raise httpx.ConnectError("no route", request=request)
        if request.url.path == SEQUENCES_PATH:
            return httpx.Response(200, json={"sequences": self.approved})
        if request.url.path == PREFERENCE_PATH:
            asked = json.loads(request.content)
            self.preferences.append(asked)
            return httpx.Response(
                200, json={"topic": asked["topic"], "silenced": asked["silenced"]}
            )
        if request.method == "GET":
            message_id = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={"delivery": self.reported.get(message_id)})
        if self.refuse is not None:
            return httpx.Response(self.refuse, json={"detail": "member@acme.com has bounced"})
        self.next_id += 1
        message_id = f"message-{self.next_id}"
        self.sent.append({**json.loads(request.content), "message_id": message_id})
        self.reported.setdefault(message_id, None)
        return httpx.Response(200, json={"message_id": message_id})


def context(
    gateway: Gateway, *, portal: bool = True, own_key_slots: tuple[str, ...] = ()
) -> ExtensionContext:
    """The scoped handle a job handler receives. `portal` off is a deploy with no browser surface,
    which has no screen to send anyone to; `own_key_slots` names the model key slots the balance
    gate's own-key exemption is tested against, as the job dispatcher passes them."""
    return context_for(
        EXTENSION,
        frozenset(),
        email=EmailSends(base_url="http://ufo-gateway", token="t", transport=gateway.transport()),
        public_base_url=BASE_URL if portal else None,
        home_surface=HOME_SURFACE if portal else None,
        own_key_slots=own_key_slots,
    )

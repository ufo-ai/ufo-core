"""The one seam an extension reaches a member by email through.

No Python here sends email, and no Python here draws one. Every SES client, the suppression union
across bounces, complaints and unsubscribes, the frame a message is drawn in, and the consumer that
turns SES feedback into a delivery state live in `servers/control`; a second sender in this process
would duplicate all four. So this posts the words of one message to control's
`/internal/email/send` and reads its delivery back from the row that consumer writes.

The drip copy an operator edits is there for a different reason: a sequence is one set of words for
the whole fleet, and every table this process can reach is scoped to one workspace by row security.
So the approved set is read over the same channel.

Composed once at the boot that wires the jobs role and threaded down as an `ExtensionContext`
field, so an extension holds the capability and never the deploy's control address or token. A
deploy that names no control service wires none, and `ctx.email` is then None — the capability is
absent rather than silently doing nothing."""

import os
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from ufo.harness.o11y import log

CONTROL_EMAIL_URL_ENV = "UFO_CONTROL_EMAIL_URL"
CONTROL_EMAIL_TOKEN_ENV = "UFO_ONBOARD_CONTROL_TOKEN"

SEND_PATH = "/internal/email/send"
SEQUENCES_PATH = "/internal/lifecycle/sequences"
PREFERENCE_PATH = "/internal/email/preference"

TRANSACTIONAL = "transactional"
"""Everything a workspace is doing with a member's money or access. Never silenced."""
PRODUCT_NEWS = "product_news"
"""Everything else. A member silences it and keeps the rest."""
SEND_TIMEOUT_SECONDS = 20.0

REFUSAL_MAX_CHARS = 500


class EmailRefused(RuntimeError):
    """Control would not send this message. The reason is control's own sentence — a malformed
    address, a body past its bound, or an address barred by the suppression union — and a caller
    that retries it unchanged is refused again."""


class EmailUnanswered(RuntimeError):
    """Control never answered, so whether SES took the message is unknown. A caller that repeats it
    may send twice, which is why a lifecycle send records its attempt before it asks."""


@dataclass(frozen=True)
class EmailSends:
    """Send one message, and read back what SES reported about it.

    `kind` names what the message is for (`balance_exhausted`), lowercase with underscores; it is
    recorded on control's row, so an operator reading the ledger can tell one kind of send from
    another. The answer is the SES message id, which is also the key `delivery` reads.

    A caller writes words, never markup: a subject, paragraphs separated by a blank line, and at
    most one act. Control draws them.

    `transport` is the httpx testability seam; a deploy leaves it None."""

    base_url: str
    token: str
    timeout_seconds: float = SEND_TIMEOUT_SECONDS
    transport: httpx.AsyncBaseTransport | None = None

    async def send(
        self,
        *,
        address: str,
        kind: str,
        topic: str,
        subject: str,
        body: str,
        action_label: str | None = None,
        action_url: str | None = None,
    ) -> str:
        answered = await self._ask(
            "POST",
            SEND_PATH,
            json={
                "email": address,
                "kind": kind,
                "topic": topic,
                "subject": subject,
                "body": body,
                "action_label": action_label,
                "action_url": action_url,
            },
        )
        message_id: str = answered["message_id"]
        log("email.sent", kind=kind, message_id=message_id)
        return message_id

    async def silence(self, address: str, topic: str, *, silenced: bool) -> None:
        """Record, or lift, what a member asked for. It is kept where suppression is applied
        rather than beside the extension that heard it, so one place decides what goes out.

        `transactional` is refused: a member cannot silence what their workspace is doing with
        their money, and an extension that tries is told so rather than quietly doing nothing."""
        await self._ask(
            "POST", PREFERENCE_PATH, json={"email": address, "topic": topic, "silenced": silenced}
        )
        log("email.preference", topic=topic, silenced=str(silenced))

    async def preferences(self, address: str) -> dict[str, bool]:
        """Every topic this address may silence, against whether they have. The whole set rather
        than the rows held, so a screen draws what it can offer without holding the list itself."""
        answered = await self._ask("GET", f"{PREFERENCE_PATH}/{quote(address, safe='')}")
        held: list[dict] = answered["topics"]
        return {str(topic["topic"]): bool(topic["silenced"]) for topic in held}

    async def sequences(self) -> tuple[dict, ...]:
        """The drip sequences an operator has approved, as control holds them: the row's id, a
        name, the event each measures from, and its steps. Only an approved revision is here —
        editing one drops its approval, so words nobody signed off never reach a member.

        The id is what an enrollment keys on, because a name can be freed by a rename and taken by
        a second sequence that would then answer for the first one's members.

        The copy lives there rather than here because a sequence is one set of words for the whole
        fleet, and every table this process can reach is scoped to one workspace."""
        answered = await self._ask("GET", SEQUENCES_PATH)
        listed: list[dict] = answered["sequences"]
        return tuple(listed)

    async def delivery(self, message_id: str) -> str | None:
        """What SES last reported for this send — `delivered`, `bounced`, `complained`, `delayed`,
        `rejected`, `rendering_failed`, `unsubscribed` — or None while it has reported nothing.
        Feedback arrives on its own clock, so None means not yet, never failed."""
        answered = await self._ask("GET", f"{SEND_PATH}/{message_id}")
        delivery: str | None = answered["delivery"]
        return delivery

    async def _ask(self, method: str, path: str, json: dict[str, object] | None = None) -> dict:
        url = f"{self.base_url.rstrip('/')}{path}"
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds, transport=self.transport
            ) as client:
                answered = await client.request(
                    method, url, json=json, headers={"authorization": f"Bearer {self.token}"}
                )
        except httpx.HTTPError as error:
            raise EmailUnanswered(f"{method} {path}: {type(error).__name__}") from error
        if answered.is_client_error:
            raise EmailRefused(_detail(answered))
        if not answered.is_success:
            raise EmailUnanswered(f"{method} {path} answered {answered.status_code}")
        decoded: dict = answered.json()
        return decoded


def _detail(answered: httpx.Response) -> str:
    try:
        stated = answered.json().get("detail")
    except ValueError:
        stated = None
    return str(stated or answered.text)[:REFUSAL_MAX_CHARS]


def email_sends_from_env() -> EmailSends | None:
    """The deploy's send seam, or None where no control service is configured — a self-host or a
    dev node, which has nowhere to post. Both halves are required together: a base with no token
    would post an unauthorized request on every send, so it fails loud instead."""
    base_url = os.environ.get(CONTROL_EMAIL_URL_ENV, "").strip()
    if not base_url:
        return None
    token = os.environ.get(CONTROL_EMAIL_TOKEN_ENV, "").strip()
    if not token:
        raise RuntimeError(
            f"{CONTROL_EMAIL_URL_ENV} is set but {CONTROL_EMAIL_TOKEN_ENV} is not — the send seam "
            "is bearer-gated and would be refused on every call"
        )
    return EmailSends(base_url=base_url, token=token)

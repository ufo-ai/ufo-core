"""Whether one ambient message earns a turn at all: the pre-admission decision a durable surface
makes about a thread reply that names nobody.

Once a mention has made a thread the agent's conversation, every later member reply is admitted as
its own turn — including the ones that are two people talking to each other. Deciding that in the
turn costs the turn: the two recorded cases spent $0.688780 and $0.925565 to say "Nothing further
from me on that one." and "Standing by if Marshall has questions on it.". So the decision moves
ahead of admission, onto one cheap model call whose whole output is one word, and a message the
agent is not wanted in never founds a turn.

The ordered rules are issue #129's. A REACT outcome is not among them: the Slack manifest requests
no `reactions:write` scope, so an agent that "reacted" would say nothing at all, and a message whose
whole answer is an acknowledgement is answered in words instead. The decision gates the founding of
a NEW turn and nothing else — it cannot reach a turn that is already running, so staying quiet never
cancels work the agent committed to (issue #129's rule 6) by construction rather than by prompt.

The history window is what makes the call decidable: `<@U0BBYEHCT8F> :point_up_2:` is six characters
and a mention of someone else, and only the thread around it says whether the agent is wanted. So
every message carries its speaker, whether the agent itself wrote it, and its text. History is
bounded to the last AMBIENT_HISTORY_MESSAGES messages and AMBIENT_MESSAGE_CHARS each. The window is
also how far a member's stop reaches — a stop the decision cannot see is a stop it cannot honour —
so it is the whole tail the Slack surface reads rather than a shorter cut of it: a thread carries
a dozen messages of sideways talk between two members in a few minutes, and the stop has to stay
in view across all of it. A new message over the per-message bound is admitted without
classification, so the text that decides whether it is heard is never a truncated substitute. This
holds the whole classifier call three orders of magnitude under the turns it prevents and far below
the 272k-token line where the family's long-prompt rate begins."""

import re
from dataclasses import dataclass
from json import dumps
from pathlib import Path
from typing import Literal, Protocol

from ufo.harness.models.interface import Message, ModelRequest
from ufo.schema.records import ReasoningEffort

AmbientDecision = Literal["REPLY", "NO_REPLY"]
REPLY: AmbientDecision = "REPLY"
NO_REPLY: AmbientDecision = "NO_REPLY"

AMBIENT_REPLY_REVISION = "2026-09-07-member-stop-holds-until-named-no-new-ask"
AMBIENT_REPLY_JOB = "core:ambient_reply"
AMBIENT_HISTORY_MESSAGES = 20
AMBIENT_MESSAGE_CHARS = 600
AMBIENT_REPLY_MAX_TOKENS = 2_048
AMBIENT_REPLY_REASONING: ReasoningEffort = "low"
AMBIENT_PAYLOAD_FENCE = "UFO_THREAD_INPUT"
_DECISION_RE = re.compile(r"NO_REPLY|REPLY")

AMBIENT_REPLY_SYSTEM = (
    (Path(__file__).parent.parent / "prompts" / "ambient_reply.md").read_text().strip()
)


@dataclass(frozen=True)
class AmbientMessage:
    """One message the decision reads: who spoke it, whether the agent itself spoke it, and its
    text. The speaker is whatever identity the surface addresses members by — a Slack member id, so
    the mentions inside a message and the speakers around it are the same names."""

    speaker: str
    text: str
    own: bool = False


class MeteredModel(Protocol):
    """The metered one-shot model call the decision runs on: `ModelAccess` pinned to the deploy's
    ambient classifier model, so the key's workspace and the billed workspace are one, metered under
    `AMBIENT_REPLY_JOB` — the gate fires off every turn the way a job does, and its own cost is what
    the turns it prevents are weighed against. Held as a Protocol so this decision stays out of the
    `ext.context` import cycle."""

    @property
    def model(self) -> str: ...

    async def complete(self, request: ModelRequest) -> str: ...


def _entry(message: AmbientMessage) -> dict[str, object]:
    return {
        "speaker": message.speaker,
        "own": message.own,
        "text": message.text[:AMBIENT_MESSAGE_CHARS],
    }


@dataclass(frozen=True)
class AmbientReplyClassifier:
    """Decide whether one ambient message earns a turn, on one bounded model call.

    Raises rather than guessing: an unreadable answer or a provider failure is the caller's to
    settle, and the surface settles it by admitting the turn — the expensive outcome, never the
    silent one."""

    model: MeteredModel

    async def decide(
        self, message: AmbientMessage, history: tuple[AmbientMessage, ...]
    ) -> AmbientDecision:
        if len(message.text) > AMBIENT_MESSAGE_CHARS:
            raise ValueError("ambient message exceeds classifier input limit")
        answer = await self.model.complete(
            ModelRequest(
                model=self.model.model,
                system=AMBIENT_REPLY_SYSTEM,
                messages=(Message(role="user", content=self._payload(message, history)),),
                max_tokens=AMBIENT_REPLY_MAX_TOKENS,
                conversation_cache_ttl="5m",
                reasoning=AMBIENT_REPLY_REASONING,
            )
        )
        found = _DECISION_RE.findall(answer.upper())
        if not found:
            raise ValueError(f"ambient reply decision is unreadable: {answer[:200]!r}")
        return found[-1]

    def _payload(self, message: AmbientMessage, history: tuple[AmbientMessage, ...]) -> str:
        """The thread as one JSON object between two identical fence lines. Every message is another
        principal's words, so they ride as JSON string values rather than as prose the prompt's own
        structure continues — a member who writes a rule of their own arrives as text under a key,
        and the fence grows until it appears nowhere in the payload."""
        payload = dumps(
            {
                "history": [_entry(entry) for entry in history[-AMBIENT_HISTORY_MESSAGES:]],
                "message": _entry(message),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        fence = AMBIENT_PAYLOAD_FENCE
        while fence in payload:
            fence += "_"
        return f"{fence}\n{payload}\n{fence}"

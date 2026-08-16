"""The reply spans a round marks for a member, and the redaction that keeps their markup off every
surface.

A turn speaks before it ends by tagging content in its intermediate output:

    <reply-to message="a532d68a-6724-5bd3-b34f-3ec90a57db80">
    Filed the launch issue as metalcraftai/ufo#1801.
    </reply-to>

`marked_replies` reads those spans out of a completed round's text and returns the text the window
keeps — the same words with the markup gone, so the round records what it said without inviting the
model to say it again. `ReplyRedaction` does that job incrementally over the live delta stream,
where a chunk boundary falls anywhere: it withholds a span, publishes everything else, and releases
a `<` the following characters prove to be prose.

Malformed markup reaches no member by construction. A stray closer and a nested opener are stripped
rather than delivered, and an opener no closer answered delivers nothing at all — the words a member
reads are only the ones the model closed."""

import re
from dataclasses import dataclass, field
from uuid import UUID

REPLY_TAG = "reply-to"
REPLY_CLOSER = f"</{REPLY_TAG}>"
REPLY_OPENER = re.compile(rf'<{REPLY_TAG}\s+message="([^"<>]*)"\s*>')
REPLY_SPAN = re.compile(f"{REPLY_OPENER.pattern}(.*?){re.escape(REPLY_CLOSER)}", re.DOTALL)
REPLY_MARKUP = re.compile(rf"<{REPLY_TAG}(?:\s[^<>]*)?>|{re.escape(REPLY_CLOSER)}")
OPENER_HEAD = f"<{REPLY_TAG}"
PARTIAL_OPENER = re.compile(rf"<{REPLY_TAG}\s[^<>]*")


@dataclass(frozen=True)
class MarkedReply:
    """One span of a round's text the model addressed to a member: the words to deliver and the
    `message_ref` the tag named, or None when it named something that is not a message id."""

    message_ref: UUID | None
    text: str


def marked_replies(text: str) -> tuple[tuple[MarkedReply, ...], str]:
    """The round's closed reply spans in the order the model produced them, and the round's text
    with every trace of the markup removed. A span's words stay in that text: the window records
    what the turn told the member, the way an ordinary assistant message records a closing reply."""
    replies: list[MarkedReply] = []
    for match in REPLY_SPAN.finditer(text):
        spoken = REPLY_MARKUP.sub("", match.group(2)).strip()
        if spoken:
            replies.append(MarkedReply(message_ref=_named_message(match.group(1)), text=spoken))
    return tuple(replies), REPLY_MARKUP.sub("", text)


def _named_message(named: str) -> UUID | None:
    try:
        return UUID(named.strip())
    except ValueError:
        return None


@dataclass
class ReplyRedaction:
    """One round's live text minus its reply spans, chunk by chunk.

    A span is delivered as its own member-visible reply once the round completes, so the live stream
    must not carry it as narration too — and must never carry its markup, which is the one thing no
    member may read. `feed` returns what is safe to publish now: text before an opener, text after
    a closer, and a `<` the following characters prove to be prose. Everything from an opener to its
    closer is withheld, and so is the tail that could still be growing into a tag. What is still
    withheld when the round ends is dropped with the redaction: an unclosed span, or the head of a
    tag the model never finished. The window keeps the round's whole text either way, so the stream
    loses a preview of those characters and no member loses words."""

    held: str = field(default="")
    inside: bool = field(default=False)

    def feed(self, chunk: str) -> str:
        self.held += chunk
        published: list[str] = []
        while True:
            if self.inside:
                cut = self.held.find(REPLY_CLOSER)
                if cut == -1:
                    self.held = _growing_suffix(self.held, REPLY_CLOSER)
                    break
                self.held = self.held[cut + len(REPLY_CLOSER) :]
                self.inside = False
                continue
            opener = REPLY_OPENER.search(self.held)
            if opener is not None:
                published.append(self.held[: opener.start()])
                self.held = self.held[opener.end() :]
                self.inside = True
                continue
            settled = _settled_chars(self.held)
            published.append(self.held[:settled])
            self.held = self.held[settled:]
            break
        return REPLY_MARKUP.sub("", "".join(published))


def _growing_suffix(text: str, token: str) -> str:
    """The longest tail of `text` that a following chunk could complete into `token`."""
    for start in range(max(len(text) - len(token) + 1, 0), len(text)):
        if token.startswith(text[start:]):
            return text[start:]
    return ""


def _settled_chars(text: str) -> int:
    """How much of `text` can be published now: everything up to a trailing `<` that could still be
    growing into a reply tag, and all of it when the last `<` cannot be one."""
    cut = text.rfind("<")
    if cut == -1:
        return len(text)
    tail = text[cut:]
    if OPENER_HEAD.startswith(tail) or REPLY_CLOSER.startswith(tail):
        return cut
    return cut if PARTIAL_OPENER.fullmatch(tail) else len(text)

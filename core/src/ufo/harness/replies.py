"""The spans a round marks — `<reply-to message="…">` replies spoken mid-turn and the
`<artifact path="…"/>` tag naming the file a closing answer carries — and the redaction that keeps
their markup off every surface.

`marked_replies` reads the reply spans out of a completed round's text and returns the text the
window keeps, the same words with the markup gone. `marked_artifacts` reads the artifact tags out
of a closing answer and returns the answer without them: the file a tag names lands beside the
reply, never words in it. `SpanRedaction` does both incrementally over the live delta stream, where
a chunk boundary falls anywhere. Malformed markup reaches no member: a stray closer and a nested
opener are stripped, a reply no closer answered delivers nothing, and an artifact tag that does not
close itself carries nothing. The tag's earlier form, a `name` opener closed by `</artifact>` around
the write-up, is withheld and stripped the same way, so a transcript the release being replaced
wrote reads as the member read it."""

import re
from dataclasses import dataclass, field
from uuid import UUID

from ufo.harness.containment import contained_leaf

REPLY_TAG = "reply-to"
REPLY_CLOSER = f"</{REPLY_TAG}>"
REPLY_OPENER = re.compile(rf'<{REPLY_TAG}\s+message="([^"<>]*)"\s*>')
REPLY_SPAN = re.compile(f"{REPLY_OPENER.pattern}(.*?){re.escape(REPLY_CLOSER)}", re.DOTALL)
REPLY_MARKUP = re.compile(rf"<{REPLY_TAG}(?:\s[^<>]*)?>|{re.escape(REPLY_CLOSER)}")

ARTIFACT_TAG = "artifact"
ARTIFACT_OPENER = re.compile(rf'<{ARTIFACT_TAG}\s+path="([^"<>]*)"\s*/>')
ARTIFACT_CLOSER = f"</{ARTIFACT_TAG}>"
ARTIFACT_BODY_OPENER = re.compile(rf'<{ARTIFACT_TAG}\s+name="[^"<>]*"\s*>')
ARTIFACT_BODY_SPAN = re.compile(
    rf"{ARTIFACT_BODY_OPENER.pattern}.*?{re.escape(ARTIFACT_CLOSER)}", re.DOTALL
)
ARTIFACT_SPAN = re.compile(
    rf"\s*(?:{ARTIFACT_OPENER.pattern}|{ARTIFACT_BODY_SPAN.pattern})", re.DOTALL
)
ARTIFACT_MARKUP = re.compile(rf"<{ARTIFACT_TAG}(?:\s[^<>]*)?>|{re.escape(ARTIFACT_CLOSER)}")
ARTIFACT_FALLBACK_NAME = "artifact"
ARTIFACT_DEFAULT_SUFFIX = ".md"


@dataclass(frozen=True)
class _Tag:
    """A span's markup: its opener, the closer that ends it — empty for a tag that closes itself —
    and the prefixes a growing tail may still become."""

    opener: re.Pattern[str]
    closer: str
    head: str
    partial: re.Pattern[str]


SPAN_TAGS = (
    _Tag(REPLY_OPENER, REPLY_CLOSER, f"<{REPLY_TAG}", re.compile(rf"<{REPLY_TAG}\s[^<>]*")),
    _Tag(ARTIFACT_OPENER, "", f"<{ARTIFACT_TAG}", re.compile(rf"<{ARTIFACT_TAG}\s[^<>]*")),
    _Tag(
        ARTIFACT_BODY_OPENER,
        ARTIFACT_CLOSER,
        f"<{ARTIFACT_TAG}",
        re.compile(rf"<{ARTIFACT_TAG}\s[^<>]*"),
    ),
)
SPAN_MARKUP = re.compile(f"{REPLY_MARKUP.pattern}|{ARTIFACT_MARKUP.pattern}")


@dataclass(frozen=True)
class MarkedReply:
    """One span of a round's text the model addressed to a member: the words to deliver and the
    `message_ref` the tag named, or None when it named something that is not a message id."""

    message_ref: UUID | None
    text: str


@dataclass(frozen=True)
class MarkedArtifact:
    """One artifact a closing answer carried: the /workspace file its tag named, and the download
    name it takes — the file's own."""

    name: str
    path: str


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


def marked_artifacts(text: str) -> tuple[tuple[MarkedArtifact, ...], str]:
    """The answer's artifact tags in the order the model wrote them, and the answer with every tag
    and every trace of the markup removed — the whitespace that led into a tag goes with it. A tag
    naming no path carries no file. A name is the path's last segment, `artifact` when it has none,
    and a name with no extension is a Markdown file. A transcript the release being replaced wrote
    holds the tag's earlier form, `<artifact name="…">` to `</artifact>` with the write-up inside;
    that span is stripped like the rest and carries nothing, since its file already stands beside
    the reply it closed."""
    artifacts: list[MarkedArtifact] = []
    for match in ARTIFACT_SPAN.finditer(text):
        path = match.group(1)
        if path is not None and path.strip():
            artifacts.append(MarkedArtifact(name=_artifact_name(path), path=path.strip()))
    return tuple(artifacts), ARTIFACT_MARKUP.sub("", ARTIFACT_SPAN.sub("", text))


def _named_message(named: str) -> UUID | None:
    try:
        return UUID(named.strip())
    except ValueError:
        return None


def _artifact_name(named: str) -> str:
    name = contained_leaf(named.strip(), ARTIFACT_FALLBACK_NAME)
    return name if "." in name else name + ARTIFACT_DEFAULT_SUFFIX


@dataclass
class SpanRedaction:
    """One round's live text minus its marked spans, chunk by chunk: a reply span is delivered on
    its own and an artifact span becomes a file, so neither streams as narration and their markup
    never reaches a member. `feed` publishes text before an opener, text after a closer, and a `<`
    the following characters prove to be prose; a span and a tail still growing into a tag are
    withheld, and whatever is withheld when the round ends is dropped — the window keeps the whole
    text."""

    held: str = field(default="")
    closer: str | None = field(default=None)

    def feed(self, chunk: str) -> str:
        self.held += chunk
        published: list[str] = []
        while True:
            if self.closer is not None:
                cut = self.held.find(self.closer)
                if cut == -1:
                    self.held = _growing_suffix(self.held, self.closer)
                    break
                self.held = self.held[cut + len(self.closer) :]
                self.closer = None
                continue
            opened = _first_opener(self.held)
            if opened is not None:
                match, tag = opened
                published.append(self.held[: match.start()])
                self.held = self.held[match.end() :]
                self.closer = tag.closer or None
                continue
            settled = _settled_chars(self.held)
            published.append(self.held[:settled])
            self.held = self.held[settled:]
            break
        return SPAN_MARKUP.sub("", "".join(published))


def _first_opener(text: str) -> tuple[re.Match[str], _Tag] | None:
    """The earliest opener of any span tag in `text`, with the tag it opens."""
    found = [(match, tag) for tag in SPAN_TAGS if (match := tag.opener.search(text)) is not None]
    return min(found, key=lambda pair: pair[0].start(), default=None)


def _growing_suffix(text: str, token: str) -> str:
    """The longest tail of `text` that a following chunk could complete into `token`."""
    for start in range(max(len(text) - len(token) + 1, 0), len(text)):
        if token.startswith(text[start:]):
            return text[start:]
    return ""


def _settled_chars(text: str) -> int:
    """How much of `text` can be published now: everything up to a trailing `<` that could still be
    growing into a span tag, and all of it when the last `<` cannot be one."""
    cut = text.rfind("<")
    if cut == -1:
        return len(text)
    tail = text[cut:]
    for tag in SPAN_TAGS:
        if tag.head.startswith(tail) or tag.closer.startswith(tail) or tag.partial.fullmatch(tail):
            return cut
    return len(text)

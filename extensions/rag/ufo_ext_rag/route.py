"""What every member message is searched for before the model runs.

The prefetch is always on: every message the member sends retrieves, whatever its shape. The
question-shape read this router started from refused what members actually type — `s&p close` is a
factual ask with no question mark, no interrogative opener and nine characters, and it reached no
provider at all. A read of the text cannot tell an ask from an instruction reliably enough to be
the gate on retrieval, and the cost of refusing a real question is a turn answered from nothing
while the cost of retrieving for an instruction is one block the preface tells the model to step
past. So the router no longer decides whether to retrieve. It decides what to retrieve for.

Two inbounds still retrieve nothing, and neither is a judgement about intent. A message with no
content holds no query. A message over `MAX_INBOUND_CHARS` is a pasted document, and sending it
verbatim is not a search: the provider is handed thousands of characters of the member's own text,
which matches nothing and spends the turn's retrieval budget. Finding the ask inside the paste
needs a model call, which is the round trip the prefetch exists to save.

What the router reads is structure, not intent: a message asking three things becomes three
queries, at most `MAX_QUERIES` of them, and a message asking nothing in particular is searched for
as it stands. The action turn is handled where it belongs now — the preface tells the model the
block replaces no tool, so a turn that must send, open, or change something still reaches for its
tools with the block in context."""

import re
from dataclasses import dataclass

MAX_INBOUND_CHARS = 2_000
MAX_QUERIES = 3
MAX_QUERY_CHARS = 400
SEGMENTS = re.compile(r"[^.?!\n]+[.?!]?")
LEADING_WORDS = re.compile(r"[a-z']+")
QUESTION_OPENERS = frozenset(
    {
        "what",
        "whats",
        "who",
        "whos",
        "when",
        "where",
        "which",
        "why",
        "how",
        "is",
        "are",
        "was",
        "were",
        "does",
        "do",
        "did",
        "has",
        "have",
        "should",
        "tell",
        "remind",
        "compare",
    }
)


@dataclass(frozen=True)
class Route:
    """What the prefetch runs, and why it runs that. `queries` empty is the refusal, and `reason`
    names which read produced it, so a routing decision is legible in the turn's log."""

    queries: tuple[str, ...]
    reason: str

    @property
    def prefetches(self) -> bool:
        return bool(self.queries)


def route(text: str) -> Route:
    """The queries `text` is retrieved for, at most `MAX_QUERIES` of them."""
    inbound = text.strip()
    if not inbound:
        return Route((), "no content")
    if len(inbound) > MAX_INBOUND_CHARS:
        return Route((), "too long")
    questions = tuple(
        segment
        for segment in (match.group().strip() for match in SEGMENTS.finditer(inbound))
        if _is_question(segment)
    )
    queries = questions or (inbound,)
    return Route(
        tuple(query[:MAX_QUERY_CHARS] for query in queries[:MAX_QUERIES]),
        "questions" if questions else "message",
    )


def _is_question(segment: str) -> bool:
    if segment.endswith("?"):
        return True
    words = LEADING_WORDS.findall(segment.lower())
    return bool(words) and words[0] in QUESTION_OPENERS

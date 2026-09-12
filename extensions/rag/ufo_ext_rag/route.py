"""Which inbounds are worth a search before the model runs, and what is searched for.

Retrieving on every turn costs a turn latency and context it cannot use: an instruction to send an
email is answered by tools, not by passages, and a retrieved block in front of it only competes
with the work. Adaptive retrieval is the published answer — Self-RAG and Adaptive-RAG both decide
per query whether to retrieve at all, and report the decision is what keeps quality while cutting
the retrieval cost — so this router admits the inbounds a corpus can answer and refuses the rest.

The decision is a read of the text, not a model call: the prefetch exists to save a round trip and
a classifier round trip would spend what it saves. A question the member asks (a `?`, or a
question opener) is admitted; an instruction to act is refused, and so is a request addressed to
the agent (`can you`, `please`) whose verb names an action, since answering that from a passage is
exactly the harm. Each admitted question becomes its own query, so an inbound asking three things
retrieves for three."""

import re
from dataclasses import dataclass

MIN_INBOUND_CHARS = 12
MAX_INBOUND_CHARS = 2_000
MAX_QUERIES = 3
MIN_QUERY_CHARS = 8
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
REQUEST_OPENERS = (
    ("can", "you"),
    ("could", "you"),
    ("would", "you"),
    ("will", "you"),
    ("please",),
    ("go", "ahead"),
)
ACTION_VERBS = frozenset(
    {
        "add",
        "approve",
        "book",
        "build",
        "cancel",
        "close",
        "commit",
        "connect",
        "create",
        "delete",
        "deploy",
        "draft",
        "edit",
        "email",
        "file",
        "fix",
        "install",
        "invite",
        "merge",
        "message",
        "move",
        "open",
        "pay",
        "post",
        "publish",
        "push",
        "refund",
        "reply",
        "reschedule",
        "restart",
        "run",
        "schedule",
        "send",
        "share",
        "ship",
        "spawn",
        "start",
        "stop",
        "submit",
        "update",
        "upload",
        "write",
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
    """The questions in `text` worth retrieving for, at most `MAX_QUERIES` of them."""
    inbound = text.strip()
    if not MIN_INBOUND_CHARS <= len(inbound) <= MAX_INBOUND_CHARS:
        return Route((), "length")
    questions = tuple(
        segment
        for segment in (match.group().strip() for match in SEGMENTS.finditer(inbound))
        if _is_question(segment)
    )
    if not questions:
        return Route((), "no question")
    if any(_asks_for_action(question) for question in questions):
        return Route((), "action requested")
    queries = tuple(
        question[:MAX_QUERY_CHARS] for question in questions if len(question) >= MIN_QUERY_CHARS
    )[:MAX_QUERIES]
    return Route(queries, "question" if queries else "questions too short")


def _is_question(segment: str) -> bool:
    if segment.endswith("?"):
        return True
    words = LEADING_WORDS.findall(segment.lower())
    return bool(words) and words[0] in QUESTION_OPENERS


def _asks_for_action(question: str) -> bool:
    """A question that asks the agent to act — `can you open the pull request?` — is answered by
    tools. The opener is what makes it a request: `how do I open it?` asks about the act and is
    answered from a corpus, so only the second-person openers reach the verb test."""
    words = LEADING_WORDS.findall(question.lower())
    for opener in REQUEST_OPENERS:
        if tuple(words[: len(opener)]) == opener:
            return any(word in ACTION_VERBS for word in words[len(opener) : len(opener) + 2])
    return False

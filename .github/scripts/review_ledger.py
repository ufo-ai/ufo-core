"""The finding ledger for one pull request: every finding it has had, and where each one stands.

Run by the review-pull-request skill's Establish phase. Prints one JSON object on stdout:

    {"rows": [{"id", "subject", "location", "state", "evidence"}] | null,
     "comment_id": <the one ledger comment to edit in place> | null,
     "next_id": "F<n>" | null,
     "body": "<the rendered ledger>" | null}

The rows are a projection, never the state itself. Every field derives from the durable evidence
`prior_findings.py` already reads — the inline comments and their replies and the pull request
comments — so a deleted ledger comment loses a rendering and no state. An id comes from the
`<!-- claude-finding id=F7 -->` marker its comment carries, and from inline-thread creation order
where a comment carries none, so one finding keeps one id for the life of the pull request.

One id is one row. Every comment sharing an id folds into that row: the first publication fixes its
subject and anchor, and replies on any of its threads settle its state, so a finding published twice
cannot render twice with two states.

`todo` moves to `done` only on the reviewer's `verified fixed: <sha>` thread reply. The token opens
its comment after the optional `F<n> — ` prefix a reply cites, so a token quoted mid-sentence moves
nothing. A settled row never moves backwards; a defect that returns gets a new id.

`rows` is `null` whenever any fetch does not produce a usable array — a failed, malformed, or hung
fetch is not evidence that a finding closed, and the caller then treats every earlier finding as
live rather than publishing a ledger that drops it.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from prior_findings import (
    CLAUDE_AUTHORS,
    JsonObject,
    Unusable,
    author_of,
    json_int,
    json_object,
    json_optional_int,
    json_str,
    pages,
)

LEDGER_MARKER = "<!-- claude-review-ledger -->"
LEDGER_HEADER = "| ID | subject | file:line | state | evidence |\n| --- | --- | --- | --- | --- |"
FINDING_MARKER = re.compile(r"<!-- claude-finding id=(F\d+) -->")
FINDING_ID = re.compile(rf"{FINDING_MARKER.pattern}\s*\Z")
ID_PREFIX = re.compile(r"^(F\d+)\s*[—-]\s*")
VERIFIED_FIXED = re.compile(r"verified fixed: ([0-9a-f]{7,40})", re.IGNORECASE)
LEDGER_SHAPE = re.compile(rf"{re.escape(LEDGER_MARKER)}\n\n{re.escape(LEDGER_HEADER)}(\n|\Z)")
SUBJECT_WORDS = 8
TODO = "todo"
DONE = "done"


@dataclass(frozen=True)
class Comment:
    """One inline review comment as the pulls comments endpoint returns it: a finding where it opens
    a thread, an act on that finding where it replies to one."""

    id: int
    author: str
    path: str
    line: int | None
    body: str
    in_reply_to_id: int | None


def comment(node: JsonObject) -> Comment:
    """Parse one inline comment, raising `Unusable` on any shape this module cannot read.

    A comment on a line the head no longer has reports that line under `original_line`.
    """
    line = node.get("line")
    return Comment(
        id=json_int(node.get("id"), "comment.id"),
        author=author_of(node, "comment"),
        path=json_str(node.get("path"), "comment.path"),
        line=json_optional_int(node.get("original_line") if line is None else line, "comment.line"),
        body=json_str(node.get("body") or "", "comment.body"),
        in_reply_to_id=json_optional_int(node.get("in_reply_to_id"), "comment.in_reply_to_id"),
    )


@dataclass(frozen=True)
class Note:
    """One pull request comment as the issues comments endpoint returns it. Its id is how the ledger
    comment is edited in place rather than posted again."""

    id: int
    author: str
    body: str


def note(node: JsonObject) -> Note:
    return Note(
        id=json_int(node.get("id"), "note.id"),
        author=author_of(node, "note"),
        body=json_str(node.get("body") or "", "note.body"),
    )


@dataclass(frozen=True)
class Row:
    id: str
    subject: str
    location: str
    state: str
    evidence: str


@dataclass(frozen=True)
class Ledger:
    rows: tuple[Row, ...] | None
    comment_id: int | None
    next_id: str | None
    body: str | None


UNDETERMINED = Ledger(rows=None, comment_id=None, next_id=None, body=None)

History = tuple[tuple[Comment, ...], tuple[Note, ...]]


def fetch(repo: str, number: str) -> History:
    """Every inline review comment and pull request comment, all pages, unfiltered.

    The inline comments carry findings and thread acts; pull request comments carry the ledger.
    Any one unusable leaves the ledger unknown, never partly known.
    """
    return (
        tuple(comment(json_object(entry, "comment")) for entry in pages(repo, number, "comments")),
        tuple(
            note(json_object(entry, "note"))
            for entry in pages(repo, number, "comments", resource="issues")
        ),
    )


def finding_ids(findings: Sequence[Comment]) -> dict[int, str]:
    """`F<n>` per finding: the id its own comment carries, else the lowest number no other finding
    has claimed, in creation order. Claimed numbers are reserved before any are handed out, so a
    finding published without a marker never takes an id another finding already published."""
    marked = {one.id: match.group(1) for one in findings if (match := FINDING_ID.search(one.body))}
    taken = set(marked.values())
    ids: dict[int, str] = {}
    counter = 0
    for one in findings:
        if one.id in marked:
            ids[one.id] = marked[one.id]
            continue
        counter += 1
        while f"F{counter}" in taken:
            counter += 1
        ids[one.id] = f"F{counter}"
    return ids


def next_id(ids: Sequence[str]) -> str:
    numbers = [int(one[1:]) for one in ids]
    return f"F{max(numbers, default=0) + 1}"


def subject(body: str) -> str:
    """The row's subject: the finding's own opening words, without its id prefix or its marker, cut
    to eight so a row stays one line, with any cell separator escaped."""
    text = ID_PREFIX.sub("", FINDING_MARKER.sub("", body).strip())
    return " ".join(text.split()[:SUBJECT_WORDS]).replace("|", r"\|")


def location(one: Comment) -> str:
    return one.path if one.line is None else f"{one.path}:{one.line}"


def acts_on(threads: Sequence[Comment], comments: Sequence[Comment]) -> tuple[Comment, ...]:
    """Every reply on one finding's threads, in creation order."""
    opened = {one.id for one in threads}
    return tuple(one for one in comments if one.in_reply_to_id in opened)


def is_ledger(one: Note) -> bool:
    """Whether this pull request comment is the ledger to edit in place. The marker is an invisible
    HTML comment anyone can plant or quote, so authorship and the rendered shape both have to hold,
    as `prior_findings.py` holds the verdict marker to its author and its shape. Without both, a
    quote-reply becomes the target and the real ledger goes stale at its own URL."""
    return one.author in CLAUDE_AUTHORS and LEDGER_SHAPE.match(one.body.strip()) is not None


def disposition(
    identifier: str,
    acts: Sequence[Comment],
) -> tuple[str, str]:
    """The state one finding is in and the evidence that put it there.

    `done` is verified once, so a later reply is read as noise rather than a regression. A reply
    citing another finding's id is not this row's act.
    """
    for act in acts:
        text = act.body.strip()
        prefix = ID_PREFIX.match(text)
        if prefix is not None:
            if prefix.group(1) != identifier:
                continue
            text = text[prefix.end() :]
        if (fixed := VERIFIED_FIXED.match(text)) and act.author in CLAUDE_AUTHORS:
            return DONE, fixed.group(1)
    return TODO, ""


def row_of(
    threads: Sequence[Comment],
    identifier: str,
    acts: Sequence[Comment],
) -> Row:
    """One row for one id, anchored where the id was first published: a later comment carrying the
    same id is the same finding, so it never becomes a second row with a state of its own."""
    state, cell = disposition(identifier, acts)
    return Row(
        id=identifier,
        subject=subject(threads[0].body),
        location=location(threads[0]),
        state=state,
        evidence=cell,
    )


def render(rows: Sequence[Row]) -> str:
    lines = [
        f"| {row.id} | {row.subject} | {row.location} | {row.state} | {row.evidence} |"
        for row in rows
    ]
    return "\n".join([LEDGER_MARKER, "", LEDGER_HEADER, *lines])


def ledger(
    comments: tuple[Comment, ...],
    notes: tuple[Note, ...],
) -> Ledger:
    """Every id this pull request has published, as one row each, plus the ledger to publish.

    Only Claude's thread-opening inline comments are findings — every endpoint returns every
    author's entries, and a human's inline note is not a finding the gate tracks. Rows come out in
    first-publication order, one per id however many comments carry it.
    """
    findings = tuple(
        one for one in comments if one.author in CLAUDE_AUTHORS and one.in_reply_to_id is None
    )
    ids = finding_ids(findings)
    threads: dict[str, list[Comment]] = {}
    for one in findings:
        threads.setdefault(ids[one.id], []).append(one)
    acts = {identifier: acts_on(group, comments) for identifier, group in threads.items()}
    rows = tuple(
        row_of(group, identifier, acts[identifier]) for identifier, group in threads.items()
    )
    existing = [one.id for one in notes if is_ledger(one)]
    return Ledger(
        rows=rows,
        comment_id=existing[0] if existing else None,
        next_id=next_id(list(threads)),
        body=render(rows),
    )


def main(argv: list[str]) -> int:
    """Print the ledger, degrading to null rows on any unusable fetch.

    A non-zero exit, a timeout, a missing `gh`, an undecodable byte stream, a malformed body, and a
    non-object element are all unusable: the caller verifies every earlier finding rather than read
    silence as a closed one. Shape errors surface as `Unusable` from the accessors, so no parse
    failure escapes as a traceback.
    """
    if len(argv) != 3:
        print(f"usage: {argv[0]} <owner/repo> <pr-number>", file=sys.stderr)
        return 2
    try:
        result = ledger(*fetch(argv[1], argv[2]))
    except (Unusable, subprocess.SubprocessError, OSError, ValueError) as error:
        print(f"review-ledger fetch failed: {error!r}", file=sys.stderr)
        result = UNDETERMINED
    print(json.dumps(asdict(result), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

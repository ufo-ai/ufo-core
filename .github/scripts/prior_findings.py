"""Prior review history for one pull request: what Claude published on earlier heads.

Run by the review-pull-request skill's Establish phase. Prints one JSON object on stdout:

    {"rounds": <count of reviews this pull request has already had> | null,
     "anchor_sha": "<head the newest prior finding anchored to>" | null,
     "verdicts": ["<each earlier round's summary body>"],
     "findings": [{"path", "line", "commit_id", "body", "replies": [{"author", "body"}]}]}

`rounds` is `0` on a first review, `N` after N earlier rounds, and `null` whenever any fetch does
not produce a usable array — a failed, malformed, or hung fetch is not evidence that no earlier
round exists, and the caller reviews every changed file in that case. `verdicts` carries each
earlier round's summary body, where a review states what it closed and what it re-published: a
round that cannot read them re-derives the same reasoning against a diff that grew since.

A round ends in a decisive review, or — where GitHub refuses one because Claude authored the pull
request — in an issue comment holding only the verdict marker. Both are counted, from three
endpoints, because a count that saw only reviews reads a Claude-authored pull request as unreviewed
however many rounds it has had. A marker carries no summary, so `verdicts` is shorter than `rounds`
on that path.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass

CLAUDE_AUTHORS = frozenset({"claude", "claude[bot]"})
FETCH_TIMEOUT_SECONDS = 120
DECISIVE_STATES = frozenset({"APPROVED", "CHANGES_REQUESTED", "DISMISSED"})
VERDICT_MARKER = re.compile(
    r"<!-- claude-review-verdict head=([0-9a-f]{40}) "
    r"verdict=(APPROVED|CHANGES_REQUESTED) -->"
)

JsonObject = Mapping[str, object]


class Unusable(Exception):
    """A fetched payload is not an array of entries this module can read."""


def json_object(value: object, name: str) -> JsonObject:
    if not isinstance(value, Mapping):
        raise Unusable(f"{name} must be an object")
    return value


def json_str(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise Unusable(f"{name} must be a string")
    return value


def json_int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise Unusable(f"{name} must be an integer")
    return value


def json_optional_int(value: object, name: str) -> int | None:
    return None if value is None else json_int(value, name)


@dataclass(frozen=True)
class Comment:
    """One inline review comment as the pulls comments endpoint returns it."""

    id: int
    author: str
    path: str
    line: int | None
    commit_id: str | None
    body: str
    in_reply_to_id: int | None


def author_of(node: JsonObject, name: str) -> str:
    """The lowercased login on a fetched entry. A deleted account arrives as a null `user`, whose
    empty login matches no reviewer."""
    user = node.get("user")
    if user is None:
        return ""
    return json_str(json_object(user, f"{name}.user").get("login"), "user.login").lower()


def comment(node: JsonObject) -> Comment:
    """Parse one comment, raising `Unusable` on any shape this module cannot read.

    A comment whose anchor commit is gone carries the head it was written against under
    `original_commit_id`.
    """
    line = node.get("line")
    anchor = node.get("commit_id") or node.get("original_commit_id")
    return Comment(
        id=json_int(node.get("id"), "comment.id"),
        author=author_of(node, "comment"),
        path=json_str(node.get("path"), "comment.path"),
        line=json_optional_int(node.get("original_line") if line is None else line, "comment.line"),
        commit_id=None if anchor is None else json_str(anchor, "comment.commit_id"),
        body=json_str(node.get("body") or "", "comment.body"),
        in_reply_to_id=json_optional_int(node.get("in_reply_to_id"), "comment.in_reply_to_id"),
    )


@dataclass(frozen=True)
class Review:
    """One review as the pulls reviews endpoint returns it."""

    author: str
    state: str
    body: str


def review(node: JsonObject) -> Review:
    return Review(
        author=author_of(node, "review"),
        state=json_str(node.get("state"), "review.state").upper(),
        body=json_str(node.get("body") or "", "review.body"),
    )


@dataclass(frozen=True)
class IssueComment:
    """One pull request comment as the issues comments endpoint returns it."""

    author: str
    body: str


def issue_comment(node: JsonObject) -> IssueComment:
    return IssueComment(
        author=author_of(node, "issue_comment"),
        body=json_str(node.get("body") or "", "issue_comment.body"),
    )


@dataclass(frozen=True)
class Reply:
    author: str
    body: str


@dataclass(frozen=True)
class Finding:
    path: str
    line: int | None
    commit_id: str | None
    body: str
    replies: tuple[Reply, ...]


@dataclass(frozen=True)
class PriorRound:
    rounds: int | None
    anchor_sha: str | None
    verdicts: tuple[str, ...]
    findings: tuple[Finding, ...]


UNDETERMINED = PriorRound(rounds=None, anchor_sha=None, verdicts=(), findings=())


def pages(repo: str, number: str, endpoint: str, resource: str = "pulls") -> list[object]:
    """Every element of one paginated endpoint, all pages, unfiltered.

    No `-q`: gh applies a filter per page, so a filtered paginated call returns only the first
    page's results. Unfiltered pages of arrays merge into one array. `--slurp` wraps each page
    instead of merging and is refused alongside `-q`, so it is not the fix here.
    """
    path = f"repos/{repo}/{resource}/{number}/{endpoint}"
    completed = subprocess.run(
        ["gh", "api", path, "--paginate"],
        capture_output=True,
        text=True,
        check=True,
        timeout=FETCH_TIMEOUT_SECONDS,
    )
    payload = json.loads(completed.stdout)
    if not isinstance(payload, list):
        raise Unusable(f"expected a JSON array from {path}, got {type(payload).__name__}")
    return payload


History = tuple[tuple[Comment, ...], tuple[Review, ...], tuple[IssueComment, ...]]


def fetch(repo: str, number: str) -> History:
    """Every inline review comment, review, and pull request comment, all pages, unfiltered.

    Three endpoints, because a round reads only from all of them together: the inline comments carry
    the findings and their replies, the reviews carry the decisive verdicts and their summaries, and
    the issue comments carry the marker verdicts GitHub leaves no other way to publish. Any one
    unusable leaves the history unknown, never partly known.
    """
    return (
        tuple(comment(json_object(node, "comment")) for node in pages(repo, number, "comments")),
        tuple(review(json_object(node, "review")) for node in pages(repo, number, "reviews")),
        tuple(
            issue_comment(json_object(node, "issue_comment"))
            for node in pages(repo, number, "comments", resource="issues")
        ),
    )


def prior_round(
    comments: tuple[Comment, ...],
    reviews: tuple[Review, ...],
    issue_comments: tuple[IssueComment, ...],
) -> PriorRound:
    """Claude's earlier rounds: every finding with the replies on its thread, every round's verdict.

    Every endpoint returns every author's entries, so classification reads Claude's alone: another
    bot's note or an unrelated human comment must not turn a first review into a follow-up. Among
    reviews only a decisive state counts — the inline comment tool posts one `COMMENTED` review per
    finding, so counting those would read a single round as a dozen.

    A marker comment is the round GitHub would not let Claude publish as a review, so it counts the
    same — recognized by the gate's own marker shape, `fullmatch` against the stripped body, so a
    progress comment that quotes the marker is not a round. It holds only the marker,
    so it contributes no summary. A published finding is a round too when neither kind of verdict
    was recorded — a review that died before publishing still spent the round — so the count never
    reads zero while findings exist.
    """
    replies: dict[int, list[Reply]] = {}
    for one in comments:
        if one.in_reply_to_id is not None:
            replies.setdefault(one.in_reply_to_id, []).append(
                Reply(author=one.author, body=one.body)
            )

    findings = tuple(
        Finding(
            path=one.path,
            line=one.line,
            commit_id=one.commit_id,
            body=one.body,
            replies=tuple(replies.get(one.id, ())),
        )
        for one in comments
        if one.author in CLAUDE_AUTHORS and one.in_reply_to_id is None
    )
    verdicts = tuple(
        one.body for one in reviews if one.author in CLAUDE_AUTHORS and one.state in DECISIVE_STATES
    )
    markers = sum(
        1
        for one in issue_comments
        if one.author in CLAUDE_AUTHORS and VERDICT_MARKER.fullmatch(one.body.strip())
    )
    anchors = [finding.commit_id for finding in findings if finding.commit_id]
    return PriorRound(
        rounds=len(verdicts) + markers or (1 if findings else 0),
        anchor_sha=anchors[-1] if anchors else None,
        verdicts=verdicts,
        findings=findings,
    )


def main(argv: list[str]) -> int:
    """Print the prior history, degrading to a null round count on any unusable fetch.

    A non-zero exit, a timeout, a missing `gh`, an undecodable byte stream, a malformed body, and a
    non-object element are all unusable: the caller reviews every changed file rather than read
    silence as a first round. Shape errors surface as `Unusable` from the accessors, so no parse
    failure escapes as a traceback.
    """
    if len(argv) != 3:
        print(f"usage: {argv[0]} <owner/repo> <pr-number>", file=sys.stderr)
        return 2
    try:
        result = prior_round(*fetch(argv[1], argv[2]))
    except (Unusable, subprocess.SubprocessError, OSError, ValueError) as error:
        print(f"prior-findings fetch failed: {error!r}", file=sys.stderr)
        result = UNDETERMINED
    print(json.dumps(asdict(result), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

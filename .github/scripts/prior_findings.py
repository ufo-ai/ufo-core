"""Prior review findings for one pull request: what Claude published on earlier heads.

Run by the review-pull-request skill's Establish phase. Prints one JSON object on stdout:

    {"round": "first" | "follow_up" | "undetermined",
     "anchor_sha": "<head the newest prior finding anchored to>" | null,
     "findings": [{"path", "line", "commit_id", "body", "replies": [{"author", "body"}]}]}

`round` is `undetermined` whenever the fetch does not produce a usable array of comment objects,
never `first` — a failed, malformed, or hung fetch is not evidence that no earlier finding exists,
and the caller reviews every changed file in that case.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass

CLAUDE_AUTHORS = frozenset({"claude", "claude[bot]"})
FETCH_TIMEOUT_SECONDS = 120

JsonObject = Mapping[str, object]


class Unusable(Exception):
    """The fetched payload is not a comments array this module can read."""


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


def comment(node: JsonObject) -> Comment:
    """Parse one comment, raising `Unusable` on any shape this module cannot read.

    A deleted account arrives as a null `user`, whose empty login matches no reviewer. A comment
    whose anchor commit is gone carries the head it was written against under `original_commit_id`.
    """
    user = node.get("user")
    line = node.get("line")
    anchor = node.get("commit_id") or node.get("original_commit_id")
    return Comment(
        id=json_int(node.get("id"), "comment.id"),
        author=(
            ""
            if user is None
            else json_str(json_object(user, "comment.user").get("login"), "user.login").lower()
        ),
        path=json_str(node.get("path"), "comment.path"),
        line=json_optional_int(node.get("original_line") if line is None else line, "comment.line"),
        commit_id=None if anchor is None else json_str(anchor, "comment.commit_id"),
        body=json_str(node.get("body") or "", "comment.body"),
        in_reply_to_id=json_optional_int(node.get("in_reply_to_id"), "comment.in_reply_to_id"),
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
    round: str
    anchor_sha: str | None
    findings: tuple[Finding, ...]


UNDETERMINED = PriorRound(round="undetermined", anchor_sha=None, findings=())


def fetch(repo: str, number: str) -> tuple[Comment, ...]:
    """Every inline review comment on the pull request, all pages, unfiltered.

    No `-q`: gh applies a filter per page, so a filtered paginated call returns only the first
    page's results. Unfiltered pages of arrays merge into one array. `--slurp` wraps each page
    instead of merging and is refused alongside `-q`, so it is not the fix here.
    """
    completed = subprocess.run(
        ["gh", "api", f"repos/{repo}/pulls/{number}/comments", "--paginate"],
        capture_output=True,
        text=True,
        check=True,
        timeout=FETCH_TIMEOUT_SECONDS,
    )
    payload = json.loads(completed.stdout)
    if not isinstance(payload, list):
        raise Unusable(f"expected a JSON array of comments, got {type(payload).__name__}")
    return tuple(comment(json_object(node, "comment")) for node in payload)


def prior_round(comments: tuple[Comment, ...]) -> PriorRound:
    """Claude-authored findings, each paired with the replies on its thread.

    The endpoint returns every author's comments, so classification reads Claude's alone: another
    bot's note or an unrelated human comment must not turn a first review into a follow-up.
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
    anchors = [finding.commit_id for finding in findings if finding.commit_id]
    return PriorRound(
        round="follow_up" if findings else "first",
        anchor_sha=anchors[-1] if anchors else None,
        findings=findings,
    )


def main(argv: list[str]) -> int:
    """Print the prior round, degrading to `undetermined` on any unusable fetch.

    A non-zero exit, a timeout, a missing `gh`, an undecodable byte stream, a malformed body, and a
    non-object element are all unusable: the caller reviews every changed file rather than read
    silence as a first round. Shape errors surface as `Unusable` from the accessors, so no parse
    failure escapes as a traceback.
    """
    if len(argv) != 3:
        print(f"usage: {argv[0]} <owner/repo> <pr-number>", file=sys.stderr)
        return 2
    try:
        result = prior_round(fetch(argv[1], argv[2]))
    except (Unusable, subprocess.SubprocessError, OSError, ValueError) as error:
        print(f"prior-findings fetch failed: {error!r}", file=sys.stderr)
        result = UNDETERMINED
    print(json.dumps(asdict(result), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

#!/usr/bin/env python3
"""Publish an AI-review gate status on PR head commits from Claude's review verdict.

Claude verdicts anchored to superseded commits are dismissed, so a PR carries at
most one live Claude review — the one for the current head."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

CLAUDE_REVIEWERS = frozenset({"claude", "claude[bot]", "claude-code[bot]"})
DECISIVE_STATES = frozenset({"APPROVED", "CHANGES_REQUESTED", "DISMISSED"})
DISMISSIBLE_STATES = frozenset({"APPROVED", "CHANGES_REQUESTED"})
STATUS_CONTEXT = "AI Review Gate"
VERDICT_MARKER = re.compile(
    r"<!-- claude-review-verdict head=([0-9a-f]{40}) "
    r"verdict=(APPROVED|CHANGES_REQUESTED) -->"
)

JsonObject = Mapping[str, object]


@dataclass(frozen=True)
class Review:
    id: int
    reviewer: str
    state: str
    commit_oid: str | None


@dataclass(frozen=True)
class IssueComment:
    author: str
    body: str


def main() -> int:
    owner, repo = repository()
    numbers = target_pull_requests(owner, repo)
    if not numbers:
        print("No pull request targets for this event.")
        return 0

    dry_run = os.environ.get("AI_REVIEW_GATE_DRY_RUN") == "1"
    saw_blocking = False
    for number in numbers:
        head_oid = pull_request_head(owner, repo, number)
        reviews = pull_request_reviews(owner, repo, number)
        verdict = claude_verdict(reviews, issue_comments(owner, repo, number), head_oid)
        state, description = gate_state(verdict)
        saw_blocking = saw_blocking or state != "success"
        print(f"PR #{number}: {description}.")
        if dry_run:
            continue
        for stale in stale_claude_reviews(reviews, head_oid):
            dismiss_review(owner, repo, number, stale, head_oid)
        publish_status(owner, repo, head_oid, state, description)

    return 1 if dry_run and saw_blocking else 0


def claude_verdict(
    reviews: tuple[Review, ...], comments: tuple[IssueComment, ...], head_oid: str
) -> str | None:
    decisive = [
        item.state
        for item in reviews
        if item.reviewer.lower() in CLAUDE_REVIEWERS
        and item.commit_oid == head_oid
        and item.state in DECISIVE_STATES
    ]
    if decisive:
        return None if decisive[-1] == "DISMISSED" else decisive[-1]
    markers = [
        verdict
        for item in comments
        if item.author.lower() in CLAUDE_REVIEWERS
        and (verdict := marker_verdict(item.body, head_oid)) is not None
    ]
    return markers[-1] if markers else None


def marker_verdict(body: str, head_oid: str) -> str | None:
    marker = VERDICT_MARKER.fullmatch(body.strip())
    if marker is None or marker[1] != head_oid:
        return None
    return marker[2]


def stale_claude_reviews(reviews: tuple[Review, ...], head_oid: str) -> tuple[Review, ...]:
    return tuple(
        item
        for item in reviews
        if item.reviewer.lower() in CLAUDE_REVIEWERS
        and item.state in DISMISSIBLE_STATES
        and item.commit_oid != head_oid
    )


def gate_state(verdict: str | None) -> tuple[str, str]:
    match verdict:
        case "APPROVED":
            return "success", "Claude approved"
        case "CHANGES_REQUESTED":
            return "failure", "Claude requested changes"
        case None:
            return "pending", "Awaiting Claude review of the head commit"
    raise SystemExit(f"unexpected verdict {verdict}")


def repository() -> tuple[str, str]:
    value = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" not in value:
        raise SystemExit("GITHUB_REPOSITORY must be set to owner/repo")
    owner, repo = value.split("/", 1)
    return owner, repo


def target_pull_requests(owner: str, repo: str) -> tuple[int, ...]:
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    if event_name == "schedule":
        return open_pull_request_numbers(owner, repo)

    event = event_payload()
    if event_name == "workflow_dispatch":
        inputs = json_object(event.get("inputs"), "workflow inputs")
        pr_input = inputs.get("pr")
        if isinstance(pr_input, str) and pr_input.strip():
            return (int(pr_input),)

    pull_request = event.get("pull_request")
    if isinstance(pull_request, Mapping):
        return (json_int(pull_request.get("number"), "pull_request.number"),)

    issue = event.get("issue")
    if isinstance(issue, Mapping) and isinstance(issue.get("pull_request"), Mapping):
        return (json_int(issue.get("number"), "issue.number"),)

    return ()


def event_payload() -> JsonObject:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        raise SystemExit("GITHUB_EVENT_PATH must be set")
    with Path(event_path).open(encoding="utf-8") as handle:
        return json_object(json.load(handle), "event payload")


def open_pull_request_numbers(owner: str, repo: str) -> tuple[int, ...]:
    result = run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            f"{owner}/{repo}",
            "--state",
            "open",
            "--json",
            "number",
            "--limit",
            "100",
        ]
    )
    rows = json_list(json.loads(result.stdout), "open pull requests")
    return tuple(
        json_int(json_object(row, "pull request row").get("number"), "number") for row in rows
    )


def pull_request_head(owner: str, repo: str, number: int) -> str:
    result = run(
        [
            "gh",
            "pr",
            "view",
            str(number),
            "--repo",
            f"{owner}/{repo}",
            "--json",
            "headRefOid",
        ]
    )
    node = json_object(json.loads(result.stdout), "pull request")
    return json_str(node.get("headRefOid"), "headRefOid")


def pull_request_reviews(owner: str, repo: str, number: int) -> tuple[Review, ...]:
    result = run(
        [
            "gh",
            "api",
            "--paginate",
            "--slurp",
            f"repos/{owner}/{repo}/pulls/{number}/reviews?per_page=100",
        ]
    )
    pages = json_list(json.loads(result.stdout), "review pages")
    return tuple(
        review(json_object(node, "review"))
        for page in pages
        for node in json_list(page, "review page")
    )


def issue_comments(owner: str, repo: str, number: int) -> tuple[IssueComment, ...]:
    result = run(
        [
            "gh",
            "api",
            "--paginate",
            "--slurp",
            f"repos/{owner}/{repo}/issues/{number}/comments?per_page=100",
        ]
    )
    pages = json_list(json.loads(result.stdout), "issue comment pages")
    return tuple(
        issue_comment(json_object(node, "issue comment"))
        for page in pages
        for node in json_list(page, "issue comment page")
    )


def review(node: JsonObject) -> Review:
    user = node.get("user")
    return Review(
        id=json_int(node.get("id"), "review.id"),
        reviewer=(
            ""
            if user is None
            else json_str(json_object(user, "review.user").get("login"), "user.login")
        ),
        state=json_str(node.get("state"), "review.state"),
        commit_oid=json_optional_str(node.get("commit_id"), "review.commit_id"),
    )


def issue_comment(node: JsonObject) -> IssueComment:
    user = node.get("user")
    return IssueComment(
        author=(
            ""
            if user is None
            else json_str(json_object(user, "comment.user").get("login"), "user.login")
        ),
        body=json_str(node.get("body"), "comment.body"),
    )


def dismiss_review(owner: str, repo: str, number: int, stale: Review, head_oid: str) -> None:
    reviewed = "unknown" if stale.commit_oid is None else stale.commit_oid[:7]
    run(
        [
            "gh",
            "api",
            "--method",
            "PUT",
            f"repos/{owner}/{repo}/pulls/{number}/reviews/{stale.id}/dismissals",
            "-f",
            f"message=Stale: reviewed {reviewed}, head is {head_oid[:7]}",
        ]
    )


def publish_status(owner: str, repo: str, head_oid: str, state: str, description: str) -> None:
    server_url = os.environ.get("GITHUB_SERVER_URL")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if not (server_url and run_id):
        raise SystemExit("GITHUB_SERVER_URL and GITHUB_RUN_ID must be set to publish a status")
    run(
        [
            "gh",
            "api",
            f"repos/{owner}/{repo}/statuses/{head_oid}",
            "-f",
            f"state={state}",
            "-f",
            f"context={STATUS_CONTEXT}",
            "-f",
            f"description={description[:140]}",
            "-f",
            f"target_url={server_url}/{owner}/{repo}/actions/runs/{run_id}",
        ]
    )


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise SystemExit(result.returncode)
    return result


def json_object(value: object, name: str) -> JsonObject:
    if not isinstance(value, Mapping):
        raise SystemExit(f"{name} must be an object")
    return value


def json_list(value: object, name: str) -> tuple[object, ...]:
    if not isinstance(value, list):
        raise SystemExit(f"{name} must be a list")
    return tuple(value)


def json_str(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise SystemExit(f"{name} must be a string")
    return value


def json_optional_str(value: object, name: str) -> str | None:
    if value is None:
        return None
    return json_str(value, name)


def json_int(value: object, name: str) -> int:
    if not isinstance(value, int):
        raise SystemExit(f"{name} must be an integer")
    return value


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Publish an AI-review gate status on PR head commits."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

CODEX_REVIEWERS = frozenset(
    {
        "codex",
        "codex[bot]",
        "openai-codex[bot]",
        "chatgpt-codex-connector",
        "chatgpt-codex-connector[bot]",
    }
)
AI_REVIEWERS = CODEX_REVIEWERS | frozenset({"claude", "claude[bot]", "claude-code[bot]"})
STATUS_CONTEXT = "AI Review Gate"
REVIEW_CHECK_NAME = "claude-review"
REVIEWED_COMMIT_PATTERN = re.compile(r"\*\*Reviewed commit:\*\* `([0-9a-f]{7,40})`")
CODEX_CLEAN_PASS = "Didn't find any major issues"

JsonObject = Mapping[str, object]


@dataclass(frozen=True)
class PullRequest:
    number: int
    title: str
    url: str
    head_oid: str


@dataclass(frozen=True)
class PageInfo:
    has_next_page: bool
    end_cursor: str | None


@dataclass(frozen=True)
class ReviewComment:
    reviewer: str
    body: str
    url: str


@dataclass(frozen=True)
class ReviewThread:
    is_resolved: bool
    is_outdated: bool
    comments: tuple[ReviewComment, ...]


@dataclass(frozen=True)
class ReviewThreadsPage:
    threads: tuple[ReviewThread, ...]
    page_info: PageInfo


@dataclass(frozen=True)
class Review:
    reviewer: str
    body: str
    url: str
    state: str
    commit_oid: str | None


@dataclass(frozen=True)
class IssueComment:
    author: str
    body: str


@dataclass(frozen=True)
class IssueCommentsPage:
    comments: tuple[IssueComment, ...]
    page_info: PageInfo


@dataclass(frozen=True)
class ReviewsPage:
    reviews: tuple[Review, ...]
    page_info: PageInfo


@dataclass(frozen=True)
class CheckRun:
    status: str
    conclusion: str | None


@dataclass(frozen=True)
class Blocker:
    reviewer: str
    reason: str
    url: str
    detail: str


def main() -> int:
    owner, repo = repository()
    numbers = target_pull_requests(owner, repo)
    if not numbers:
        print("No pull request targets for this event.")
        return 0

    dry_run = os.environ.get("AI_REVIEW_GATE_DRY_RUN") == "1"
    saw_blocking = False
    for number in numbers:
        pull_request = fetch_pull_request(owner, repo, number)
        reviews = pull_request_reviews(owner, repo, number)
        comments = issue_comments(owner, repo, number)
        blockers = unresolved_thread_blockers(review_threads(owner, repo, number))
        blockers.extend(changes_requested_blockers(reviews, pull_request.head_oid))
        awaiting = awaiting_reasons(
            claude_review_runs(owner, repo, pull_request.head_oid),
            reviews,
            comments,
            pull_request.head_oid,
        )
        state, description = gate_state(blockers, awaiting)
        saw_blocking = saw_blocking or state != "success"
        print_result(pull_request, blockers, awaiting)
        if dry_run:
            continue
        publish_status(owner, repo, pull_request, state, description)

    return 1 if dry_run and saw_blocking else 0


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

    if event_name == "workflow_run":
        workflow_run = json_object(event.get("workflow_run"), "workflow_run")
        rows = json_list(workflow_run.get("pull_requests"), "workflow_run.pull_requests")
        return tuple(
            json_int(json_object(row, "workflow_run pull request").get("number"), "number")
            for row in rows
        )

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


def fetch_pull_request(owner: str, repo: str, number: int) -> PullRequest:
    query = """
    query($owner: String!, $repo: String!, $number: Int!) {
      repository(owner: $owner, name: $repo) {
        pullRequest(number: $number) {
          number
          title
          url
          headRefOid
        }
      }
    }
    """
    data = graphql(query, {"owner": owner, "repo": repo, "number": number})
    pr = pull_request_node(data)
    return PullRequest(
        number=json_int(pr.get("number"), "pullRequest.number"),
        title=json_str(pr.get("title"), "pullRequest.title"),
        url=json_str(pr.get("url"), "pullRequest.url"),
        head_oid=json_str(pr.get("headRefOid"), "pullRequest.headRefOid"),
    )


def claude_review_runs(owner: str, repo: str, head_oid: str) -> tuple[CheckRun, ...]:
    result = run(
        [
            "gh",
            "api",
            f"repos/{owner}/{repo}/commits/{head_oid}/check-runs"
            f"?check_name={REVIEW_CHECK_NAME}&per_page=100",
        ]
    )
    payload = json_object(json.loads(result.stdout), "check runs response")
    rows = json_list(payload.get("check_runs"), "check_runs")
    return tuple(check_run(json_object(row, "check run")) for row in rows)


def check_run(node: JsonObject) -> CheckRun:
    return CheckRun(
        status=json_str(node.get("status"), "check_run.status"),
        conclusion=json_optional_str(node.get("conclusion"), "check_run.conclusion"),
    )


def awaiting_reasons(
    runs: tuple[CheckRun, ...],
    reviews: tuple[Review, ...],
    comments: tuple[IssueComment, ...],
    head_oid: str,
) -> tuple[str, ...]:
    reasons = []
    claude = claude_awaiting(runs)
    if claude:
        reasons.append(claude)
    if not codex_reviewed(reviews, comments, head_oid):
        reasons.append(f"codex has not reviewed {head_oid[:10]}")
    return tuple(reasons)


def claude_awaiting(runs: tuple[CheckRun, ...]) -> str | None:
    if any(item.status == "completed" and item.conclusion == "success" for item in runs):
        return None
    if not runs:
        return f"{REVIEW_CHECK_NAME} has not started"
    if any(item.status != "completed" for item in runs):
        return f"{REVIEW_CHECK_NAME} is running"
    return f"{REVIEW_CHECK_NAME} did not succeed"


def codex_reviewed(
    reviews: tuple[Review, ...], comments: tuple[IssueComment, ...], head_oid: str
) -> bool:
    if any(
        item.reviewer.lower() in CODEX_REVIEWERS
        and item.commit_oid == head_oid
        and item.state != "DISMISSED"
        for item in reviews
    ):
        return True
    return any(
        comment.author.lower() in CODEX_REVIEWERS
        and CODEX_CLEAN_PASS in comment.body
        and any(
            head_oid.startswith(prefix) for prefix in REVIEWED_COMMIT_PATTERN.findall(comment.body)
        )
        for comment in comments
    )


def gate_state(blockers: list[Blocker], awaiting: tuple[str, ...]) -> tuple[str, str]:
    if blockers:
        return "failure", f"{len(blockers)} unresolved AI review item(s)"
    if awaiting:
        return "pending", f"Awaiting AI review: {'; '.join(awaiting)}"
    return "success", "AI review complete; no unresolved feedback"


def review_threads(owner: str, repo: str, number: int) -> tuple[ReviewThread, ...]:
    query = """
    query($owner: String!, $repo: String!, $number: Int!, $after: String) {
      repository(owner: $owner, name: $repo) {
        pullRequest(number: $number) {
          reviewThreads(first: 100, after: $after) {
            pageInfo { hasNextPage endCursor }
            nodes {
              isResolved
              isOutdated
              comments(first: 100) {
                nodes {
                  author { login }
                  body
                  url
                }
              }
            }
          }
        }
      }
    }
    """
    threads: list[ReviewThread] = []
    after: str | None = None
    while True:
        page = review_threads_page(
            graphql(query, {"owner": owner, "repo": repo, "number": number, "after": after})
        )
        threads.extend(page.threads)
        if not page.page_info.has_next_page:
            return tuple(threads)
        after = page.page_info.end_cursor


def pull_request_reviews(owner: str, repo: str, number: int) -> tuple[Review, ...]:
    query = """
    query($owner: String!, $repo: String!, $number: Int!, $after: String) {
      repository(owner: $owner, name: $repo) {
        pullRequest(number: $number) {
          reviews(first: 100, after: $after) {
            pageInfo { hasNextPage endCursor }
            nodes {
              author { login }
              body
              url
              state
              commit { oid }
            }
          }
        }
      }
    }
    """
    reviews: list[Review] = []
    after: str | None = None
    while True:
        page = reviews_page(
            graphql(query, {"owner": owner, "repo": repo, "number": number, "after": after})
        )
        reviews.extend(page.reviews)
        if not page.page_info.has_next_page:
            return tuple(reviews)
        after = page.page_info.end_cursor


def issue_comments(owner: str, repo: str, number: int) -> tuple[IssueComment, ...]:
    query = """
    query($owner: String!, $repo: String!, $number: Int!, $after: String) {
      repository(owner: $owner, name: $repo) {
        pullRequest(number: $number) {
          comments(first: 100, after: $after) {
            pageInfo { hasNextPage endCursor }
            nodes {
              author { login }
              body
            }
          }
        }
      }
    }
    """
    comments: list[IssueComment] = []
    after: str | None = None
    while True:
        page = issue_comments_page(
            graphql(query, {"owner": owner, "repo": repo, "number": number, "after": after})
        )
        comments.extend(page.comments)
        if not page.page_info.has_next_page:
            return tuple(comments)
        after = page.page_info.end_cursor


def graphql(query: str, variables: JsonObject) -> JsonObject:
    command = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        if value is None:
            continue
        flag = "-F" if isinstance(value, (int, bool)) else "-f"
        command.extend([flag, f"{key}={value}"])

    result = run(command)
    response = json_object(json.loads(result.stdout), "GraphQL response")
    errors = response.get("errors")
    if errors:
        raise SystemExit(json.dumps(errors, indent=2))
    return json_object(response.get("data"), "GraphQL data")


def pull_request_node(data: JsonObject) -> JsonObject:
    repository_node = json_object(data.get("repository"), "repository")
    return json_object(repository_node.get("pullRequest"), "pullRequest")


def page_info(node: JsonObject) -> PageInfo:
    raw = json_object(node.get("pageInfo"), "pageInfo")
    return PageInfo(
        has_next_page=json_bool(raw.get("hasNextPage"), "hasNextPage"),
        end_cursor=json_optional_str(raw.get("endCursor"), "endCursor"),
    )


def review_threads_page(data: JsonObject) -> ReviewThreadsPage:
    connection = json_object(pull_request_node(data).get("reviewThreads"), "reviewThreads")
    nodes = json_list(connection.get("nodes"), "reviewThreads.nodes")
    threads = tuple(review_thread(json_object(node, "review thread")) for node in nodes)
    return ReviewThreadsPage(threads=threads, page_info=page_info(connection))


def review_thread(node: JsonObject) -> ReviewThread:
    comments = json_object(node.get("comments"), "comments")
    comment_nodes = json_list(comments.get("nodes"), "comments.nodes")
    return ReviewThread(
        is_resolved=json_bool(node.get("isResolved"), "isResolved"),
        is_outdated=json_bool(node.get("isOutdated"), "isOutdated"),
        comments=tuple(
            review_comment(json_object(comment, "review comment")) for comment in comment_nodes
        ),
    )


def review_comment(node: JsonObject) -> ReviewComment:
    return ReviewComment(
        reviewer=author_login(node),
        body=json_str(node.get("body"), "comment.body"),
        url=json_str(node.get("url"), "comment.url"),
    )


def reviews_page(data: JsonObject) -> ReviewsPage:
    connection = json_object(pull_request_node(data).get("reviews"), "reviews")
    nodes = json_list(connection.get("nodes"), "reviews.nodes")
    reviews = tuple(review(json_object(node, "review")) for node in nodes)
    return ReviewsPage(reviews=reviews, page_info=page_info(connection))


def review(node: JsonObject) -> Review:
    commit = node.get("commit")
    return Review(
        reviewer=author_login(node),
        body=json_str(node.get("body"), "review.body"),
        url=json_str(node.get("url"), "review.url"),
        state=json_str(node.get("state"), "review.state"),
        commit_oid=(
            json_str(json_object(commit, "review.commit").get("oid"), "commit.oid")
            if commit is not None
            else None
        ),
    )


def issue_comments_page(data: JsonObject) -> IssueCommentsPage:
    connection = json_object(pull_request_node(data).get("comments"), "comments")
    nodes = json_list(connection.get("nodes"), "comments.nodes")
    comments = tuple(issue_comment(json_object(node, "issue comment")) for node in nodes)
    return IssueCommentsPage(comments=comments, page_info=page_info(connection))


def issue_comment(node: JsonObject) -> IssueComment:
    return IssueComment(
        author=author_login(node),
        body=json_str(node.get("body"), "comment.body"),
    )


def author_login(node: JsonObject) -> str:
    author = node.get("author")
    if author is None:
        return ""
    return json_str(json_object(author, "author").get("login"), "author.login")


def unresolved_thread_blockers(threads: tuple[ReviewThread, ...]) -> list[Blocker]:
    blockers: list[Blocker] = []
    for thread in threads:
        if thread.is_resolved:
            continue
        for comment in thread.comments:
            if comment.reviewer.lower() not in AI_REVIEWERS:
                continue
            reason = "unresolved review thread"
            if thread.is_outdated:
                reason += " (outdated)"
            blockers.append(
                Blocker(
                    reviewer=comment.reviewer,
                    reason=reason,
                    url=comment.url,
                    detail=snippet(comment.body),
                )
            )
            break
    return blockers


def changes_requested_blockers(reviews: tuple[Review, ...], head_oid: str) -> list[Blocker]:
    latest_by_author: dict[str, Review] = {}
    for item in reviews:
        reviewer = item.reviewer.lower()
        if reviewer in AI_REVIEWERS and item.commit_oid == head_oid:
            latest_by_author[reviewer] = item

    blockers: list[Blocker] = []
    for item in sorted(latest_by_author.values(), key=lambda review_item: review_item.reviewer):
        if item.state != "CHANGES_REQUESTED":
            continue
        blockers.append(
            Blocker(
                reviewer=item.reviewer,
                reason="latest review requests changes",
                url=item.url,
                detail=snippet(item.body),
            )
        )
    return blockers


def publish_status(
    owner: str, repo: str, pull_request: PullRequest, state: str, description: str
) -> None:
    command = [
        "gh",
        "api",
        f"repos/{owner}/{repo}/statuses/{pull_request.head_oid}",
        "-f",
        f"state={state}",
        "-f",
        f"context={STATUS_CONTEXT}",
        "-f",
        f"description={description[:140]}",
    ]
    target_url = status_target_url(pull_request)
    if target_url:
        command.extend(["-f", f"target_url={target_url}"])
    run(command)


def status_target_url(pull_request: PullRequest) -> str:
    server_url = os.environ.get("GITHUB_SERVER_URL")
    repository_name = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if server_url and repository_name and run_id:
        return f"{server_url}/{repository_name}/actions/runs/{run_id}"
    return pull_request.url


def print_result(
    pull_request: PullRequest, blockers: list[Blocker], awaiting: tuple[str, ...]
) -> None:
    if not blockers:
        if awaiting:
            print(f"PR #{pull_request.number} is awaiting AI review: {'; '.join(awaiting)}.")
        else:
            print(f"No unresolved AI review feedback found on PR #{pull_request.number}.")
        return
    print(f"AI review gate found {len(blockers)} blocking item(s) on PR #{pull_request.number}:")
    for blocker in blockers:
        print(f"- {blocker.reviewer}: {blocker.reason}")
        print(f"  {blocker.url}")
        if blocker.detail:
            print(f"  {blocker.detail}")


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


def json_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise SystemExit(f"{name} must be a boolean")
    return value


def snippet(body: str) -> str:
    text = " ".join(body.strip().split())
    return text[:220] + ("..." if len(text) > 220 else "")


if __name__ == "__main__":
    raise SystemExit(main())

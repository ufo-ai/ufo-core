#!/usr/bin/env python3
"""Publish an AI-review gate status on PR head commits."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

AI_REVIEWERS = frozenset(
    {
        "codex",
        "codex[bot]",
        "openai-codex[bot]",
        "chatgpt-codex-connector",
        "claude",
        "claude[bot]",
        "claude-code[bot]",
    }
)
STATUS_CONTEXT = "AI Review Gate"

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


@dataclass(frozen=True)
class ReviewsPage:
    reviews: tuple[Review, ...]
    page_info: PageInfo


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
    saw_blockers = False
    for number in numbers:
        pull_request = fetch_pull_request(owner, repo, number)
        blockers = gate_blockers(owner, repo, number)
        saw_blockers = saw_blockers or bool(blockers)
        print_result(pull_request, blockers)
        if dry_run:
            continue
        publish_status(owner, repo, pull_request, blockers)

    return 1 if dry_run and saw_blockers else 0


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


def gate_blockers(owner: str, repo: str, number: int) -> list[Blocker]:
    threads = review_threads(owner, repo, number)
    reviews = pull_request_reviews(owner, repo, number)
    blockers = unresolved_thread_blockers(threads)
    blockers.extend(changes_requested_blockers(reviews))
    return blockers


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
    author = json_object(node.get("author"), "comment author")
    return ReviewComment(
        reviewer=json_str(author.get("login"), "author.login"),
        body=json_str(node.get("body"), "comment.body"),
        url=json_str(node.get("url"), "comment.url"),
    )


def reviews_page(data: JsonObject) -> ReviewsPage:
    connection = json_object(pull_request_node(data).get("reviews"), "reviews")
    nodes = json_list(connection.get("nodes"), "reviews.nodes")
    reviews = tuple(review(json_object(node, "review")) for node in nodes)
    return ReviewsPage(reviews=reviews, page_info=page_info(connection))


def review(node: JsonObject) -> Review:
    author = json_object(node.get("author"), "review author")
    return Review(
        reviewer=json_str(author.get("login"), "author.login"),
        body=json_str(node.get("body"), "review.body"),
        url=json_str(node.get("url"), "review.url"),
        state=json_str(node.get("state"), "review.state"),
    )


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


def changes_requested_blockers(reviews: tuple[Review, ...]) -> list[Blocker]:
    latest_by_author: dict[str, Review] = {}
    for item in reviews:
        reviewer = item.reviewer.lower()
        if reviewer in AI_REVIEWERS:
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
    owner: str, repo: str, pull_request: PullRequest, blockers: list[Blocker]
) -> None:
    state = "failure" if blockers else "success"
    description = (
        f"{len(blockers)} unresolved AI review item(s)"
        if blockers
        else "No unresolved AI review feedback"
    )
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


def print_result(pull_request: PullRequest, blockers: list[Blocker]) -> None:
    if not blockers:
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

import json
from uuid import UUID, uuid4

import httpx
from starlette.routing import compile_path
from ufo_ext_coding.review_checkout import (
    CODE_REVIEW_PROFILE_NAME,
    CodeReviewFinding,
    CodeReviewOutput,
)
from ufo_ext_coding.review_publish import (
    CHECK_NAME,
    CHECK_SUMMARY_MAX_CHARS,
    CHECKS_PAGE_SIZE,
    GitHubCheckPublisher,
    render_check_summary,
    review_conversation_url,
)
from ufo_ext_coding.review_routing import StoredReviewRun
from ufo_ext_web.surface import PORTAL_PATH, ROUTES, subagent_conversation

REVIEW_BASE_URL = "https://app.example.com"
REVIEW_CONVERSATION = UUID("63edf3d9-f12c-4ceb-aed1-1bb41312a8ad")
REVIEW_URL = (
    f"{REVIEW_BASE_URL}{PORTAL_PATH}#/subagents/{CODE_REVIEW_PROFILE_NAME}"
    f"/conversations/{REVIEW_CONVERSATION}"
)


def _run(review_conversation_id: UUID | None = REVIEW_CONVERSATION) -> StoredReviewRun:
    return StoredReviewRun(
        run_id=uuid4(),
        repository="metalcraftai/ufo",
        pull_request_number=1237,
        base_sha="b" * 40,
        head_sha="a" * 40,
        conversation_id=uuid4(),
        review_conversation_id=review_conversation_id,
    )


async def test_publisher_creates_action_required_exact_head_check_for_severe_defect() -> None:
    requests: list[httpx.Request] = []

    def github(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"check_runs": []})
        return httpx.Response(201, json={"id": 91})

    run = _run()
    check_id = await GitHubCheckPublisher(
        "secret", REVIEW_URL, transport=httpx.MockTransport(github)
    ).publish(
        run,
        CodeReviewOutput(
            findings=(
                CodeReviewFinding(
                    path="core/review.py",
                    line=17,
                    title="Wrong comparison",
                    trigger="Publish after the pull request head changes.",
                    failure="The published SHA differs from the reviewed SHA.",
                    impact="materially incorrect result or state for a supported workflow",
                ),
            ),
        ),
    )

    assert check_id == 91
    assert [request.method for request in requests] == ["GET", "POST"]
    assert requests[0].url.params["check_name"] == CHECK_NAME
    assert requests[0].url.params["filter"] == "all"
    assert requests[0].url.params["per_page"] == str(CHECKS_PAGE_SIZE)
    body = json.loads(requests[1].content)
    assert body["name"] == CHECK_NAME
    assert body["head_sha"] == run.head_sha
    assert body["status"] == "completed"
    assert body["conclusion"] == "action_required"
    assert body["external_id"] == str(run.run_id)
    assert "`core/review.py:17` Wrong comparison" in body["output"]["summary"]
    assert "Trigger: Publish after the pull request head changes." in body["output"]["summary"]
    assert "Failure: The published SHA differs from the reviewed SHA." in body["output"]["summary"]
    assert (
        "Impact: Materially incorrect result or state for a supported workflow."
        in body["output"]["summary"]
    )
    assert body["output"]["summary"].endswith(
        "  Impact: Materially incorrect result or state for a supported workflow.\n"
        f"\nReview conversation: {REVIEW_URL}"
    )
    assert body["details_url"] == REVIEW_URL
    assert requests[1].headers["Authorization"] == "Bearer secret"


async def test_publisher_reconciles_existing_run_by_external_id() -> None:
    requests: list[httpx.Request] = []
    run = _run()

    def github(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"check_runs": [{"id": 42, "external_id": str(run.run_id)}]},
            )
        return httpx.Response(200, json={"id": 42})

    check_id = await GitHubCheckPublisher(
        "secret", REVIEW_URL, transport=httpx.MockTransport(github)
    ).publish(run, CodeReviewOutput())

    assert check_id == 42
    assert [request.method for request in requests] == ["GET", "PATCH"]
    assert requests[1].url.path.endswith("/check-runs/42")
    body = json.loads(requests[1].content)
    assert body["conclusion"] == "success"
    assert body["external_id"] == str(run.run_id)
    assert (
        body["output"]["summary"] == f"No severe defect found.\n\nReview conversation: {REVIEW_URL}"
    )
    assert body["details_url"] == REVIEW_URL
    assert "head_sha" not in body


async def test_publisher_sends_no_link_when_no_conversation_ran_the_review() -> None:
    requests: list[httpx.Request] = []

    def github(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"check_runs": []})
        return httpx.Response(201, json={"id": 7})

    run = _run(review_conversation_id=None)
    await GitHubCheckPublisher(
        "secret",
        review_conversation_url(REVIEW_BASE_URL, run),
        transport=httpx.MockTransport(github),
    ).publish(run, CodeReviewOutput())

    body = json.loads(requests[1].content)
    assert body["output"]["summary"] == "No severe defect found."
    assert "details_url" not in body


def test_review_conversation_url_names_the_reviewer_transcript() -> None:
    assert review_conversation_url(f"{REVIEW_BASE_URL}/", _run()) == REVIEW_URL
    assert review_conversation_url(REVIEW_BASE_URL, _run()) == REVIEW_URL
    assert review_conversation_url(None, _run()) is None
    assert review_conversation_url(REVIEW_BASE_URL, _run(None)) is None


def test_the_published_link_opens_the_portal_read_that_serves_the_reviewer_run() -> None:
    """The recorded conversation's surface is the subagent one, which only a profile's run page
    opens, and every portal page reads the panel route its own fragment names — so the registered
    route table answers whether a published link opens anything. This one opens exactly when its
    fragment resolves to that single read, carrying the reviewer's profile and its conversation."""
    url = review_conversation_url(REVIEW_BASE_URL, _run())
    assert url is not None
    base, _, fragment = url.partition("#")

    assert base == f"{REVIEW_BASE_URL}{PORTAL_PATH}"
    assert [
        (route.handler, opened.groupdict())
        for route in ROUTES
        if route.method == "GET"
        and (opened := compile_path(f"/{route.path}")[0].fullmatch(fragment))
    ] == [
        (
            subagent_conversation,
            {"subagent": CODE_REVIEW_PROFILE_NAME, "conversation_id": str(REVIEW_CONVERSATION)},
        )
    ]


def test_check_summary_is_bounded_next_to_publication() -> None:
    review = CodeReviewOutput(
        findings=(
            CodeReviewFinding(
                path="core/review.py",
                line=17,
                title="x" * (CHECK_SUMMARY_MAX_CHARS + 10),
                trigger="trigger",
                failure="failure",
                impact="the code fails to build or breaks required CI",
            ),
        )
    )

    assert len(render_check_summary(review)) == CHECK_SUMMARY_MAX_CHARS
    linked = render_check_summary(review, REVIEW_URL)
    assert len(linked) == CHECK_SUMMARY_MAX_CHARS
    assert linked.endswith(f"\n\nReview conversation: {REVIEW_URL}")

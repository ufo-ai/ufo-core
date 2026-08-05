import json
from uuid import uuid4

import httpx
from ufo_ext_coding.review_checkout import CodeReviewFinding, CodeReviewOutput
from ufo_ext_coding.review_publish import (
    CHECK_NAME,
    CHECK_SUMMARY_MAX_CHARS,
    CHECKS_PAGE_SIZE,
    GitHubCheckPublisher,
    render_check_summary,
)
from ufo_ext_coding.review_routing import StoredReviewRun


def _run() -> StoredReviewRun:
    return StoredReviewRun(
        run_id=uuid4(),
        repository="metalcraftai/ufo",
        pull_request_number=1237,
        base_sha="b" * 40,
        head_sha="a" * 40,
        conversation_id=uuid4(),
    )


async def test_publisher_creates_neutral_exact_head_check() -> None:
    requests: list[httpx.Request] = []

    def github(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"check_runs": []})
        return httpx.Response(201, json={"id": 91})

    run = _run()
    check_id = await GitHubCheckPublisher("secret", transport=httpx.MockTransport(github)).publish(
        run,
        CodeReviewOutput(
            summary="One defect.",
            findings=(
                CodeReviewFinding(
                    path="core/review.py",
                    line=17,
                    severity="P1",
                    title="Wrong comparison",
                    body="The published SHA differs from the reviewed SHA.",
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
    assert body["conclusion"] == "neutral"
    assert body["external_id"] == str(run.run_id)
    assert "P1 `core/review.py:17` Wrong comparison" in body["output"]["summary"]
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

    check_id = await GitHubCheckPublisher("secret", transport=httpx.MockTransport(github)).publish(
        run, CodeReviewOutput(summary="Clear.")
    )

    assert check_id == 42
    assert [request.method for request in requests] == ["GET", "PATCH"]
    assert requests[1].url.path.endswith("/check-runs/42")
    body = json.loads(requests[1].content)
    assert body["conclusion"] == "neutral"
    assert body["external_id"] == str(run.run_id)
    assert "head_sha" not in body


def test_check_summary_is_bounded_next_to_publication() -> None:
    summary = render_check_summary(CodeReviewOutput(summary="x" * (CHECK_SUMMARY_MAX_CHARS + 10)))
    assert len(summary) == CHECK_SUMMARY_MAX_CHARS

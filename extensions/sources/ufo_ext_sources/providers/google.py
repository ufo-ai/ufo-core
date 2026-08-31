"""Which of two unrelated things a Google API's `401`/`403` names, for the connectors reading one.

Every Google API answers a refusal in one envelope, and the status alone does not separate them. A
grant that lacks the scope is settled: no retry widens it, so the stream is skipped, and the driver
parks the source once it has counted enough of those refusals (`ufo.runtime.sources.sync`). A usage
limit is
the opposite — `RESOURCE_EXHAUSTED`, or one of Google's `usageLimits` reasons, on that same `403` —
and it clears as the quota window rolls, so it fails the run and takes the error backoff, which
retries and recovers with nobody in it. Skipping on one would spend the park threshold on a stream
about to come back, and put it out of the driver's reach until someone reconnected an account that
was never the problem."""

from typing import Any

import httpx

from ufo.sdk.sources import dict_or_empty, list_or_empty

REFUSAL_STATUS = frozenset({401, 403})
QUOTA_STATUS = "RESOURCE_EXHAUSTED"
QUOTA_REASONS = frozenset(
    {
        "dailyLimitExceeded",
        "quotaExceeded",
        "rateLimitExceeded",
        "userRateLimitExceeded",
    }
)


def error_detail(error: httpx.HTTPStatusError) -> dict[str, Any]:
    """The `error` object of a Google API error body — empty when the response carries none, which
    is what a refusal from in front of the API looks like."""
    try:
        body = error.response.json()
    except ValueError:
        return {}
    return dict_or_empty(dict_or_empty(body).get("error"))


def is_quota_refusal(detail: dict[str, Any]) -> bool:
    """Whether a refusal names a usage limit rather than the grant."""
    if detail.get("status") == QUOTA_STATUS:
        return True
    return any(item.get("reason") in QUOTA_REASONS for item in list_or_empty(detail.get("errors")))


def refused_for_scope(error: httpx.HTTPStatusError) -> bool:
    """Whether this status error is the settled kind — a `401`/`403` the grant itself cannot answer,
    which the caller turns into `StreamSkipped`. A quota refusal answers False and is raised."""
    return error.response.status_code in REFUSAL_STATUS and not is_quota_refusal(
        error_detail(error)
    )

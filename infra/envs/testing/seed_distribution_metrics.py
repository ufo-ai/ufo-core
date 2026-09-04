#!/usr/bin/env python3
"""Create the named distribution metrics in Datadog, so the apply can configure tags on them.

Datadog refuses `POST /api/v2/metrics/<name>/tags` with `400 cannot configure tags on a distribution
metric that does not exist` until it holds one point under the name, and a new histogram's first
point comes from the fleet build the same apply rolls out. So the deploy that adds a histogram fails
on its own new `datadog_metric_tag_configuration`, and stays failed until some workspace happens to
emit that step — every deploy behind it blocked on one metric nothing has reported yet.

A metadata write against such a name is answered 404, so the same apply also fails on the metric's
`datadog_metric_metadata`. `metrics.tf` runs this on the create of each metric's seed, ahead of
every resource that reads the name. One point creates the name; it is a zero millisecond on a
latency distribution that has reported nothing. A name Datadog already holds is left alone, so a
re-created seed submits nothing.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

API_BASE_URL = "https://api.us5.datadoghq.com"
API_KEY_ENV = "DD_API_KEY"
APP_KEY_ENV = "DD_APP_KEY"
SEED_HOST = "github-actions"
SEED_TAGS = ["env:testing"]
SEED_VALUE = 0.0
REQUEST_TIMEOUT_SECONDS = 30.0
VISIBLE_TIMEOUT_SECONDS = 120.0
POLL_SECONDS = 5.0
NOT_FOUND = 404
REFUSED = 400


def _call(
    base_url: str, method: str, path: str, headers: dict[str, str], payload: object = None
) -> int:
    """The status Datadog answered, with a refusal reported as its status rather than raised."""
    body = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(f"{base_url}{path}", data=body, method=method)
    for name, value in headers.items():
        request.add_header(name, value)
    if body is not None:
        request.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as answer:
            return int(answer.status)
    except urllib.error.HTTPError as refusal:
        return int(refusal.code)


def metric_exists(base_url: str, headers: dict[str, str], metric: str) -> bool:
    """Whether Datadog holds the metric name. `all-tags` reads the name itself rather than its tag
    configuration, which answers 404 for exactly the metric this script exists to create."""
    status = _call(base_url, "GET", f"/api/v2/metrics/{metric}/all-tags", headers)
    if status == NOT_FOUND:
        return False
    if status >= REFUSED:
        raise SystemExit(f"datadog GET all-tags {metric}: {status}")
    return True


def seed_metric(base_url: str, headers: dict[str, str], metric: str) -> None:
    """Submit one point, which is what creates the name."""
    status = _call(
        base_url,
        "POST",
        "/api/v1/distribution_points",
        headers,
        {
            "series": [
                {
                    "metric": metric,
                    "points": [[int(time.time()), [SEED_VALUE]]],
                    "host": SEED_HOST,
                    "tags": SEED_TAGS,
                }
            ]
        },
    )
    if status >= REFUSED:
        raise SystemExit(f"datadog POST distribution_points {metric}: {status}")


def await_metric(base_url: str, headers: dict[str, str], metric: str) -> None:
    """Wait until the seeded name reads back, so the tag configuration cannot race the intake."""
    deadline = time.monotonic() + VISIBLE_TIMEOUT_SECONDS
    while not metric_exists(base_url, headers, metric):
        if time.monotonic() >= deadline:
            raise SystemExit(f"{metric} is still unknown to datadog after its seed point")
        time.sleep(POLL_SECONDS)


def main(metrics: list[str], base_url: str = API_BASE_URL) -> int:
    api_key = os.environ.get(API_KEY_ENV, "")
    app_key = os.environ.get(APP_KEY_ENV, "")
    if not api_key or not app_key:
        print(f"{API_KEY_ENV} and {APP_KEY_ENV} are required to seed a metric", file=sys.stderr)
        return 1
    headers = {"DD-API-KEY": api_key, "DD-APPLICATION-KEY": app_key}
    for metric in metrics:
        if metric_exists(base_url, headers, metric):
            print(f"{metric} already exists")
            continue
        seed_metric(base_url, headers, metric)
        await_metric(base_url, headers, metric)
        print(f"{metric} seeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

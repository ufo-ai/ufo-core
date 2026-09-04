import json

import httpx
import pytest

from infra.seed_metrics import (
    API_KEY_ENV,
    APP_KEY_ENV,
    METRICS_TF,
    MetricSeeding,
    configured_metrics,
    main,
)
from ufo.harness.o11y import HISTOGRAMS

KNOWN = "ufo.turn_ms"
UNKNOWN = "ufo.onboarding_step_latency_ms"


class _Datadog:
    """The metric index Datadog answers `/api/v1/search` from: a name appears `lag` reads after the
    point that created it, the way the real index trails a submission."""

    def __init__(self, known: set[str], lag: int = 0) -> None:
        self.known = known
        self.lag = lag
        self.submitted: list[str] = []
        self.searched: list[str] = []
        self.pending: dict[str, int] = {}

    def client(self) -> httpx.Client:
        return httpx.Client(base_url="https://datadog.test", transport=httpx.MockTransport(self))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/search":
            name = request.url.params["q"].removeprefix("metrics:")
            self.searched.append(name)
            if name in self.pending:
                self.pending[name] -= 1
                if self.pending[name] <= 0:
                    self.known.add(name)
            found = [name] if name in self.known else None
            return httpx.Response(200, json={"results": {"metrics": found}})
        if request.url.path == "/api/v1/distribution_points":
            body = json.loads(request.content)
            for series in body["series"]:
                self.submitted.append(series["metric"])
                self.pending[series["metric"]] = self.lag
                if self.lag <= 0:
                    self.known.add(series["metric"])
            return httpx.Response(202, json={"status": "ok"})
        return httpx.Response(404)


def _seeding(datadog: _Datadog, *names: str) -> MetricSeeding:
    return MetricSeeding(
        client=datadog.client(), names=names, poll_seconds=0.0, timeout_seconds=1.0
    )


def _check_a_name_datadog_already_queries_is_left_alone() -> None:
    datadog = _Datadog({KNOWN})
    assert _seeding(datadog, KNOWN).run() == ()
    assert datadog.submitted == []


def _check_an_unknown_name_is_submitted_once_and_waited_for() -> None:
    """The point is what makes the name exist; the wait is what makes the tag configuration that
    follows in the same job apply against it rather than 400."""
    datadog = _Datadog({KNOWN}, lag=3)
    assert _seeding(datadog, KNOWN, UNKNOWN).run() == (UNKNOWN,)
    assert datadog.submitted == [UNKNOWN]
    assert datadog.searched.count(UNKNOWN) == 4


def _check_a_name_datadog_never_queries_stops_the_deploy() -> None:
    """Terraform would refuse the tag configuration a minute later with a 400 naming neither the
    metric's absence nor the release that introduced it."""
    datadog = _Datadog(set(), lag=10**6)
    with pytest.raises(RuntimeError, match=f"accepted a point for {UNKNOWN}"):
        _seeding(datadog, UNKNOWN).run()


def _check_a_datadog_fault_fails_loud() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    seeding = MetricSeeding(
        client=httpx.Client(base_url="https://datadog.test", transport=httpx.MockTransport(refuse)),
        names=(UNKNOWN,),
    )
    with pytest.raises(RuntimeError, match=r"datadog GET /api/v1/search .*403 forbidden"):
        seeding.run()


def _check_every_configured_metric_is_seeded() -> None:
    """Both ends of the seeding: the names read here are exactly the tag configurations the apply
    creates, so a histogram added to `metrics.tf` is seeded without this script being touched."""
    assert configured_metrics(METRICS_TF.read_text()) == tuple(
        sorted(f"ufo.{name}" for name in HISTOGRAMS)
    )


def _check_a_metrics_file_declaring_no_tag_configuration_fails_loud() -> None:
    with pytest.raises(RuntimeError, match="declares no datadog_metric_tag_configuration"):
        configured_metrics(
            'resource "datadog_metric_metadata" "turn_ms" {\n  metric = "ufo.turn_ms"\n}\n'
        )


def _check_main_requires_both_datadog_keys() -> None:
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv(API_KEY_ENV, raising=False)
        patch.delenv(APP_KEY_ENV, raising=False)
        with pytest.raises(RuntimeError, match=f"{API_KEY_ENV}, {APP_KEY_ENV} must be set"):
            main()


def _check_main_rejects_arguments() -> None:
    with pytest.raises(RuntimeError, match=r"usage: seed_metrics\.py"):
        main(("unknown",))


def test_seed_metrics_contract() -> None:
    checks = tuple(value for name, value in globals().items() if name.startswith("_check_"))
    assert len(checks) == 8
    for check in checks:
        check()

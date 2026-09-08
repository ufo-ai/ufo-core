"""Give Datadog every metric name `metrics.tf` configures, before terraform configures it.

Datadog refuses a tag configuration for a metric it has received no point for, and a distribution a
release introduces has sent none: `ufo.onboarding_step_latency_ms` reaches the pipeline only when an
onboarding step lands, which can be days after the image that emits it rolled. So the apply that
ships an emitter cannot also create its configuration — and because every push to main applies this
one root, that refusal fails every deploy queued behind it, not only its own.

One point per unknown name settles it: the name becomes queryable, the configuration applies in the
same release as its emitter, and the first real point lands on a metric whose percentiles already
resolve. The point carries no tags, so it sits outside every `env`-scoped graph reading the metric,
and it is submitted once — a name Datadog already knows is left alone.
"""

import os
import re
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx

DATADOG_API_URL = "https://api.us5.datadoghq.com"
API_KEY_ENV = "DD_API_KEY"
APP_KEY_ENV = "DD_APP_KEY"
METRICS_TF = Path(__file__).parent / "envs/testing/metrics.tf"
TAG_CONFIGURATION_METRIC = re.compile(r'^\s*metric_name\s*=\s*"([^"]+)"', re.MULTILINE)
REQUEST_TIMEOUT_SECONDS = 30.0
INDEX_POLL_SECONDS = 5.0
INDEX_TIMEOUT_SECONDS = 300.0
SEED_MILLISECONDS = 0.0


def configured_metrics(source: str) -> tuple[str, ...]:
    """Every metric `metrics.tf` gives a tag configuration — the resources whose create fails while
    Datadog does not know the name."""
    names = tuple(sorted(set(TAG_CONFIGURATION_METRIC.findall(source))))
    if not names:
        raise RuntimeError(f"{METRICS_TF} declares no datadog_metric_tag_configuration")
    return names


@dataclass(frozen=True)
class MetricSeeding:
    client: httpx.Client
    names: tuple[str, ...]
    poll_seconds: float = INDEX_POLL_SECONDS
    timeout_seconds: float = INDEX_TIMEOUT_SECONDS

    def run(self) -> tuple[str, ...]:
        """The names Datadog did not know, submitted and then queryable."""
        seeded = tuple(name for name in self.names if not self._queryable(name))
        for name in seeded:
            self._submit(name)
        for name in seeded:
            self._await_query(name)
        return seeded

    def _queryable(self, name: str) -> bool:
        answer = self.client.get("/api/v1/search", params={"q": f"metrics:{name}"})
        if answer.status_code >= 400:
            raise RuntimeError(
                f"datadog GET /api/v1/search {name}: {answer.status_code} {answer.text}"
            )
        return name in (answer.json()["results"]["metrics"] or ())

    def _submit(self, name: str) -> None:
        answer = self.client.post(
            "/api/v1/distribution_points",
            json={
                "series": [
                    {"metric": name, "points": [[int(time.time()), [SEED_MILLISECONDS]]]},
                ]
            },
        )
        if answer.status_code >= 400:
            raise RuntimeError(
                f"datadog POST /api/v1/distribution_points {name}: "
                f"{answer.status_code} {answer.text}"
            )

    def _await_query(self, name: str) -> None:
        deadline = time.monotonic() + self.timeout_seconds
        while not self._queryable(name):
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"datadog accepted a point for {name} but does not query it after "
                    f"{self.timeout_seconds:.0f}s; its tag configuration would be refused"
                )
            time.sleep(self.poll_seconds)


def main(arguments: Sequence[str] = ()) -> None:
    if arguments:
        raise RuntimeError("usage: seed_metrics.py")
    unset = tuple(name for name in (API_KEY_ENV, APP_KEY_ENV) if not os.environ.get(name))
    if unset:
        raise RuntimeError(f"{', '.join(unset)} must be set to seed metric names")
    with httpx.Client(
        base_url=DATADOG_API_URL,
        headers={
            "DD-API-KEY": os.environ[API_KEY_ENV],
            "DD-APPLICATION-KEY": os.environ[APP_KEY_ENV],
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    ) as client:
        seeded = MetricSeeding(
            client=client, names=configured_metrics(METRICS_TF.read_text())
        ).run()
    print(", ".join(seeded) if seeded else "every configured metric name is queryable")


if __name__ == "__main__":
    main(sys.argv[1:])

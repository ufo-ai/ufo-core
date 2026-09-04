import importlib.util
import json
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from types import ModuleType

import pytest

ROOT = Path(__file__).parents[2]
SEED = ROOT / "infra" / "envs" / "testing" / "seed_distribution_metrics.py"
METRIC = "ufo.onboarding_step_latency_ms"
KEYS = {"DD-API-KEY": "dd-key", "DD-APPLICATION-KEY": "dd-app-key"}


def _seed() -> ModuleType:
    spec = importlib.util.spec_from_file_location("seed_distribution_metrics", SEED)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.POLL_SECONDS = 0.0
    return module


def _a_metrics_intake(
    status: Callable[[str, str], int],
) -> tuple[list[dict[str, object]], HTTPServer]:
    """A stand-in Datadog metrics API answering `status(method, path)`, and what reached it."""
    received: list[dict[str, object]] = []

    class Intake(BaseHTTPRequestHandler):
        def _answer(self) -> None:
            length = int(self.headers.get("content-length") or 0)
            received.append(
                {
                    "method": self.command,
                    "path": self.path,
                    "api_key": self.headers["DD-API-KEY"],
                    "app_key": self.headers["DD-APPLICATION-KEY"],
                    "body": json.loads(self.rfile.read(length)) if length else None,
                }
            )
            self.send_response(status(self.command, self.path))
            self.send_header("content-length", "0")
            self.end_headers()

        do_GET = _answer
        do_POST = _answer

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Intake)
    Thread(target=server.serve_forever, daemon=True).start()
    return received, server


def _base_url(server: HTTPServer) -> str:
    return f"http://127.0.0.1:{server.server_port}"


def _check_a_metric_datadog_already_holds_is_not_seeded() -> None:
    """Every apply after the first re-creates no seed, but a fresh state re-runs all of them, and a
    point per run would be a zero millisecond dropped into a live latency distribution each time."""
    received, server = _a_metrics_intake(lambda method, path: 200)
    try:
        assert _seed().main([METRIC], _base_url(server)) == 0
    finally:
        server.shutdown()
    assert [(call["method"], call["path"]) for call in received] == [
        ("GET", f"/api/v2/metrics/{METRIC}/all-tags")
    ]
    assert received[0]["api_key"] == KEYS["DD-API-KEY"]
    assert received[0]["app_key"] == KEYS["DD-APPLICATION-KEY"]


def _check_an_unknown_metric_is_created_by_one_point_and_waited_for() -> None:
    """The tag configuration follows immediately, so a name submitted and not yet queryable fails
    the apply exactly as an unseeded one does."""
    reads = 0

    def status(method: str, path: str) -> int:
        nonlocal reads
        if method == "POST":
            return 202
        reads += 1
        return 404 if reads < 3 else 200

    received, server = _a_metrics_intake(status)
    try:
        assert _seed().main([METRIC], _base_url(server)) == 0
    finally:
        server.shutdown()
    submissions = [call for call in received if call["method"] == "POST"]
    assert len(submissions) == 1
    assert submissions[0]["path"] == "/api/v1/distribution_points"
    body = submissions[0]["body"]
    assert isinstance(body, dict)
    assert body["series"][0]["metric"] == METRIC
    assert body["series"][0]["points"][0][1] == [0.0]
    assert [call["method"] for call in received] == ["GET", "POST", "GET", "GET"]


def _check_a_name_that_never_appears_fails_the_apply_loud() -> None:
    """Silence here hands the apply the same 400 the seed exists to prevent, under a resource that
    created cleanly."""
    module = _seed()
    module.VISIBLE_TIMEOUT_SECONDS = 0.0
    _, server = _a_metrics_intake(lambda method, path: 404 if method == "GET" else 202)
    try:
        with pytest.raises(SystemExit, match="still unknown to datadog"):
            module.main([METRIC], _base_url(server))
    finally:
        server.shutdown()


def _check_a_refused_read_or_submission_fails_rather_than_reads_as_absent() -> None:
    """A key Datadog refuses answers 403 to both calls: read as "absent" the seed submits forever,
    and read as "submitted" the apply runs straight into the refusal."""
    module = _seed()
    _, server = _a_metrics_intake(lambda method, path: 403)
    try:
        with pytest.raises(SystemExit, match="datadog GET all-tags"):
            module.main([METRIC], _base_url(server))
        with pytest.raises(SystemExit, match="datadog POST distribution_points"):
            module.seed_metric(_base_url(server), dict(KEYS), METRIC)
    finally:
        server.shutdown()


def _check_a_run_without_both_keys_reports_the_fault_rather_than_seeding() -> None:
    """The app key reads and the api key submits, so one alone would call an endpoint it cannot
    reach and report the refusal as the metric's fault."""
    module = _seed()
    received, server = _a_metrics_intake(lambda method, path: 200)
    try:
        assert module.main([METRIC], _base_url(server)) == 1
    finally:
        server.shutdown()
    assert received == []


def test_seed_distribution_metrics_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    checks = tuple(value for name, value in globals().items() if name.startswith("_check_"))
    assert len(checks) == 5
    for check in checks:
        if check is _check_a_run_without_both_keys_reports_the_fault_rather_than_seeding:
            monkeypatch.delenv("DD_API_KEY", raising=False)
            monkeypatch.delenv("DD_APP_KEY", raising=False)
        else:
            monkeypatch.setenv("DD_API_KEY", KEYS["DD-API-KEY"])
            monkeypatch.setenv("DD_APP_KEY", KEYS["DD-APPLICATION-KEY"])
        check()

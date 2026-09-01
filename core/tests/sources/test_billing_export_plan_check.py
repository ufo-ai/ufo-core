import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[3]
CHECK = ROOT / ".github" / "scripts" / "billing_export_plan_check.py"
GATE = ROOT / ".github" / "scripts" / "billing_export_plan_gate.sh"
RUN_URL = "https://github.com/metalcraftai/ufo/actions/runs/30120902872"


def _check_module():
    spec = importlib.util.spec_from_file_location("billing_export_plan_check", CHECK)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _explained(node: dict[str, object], execution_ms: float = 0.391) -> dict[str, object]:
    return {"Plan": {"Node Type": "Result", "Plans": [node]}, "Execution Time": execution_ms}


def _index_node(actual_rows: int = 0, index: str = "ledger_export_pkey") -> dict[str, object]:
    return {
        "Node Type": "Index Scan",
        "Relation Name": "ledger_export",
        "Index Name": index,
        "Actual Rows": actual_rows,
        "Actual Loops": 1,
    }


def test_live_plan_accepts_only_the_app_role_primary_key_path() -> None:
    result = _check_module().evaluate_plan(_explained(_index_node()), "ufo_serve", "query-digest")

    assert result == {
        "status": "ok",
        "db_role": "ufo_serve",
        "relation": "ledger_export",
        "index": "ledger_export_pkey",
        "access_method": "index_scan",
        "execution_ms": 0.391,
        "threshold_ms": 10.0,
        "seq_scan": False,
        "actual_export_rows": 0,
        "query_sha256": "query-digest",
        "failures": [],
    }


@pytest.mark.parametrize(
    ("explained", "role", "failure"),
    [
        (
            _explained(
                {
                    "Node Type": "Seq Scan",
                    "Relation Name": "ledger_export",
                    "Actual Rows": 1,
                    "Actual Loops": 1,
                }
            ),
            "ufo_serve",
            "ledger_export uses a sequential scan",
        ),
        (
            _explained(_index_node(index="ledger_export_consumer_idx")),
            "ufo_serve",
            "ledger_export_pkey is absent from the plan",
        ),
        (
            _explained(_index_node(), execution_ms=10.0),
            "ufo_serve",
            "execution time exceeds the bound",
        ),
        (
            _explained(_index_node(actual_rows=3)),
            "ufo_serve",
            "ledger_export row visits exceed the bound",
        ),
        (
            _explained(_index_node()),
            "ufo_owner",
            "unexpected database role",
        ),
    ],
)
def test_live_plan_rejects_each_plan_contract_failure(
    explained: dict[str, object], role: str, failure: str
) -> None:
    result = _check_module().evaluate_plan(explained, role, "query-digest")

    assert result["status"] == "error"
    assert failure in result["failures"]
    assert "ufo_owner" not in json.dumps(result)


def test_probe_failure_withholds_the_exception_message() -> None:
    result = _check_module()._failed(RuntimeError("postgresql://role:secret@database/ufo"))

    assert result["failures"] == ["probe failed with RuntimeError"]
    assert "secret" not in json.dumps(result)


def _stub_gate_dependencies(tmp_path: Path) -> tuple[Path, Path]:
    kubectl = tmp_path / "kubectl"
    kubectl.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        '  *"get deployment/ufo-serve"*) printf \'%s\' "$DEPLOYMENT_JSON" ;;\n'
        '  *"get pods"*) printf \'%s\' "$PODS_JSON" ;;\n'
        '  *"exec -i"*) cat >/dev/null; printf \'%s\' "$PROBE_JSON"; exit "$PROBE_STATUS" ;;\n'
        "  *) exit 2 ;;\n"
        "esac\n"
    )
    kubectl.chmod(0o755)
    curl = tmp_path / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        'while [ "$#" -gt 0 ]; do\n'
        '  case "$1" in\n'
        '    -d) printf \'%s\' "$2" > "$CURL_PAYLOAD"; shift ;;\n'
        '    https://*) printf \'%s\' "$1" > "$CURL_URL" ;;\n'
        "  esac\n"
        "  shift\n"
        "done\n"
    )
    curl.chmod(0o755)
    return tmp_path / "event.json", tmp_path / "url"


@pytest.mark.parametrize(("status", "probe_status"), [("ok", 0), ("error", 1)])
def test_gate_posts_one_safe_event_and_preserves_the_probe_verdict(
    tmp_path: Path, status: str, probe_status: int
) -> None:
    payload_path, url_path = _stub_gate_dependencies(tmp_path)
    result = {
        "status": status,
        "db_role": "ufo_serve",
        "relation": "ledger_export",
        "index": "ledger_export_pkey",
        "access_method": "index_scan",
        "execution_ms": 0.391,
        "threshold_ms": 10.0,
        "seq_scan": False,
        "actual_export_rows": 0,
        "query_sha256": "query-digest",
        "failures": [] if status == "ok" else ["execution time exceeds the bound"],
    }
    environment = os.environ | {
        "CURL_PAYLOAD": str(payload_path),
        "CURL_URL": str(url_path),
        "DD_API_KEY": "api-secret",
        "DD_APP_KEY": "app-secret",
        "DEPLOYMENT_JSON": json.dumps(
            {
                "spec": {
                    "template": {
                        "spec": {"containers": [{"name": "serve", "image": "ufo@sha256:new"}]}
                    }
                }
            }
        ),
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "PODS_JSON": json.dumps(
            {
                "items": [
                    {
                        "metadata": {
                            "name": "ufo-serve-new",
                            "creationTimestamp": "2026-09-01T00:00:00Z",
                        },
                        "spec": {"containers": [{"name": "serve", "image": "ufo@sha256:new"}]},
                        "status": {
                            "phase": "Running",
                            "containerStatuses": [{"name": "serve", "ready": True}],
                        },
                    }
                ]
            }
        ),
        "PROBE_JSON": json.dumps(result),
        "PROBE_STATUS": str(probe_status),
    }
    run = subprocess.run(
        ["bash", str(GATE), "system", "testing", "015f0b03", "a" * 40, RUN_URL],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert run.returncode == probe_status, run.stderr
    assert url_path.read_text() == "https://event-management-intake.us5.datadoghq.com/api/v2/events"
    payload = json.loads(payload_path.read_text())
    attributes = payload["data"]["attributes"]
    assert attributes["category"] == "alert"
    assert attributes["attributes"]["status"] == status
    assert attributes["attributes"]["custom"] == result | {
        "git_sha": "a" * 40,
        "image_tag": "015f0b03",
        "run_url": RUN_URL,
    }
    assert set(attributes["tags"]) == {
        "env:testing",
        "service:ufo",
        "check:billing_usage_export_plan",
        "db_role:ufo_serve",
        "relation:ledger_export",
        "index:ledger_export_pkey",
        "access_method:index_scan",
    }
    rendered = json.dumps(payload)
    for withheld in ("api-secret", "app-secret", "workspace_id", "parameters", "statement"):
        assert withheld not in rendered

import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).parents[2]


@pytest.mark.parametrize(
    ("stack", "host", "postgres", "redis", "gateway", "serve"),
    [
        ("1", "ufo-1.localhost", "15541", "15543", "18080", "18710"),
        ("2", "ufo-2.localhost", "15641", "15643", "18180", "18810"),
        ("3", "ufo-3.localhost", "15741", "15743", "18280", "18910"),
        ("4", "ufo-4.localhost", "15841", "15843", "18380", "19010"),
        ("5", "ufo-5.localhost", "15941", "15943", "18480", "19110"),
    ],
)
def test_stack_slot_selects_one_complete_compose_project(
    stack: str, host: str, postgres: str, redis: str, gateway: str, serve: str
) -> None:
    result = subprocess.run(
        ["make", "--no-print-directory", "-n", "stack-down", f"STACK={stack}"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )

    command = result.stdout.strip()
    assert f"UFO_DEV_IMAGE=ufo-{stack}-dev" in command
    assert f"UFO_STACK_HOST={host}" in command
    assert f"UFO_PG_PORT={postgres}" in command
    assert f"UFO_REDIS_PORT={redis}" in command
    assert f"UFO_GATEWAY_PORT_HOST={gateway}" in command
    assert f"UFO_SERVE_PORT_HOST={serve}" in command
    assert f"docker compose --project-name ufo-{stack} down" in command


def test_stack_origin_reaches_every_browser_callback() -> None:
    compose = yaml.safe_load((REPO / "compose.yaml").read_text())
    gateway = compose["services"]["gateway"]["environment"]
    serve = compose["services"]["serve"]["environment"]

    gateway_origin = "http://${UFO_STACK_HOST:-localhost}:${UFO_GATEWAY_PORT_HOST:-8080}"
    serve_origin = "http://${UFO_STACK_HOST:-localhost}:${UFO_SERVE_PORT_HOST:-8710}"
    assert gateway["UFO_PUBLIC_BASE_URL"] == gateway_origin
    assert gateway["UFO_WORKSPACE_BASE_URL"] == serve_origin
    assert gateway["WORKOS_REDIRECT_URI"] == f"{gateway_origin}/v1/onboard/auth/callback"
    assert serve["UFO_PUBLIC_BASE_URL"] == serve_origin
    assert 'public_base_url = "__PUBLIC_BASE_URL__"' in (REPO / "dev/ufo.toml").read_text()
    assert "s#__PUBLIC_BASE_URL__#${PUBLIC_BASE_URL}#g" in (REPO / "dev/entrypoint.sh").read_text()


def test_stack_refuses_a_slot_outside_its_closed_range() -> None:
    result = subprocess.run(
        ["make", "--no-print-directory", "-n", "stack", "STACK=6"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "STACK must be one of: 1 2 3 4 5" in result.stderr


def test_db_keeps_the_test_and_eval_postgres_port() -> None:
    result = subprocess.run(
        ["make", "--no-print-directory", "-n", "db"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "docker compose up -d postgres"

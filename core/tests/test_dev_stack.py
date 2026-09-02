import subprocess
from pathlib import Path

import yaml

from sandbox.build_template import NODE_VERSION
from ufo.sdk.sandbox import NODE_GLOBAL_MODULES, PLAYWRIGHT_BROWSERS_DIR, PLAYWRIGHT_VERSION

REPO = Path(__file__).parents[2]


def _check_stack_slot_selects_one_complete_compose_project() -> None:
    for stack, host, postgres, redis, gateway, serve, ingress in [
        ("1", "ufo-1.localhost", "15541", "15543", "18080", "18710", "18100"),
        ("2", "ufo-2.localhost", "15641", "15643", "18180", "18810", "18200"),
        ("3", "ufo-3.localhost", "15741", "15743", "18280", "18910", "18300"),
        ("4", "ufo-4.localhost", "15841", "15843", "18380", "19010", "18400"),
        ("5", "ufo-5.localhost", "15941", "15943", "18480", "19110", "18500"),
    ]:
        result = subprocess.run(
            ["make", "--no-print-directory", "-n", "stack-down", f"STACK={stack}"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        )

        command = result.stdout.strip()
        repository_root = Path(
            subprocess.run(
                ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                cwd=REPO,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        ).parent
        workspace_root = repository_root / ".local" / f"ufo-{stack}" / "workspaces"
        assert f"UFO_DEV_IMAGE=ufo-{stack}-dev" in command
        assert f"UFO_STACK_HOST={host}" in command
        assert f"UFO_PG_PORT={postgres}" in command
        assert f"UFO_REDIS_PORT={redis}" in command
        assert f"UFO_GATEWAY_PORT_HOST={gateway}" in command
        assert f"UFO_SERVE_PORT_HOST={serve}" in command
        assert f"UFO_INGRESS_PORT_HOST={ingress}" in command
        assert f'UFO_WORKSPACE_ROOT="{workspace_root}"' in command
        assert f"docker compose --project-name ufo-{stack} down" in command


def _check_stack_origin_reaches_every_browser_callback() -> None:
    compose = yaml.safe_load((REPO / "compose.yaml").read_text())
    gateway = compose["services"]["gateway"]["environment"]
    serve = compose["services"]["serve"]["environment"]
    ingress = compose["services"]["ingress"]

    gateway_origin = "http://${UFO_STACK_HOST:-localhost}:${UFO_GATEWAY_PORT_HOST:-8080}"
    serve_origin = "http://${UFO_STACK_HOST:-localhost}:${UFO_SERVE_PORT_HOST:-8710}"
    ingress_origin = "http://${UFO_STACK_HOST:-ufo.localhost}:${UFO_INGRESS_PORT_HOST:-8100}"
    assert gateway["UFO_PUBLIC_BASE_URL"] == gateway_origin
    assert gateway["UFO_WORKSPACE_BASE_URL"] == serve_origin
    assert gateway["WORKOS_REDIRECT_URI"] == f"{gateway_origin}/v1/onboard/auth/callback"
    assert serve["UFO_PUBLIC_BASE_URL"] == serve_origin
    assert serve["UFO_INGRESS_PUBLIC_URL"] == ingress_origin
    assert ingress["command"] == ["ingress"]
    assert ingress["network_mode"] == "service:serve"
    assert ingress["environment"]["UFO_PUBLIC_BASE_URL"] == serve_origin
    assert ingress["environment"]["UFO_INGRESS_PUBLIC_URL"] == ingress_origin
    assert "127.0.0.1:${UFO_INGRESS_PORT_HOST:-8100}:8100" in compose["services"]["serve"]["ports"]
    assert 'public_base_url = "__PUBLIC_BASE_URL__"' in (REPO / "dev/ufo.toml").read_text()
    assert 'ingress_public_url = "__INGRESS_PUBLIC_URL__"' in (REPO / "dev/ufo.toml").read_text()
    assert "s#__PUBLIC_BASE_URL__#${PUBLIC_BASE_URL}#g" in (REPO / "dev/entrypoint.sh").read_text()
    assert (
        "s#__INGRESS_PUBLIC_URL__#${INGRESS_PUBLIC_URL}#g"
        in (REPO / "dev/entrypoint.sh").read_text()
    )


def _check_stack_passes_every_model_provider_key() -> None:
    compose = yaml.safe_load((REPO / "compose.yaml").read_text())
    serve = compose["services"]["serve"]["environment"]

    assert serve["UFO_ANTHROPIC_API_KEY"] == "${UFO_ANTHROPIC_API_KEY:-}"
    assert serve["UFO_OPENAI_API_KEY"] == "${UFO_OPENAI_API_KEY:-}"
    assert serve["OPENROUTER_API_KEY"] == "${OPENROUTER_API_KEY:-}"


def _check_local_sandbox_has_the_application_builder_runtime() -> None:
    dockerfile = (REPO / "dev/Dockerfile").read_text()

    assert f"FROM node:{NODE_VERSION}-bookworm-slim AS node" in dockerfile
    assert f"playwright@{PLAYWRIGHT_VERSION}" in dockerfile
    assert "--no-audit vite playwright@" in dockerfile
    assert "COPY --from=node /usr/local/bin/node /usr/local/bin/node" in dockerfile
    assert "COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules" in dockerfile
    assert "ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm" in dockerfile
    assert "ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx" in dockerfile
    assert "ln -s ../lib/node_modules/vite/bin/vite.js /usr/local/bin/vite" in dockerfile
    assert "ln -s ../lib/node_modules/playwright/cli.js /usr/local/bin/playwright" in dockerfile
    assert f"ENV NODE_PATH={NODE_GLOBAL_MODULES}" in dockerfile
    assert f"ENV PLAYWRIGHT_BROWSERS_PATH={PLAYWRIGHT_BROWSERS_DIR}" in dockerfile
    assert "playwright install chromium" in dockerfile
    assert 'test -x "$chromium_path"' in dockerfile
    assert 'ln -s "$chromium_path" /usr/local/bin/chromium' in dockerfile


def _check_local_workspaces_stay_out_of_the_image_and_mount_per_project() -> None:
    ignored = set((REPO / ".dockerignore").read_text().splitlines())
    assert {".local", "**/.local", ".worktrees", "**/.worktrees"} <= ignored

    compose = yaml.safe_load((REPO / "compose.yaml").read_text())
    serve_volumes = compose["services"]["serve"]["volumes"]
    ingress_volumes = compose["services"]["ingress"]["volumes"]
    workspace_mount = (
        "${UFO_WORKSPACE_ROOT:-./.local/${COMPOSE_PROJECT_NAME:-ufo}/workspaces}:/data/workspaces"
    )
    assert "blobs:/data/blobs" in serve_volumes
    assert workspace_mount in serve_volumes
    assert workspace_mount in ingress_volumes
    assert "blobs" in compose["volumes"]


def _check_web_reloads_from_source_against_each_slot() -> None:
    for stack, host, web, serve in [
        ("1", "ufo-1.localhost", "15173", "18710"),
        ("2", "ufo-2.localhost", "15273", "18810"),
        ("3", "ufo-3.localhost", "15373", "18910"),
        ("4", "ufo-4.localhost", "15473", "19010"),
        ("5", "ufo-5.localhost", "15573", "19110"),
    ]:
        command = subprocess.run(
            ["make", "--no-print-directory", "-n", "web", f"STACK={stack}"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        ).stdout

        assert f"UFO_SERVE_ORIGIN=http://{host}:{serve}" in command
        # The flags go to vite bare: pnpm swallows a `--` before them and vite then serves the
        # lanes shell on its own default port.
        assert "pnpm -C extensions/web/frontend run dev --config sidebar/vite.config.ts" in command
        assert f"--host {host} --port {web} --strictPort" in command
        assert f"http://{host}:{web}/surface/web" in command

    lanes = subprocess.run(
        ["make", "--no-print-directory", "-n", "web", "SHELL_NAME=lanes"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "--config" not in lanes

    refused = subprocess.run(
        ["make", "--no-print-directory", "-n", "web", "SHELL_NAME=both"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert refused.returncode != 0
    assert "SHELL_NAME must be one of: sidebar lanes" in refused.stderr

    # The dev server reads the fleet through this variable, and its own suite pins the reading.
    for config in ["vite.config.ts", "sidebar/vite.config.ts"]:
        source = (REPO / "extensions/web/frontend" / config).read_text()
        assert "process.env.UFO_SERVE_ORIGIN" in source


def _check_stack_refuses_a_slot_outside_its_closed_range() -> None:
    result = subprocess.run(
        ["make", "--no-print-directory", "-n", "stack", "STACK=6"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "STACK must be one of: 1 2 3 4 5" in result.stderr


def _check_db_keeps_the_test_and_eval_postgres_port() -> None:
    result = subprocess.run(
        ["make", "--no-print-directory", "-n", "db"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "docker compose up -d postgres"


def test_dev_stack_sync_contract() -> None:
    checks = tuple(value for name, value in globals().items() if name.startswith("_check_"))
    assert len(checks) == 8
    for check in checks:
        check()

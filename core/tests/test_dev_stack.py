import os
import re
import subprocess
from pathlib import Path

import yaml

from sandbox.build_template import NODE_VERSION
from ufo.sdk.sandbox import NODE_GLOBAL_MODULES, PLAYWRIGHT_BROWSERS_DIR, PLAYWRIGHT_VERSION

REPO = Path(__file__).parents[2]


def _check_stack_slot_selects_one_complete_compose_project() -> None:
    for stack, host, postgres, redis, front, serve, ingress in [
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
        assert f"UFO_STACK_PORT_HOST={front}" in command
        assert f"UFO_SERVE_PORT_HOST={serve}" in command
        assert f"UFO_INGRESS_PORT_HOST={ingress}" in command
        assert f'UFO_WORKSPACE_ROOT="{workspace_root}"' in command
        assert f"docker compose --project-name ufo-{stack} down" in command


def _check_stack_origin_reaches_every_browser_callback() -> None:
    compose = yaml.safe_load((REPO / "compose.yaml").read_text())
    gateway = compose["services"]["gateway"]
    serve = compose["services"]["serve"]["environment"]
    front = compose["services"]["front"]
    ingress = compose["services"]["ingress"]

    stack_origin = "http://${UFO_STACK_HOST:-localhost}:${UFO_STACK_PORT_HOST:-8080}"
    ingress_origin = "http://${UFO_STACK_HOST:-ufo.localhost}:${UFO_INGRESS_PORT_HOST:-8100}"
    assert gateway["environment"]["UFO_PUBLIC_BASE_URL"] == stack_origin
    assert gateway["environment"]["UFO_WORKSPACE_BASE_URL"] == stack_origin
    assert (
        gateway["environment"]["WORKOS_REDIRECT_URI"] == f"{stack_origin}/v1/onboard/auth/callback"
    )
    assert "ports" not in gateway
    assert front["ports"] == ["127.0.0.1:${UFO_STACK_PORT_HOST:-8080}:8080"]
    assert "./dev/front.conf:/etc/nginx/conf.d/default.conf:ro" in front["volumes"]
    conf = (REPO / "dev/front.conf").read_text()
    assert "resolver 127.0.0.11 valid=10s;" in conf
    assert "set $gateway http://gateway:8080;" in conf
    assert "set $serve http://serve:8710;" in conf
    for door in ["/login", "/logout", "/join", "/v1/onboard", "/ufo"]:
        assert f"location {door} {{ proxy_pass $gateway; }}" in conf
    assert "location / { proxy_pass $serve; }" in conf
    assert "proxy_set_header Host $http_host;" in conf
    assert serve["UFO_PUBLIC_BASE_URL"] == stack_origin
    assert serve["UFO_INGRESS_PUBLIC_URL"] == ingress_origin
    assert ingress["command"] == ["ingress"]
    assert ingress["network_mode"] == "service:serve"
    assert ingress["environment"]["UFO_PUBLIC_BASE_URL"] == stack_origin
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


def _check_stack_serves_the_shell_and_the_app_pages_from_source() -> None:
    compose = yaml.safe_load((REPO / "compose.yaml").read_text())
    web = compose["services"]["web"]
    assert web["build"] == {"context": ".", "dockerfile": "dev/web.Dockerfile"}
    assert web["image"] == "${UFO_DEV_IMAGE:-ufo-dev}-web"
    assert (
        web["environment"]["UFO_WEB_VITE_CONFIG"]
        == "${UFO_WEB_VITE_CONFIG:-sidebar/vite.config.ts}"
    )
    assert web["environment"]["UFO_STACK_ORIGIN"] == "http://front:8080"
    for mount in [
        "./dev:/app/dev:ro",
        "./assets:/app/assets:ro",
        "./extensions:/app/extensions:ro",
        "web-modules:/app/deps/node_modules",
        "web-modules:/app/extensions/web/frontend/node_modules",
    ]:
        assert mount in web["volumes"]
    assert "web-modules" in compose["volumes"]
    assert compose["services"]["front"]["depends_on"]["web"] == {"condition": "service_started"}

    dockerfile = (REPO / "dev/web.Dockerfile").read_text()
    assert f"FROM node:{NODE_VERSION}-bookworm-slim" in dockerfile
    assert "WORKDIR /app/deps" in dockerfile
    assert "RUN corepack enable && corepack install" in dockerfile
    assert 'ENTRYPOINT ["/app/dev/web.sh"]' in dockerfile
    script = (REPO / "dev/web.sh").read_text()
    assert os.access(REPO / "dev/web.sh", os.X_OK)
    assert "/app/extensions/web/frontend/pnpm-lock.yaml" in script
    assert (
        "(cd /app/deps && pnpm install --frozen-lockfile "
        "--store-dir /app/deps/node_modules/.pnpm-store)"
    ) in script
    assert "\ncd /app/extensions/web/frontend\n" in script
    assert script.count("node_modules/.bin/vite --config ") == 2
    assert '--config "$UFO_WEB_VITE_CONFIG" --host 0.0.0.0 --port 5173 --strictPort &' in script
    assert "--config vite.apps.config.ts --host 0.0.0.0 --port 5174 --strictPort &" in script

    conf = (REPO / "dev/front.conf").read_text()
    assert re.search(r"map \$http_upgrade \$connection_upgrade \{\s*default upgrade;", conf)
    assert re.search(
        r"map \$request_method \$portal_page \{\s*GET\s+http://web:5173;\s*default\s+http://serve:8710;",
        conf,
    )
    assert "set $web http://web:5173;" in conf
    assert "location ~ ^/surface/web/?$ { proxy_pass $portal_page; }" in conf
    assert "location /surface/web/static/ { proxy_pass $web; }" in conf
    assert "proxy_set_header Upgrade $http_upgrade;" in conf
    assert "proxy_set_header Connection $connection_upgrade;" in conf
    assert 'apps_dev_server = "http://web:5174"' in (REPO / "dev/ufo.toml").read_text()

    for shell, config in [("sidebar", "sidebar/vite.config.ts"), ("lanes", "vite.config.ts")]:
        command = subprocess.run(
            ["make", "--no-print-directory", "-n", "stack-down", f"SHELL_NAME={shell}"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert f"UFO_WEB_VITE_CONFIG={config} docker compose" in command
    refused = subprocess.run(
        ["make", "--no-print-directory", "-n", "stack-down", "SHELL_NAME=both"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert refused.returncode != 0
    assert "SHELL_NAME must be one of: sidebar lanes" in refused.stderr
    assert "web:" not in (REPO / "Makefile").read_text().split("stack-down:")[1].split("db:")[0]


def _check_signin_seats_the_dev_email_on_the_stack_origin() -> None:
    for stack, host, front in [
        ("1", "ufo-1.localhost", "18080"),
        ("3", "ufo-3.localhost", "18280"),
    ]:
        command = subprocess.run(
            ["make", "--no-print-directory", "-n", "signin", f"STACK={stack}"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
            env={name: value for name, value in os.environ.items() if name != "BROWSER"},
        ).stdout
        assert f'dev/signin.py --origin http://{host}:{front} --email "$UFO_DEV_EMAIL"' in command
        assert 'test -n "$UFO_DEV_EMAIL"' in command
        assert "BROWSER='open -a \"Google Chrome\" %s'" in command
    assert "UFO_DEV_EMAIL=" in (REPO / ".env.template").read_text().splitlines()


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
    assert len(checks) == 9
    for check in checks:
        check()

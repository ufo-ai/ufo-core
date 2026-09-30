import base64
import struct

import pytest
from ufo_testsupport.plugin import CONTAINER_OP_TIMEOUT_S, docker_or_fail

from ufo.harness.sandbox.session import SYSTEM_SKILLS_ROOT

pytestmark = pytest.mark.docker

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
CANVAS_WIDTH = 200
CANVAS_HEIGHT = 120
GAME_PAGE = (
    '<!doctype html><button id="start-btn">Start</button>'
    f'<canvas width="{CANVAS_WIDTH}" height="{CANVAS_HEIGHT}"></canvas>'
    '<script>document.querySelector("canvas").getContext("2d").fillRect(0, 0, 10, 10);</script>'
)
DOCUMENTED_RUN = (
    'node "game/scripts/web_game_playwright_client.js" --url file:///var/tmp/game/index.html '
    '--actions-file "game/references/action_payloads.json" --click-selector "#start-btn" '
    "--iterations 1 --pause-ms 50 --screenshot-dir /var/tmp/shots"
)


def test_the_documented_client_command_screenshots_a_game_in_the_sandbox_image(
    sandbox_image: str,
) -> None:
    script = (
        "set -e; mkdir -p /var/tmp/game; "
        f"printf '%s' '{GAME_PAGE}' > /var/tmp/game/index.html; "
        f"cd {SYSTEM_SKILLS_ROOT}/website-building; "
        f"{DOCUMENTED_RUN} >&2; "
        "base64 -w0 /var/tmp/shots/shot-0.png"
    )
    ran = docker_or_fail(
        ["docker", "run", "--rm", "--entrypoint", "bash", sandbox_image, "-c", script],
        timeout=CONTAINER_OP_TIMEOUT_S,
    )
    shot = base64.b64decode(ran.stdout)
    assert shot.startswith(PNG_SIGNATURE)
    assert struct.unpack(">II", shot[16:24]) == (CANVAS_WIDTH, CANVAS_HEIGHT)

"""The grader-owned compile and browser audit command for fixed app workspaces."""

import base64
import shlex
from hashlib import sha256
from importlib.resources import files

from ufo_ext_sites.application_audit import APPLICATION_AUDIT_SERVER

AUDIT_CONTENT = files("ufo_ext_sites").joinpath("scripts/audit_application.cjs").read_bytes()
AUDIT_DIGEST = sha256(AUDIT_CONTENT).hexdigest()


def app_audit_server_readiness(port: int) -> str:
    return f"""python3 - "$server_pid" "$health_token" <<'PY'
import os
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

pid = int(sys.argv[1])
health_token = sys.argv[2]
deadline = time.time() + 15
while time.time() < deadline:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        raise SystemExit(1)
    try:
        with urlopen(f'http://127.0.0.1:{port}/health/{{health_token}}', timeout=1) as response:
            if response.read().decode() == health_token:
                raise SystemExit(0)
    except URLError:
        pass
    time.sleep(0.2)
raise SystemExit(1)
PY"""


def app_audit_command(
    *,
    name: str,
    output_dir: str,
    project: str,
    design_path: str,
    port: int,
    compile_source: bool,
) -> str:
    audit = base64.b64encode(AUDIT_CONTENT).decode()
    handler = b"    def do_GET(self):\n"
    if APPLICATION_AUDIT_SERVER.count(handler) != 1:
        raise RuntimeError("application audit server has no single GET handler")
    health_handler = b"""    def do_GET(self):
        health_token = sys.argv[4]
        if self.path.partition("?")[0] == f"/health/{health_token}":
            data = health_token.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
"""
    server = base64.b64encode(APPLICATION_AUDIT_SERVER.replace(handler, health_handler)).decode()
    compile_command = (
        'compile_started=$(python3 -c "import time;print(time.monotonic_ns())")\n'
        f"cd {shlex.quote(project)} && vite build\n"
        'compile_stopped=$(python3 -c "import time;print(time.monotonic_ns())")\n'
        if compile_source
        else "compile_started=0\ncompile_stopped=0\n"
    )
    return (
        "set -eu\n"
        f"capture={shlex.quote(output_dir)}\n"
        'rm -rf "$capture"\n'
        'mkdir -p "$capture"\n'
        f"printf %s {shlex.quote(audit)} | base64 -d > /tmp/ufo-app-bench-audit.cjs\n"
        f"printf %s {shlex.quote(server)} | base64 -d > /tmp/ufo-app-bench-server.py\n"
        f"test -s {project}/app.tsx\n"
        f"test -s {design_path}\n"
        f'cp {design_path} "$capture/{name}-design.svg"\n'
        'printf \'%s\' \'<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<style>html,body{margin:0;min-height:100%;background:#f5f5f5}main{padding:24px}"
        "svg{display:block;width:100%;height:auto;background:white}</style></head>"
        "<body><main>' "
        f'> "$capture/{name}-design.html"\n'
        f'cat {design_path} >> "$capture/{name}-design.html"\n'
        "printf '%s' '</main></body></html>' "
        f'>> "$capture/{name}-design.html"\n'
        + compile_command
        + "health_token=$(python3 -c 'import secrets;print(secrets.token_hex(32))')\n"
        + f"python3 /tmp/ufo-app-bench-server.py {project} {port} "
        f'"$capture/{name}-design.svg" "$health_token" '
        ">/tmp/ufo-app-bench-server.log 2>&1 &\n"
        + "server_pid=$!\n"
        + "trap 'kill \"$server_pid\" 2>/dev/null || true' EXIT\n"
        + 'kill -0 "$server_pid"\n'
        + f"{app_audit_server_readiness(port)}\n"
        + 'kill -0 "$server_pid"\n'
        + 'audit_started=$(python3 -c "import time;print(time.monotonic_ns())")\n'
        + "node /tmp/ufo-app-bench-audit.cjs "
        f"http://localhost:{port}/preview.html "
        f'"$capture/{name}-audit.json" "$capture/{name}-light.png" '
        f'"$capture/{name}-dark.png" "$capture/{name}-interactive.html" '
        f'"$capture/{name}-static.html" http://localhost:{port}/accepted-design.svg\n'
        + 'audit_stopped=$(python3 -c "import time;print(time.monotonic_ns())")\n'
        + 'python3 - "$capture/timing.json" "$compile_started" "$compile_stopped" '
        '"$audit_started" "$audit_stopped" <<\'PY\'\n'
        + "import json\nimport sys\n"
        + "_, path, compile_started, compile_stopped, audit_started, audit_stopped = sys.argv\n"
        + "compile_ms = 0 if compile_started == '0' else "
        "round((int(compile_stopped) - int(compile_started)) / 1_000_000)\n"
        + "audit_ms = round((int(audit_stopped) - int(audit_started)) / 1_000_000)\n"
        + "open(path, 'w').write(json.dumps({'compileMs': compile_ms, 'auditMs': audit_ms}))\n"
        + "PY"
    )

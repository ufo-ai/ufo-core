"""The grader-owned compile and browser audit command for fixed app workspaces."""

import base64
import shlex
from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files

from ufo_ext_sites.application_audit import APPLICATION_DESIGN_WIDTH

AUDIT_CONTENT = (
    files("ufo_ext_sites")
    .joinpath("skills/application-homepage/scripts/audit_application.cjs")
    .read_bytes()
)
AUDIT_DIGEST = sha256(AUDIT_CONTENT).hexdigest()


@dataclass(frozen=True)
class AppAudit:
    """The command and the files it writes, so no caller can require one the run never made."""

    command: str
    outputs: tuple[str, ...]


def app_audit(
    *,
    name: str,
    output_dir: str,
    project: str,
    design_path: str | None,
    compile_source: bool,
) -> AppAudit:
    audit = base64.b64encode(AUDIT_CONTENT).decode()
    compile_command = (
        'compile_started=$(python3 -c "import time;print(time.monotonic_ns())")\n'
        f"cd {shlex.quote(project)} && vite build\n"
        'compile_stopped=$(python3 -c "import time;print(time.monotonic_ns())")\n'
        if compile_source
        else "compile_started=0\ncompile_stopped=0\n"
    )
    design_outputs = (
        ()
        if design_path is None
        else (f"{name}-design.svg", f"{name}-design.html", f"{name}-design-regions.json")
    )
    command = (
        "set -eu\n"
        f"capture={shlex.quote(output_dir)}\n"
        'rm -rf "$capture"\n'
        'mkdir -p "$capture"\n'
        f"printf %s {shlex.quote(audit)} | base64 -d > /tmp/ufo-app-bench-audit.cjs\n"
        f"test -s {project}/app.tsx\n"
        + (
            ""
            if design_path is None
            else (
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
                f"node /tmp/ufo-app-bench-audit.cjs --design {APPLICATION_DESIGN_WIDTH} "
                f'"$capture/{name}-design.svg" '
                f'> "$capture/{name}-design-regions.json"\n'
            )
        )
        + compile_command
        + 'audit_started=$(python3 -c "import time;print(time.monotonic_ns())")\n'
        + "node /tmp/ufo-app-bench-audit.cjs "
        f"{project} "
        f'"$capture/{name}-audit.json" "$capture/{name}-light.png" '
        f'"$capture/{name}-dark.png" "$capture/{name}-interactive.html" '
        f'"$capture/{name}-static.html" {APPLICATION_DESIGN_WIDTH}'
        + ("" if design_path is None else f' "$capture/{name}-design.svg"')
        + "\n"
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
    return AppAudit(
        command=command,
        outputs=(
            *design_outputs,
            f"{name}-audit.json",
            f"{name}-light.png",
            f"{name}-dark.png",
            f"{name}-interactive.html",
            f"{name}-static.html",
            "timing.json",
        ),
    )

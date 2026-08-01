#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path

AUTHORIZATION_PATHS = frozenset(
    {
        "infra/modules/platform/iam.tf",
        "infra/modules/platform/ses.tf",
    }
)
RUNTIME_PATHS = frozenset(
    {
        ".github/scripts/deploy_change_gate.py",
        ".github/workflows/deploy.yml",
        "hosted.toml",
        "infra/envs/prod/ufo.tf",
        "infra/envs/testing/ufo.tf",
        "infra/production_secrets.py",
        "infra/templates/hosted.yaml.tpl",
        "pyproject.toml",
        "uv.lock",
    }
)
RUNTIME_PREFIXES = ("control/", "core/src/", "extensions/", "packs/", "sandbox/")
BOUNDARY_ERROR = (
    "runtime authorization and its consumers must land separately: expand IAM, roll and drain "
    "the runtime, then contract IAM"
)


def validate_deploy_change(paths: tuple[str, ...]) -> None:
    authorization_changed = any(path in AUTHORIZATION_PATHS for path in paths)
    runtime_changed = any(
        path in RUNTIME_PATHS or path.startswith(RUNTIME_PREFIXES) for path in paths
    )
    if authorization_changed and runtime_changed:
        raise ValueError(BOUNDARY_ERROR)


def main() -> int:
    paths = tuple(Path(sys.argv[1]).read_text().splitlines())
    try:
        validate_deploy_change(paths)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

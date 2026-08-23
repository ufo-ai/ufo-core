#!/usr/bin/env python3

from __future__ import annotations

import re
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
        ".github/scripts/production_prerequisites.sh",
        ".github/workflows/deploy-production.yml",
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
RUNTIME_PREFIXES = ("servers/control/", "core/src/", "extensions/", "packs/", "sandbox/")
BOUNDARY_ERROR = (
    "runtime authorization and its consumers must land separately: contract IAM only after the "
    "runtime that used the grant has rolled and drained"
)
DENY_EFFECT = '"Deny"'
BLOCK_HEADER = re.compile(r'(resource|data|module)\s+"([^"]+)"(?:\s+"([^"]+)")?')
PRINCIPAL_ARGUMENT = re.compile(r"^\s*(?:role|roles|user|users|group|groups)\s*=\s*(.+)$")
REFERENCE = re.compile(r"\b(?:data\.\w+\.\w+|\w+\.\w+)")


def _is_grant_content(line: str) -> bool:
    body = line.strip()
    return bool(body) and not body.startswith(("#", "//"))


def _opens_top_level_block(line: str) -> bool:
    return not line[:1].isspace() and "{" in line


def _declared_block(line: str) -> str | None:
    match = BLOCK_HEADER.match(line)
    if match is None:
        return None
    kind, first, second = match.groups()
    if kind == "module":
        return f"module.{first}"
    if second is None:
        return None
    return f"{first}.{second}" if kind == "resource" else f"data.{first}.{second}"


def _authorization_contracts(authorization_diff: str) -> bool:
    """True unless the authorization diff only adds whole new blocks at the top level that stand on
    their own. A removed content line drops a grant a live consumer may still hold. An added line
    inside a block that already exists narrows that block's grant just as easily — an
    "assume_role_condition_test" argument, a "condition" block — so an added line counts only
    inside a block the diff itself opens and closes. New blocks narrow too when they reach a
    principal that outlives the diff: a Deny denies whoever holds the policy, and a "role"/"user"/
    "group" argument naming a principal the diff does not declare rewrites what that live principal
    may do. Blank and comment lines change no grant."""
    open_blocks = 0
    declared: set[str] = set()
    bound: list[frozenset[str]] = []
    for line in authorization_diff.splitlines():
        if line.startswith(("---", "+++")):
            continue
        if line.startswith("+"):
            body = line[1:]
            if not _is_grant_content(body):
                continue
            if open_blocks == 0:
                if not _opens_top_level_block(body):
                    return True
                identifier = _declared_block(body)
                if identifier is not None:
                    declared.add(identifier)
            if DENY_EFFECT in body:
                return True
            principal = PRINCIPAL_ARGUMENT.match(body)
            if principal is not None:
                bound.append(frozenset(REFERENCE.findall(principal.group(1))))
            open_blocks += body.count("{") - body.count("}")
            if open_blocks < 0:
                return True
            continue
        if line.startswith("-") and _is_grant_content(line[1:]):
            return True
        open_blocks = 0
    return any(not targets or not targets <= declared for targets in bound)


def validate_deploy_change(paths: tuple[str, ...], authorization_diff: str | None = None) -> None:
    """Reject an authorization *contraction* landing with its consumers in one deploy. An expansion
    (a new grant, whole and self-contained) co-deploys safely: terraform creates it before the
    rollout, and the rollout-health gate backstops a consumer that cannot yet assume it. Narrowing
    or removing an existing grant is the dangerous ordering — a consumer still holding it breaks
    after the deploy. The diff is the authorization files' unified diff; None means it was not
    supplied, so the split is enforced (fail closed — an unknown change may be a contraction)."""
    authorization_changed = any(path in AUTHORIZATION_PATHS for path in paths)
    runtime_changed = any(
        path in RUNTIME_PATHS or path.startswith(RUNTIME_PREFIXES) for path in paths
    )
    if not (authorization_changed and runtime_changed):
        return
    if authorization_diff is None or _authorization_contracts(authorization_diff):
        raise ValueError(BOUNDARY_ERROR)


def main() -> int:
    paths = tuple(Path(sys.argv[1]).read_text().splitlines())
    authorization_diff = Path(sys.argv[2]).read_text() if len(sys.argv) > 2 else None
    try:
        validate_deploy_change(paths, authorization_diff)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

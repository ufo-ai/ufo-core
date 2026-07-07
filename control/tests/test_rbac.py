import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
CONTROL_PLANE = REPO / "deploy" / "control-plane.yaml"
CHART = REPO / "charts" / "ufo-tenant"
DIGEST = "sha256:" + "e" * 64

VALUES = f"""\
namespace: ufo-acme
tenant_name: acme
pack: assistant
host: acme.ufo.app
owner_email: you@acme.com
bundle_image: ghcr.io/acme/ufo@{DIGEST}
sandbox_image: ghcr.io/acme/sandbox@{DIGEST}
"""

Grants = set[tuple[str, str, str]]


def _grants(rules: list[dict]) -> Grants:
    return {
        (group, resource, verb)
        for rule in rules
        for group in rule["apiGroups"]
        for resource in rule["resources"]
        for verb in rule["verbs"]
    }


def _named(docs: Iterable[Any], kind: str, name: str) -> dict:
    for doc in docs:
        if doc and doc.get("kind") == kind and doc["metadata"]["name"] == name:
            return doc
    raise AssertionError(f"{kind}/{name} not found")


def _operator_cluster_role() -> Grants:
    docs = yaml.safe_load_all(CONTROL_PLANE.read_text())
    return _grants(_named(docs, "ClusterRole", "ufo-operator")["rules"])


def _sandbox_manager_role(tmp_path: Path) -> Grants:
    values = tmp_path / "values.yaml"
    values.write_text(VALUES)
    result = subprocess.run(
        [
            "helm",
            "template",
            "acme",
            str(CHART),
            "--namespace",
            "ufo-acme",
            "--values",
            str(values),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    docs = yaml.safe_load_all(result.stdout)
    return _grants(_named(docs, "Role", "ufo-sandbox-manager")["rules"])


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
def test_operator_holds_every_verb_the_sandbox_manager_role_grants(tmp_path: Path) -> None:
    # K8s escalation-prevention rejects the operator creating a Role that grants any verb the
    # operator does not itself hold. The Role's grants must be a subset of the operator ClusterRole.
    granted = _sandbox_manager_role(tmp_path)
    held = _operator_cluster_role()
    assert granted <= held, f"operator missing: {sorted(granted - held)}"

import shutil
import subprocess
from pathlib import Path

import pytest

CHART = Path(__file__).resolve().parent.parent / "charts" / "ufo-tenant"
DIGEST = "sha256:" + "d" * 64

VALUES = f"""\
namespace: ufo-acme
tenant_name: acme
pack: assistant
host: acme.ufo.app
owner_email: you@acme.com
bundle_image: ghcr.io/acme/ufo@{DIGEST}
sandbox_image: ghcr.io/acme/sandbox@{DIGEST}
"""

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")


def _write_values(tmp_path: Path) -> Path:
    values = tmp_path / "values.yaml"
    values.write_text(VALUES)
    return values


def test_chart_lints(tmp_path: Path) -> None:
    result = subprocess.run(
        ["helm", "lint", str(CHART), "--values", str(_write_values(tmp_path))],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_chart_renders_the_tenant_workload(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "helm",
            "template",
            "acme",
            str(CHART),
            "--namespace",
            "ufo-acme",
            "--values",
            str(_write_values(tmp_path)),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    rendered = result.stdout
    assert "kind: Deployment" in rendered
    assert "kind: HorizontalPodAutoscaler" in rendered
    assert "kind: Ingress" in rendered
    assert 'cert-manager.io/cluster-issuer: "letsencrypt"' in rendered
    assert "external-dns.alpha.kubernetes.io/hostname" in rendered
    assert f"ghcr.io/acme/ufo@{DIGEST}" in rendered
    # The init Job runs once, on install only.
    assert '"helm.sh/hook": post-install' in rendered
    assert "ufo-sandbox-manager" in rendered
    # Both the serve Deployment and the init Job envFrom the platform Secret (model + cloud creds)
    # the operator replicates into the namespace — the consumer end of that Secret.
    assert rendered.count("secretRef") >= 2
    assert "ufo-platform-secrets" in rendered

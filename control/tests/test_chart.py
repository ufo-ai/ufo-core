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
tenant_secret_checksum: cafe1234
"""

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")


def _write_values(tmp_path: Path, extra: str = "") -> Path:
    values = tmp_path / "values.yaml"
    values.write_text(VALUES + extra)
    return values


def _template(values: Path) -> str:
    result = subprocess.run(
        ["helm", "template", "acme", str(CHART), "-n", "ufo-acme", "--values", str(values)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


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
    # Proxied (orange-cloud) is load-bearing: the shared NLB admits only Cloudflare's edge ranges,
    # so a DNS-only tenant record is unreachable from the internet.
    assert 'external-dns.alpha.kubernetes.io/cloudflare-proxied: "true"' in rendered
    assert f"ghcr.io/acme/ufo@{DIGEST}" in rendered
    # The init Job runs once, on install only.
    assert '"helm.sh/hook": post-install' in rendered
    assert "ufo-sandbox-manager" in rendered
    # Both the serve Deployment and the init Job envFrom the platform Secret (model + cloud creds)
    # the operator replicates into the namespace — the consumer end of that Secret.
    assert rendered.count("secretRef") >= 2
    assert "ufo-platform-secrets" in rendered
    # The database tier omits workspace_id, so init mints its own workspace uuid.
    assert "--workspace-id" not in rendered
    # The tenant Secret checksum rides the pod template so a changed Secret rolls the serve pods.
    assert 'checksum/tenant-secret: "cafe1234"' in rendered


def test_init_job_pins_workspace_id_for_the_rls_tier(tmp_path: Path) -> None:
    # The rls tier renders workspace_id, so the init Job pins the control-plane-minted uuid the RLS
    # GUC checks — the consumer end of render's workspace_id value.
    workspace_id = "11111111-2222-3333-4444-555555555555"
    rendered = _template(_write_values(tmp_path, extra=f"workspace_id: {workspace_id}\n"))
    assert "--workspace-id" in rendered
    assert workspace_id in rendered

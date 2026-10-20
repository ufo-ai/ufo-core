import ssl
import tomllib
from pathlib import Path

import certifi
import pytest
from cryptography import x509
from cryptography.x509.oid import NameOID
from packaging.version import Version

LOCK = Path(__file__).parents[2] / "uv.lock"


@pytest.mark.parametrize(
    ("package", "minimum"),
    [("pillow", "13.0.0"), ("certifi", "2026.10.19"), ("aiohttp", "3.14.4"), ("mcp", "1.30.0")],
)
def test_lock_keeps_security_fixes(package: str, minimum: str) -> None:
    locked = tomllib.loads(LOCK.read_text())["package"]
    version = next(row["version"] for row in locked if row["name"] == package)
    assert Version(version) >= Version(minimum)


def test_trust_bundle_excludes_compromised_emsign_root() -> None:
    context = ssl.create_default_context(cafile=certifi.where())
    assert len(context.get_ca_certs()) > 100
    certificates = x509.load_pem_x509_certificates(Path(certifi.where()).read_bytes())
    assert "emSign ECC Root CA - G3" not in {
        name.value
        for certificate in certificates
        for name in certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    }

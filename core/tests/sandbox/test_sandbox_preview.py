import re
from pathlib import Path

import pytest

from ufo.harness.sandbox.preview import PREVIEW_HOST, PREVIEW_SENTINEL, parse_preview_service


def _check_parse_preview_service_splits_host_and_port() -> None:
    assert parse_preview_service("ufo-preview.ufo.svc.cluster.local:8930") == (
        "ufo-preview.ufo.svc.cluster.local",
        8930,
    )


def _check_parse_preview_service_is_none_when_unset() -> None:
    assert parse_preview_service(None) is None


def _check_parse_preview_service_fails_loud_on_a_malformed_address() -> None:
    for value in ["nohost", ":8930", ""]:
        with pytest.raises(ValueError):
            parse_preview_service(value)


def _check_the_preview_host_matches_the_proxys_own_constant() -> None:
    server_rs = Path(__file__).resolve().parents[3] / "servers" / "egress" / "src" / "server.rs"
    declared = re.search(r'PREVIEW_HOST: &str = "([^"]+)";', server_rs.read_text())
    assert declared is not None, "PREVIEW_HOST not found in servers/egress/src/server.rs"
    assert declared.group(1) == PREVIEW_HOST


def _check_the_preview_sentinel_matches_the_clients_fixed_bearer() -> None:
    client_rs = (
        Path(__file__).resolve().parents[3] / "client" / "src" / "ops" / "fileops" / "fs_read.rs"
    )
    declared = re.search(r'DOCUMENT_RENDER_BEARER: &str = "([^"]+)";', client_rs.read_text())
    assert declared is not None, "DOCUMENT_RENDER_BEARER not found in client fs_read"
    assert declared.group(1) == PREVIEW_SENTINEL


def test_sandbox_preview_sync_contract() -> None:
    checks = tuple(value for name, value in globals().items() if name.startswith("_check_"))
    assert len(checks) == 5
    for check in checks:
        check()

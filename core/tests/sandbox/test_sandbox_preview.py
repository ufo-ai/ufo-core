import re
from pathlib import Path

import pytest

from ufo.harness.sandbox.preview import PREVIEW_HOST, PREVIEW_SENTINEL, parse_preview_service


def test_parse_preview_service_splits_host_and_port() -> None:
    assert parse_preview_service("ufo-preview.ufo.svc.cluster.local:8930") == (
        "ufo-preview.ufo.svc.cluster.local",
        8930,
    )


def test_parse_preview_service_is_none_when_unset() -> None:
    assert parse_preview_service(None) is None


@pytest.mark.parametrize("value", ["nohost", ":8930", ""])
def test_parse_preview_service_fails_loud_on_a_malformed_address(value: str) -> None:
    with pytest.raises(ValueError):
        parse_preview_service(value)


def test_the_preview_host_matches_the_proxys_own_constant() -> None:
    """The proxy picks which daemon a service rule relays to by comparing the rule's host against
    its own copy of this constant, so a drift between the two would relay every render at the cache
    daemon instead — and the cache would answer none of them."""
    server_rs = Path(__file__).resolve().parents[3] / "servers" / "egress" / "src" / "server.rs"
    declared = re.search(r'PREVIEW_HOST: &str = "([^"]+)";', server_rs.read_text())
    assert declared is not None, "PREVIEW_HOST not found in servers/egress/src/server.rs"
    assert declared.group(1) == PREVIEW_HOST


def test_the_preview_sentinel_matches_the_clients_fixed_bearer() -> None:
    client_rs = (
        Path(__file__).resolve().parents[3] / "client" / "src" / "ops" / "fileops" / "fs_read.rs"
    )
    declared = re.search(r'DOCUMENT_RENDER_BEARER: &str = "([^"]+)";', client_rs.read_text())
    assert declared is not None, "DOCUMENT_RENDER_BEARER not found in client fs_read"
    assert declared.group(1) == PREVIEW_SENTINEL

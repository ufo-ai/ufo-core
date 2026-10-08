from pathlib import Path

import pytest

ROOT = Path(__file__).parents[3]


@pytest.mark.parametrize("server", ("cache", "egress", "preview"))
def test_server_runtime_packages_can_refresh_without_compiling(server: str) -> None:
    dockerfile = (ROOT / "servers" / server / "Dockerfile").read_text()
    packages = dockerfile.split(" AS runtime-packages\n")[1]
    packages, runtime = packages.split("FROM runtime-packages AS runtime\n")
    assert "apt-get update" in packages
    assert "apt-get upgrade -y" in packages
    assert "apt-get install" in packages
    assert "--from=build" not in packages
    assert "COPY --from=build" in runtime
    assert "ENTRYPOINT" in runtime


@pytest.mark.parametrize("server", ("cache", "preview"))
def test_libcurl_consumers_use_fixed_backports(server: str) -> None:
    dockerfile = (ROOT / "servers" / server / "Dockerfile").read_text()
    assert "FROM rust:1-slim-trixie AS chef" in dockerfile
    assert "FROM debian:trixie-slim AS runtime-packages" in dockerfile
    assert "deb http://deb.debian.org/debian trixie-backports main" in dockerfile
    assert "-t trixie-backports" in dockerfile
    assert "curl libcurl4t64 libcurl3t64-gnutls" in dockerfile
    assert "for package in curl libcurl4t64 libcurl4-gnutls; do" in dockerfile
    assert (
        'dpkg --compare-versions "$(dpkg-query -W -f=\'${Version}\' "$package")" ge 8.21.0'
        in dockerfile
    )

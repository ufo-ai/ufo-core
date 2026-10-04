from pathlib import Path

import pytest

ROOT = Path(__file__).parents[3]


@pytest.mark.parametrize("server", ("cache", "egress", "preview"))
def test_server_runtime_packages_can_refresh_without_compiling(server: str) -> None:
    dockerfile = (ROOT / "servers" / server / "Dockerfile").read_text()
    packages = dockerfile.split("FROM debian:stable-slim AS runtime-packages\n")[1]
    packages, runtime = packages.split("FROM runtime-packages AS runtime\n")
    assert "apt-get update" in packages
    assert "apt-get upgrade -y" in packages
    assert "apt-get install" in packages
    assert "--from=build" not in packages
    assert "COPY --from=build" in runtime
    assert "ENTRYPOINT" in runtime

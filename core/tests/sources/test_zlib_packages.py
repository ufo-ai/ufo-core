import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[3]
SECURITY = ROOT / "core/src/ufo/runtime/security/zlib"


@pytest.mark.parametrize("server", ("cache", "egress", "preview"))
def test_runtime_images_install_the_checked_zlib_package(server: str) -> None:
    dockerfile = (ROOT / "servers" / server / "Dockerfile").read_text()
    assert "FROM debian:trixie-slim AS zlib-build" in dockerfile
    assert "COPY --from=zlib-security / /security" in dockerfile
    assert "RUN sh /security/build.sh" in dockerfile
    assert "COPY --from=zlib-build /out/ /tmp/zlib-packages/" in dockerfile
    assert "RUN sh /tmp/zlib-packages/install.sh /tmp/zlib-packages" in dockerfile
    assert dockerfile.index("sh /tmp/zlib-packages/install.sh") < dockerfile.index(
        "FROM runtime-packages AS runtime"
    )


def test_preview_native_builds_receive_the_same_zlib_source() -> None:
    workflow = (ROOT / ".github/workflows/preview.yml").read_text()
    builds = [line for line in workflow.splitlines() if "docker build " in line]
    assert len(builds) == 2
    assert all(
        "--build-context zlib-security=../../core/src/ufo/runtime/security/zlib" in line
        for line in builds
    )
    testbed = (ROOT / "servers/preview/testbed.Dockerfile").read_text()
    assert "COPY --from=zlib-security / /security" in testbed
    assert "RUN sh /tmp/zlib-packages/install.sh /tmp/zlib-packages" in testbed


def test_zlib_backport_is_pinned_and_checks_real_package_state() -> None:
    provenance = json.loads((SECURITY / "provenance.json").read_text())
    assert provenance["upstreamFix"].endswith("df84af25dc1942490e1d1c899a07619152a46148")
    build = (SECURITY / "build.sh").read_text()
    assert "sha256sum -c" in build
    assert "--fuzz=0" in build
    assert 'test "$before" = 42' in build
    assert "dpkg-buildpackage --build=binary -Pnobiarch" in build
    assert "LD_LIBRARY_PATH=$(dirname" in build
    assert "diff -u /tmp/zlib-before.symbols /tmp/zlib-after.symbols" in build
    regression = (SECURITY / "test-write-state.c").read_text()
    assert "state->strm.avail_in != 0" in regression
    assert "state->strm.next_in != state->in" in regression
    assert "gzclearerr" not in regression

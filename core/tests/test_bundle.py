from pathlib import Path

import pytest

from ufo.bundle import BUNDLE_CONSTRAINTS_NAME, BUNDLE_WHEELS_DIR, Bundle, wheel_name
from ufo.gconv import DISABLE_COMMAND

ACME_WHEEL = "acme-0.1.0-py3-none-any.whl"


def _dockerfile() -> str:
    return Bundle(
        config_path=Path("ufo.toml"),
        catalog=None,
        out=Path("bundle"),
        wheels=(Path(wheel_name()), Path(ACME_WHEEL)),
        client_binary=Path("ufo-sandbox-client"),
        constraints="",
    )._dockerfile()


def test_bundle_installs_every_built_wheel_at_the_locked_versions() -> None:
    lines = _dockerfile().splitlines()
    copy = next(line for line in lines if line.startswith("COPY ") and line.endswith("/"))
    install = next(line for line in lines if line.startswith("RUN pip install"))
    assert (
        copy == f"COPY {wheel_name()} {ACME_WHEEL} {BUNDLE_CONSTRAINTS_NAME} {BUNDLE_WHEELS_DIR}/"
    )
    assert f"-c {BUNDLE_WHEELS_DIR}/{BUNDLE_CONSTRAINTS_NAME} " in install
    assert f"{BUNDLE_WHEELS_DIR}/{wheel_name()}" in install
    assert f"{BUNDLE_WHEELS_DIR}/{ACME_WHEEL}" in install
    assert install.endswith(f"&& rm -r {BUNDLE_WHEELS_DIR}")


def test_bundle_enforces_converter_policy() -> None:
    assert f"RUN {DISABLE_COMMAND}\n" in _dockerfile()


def test_bundle_names_the_distributions_the_loader_trusts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UFO_FIRST_PARTY_DISTRIBUTIONS", raising=False)
    assert "UFO_FIRST_PARTY_DISTRIBUTIONS=ufo\n" in _dockerfile()
    monkeypatch.setenv("UFO_FIRST_PARTY_DISTRIBUTIONS", "acme")
    assert "UFO_FIRST_PARTY_DISTRIBUTIONS=acme,ufo\n" in _dockerfile()


def test_bundle_installs_the_checked_zlib_package_before_wheels() -> None:
    dockerfile = _dockerfile()
    assert "COPY zlib /security" in dockerfile
    assert "RUN sh /security/build.sh" in dockerfile
    assert "COPY --from=build /out /tmp/zlib-packages" in dockerfile
    assert "sh /tmp/zlib-packages/install.sh /tmp/zlib-packages" in dockerfile
    assert dockerfile.index("sh /tmp/zlib-packages/install.sh") < dockerfile.index(
        "RUN pip install"
    )


def test_bundle_installs_the_checked_lxml_wheel_as_a_direct_requirement() -> None:
    dockerfile = _dockerfile()
    assert "COPY lxml/build.sh /lxml-security/build.sh" in dockerfile
    assert "RUN sh /lxml-security/build.sh" in dockerfile
    assert f"COPY --from=lxml-build /out/*.whl {BUNDLE_WHEELS_DIR}/" in dockerfile
    assert f"{BUNDLE_WHEELS_DIR}/lxml-*.whl" in dockerfile
    assert "/out/module-sha256.json /usr/local/share/ufo-lxml-zlib/" in dockerfile

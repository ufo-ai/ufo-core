from pathlib import Path

from ufo.bundle import Bundle, wheel_name


def test_bundle_installs_the_ufo_wheel() -> None:
    dockerfile = Bundle(
        config_path=Path("ufo.toml"),
        catalog=None,
        out=Path("bundle"),
        wheel=Path(wheel_name()),
        client_binary=Path("ufo-sandbox-client"),
    )._dockerfile()

    install = next(line for line in dockerfile.splitlines() if line.startswith("RUN pip install"))
    assert install.count(wheel_name()) == 2

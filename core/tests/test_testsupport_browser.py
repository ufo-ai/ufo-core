import sys
from pathlib import Path

import pytest
from ufo_testsupport.browser import (
    MISSING_BROWSER_REASON,
    PLAYWRIGHT_PATH_ENV,
    chrome_for_testing,
    headless_flags,
)

from ufo.sandbox.session import PLAYWRIGHT_CHROMIUM_REVISION, PLAYWRIGHT_VERSION


@pytest.mark.parametrize(
    ("platform", "executable"),
    (
        (
            "darwin",
            "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/"
            "Google Chrome for Testing",
        ),
        ("linux", "chrome-linux64/chrome"),
    ),
)
def test_only_the_pinned_browser_resolves(
    platform: str, executable: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setenv(PLAYWRIGHT_PATH_ENV, str(tmp_path))
    pinned = tmp_path / f"chromium-{PLAYWRIGHT_CHROMIUM_REVISION}" / executable
    newer = tmp_path / "chromium-1300" / executable
    for binary in (pinned, newer):
        binary.parent.mkdir(parents=True)
        binary.write_text("")

    assert chrome_for_testing() == str(pinned)
    pinned.unlink()
    assert chrome_for_testing() is None
    assert f"playwright@{PLAYWRIGHT_VERSION}" in MISSING_BROWSER_REASON


def test_headless_flags_keep_containment_linux_only() -> None:
    macos = headless_flags("darwin")
    linux = headless_flags("linux")
    for flag in ("--use-mock-keychain", "--password-store=basic"):
        assert flag in macos
        assert flag in linux
    for flag in ("--no-sandbox", "--disable-dev-shm-usage"):
        assert flag not in macos
        assert flag in linux

from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize("filename", ["Dockerfile", "testbed.Dockerfile"])
def test_preview_keeps_openpgp_without_cms_or_archive_tools(filename: str) -> None:
    text = " ".join((ROOT / "servers/preview" / filename).read_text().split())
    assert "gpg gpg-agent gpgconf" in text
    assert "RUN apt-get purge -y gnupg gpgsm" in text
    assert (
        "for package in gpg gpg-agent gpgconf "
        "libreoffice-writer libreoffice-calc libreoffice-impress;"
    ) in text
    assert "'install ok installed' || exit 1" in text
    for tool in ("gpgsm", "gpgtar"):
        assert f"&& ! command -v {tool}" in text
        assert f"&& test ! -e /usr/bin/{tool}" in text
    assert '&& test -z "$(dpkg --audit)"' in text

import json
from pathlib import Path

ROOT = Path(__file__).parents[3]
ASSETS = ROOT / "core/src/ufo/runtime/security/lxml"


def test_lxml_build_keeps_versions_and_checks_both_embedded_libraries() -> None:
    provenance = json.loads((ASSETS / "provenance.json").read_text())
    assert {key: provenance[key] for key in ("lxml", "libxml2", "libxslt", "libiconv", "zlib")} == {
        "lxml": "6.1.2",
        "libxml2": "2.14.6",
        "libxslt": "1.1.43",
        "libiconv": "1.18",
        "zlib": "1.3.2",
    }
    assert provenance["upstreamFix"].endswith("df84af25dc1942490e1d1c899a07619152a46148")
    assert set(provenance["libxml2Fixes"]) == {
        "CVE-2026-86138",
        "CVE-2026-86140",
        "CVE-2026-86142",
        "CVE-2026-86143",
        "CVE-2026-86144",
    }
    build = (ASSETS / "build.sh").read_text()
    assert "sha256sum -c" in build
    assert "--fuzz=0" in build
    assert 'test "$before" = 42' in build
    assert "for module in etree objectify" in build
    assert "for name in dict valid xpointer io xinclude" in build
    assert 'timeout 60 "/checks/test-$name"' in build
    assert "WITHOUT_ZLIB" not in build
    check = (ASSETS / "check.py").read_text()
    assert "etree.XMLParser(decompress=True)" in check
    assert "check_zlib_state() == 0" in check
    assert "hashes == json.loads(receipt.read_text())" in check


def test_embedded_zlib_regression_is_the_same_bounded_first_write_check() -> None:
    assert (ASSETS / "test-write-state.c").read_bytes() == (
        ASSETS.parent / "zlib/test-write-state.c"
    ).read_bytes()
    assert "gzclearerr" not in (ASSETS / "test-write-state.c").read_text()

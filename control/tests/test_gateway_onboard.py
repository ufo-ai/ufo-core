import re

import pytest

from ufo_control.gateway import STAMPED_SCRIPT, _stamp_script
from ufo_control.gateway_invite import CODE_ALPHABET, hash_invite, mint_code

RAW_SCRIPT = 'UFO_SCRIPT_VERSION=dev\nUFO_URL="${UFO_URL:-https://flyingobject.ai}"\n'


def test_stamp_substitutes_version_and_public_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UFO_PUBLIC_BASE_URL", "https://testing.flyingobject.ai")
    stamped = _stamp_script(RAW_SCRIPT)
    assert "UFO_SCRIPT_VERSION=dev" not in stamped
    assert re.search(r"UFO_SCRIPT_VERSION=[0-9a-f]{12}", stamped) is not None
    assert 'UFO_URL="${UFO_URL:-https://testing.flyingobject.ai}"' in stamped


def test_shipped_script_is_stamped_and_targets_chat() -> None:
    assert "UFO_SCRIPT_VERSION=dev" not in STAMPED_SCRIPT
    assert re.search(r"UFO_SCRIPT_VERSION=[0-9a-f]{12}", STAMPED_SCRIPT) is not None
    assert "surface/ufo/" in STAMPED_SCRIPT


def test_minted_codes_are_grouped_and_hash_ignores_case_and_whitespace() -> None:
    code = mint_code()
    groups = code.split("-")
    assert len(groups) == 3
    assert all(len(group) == 4 and set(group) <= set(CODE_ALPHABET) for group in groups)
    assert hash_invite(f"  {code.upper()} ") == hash_invite(code)

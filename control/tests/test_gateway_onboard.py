import re

import pytest

from ufo_control.gateway import INVITE_REQUIRED_ENV, STAMPED_SCRIPT, _invite_required, _stamp_script

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


def test_invite_required_defaults_on_and_fails_loud_on_non_boolean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(INVITE_REQUIRED_ENV, raising=False)
    assert _invite_required() is True
    monkeypatch.setenv(INVITE_REQUIRED_ENV, "1")
    assert _invite_required() is True
    monkeypatch.setenv(INVITE_REQUIRED_ENV, "false")
    assert _invite_required() is False
    monkeypatch.setenv(INVITE_REQUIRED_ENV, "yes")
    with pytest.raises(RuntimeError, match="not a boolean"):
        _invite_required()

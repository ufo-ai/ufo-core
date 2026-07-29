import pytest
from ufo_testsupport.plugin import (
    INTEGRATION_REQUIRED_ENV,
    integration_dependency_available,
)


def test_an_unavailable_dependency_fails_the_required_gate_and_is_optional_elsewhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(INTEGRATION_REQUIRED_ENV, raising=False)
    assert integration_dependency_available(True, "Docker executable is not available")
    assert not integration_dependency_available(False, "Docker executable is not available")

    monkeypatch.setenv(INTEGRATION_REQUIRED_ENV, "1")
    assert integration_dependency_available(True, "Docker executable is not available")
    with pytest.raises(
        RuntimeError,
        match="required integration dependency unavailable: Docker executable is not available",
    ):
        integration_dependency_available(False, "Docker executable is not available")

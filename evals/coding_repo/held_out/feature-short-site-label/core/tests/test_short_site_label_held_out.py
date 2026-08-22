from uuid import uuid4

import pytest

from ufo.auth.bearer import UFO_TOKEN_SECRET_ENV
from ufo.sandbox.ingress_host import SIGNATURE_BYTES, site_label


def test_site_label_uses_four_signature_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, "secret")
    assert SIGNATURE_BYTES == 4
    assert len(site_label(uuid4(), 8000)) == 36

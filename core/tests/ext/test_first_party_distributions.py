from types import SimpleNamespace

import pytest
from ufo_ext_sample.census import first_noted
from ufo_ext_sample.spend import SampleGate

import ufo.host.ext.loader as loader
from ufo.host.ext.loader import FIRST_PARTY_ENV, discovered, first_party_distributions
from ufo.product import CensusSpec
from ufo.runtime.billing.spend import SpendGateSpec
from ufo.runtime.ext.manifest import Manifest


def test_the_runtime_distribution_is_always_first_party(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(FIRST_PARTY_ENV, raising=False)
    assert first_party_distributions() == {"ufo"}
    monkeypatch.setenv(FIRST_PARTY_ENV, "beta, acme,")
    assert first_party_distributions() == {"ufo", "beta", "acme"}


@pytest.mark.parametrize(
    "privileged",
    [
        Manifest(name="acme", version="0", member_context_read=True),
        Manifest(name="acme", version="0", vault_read=True),
        Manifest(
            name="acme",
            version="0",
            census=(CensusSpec(stage="acme_noted", step=None, first_at=first_noted),),
        ),
        Manifest(
            name="acme",
            version="0",
            spend_gates=(SpendGateSpec(name="acme_gate", build=SampleGate),),
        ),
    ],
    ids=["member_context_read", "vault_read", "census", "spend_gates"],
)
def test_a_privileged_manifest_is_refused_unless_its_distribution_is_named(
    monkeypatch: pytest.MonkeyPatch, privileged: Manifest
) -> None:
    acme = SimpleNamespace(
        dist=SimpleNamespace(name="acme"),
        module="acme_ext",
        load=lambda: lambda: privileged,
    )
    monkeypatch.setattr(loader, "entry_points", lambda group: (acme,))
    monkeypatch.delenv(FIRST_PARTY_ENV, raising=False)
    with pytest.raises(ValueError, match="cannot declare privileged capabilities"):
        discovered()
    monkeypatch.setenv(FIRST_PARTY_ENV, "acme")
    assert set(discovered()) == {"acme"}

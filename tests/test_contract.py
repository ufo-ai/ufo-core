import pytest
from pydantic import ValidationError

from selfhost_k8s.contract import DeployRequest, ImageRef, TenantIdentity
from selfhost_k8s.kube import request_from_tenant, tenant_body

DIGEST = "sha256:" + "a" * 64


def _request(**overrides: object) -> DeployRequest:
    base: dict[str, object] = {
        "tenant": {"name": "acme", "host": "acme.selfhost.app", "owner_email": "you@acme.com"},
        "bundle_image": {"repository": "ghcr.io/acme/selfhost", "digest": DIGEST},
        "sandbox_image": {"repository": "ghcr.io/acme/sandbox", "digest": DIGEST},
        "config_toml": '[pack]\nname = "assistant"\n',
        "pack": "assistant",
    }
    base.update(overrides)
    return DeployRequest.model_validate(base)


def test_request_round_trips_through_the_tenant_object() -> None:
    request = _request()
    restored = request_from_tenant(tenant_body(request))
    assert restored == request


def test_image_ref_requires_a_pinned_digest() -> None:
    with pytest.raises(ValidationError):
        ImageRef(repository="ghcr.io/acme/selfhost", digest="latest")


def test_image_ref_builds_a_digest_ref() -> None:
    assert ImageRef(repository="r/x", digest=DIGEST).ref == f"r/x@{DIGEST}"


def test_tenant_name_is_a_lowercased_dns_label() -> None:
    assert TenantIdentity(name="Acme", host="h", owner_email="e").name == "acme"


@pytest.mark.parametrize("bad", ["1acme", "ac me", "a" * 41, ""])
def test_tenant_name_rejects_non_labels(bad: str) -> None:
    with pytest.raises(ValidationError):
        TenantIdentity(name=bad, host="h", owner_email="e")


def test_namespace_is_derived_from_the_name() -> None:
    assert _request().tenant.namespace == "selfhost-acme"


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _request(mystery=1)

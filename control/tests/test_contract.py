import pytest
from pydantic import ValidationError
from ufo.deploy import DeployImage, DeployRequest, TenantIdentity

from ufo_control.kube import request_from_tenant, tenant_body
from ufo_control.platform import tenant_namespace

DIGEST = "sha256:" + "a" * 64


def _request(**overrides: object) -> DeployRequest:
    base: dict[str, object] = {
        "tenant": {"name": "acme", "host": "acme.ufo.app", "owner_email": "you@acme.com"},
        "bundle_image": {"repository": "ghcr.io/acme/ufo", "digest": DIGEST},
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
        DeployImage(repository="ghcr.io/acme/ufo", digest="latest")


def test_tenant_name_is_a_lowercased_dns_label() -> None:
    assert TenantIdentity(name="Acme", host="h", owner_email="e").name == "acme"


def test_namespace_is_derived_from_the_name() -> None:
    assert tenant_namespace(_request().tenant.name) == "ufo-acme"


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _request(mystery=1)

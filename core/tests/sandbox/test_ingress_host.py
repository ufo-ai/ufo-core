import base64
from uuid import uuid4

import pytest

from ufo.auth.bearer import UFO_TOKEN_SECRET_ENV
from ufo.sandbox.ingress_host import (
    ADDRESS_BYTES,
    BASE32_BITS_PER_CHAR,
    DNS_LABEL_MAX_CHARS,
    LABEL_CHARS,
    SIGNATURE_BYTES,
    SiteLabelError,
    parse_site_label,
    serve_port,
    shipped_anchor,
    shipped_app_slug,
    site_label,
)


def test_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, "s3cret")
    conversation_id = uuid4()
    assert parse_site_label(site_label(conversation_id, 8000)) == (conversation_id, 8000)


def test_one_conversation_port_is_one_stable_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    """The label is an address, not a nonce: it is the same every time for a `(conversation, port)`
    — a member's bookmark and their site's cookies survive a redeploy — and every other pair is a
    different origin."""
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, "s3cret")
    conversation_id = uuid4()
    label = site_label(conversation_id, 8000)
    assert site_label(conversation_id, 8000) == label
    assert site_label(conversation_id, 3000) != label
    assert site_label(uuid4(), 8000) != label


def test_a_shipped_app_has_one_workspace_origin() -> None:
    """The provision identity supplies the stable slug, and that slug gives each workspace one
    synthetic conversation and port even though no hosted-site row backs the page."""
    workspace_id = uuid4()
    anchor = shipped_anchor(workspace_id, "artifacts")
    assert shipped_app_slug("app_artifacts") == "artifacts"
    assert shipped_app_slug("artifacts") is None
    assert shipped_app_slug(None) is None
    assert shipped_anchor(workspace_id, "artifacts") == anchor
    assert shipped_anchor(uuid4(), "artifacts") != anchor
    assert 20000 <= serve_port(anchor) < 40000


def test_a_label_fits_one_dns_label(monkeypatch: pytest.MonkeyPatch) -> None:
    """The encoder agrees with the bound the module refuses to import past, and draws only on
    characters a hostname may carry — so the wildcard record resolves whatever the label."""
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, "s3cret")
    label = site_label(uuid4(), 65535)
    assert len(label) == LABEL_CHARS <= DNS_LABEL_MAX_CHARS
    assert set(label) <= set("abcdefghijklmnopqrstuvwxyz234567")


def test_case_folds_because_dns_does(monkeypatch: pytest.MonkeyPatch) -> None:
    """A resolver, a proxy, or a member may hand back any case of the same hostname."""
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, "s3cret")
    conversation_id = uuid4()
    label = site_label(conversation_id, 8000)
    assert parse_site_label(label.upper()) == (conversation_id, 8000)


def test_only_the_canonical_spelling_addresses_the_site(monkeypatch: pytest.MonkeyPatch) -> None:
    """A label carries more bits than the address fills, and `b32decode` throws the difference
    away, so several spellings decode alike. A browser reads each as a separate origin with its own
    cookie jar and storage, fracturing one site across them — so exactly one spelling parses and the
    rest are refused. The count comes from the module's own arithmetic rather than a literal, so
    shortening the signature cannot leave this asserting the old width."""
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, "s3cret")
    conversation_id = uuid4()
    label = site_label(conversation_id, 8000)
    padding = "=" * (-len(label) % 8)
    decoded = base64.b32decode(label + padding, casefold=True)
    aliases = [
        candidate
        for character in "abcdefghijklmnopqrstuvwxyz234567"
        if (candidate := label[:-1] + character) != label
        and base64.b32decode(candidate + padding, casefold=True) == decoded
    ]
    spare_bits = LABEL_CHARS * BASE32_BITS_PER_CHAR - (ADDRESS_BYTES + SIGNATURE_BYTES) * 8
    assert len(aliases) == 2**spare_bits - 1
    assert parse_site_label(label) == (conversation_id, 8000)
    for alias in aliases:
        with pytest.raises(SiteLabelError, match="canonical"):
            parse_site_label(alias)


def test_rejects_tampered_malformed_and_foreign_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, "s3cret")
    label = site_label(uuid4(), 8000)
    tampered = ("a" if label[0] != "a" else "b") + label[1:]
    with pytest.raises(SiteLabelError):
        parse_site_label(tampered)
    with pytest.raises(SiteLabelError):
        parse_site_label(label[:-1])
    with pytest.raises(SiteLabelError):
        parse_site_label("not-a-label")
    with pytest.raises(SiteLabelError):
        parse_site_label("")
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, "another-deploys-secret")
    with pytest.raises(SiteLabelError):
        parse_site_label(label)


def test_rejects_unaddressable_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, "s3cret")
    for port in (0, -1, 65536):
        with pytest.raises(ValueError, match="addressable range"):
            site_label(uuid4(), port)

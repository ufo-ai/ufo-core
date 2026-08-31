from datetime import UTC, datetime
from urllib.parse import SplitResult, quote, urlsplit
from uuid import UUID

from ufo.harness.sandbox.ingress_host import SiteLabelError, parse_site_label, site_label
from ufo.harness.sandbox.ingress_token import (
    INGRESS_VIEW_KIND,
    INGRESS_VIEW_PATH,
    INGRESS_VIEW_TTL_SECONDS,
    FramerClaim,
    IngressClaims,
    ShippedClaim,
    mint_ingress_token,
)


def mint_ingress_view_url(
    public_url: str | None,
    workspace_id: UUID,
    conversation_id: UUID,
    port: int,
    entry_path: str,
    *,
    framed_from: str | None = None,
    shipped_slug: str | None = None,
    shipped_digest: str | None = None,
) -> str | None:
    """Mint the expiring browser URL for one workspace's sandbox port."""
    if public_url is None:
        return None
    base = urlsplit(public_url)
    shipped = (
        ShippedClaim(slug=shipped_slug, digest=shipped_digest)
        if shipped_slug is not None and shipped_digest is not None
        else None
    )
    token = mint_ingress_token(
        IngressClaims(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            port=port,
            expires_at=int(datetime.now(UTC).timestamp()) + INGRESS_VIEW_TTL_SECONDS,
            shipped=shipped,
            framer=_framer_claim(base, framed_from),
        ),
        INGRESS_VIEW_KIND,
    )
    label = site_label(conversation_id, port)
    entry = "" if entry_path == "/" else quote(entry_path, safe="/")
    return f"{base.scheme}://{label}.{base.netloc}{INGRESS_VIEW_PATH}/{token}{entry}"


def _framer_claim(base: SplitResult, framed_from: str | None) -> FramerClaim | None:
    framed = urlsplit(framed_from or "")
    base_host = base.hostname
    framed_host = framed.hostname
    if framed_host is None or base_host is None:
        return None
    try:
        if not (
            framed.scheme == base.scheme
            and framed.port == base.port
            and framed_host.endswith(f".{base_host}")
        ):
            return None
    except ValueError:
        return None
    label = framed_host.removesuffix(f".{base_host}")
    try:
        conversation_id, port = parse_site_label(label)
    except SiteLabelError:
        return None
    return FramerClaim(conversation_id=conversation_id, port=port)

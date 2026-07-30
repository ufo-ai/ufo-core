"""The per-site DNS label: a signed address for one conversation's sandbox port.

Every hosted site is served from its own origin, so a site's `/`-rooted assets and redirects
resolve and its cookies and browser storage can never reach another site's. The label naming that
origin encodes the conversation and the port, signed with a truncated HMAC over the same deploy
secret the ingress token carries: 16 bytes of conversation id, 2 of port, 16 of signature, base32
without padding — inside DNS's 63-character label bound, and case-insensitive by construction
because DNS is. The label is an address, not an authorization: it is stable for a
`(conversation, port)` so a member's bookmark and their site's stored state survive a redeploy,
while the signature keeps a stranger from addressing a conversation nobody handed them.

One site, one label, exactly. 34 bytes fill 272 of the 275 bits 55 base32 characters carry, and
base32 decoding discards the 3 that are left over — so eight labels would decode alike, and a
browser would treat all eight as separate origins with their own cookie jars and storage. Parsing
therefore re-encodes what it decoded and refuses anything but the one canonical spelling."""

import base64
import hmac
from hashlib import sha256
from uuid import UUID

from ufo.sandbox.ingress_token import ingress_secret

SITE_LABEL_KIND = b"sandbox-site"
UUID_BYTES = 16
PORT_BYTES = 2
ADDRESS_BYTES = UUID_BYTES + PORT_BYTES
SIGNATURE_BYTES = 16
BASE32_BITS_PER_CHAR = 5
DNS_LABEL_MAX_CHARS = 63
LABEL_CHARS = -(-(ADDRESS_BYTES + SIGNATURE_BYTES) * 8 // BASE32_BITS_PER_CHAR)

if LABEL_CHARS > DNS_LABEL_MAX_CHARS:
    raise RuntimeError(
        f"a {LABEL_CHARS}-character site label does not fit DNS's {DNS_LABEL_MAX_CHARS}-character "
        "label bound; shorten the signature"
    )


class SiteLabelError(ValueError):
    """A site label is malformed, or this deploy's secret did not sign it."""


def site_label(conversation_id: UUID, port: int) -> str:
    """The DNS label one conversation's sandbox port is served at."""
    if not 0 < port < 65536:
        raise ValueError(f"sandbox port {port} is outside the addressable range")
    address = conversation_id.bytes + port.to_bytes(PORT_BYTES, "big")
    return _encode(address + _signature(address))


def parse_site_label(label: str) -> tuple[UUID, int]:
    """The conversation and port a label addresses, raising `SiteLabelError` unless this deploy's
    secret signed exactly these bytes and the label is their one canonical spelling."""
    try:
        decoded = base64.b32decode(label + "=" * (-len(label) % 8), casefold=True)
    except ValueError as error:
        raise SiteLabelError("site label is malformed") from error
    if _encode(decoded) != label.lower():
        raise SiteLabelError("site label is not the canonical spelling of its bytes")
    address, signature = decoded[:ADDRESS_BYTES], decoded[ADDRESS_BYTES:]
    if len(signature) != SIGNATURE_BYTES or not hmac.compare_digest(signature, _signature(address)):
        raise SiteLabelError("site label signature does not match")
    return UUID(bytes=address[:UUID_BYTES]), int.from_bytes(address[UUID_BYTES:], "big")


def _encode(raw: bytes) -> str:
    return base64.b32encode(raw).decode().rstrip("=").lower()


def _signature(address: bytes) -> bytes:
    digest = hmac.new(ingress_secret().encode(), SITE_LABEL_KIND + address, sha256).digest()
    return digest[:SIGNATURE_BYTES]

"""The per-site DNS label: a signed address for one conversation's sandbox port.

Every hosted site is served from its own origin, so a site's `/`-rooted assets and redirects
resolve and its cookies and browser storage can never reach another site's. The label naming that
origin encodes the conversation and the port, signed with a truncated HMAC over the same deploy
secret the ingress token carries: 16 bytes of conversation id, 2 of port, 4 of signature, base32
without padding — 36 characters, inside DNS's 63-character label bound, and case-insensitive by
construction because DNS is. The label is an address, not an authorization: it is stable for a
`(conversation, port)` so a member's bookmark and their site's stored state survive a redeploy.

The signature is 4 bytes, sized by what forging one wins rather than by habit, and what it wins is
nothing on its own. Both ways in carry the `(conversation, port)` they claim and must match the
label: the view path verifies a minted token, every later request a session cookie, and a mismatch
is 403 before a sandbox is dialed or a row is read. So the signature never stands between a stranger
and a site — it keeps a guessed hostname from reaching the conversation read at all, and a member
reads and copies 19 characters fewer for it. What 32 bits costs is an attacker willing to spend
billions of requests to have one chosen conversation's row read and then refused; a wider tag buys
nothing past that, because the cookie is the gate.

One site, one label, exactly. 22 bytes fill 176 of the 180 bits 36 base32 characters carry, and
base32 decoding discards the 4 that are left over — so sixteen labels decode alike, and a browser
would treat all sixteen as separate origins with their own cookie jars and storage. Parsing
therefore re-encodes what it decoded and refuses anything but the one canonical spelling."""

import base64
import hmac
import re
from hashlib import sha256
from uuid import NAMESPACE_URL, UUID, uuid5

from ufo.harness.sandbox.ingress_token import ingress_secret

SITE_LABEL_KIND = b"sandbox-site"
APP_PORT_FLOOR = 20000
APP_PORT_SPAN = 20000
SHIPPED_ANCHOR_LABEL = "ufo-shipped-app"
SHIPPED_APP_PROVISION_RE = re.compile(r"app_(?P<slug>[a-z0-9]+)")
UUID_BYTES = 16
PORT_BYTES = 2
ADDRESS_BYTES = UUID_BYTES + PORT_BYTES
SIGNATURE_BYTES = 4
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


def serve_port(conversation_id: UUID) -> int:
    """The stable sandbox port assigned to a conversation's hosted site.

    Per-conversation ports keep host-path sandboxes from displacing one another while container
    carriers remain indifferent. The port is half of the site's durable origin, so every producer
    derives it here from the conversation rather than storing a second answer."""
    return APP_PORT_FLOOR + conversation_id.int % APP_PORT_SPAN


def shipped_app_slug(provisioned_by: str | None) -> str | None:
    """The page slug carried by an app extension's provision name.

    The extension identity is stable where a provisioned agent's member-visible name may be
    suffixed on collision, so origins and bundle paths derive from it rather than the row name."""
    if provisioned_by is None:
        return None
    match = SHIPPED_APP_PROVISION_RE.fullmatch(provisioned_by)
    return None if match is None else match["slug"]


def shipped_anchor(workspace_id: UUID, slug: str) -> UUID:
    """The stable per-workspace origin anchor for one shipped app page.

    No conversation row backs a shipped page. This synthetic identity gives each workspace's copy
    its own cookies and storage and stays the same when that app is forked into a hosted site."""
    return uuid5(NAMESPACE_URL, f"{SHIPPED_ANCHOR_LABEL}:{workspace_id}:{slug}")


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

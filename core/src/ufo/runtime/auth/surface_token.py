"""A surface's permanent signed address: an opaque token carrying the claims a route needs before
any workspace is bound.

A surface route reached by link alone has no cookie to scope by — the shared fleet's
`SurfaceSpec.identify` must name the workspace from the URL itself, before a single row is read.
This is that carrier: an HMAC over the deploy's `UFO_TOKEN_SECRET`, resolved here exactly as
`ufo.runtime.auth.bearer` resolves it, so a surface hands over claims and gets claims back without
ever holding the key. The signed body carries the minting surface's own name and
`verify_surface_token` demands it match, so one surface's token yields nothing at another's route.

It is an **address, not an authorization**: nothing expires, and every request it opens still passes
its route's own gate. A claim value is always a string — the payload crosses a URL."""

import json
import os
from collections.abc import Mapping

from ufo.runtime.auth.bearer import UFO_TOKEN_SECRET_ENV
from ufo.runtime.auth.token_signing import SignedTokenError, sign_token, verify_token

SURFACE_CLAIM = "surface"


def mint_surface_token(surface: str, payload: Mapping[str, str]) -> str:
    """Sign `payload` as `surface`'s own address. Fails loud on an empty surface name and on a
    payload claiming the reserved `surface` key, which would let a caller forge the namespace the
    verify half trusts."""
    if not surface:
        raise ValueError("a surface token must name its surface")
    if SURFACE_CLAIM in payload:
        raise ValueError(f"{SURFACE_CLAIM!r} is the reserved surface-namespace claim")
    body = json.dumps(
        {SURFACE_CLAIM: surface, **payload}, separators=(",", ":"), sort_keys=True
    ).encode()
    return sign_token(_secret().encode(), body)


def verify_surface_token(surface: str, token: str) -> dict[str, str] | None:
    """The claims `token` proves for `surface`, or None when its signature, shape, or surface
    namespace fails. Nothing is trusted before the HMAC over this deploy's secret matches, so a
    forged token and one minted for another surface both yield nothing to resolve by."""
    try:
        payload = json.loads(verify_token(token, _secret().encode()))
    except (SignedTokenError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get(SURFACE_CLAIM) != surface:
        return None
    claims = {key: value for key, value in payload.items() if key != SURFACE_CLAIM}
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in claims.items()):
        return None
    return claims


def _secret() -> str:
    value = os.environ.get(UFO_TOKEN_SECRET_ENV)
    if not value:
        raise RuntimeError(f"{UFO_TOKEN_SECRET_ENV} must be set to sign and verify surface tokens")
    return value

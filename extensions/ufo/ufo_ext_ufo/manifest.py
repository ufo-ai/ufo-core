"""The ufo extension's manifest: one live surface on the core seam, the terminal wire the `ufo`
shell client renders. No credential slots — the member's bearer is verified against the env
`UFO_TOKEN_SECRET`, not a workspace slot — and no config knob: installed means mounted, like web.
It declares one route (the held per-channel stream) and no writeback delivery: it admits without
writeback and tails the hub in that same route."""

from ufo.sdk.manifest import Manifest
from ufo.sdk.surfaces import SurfaceSpec
from ufo_ext_ufo.surface import ROUTES, SURFACE_UFO

NAME = "ufo"
VERSION = "0.1.0"


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        surfaces=(SurfaceSpec(name=SURFACE_UFO, routes=ROUTES),),
    )

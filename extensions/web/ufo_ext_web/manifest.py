"""The web extension's manifest: one surface on the core seam, live mode. No credential slots (its
session cookie carries the member's own token, not a bot secret) and no config knob — installed
means mounted, like Slack. It declares four routes and no writeback delivery: web admits without
writeback and tails the hub in its own stream route."""

from ufo.sdk.manifest import Manifest
from ufo.sdk.surfaces import SurfaceSpec
from ufo_ext_web.surface import ROUTES, SURFACE_WEB

NAME = "web"
VERSION = "0.1.0"


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        surfaces=(SurfaceSpec(name=SURFACE_WEB, routes=ROUTES),),
    )

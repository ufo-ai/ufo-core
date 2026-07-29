"""The web extension's manifest: one surface on the core seam, live mode, plus the two chat verbs
maintaining the surface's own audience (#645 — the web surface is its audience authority). No
credential slots (its session cookie carries the member's own token, not a bot secret) and no
config knob — installed means mounted, like Slack. The surface admits without writeback and tails
the hub in its own stream route."""

from ufo.sdk.manifest import Manifest
from ufo.sdk.surfaces import SurfaceSpec
from ufo_ext_web.audience import EXTENSION_WEB, WEB_ACCESS_TOOLS
from ufo_ext_web.surface import ROUTES, SURFACE_WEB, resolve_workspace

NAME = EXTENSION_WEB
VERSION = "0.1.0"


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=WEB_ACCESS_TOOLS,
        surfaces=(SurfaceSpec(name=SURFACE_WEB, routes=ROUTES, identify=resolve_workspace),),
    )

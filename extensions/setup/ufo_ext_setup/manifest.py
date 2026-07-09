"""The setup extension's manifest: one surface on the core seam, live mode — the first-run portal
that connects Slack and reports its state. No credential slots of its own (it reads and fills the
slack surface's slots through the privileged context; slots are workspace-global) and no writeback
delivery: every route is plain request/response."""

from ufo.sdk.manifest import Manifest
from ufo.sdk.surfaces import SurfaceSpec
from ufo_ext_setup.surface import ROUTES, SURFACE_SETUP

NAME = "setup"
VERSION = "0.1.0"


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        surfaces=(SurfaceSpec(name=SURFACE_SETUP, routes=ROUTES),),
    )

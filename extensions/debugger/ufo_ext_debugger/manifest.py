"""The debugger extension's manifest: one live surface on the core seam, mounted only where its
`identify` resolver can authenticate — the shared fleet — and reachable only by a gateway bearer
whose email domain is the operator's."""

from ufo.sdk.manifest import Manifest
from ufo.sdk.operator import resolve_operator_workspace
from ufo.sdk.surfaces import SurfaceSpec
from ufo_ext_debugger.surface import ROUTES, SURFACE_DEBUG

NAME = "debugger"
VERSION = "0.1.0"


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        surfaces=(
            SurfaceSpec(name=SURFACE_DEBUG, routes=ROUTES, identify=resolve_operator_workspace),
        ),
    )

"""The debugger extension's manifest: one live surface on the core seam, mounted only where its
`identify` resolver can authenticate — the shared fleet — and reachable only by a gateway bearer
whose email domain is the operator's, plus `report_problem`, the tool that pushes one workspace
fault to those operators with a link into that surface."""

from ufo.sdk.manifest import Manifest
from ufo.sdk.operator import resolve_operator_workspace
from ufo.sdk.surfaces import SurfaceSpec
from ufo_ext_debugger.report import REPORT_PROBLEM_TOOL_DEF
from ufo_ext_debugger.surface import ROUTES, SURFACE_DEBUG

NAME = "debugger"
VERSION = "0.1.0"


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=(REPORT_PROBLEM_TOOL_DEF,),
        surfaces=(
            SurfaceSpec(name=SURFACE_DEBUG, routes=ROUTES, identify=resolve_operator_workspace),
        ),
    )

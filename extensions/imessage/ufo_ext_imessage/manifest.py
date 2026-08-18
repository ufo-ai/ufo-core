from ufo.sdk.manifest import Manifest
from ufo.sdk.surfaces import SurfaceSpec
from ufo.sdk.tools import ToolDef
from ufo_ext_imessage.cloud import (
    SPECTRUM_PROJECT_ID_ENV,
    SPECTRUM_PROJECT_SECRET_ENV,
    spectrum_project,
)
from ufo_ext_imessage.surface import SURFACE_IMESSAGE, ImessageSurface
from ufo_ext_imessage.tools import ImessageConnect, ImessageConnectInput

NAME = "imessage"
VERSION = "0.1.0"


def manifest() -> Manifest:
    surface = ImessageSurface(provider=spectrum_project)
    connect = ImessageConnect(provider=spectrum_project)
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=(
            ToolDef(
                name="imessage_connect",
                description=(
                    "Connect the requesting member's iMessage phone. An admin connects the "
                    "provider; each member confirms their own phone by replying to a direct "
                    "message."
                ),
                input_model=ImessageConnectInput,
                handler=connect.run,
                untrusted=True,
                side_effecting=True,
            ),
        ),
        surfaces=(
            SurfaceSpec(
                name=SURFACE_IMESSAGE,
                listen=surface.listen,
                post=surface.post,
                attach=surface.attach,
                speak=surface.speak,
            ),
        ),
        deploy_keys=(SPECTRUM_PROJECT_ID_ENV, SPECTRUM_PROJECT_SECRET_ENV),
    )

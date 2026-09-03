from ufo.sdk.manifest import Manifest
from ufo.sdk.objects import SURFACE_KIND
from ufo.sdk.surfaces import SurfaceSpec
from ufo.sdk.tools import ActionPresentation, ObjectBinding, ToolDef
from ufo_ext_imessage.cloud import (
    SPECTRUM_PROJECT_ID_ENV,
    SPECTRUM_PROJECT_SECRET_ENV,
    line_provider,
)
from ufo_ext_imessage.surface import SURFACE_IMESSAGE, ImessageSurface
from ufo_ext_imessage.tools import (
    IMESSAGE_CONNECT_ACTION,
    ImessageConnect,
    ImessageConnectInput,
)

NAME = "imessage"
VERSION = "0.1.0"


def manifest() -> Manifest:
    surface = ImessageSurface(provider=line_provider)
    connect = ImessageConnect(provider=line_provider)
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=(
            ToolDef(
                name=IMESSAGE_CONNECT_ACTION,
                description=(
                    "Connect the requesting member's iMessage phone. An admin connects the "
                    "provider; each member proves control by texting the pending result's code to "
                    "the assigned line. Relay the line, the code and the sms: link exactly. A "
                    "connected result means that phone already reaches this workspace."
                ),
                input_model=ImessageConnectInput,
                handler=connect.run,
                bound=ObjectBinding(kind=SURFACE_KIND, binding="instance", name=SURFACE_IMESSAGE),
                untrusted=True,
                side_effecting=True,
                presentation=ActionPresentation(label="Connect iMessage"),
            ),
        ),
        surfaces=(
            SurfaceSpec(
                name=SURFACE_IMESSAGE,
                addressed=True,
                listen=surface.listen,
                post=surface.post,
                attach=surface.attach,
                speak=surface.speak,
            ),
        ),
        deploy_keys=(SPECTRUM_PROJECT_ID_ENV, SPECTRUM_PROJECT_SECRET_ENV),
    )

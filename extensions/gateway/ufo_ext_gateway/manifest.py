"""The onboarding backend's declared points: two routes on the public seam.

`GET /ext/gateway/ufo` serves the version-stamped `ufo` client; `POST
/ext/gateway/onboard/{channel}` drives the code-verify + join-or-provision sign-in as directives.
The apex Ingress rewrites the two public paths (`/ufo`, `/v1/onboard/{channel}`) onto these. The
extension runs in a dedicated platform workspace (pack `gateway`) and imports only `ufo.sdk`."""

from ufo.sdk.manifest import Manifest, RouteSpec
from ufo_ext_gateway.onboard import onboard, serve_script

NAME = "gateway"
VERSION = "0.1.0"


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        routes=(
            RouteSpec(method="GET", path="ufo", handler=serve_script),
            RouteSpec(method="POST", path="onboard/{channel}", handler=onboard),
        ),
    )

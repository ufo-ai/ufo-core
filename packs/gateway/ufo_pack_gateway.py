"""The gateway pack: the apex onboarding backend, run as its own platform workspace.

Activating it (config `[pack] name = "gateway"`) brings up exactly the onboarding extension — the
version-stamped `ufo` client delivery and the code-verify + join-or-provision sign-in. It is a whole
`ufoctl serve` of the same bundle image as every tenant, provisioned through the control plane like
any tenant with host `flyingobject.ai`, so the hosted service dogfoods the extension seam."""

from ufo.sdk.manifest import Pack

NAME = "gateway"
VERSION = "0.1.0"
EXTENSIONS = ("gateway",)


def pack() -> Pack:
    return Pack(name=NAME, version=VERSION, extensions=EXTENSIONS)

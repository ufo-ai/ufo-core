"""What the eval-harness extension declares. Running a suite is an operator act (the `evals` runner
drives it against the workspace), so the pack contributes no agent-runtime point — its entry point
exists so the harness is a real sdk-only package the CI import gate pins, and so it is discoverable
alongside the other packs."""

from selfhost.sdk.manifest import Manifest

NAME = "eval_harness"
VERSION = "0.1.0"


def manifest() -> Manifest:
    return Manifest(name=NAME, version=VERSION)

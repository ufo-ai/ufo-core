"""The conformance sample pack: a real installed pack that exercises the whole packs seam.

It imports only `ufo.sdk` — the surface a CI gate pins — and its `ufo.pack` entry point
returns a Pack that bundles the sample extension and contributes one pack-level skill and one
onboarding step of its own. Activating it (`load_manifests("sample_pack")`) narrows the active set
to the sample extension's manifest plus a manifest carrying the pack's skill and onboarding step.
The onboarding step records through the pack's scoped store (a durable `ext_store` row, never a mock
log), so the conformance test reads it back through the same public surface core writes it by;
breaking this pack breaks the packs-seam probe."""

from pathlib import Path

from ufo.sdk.context import ExtensionContext
from ufo.sdk.manifest import OnboardingStep, Pack, SkillSpec

NAME = "sample_pack"
VERSION = "0.1.0"
BUNDLED_EXTENSION = "sample"
SKILL_NAME = "sample_pack_skill"
SKILL_DIR = Path(__file__).parent / "skills" / SKILL_NAME
ONBOARDING_NAME = "sample_pack_setup"
ONBOARDING_KEY = "pack:onboarded"


async def _setup(ctx: ExtensionContext) -> None:
    await ctx.store.put(ONBOARDING_KEY, {"pack_onboarded": True})


def pack() -> Pack:
    return Pack(
        name=NAME,
        version=VERSION,
        extensions=(BUNDLED_EXTENSION,),
        skills=(SkillSpec(path=SKILL_DIR),),
        onboarding_steps=(OnboardingStep(name=ONBOARDING_NAME, handler=_setup),),
    )

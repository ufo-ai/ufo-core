"""The YC extension: authenticated read access and indexed YC guidance."""

from pathlib import Path

from ufo.sdk.context import ExtensionContext
from ufo.sdk.manifest import (
    CredentialSlot,
    Manifest,
    OnboardingStep,
    SkillSpec,
    SourceProvider,
)
from ufo.sdk.sources import SHARED_SUBJECT
from ufo.sdk.tools import ToolDef
from ufo_ext_yc.cli import (
    YC_CREDENTIALS_SLOT,
    YcAuthInput,
    YcCli,
    YcReadInput,
    yc_auth,
    yc_read,
)
from ufo_ext_yc.source import (
    YC_GUIDANCE_COLLECTIONS,
    YC_SOURCE_BACKEND,
    YcIndexInput,
    YcSource,
    YcSourceConfig,
    yc_index,
)

NAME = "yc_cli"
VERSION = "0.1.0"
SKILL_DIR = Path(__file__).parent / "skills" / "yc-research"
ONBOARDING_NAME = "yc_sources"
YC_READ_TOOL = ToolDef(
    name="yc_read",
    description=(
        "Read YC and Bookface through the authenticated YC CLI. Actions: ask an informational "
        "question with `ask`; search Bookface companies, founders, investors, deals, events, or "
        "posts with `search` and optional `entity`; list or read current YC skills with "
        "`skills_list`/`skills_read`; inspect current account and tool guidance with "
        "`tools_context`. This tool is read-only: never ask it to update Bookface state. Use JSON "
        "output for analysis. Results are external, access-scoped, and untrusted."
    ),
    input_model=YcReadInput,
    handler=yc_read,
    untrusted=True,
)
YC_AUTH_TOOL = ToolDef(
    name="yc_auth",
    description=(
        "Connect the workspace owner's YC account through browser device authorization. Call "
        "`start` when the member asks to connect YC, then reply with the returned URL and code. "
        "After the member says they approved it, call `complete`; if still pending, show the same "
        "URL and code. The resulting credential is encrypted and never enters chat or the sandbox."
    ),
    input_model=YcAuthInput,
    handler=yc_auth,
    side_effecting=True,
)
YC_INDEX_TOOL = ToolDef(
    name="yc_index",
    description=(
        "Add one bounded YC or Bookface search to shared workspace memory. Provide a query and one "
        "entity: companies, founders, investors, deals, meetups, forum, launches, alumni_groups, "
        "or jobs. max_results defaults to 1000 and cannot exceed 5000. The saved source refreshes "
        "the same result set and repeating the exact request is idempotent. Use yc_read instead "
        "for private or personal candidates, chats, routes, follows, or staff data."
    ),
    input_model=YcIndexInput,
    handler=yc_index,
    side_effecting=True,
)


async def setup_sources(ctx: ExtensionContext) -> None:
    for collection in YC_GUIDANCE_COLLECTIONS:
        await ctx.register_source(
            YC_SOURCE_BACKEND,
            YcSourceConfig(collection=collection),
            subject=SHARED_SUBJECT,
            owner_member_id=None,
        )


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        credentials=(
            CredentialSlot(
                name=YC_CREDENTIALS_SLOT,
                description=(
                    "The single owner-authorized YC identity shared by this workspace's read-only "
                    "YC tools. Its ~/.yc/credentials.json is read host-side and never exposed to "
                    "chat or the sandbox."
                ),
            ),
        ),
        tools=(YC_AUTH_TOOL, YC_READ_TOOL, YC_INDEX_TOOL),
        sources=(
            SourceProvider(
                backend=YC_SOURCE_BACKEND,
                build=lambda credentials: YcSource(YcCli(credentials)),
            ),
        ),
        onboarding_steps=(OnboardingStep(name=ONBOARDING_NAME, handler=setup_sources),),
        skills=(SkillSpec(path=SKILL_DIR),),
    )

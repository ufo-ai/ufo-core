"""The coding subagent pack: a software-engineering child turn over the core code builtins, plus the
`coding` skill the agent loads on demand.

`spawn_subagent("coding", {"objective": ...})` runs a child that explores a repo, edits code, runs
tests, and reports a result. The profile names only tool names — bash/read/write/
edit/glob/grep to work the code, load_skill to pull a workflow, and js_repl to exercise Node
code — so the pack is self-contained and carries no cross-extension import. Nothing in it delivers
a file to the member: the child leaves work in the workspace it shares with the parent, and the
parent decides what reaches the member. Core wraps the prompt with the shared citation/formatting
discipline and fills its skill index. The `coding` skill teaches the main agent to route repo work
to that child.

The pack also declares the git credential slot, because a checkout is the work it routes: a
workspace that fills it gets authenticated `git clone` and `git push` for private repositories,
with the token swapped onto the wire at the egress proxy and only a sentinel inside the sandbox.
A source bound to a review agent turns each changed pull-request head into one conversation of that
agent, which reviews it through an exact-checkout child and publishes the typed result as an
advisory GitHub Check."""

import os
from pathlib import Path

from pydantic import BaseModel, Field

from ufo.sdk.manifest import (
    CredentialSlot,
    HookSpec,
    InjectionTarget,
    Manifest,
    RouteSpec,
    SkillSpec,
    SubagentProfile,
)
from ufo.sdk.tools import ToolDef
from ufo_ext_coding.connect import (
    GIT_INSTALLATION_SLOT,
    ROUTE_PATH,
    ConnectGitHubInput,
    connect_github,
    github_installed,
    install_workspace,
)
from ufo_ext_coding.github_app import GIT_SLOT, app_tokens
from ufo_ext_coding.review_checkout import CODE_REVIEW_PROFILE, CODE_REVIEW_TOOLS
from ufo_ext_coding.review_publish import PublishCodeReviewInput, publish_code_review
from ufo_ext_coding.review_routing import (
    ConfigureReviewInboxInput,
    configure_review_inbox,
    route_review_pages,
)

NAME = "coding"
VERSION = "0.1.0"
CODING_PROFILE_NAME = "coding"
SKILLS_ROOT = Path(__file__).parent / "skills"
SKILL_NAMES = ("coding",)
CODING_TOOL_NAMES = (
    "bash",
    "read",
    "write",
    "edit",
    "glob",
    "grep",
    "load_skill",
    "js_repl",
    "search_web",
    "fetch_url",
)
CODING_PROMPT = (Path(__file__).parent / "prompts" / "subagent_coding.md").read_text()
CODING_ROUND_LIMIT = 100


class CodingInput(BaseModel):
    objective: str = Field(
        description="Freeform task governed by the shared delivery register.",
    )
    extended_context: bool | None = Field(
        default=None,
        description="Run the child under the main agent's round ceiling instead of its default "
        "budget, for an unusually deep task that needs more tool-use rounds.",
    )


class CodingOutput(BaseModel):
    result: str = Field(
        description="Freeform result governed by the shared delivery register.",
    )


GIT_APP_ID_ENV = "GITHUB_APP_ID"
GIT_APP_CLIENT_ID_ENV = "GITHUB_APP_CLIENT_ID"
GIT_APP_SECRET_ENV = "GITHUB_APP_CLIENT_SECRET"
GIT_APP_KEY_ENV = "GITHUB_APP_PRIVATE_KEY"
GIT_HOST = "github.com"
GIT_SENTINEL = "UFO_SENTINEL_GIT_GITHUB"
GIT_BASIC_USER = "x-access-token"
GIT_INSTALLATION = CredentialSlot(
    name=GIT_INSTALLATION_SLOT,
    description="Which ufo GitHub App installation this workspace uses. Filled by installing the "
    "App: the value is a seal this deploy writes once GitHub confirms the install belongs to the "
    "member who authorized it.",
    member_filled=False,
)


def github_app_id() -> str | None:
    """The deploy's App registration, all of it or none: an id without a client id or a readable key
    is a deploy whose members would be told to connect GitHub and then silently answered with
    member-token-only mode, with no signal which half is missing."""
    registration = {
        name: os.environ.get(name)
        for name in (GIT_APP_ID_ENV, GIT_APP_CLIENT_ID_ENV, GIT_APP_SECRET_ENV, GIT_APP_KEY_ENV)
    }
    if not any(registration.values()):
        return None
    missing = [name for name, value in registration.items() if not value]
    if missing:
        raise RuntimeError(f"{', '.join(missing)} must be set with the GitHub App registration")
    return registration[GIT_APP_ID_ENV]


GIT_CREDENTIAL = CredentialSlot(
    name=GIT_SLOT,
    description="A GitHub token with repository contents read and write and Checks write — a "
    "fine-grained personal access token scoped to the repositories the agent works in. Only "
    "needed for a repository outside an organization that installed the ufo GitHub App; where "
    "the App is installed, its own token is minted per turn instead.",
    source=None if github_app_id() is None else app_tokens(GIT_INSTALLATION_SLOT),
    injection=InjectionTarget(
        host=GIT_HOST,
        header="Authorization",
        sentinel=GIT_SENTINEL,
        dimension="requests",
        git_basic_user=GIT_BASIC_USER,
    ),
)

CODING_PROFILE = SubagentProfile(
    name=CODING_PROFILE_NAME,
    prompt=CODING_PROMPT,
    tool_names=CODING_TOOL_NAMES,
    input_model=CodingInput,
    output_model=CodingOutput,
    max_rounds=CODING_ROUND_LIMIT,
)


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        subagents=(CODING_PROFILE, CODE_REVIEW_PROFILE),
        skills=tuple(SkillSpec(path=SKILLS_ROOT / name) for name in SKILL_NAMES),
        credentials=(GIT_INSTALLATION, GIT_CREDENTIAL),
        tools=(
            ToolDef(
                name="connect_github",
                description="Install the workspace's ufo GitHub App for private clone, push, and "
                "PR work: hands an admin the App install link. Admin-only. GitHub operations "
                "still need the separate connector connection.",
                input_model=ConnectGitHubInput,
                handler=connect_github,
            ),
            *CODE_REVIEW_TOOLS,
            ToolDef(
                name="configure_review_inbox",
                description="Review one shared GitHub pull-request source as this agent: every "
                "later head opens its own conversation here. Existing pages become the baseline. "
                "Admin-only.",
                input_model=ConfigureReviewInboxInput,
                handler=configure_review_inbox,
                side_effecting=True,
            ),
            ToolDef(
                name="publish_code_review",
                description="Publish the exact delivered review result as an advisory ufo review "
                "Check on the stored head for this conversation's review run.",
                input_model=PublishCodeReviewInput,
                handler=publish_code_review,
                side_effecting=True,
            ),
        ),
        hooks=(HookSpec(event="page_change", handler=route_review_pages),),
        routes=(
            RouteSpec(
                method="GET",
                path=ROUTE_PATH,
                handler=github_installed,
                identify=install_workspace,
            ),
        ),
    )

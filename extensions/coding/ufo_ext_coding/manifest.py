"""The coding pack: a software-engineering child turn over the core code builtins, the `coding`
skill that routes work to it, and the GitHub credentials its checkouts and API calls ride.

`spawn("coding", {"objective": ...})` runs a child that explores a repo, edits code, runs
 tests, and reports a result. The profile names only tool names — bash/read/write/
 edit/glob/grep to work the code, load_skill to pull a workflow, js_repl to exercise Node code, and
 search_web/fetch_url for external sources — so the pack is self-contained and carries no
 cross-extension import. Nothing in it delivers a file to the member: the child leaves work in the
 workspace it shares with the parent, and the parent decides what reaches the member. Core wraps
 the prompt with the shared citation/formatting discipline and fills its skill index. The `coding`
 skill teaches the main agent to route repo work to that child.

`coding` runs on Opus. `spawn("fable_escalation", ...)` is the last rung on one pull-request
blocker: the same tools and the same input contract on a stronger pinned model, with its own prompt
for a worker that two `coding` children already failed in front of. It reads wider than the diff,
makes one attempt, and reports. Each model is pinned on its profile because the caller chooses the
rung by naming the target.

The pack ships no agent. It is the machinery a durable one runs on — the child profiles, the
 skill, the credentials — and a durable agent with work of its own is an application: it ships as
its own extension, under the slug its page is served at.

The pack declares sandbox internet because repository builds install dependencies and download
their release assets. It also declares the GitHub credential slots, because repository work
includes authenticated Git and API calls. A workspace that installs the App gets short-lived
installation tokens swapped onto those exact wires while only sentinels enter the sandbox."""

import os
from pathlib import Path

from pydantic import BaseModel, Field

from ufo.sdk.credentials import credential_object_name
from ufo.sdk.manifest import (
    CredentialSlot,
    InjectionTarget,
    Manifest,
    RouteSpec,
    SetupCredential,
    SkillSpec,
    SubagentProfile,
    WorkspaceFact,
)
from ufo.sdk.objects import CREDENTIAL_KIND
from ufo.sdk.tools import ActionPresentation, ObjectBinding, ToolDef
from ufo_ext_coding.connect import (
    GIT_INSTALLATION_SLOT,
    GITHUB_APP_INSTALLED_LINE,
    GITHUB_PROVIDER,
    ROUTE_PATH,
    ConnectGitHubInput,
    connect_github,
    github_app_installed,
    github_installed,
    install_workspace,
)
from ufo_ext_coding.github_app import API_SLOT, GIT_SLOT, GitHubAPIAuth, app_tokens

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
"""What the coding profile runs on where no member account can be held: the deploy's own Opus
key."""
CODING_MODEL = "claude-opus-5"
CODING_MODELS = {"anthropic": "claude-opus-5", "openai": "gpt-5.6-sol"}
FABLE_ESCALATION_PROFILE_NAME = "fable_escalation"
FABLE_ESCALATION_MODEL = "anthropic/claude-fable-5"
FABLE_ESCALATION_PROMPT = (
    Path(__file__).parent / "prompts" / "subagent_fable_escalation.md"
).read_text()
GITHUB_CONNECTOR = "github"


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
GITHUB_API_HOST = "api.github.com"
GITHUB_API_SENTINEL = "UFO_SENTINEL_API_GITHUB"
GITHUB_API_AUTH_ENV = "UFO_GITHUB_API_AUTH"
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


GITHUB_APP_TOKENS = None if github_app_id() is None else app_tokens(GIT_INSTALLATION_SLOT)
GITHUB_API_TOKENS = (
    None if GITHUB_APP_TOKENS is None else app_tokens(GIT_INSTALLATION_SLOT, permissions=None)
)
GIT_CREDENTIAL = CredentialSlot(
    name=GIT_SLOT,
    description="A GitHub token with repository contents read and write — a fine-grained personal "
    "access token scoped to the repositories the agent works in. Only needed for a repository "
    "outside an organization that installed the ufo GitHub App; where the App is installed, its "
    "own token is minted per turn instead.",
    source=GITHUB_APP_TOKENS,
    injection=InjectionTarget(
        host=GIT_HOST,
        header="Authorization",
        sentinel=GIT_SENTINEL,
        dimension="requests",
        git_basic_user=GIT_BASIC_USER,
    ),
)
GITHUB_API_CREDENTIAL = CredentialSlot(
    name=API_SLOT,
    description="GitHub API authentication from the installed ufo GitHub App or the stored "
    "fine-grained personal access token fallback.",
    member_filled=False,
    source=GitHubAPIAuth(tokens=GITHUB_API_TOKENS, fallback_slot=GIT_SLOT),
    injection=InjectionTarget(
        host=GITHUB_API_HOST,
        header="Authorization",
        sentinel=GITHUB_API_SENTINEL,
        env=GITHUB_API_AUTH_ENV,
        dimension="requests",
    ),
)

CODING_PROFILE = SubagentProfile(
    name=CODING_PROFILE_NAME,
    prompt=CODING_PROMPT,
    tool_names=CODING_TOOL_NAMES,
    input_model=CodingInput,
    output_model=CodingOutput,
    max_rounds=CODING_ROUND_LIMIT,
    model=CODING_MODEL,
    own_key_models=CODING_MODELS,
    needs_own_model_key=True,
)

FABLE_ESCALATION_PROFILE = SubagentProfile(
    name=FABLE_ESCALATION_PROFILE_NAME,
    prompt=FABLE_ESCALATION_PROMPT,
    tool_names=CODING_TOOL_NAMES,
    input_model=CodingInput,
    output_model=CodingOutput,
    max_rounds=CODING_ROUND_LIMIT,
    model=FABLE_ESCALATION_MODEL,
)

UFO_GITHUB_APP = SetupCredential(
    label="ufo GitHub App",
    slots=(GIT_INSTALLATION_SLOT, GIT_SLOT),
    provider=GITHUB_CONNECTOR,
)
"""The GitHub authority an app declares it cannot work without, named once by the extension that
owns the slots.

Two slots answer it, because a workspace has two ways in: the ufo GitHub App's installation, and a
fine-grained token for a repository in no organization that installed it. An app naming only the
first would report itself unready for a workspace that chose the second, and one naming only the
second would ask an admin who already installed the App to go and mint a token."""


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        sandbox_internet=True,
        subagents=(CODING_PROFILE, FABLE_ESCALATION_PROFILE),
        skills=tuple(SkillSpec(path=SKILLS_ROOT / name) for name in SKILL_NAMES),
        credentials=(GIT_INSTALLATION, GIT_CREDENTIAL, GITHUB_API_CREDENTIAL),
        tools=(
            ToolDef(
                name="connect_github",
                description="Install the workspace's ufo GitHub App for private clone, push, and "
                "GitHub API work: hands an admin the App install link. Admin-only.",
                input_model=ConnectGitHubInput,
                handler=connect_github,
                bound=ObjectBinding(
                    kind=CREDENTIAL_KIND,
                    binding="instance",
                    name=credential_object_name(GIT_INSTALLATION_SLOT),
                ),
                presentation=ActionPresentation(label="Connect GitHub"),
            ),
        ),
        routes=(
            RouteSpec(
                method="GET",
                path=ROUTE_PATH,
                handler=github_installed,
                identify=install_workspace,
            ),
        ),
        workspace_facts=(
            WorkspaceFact(
                name=GITHUB_PROVIDER,
                line=GITHUB_APP_INSTALLED_LINE,
                holds=github_app_installed,
            ),
        ),
    )

"""The coding pack: a software-engineering child turn over the core code builtins, the `coding`
skill the agent loads on demand, and the `code-review` agent that puts both to work.

`spawn("coding", {"objective": ...})` runs a child that explores a repo, edits code, runs
tests, and reports a result. The profile names only tool names — bash/read/write/
edit/glob/grep to work the code, load_skill to pull a workflow, and js_repl to exercise Node
code — so the pack is self-contained and carries no cross-extension import. Nothing in it delivers
a file to the member: the child leaves work in the workspace it shares with the parent, and the
parent decides what reaches the member. Core wraps the prompt with the shared citation/formatting
discipline and fills its skill index. The `coding` skill teaches the main agent to route repo work
to that child.

`spawn("fable_escalation", ...)` is the last rung on one pull-request blocker: the same tools and
the same input contract on a stronger pinned model, with its own prompt for a worker that two
`coding` children already failed in front of. It reads wider than the diff, makes one attempt, and
reports. The model is pinned on the profile because the answer is always "this rung wants that
model", and the caller escalates by naming the target.

`code-review` is the durable agent the pack ships. One conversation tracks one pull request: it
reads the page the source updates, spawns two `coding` children over the same head SHA with
different focuses, coalesces their findings, and publishes one `ufo review` commit status. It runs
on the member-facing tool set, because the page it reads and the GitHub connection it publishes
through both belong to the workspace, not to this pack. It arrives with no grant and no connection
of its own: a member gives it the GitHub account in chat, which is what its publication calls
through.

The pack also declares the GitHub credential slots, because repository work includes Git and API
calls. A workspace that installs the App gets authenticated Git and API access, with short-lived
installation tokens swapped onto each wire and only sentinels inside the sandbox. That is what
opens `github.com` for a child's checkout — public internet stays blocked, so the fetch rides the
slot's own egress rule and never the open wire."""

import os
from pathlib import Path

from pydantic import BaseModel, Field

from ufo.sdk.manifest import (
    AgentProvision,
    AgentSetup,
    AgentSpec,
    CredentialSlot,
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
FABLE_ESCALATION_PROFILE_NAME = "fable_escalation"
FABLE_ESCALATION_MODEL = "claude-fable-5"
FABLE_ESCALATION_PROMPT = (
    Path(__file__).parent / "prompts" / "subagent_fable_escalation.md"
).read_text()
CODE_REVIEW_AGENT_NAME = "code-review"
GITHUB_CONNECTOR = "github"
CODE_REVIEW_PROMPT = (Path(__file__).parent / "prompts" / "agent_code_review.md").read_text()


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

CODE_REVIEW_SETUP = (
    "Connect the GitHub account this workspace reviews under. A connection binds to the agent "
    "whose conversation it is made in, so make it here, with you. Then ask the member to "
    "register the pull-request source for that account and share it — only a shared source "
    "carries a trigger — "
    "and apply a source trigger naming it, so a changed pull request wakes this conversation. The "
    "children also need the ufo GitHub App installed to fetch commits: `connect_github` hands an "
    "admin that link once for the whole workspace."
)

CODE_REVIEW_AGENT = AgentProvision(
    name=CODE_REVIEW_AGENT_NAME,
    spec=AgentSpec(
        prompt=CODE_REVIEW_PROMPT,
        model="auto",
        reasoning="high",
        internet_access_allowed=False,
        sandbox_size="large",
    ),
    setup=AgentSetup(connectors=(GITHUB_CONNECTOR,), instructions=CODE_REVIEW_SETUP),
)


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        subagents=(CODING_PROFILE, FABLE_ESCALATION_PROFILE),
        agents=(CODE_REVIEW_AGENT,),
        skills=tuple(SkillSpec(path=SKILLS_ROOT / name) for name in SKILL_NAMES),
        credentials=(GIT_INSTALLATION, GIT_CREDENTIAL, GITHUB_API_CREDENTIAL),
        tools=(
            ToolDef(
                name="connect_github",
                description="Install the workspace's ufo GitHub App for private clone, push, and "
                "GitHub API work: hands an admin the App install link. Admin-only.",
                input_model=ConnectGitHubInput,
                handler=connect_github,
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
    )

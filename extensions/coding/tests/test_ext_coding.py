import json

import pytest
import ufo_ext_coding.connect as connect
import ufo_ext_coding.manifest as coding

from ufo.ext.loader import skill_registry
from ufo.loop.subagents import FINISH_CONTRACT, subagent_system_prompt
from ufo.sandbox.exec_env import CONVERSATION_ID_ENV
from ufo.tools.builtins import BUILTIN_TOOLS

TOOL_NARRATION = "connecting their GitHub"


def test_empty_github_app_registration_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        coding.GIT_APP_ID_ENV,
        coding.GIT_APP_CLIENT_ID_ENV,
        coding.GIT_APP_SECRET_ENV,
        coding.GIT_APP_KEY_ENV,
    ):
        monkeypatch.setenv(name, "")
    assert coding.github_app_id() is None


@pytest.mark.parametrize(
    "missing",
    (
        coding.GIT_APP_ID_ENV,
        coding.GIT_APP_CLIENT_ID_ENV,
        coding.GIT_APP_SECRET_ENV,
        coding.GIT_APP_KEY_ENV,
    ),
)
def test_partial_github_app_registration_fails(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    for name in (
        coding.GIT_APP_ID_ENV,
        coding.GIT_APP_CLIENT_ID_ENV,
        coding.GIT_APP_SECRET_ENV,
        coding.GIT_APP_KEY_ENV,
    ):
        monkeypatch.setenv(name, "" if name == missing else "value")
    with pytest.raises(RuntimeError, match=missing):
        coding.github_app_id()


def test_coding_manifest_registers_the_coding_profile() -> None:
    manifest = coding.manifest()
    assert [tool.name for tool in manifest.tools] == ["connect_github"]
    profile = manifest.subagents[0]
    assert [subagent.name for subagent in manifest.subagents] == ["coding"]
    assert profile.name == "coding"
    assert (
        profile.input_model.model_validate(
            {"user_description": TOOL_NARRATION, "objective": "fix it"}
        ).objective
        == "fix it"
    )
    assert profile.output_model.model_validate({"result": "fixed"}).result == "fixed"
    assert manifest.hooks == ()


def test_coding_result_uses_only_the_shared_register_bound() -> None:
    result = "x" * 10_000
    assert coding.CodingOutput.model_validate({"result": result}).result == result

    input_schema = coding.CodingInput.model_json_schema()["properties"]["objective"]
    schema = coding.CodingOutput.model_json_schema()["properties"]["result"]
    assert "maxLength" not in input_schema
    assert "maxLength" not in schema
    assert "shared delivery register" in schema["description"].casefold()


def test_coding_prompt_uses_the_shared_delivery_contract() -> None:
    assert "Do not narrate routine tool calls" in coding.CODING_PROMPT
    assert "memory for a later model round in this turn" in coding.CODING_PROMPT
    prompt = subagent_system_prompt(coding.CODING_PROFILE)
    assert "A delivery crosses an agent boundary" in prompt
    assert prompt.endswith(FINISH_CONTRACT)
    assert "Only the `finish` payload is returned through the spawn" not in coding.CODING_PROMPT
    assert "# Returning to the parent" not in coding.CODING_PROMPT


def test_coding_prompt_writes_member_prose_in_simplified_technical_english() -> None:
    assert "ASD-STE100 Simplified Technical English" in coding.CODING_PROMPT
    assert "a plan, a finish result, a PR or issue body, a review finding" in coding.CODING_PROMPT
    assert "one instruction per sentence, active voice, present tense" in coding.CODING_PROMPT
    assert (
        "Code, identifiers, paths, commands, and quoted diff lines are quotations"
        in coding.CODING_PROMPT
    )


def test_coding_tools_are_core_builtins_plus_the_repl_and_exclude_the_forbidden_ones() -> None:
    profile = coding.CODING_PROFILE
    builtin_names = {tool.name for tool in BUILTIN_TOOLS}
    for name in ("bash", "read", "write", "edit", "glob", "grep"):
        assert name in profile.tool_names and name in builtin_names
    assert "js_repl" in profile.tool_names
    assert {"ask_user", "spawn", "cancel_spawn"}.isdisjoint(profile.tool_names)


def test_coding_profile_leaves_member_delivery_to_the_parent() -> None:
    profile = coding.CODING_PROFILE
    instructions = skill_registry((coding.manifest(),)).named("coding").instructions
    assert "share_file" not in profile.tool_names
    assert "A coding subagent has no `share_file`" in instructions
    assert "under the shared delivery register" in instructions
    assert "mention function names, file paths" not in instructions
    assert "## Post-Completion" not in instructions
    assert "The parent reads it there and decides what reaches the member" in coding.CODING_PROMPT


def test_the_child_prompt_names_the_shared_workspace_and_checks_before_cloning() -> None:
    assert "sandbox workspace shared with the parent agent" in coding.CODING_PROMPT
    assert "you share the workspace with the parent and sibling subagents" in coding.CODING_PROMPT
    assert "Look at the path first" in coding.CODING_PROMPT
    assert "if not, clone once" in coding.CODING_PROMPT
    assert "use the existing checkout at <path>, from <url>. Do not clone." in coding.CODING_PROMPT


def test_the_setup_contract_fetches_the_repository_once_per_turn() -> None:
    instructions = skill_registry((coding.manifest(),)).named("coding").instructions
    assert "One remote clone per turn" in instructions
    assert "the first remote clone is the only fetch" in instructions
    assert "You do not run the clone — the first child does" in instructions
    assert "The cloning child finishes before another child touches that path" in instructions
    assert "**Existing checkout:** Use for any later spawn after the child using that path" in (
        instructions
    )
    assert (
        "use the existing checkout at /workspace/org-repo, from https://github.com/org/repo. "
        "Do not clone."
    ) in instructions


def test_the_setup_contract_isolates_parallel_writers() -> None:
    instructions = skill_registry((coding.manifest(),)).named("coding").instructions
    assert "Never point two live children at one checkout" in instructions
    assert "the cloning child only creates and verifies the canonical checkout" in instructions
    setup = (
        "verify the checkout, report the checked-out branch as the base, then finish without task "
        "work"
    )
    assert setup in instructions
    assert "start every worker in local checkout mode at a distinct path" in instructions
    assert "**Local checkout:** Use for every spawn that overlaps another child" in instructions
    assert (
        "copy the committed tree at /workspace/org-repo to /workspace/org-repo-<slug> with git, "
        "base the work on <base>, keep the source as workspace, use "
        "https://github.com/org/repo as origin" in instructions
    )
    assert "Use the branch the setup child reports as `<base>`" in instructions
    assert "base the work on <base>" in coding.CODING_PROMPT


def test_both_ends_of_the_setup_only_clone_stop_after_the_checkout() -> None:
    instructions = skill_registry((coding.manifest(),)).named("coding").instructions
    setup = (
        "verify the checkout, report the checked-out branch as the base, then finish without task "
        "work"
    )
    assert setup in instructions
    assert setup in coding.CODING_PROMPT
    assert "report `git branch --show-current` as the base" in coding.CODING_PROMPT
    assert "finish without doing the task" in coding.CODING_PROMPT


def test_both_ends_of_the_local_checkout_mode_restore_the_github_origin() -> None:
    instructions = skill_registry((coding.manifest(),)).named("coding").instructions
    assert "use https://github.com/org/repo as origin" in instructions
    assert "keep the source as workspace, use <url> as origin" in coding.CODING_PROMPT
    assert "base the work on <base>" in coding.CODING_PROMPT
    assert "git clone --origin workspace <source> <path>" in coding.CODING_PROMPT
    assert "git remote add origin <url>" in coding.CODING_PROMPT
    assert "git config remote.pushDefault origin" in coding.CODING_PROMPT
    assert "git checkout -B <your branch> workspace/<base>" in coding.CODING_PROMPT
    assert "git symbolic-ref --short refs/remotes/origin/HEAD" not in coding.CODING_PROMPT
    assert "Never relabel the source's branches as `origin/*`" in coding.CODING_PROMPT
    assert "git fetch --prune workspace" not in coding.CODING_PROMPT
    assert "git fetch origin" not in coding.CODING_PROMPT
    assert "--branch <default-branch>" not in coding.CODING_PROMPT


def test_the_local_checkout_mode_handles_missing_and_dirty_sources() -> None:
    assert "If the source is missing, run `git clone --branch <base> <url> <path>`" in (
        coding.CODING_PROMPT
    )
    assert "git status --porcelain=v1 --untracked-files=all` is not empty" in coding.CODING_PROMPT
    assert "Uncommitted and untracked changes are not copied" in coding.CODING_PROMPT
    assert "`workspace/*` names the source's committed local branches" in coding.CODING_PROMPT
    assert "`origin/*` does not exist until GitHub supplies it" in coding.CODING_PROMPT


def test_coding_profile_excludes_connector_tools() -> None:
    profile = coding.CODING_PROFILE
    assert {"call_external_tool", "describe_external_tools"}.isdisjoint(profile.tool_names)


def test_coding_profile_raises_the_round_budget() -> None:
    assert coding.CODING_PROFILE.max_rounds == 100


def test_both_prompt_ends_carry_the_source_through_the_objective() -> None:
    instructions = skill_registry((coding.manifest(),)).named("coding").instructions
    assert "requesting message's `<context>` carries a `source`" in instructions
    assert "put it in the objective" in instructions
    assert "the objective names where the member asked" in coding.CODING_PROMPT
    assert "Requested in: <source>" in coding.CODING_PROMPT
    assert "input carries" not in coding.CODING_PROMPT


def test_the_prompt_spends_the_conversation_identity_the_engine_actually_exports() -> None:
    """The subagent has no connector tool, so it opens a PR through `gh` in bash — nothing on the
    engine side stamps the conversation onto the branch or the PR body. The prompt is that
    guarantee, and it is worth only as much as the variable it names: pinned against the engine's
    own export so a rename on either side fails here rather than silently producing `ufo/-slug`
    branches."""
    assert CONVERSATION_ID_ENV == "UFO_CONVERSATION_ID"
    assert f"${CONVERSATION_ID_ENV}" in coding.CODING_PROMPT
    assert "ufo/<first 8 characters of $UFO_CONVERSATION_ID>-<short-slug>" in coding.CODING_PROMPT
    assert "`Ufo-Conversation-Id: <the full value>` trailer" in coding.CODING_PROMPT


def test_extended_context_survives_the_spawn_payload_serialization() -> None:
    spawned = coding.CodingInput.model_validate({"objective": "x", "extended_context": True})
    assert json.loads(spawned.model_dump_json())["extended_context"] is True


def test_coding_skills_parse_and_index() -> None:
    manifest = coding.manifest()
    registry = skill_registry((manifest,))
    index = dict(registry.index())
    assert tuple(skill.path.name for skill in manifest.skills) == ("coding",)
    assert "coding" in index
    instructions = registry.named("coding").instructions
    assert "only a workspace admin can do it" in instructions
    assert "only the owner" not in instructions


def test_private_git_access_does_not_infer_from_the_connector() -> None:
    instructions = skill_registry((coding.manifest(),)).named("coding").instructions

    assert (
        "A working connector is never a reason to skip `connect_github` when private git access "
        "is missing."
    ) in instructions
    assert "Composio" not in instructions


def test_coding_prompt_wraps_with_citation_and_fills_the_skill_index() -> None:
    prompt = subagent_system_prompt(
        coding.CODING_PROFILE, skills=(("extension-skill", "A turn-specific coding workflow."),)
    )
    assert "{{skill_index}}" not in prompt
    assert "<available_skills>" in prompt
    assert "- extension-skill: A turn-specific coding workflow." in prompt
    assert "<citation_instructions>" in prompt
    assert "software-engineering task" in prompt
    assert "keep it a short summary, put bulk output in a workspace file" not in prompt
    assert prompt.endswith(FINISH_CONTRACT)
    assert "list_skills" not in coding.CODING_PROFILE.tool_names


def test_coding_manifest_declares_the_github_credentials_the_proxy_swaps() -> None:
    """The Git and API wires get the same credential in their required authentication shapes."""
    installation, slot, api = coding.manifest().credentials
    assert installation.name == "github_app_installation"
    assert installation.injection is None
    assert installation.member_filled is False
    assert slot.member_filled is True
    assert slot.name == "github_git_token"
    assert slot.injection is not None
    assert (slot.injection.host, slot.injection.header) == ("github.com", "Authorization")
    assert slot.injection.git_basic_user == "x-access-token"
    assert slot.injection.env is None
    assert api.name == "github_api_auth"
    assert api.member_filled is False
    assert api.injection is not None
    assert (api.injection.host, api.injection.header) == ("api.github.com", "Authorization")
    assert api.injection.env == "UFO_GITHUB_API_AUTH"
    assert api.injection.git_basic_user is None
    assert isinstance(api.source, coding.GitHubAPIAuth)
    assert api.source.tokens is coding.GITHUB_API_TOKENS


def test_coding_prompt_and_skill_consume_the_github_api_credential() -> None:
    command = 'GH_TOKEN="$UFO_GITHUB_API_AUTH"'
    skill = (coding.SKILLS_ROOT / "coding" / "SKILL.md").read_text()

    assert command in coding.CODING_PROMPT
    assert command in skill


def test_the_connect_tool_and_its_return_leg_ship_together() -> None:
    """The member acts between them: a tool that mints an install link with no route to return to
    would strand every connection, and a route with no tool could never be reached with a seal."""
    manifest = coding.manifest()
    assert "connect_github" in {tool.name for tool in manifest.tools}
    (route,) = manifest.routes
    assert (route.method, route.path) == ("GET", connect.ROUTE_PATH)
    assert route.identify is connect.install_workspace


def test_the_install_url_names_the_published_app() -> None:
    assert connect.INSTALL_URL == "https://github.com/apps/ufo-ai/installations/new"


def test_the_review_agent_spawns_the_profile_this_pack_registers() -> None:
    """The shipped agent and the child it drives ship together. A prompt naming a profile the pack
    does not register is a review agent that can never spawn anything."""
    manifest = coding.manifest()
    (agent,) = manifest.agents
    (profile,) = manifest.subagents
    assert agent.name == coding.CODE_REVIEW_AGENT_NAME
    assert f"`{profile.name}` subagents" in agent.spec.prompt


def test_the_review_agent_runs_the_member_facing_tool_set() -> None:
    """It reads the pull-request page and publishes through the workspace's GitHub connection, and
    this pack declares neither. An allowlist here would leave it holding only its own tools."""
    (agent,) = coding.manifest().agents
    assert agent.tools is None


def test_the_review_agent_is_provisioned_for_two_parallel_children() -> None:
    """Two `coding` children run at once in the one sandbox the conversation holds, each with its
    own checkout of the same repository."""
    (agent,) = coding.manifest().agents
    assert agent.spec.sandbox_size == "large"
    assert agent.spec.reasoning == "high"
    assert "spawn exactly two `coding` subagents" in agent.spec.prompt
    assert "Do not spawn preparation, synthesis, or adjudication subagents" in agent.spec.prompt
    assert "Resolve disagreements yourself" in agent.spec.prompt
    assert "Spawn exactly two `coding` subagents for each head SHA" in agent.spec.prompt
    assert "spawn one replacement for that focus and that same head" in agent.spec.prompt
    assert "Replace a focus at most once per head" in agent.spec.prompt


def test_the_review_agent_never_cancels_and_keeps_each_head_separate() -> None:
    """Reviews are additive: a running pass finishes and publishes against the commit it was
    computed on. Nothing is cancelled, so the agent depends on no cancel verb — and findings never
    cross head SHAs, because a defect is a claim about one commit and its line may not exist on the
    next."""
    (agent,) = coding.manifest().agents
    assert "Reviews are additive. Never cancel a subagent." in agent.spec.prompt
    assert "Never coalesce results across head SHAs" in agent.spec.prompt
    assert "cancel_subagent" not in agent.spec.prompt
    assert "cancel_spawn" not in agent.spec.prompt


def test_the_review_agent_stops_unchanged_head_revisions_without_more_tools() -> None:
    (agent,) = coding.manifest().agents
    assert "A new head SHA is the only source change that starts review work." in agent.spec.prompt
    assert "stop in the next response without another tool call" in agent.spec.prompt
    assert "report that no action was needed" in agent.spec.prompt


def test_the_review_agent_bounds_each_child_evidence_pass() -> None:
    (agent,) = coding.manifest().agents
    assert "Make one bounded evidence pass." in agent.spec.prompt
    assert "Issue independent reads and searches together." in agent.spec.prompt
    assert "Do not repeat a command when its output was complete." in agent.spec.prompt
    assert "return the JSON result immediately" in agent.spec.prompt


def test_the_review_agent_reaches_github_through_the_slot_this_pack_declares() -> None:
    """Public internet stays blocked, so a child's `git fetch` reaches github.com only through the
    git slot's own injection rule — which is why the pack that ships the agent is the pack that
    declares that slot. Opening the public wire instead would give the agent every host."""
    manifest = coding.manifest()
    (agent,) = manifest.agents
    assert agent.spec.internet_access_allowed is False
    hosts = {slot.injection.host for slot in manifest.credentials if slot.injection is not None}
    assert coding.GIT_HOST in hosts


def test_the_review_agent_publishes_the_status_the_repository_requires() -> None:
    """`ufo review` is the required check on the pull request, and the head SHA is what it attaches
    to. A prompt that names either differently publishes a status nothing is waiting for. Findings
    ride a pull-request review, so each one lands on the changed line it describes rather than in a
    status description that holds one sentence."""
    (agent,) = coding.manifest().agents
    assert "`ufo review`" in agent.spec.prompt
    assert "Never create a GitHub Check Run for it" in agent.spec.prompt
    assert "never `ufo review`" in agent.spec.prompt
    assert "/repos/{owner}/{repo}/statuses/{head_sha}" in agent.spec.prompt
    assert "/repos/{owner}/{repo}/pulls/{pull_number}/reviews" in agent.spec.prompt


def test_the_review_agent_declares_the_account_it_cannot_arrive_with() -> None:
    """A shipped agent arrives with no grant, so the provision names the one kind of authority a
    member must give it: a GitHub account, which its publication calls through. The instructions
    also name the two acts no declaration can carry — sharing the pull-request source, which is
    what lets a trigger wake it, and installing the App its children fetch commits with."""
    (agent,) = coding.manifest().agents
    assert agent.setup.connectors == (coding.GITHUB_CONNECTOR,)
    assert "share it" in agent.setup.instructions
    assert "source trigger" in agent.setup.instructions
    assert "connect_github" in agent.setup.instructions


def test_the_review_agent_holds_the_verbs_its_own_setup_names() -> None:
    """The setup asks the agent to connect an account and apply objects in its own conversation. It
    runs the member-facing set, so it holds those verbs — an allowlist here would leave it reading
    instructions it could not follow, which `AgentProvision` refuses outright."""
    (agent,) = coding.manifest().agents
    assert agent.tools is None

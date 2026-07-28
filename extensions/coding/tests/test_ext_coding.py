"""The coding pack's proof: its manifest registers a single `coding` SubagentProfile over the core
code builtins, and core's shell-wrap renders it with the shared citation discipline and a filled
skill index. The profile names only tool names, so the pack is self-contained — nothing here
imports a core internal."""

import json

import ufo_ext_coding.connect as connect
import ufo_ext_coding.manifest as coding

from ufo.ext.loader import skill_registry
from ufo.loop.subagents import FINISH_CONTRACT, subagent_system_prompt
from ufo.tools.builtins import BUILTIN_TOOLS

TOOL_NARRATION = "connecting their GitHub"


def test_coding_manifest_registers_a_single_coding_profile() -> None:
    manifest = coding.manifest()
    assert [tool.name for tool in manifest.tools] == ["connect_github"]
    (profile,) = manifest.subagents
    assert profile.name == "coding"
    assert (
        profile.input_model.model_validate(
            {"user_description": TOOL_NARRATION, "objective": "fix it"}
        ).objective
        == "fix it"
    )
    assert profile.output_model.model_validate({"result": "fixed"}).result == "fixed"


def test_coding_tools_are_core_builtins_plus_the_repl_and_exclude_the_forbidden_ones() -> None:
    profile = coding.CODING_PROFILE
    builtin_names = {tool.name for tool in BUILTIN_TOOLS}
    for name in ("bash", "read", "write", "edit", "glob", "grep"):
        assert name in profile.tool_names and name in builtin_names
    assert "js_repl" in profile.tool_names
    assert {"ask_user", "spawn_subagent", "wait_for_subagents", "cancel_subagent"}.isdisjoint(
        profile.tool_names
    )


def test_coding_profile_excludes_the_connector_tools_pr_review_uses() -> None:
    """`code-review` reaches GitHub through the connector trio (`call_external_tool` +
    `describe_external_tools`); by design the main agent runs that skill directly, never the coding
    subagent — whose profile therefore names none of them, so it cannot review a PR itself."""
    profile = coding.CODING_PROFILE
    assert {"call_external_tool", "describe_external_tools"}.isdisjoint(profile.tool_names)


def test_coding_profile_raises_the_round_budget() -> None:
    assert coding.CODING_PROFILE.max_rounds == 100


def test_extended_context_survives_the_spawn_payload_serialization() -> None:
    spawned = coding.CodingInput.model_validate({"objective": "x", "extended_context": True})
    assert json.loads(spawned.model_dump_json())["extended_context"] is True


def test_coding_skills_parse_and_index() -> None:
    registry = skill_registry((coding.manifest(),))
    index = dict(registry.index())
    for name in ("coding", "code-review"):
        assert name in index
    instructions = registry.named("coding").instructions
    assert "only a workspace admin can do it" in instructions
    assert "only the owner" not in instructions


def test_coding_prompt_wraps_with_citation_and_fills_the_skill_index() -> None:
    prompt = subagent_system_prompt(
        coding.CODING_PROFILE, skills=(("extension-skill", "A turn-specific coding workflow."),)
    )
    assert "{{skill_index}}" not in prompt
    assert "<available_skills>" in prompt
    assert "- extension-skill: A turn-specific coding workflow." in prompt
    assert "<citation_instructions>" in prompt
    assert "software-engineering task" in prompt
    assert prompt.endswith(FINISH_CONTRACT)
    assert "list_skills" not in coding.CODING_PROFILE.tool_names


def test_coding_manifest_declares_the_git_credential_the_proxy_swaps() -> None:
    """The pack that routes repo work declares the credential a checkout needs: one slot, injected
    on `github.com` as the Basic password half, so the sandbox holds a sentinel and the proxy holds
    the swap. A bearer here would be refused by git's smart-HTTP even for a public repository."""
    installation, slot = coding.manifest().credentials
    assert installation.name == "github_app_installation"
    assert installation.injection is None
    assert installation.member_filled is False
    assert slot.member_filled is True
    assert slot.name == "github_git_token"
    assert slot.injection is not None
    assert (slot.injection.host, slot.injection.header) == ("github.com", "Authorization")
    assert slot.injection.git_basic_user == "x-access-token"
    assert slot.injection.env is None


def test_the_connect_tool_and_its_return_leg_ship_together() -> None:
    """The member acts between them: a tool that mints an install link with no route to return to
    would strand every connection, and a route with no tool could never be reached with a seal."""
    manifest = coding.manifest()
    assert [tool.name for tool in manifest.tools] == ["connect_github"]
    (route,) = manifest.routes
    assert (route.method, route.path) == ("GET", connect.ROUTE_PATH)
    assert route.identify is connect.install_workspace


def test_the_install_url_names_the_published_app() -> None:
    assert connect.INSTALL_URL == ("https://github.com/apps/flyingobject-ai-ufo/installations/new")

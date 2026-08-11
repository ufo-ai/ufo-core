"""The render engine: slot filling, strict both-ends var validation, the skill index, the digest.

These exercise the pure text engine directly — no turn, no model — so the faithful metalcraft
behaviors are pinned: only a template with the `{{agent-prompt}}` slot accepts an agent prompt, a
template without `{{skill_index}}` renders no skill block, every var is validated both ways, and a
slot nothing fills fails loud rather than reaching the model as a literal brace."""

import re

import pytest

from ufo.ext.loader import load_manifests
from ufo.loop.prompts.render import (
    COMPACTION_SYSTEM_PROMPT,
    DELIVERY_REGISTER_BLOCK,
    SHELL,
    render_skill_index,
    render_system_prompt,
    render_template,
    rendered_prompt,
)
from ufo.models.catalog import CORE_MODEL_SPECS

SHELL_FIXTURE = "You are {{agent-prompt}}.\n\n{{skill_index}}\n\n{{sections}}"
BROWSER_FIXTURE = "You browse the web. Cite what you find.\n\n{{sections}}"


def test_system_prompt_slots_the_agent_prompt_sections_and_citation() -> None:
    rendered = render_system_prompt(
        "You are the workspace assistant.",
        (("search", "<search>Search the web before answering factual questions.</search>"),),
        knowledge_cutoff="2026-01",
    )
    assert "You are the workspace assistant." in rendered.content
    assert "<search>Search the web before answering factual questions.</search>" in rendered.content
    assert "<citation_instructions>" in rendered.content
    assert "{{" not in rendered.content


def test_shell_requires_message_authority_for_admin_actions() -> None:
    assert "including admin actions" in SHELL
    assert "Omit it only for conversation-common work." in SHELL


def test_shell_refuses_to_manufacture_cross_member_approval() -> None:
    assert "require an authorized member to request it in their own conversation" in SHELL
    assert "tell them to ask you directly instead of asking them to approve" in SHELL
    assert "Never promise that a reply elsewhere will apply the action" in SHELL
    assert "or that you will report its outcome back here" in SHELL


def test_shell_asks_for_the_source_without_naming_a_surface() -> None:
    assert "may also carry a `source`" in SHELL
    assert "Requested in: <source>" in SHELL
    for surface in ("Slack", "slack", "ufo cli", "ufo web", "permalink"):
        assert surface not in SHELL


def test_shell_splits_long_delivery_between_chat_and_one_markdown_report() -> None:
    assert SHELL.count(DELIVERY_REGISTER_BLOCK) == 1
    prose = " ".join(SHELL.split())
    assert "A delivery crosses an agent boundary" in prose
    assert "task, when assigning work to another agent" in prose
    assert "complete evidence, uncertainty, and reasoning" in prose
    assert "even when they fit inline" in prose
    assert "For a member, inline includes only the answer" in prose
    assert "one deciding product fact" in prose
    assert "Do not invent an action or result" in prose
    assert "what remains unknown" in prose
    assert "distinguish the rule from the instance" in prose
    assert "put the full report in one Markdown artifact" in prose
    assert "never duplicate its body inline" in prose
    assert "Send it a task-register objective" in prose
    assert "Keep a spawn foreground when your next step needs its result" in prose
    assert "A subagent's return is a short text summary" not in SHELL
    assert "put every piece of it in\nthis one message, in full" not in SHELL
    assert "report, when you deliver" in SHELL
    assert "full structure, because the content is genuinely report-shaped" not in SHELL


def test_shell_answers_first_and_grounds_an_explanation_in_current_content() -> None:
    prose = " ".join(SHELL.split())
    assert "In answer, discuss, and report, the first sentence answers the question" in prose
    assert "reads its current content first" in prose
    assert "a claim to check against that content, never a fact to repeat" in prose
    assert "Inline to a member, say which action produces which result" in prose
    assert "never the internal names the thing uses for its own parts" in prose


def test_digest_is_stable_for_equal_content_and_shifts_with_it() -> None:
    first = render_system_prompt("A", (), knowledge_cutoff="2026-01")
    again = render_system_prompt("A", (), knowledge_cutoff="2026-01")
    other = render_system_prompt("B", (), knowledge_cutoff="2026-01")
    other_model = render_system_prompt("A", (), knowledge_cutoff="2025-07")
    assert first.digest == again.digest
    assert first.digest != other.digest
    assert first.digest != other_model.digest
    assert first.digest.startswith("sha256:")


def test_sections_render_in_name_order() -> None:
    rendered = render_system_prompt(
        "A", (("zeta", "BODY_ZETA"), ("alpha", "BODY_ALPHA")), knowledge_cutoff="2026-01"
    )
    assert rendered.content.index("BODY_ALPHA") < rendered.content.index("BODY_ZETA")


def test_knowledge_cutoff_renders_the_models_boundary() -> None:
    rendered = render_system_prompt("A", (), knowledge_cutoff="2026-01")
    assert "<knowledge_cutoff>" in rendered.content
    assert "January 2026" in rendered.content
    assert "{{" not in rendered.content


def test_gpt_5_6_terra_renders_its_knowledge_cutoff() -> None:
    rendered = render_system_prompt("A", (), knowledge_cutoff="2026-02")
    assert "February 2026" in rendered.content


def test_a_malformed_cutoff_fails_loud() -> None:
    with pytest.raises(ValueError):
        render_system_prompt("A", (), knowledge_cutoff="unknown")


def test_every_registered_model_declares_a_knowledge_cutoff() -> None:
    specs = [*CORE_MODEL_SPECS, *(spec for m in load_manifests() for spec in m.models)]
    assert specs
    for spec in specs:
        assert re.match(r"^\d{4}-\d{2}$", spec.knowledge_cutoff), (
            f"model {spec.id!r} has a non-YYYY-MM knowledge cutoff {spec.knowledge_cutoff!r}"
        )


def test_a_var_substitutes_in_the_agent_prompt() -> None:
    rendered = render_template(
        SHELL_FIXTURE, "the assistant for {{team}}", {"team": "Acme"}, (), ()
    )
    assert "the assistant for Acme" in rendered.content


def test_a_missing_var_fails_loud() -> None:
    with pytest.raises(ValueError, match="missing: team"):
        render_template(SHELL_FIXTURE, "for {{team}}", {}, (), ())


def test_an_undeclared_var_fails_loud() -> None:
    with pytest.raises(ValueError, match="undeclared: extra"):
        render_template(SHELL_FIXTURE, "static prompt", {"extra": "x"}, (), ())


def test_only_a_template_with_the_slot_accepts_an_agent_prompt() -> None:
    with pytest.raises(ValueError, match="agent-prompt"):
        render_template(BROWSER_FIXTURE, "an agent prompt", {}, (), ())
    empty = render_template(BROWSER_FIXTURE, "", {}, (), ())
    assert "You browse the web" in empty.content


def test_a_template_without_a_skill_index_slot_renders_no_skill_block() -> None:
    rendered = render_template(BROWSER_FIXTURE, "", {}, (("web", "browse the web"),), ())
    assert "<available_skills>" not in rendered.content


def test_skill_index_renders_a_block_and_is_empty_without_skills() -> None:
    assert render_skill_index(()) == ""
    block = render_skill_index((("sandbox", "run commands"), ("memory", "store facts")))
    assert block.startswith("<available_skills>")
    assert "- sandbox: run commands" in block
    assert "- memory: store facts" in block


def test_main_prompt_renders_the_complete_per_turn_skill_index() -> None:
    skills = (("first", "First workflow."), ("second", "Second workflow."))
    prompt = render_system_prompt("A", (), skills=skills, knowledge_cutoff="2026-01").content
    assert "<available_skills>" in prompt
    assert "</available_skills>" in prompt
    assert all(f"- {name}: {description}" in prompt for name, description in skills)


def test_an_unresolved_slot_in_a_section_fails_loud() -> None:
    with pytest.raises(ValueError, match="unresolved slots: leftover"):
        render_system_prompt("A", (("bad", "<bad>{{leftover}}</bad>"),), knowledge_cutoff="2026-01")


def test_rendered_prompt_wraps_a_raw_string_with_a_digest() -> None:
    wrapped = rendered_prompt("a subagent's own prompt")
    assert wrapped.content == "a subagent's own prompt"
    assert wrapped.digest.startswith("sha256:")


def test_compaction_prompt_is_structured_and_loaded() -> None:
    assert "SINGLE JSON object" in COMPACTION_SYSTEM_PROMPT
    assert "`intent`" in COMPACTION_SYSTEM_PROMPT
    assert "`next_step`" in COMPACTION_SYSTEM_PROMPT

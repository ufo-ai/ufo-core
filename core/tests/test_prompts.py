"""The render engine: slot filling, strict both-ends var validation, the skill index, the digest.

These exercise the pure text engine directly — no turn, no model — so the faithful metalcraft
behaviors are pinned: only a template with the `{{agent-prompt}}` slot accepts an agent prompt, a
template without `{{skill_index}}` renders no skill block, every var is validated both ways, and a
slot nothing fills fails loud rather than reaching the model as a literal brace."""

import pytest

from ufo.ext.loader import load_manifests
from ufo.loop.prompts.render import (
    COMPACTION_SYSTEM_PROMPT,
    MODEL_KNOWLEDGE_CUTOFF,
    render_skill_index,
    render_system_prompt,
    render_template,
    rendered_prompt,
)

SHELL_FIXTURE = "You are {{agent-prompt}}.\n\n{{skill_index}}\n\n{{sections}}"
BROWSER_FIXTURE = "You browse the web. Cite what you find.\n\n{{sections}}"


def test_system_prompt_slots_the_agent_prompt_sections_and_citation() -> None:
    rendered = render_system_prompt(
        "You are the workspace assistant.",
        (("search", "<search>Search the web before answering factual questions.</search>"),),
        model="claude-opus-4-8",
    )
    assert "You are the workspace assistant." in rendered.content
    assert "<search>Search the web before answering factual questions.</search>" in rendered.content
    assert "<citation_instructions>" in rendered.content
    assert "{{" not in rendered.content


def test_digest_is_stable_for_equal_content_and_shifts_with_it() -> None:
    first = render_system_prompt("A", (), model="claude-opus-4-8")
    again = render_system_prompt("A", (), model="claude-opus-4-8")
    other = render_system_prompt("B", (), model="claude-opus-4-8")
    other_model = render_system_prompt("A", (), model="claude-haiku-4-5")
    assert first.digest == again.digest
    assert first.digest != other.digest
    assert first.digest != other_model.digest
    assert first.digest.startswith("sha256:")


def test_sections_render_in_name_order() -> None:
    rendered = render_system_prompt(
        "A", (("zeta", "BODY_ZETA"), ("alpha", "BODY_ALPHA")), model="claude-opus-4-8"
    )
    assert rendered.content.index("BODY_ALPHA") < rendered.content.index("BODY_ZETA")


def test_knowledge_cutoff_renders_the_models_boundary() -> None:
    rendered = render_system_prompt("A", (), model="claude-opus-4-8")
    assert "<knowledge_cutoff>" in rendered.content
    assert "January 2026" in rendered.content
    assert "{{" not in rendered.content


def test_a_model_without_a_declared_cutoff_fails_loud() -> None:
    with pytest.raises(ValueError, match="no knowledge cutoff declared for model 'gpt-5'"):
        render_system_prompt("A", (), model="gpt-5")


def test_every_extension_priced_model_declares_a_knowledge_cutoff() -> None:
    for manifest in load_manifests():
        if manifest.name == "sample":
            continue
        for spec in manifest.models:
            for model, _ in spec.prices:
                assert model in MODEL_KNOWLEDGE_CUTOFF, (
                    f"extension {manifest.name!r} prices model {model!r} without a knowledge "
                    f"cutoff — an agent set to it fails every turn at prompt render"
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
    prompt = render_system_prompt("A", (), skills=skills, model="claude-opus-4-8").content
    assert "<available_skills>" in prompt
    assert "</available_skills>" in prompt
    assert all(f"- {name}: {description}" in prompt for name, description in skills)


def test_an_unresolved_slot_in_a_section_fails_loud() -> None:
    with pytest.raises(ValueError, match="unresolved slots: leftover"):
        render_system_prompt("A", (("bad", "<bad>{{leftover}}</bad>"),), model="claude-opus-4-8")


def test_rendered_prompt_wraps_a_raw_string_with_a_digest() -> None:
    wrapped = rendered_prompt("a subagent's own prompt")
    assert wrapped.content == "a subagent's own prompt"
    assert wrapped.digest.startswith("sha256:")


def test_compaction_prompt_is_structured_and_loaded() -> None:
    assert "SINGLE JSON object" in COMPACTION_SYSTEM_PROMPT
    assert "`intent`" in COMPACTION_SYSTEM_PROMPT
    assert "`next_step`" in COMPACTION_SYSTEM_PROMPT

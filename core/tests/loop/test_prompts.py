"""The render engine: slot filling, strict both-ends var validation, the skill index, the digest.

These exercise the pure text engine directly — no turn, no model — so the faithful metalcraft
behaviors are pinned: only a template with the `{{agent-prompt}}` slot accepts an agent prompt, a
template without `{{skill_index}}` renders no skill block, every var is validated both ways, and a
slot nothing fills fails loud rather than reaching the model as a literal brace."""

import re

import pytest

from ufo.harness.models.catalog import CORE_MODEL_SPECS
from ufo.host.ext.loader import load_manifests
from ufo.runtime.prompts.render import (
    COMPACTION_SYSTEM_PROMPT,
    SHELL,
    WORKSPACE_FACTS_CLOSER,
    RenderedPrompt,
    render_object_kinds,
    render_skill_index,
    render_system_prompt,
    render_template,
    render_workspace_facts,
    rendered_prompt,
)
from ufo.runtime.skills.runtime import CORE_SKILL_REGISTRY, SkillCard, SkillRegistry
from ufo.runtime.skills.selection import prompt_index
from ufo.runtime.turns.delivery_register import DELIVERY_REGISTER_BLOCK
from ufo.sdk.delivery_register import DELIVERY_REGISTER_BLOCK as SDK_DELIVERY_REGISTER_BLOCK

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


def test_shell_routes_around_a_constraint_instead_of_ending_the_turn() -> None:
    prose = " ".join(SHELL.split("<refusals>")[1].split("</refusals>")[0].split())
    assert "blocks that route, not the goal behind it" in prose
    assert (
        "name the specific substitute available to you and the ground on which you may use it"
        in prose
    )
    assert "why it is the closest fit to what they wanted" in prose
    assert "what the one you chose has, never what the rest lack" in prose
    assert "Pick the substitute on what you already know or can see in a step or two" in prose
    assert "do the member's work with it and refine only if rounds remain" in prose
    assert "Something the member has already rejected is not a substitute" in prose
    assert "Never reconstruct the blocked thing to score candidates against it" in prose
    assert "Lead the answer with the work you did rather than with what you would not do" in prose
    assert "ride as one sentence inside it rather than as the closing ask" in prose


def test_shell_keeps_a_file_rewrite_off_the_shell() -> None:
    prose = " ".join(SHELL.split())
    assert "never rides an ad-hoc cat, sed, or echo redirection" in prose
    assert "nor a script whose purpose is to rewrite a file" in prose


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


def test_shell_writes_the_report_and_sends_it_only_when_the_member_asks() -> None:
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
    assert "For a member, the artifact is a Markdown file in /workspace" in prose
    assert "one a subagent wrote in your sandbox" in prose
    assert "carried by a tag at the end of the closing message," in prose
    assert '<artifact path="/workspace/nightly-runner-queue.md"/>' in SHELL
    assert '<artifact name="' not in SHELL
    assert "when the agent shares your sandbox, carry its /workspace path in the tag" in prose
    assert "save what it returned under /workspace as received and carry that" in prose
    assert "The member's surface offers the file as a download beside the reply" in prose
    assert "says nothing about where the write-up is or that it can be sent" in prose
    assert (
        "share a file with share_file only when the member's ask carries one of these triggers"
    ) in prose
    assert "- they asked for a file, a document, or a format;" in SHELL
    assert (
        "- they asked for a copy of the write-up, or a new revision of a file you already shared."
    ) in SHELL
    assert "No other ask is a trigger" in prose
    assert "The trigger is what the member asked for, never the verb that carries it" in prose
    assert 'answer "send me a summary" or "give me the comparison" inline' in prose
    assert '"send me the file" and "send me that as a file" each carry a trigger' in prose
    assert "in the first sentence, then the remedy." in prose
    assert "Every inline delivery is plain prose with no header" in prose
    assert "Write that artifact to /workspace" not in prose
    assert "can be sent" not in prose.replace("or that it can be sent", "")
    assert "write the report in the message itself" not in prose
    assert "A written answer nobody asked to receive as a file" not in prose
    assert "reasoning in the same message below it" not in prose
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


def test_the_delivery_register_reaches_an_extension_through_the_sdk() -> None:
    assert SDK_DELIVERY_REGISTER_BLOCK is DELIVERY_REGISTER_BLOCK
    assert SHELL.count(SDK_DELIVERY_REGISTER_BLOCK) == 1


def test_the_register_governs_the_words_a_schema_owned_field_carries() -> None:
    prose = " ".join(SHELL.split())
    assert "A typed profile field whose schema requests the work itself is the delivery" in prose
    assert "the field carries the whole work product" in prose
    assert (
        "Plain words, what comes first, and one fact per line govern every word a member reads, "
        "whichever field carries it."
    ) in prose
    assert "not schema-owned content" not in prose
    for group in ("## Plain words", "## What comes first", "## One fact per line"):
        assert group in SHELL
    assert "Name the act and the result it produces" in prose
    assert '"No leaks permitted." → "Repair the leaks."' in prose


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


def test_the_object_kinds_block_lists_each_kind_with_its_actions() -> None:
    """One plain line per registered kind with its whole description, and the actions the turn
    holds under it. A deploy with no kind renders nothing, so the prompt carries no empty tag."""
    assert render_object_kinds(()) == ""
    page = "A hosted page a member opens by permanent link. Delete unhosts it."
    block = render_object_kinds(
        (
            ("note", "A note.", ("publish (instance)", "search (collection)")),
            ("page", page, ()),
        )
    )
    assert block.startswith("<workspace_objects>\n")
    assert "- kind: note - A note.\n  actions: publish (instance), search (collection)" in block
    assert f"- kind: page - {page}\n</workspace_objects>" in block


def test_main_prompt_renders_the_object_kinds_block_as_a_section() -> None:
    block = render_object_kinds((("note", "A note.", ("publish (instance)",)),))
    prompt = render_system_prompt(
        "A", (("workspace_objects", block),), knowledge_cutoff="2026-01"
    ).content
    assert block in prompt


def test_main_prompt_renders_the_deploy_skill_index() -> None:
    skills = (("first", "First workflow."), ("second", "Second workflow."))
    prompt = render_system_prompt("A", (), skills=skills, knowledge_cutoff="2026-01").content
    assert "<available_skills>" in prompt
    assert "</available_skills>" in prompt
    assert all(f"- {name}: {description}" in prompt for name, description in skills)


async def _materialize_nothing(name: str) -> None:
    return None


def _member_registry(count: int, stem: str = "saved") -> SkillRegistry:
    return CORE_SKILL_REGISTRY.with_member(
        tuple(
            SkillCard(name=f"{stem}-{i:04d}", description=f"Load when a member asks about {i}.")
            for i in range(count)
        ),
        _materialize_nothing,
    )


def _render(registry: SkillRegistry) -> RenderedPrompt:
    return render_system_prompt("A", (), skills=prompt_index(registry), knowledge_cutoff="2026-01")


def test_member_cards_past_the_fold_never_move_the_rendered_system_prompt() -> None:
    """The cache-invariance contract above the fold: a large member tier renders into the turn
    message, so the system prompt is byte-identical whether the agent has zero saved skills or
    thousands — at the sizes where invalidation matters, a save can never invalidate a
    conversation's cached prefix."""
    assert _render(_member_registry(0)).content == _render(_member_registry(2000)).content
    assert _render(_member_registry(0)).digest == _render(_member_registry(2000)).digest


def test_a_folded_member_tier_lists_in_the_system_prompt() -> None:
    """Below the fold the member tier lists like a deploy skill — two different small sets render
    two different prompts, and that repetition-free listing is the intended trade at this size."""
    invoice = _member_registry(1, stem="invoice")
    holiday = _member_registry(1, stem="holiday")

    assert "- invoice-0000: Load when a member asks about 0." in _render(invoice).content
    assert _render(invoice).content != _render(holiday).content
    assert _render(invoice).content != _render(_member_registry(0)).content


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


def test_the_capability_block_states_its_closer_once_for_every_line() -> None:
    """The block is the whole point of the seam: an extension contributes one line of its own, and
    the rule that reads them — already held, never a connector account — is stated once here rather
    than repeated by each. A workspace holding nothing renders nothing, so the prompt of a fresh
    workspace carries no empty tag."""
    assert render_workspace_facts(()) == ""
    block = render_workspace_facts(("Slack: installed.", "GitHub: installed."))
    assert block.startswith("<workspace_capabilities>\n")
    assert block.endswith(WORKSPACE_FACTS_CLOSER)
    assert block.count(WORKSPACE_FACTS_CLOSER) == 1
    assert "Slack: installed.\nGitHub: installed.\n</workspace_capabilities>" in block

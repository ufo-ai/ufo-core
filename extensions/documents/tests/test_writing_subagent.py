"""The `writing` profile the documents pack registers: what it may call, the model it pins, and the
skill every spawn of it starts with in hand."""

import ufo_ext_documents.manifest as documents
import ufo_ext_documents.subagent as subagent

from ufo.harness.models.catalog import CORE_MODEL_SPECS
from ufo.host.ext.loader import skill_registry
from ufo.host.tools.builtins import BUILTIN_TOOLS
from ufo.runtime.subagents import subagent_system_prompt

FILE_TOOLS = ("read", "write", "edit", "glob", "grep")


def test_documents_manifest_registers_the_writing_profile() -> None:
    manifest = documents.manifest()
    assert [profile.name for profile in manifest.subagents] == ["writing"]
    profile = manifest.subagents[0]
    assert profile.models == ("gpt-5.6-terra",)
    assert profile.input_model.model_validate({"objective": "tighten it"}).objective == "tighten it"
    assert profile.output_model.model_validate({"result": "drafted"}).result == "drafted"


def test_the_pinned_model_is_registered_and_called_on_the_surface_that_takes_tools() -> None:
    """The profile hands the child tools, so the pinned id has to be one whose spec declares the
    Responses surface: gpt-5.6-terra rejects tools plus reasoning on chat completions (#568)."""
    spec = {spec.id: spec for spec in CORE_MODEL_SPECS}[subagent.WRITING_MODEL]
    assert spec.api_surface == "responses"


async def test_every_spawn_starts_with_the_writing_drafts_workflow_in_hand() -> None:
    task = subagent.WritingTask.model_validate({"objective": "draft the announcement"})
    assert task.preload_skills == ("writing-drafts",)

    registry = skill_registry((documents.manifest(),))
    preload = await registry.materialize(registry.closure(*task.preload_skills))
    assert [entry.skill.name for entry in preload] == ["writing-drafts"]

    prompt = subagent_system_prompt(subagent.WRITING_PROFILE, preload=preload)
    assert "Act as a sharp human editor" in prompt
    assert "Measure the minimum by failures cleared, not words changed" in prompt
    assert "rewrite from this note" in prompt
    assert "use the source only to check" in prompt
    assert "make the product,\nchange, or reader the subject" in prompt
    assert '"today we release," and "today we launch"' in prompt
    assert '"Every company, project, and person is unique" stays generic' in prompt
    assert "references/checklist.md" in prompt


def test_writing_tools_are_the_file_builtins_without_execution_or_delivery() -> None:
    profile = subagent.WRITING_PROFILE
    builtin_names = {tool.name for tool in BUILTIN_TOOLS}
    for name in FILE_TOOLS:
        assert name in profile.tool_names and name in builtin_names
    assert "load_skill" in profile.tool_names
    assert {
        "bash",
        "js_repl",
        "xlsx_repl",
        "search_web",
        "fetch_url",
        "share_file",
        "ask_user",
        "spawn",
        "cancel_spawn",
    }.isdisjoint(profile.tool_names)


def test_the_child_prompt_carries_the_writing_boundary() -> None:
    assert "Follow the preloaded `writing-drafts` skill" in subagent.WRITING_PROMPT
    assert "make the narrowest reasonable assumption and name it" in subagent.WRITING_PROMPT
    assert "Never invent a claim, quote, number, source, or link" in subagent.WRITING_PROMPT
    assert "Do not ask questions" in subagent.WRITING_PROMPT
    assert "Return short copy inline" in subagent.WRITING_PROMPT
    assert "Use Simplified Technical English as the clarity baseline" in subagent.WRITING_PROMPT
    assert "Preserve the claims and voice, not the draft's wording" in subagent.WRITING_PROMPT
    assert "Do not use the source's sentences as a scaffold" in subagent.WRITING_PROMPT
    assert "A source sentence is not itself a claim" in subagent.WRITING_PROMPT
    assert "Treat non-prose markers as fixed anchors" in subagent.WRITING_PROMPT
    assert "bind each marker to its adjacent source claim" in subagent.WRITING_PROMPT
    assert "lightly paraphrasing it is not a completed edit" in subagent.WRITING_PROMPT
    assert "assign each option a different purpose" in subagent.WRITING_PROMPT
    assert "the intended user or use, and a concrete constraint" in subagent.WRITING_PROMPT
    assert "Do not return paraphrases of one line" in subagent.WRITING_PROMPT


def test_writing_result_uses_only_the_shared_register_bound() -> None:
    draft = "x" * 10_000
    assert subagent.WritingResult.model_validate({"result": draft}).result == draft

    schema = subagent.WritingResult.model_json_schema()["properties"]["result"]
    assert "maxLength" not in schema
    assert "shared delivery register" in schema["description"].casefold()

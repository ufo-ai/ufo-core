import pytest
from pydantic import BaseModel, Field, ValidationError

from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.host import environment
from ufo.host.assemble import (
    _applied_document,
    _run_command,
    _run_tool,
    _skills_with_document,
)
from ufo.host.environment import (
    EnvironmentDocument,
    EnvironmentOverrides,
    PromptOverride,
    ToolInput,
    ToolOverride,
)
from ufo.runtime.prompts.render import rendered_prompt
from ufo.runtime.skills.runtime import SkillCard, SkillRegistry, parse_skill_content
from ufo.runtime.tools.context import ToolContext, ToolResult
from ufo.runtime.tools.registry import ToolDef, ToolRegistry
from ufo.runtime.workspace import ws


class _BashInput(BaseModel):
    command: str = Field(description="The command to run.")
    background: bool = Field(default=False, description="Run detached.")


async def _never(context: ToolContext, payload: _BashInput) -> ToolResult:
    raise AssertionError("environment tests never dispatch")


def _registry() -> ToolRegistry:
    return ToolRegistry(
        (
            ToolDef(
                name="bash", description="run a command", input_model=_BashInput, handler=_never
            ),
            ToolDef(name="web", description="fetch a page", input_model=_BashInput, handler=_never),
        )
    )


PROMPT = rendered_prompt(
    "You are the coding agent.\n"
    "- Prefer the dedicated tools over `bash` when one fits.\n"
    "- Match the surrounding code's conventions."
)


def _skill_md(name: str, description: str, body: str) -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n"


def test_a_document_parses_from_yaml_or_json_to_one_digest() -> None:
    yaml_body = (
        b"profiles:\n"
        b"  coding:\n"
        b"    prompt:\n"
        b"      text: OVERRIDDEN\n"
        b"    tools:\n"
        b"      bash:\n"
        b"        description: patched\n"
    )
    json_body = (
        b'{"profiles": {"coding": {"tools": {"bash": {"description": "patched"}},'
        b' "prompt": {"text": "OVERRIDDEN"}}}}'
    )
    yaml_doc, yaml_canonical, yaml_digest = environment.parse_environment_document(yaml_body)
    json_doc, json_canonical, json_digest = environment.parse_environment_document(json_body)
    assert yaml_doc == json_doc
    assert yaml_canonical == json_canonical
    assert yaml_digest == json_digest
    assert yaml_digest.startswith("sha256:")


def test_a_malformed_or_oversized_or_widened_document_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="not valid YAML"):
        environment.parse_environment_document(b"{unclosed")
    with pytest.raises(ValueError, match=r"extra_forbidden|Extra inputs"):
        environment.parse_environment_document(b'{"grant_tools": ["bash"]}')
    monkeypatch.setattr(environment, "ENVIRONMENT_DOCUMENT_BYTE_CAP", 8)
    with pytest.raises(ValueError, match="the cap is 8"):
        environment.parse_environment_document(b'{"main": {}}')


def test_a_prompt_override_is_text_or_replace_never_both() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        PromptOverride(text="x", replace=({"old": "a", "new": "b"},))
    with pytest.raises(ValidationError, match="exactly one"):
        PromptOverride()


def test_a_tool_override_carries_one_meaning() -> None:
    with pytest.raises(ValidationError, match="takes no other field"):
        ToolOverride(enabled=False, description="x")
    with pytest.raises(ValidationError, match="requires `run`"):
        ToolOverride(input={"path": ToolInput(description="a path")})
    with pytest.raises(ValidationError, match="defines its own `input`"):
        ToolOverride(run="wc -l", parameters={"path": "x"})
    with pytest.raises(ValidationError, match="must change something"):
        ToolOverride()


def test_a_skills_entry_parses_as_the_skill_it_names() -> None:
    with pytest.raises(ValueError, match="must match its directory"):
        EnvironmentDocument.model_validate(
            {"skills": {"coding": _skill_md("other", "Load when coding.", "Body.")}}
        )
    document = EnvironmentDocument.model_validate(
        {"skills": {"coding": _skill_md("coding", "Load when coding.", "Body.")}}
    )
    assert "coding" in document.skills


def test_a_files_entry_is_a_contained_destination_and_a_digest() -> None:
    digest = f"sha256:{'a' * 64}"
    document = EnvironmentDocument.model_validate({"files": {"data/case.tar": digest}})
    assert document.files == {"data/case.tar": digest}
    with pytest.raises(ValueError, match="workspace-relative"):
        EnvironmentDocument.model_validate({"files": {"/etc/passwd": digest}})
    with pytest.raises(ValueError, match="workspace-relative"):
        EnvironmentDocument.model_validate({"files": {"../escape": digest}})
    with pytest.raises(ValueError, match="sha256 digest"):
        EnvironmentDocument.model_validate({"files": {"data/case.tar": "./case.tar"}})


async def test_a_stored_file_loads_by_its_digest_and_only_intact(tmp_path) -> None:
    blob = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path))
    with ws("11111111-1111-1111-1111-111111111111"):
        digest = await environment.store_environment_file(blob, b"archive bytes")
        assert digest == await environment.store_environment_file(blob, b"archive bytes")
        assert await environment.load_environment_file(blob, digest) == b"archive bytes"
        key = environment.ENVIRONMENT_FILE_KEY_PREFIX + digest.removeprefix("sha256:")
        await blob.put(key, b"tampered")
        with pytest.raises(ValueError, match="does not match its digest"):
            await environment.load_environment_file(blob, digest)


async def test_a_stored_document_loads_by_its_digest_and_only_intact(tmp_path) -> None:
    blob = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path))
    with ws("11111111-1111-1111-1111-111111111111"):
        digest = await environment.store_environment_document(
            blob, b"main:\n  tools:\n    web:\n      enabled: false\n"
        )
        loaded = await environment.load_environment_document(blob, digest)
        assert loaded.main == EnvironmentOverrides(tools={"web": ToolOverride(enabled=False)})
        with pytest.raises(ValueError, match="malformed"):
            await environment.load_environment_document(blob, "sha256:zz")
        key = environment.ENVIRONMENT_DOCUMENT_KEY_PREFIX + digest.removeprefix("sha256:")
        await blob.put(key, b'{"main": null}')
        with pytest.raises(ValueError, match="does not match its digest"):
            await environment.load_environment_document(blob, digest)


def test_a_description_rewrite_touches_only_the_named_tool() -> None:
    prompt, tools, replaced = _applied_document(
        PROMPT,
        _registry(),
        EnvironmentOverrides(tools={"bash": ToolOverride(description="patched")}),
        {},
    )
    assert prompt == PROMPT
    assert not replaced
    assert tools.get("bash").description == "patched"
    assert tools.get("web").description == "fetch a page"
    assert tools.get("bash").handler is _never


def test_a_parameter_rewrite_clones_the_model_and_keeps_the_rest() -> None:
    _prompt, tools, _replaced = _applied_document(
        PROMPT,
        _registry(),
        EnvironmentOverrides(
            tools={"bash": ToolOverride(parameters={"background": "the waiting arm's wording"})}
        ),
        {},
    )
    patched = tools.get("bash").input_model
    assert patched is not _BashInput
    assert patched.model_fields["background"].description == "the waiting arm's wording"
    assert patched.model_fields["background"].default is False
    assert patched.model_fields["command"].description == "The command to run."
    assert _BashInput.model_fields["background"].description == "Run detached."
    assert tools.get("web").input_model is _BashInput
    with pytest.raises(ValueError, match="does not take: budget"):
        _applied_document(
            PROMPT,
            _registry(),
            EnvironmentOverrides(tools={"bash": ToolOverride(parameters={"budget": "x"})}),
            {},
        )


def test_a_disabled_tool_leaves_the_offer() -> None:
    _prompt, tools, _replaced = _applied_document(
        PROMPT,
        _registry(),
        EnvironmentOverrides(tools={"web": ToolOverride(enabled=False)}),
        {},
    )
    assert [tool.name for tool in tools.tools] == ["bash"]


def test_a_scoped_name_the_offer_does_not_hold_fails_loud() -> None:
    with pytest.raises(ValueError, match="does not offer: spawn"):
        _applied_document(
            PROMPT,
            _registry(),
            EnvironmentOverrides(tools={"spawn": ToolOverride(description="granted")}),
            {},
        )


def test_a_global_override_applies_where_offered_and_skips_elsewhere() -> None:
    _prompt, tools, _replaced = _applied_document(
        PROMPT,
        _registry(),
        None,
        {
            "bash": ToolOverride(description="patched everywhere"),
            "spawn": ToolOverride(description="not offered here"),
        },
    )
    assert tools.get("bash").description == "patched everywhere"
    assert [tool.name for tool in tools.tools] == ["bash", "web"]


def test_a_scoped_entry_wins_over_the_global_one() -> None:
    _prompt, tools, _replaced = _applied_document(
        PROMPT,
        _registry(),
        EnvironmentOverrides(tools={"bash": ToolOverride(description="scoped")}),
        {"bash": ToolOverride(description="global")},
    )
    assert tools.get("bash").description == "scoped"


def test_a_prompt_text_override_replaces_content_and_digest() -> None:
    prompt, _tools, replaced = _applied_document(
        PROMPT,
        _registry(),
        EnvironmentOverrides(prompt=PromptOverride(text="OVERRIDDEN")),
        {},
    )
    assert prompt.content == "OVERRIDDEN"
    assert prompt.digest != PROMPT.digest
    assert replaced


def test_prompt_edits_land_in_sequence_each_exactly_once() -> None:
    prompt, _tools, replaced = _applied_document(
        PROMPT,
        _registry(),
        EnvironmentOverrides(
            prompt=PromptOverride(
                replace=(
                    {
                        "old": "Match the surrounding code's conventions.",
                        "new": (
                            "Match the surrounding code's conventions.\n"
                            "- Run a long command with a `timeout` that covers it."
                        ),
                    },
                    {
                        "old": "Prefer the dedicated tools over `bash` when one fits.",
                        "new": "Prefer the dedicated tools.",
                    },
                )
            )
        ),
        {},
    )
    assert "timeout` that covers it" in prompt.content
    assert "Prefer the dedicated tools.\n" in prompt.content
    assert "when one fits" not in prompt.content
    assert prompt.content.startswith("You are the coding agent.")
    assert not replaced
    with pytest.raises(ValueError, match="found 0"):
        _applied_document(
            PROMPT,
            _registry(),
            EnvironmentOverrides(prompt=PromptOverride(replace=({"old": "absent", "new": "x"},))),
            {},
        )
    with pytest.raises(ValueError, match="found 3"):
        _applied_document(
            PROMPT,
            _registry(),
            EnvironmentOverrides(prompt=PromptOverride(replace=({"old": "the", "new": "x"},))),
            {},
        )


def test_a_run_tool_joins_the_offer_with_its_input_model() -> None:
    _prompt, tools, _replaced = _applied_document(
        PROMPT,
        _registry(),
        EnvironmentOverrides(
            tools={
                "count_lines": ToolOverride(
                    description="Count lines in a file.",
                    input={"file": ToolInput(description="Path to the file.")},
                    run='wc -l < "$INPUT_FILE"',
                )
            }
        ),
        {},
    )
    added = tools.get("count_lines")
    assert added.description == "Count lines in a file."
    assert added.side_effecting
    assert added.input_model.model_fields["file"].description == "Path to the file."
    with pytest.raises(ValueError, match="needs a description"):
        _applied_document(
            PROMPT,
            _registry(),
            EnvironmentOverrides(tools={"nameless": ToolOverride(run="true")}),
            {},
        )


def test_a_run_tool_replacing_an_offered_tool_keeps_its_description() -> None:
    _prompt, tools, _replaced = _applied_document(
        PROMPT,
        _registry(),
        EnvironmentOverrides(tools={"web": ToolOverride(run='curl -s "$INPUT_URL"')}),
        {},
    )
    replaced_tool = tools.get("web")
    assert replaced_tool.description == "fetch a page"
    assert replaced_tool.handler is not _never


def test_a_run_command_carries_inputs_as_environment_variables() -> None:
    model = _run_tool(
        "probe",
        "probe",
        {
            "path": ToolInput(description="a path"),
            "count": ToolInput(type="integer", description="how many", required=False),
            "deep": ToolInput(type="boolean", description="recurse", required=False),
        },
        "probe.sh",
    ).input_model
    command = _run_command("probe.sh", model(path="a b", deep=True))
    assert command == "env INPUT_PATH='a b' INPUT_DEEP=true sh -c probe.sh"
    assert _run_command("probe.sh", model(path="x", count=3)).startswith(
        "env INPUT_PATH=x INPUT_COUNT=3 "
    )


def _skills() -> SkillRegistry:
    coding = parse_skill_content(
        "coding",
        {
            "SKILL.md": _skill_md("coding", "Load when coding.", "The original workflow.").encode(),
            "references/tips.md": b"keep tips",
        },
    )
    return SkillRegistry(by_name={"coding": coding})


def test_a_skill_override_replaces_the_body_and_keeps_assets() -> None:
    document = EnvironmentDocument(
        skills={"coding": _skill_md("coding", "Load when coding.", "The ablated workflow.")}
    )
    skills = _skills_with_document(_skills(), document)
    overridden = skills.named("coding")
    assert overridden.instructions == "The ablated workflow."
    assert dict(overridden.files)["references/tips.md"] == b"keep tips"
    assert "coding" not in (skills.bundled_names or frozenset())


def test_a_skill_edit_lands_on_the_live_skill_or_fails_loud() -> None:
    document = EnvironmentDocument.model_validate(
        {
            "skills": {
                "coding": {
                    "replace": [
                        {"old": "Load when coding.", "new": "Load when the routing arm says."},
                        {"old": "The original workflow.", "new": "The edited workflow."},
                    ]
                }
            }
        }
    )
    edited = _skills_with_document(_skills(), document).named("coding")
    assert edited.description == "Load when the routing arm says."
    assert edited.instructions == "The edited workflow."
    assert dict(edited.files)["references/tips.md"] == b"keep tips"
    with pytest.raises(ValueError, match="found 0"):
        _skills_with_document(
            _skills(),
            EnvironmentDocument.model_validate(
                {"skills": {"coding": {"replace": [{"old": "absent anchor", "new": "x"}]}}}
            ),
        )
    with pytest.raises(ValueError, match="does not hold"):
        _skills_with_document(
            _skills(),
            EnvironmentDocument.model_validate(
                {"skills": {"unknown": {"replace": [{"old": "a", "new": "b"}]}}}
            ),
        )


def test_a_skill_addition_joins_the_index() -> None:
    document = EnvironmentDocument(
        skills={"report-style": _skill_md("report-style", "Load when reporting.", "New workflow.")}
    )
    skills = _skills_with_document(_skills(), document)
    assert ("report-style", "Load when reporting.") in skills.index()
    assert skills.named("report-style").instructions == "New workflow."


def test_a_member_skill_is_never_a_documents_to_change() -> None:
    registry = SkillRegistry(
        by_name={},
        member_cards={"saved": SkillCard(name="saved", description="a member's own")},
    )
    document = EnvironmentDocument(
        skills={"saved": _skill_md("saved", "Load when saved.", "Clobbered.")}
    )
    with pytest.raises(ValueError, match="member skill"):
        _skills_with_document(registry, document)

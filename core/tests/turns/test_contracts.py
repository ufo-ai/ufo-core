import json

import pytest
from pydantic import ValidationError

from ufo.turns.contracts import (
    DECLARED_SCHEMA_MAX_CHARS,
    JsonContract,
    ResultOutput,
    TaskInput,
    check_declared_schema,
    input_contract,
    output_contract,
)

TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {"severity": {"type": "string"}, "owner": {"type": "string"}},
    "required": ["severity"],
    "additionalProperties": False,
}


def test_json_contract_validates_conforming_payload() -> None:
    validated = JsonContract(TRIAGE_SCHEMA).model_validate({"severity": "high"})
    assert json.loads(validated.model_dump_json()) == {"severity": "high"}


def test_json_contract_names_each_fault_with_its_path() -> None:
    with pytest.raises(ValidationError) as caught:
        JsonContract(TRIAGE_SCHEMA).model_validate({"owner": 3})
    faults = caught.value.errors(include_url=False, include_input=False)
    locs = {fault["loc"] for fault in faults}
    assert ("owner",) in locs
    assert any("severity" in fault["msg"] for fault in faults)


def test_json_contract_rejects_unparseable_json() -> None:
    with pytest.raises(ValidationError) as caught:
        JsonContract(TRIAGE_SCHEMA).model_validate_json("not json")
    assert "invalid JSON" in str(caught.value)


def test_json_contract_round_trips_text() -> None:
    validated = JsonContract(TRIAGE_SCHEMA).model_validate_json('{"severity": "low"}')
    assert validated.model_dump() == {"severity": "low"}


def test_contracts_default_to_task_and_result() -> None:
    assert input_contract(None) is TaskInput
    assert output_contract(None) is ResultOutput
    assert isinstance(input_contract(TRIAGE_SCHEMA), JsonContract)
    assert isinstance(output_contract(TRIAGE_SCHEMA), JsonContract)


def test_json_contract_exposes_the_declared_schema() -> None:
    assert JsonContract(TRIAGE_SCHEMA).model_json_schema() == TRIAGE_SCHEMA


def test_declared_schema_must_be_a_top_level_object() -> None:
    with pytest.raises(ValueError, match="type of 'object'"):
        check_declared_schema({"type": "string"}, "input_schema")


def test_declared_schema_must_compile() -> None:
    with pytest.raises(ValueError, match="not a valid JSON Schema"):
        check_declared_schema(
            {"type": "object", "properties": {"x": {"type": "no-such-type"}}}, "output_schema"
        )


def test_declared_schema_is_bounded() -> None:
    oversized = {
        "type": "object",
        "properties": {f"key{i}": {"type": "string"} for i in range(DECLARED_SCHEMA_MAX_CHARS)},
    }
    with pytest.raises(ValueError, match="exceeds"):
        check_declared_schema(oversized, "input_schema")


def test_declared_schema_accepts_a_typed_specialist_contract() -> None:
    check_declared_schema(TRIAGE_SCHEMA, "output_schema")


def test_declared_schema_refuses_references() -> None:
    with pytest.raises(ValueError, match=r"may not use \$ref"):
        check_declared_schema(
            {
                "type": "object",
                "properties": {"x": {"$ref": "http://169.254.169.254/latest/meta-data/"}},
            },
            "input_schema",
        )
    with pytest.raises(ValueError, match=r"may not use \$dynamicRef"):
        check_declared_schema(
            {
                "type": "object",
                "properties": {"x": {"items": [{"$dynamicRef": "#thing"}]}},
            },
            "output_schema",
        )


def test_declared_schema_refuses_regular_expressions() -> None:
    with pytest.raises(ValueError, match="may not use pattern"):
        check_declared_schema(
            {"type": "object", "properties": {"task": {"type": "string", "pattern": "^(a+)+$"}}},
            "input_schema",
        )
    with pytest.raises(ValueError, match="may not use patternProperties"):
        check_declared_schema(
            {
                "type": "object",
                "properties": {"x": {"patternProperties": {"^(a+)+$": {"type": "string"}}}},
            },
            "output_schema",
        )


def test_a_reference_that_reaches_a_contract_raises_without_fetching() -> None:
    smuggled = JsonContract(
        {
            "type": "object",
            "properties": {"x": {"$ref": "http://169.254.169.254/latest/meta-data/"}},
        }
    )
    with pytest.raises(ValidationError, match="unresolvable reference"):
        smuggled.model_validate({"x": 1})

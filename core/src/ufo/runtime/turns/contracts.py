"""Spawn I/O contracts: one validating interface both target kinds satisfy.

A subagent profile's contract is its pydantic models. A workspace agent's is data — an optional
raw JSON Schema pair on its row, defaulting to the task/result pair below. A declared schema is
validated raw through `JsonContract`, never translated into a generated model: the contract
answers the same three calls a model class answers where spawn, dispatch, and delivery validate
(`model_json_schema`, `model_validate`, `model_validate_json`) and raises pydantic's own
`ValidationError`, so every catch site reads one error shape. A declared schema is member data,
so it never references out and never carries a regular expression: `$ref` and `pattern` are
refused at the write, and validation resolves against an empty registry — a reference that
reaches a contract anyway raises as a `ValidationError`, never as a fetch from the serve
process, and no member value can make a match cost the one serving loop unbounded time."""

import json
from collections.abc import Mapping
from dataclasses import dataclass

from jsonschema.exceptions import SchemaError
from jsonschema.validators import Draft202012Validator
from pydantic import BaseModel, Field, ValidationError
from pydantic_core import InitErrorDetails, PydanticCustomError
from referencing import Registry
from referencing.exceptions import Unresolvable

from ufo.runtime.turns.delivery_register import SUBAGENT_RESULT_DESCRIPTION

DECLARED_SCHEMA_MAX_CHARS = 8_192
REFUSED_KEYWORDS = frozenset({"$ref", "$dynamicRef", "pattern", "patternProperties"})


class TaskInput(BaseModel):
    task: str = Field(
        description="Freeform task governed by the shared delivery register.",
    )


class ResultOutput(BaseModel):
    result: str = Field(
        description=SUBAGENT_RESULT_DESCRIPTION,
    )


class AgentResultOutput(BaseModel):
    result: str = Field(
        description="Freeform result governed by the shared delivery register.",
    )


@dataclass(frozen=True)
class ValidatedJson:
    """A payload that passed its raw schema, answering the reads a validated model answers."""

    data: object

    def model_dump(self) -> object:
        return self.data

    def model_dump_json(self) -> str:
        return json.dumps(self.data)


@dataclass(frozen=True)
class JsonContract:
    """A raw JSON Schema standing where a pydantic model class stands."""

    schema: Mapping[str, object]

    def model_json_schema(self) -> dict[str, object]:
        return dict(self.schema)

    def model_validate(self, data: object) -> ValidatedJson:
        try:
            faults = sorted(
                Draft202012Validator(dict(self.schema), registry=Registry()).iter_errors(data),
                key=lambda fault: fault.json_path,
            )
        except Unresolvable as error:
            raise ValidationError.from_exception_data(
                "JsonContract",
                [
                    InitErrorDetails(
                        type=PydanticCustomError(
                            "json_schema",
                            "{fault}",
                            {"fault": f"schema holds an unresolvable reference: {error}"},
                        ),
                        loc=(),
                        input=data,
                    )
                ],
            ) from error
        if faults:
            raise ValidationError.from_exception_data(
                "JsonContract",
                [
                    InitErrorDetails(
                        type=PydanticCustomError(
                            "json_schema", "{fault}", {"fault": fault.message}
                        ),
                        loc=tuple(fault.absolute_path),
                        input=fault.instance,
                    )
                    for fault in faults
                ],
            )
        return ValidatedJson(data)

    def model_validate_json(self, text: str) -> ValidatedJson:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as error:
            raise ValidationError.from_exception_data(
                "JsonContract",
                [
                    InitErrorDetails(
                        type=PydanticCustomError(
                            "json_schema", "{fault}", {"fault": f"invalid JSON: {error}"}
                        ),
                        loc=(),
                        input=text,
                    )
                ],
            ) from error
        return self.model_validate(data)


Contract = type[BaseModel] | JsonContract


def freeform_result_contract(contract: Contract) -> bool:
    """Whether a contract is the one freeform `result: str` handoff shape."""
    match contract:
        case type() as model:
            fields = model.model_fields
            return tuple(fields) == ("result",) and fields["result"].annotation is str
        case _:
            return False


def payload_keys(contract: Contract) -> str:
    """The keys a payload for this contract takes: required ones first and bare, the rest marked.

    Read from `model_json_schema` so a profile's model class and an agent's declared schema render
    the same, and so this is the only place that decides how a contract's shape is spelled to a
    model: the spawn catalog lists it per target, and a refused spawn quotes it for the one target
    that refused."""
    schema = contract.model_json_schema()
    properties = schema.get("properties")
    if not isinstance(properties, Mapping) or not properties:
        return "(no keys)"
    required = schema.get("required")
    names = frozenset(required) if isinstance(required, list) else frozenset()
    return ", ".join(
        f"`{name}`" if name in names else f"`{name}` (optional)"
        for name in sorted(properties, key=lambda name: (name not in names, name))
    )


def input_contract(schema: Mapping[str, object] | None) -> Contract:
    return TaskInput if schema is None else JsonContract(schema)


def output_contract(schema: Mapping[str, object] | None) -> Contract:
    return AgentResultOutput if schema is None else JsonContract(schema)


def check_declared_schema(candidate: Mapping[str, object], field: str) -> None:
    """Refuse a declared I/O schema at the write that stores it, never at the spawn that reads it:
    it must be a bounded JSON Schema object declaring a top-level object payload, it must compile,
    it must reference nothing — a `$ref` names something outside the row, and resolving one from
    member data would hand the serve process a URI to fetch — and it must carry no regular
    expression, whose match cost member data could make unbounded on the one serving loop."""
    if len(json.dumps(candidate)) > DECLARED_SCHEMA_MAX_CHARS:
        raise ValueError(f"{field} exceeds {DECLARED_SCHEMA_MAX_CHARS} characters")
    if candidate.get("type") != "object":
        raise ValueError(f"{field} must declare a top-level type of 'object'")
    if refused := _refused_keyword(candidate):
        raise ValueError(f"{field} may not use {refused}; declare the shape inline without it")
    try:
        Draft202012Validator.check_schema(dict(candidate))
    except SchemaError as error:
        raise ValueError(f"{field} is not a valid JSON Schema: {error.message}") from error


def _refused_keyword(node: object) -> str | None:
    match node:
        case Mapping():
            for key, value in node.items():
                if key in REFUSED_KEYWORDS:
                    return key
                if found := _refused_keyword(value):
                    return found
        case list():
            for item in node:
                if found := _refused_keyword(item):
                    return found
    return None

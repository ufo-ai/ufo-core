"""The app-bench process criterion, driven directly.

A live run costs an hour and a loaded machine distorts it, so the rule that decides who may bind
the homepage is pinned here instead."""

import json

import pytest

from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.suites.ufo_app_bench import (
    APPLICATION_BUILDER_NAME,
    BUILDER_TARGET,
    _application_builder_scorer,
)
from ufo.harness.untrusted import wall

DEPLOYED = json.dumps({"site": "board", "site_url": "https://ufo.test/board"})


def _tool(name: str, call_id: str, **input_fields: object) -> ToolInvocation:
    return ToolInvocation(
        name=name, input=dict(input_fields), result="{}", has_result=True, call_id=call_id
    )


def _spawn(call_id: str, target: str = BUILDER_TARGET) -> ToolInvocation:
    return ToolInvocation(
        name="spawn",
        input={"target": target, "payload": {"objective": "build the page"}},
        result=wall("spawn", DEPLOYED),
        has_result=True,
        call_id=call_id,
    )


def _bind(call_id: str) -> ToolInvocation:
    return _tool(
        "object_action", call_id, kind="agent", action="set_homepage", input={"site": "board"}
    )


def _worker_calls() -> tuple[ToolInvocation, ...]:
    return (
        _tool("write", "c1", file_path="/workspace/ufo-app/application-design.svg"),
        _tool("write", "c2", file_path="/workspace/ufo-app/app.tsx"),
        _tool("object_action", "c3", kind="site", action="deploy_website", input={}),
    )


async def _grade(own: tuple[ToolInvocation, ...], children: tuple[ToolInvocation, ...]):
    grader = _application_builder_scorer()
    return await grader(CapabilityOutput(response="", calls=(*own, *children), own_calls=own))


@pytest.mark.asyncio
async def test_the_parent_may_bind_the_homepage() -> None:
    """`new_application` states the contract both suites hold — "Binding is the parent's act,
    never its own" — and the skill has the parent bind what the worker hosted. Listing the action
    among the parent's forbidden tools failed a parent for finishing the flow: two recorded
    app-bench cases died on "the parent entered the worker loop: action:agent:set_homepage"."""

    verdict = await _grade((_spawn("p1"), _bind("p2")), _worker_calls())
    assert verdict.passed, verdict.reason


@pytest.mark.asyncio
async def test_the_worker_may_not_bind_its_own_homepage() -> None:
    verdict = await _grade((_spawn("p1"),), (*_worker_calls(), _bind("c4")))
    assert not verdict.passed
    assert verdict.reason == "the worker tried to bind its own homepage"


@pytest.mark.asyncio
async def test_a_bare_target_names_the_same_builder() -> None:
    """`_resolve` takes `profile:<name>` and a bare `<name>` to one profile, so a parent that
    spelled it bare graded as no delegation at all: a recorded `action-issue-owner` case spawned
    `ufo_application_builder`, hosted its page, and read back "did not spawn the application
    builder"."""

    verdict = await _grade((_spawn("p1", APPLICATION_BUILDER_NAME), _bind("p2")), _worker_calls())
    assert verdict.passed, verdict.reason


@pytest.mark.asyncio
async def test_the_parent_may_not_write_the_source() -> None:
    own = (_spawn("p1"), _tool("write", "p2", file_path="/workspace/ufo-app/app.tsx"))
    verdict = await _grade(own, _worker_calls())
    assert not verdict.passed
    assert "entered the worker loop" in verdict.reason

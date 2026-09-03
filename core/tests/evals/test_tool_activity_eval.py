import asyncio
import json
from dataclasses import dataclass, field
from typing import cast

from evals.harness.target import CapabilityTarget
from evals.suites.tool_activity import tool_activity_task
from ufo.harness.models.interface import Message
from ufo.runtime.turns.activity import ACTIVITY_PROMPT


@dataclass
class _Judge:
    systems: list[str] = field(default_factory=list)

    async def complete(self, system: str, messages: tuple[Message, ...]) -> str:
        self.systems.append(system)
        content = messages[0].content
        assert isinstance(content, str)
        payload = json.loads(content)
        tool = payload["tool_call"]["name"]
        if tool == "bash" and "ext4.txt" in payload["tool_call"]["arguments"]:
            return "Reading the corrected repository source"
        return {
            "memory_update": "Saving the Monday standup time",
            "edit_file": "Updating the release notes heading",
            "bash": "Checking the requested text output",
            "update_todo_status": "Updating checklist progress",
            "read": "Reading the deployment report",
            "review_grep": "Finding the Slack activity rendering",
            "call_external_tool": "Updating the launch checklist issue",
            "spawn_subagent": "Reviewing the access-control change",
        }[tool]


@dataclass(frozen=True)
class _Target:
    judge: _Judge


async def test_tool_activity_task_grades_fixed_calls_through_the_production_summarizer() -> None:
    judge = _Judge()
    task = tool_activity_task()

    report = await task.run(
        cast(CapabilityTarget, _Target(judge)), asyncio.Semaphore(len(task.cases))
    )

    assert report.passed
    assert [case.name for case in report.cases] == list(task.cases)
    assert judge.systems == [ACTIVITY_PROMPT] * len(task.cases)

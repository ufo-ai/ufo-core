"""Member-facing summaries of tool calls."""

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ufo.harness.models.interface import Message, ModelRequest, ToolUseBlock
from ufo.harness.o11y import emit_metric, log

SKILL_LOAD_TOOL = "load_skill"
SKILL_SEARCH_ACTION_ID = "action:skill:skill_search"
ACTIVITY_JOB = "core:tool_activity"
ACTIVITY_GOAL_CHARS = 800
ACTIVITY_ARGUMENT_CHARS = 600
ACTIVITY_MAX_TOKENS = 32
ACTIVITY_TIMEOUT_SECONDS = 8
ACTIVITY_RECENT_LABELS = 5
ACTIVITY_PROMPT = (Path(__file__).parent.parent / "prompts" / "activity.md").read_text().strip()


class ActivityModel(Protocol):
    @property
    def model(self) -> str: ...

    async def complete(self, request: ModelRequest) -> str: ...


@dataclass(frozen=True)
class ActivitySummarizer:
    """Summarize one tool call as a member-facing step."""

    model: ActivityModel

    async def summarize(
        self,
        call: ToolUseBlock,
        goal: str = "",
        recent: tuple[str, ...] = (),
    ) -> str | None:
        payload = json.dumps(
            {
                "goal": goal[:ACTIVITY_GOAL_CHARS],
                "tool_call": {
                    "name": call.name,
                    "arguments": _bounded_arguments(call.input),
                },
                "recent_labels": list(recent[-ACTIVITY_RECENT_LABELS:]),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        request = ModelRequest(
            model=self.model.model,
            system=ACTIVITY_PROMPT,
            messages=(Message(role="user", content=payload),),
            max_tokens=ACTIVITY_MAX_TOKENS,
            conversation_cache_ttl="5m",
            reasoning="off",
        )
        try:
            async with asyncio.timeout(ACTIVITY_TIMEOUT_SECONDS):
                completion = await self.model.complete(request)
        except Exception as error:
            emit_metric("tool_activity_failed_total", error_class=type(error).__name__)
            log("tool.activity_failed", error_class=type(error).__name__)
            return None
        return activity_line(completion)


def _bounded_arguments(arguments: dict[str, object]) -> str:
    rendered = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
    if len(rendered) <= ACTIVITY_ARGUMENT_CHARS:
        return rendered
    return rendered[:ACTIVITY_ARGUMENT_CHARS] + "…"


def activity_line(text: str) -> str | None:
    """Return one normalized label from a model completion."""
    line = re.sub(r"\s+", " ", text).strip().lstrip("-*• ").strip("`\"'")
    return line.rstrip(".!?…").rstrip() or None

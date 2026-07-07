"""The LLM rubric judge: grade a produced answer against an ordered rubric with one judge
completion. The judge is a grader, not a control-plane turn — it runs on an injected model leg the
operator builds in-process. With no judge wired, the scripted path keyword-matches each criterion's
quoted salient terms, so the grading machinery runs deterministically without a model."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Protocol

from ufo.sdk.models import Message

JUDGE_SYSTEM = (
    "You grade an AI agent's answer against a rubric. You see the instruction, the answer the "
    "agent produced, and a rubric. Score each criterion PASS/FAIL on whether the answer satisfies "
    "it. The agent phrases things its own way — judge the MEANING, never the exact wording. If "
    "the answer is "
    'empty or does not match a criterion, fail it. Return ONLY a JSON object {"items":[{"passed":'
    'true,"reason":"..."}]} with one entry per criterion, in order.'
)


class JudgeLeg(Protocol):
    async def complete(self, system: str, messages: tuple[Message, ...]) -> str: ...


async def rubric_pass(
    instruction: str, answer: str, rubric: Sequence[str], judge: JudgeLeg | None
) -> tuple[bool, str]:
    """Grade `answer` against `rubric`. With a `judge` wired, one judge completion scores every
    criterion; without one, the scripted fallback keyword-matches each criterion's quoted terms."""
    if judge is None:
        return _scripted_rubric(answer, rubric)
    criteria = "\n".join(f"{index + 1}. {item}" for index, item in enumerate(rubric))
    prompt = (
        f"INSTRUCTION:\n{instruction}\n\n"
        f"ANSWER THE AGENT PRODUCED:\n{answer}\n\n"
        f"RUBRIC (judge each, in order):\n{criteria}\n\nReturn the JSON verdict now."
    )
    verdicts = _verdicts(
        await judge.complete(JUDGE_SYSTEM, (Message(role="user", content=prompt),))
    )

    def passed(index: int) -> bool:
        verdict = verdicts[index] if index < len(verdicts) else None
        return isinstance(verdict, dict) and verdict.get("passed") is True

    failures = [item for index, item in enumerate(rubric) if not passed(index)]
    if failures:
        return False, f"unmet: {'; '.join(failures)}"
    return True, f"{len(rubric)}/{len(rubric)} criteria met"


def _verdicts(text: str) -> list[object]:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return []
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    items = parsed.get("items") if isinstance(parsed, dict) else None
    return items if isinstance(items, list) else []


def _scripted_rubric(answer: str, rubric: Sequence[str]) -> tuple[bool, str]:
    lowered = answer.lower()
    unmet = [item for item in rubric if not _salient_present(item, lowered)]
    if unmet:
        return False, f"unmet: {'; '.join(unmet)}"
    return True, f"{len(rubric)}/{len(rubric)} criteria met"


def _salient_present(criterion: str, answer: str) -> bool:
    if criterion.lower().startswith(("no ", "the agent commits no", "does not")):
        return True
    return all(term.lower() in answer for term in re.findall(r"'([^']+)'", criterion))

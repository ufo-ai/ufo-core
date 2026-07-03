"""The gate's evidence: replay each held-out task under both prompt arms, grade the answers, and
score the lift. `absent` re-runs the agent's current prompt, `present` the candidate — same task,
same judge, so the only difference is the prompt body. A candidate passes a tick when its present
acceptance rate clears the absent rate by `lift_margin`, with each arm at or above `n_floor`."""

import json
from dataclasses import dataclass

from selfhost.sdk.models import Message, ToolUseBlock
from selfhost_ext_self_improvement.corpus import TaskExample
from selfhost_ext_self_improvement.model import ModelLeg

N_FLOOR = 1
LIFT_MARGIN = 0.01
REPLAY_HEAD_LIMIT = 40


@dataclass(frozen=True)
class EvalOutcome:
    conversation_id: str
    present: bool
    accepted: bool


@dataclass(frozen=True)
class GateVerdict:
    passed: bool
    present_rate: float
    absent_rate: float


@dataclass(frozen=True)
class CandidateEvaluation:
    replay_model: ModelLeg
    judge: ModelLeg
    n_floor: int = N_FLOOR
    lift_margin: float = LIFT_MARGIN

    async def evaluate(
        self, candidate_prompt: str, current_prompt: str, held_out: tuple[TaskExample, ...]
    ) -> GateVerdict:
        outcomes: list[EvalOutcome] = []
        for example in held_out:
            head = _replay_head(example.messages)
            for present, prompt in ((False, current_prompt), (True, candidate_prompt)):
                answer = await self.replay_model.complete(prompt, head)
                accepted = await self._accepts(example.request, answer)
                outcomes.append(EvalOutcome(str(example.conversation_id), present, accepted))
        return self._score(tuple(outcomes))

    async def _accepts(self, request: str, answer: str) -> bool:
        verdict = await self.judge.complete(
            GRADER_SYSTEM,
            (Message(role="user", content=f"REQUEST:\n{request}\n\nANSWER:\n{answer}"),),
        )
        start, end = verdict.find("{"), verdict.rfind("}")
        if start == -1 or end <= start:
            return False
        try:
            parsed = json.loads(verdict[start : end + 1])
        except json.JSONDecodeError:
            return False
        return isinstance(parsed, dict) and parsed.get("accepted") is True

    def _score(self, outcomes: tuple[EvalOutcome, ...]) -> GateVerdict:
        present = [outcome for outcome in outcomes if outcome.present]
        absent = [outcome for outcome in outcomes if not outcome.present]
        present_rate = _rate(present)
        absent_rate = _rate(absent)
        passed = (
            len(present) >= self.n_floor
            and len(absent) >= self.n_floor
            and present_rate - absent_rate >= self.lift_margin
        )
        return GateVerdict(passed, present_rate, absent_rate)


GRADER_SYSTEM = (
    "You grade an AI agent's answer to a user request. You see the REQUEST and the ANSWER. Judge "
    "whether the answer correctly and completely satisfies the request — the meaning, not the "
    'wording. Return ONLY a JSON object {"accepted": true|false} — true if the answer satisfies '
    "the request, false otherwise."
)


def _replay_head(messages: tuple[Message, ...]) -> tuple[Message, ...]:
    """The archived conversation with its final answer stripped — the context the arm regenerates
    from — bounded to `REPLAY_HEAD_LIMIT` messages, keeping the request and recent tool rounds."""
    head = list(messages)
    while head:
        last = head[-1]
        if last.role != "assistant":
            break
        if not isinstance(last.content, str) and any(
            isinstance(block, ToolUseBlock) for block in last.content
        ):
            break
        head.pop()
    if len(head) > REPLAY_HEAD_LIMIT:
        head = head[:1] + head[-(REPLAY_HEAD_LIMIT - 1) :]
    return tuple(head)


def _rate(outcomes: list[EvalOutcome]) -> float:
    return sum(o.accepted for o in outcomes) / len(outcomes) if outcomes else 0.0
